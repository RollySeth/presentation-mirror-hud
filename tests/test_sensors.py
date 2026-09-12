import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

np = pytest.importorskip("numpy")

from presenter_hud.sensor_runner import device_argument
from presenter_hud.sensors.audio import AudioLevelAdapter
from presenter_hud.sensors.camera import CameraPresenceAdapter


@pytest.fixture
def camera_backend(monkeypatch):
    capture = MagicMock()
    capture.isOpened.return_value = True
    capture.set.return_value = True
    capture.read.return_value = (True, np.zeros((480, 640, 3), dtype=np.uint8))
    detector = MagicMock()
    detector.empty.return_value = False
    detector.detectMultiScale.return_value = np.array([(280, 100, 80, 80)], dtype=np.int32)
    smile = MagicMock()
    smile.empty.return_value = False
    smile.detectMultiScale.return_value = []
    backend = SimpleNamespace(
        VideoCapture=MagicMock(return_value=capture),
        CascadeClassifier=MagicMock(side_effect=lambda path: smile if path.endswith("haarcascade_smile.xml") else detector),
        smile=smile,
        setNumThreads=MagicMock(),
        cvtColor=MagicMock(return_value=np.zeros((480, 640), dtype=np.uint8)),
        COLOR_BGR2GRAY=1,
        CAP_PROP_FRAME_WIDTH=2,
        CAP_PROP_FRAME_HEIGHT=3,
        CAP_PROP_BUFFERSIZE=4,
        CAP_V4L2=5,
    )
    monkeypatch.setitem(sys.modules, "cv2", backend)
    monkeypatch.setattr(
        Path, "is_file",
        lambda path: path.parent.as_posix() == "/usr/share/opencv4/haarcascades"
        and path.name in ("haarcascade_frontalface_default.xml", "haarcascade_smile.xml"),
    )
    return backend, capture, detector


def test_camera_loads_distribution_cascade_and_detects_presence(camera_backend):
    backend, capture, _ = camera_backend
    adapter = CameraPresenceAdapter("/dev/video0")
    try:
        metrics = adapter.sample()
        assert metrics == {
            "camera_active": True, "face_present": True, "centered": True,
            "face_center_x": 0.5, "face_center_y": pytest.approx(140 / 480),
            "face_motion": None,
            "face_missing_seconds": 0.0,
            "expression_active": True, "smile_detected": None,
            "expression_model_active": False, "expression_label": "",
            "expression_confidence": 0.0, "expression_observed_at": 0.0,
        }
        assert json.loads(json.dumps(metrics)) == metrics
        backend.VideoCapture.assert_called_once_with("/dev/video0", backend.CAP_V4L2)
        backend.CascadeClassifier.assert_has_calls([
            call(str(Path("/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"))),
            call(str(Path("/usr/share/opencv4/haarcascades/haarcascade_smile.xml"))),
        ])
    finally:
        adapter.close()
    capture.release.assert_called_once()


def test_no_face_is_distinct_from_camera_failure(camera_backend):
    _, capture, detector = camera_backend
    adapter = CameraPresenceAdapter()
    try:
        detector.detectMultiScale.return_value = []
        metrics = adapter.sample()
        assert metrics["camera_active"] is True
        assert metrics["face_present"] is False
        assert metrics["face_motion"] is None
        assert metrics["face_missing_seconds"] >= 0
        capture.read.return_value = (False, None)
        with pytest.raises(RuntimeError, match="stopped delivering frames"):
            adapter.sample()
    finally:
        adapter.close()


def test_camera_open_failure_releases_device(camera_backend):
    _, capture, _ = camera_backend
    capture.isOpened.return_value = False
    with pytest.raises(RuntimeError, match="Could not open camera"):
        CameraPresenceAdapter()
    capture.release.assert_called_once()


def test_audio_selects_requested_device_and_native_sample_rate(monkeypatch):
    backend = SimpleNamespace(
        query_devices=MagicMock(return_value={"default_samplerate": 32000.0}),
        check_input_settings=MagicMock(),
        rec=MagicMock(return_value=np.full((8000, 1), 0.01, dtype=np.float32)),
    )
    monkeypatch.setitem(sys.modules, "sounddevice", backend)
    adapter = AudioLevelAdapter(device="C920")
    metrics = adapter.sample()
    assert metrics["microphone_active"] is True
    assert metrics["volume"] == pytest.approx(0.08)
    backend.rec.assert_called_once_with(
        8000, samplerate=32000, channels=1, dtype="float32", device="C920", blocking=True
    )


def test_device_arguments_preserve_names_and_paths():
    assert device_argument("0") == 0
    assert device_argument("C920") == "C920"
    assert device_argument("/dev/video0") == "/dev/video0"


def test_face_motion_uses_observed_positions_and_resets_after_loss(camera_backend, monkeypatch):
    _, _, detector = camera_backend
    moments = iter([0.0, 1.0, 2.0, 3.0, 4.0])
    monkeypatch.setattr("presenter_hud.sensors.camera.time.monotonic", lambda: next(moments))
    adapter = CameraPresenceAdapter()
    try:
        assert adapter.sample()["face_motion"] is None
        detector.detectMultiScale.return_value = np.array([(344, 100, 80, 80)], dtype=np.int32)
        assert adapter.sample()["face_motion"] == pytest.approx(0.1)
        detector.detectMultiScale.return_value = []
        assert adapter.sample()["face_motion"] is None
        detector.detectMultiScale.return_value = np.array([(344, 100, 80, 80)], dtype=np.int32)
        assert adapter.sample()["face_motion"] is None
    finally:
        adapter.close()


