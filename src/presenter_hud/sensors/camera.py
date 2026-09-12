from __future__ import annotations

import logging
from pathlib import Path
import time

from .base import MetricValue, SensorAdapter
from .expression import ExpressionEstimator


class CameraPresenceAdapter(SensorAdapter):
    """Low-rate position tracking with optional offline expression estimates."""

    def __init__(self, device: int | str = 0, *, expression_models: str | None = None):
        try:
            import cv2
        except ImportError as error:
            raise RuntimeError("Install requirements-sensors.txt to use the camera adapter") from error
        self.cv2 = cv2
        self.device = device
        cv2.setNumThreads(1)
        self.expression_estimator = ExpressionEstimator(expression_models) if expression_models is not None else None
        self.detector = self.smile_detector = None
        if self.expression_estimator is None:
            self._load_haar()
        self.smile_candidate = None
        self.smile_streak = 0
        self.smile_detected = None
        self.last_face = None
        self.last_seen_at = time.monotonic()
        self.capture = (
            cv2.VideoCapture(device, cv2.CAP_V4L2)
            if isinstance(device, str) and device.startswith("/dev/")
            else cv2.VideoCapture(device)
        )
        if not self.capture.isOpened():
            self.capture.release()
            raise RuntimeError(f"Could not open camera {device}")
        for setting, value in (
            (cv2.CAP_PROP_FRAME_WIDTH, 640),
            (cv2.CAP_PROP_FRAME_HEIGHT, 480),
            (cv2.CAP_PROP_BUFFERSIZE, 1),
        ):
            if not self.capture.set(setting, value):
                logging.getLogger(__name__).warning("Camera %s could not set property %s", device, setting)

    def _load_haar(self):
        cv2 = self.cv2
        cascade_directory = getattr(getattr(cv2, "data", None), "haarcascades", None)
        candidates = []
        if cascade_directory:
            candidates.append(Path(cascade_directory) / "haarcascade_frontalface_default.xml")
        candidates.append(Path("/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"))
        cascade_path = next((path for path in candidates if path.is_file()), None)
        if cascade_path is None:
            raise RuntimeError("Face detector data missing. Install opencv-data on Raspberry Pi OS.")
        self.detector = cv2.CascadeClassifier(str(cascade_path))
        if self.detector.empty():
            raise RuntimeError(f"Could not load face detector: {cascade_path}")
        smile_path = cascade_path.with_name("haarcascade_smile.xml")
        self.smile_detector = cv2.CascadeClassifier(str(smile_path)) if smile_path.is_file() else None
        if self.smile_detector is not None and self.smile_detector.empty():
            self.smile_detector = None
        if self.smile_detector is None:
            logging.getLogger(__name__).warning(
                "Smile indicator unavailable. Install opencv-data; face-position tracking remains enabled."
            )

    def sample(self) -> dict[str, MetricValue]:
        captured_at = time.monotonic()
        ok, frame = self.capture.read()
        if not ok:
            if self.expression_estimator is not None:
                self.expression_estimator.reset()
            raise RuntimeError(f"Camera {self.device} stopped delivering frames")
        if self.expression_estimator is not None:
            observation = self.expression_estimator.observe(frame)
            faces = [observation.bbox] if observation.bbox is not None else []
        else:
            gray = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
            faces = self.detector.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=4, minSize=(40, 40))
        updates: dict[str, MetricValue] = {
            "camera_active": True, "face_present": bool(len(faces)), "face_motion": None,
            "expression_active": self.expression_estimator is not None or self.smile_detector is not None,
            "expression_model_active": self.expression_estimator is not None,
            "expression_label": "", "expression_confidence": 0.0, "expression_observed_at": 0.0,
        }
        if self.expression_estimator is not None:
            updates.update({
                "expression_label": observation.label,
                "expression_confidence": observation.confidence,
                "expression_observed_at": observation.observed_at,
            })
        if len(faces):
            self.last_seen_at = captured_at
            x, y, width, height = max(faces, key=lambda face: face[2] * face[3])
            center = (x + width / 2) / frame.shape[1]
            updates.update({
                "face_center_x": float(center),
                "face_center_y": float((y + height / 2) / frame.shape[0]),
                "centered": bool(0.35 <= center <= 0.65),
            })
            if self.last_face is not None:
                previous_time, previous_x = self.last_face
                elapsed = captured_at - previous_time
                if 0.1 <= elapsed <= 5:
                    updates["face_motion"] = float(abs(center - previous_x) / elapsed)
            self.last_face = captured_at, float(center)
            if self.smile_detector is not None:
                mouth_region = gray[y + height // 3:y + height, x:x + width]
                smiles = self.smile_detector.detectMultiScale(
                    mouth_region, scaleFactor=1.3, minNeighbors=22,
                    minSize=(max(20, int(width * .25)), 10),
                )
                detected = bool(len(smiles))
                self.smile_streak = min(2, self.smile_streak + 1) if detected == self.smile_candidate else 1
                self.smile_candidate = detected
                if self.smile_streak >= 2:
                    self.smile_detected = detected
        else:
            self.last_face = None
            self.smile_candidate = self.smile_detected = None
            self.smile_streak = 0
        updates["smile_detected"] = self.smile_detected
        updates["face_missing_seconds"] = max(0.0, captured_at - self.last_seen_at)
        return updates

    def close(self) -> None:
        try:
            self.capture.release()
        finally:
            self.last_face = None
            self.smile_candidate = self.smile_detected = None
            self.smile_streak = 0
            if self.expression_estimator is not None:
                self.expression_estimator.reset()
