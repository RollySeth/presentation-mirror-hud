import sys
from unittest.mock import MagicMock

import pytest

from presenter_hud import sensor_runner
from presenter_hud.models import HudState


@pytest.fixture
def runner(monkeypatch):
    state = HudState()
    state.metrics.camera_active = True
    state.metrics.face_present = True
    state.metrics.expression_active = True
    state.metrics.expression_model_active = True
    state.metrics.expression_label = "sad"
    state.metrics.expression_confidence = .9
    state.metrics.expression_observed_at = 123.
    snapshots = []
    store = MagicMock()

    def update(mutate):
        mutate(state)
        snapshots.append(state.to_dict())

    store.update.side_effect = update
    stopped = MagicMock()
    stopped.is_set.side_effect = [False, True]
    adapter = MagicMock()
    adapter.sample.return_value = {
        "camera_active": True, "face_present": True, "expression_active": True,
        "expression_model_active": True, "expression_label": "happy",
        "expression_confidence": .9, "expression_observed_at": 200., "smile_detected": None,
    }
    monkeypatch.setattr(sensor_runner, "StateStore", MagicMock(return_value=store))
    monkeypatch.setattr(sensor_runner.threading, "Event", MagicMock(return_value=stopped))
    monkeypatch.setattr(sensor_runner.signal, "signal", MagicMock())
    monkeypatch.setattr(sensor_runner, "refresh_coach", MagicMock())
    factory = MagicMock(return_value=adapter)
    monkeypatch.setattr(sensor_runner, "CameraPresenceAdapter", factory)
    return state, snapshots, adapter, factory


def assert_reset(state):
    metrics = state.metrics
    assert metrics.camera_active is False and metrics.face_present is False
    assert metrics.expression_model_active is False and metrics.expression_active is False
    assert metrics.expression_label == "" and metrics.expression_confidence == 0.
    assert metrics.expression_observed_at == 0. and metrics.smile_detected is None
    assert metrics.face_motion is None


@pytest.mark.parametrize("directory", [None, "models/expressions"])
def test_runner_explicit_opt_in_and_shutdown_reset(runner, monkeypatch, directory):
    state, snapshots, adapter, factory = runner
    arguments = ["sensor_runner", "--camera", "--camera-device", "/dev/video0"]
    if directory is not None:
        arguments += ["--expression-models", directory]
    monkeypatch.setattr(sys, "argv", arguments)
    sensor_runner.main()
    factory.assert_called_once_with(device="/dev/video0", expression_models=directory)
    assert snapshots[0]["metrics"]["expression_label"] == "happy"
    assert_reset(state)
    adapter.close.assert_called_once()


@pytest.mark.parametrize("failure", ["initialization", "sample", "close"])
def test_runner_errors_clear_stale_fields(runner, monkeypatch, caplog, failure):
    state, snapshots, adapter, factory = runner
    monkeypatch.setattr(sys, "argv", ["sensor_runner", "--camera", "--expression-models", "missing"])
    if failure == "initialization":
        factory.side_effect = RuntimeError("model missing")
    elif failure == "sample":
        adapter.sample.side_effect = RuntimeError("model error")
    else:
        adapter.close.side_effect = RuntimeError("close failed")
    if failure == "close":
        sensor_runner.main()
    else:
        with pytest.raises(RuntimeError):
            sensor_runner.main()
    assert_reset(state)
    assert caplog.records


def test_model_flag_without_camera_rejected(runner, monkeypatch):
    _, _, _, factory = runner
    monkeypatch.setattr(sys, "argv", ["sensor_runner", "--audio", "--expression-models", "models/expressions"])
    with pytest.raises(SystemExit) as error:
        sensor_runner.main()
    assert error.value.code == 2
    factory.assert_not_called()
