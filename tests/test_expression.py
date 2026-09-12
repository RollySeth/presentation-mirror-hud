import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from presenter_hud.models import HudState
from presenter_hud.sensors.expression import ExpressionEstimator, LABELS, TARGET_POINTS


def face_row(x=160, y=100, scale=1):
    points = np.asarray(TARGET_POINTS) * scale + (x, y)
    return np.array([x / 2, y / 2, 112 * scale / 2, 112 * scale / 2,
                     *(points / 2).flatten(), .99], dtype=np.float32)


def logits(index=3, confidence=.7):
    values = np.full(7, (1 - confidence) / 6, dtype=np.float64)
    values[index] = confidence
    return np.log(values)[None, :]


@pytest.fixture
def estimator():
    detector = MagicMock()
    detector.detect.return_value = (1, np.array([face_row()]))
    network = MagicMock()
    network.forward.return_value = logits()
    return ExpressionEstimator("unused", backend=cv2, detector=detector, network=network)


@pytest.fixture
def frame():
    return np.zeros((480, 640, 3), dtype=np.uint8)


@pytest.mark.parametrize("index,label", enumerate(LABELS))
def test_label_mapping_and_high_confidence_immediate(estimator, frame, index, label):
    estimator.network.forward.return_value = logits(index, .9)
    result = estimator.observe(frame)
    assert result.label == label
    assert result.confidence == pytest.approx(.9)
    assert result.bbox == pytest.approx((160, 100, 112, 112))
    assert result.observed_at > 0
    assert type(result.confidence) is float and type(result.observed_at) is float
    assert all(type(value) is float for value in result.bbox)
    estimator.network.forward.assert_called_with("label")
    assert estimator.detector.detect.call_args.args[0].shape == (240, 320, 3)


def test_two_samples_and_ambiguous_or_different_class_clears(estimator, frame):
    assert estimator.observe(frame).label == ""
    assert estimator.observe(frame).label == "happy"
    estimator.network.forward.return_value = logits(5, .7)
    assert estimator.observe(frame).label == ""
    assert estimator.observe(frame).label == "sad"
    estimator.network.forward.return_value = logits(5, .5)
    result = estimator.observe(frame)
    assert result.label == "" and result.confidence == pytest.approx(.5)
    estimator.network.forward.return_value = logits(5, .7)
    assert estimator.observe(frame).label == ""


@pytest.mark.parametrize("confidence,expected", [(.549, ""), (.551, ""), (.849, ""), (.851, "happy")])
def test_first_sample_thresholds(estimator, frame, confidence, expected):
    estimator.network.forward.return_value = logits(3, confidence)
    assert estimator.observe(frame).label == expected


def test_no_face_clears_without_classifier_and_reacquisition_needs_two(estimator, frame):
    estimator.observe(frame)
    assert estimator.observe(frame).label == "happy"
    estimator.detector.detect.return_value = (0, None)
    estimator.network.reset_mock()
    missing = estimator.observe(frame)
    assert missing.bbox is None and missing.label == "" and missing.confidence == 0.
    assert missing.observed_at > 0
    estimator.network.forward.assert_not_called()
    estimator.detector.detect.return_value = (1, np.array([face_row()]))
    assert estimator.observe(frame).label == ""


def test_stale_gap_and_clock_regression_reset_streak(estimator, frame, monkeypatch):
    moments = iter([1., 2., 9., 10., 8.])
    monkeypatch.setattr("presenter_hud.sensors.expression.time.monotonic", lambda: next(moments))
    assert [estimator.observe(frame).label for _ in range(5)] == ["", "happy", "", "happy", ""]
    estimator.reset()
    assert estimator.candidate == "" and estimator.streak == 0 and estimator.last_sample is None


@pytest.mark.parametrize("bad", [None, np.zeros((1, 1, 3), np.uint8),
                                np.zeros((20, 20), np.uint8), np.zeros((20, 20, 4), np.uint8),
                                np.zeros((20, 20, 3), np.float32), np.zeros((0, 20, 3), np.uint8)])
def test_invalid_frames_are_rejected_and_reset(estimator, bad):
    estimator.streak = 2
    with pytest.raises(ValueError, match="uint8 BGR"):
        estimator.observe(bad)
    assert estimator.streak == 0
    estimator.detector.detect.assert_not_called()


@pytest.mark.parametrize("index,value", [(0, np.nan), (1, np.inf), (2, -1), (3, 0), (2, 1e30),
                                        (4, -20), (5, 999), (6, 80), (14, .7), (14, 2)])
def test_invalid_bbox_landmarks_or_detection_scores_are_rejected(estimator, frame, index, value):
    row = face_row()
    row[index] = value
    estimator.detector.detect.return_value = (1, np.array([row]))
    result = estimator.observe(frame)
    assert result.bbox is None and result.label == ""
    estimator.network.forward.assert_not_called()


@pytest.mark.parametrize("rows", [[], np.ones((1, 14)), np.ones(15), np.ones((2, 16))])
def test_malformed_detector_rows_are_rejected(estimator, frame, rows):
    estimator.detector.detect.return_value = (1, rows)
    assert estimator.observe(frame).bbox is None