def test_smile_needs_two_samples_and_resets_when_face_is_lost(camera_backend):
    backend, _, detector = camera_backend
    adapter = CameraPresenceAdapter()
    try:
        backend.smile.detectMultiScale.return_value = [(10, 10, 30, 15)]
        assert adapter.sample()["smile_detected"] is None
        assert adapter.sample()["smile_detected"] is True
        backend.smile.detectMultiScale.return_value = []
        assert adapter.sample()["smile_detected"] is True
        assert adapter.sample()["smile_detected"] is False
        detector.detectMultiScale.return_value = []
        assert adapter.sample()["smile_detected"] is None
    finally:
        adapter.close()


def test_missing_smile_cascade_keeps_camera_working_with_visible_warning(camera_backend, monkeypatch, caplog):
    monkeypatch.setattr(Path, "is_file", lambda path: path.name == "haarcascade_frontalface_default.xml")
    adapter = CameraPresenceAdapter()
    try:
        metrics = adapter.sample()
        assert metrics["camera_active"] and metrics["face_present"]
        assert metrics["expression_active"] is False
        assert metrics["smile_detected"] is None
        assert "Smile indicator unavailable" in caplog.text
    finally:
        adapter.close()


def test_model_mode_reuses_capture_and_position_without_haar(camera_backend, monkeypatch):
    from presenter_hud.sensors.expression import ExpressionObservation
    backend, capture, _ = camera_backend
    estimator = MagicMock()
    estimator.observe.side_effect = [
        ExpressionObservation((280., 100., 80., 80.), "happy", .9, 100.),
        ExpressionObservation((344., 100., 80., 80.), "", .5, 101.),
        ExpressionObservation(None, "", 0., 102.),
        ExpressionObservation((344., 100., 80., 80.), "", .7, 103.),
    ]
    factory = MagicMock(return_value=estimator)
    monkeypatch.setattr("presenter_hud.sensors.camera.ExpressionEstimator", factory)
    moments = iter([0., 1., 2., 3., 4.])
    monkeypatch.setattr("presenter_hud.sensors.camera.time.monotonic", lambda: next(moments))
    adapter = CameraPresenceAdapter("/dev/video0", expression_models="models/expressions")
    try:
        first = adapter.sample()
        assert first["expression_model_active"] is True and first["expression_active"] is True
        assert first["smile_detected"] is None and first["expression_label"] == "happy"
        assert first["expression_confidence"] == .9 and first["expression_observed_at"] == 100.
        assert first["face_center_x"] == .5 and first["face_motion"] is None
        assert json.loads(json.dumps(first)) == first
        second = adapter.sample()
        assert second["face_motion"] == pytest.approx(.1) and second["expression_label"] == ""
        lost = adapter.sample()
        assert lost["face_present"] is False and lost["face_motion"] is None
        assert lost["face_missing_seconds"] == 1.
        assert lost["expression_label"] == "" and lost["expression_confidence"] == 0.
        assert "face_center_x" not in lost and "face_center_y" not in lost
        assert adapter.sample()["face_motion"] is None
        backend.CascadeClassifier.assert_not_called()
        backend.cvtColor.assert_not_called()
        backend.VideoCapture.assert_called_once_with("/dev/video0", backend.CAP_V4L2)
        factory.assert_called_once_with("models/expressions")
    finally:
        adapter.close()
    capture.release.assert_called_once()
    estimator.reset.assert_called_once()


def test_explicit_model_load_failure_never_opens_camera_or_falls_back(camera_backend, monkeypatch):
    backend, _, _ = camera_backend
    monkeypatch.setattr("presenter_hud.sensors.camera.ExpressionEstimator",
                        MagicMock(side_effect=RuntimeError("model missing")))
    with pytest.raises(RuntimeError, match="model missing"):
        CameraPresenceAdapter(expression_models="missing")
    backend.VideoCapture.assert_not_called()
    backend.CascadeClassifier.assert_not_called()


def test_model_capture_loss_resets_estimator(camera_backend, monkeypatch):
    _, capture, _ = camera_backend
    estimator = MagicMock()
    monkeypatch.setattr("presenter_hud.sensors.camera.ExpressionEstimator", MagicMock(return_value=estimator))
    adapter = CameraPresenceAdapter(expression_models="models/expressions")
    capture.read.return_value = (False, None)
    try:
        with pytest.raises(RuntimeError, match="stopped delivering"):
            adapter.sample()
        estimator.reset.assert_called_once()
    finally:
        adapter.close()


def test_model_state_resets_even_when_capture_release_fails(camera_backend, monkeypatch):
    _, capture, _ = camera_backend
    estimator = MagicMock()
    monkeypatch.setattr("presenter_hud.sensors.camera.ExpressionEstimator", MagicMock(return_value=estimator))
    adapter = CameraPresenceAdapter(expression_models="models/expressions")
    capture.release.side_effect = RuntimeError("release failed")
    with pytest.raises(RuntimeError, match="release failed"):
        adapter.close()
    estimator.reset.assert_called_once()
    assert adapter.last_face is None
