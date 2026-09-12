import pytest

from presenter_hud.avatar import EXPRESSION_LABELS, coach_avatar, expression_avatar
from presenter_hud.models import HudState


def test_live_avatar_reacts_to_microphone_and_pause_without_emotion_inference():
    state = HudState()
    assert coach_avatar(state)["mode"] == "ready"
    state.metrics.microphone_active = True
    state.metrics.volume = 0.4
    assert coach_avatar(state)["mode"] == "speaking"
    assert coach_avatar(state)["mouth"] == 0.4
    state.paused = True
    assert coach_avatar(state)["mode"] == "paused"
    assert coach_avatar(state)["mouth"] == 0


def test_live_avatar_reports_camera_presence_and_centering():
    state = HudState()
    state.metrics.camera_active = True
    assert coach_avatar(state)["mode"] == "looking"
    state.metrics.face_present = True
    state.metrics.centered = False
    assert coach_avatar(state)["mode"] == "align"


def test_expression_reports_smile_detection_not_a_guessed_emotion():
    state = HudState()
    assert expression_avatar(state)["mode"] == "off"
    state.metrics.camera_active = state.metrics.expression_active = True
    assert expression_avatar(state)["mode"] == "no-face"
    state.metrics.face_present = True
    assert expression_avatar(state)["mode"] == "checking"
    state.metrics.smile_detected = True
    assert expression_avatar(state)["mode"] == "smile"
    state.metrics.smile_detected = False
    assert expression_avatar(state)["label"] == "Face visible"
    state.metrics.face_present = False
    assert expression_avatar(state)["mode"] == "no-face"


def test_speaking_animation_uses_recognized_speech_then_returns_to_quiet():
    state = HudState(running=True)
    state.metrics.microphone_active = state.metrics.speech_active = True
    state.metrics.speech_observed_at = 100
    state.metrics.speech_last_word_age = .2
    speaking = expression_avatar(state, 100.1)
    assert speaking["voice"] == "speaking"
    assert speaking["activity"] == "Speaking"
    assert speaking["mouth"] > 0
    quiet = expression_avatar(state, 103)
    assert quiet["voice"] == "quiet"
    assert quiet["mouth"] == 0
    state.paused = True
    assert expression_avatar(state, 100.1)["voice"] == "paused"


def test_microphone_noise_is_not_labelled_talking_or_an_emotion():
    state = HudState()
    state.metrics.microphone_active = True
    state.metrics.volume = .5
    avatar = expression_avatar(state, 100)
    assert avatar["voice"] == "sound"
    assert avatar["activity"] == "Sound detected"
    assert avatar["mouth"] == 0


@pytest.mark.parametrize("label", list(EXPRESSION_LABELS))
def test_model_expression_name_and_emoji_are_preserved_while_speaking(label):
    state = HudState(running=True)
    state.metrics.camera_active = state.metrics.expression_active = state.metrics.face_present = True
    state.metrics.expression_model_active = True
    state.metrics.expression_label = label
    state.metrics.expression_confidence = .9
    state.metrics.expression_observed_at = 100
    state.metrics.microphone_active = state.metrics.speech_active = True
    state.metrics.speech_last_word_age = .1
    state.metrics.speech_observed_at = 100
    avatar = expression_avatar(state, 100)
    assert avatar["mode"] == label
    assert avatar["emoji"] == EXPRESSION_LABELS[label][1]
    assert avatar["label"].endswith("(est.)")
    assert avatar["estimated"] and avatar["voice"] == "speaking"


@pytest.mark.parametrize("label,score,age", [("happy", .2, 0), ("sad", .9, 6), ("laughing", .99, 0)])
def test_uncertain_stale_and_unsupported_predictions_are_not_labelled_as_emotions(label, score, age):
    state = HudState()
    state.metrics.camera_active = state.metrics.expression_active = state.metrics.face_present = True
    state.metrics.expression_model_active = True
    state.metrics.expression_label = label
    state.metrics.expression_confidence = score
    state.metrics.expression_observed_at = 100
    avatar = expression_avatar(state, 100 + age)
    assert avatar["mode"] == "uncertain"
    assert not avatar["estimated"]