def test_largest_valid_face_only_and_clipped_bbox(estimator, frame):
    invalid = face_row(scale=3)
    invalid[4] = -100
    large = face_row(x=10, y=0, scale=2)
    large[0] = -5
    large[2] += 10
    estimator.detector.detect.return_value = (3, np.array([face_row(), invalid, large]))
    result = estimator.observe(frame)
    assert result.bbox == pytest.approx((0, 0, 234, 224))
    estimator.network.forward.assert_called_once()


@pytest.mark.parametrize("transform", [
    None, np.full((2, 3), np.nan), np.eye(3), np.zeros((2, 3)),
    np.array([[-1., 0, 0], [0, 1., 0]]),
    np.array([[1., 0, 1000], [0, 1., 1000]]),
    np.array([[3., 0, 0], [0, 1., 0]]),
])
def test_invalid_transform_preserves_position_but_clears_expression(estimator, frame, monkeypatch, transform):
    monkeypatch.setattr(cv2, "estimateAffinePartial2D", lambda *args, **kwargs: (transform, None))
    result = estimator.observe(frame)
    assert result.bbox is not None
    assert result.label == "" and result.confidence == 0.
    estimator.network.forward.assert_not_called()


@pytest.mark.parametrize("raw", [np.full((1, 7), np.nan), np.full((1, 7), np.inf),
                                np.ones((7,)), np.ones((1, 6)), np.ones((2, 7))])
def test_invalid_model_outputs_clear(estimator, frame, raw):
    estimator.streak = 2
    estimator.network.forward.return_value = raw
    result = estimator.observe(frame)
    assert result.label == "" and result.confidence == 0.
    assert estimator.streak == 0


def test_softmax_is_stable_and_once_only_for_verified_logits(estimator, frame):
    estimator.network.forward.return_value = logits(4, .9) + 100000
    result = estimator.observe(frame)
    assert result.label == "neutral" and result.confidence == pytest.approx(.9)


def test_rgb_normalization_and_input_name(estimator, frame):
    frame[:] = [0, 127, 255]
    estimator.observe(frame)
    blob, name = estimator.network.setInput.call_args.args
    assert name == "data" and blob.shape == (1, 3, 112, 112)
    assert blob.dtype == np.float32
    assert blob[0, :, 56, 56] == pytest.approx([1., 127 / 127.5 - 1, -1.])


def test_inference_error_is_actionable_and_resets(estimator, frame):
    estimator.network.forward.side_effect = RuntimeError("bad network")
    with pytest.raises(RuntimeError, match="no smile fallback"):
        estimator.observe(frame)
    assert estimator.streak == 0


def test_missing_models_fail_explicitly_before_loading(tmp_path):
    with pytest.raises(RuntimeError, match="setup_expression_models.py"):
        ExpressionEstimator(tmp_path)


def test_wrong_hash_rejected_before_network_creation(tmp_path, monkeypatch):
    from presenter_hud.sensors import expression
    monkeypatch.setattr(expression, "MODEL_ASSETS", {"yunet.onnx": (4, "0" * 64)})
    (tmp_path / "yunet.onnx").write_bytes(b"oops")
    with pytest.raises(RuntimeError, match="SHA256 mismatch"):
        ExpressionEstimator(tmp_path)


def test_backward_state_defaults_and_json_native_fields():
    state = HudState.from_dict({"metrics": {"camera_active": True}})
    assert state.metrics.expression_model_active is False
    assert state.metrics.expression_label == ""
    assert state.metrics.expression_confidence == 0.
    assert state.metrics.expression_observed_at == 0.
    assert json.loads(json.dumps(state.to_dict()))["metrics"]["expression_label"] == ""


def test_deployment_manifest_matches_runtime_vetted_assets():
    from presenter_hud.sensors.expression import MODEL_ASSETS
    manifest = Path(__file__).parents[1] / "deploy" / "expression_models.json"
    assets = json.loads(manifest.read_text())["models"]
    assert {asset["file"]: (asset["size"], asset["sha256"]) for asset in assets} == MODEL_ASSETS


def test_native_vetted_models_synthetic_only(monkeypatch):
    directory = os.environ.get("SMARTMIRROR_EXPRESSION_MODELS")
    if not directory:
        pytest.skip("Set SMARTMIRROR_EXPRESSION_MODELS for camera-free native integration")
    monkeypatch.setattr(cv2, "VideoCapture", MagicMock(side_effect=AssertionError("No camera access allowed")))
    estimator = ExpressionEstimator(Path(directory))
    result = estimator.observe(np.zeros((480, 640, 3), dtype=np.uint8))
    assert result.bbox is None and result.label == ""
    scores = estimator._scores(np.zeros((112, 112, 3), dtype=np.uint8))
    assert scores.shape == (7,) and np.isfinite(scores).all()
    assert scores.sum() == pytest.approx(1.)
    assert 0. <= float(scores.min()) <= float(scores.max()) <= 1.
