"""Optional offline facial-expression estimates, never a determination of feelings.

OpenCV Zoo YuNet (MIT) + MobileFaceNet Progressive Teacher (Apache-2.0).
Only the vetted weights are supported. Scores are uncalibrated softmax scores.
No image files, identities or recordings are stored.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import time


LABELS = ("angry", "disgust", "fearful", "happy", "neutral", "sad", "surprised")
MODEL_ASSETS = {
    "yunet.onnx": (232589, "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "mobilefacenet.onnx": (4791892, "4f61307602fc089ce20488a31d4e4614e3c9753a7d6c41578c854858b183e1a9"),
}
TARGET_POINTS = (
    (38.2946, 51.6963), (73.5318, 51.5014), (56.0252, 71.7366),
    (41.5493, 92.3655), (70.7299, 92.2041),
)


@dataclass(frozen=True)
class ExpressionObservation:
    bbox: tuple[float, float, float, float] | None
    label: str = ""
    confidence: float = 0.0
    observed_at: float = 0.0


class ExpressionEstimator:
    """One largest valid face; 320x240 detection and 112x112 aligned inference.

    A >=.85 score is immediate; >=.55 needs two consecutive matching samples.
    Ambiguity/loss clears the label immediately, and >5s gaps reset the streak.
    An empty label may carry the current top score, but never an old label.
    """

    def __init__(self, model_directory: str | Path, *, backend=None, detector=None, network=None):
        import numpy as np
        if backend is None:
            import cv2 as backend
        self.np, self.cv2 = np, backend
        self.reset()
        try:
            if detector is None or network is None:
                directory = Path(model_directory)
                for name, (size, digest) in MODEL_ASSETS.items():
                    path = directory / name
                    if not path.is_file() or path.stat().st_size != size:
                        raise ValueError(f"Missing or wrong-size model: {path}")
                    with path.open("rb") as stream:
                        actual = hashlib.file_digest(stream, "sha256").hexdigest()
                    if actual != digest:
                        raise ValueError(f"SHA256 mismatch: {path}")
                backend.setNumThreads(1)
                detector = backend.FaceDetectorYN.create(
                    str(directory / "yunet.onnx"), "", (320, 240), 0.8, 0.3, 100
                )
                network = backend.dnn.readNetFromONNX(str(directory / "mobilefacenet.onnx"))
                network.setPreferableBackend(backend.dnn.DNN_BACKEND_OPENCV)
                network.setPreferableTarget(backend.dnn.DNN_TARGET_CPU)
            self.detector, self.network = detector, network
        except Exception as error:
            raise RuntimeError(
                f"Offline expression models could not load: {error}. "
                "Run deploy/setup_expression_models.py, then check --expression-models. "
                "No smile fallback was enabled."
            ) from error

    def reset(self):
        self.candidate = ""
        self.streak = 0
        self.last_sample = None

    def _largest_face(self, rows, frame_width, frame_height):
        np = self.np
        if rows is None:
            return None
        rows = np.asarray(rows)
        if rows.ndim != 2 or rows.shape[1] != 15:
            logging.getLogger(__name__).warning("YuNet returned malformed detections; discarding this observation")
            return None
        valid = []
        for row in rows:
            if not np.isfinite(row).all() or not 0.8 <= row[14] <= 1:
                continue
            face = row.astype(np.float64, copy=True)
            # Reject impossible detector coordinates before rescaling/clipping.
            if (np.any(np.abs(face[[0, 2, 4, 6, 8, 10, 12]]) > 640)
                    or np.any(np.abs(face[[1, 3, 5, 7, 9, 11, 13]]) > 480)):
                continue
            face[[0, 2, 4, 6, 8, 10, 12]] *= frame_width / 320
            face[[1, 3, 5, 7, 9, 11, 13]] *= frame_height / 240
            x, y, width, height = face[:4]
            if width <= 0 or height <= 0:
                continue
            left, top = max(0., x), max(0., y)
            right, bottom = min(float(frame_width), x + width), min(float(frame_height), y + height)
            if right - left < 20 or bottom - top < 20:
                continue
            points = face[4:14].reshape(5, 2)
            if (np.any(points[:, 0] < left) or np.any(points[:, 0] >= right)
                    or np.any(points[:, 1] < top) or np.any(points[:, 1] >= bottom)):
                continue
            if (points[1, 0] - points[0, 0] < 4
                    or points[4, 0] - points[3, 0] < 2
                    or points[3:5, 1].mean() <= points[:2, 1].mean()):
                continue
            face[:4] = left, top, right - left, bottom - top
            valid.append(face)
        return max(valid, key=lambda face: face[2] * face[3]) if valid else None

    def _aligned_crop(self, frame, face):
        np, cv2 = self.np, self.cv2
        points = face[4:14].reshape(5, 2)
        target = np.array(TARGET_POINTS, dtype=np.float64)
        transform, _ = cv2.estimateAffinePartial2D(points, target, method=cv2.LMEDS)
        if transform is None or transform.shape != (2, 3) or not np.isfinite(transform).all():
            return None
        linear = transform[:, :2]
        determinant = np.linalg.det(linear)
        if not 0.0004 <= determinant <= 400:
            return None
        singular = np.linalg.svd(linear, compute_uv=False)
        if singular[0] / singular[1] > 1.05:
            return None
        mapped = points @ linear.T + transform[:, 2]
        if np.max(np.linalg.norm(mapped - target, axis=1)) > 8:
            return None
        return cv2.warpAffine(frame, transform, (112, 112))

    def _scores(self, crop):
        np, cv2 = self.np, self.cv2
        image = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32) / 127.5 - 1.0
        self.network.setInput(cv2.dnn.blobFromImage(image), "data")
        raw = np.asarray(self.network.forward("label"))
        if raw.shape != (1, 7) or not np.isfinite(raw).all():
            logging.getLogger(__name__).warning("Expression model returned invalid scores; discarding this estimate")
            return None
        # The SHA-pinned model ends in Gemm -> Identity, NOT Softmax.
        # Verified raw logits contain negative values and values greater than 1.
        logits = raw[0].astype(np.float64)
        scores = np.exp(logits - logits.max())
        scores /= scores.sum()
        return scores if np.isfinite(scores).all() else None

    def observe(self, frame) -> ExpressionObservation:
        np = self.np
        if (not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3
                or min(frame.shape[:2]) < 20 or frame.dtype != np.uint8):
            self.reset()
            raise ValueError("Expression camera frame must be a nonempty uint8 BGR image")
        now = time.monotonic()
        if self.last_sample is not None and not 0 <= now - self.last_sample <= 5:
            self.reset()
        try:
            resized = self.cv2.resize(frame, (320, 240))
            _, rows = self.detector.detect(resized)
            face = self._largest_face(rows, frame.shape[1], frame.shape[0])
            bbox = tuple(float(value) for value in face[:4]) if face is not None else None
            crop = self._aligned_crop(frame, face) if face is not None else None
            scores = self._scores(crop) if crop is not None else None
            label, confidence = "", 0.0
            if scores is None:
                self.reset()
            else:
                index = int(np.argmax(scores))
                confidence = float(scores[index])
                candidate = LABELS[index]
                if confidence < 0.55:
                    self.reset()
                else:
                    self.streak = min(2, self.streak + 1) if candidate == self.candidate else 1
                    self.candidate = candidate
                    if confidence >= 0.85 or self.streak >= 2:
                        label = candidate
            self.last_sample = now
            return ExpressionObservation(bbox, label, confidence, float(time.time()))
        except Exception as error:
            self.reset()
            raise RuntimeError(
                f"Offline expression observation failed: {error}. Check OpenCV and model files; "
                "no smile fallback was enabled."
            ) from error
