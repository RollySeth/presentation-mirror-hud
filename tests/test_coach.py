from datetime import datetime, timezone

import pytest

from presenter_hud.coach import choose_coach_cue, refresh_coach
from presenter_hud.models import CoachCue, HudState


def speaking(pace=130, now=100):
    state = HudState(running=True)
    state.metrics.speech_active = True
    state.metrics.microphone_active = True
    state.metrics.volume = 0.3
    state.metrics.speech_word_count = 40
    state.metrics.speech_recent_word_count = 12
    state.metrics.speech_recent_wpm = pace
    state.metrics.words_per_minute = pace
    state.metrics.speech_last_word_age = 0.3
    state.metrics.speech_observed_at = now
    return state


def refresh(state, now):
    state.metrics.speech_observed_at = now
    refresh_coach(state, now)


def test_current_fast_phrase_takes_priority_over_missing_face():
    state = speaking(190)
    state.metrics.camera_active = True
    state.metrics.face_missing_seconds = 8
    assert choose_coach_cue(state, 100).kind == "pace-fast"
    assert "190 WPM" in choose_coach_cue(state, 100).text


@pytest.mark.parametrize("pace", [70, 190])
def test_ongoing_pace_issue_cannot_pin_or_repeat_one_cue(pace):
    state = speaking(pace)
    refresh(state, 100)
    assert state.cue.kind.startswith("pace-")
    refresh(state, 104)
    assert state.cue.kind.startswith("pace-")
    for now in (108, 120, 140, 200):
        refresh(state, now)
        assert state.cue.kind == "progress"


def test_other_actionable_signal_gets_a_turn_after_pace_advice():
    state = speaking(70)
    refresh(state, 100)
    state.metrics.camera_active = True
    state.metrics.face_present = True
    state.metrics.centered = False
    refresh(state, 108)
    assert state.cue.kind == "position"


@pytest.mark.parametrize("pace", [70, 190])
def test_verified_pace_improvement_is_acknowledged_immediately(pace):
    state = speaking(pace)
    refresh(state, 100)
    state.metrics.speech_recent_wpm = 130
    refresh(state, 101)
    assert state.cue.kind == "improvement"
    assert "130 WPM" in state.cue.text
    refresh(state, 108)
    assert state.cue.kind == "progress"


def test_pause_depressed_rolling_wpm_does_not_drive_phrase_advice():
    state = speaking(130)
    state.metrics.words_per_minute = 35
    assert choose_coach_cue(state, 100).kind == "progress"
    assert "130 WPM" in choose_coach_cue(state, 100).text


@pytest.mark.parametrize("pace", [70, 190])
def test_old_words_and_microphone_noise_do_not_trigger_pace(pace):
    state = speaking(pace)
    state.metrics.speech_last_word_age = 8
    state.metrics.silence_seconds = 0
    assert choose_coach_cue(state, 100).kind == "progress"
    assert "WPM" not in choose_coach_cue(state, 100).text
    state.metrics.speech_last_word_age = 0
    assert choose_coach_cue(state, 104).kind == "progress"


def test_silence_clears_correction_without_claiming_improvement():
    state = speaking(190)
    refresh(state, 100)
    state.metrics.speech_last_word_age = 3
    state.metrics.silence_seconds = 3
    refresh(state, 101)
    assert state.cue.kind == "progress"
    for now in (109, 140, 200):
        state.metrics.silence_seconds = now - 100
        refresh(state, now)
        assert state.cue.kind == "progress"
        assert "breath" not in state.cue.text.lower()
        assert "WPM" not in state.cue.text


def test_short_phrase_has_no_pace_warning():
    state = speaking(60)
    state.metrics.speech_recent_word_count = 3
    assert choose_coach_cue(state, 100).kind == "progress"


def test_only_new_finalized_fillers_generate_guidance():
    state = speaking()
    state.metrics.speech_filler_count = 12
    assert choose_coach_cue(state, 100).kind == "progress"
    state.coach_history = [[90, 30, 12]]
    assert choose_coach_cue(state, 100).kind == "progress"
    state.metrics.speech_filler_count = 14
    assert choose_coach_cue(state, 100).kind == "fillers"
    assert "2 new possible fillers in 10 words" in choose_coach_cue(state, 100).text
    state.coach_history = [[60, 30, 12]]
    assert choose_coach_cue(state, 100).kind == "progress"


def test_filler_advice_is_latched_then_recognizes_twenty_clean_words():
    state = speaking()
    state.coach_history = [[90, 30, 0]]
    state.metrics.speech_filler_count = 3
    refresh(state, 100)
    assert state.cue.kind == "fillers"
    refresh(state, 109)
    assert state.cue.kind == "progress"
    state.metrics.speech_word_count = 60
    refresh(state, 115)
    assert state.cue.kind == "improvement"
    assert "20 more words" in state.cue.text


@pytest.mark.parametrize("kind", ["camera", "position", "movement"])
def test_camera_correction_expires_and_fixed_signal_is_recognized(kind):
    state = speaking()
    state.metrics.camera_active = True
    state.metrics.face_present = kind != "camera"
    state.metrics.centered = kind != "position"
    state.metrics.face_motion = 0.2 if kind == "movement" else None
    state.metrics.face_missing_seconds = 8
    refresh(state, 100)
    assert state.cue.kind == kind
    refresh(state, 109)
    assert state.cue.kind == "progress"
    refresh(state, 140)
    assert state.cue.kind == "progress"
    state.metrics.face_present = True
    state.metrics.centered = True
    state.metrics.face_motion = 0.01
    refresh(state, 141)
    assert state.cue.kind == "improvement"
    refresh(state, 149)
    assert state.cue.kind == "progress"


def test_corrected_centering_is_not_held_for_readability():
    state = speaking()
    state.metrics.camera_active = state.metrics.face_present = True
    state.metrics.centered = False
    refresh(state, 100)
    state.metrics.centered = True
    refresh(state, 101)
    assert state.cue.kind == "improvement"


def test_missing_camera_or_unknown_motion_does_not_claim_measurements():
    state = speaking()
    state.metrics.face_present = True
    state.metrics.face_motion = 0.2
    state.metrics.centered = False
    state.metrics.eye_line_score = 0.1
    assert choose_coach_cue(state, 100).kind == "progress"
    state.metrics.camera_active = True
    state.metrics.centered = True
    state.metrics.face_motion = None
    assert choose_coach_cue(state, 100).kind == "progress"


def test_low_mic_needs_current_words_and_recovery_needs_current_words():
    state = speaking()
    state.metrics.volume = 0.05
    refresh(state, 100)
    assert state.cue.kind == "volume"
    state.metrics.speech_last_word_age = 8
    state.metrics.volume = 0.5
    refresh(state, 101)
    assert state.cue.kind == "progress"
    state.metrics.speech_last_word_age = 0
    refresh(state, 102)
    assert state.cue.kind == "improvement"


def test_manual_cue_holds_ten_seconds_even_when_paused():
    state = speaking(200)
    state.cue = CoachCue(
        "My custom cue", created_at=datetime.fromtimestamp(100, timezone.utc).isoformat(),
        expires_in_seconds=10, kind="manual",
    )
    refresh(state, 109)
    assert state.cue.kind == "manual"
    state.paused = True
    refresh(state, 109.5)
    assert state.cue.kind == "manual"
    refresh(state, 110)
    assert state.cue.kind == "paused"


def test_expired_manual_cue_yields_to_current_speech():
    state = speaking(200)
    state.cue = CoachCue(
        "Manual", created_at=datetime.fromtimestamp(100, timezone.utc).isoformat(),
        expires_in_seconds=10, kind="manual",
    )
    refresh(state, 110)
    assert state.cue.kind == "pace-fast"


def test_pausing_suppresses_correction_and_drops_recent_history():
    state = speaking(200)
    refresh(state, 100)
    state.paused = True
    refresh(state, 101)
    assert state.cue.kind == "paused"
    assert state.coach_history == []


def test_idle_does_not_coach_even_when_stale_sensors_claim_active():
    state = speaking(200)
    state.running = False
    state.metrics.camera_active = True
    state.metrics.face_missing_seconds = 30
    refresh(state, 100)
    assert state.cue.kind == "ready"
    assert state.coach_history == []
    assert state.coach_feedback == {}


def test_one_minute_warning_is_shown_once_not_forever():
    state = speaking()
    state.elapsed_seconds = 545
    refresh(state, 100)
    assert state.cue.kind == "time"
    refresh(state, 109)
    assert state.cue.kind == "progress"
    refresh(state, 140)
    assert state.cue.kind == "progress"


def test_history_is_bounded_numeric_and_old_state_loads():
    state = HudState.from_dict({"running": True})
    for now in range(100, 300):
        refresh(state, now)
    assert len(state.coach_history) <= 16
    assert all(isinstance(value, (int, float)) for row in state.coach_history for value in row)
    assert HudState.from_dict(state.to_dict()) == state


def language_event(state, event_id=1, at=100, issue="comparative-better"):
    state.metrics.speech_language_event_id = event_id
    state.metrics.speech_language_issue = issue
    state.metrics.speech_language_at = at


@pytest.mark.parametrize("previous", ["camera", "progress", "improvement", "pace-fast"])
def test_new_language_preempts_old_cues_and_existing_latches(previous):
    state = speaking(190)
    state.metrics.camera_active = True
    state.metrics.face_missing_seconds = 20
    state.cue = CoachCue(
        "Previous feedback", kind=previous,
        created_at=datetime.fromtimestamp(100, timezone.utc).isoformat(),
        expires_in_seconds=8,
    )
    state.coach_feedback = {"camera": 1, "pace-fast": 1}
    language_event(state, at=101)
    refresh(state, 101)
    assert state.cue.kind == "language"
    assert "better" in state.cue.text
    assert state.coach_feedback["language-event"] == 1


def test_language_precedes_recovery_and_camera_candidate():
    state = speaking()
    state.coach_feedback["pace-fast"] = 1
    state.metrics.camera_active = True
    state.metrics.face_missing_seconds = 8
    language_event(state)
    refresh(state, 100)
    assert state.cue.kind == "language"


def test_new_language_waits_three_seconds_then_replaces_previous_issue():
    state = speaking()
    language_event(state)
    refresh(state, 100)
    language_event(state, event_id=2, at=101, issue="agreement-we")
    refresh(state, 101)
    assert "better" in state.cue.text
    refresh(state, 103)
    assert "'we were'" in state.cue.text
    assert state.coach_feedback["language-event"] == 2
    assert state.cue.expires_in_seconds == 6
    refresh(state, 109)
    assert state.cue.kind == "progress"


def test_language_expires_from_final_observation_and_is_never_refreshed_by_publishers():
    state = speaking()
    language_event(state, at=98)
    refresh(state, 100)
    assert state.cue.expires_in_seconds == 6
    created = state.cue.created_at
    for now in (101, 103, 105):
        refresh(state, now)
        assert state.cue.created_at == created
    for now in (106, 109, 140):
        refresh(state, now)
        assert state.cue.kind == "progress"
    assert state.coach_feedback["language-event"] == 1


def test_same_language_pattern_with_new_evidence_can_be_shown_later():
    state = speaking()
    language_event(state)
    refresh(state, 100)
    refresh(state, 109)
    language_event(state, event_id=2, at=131)
    refresh(state, 131)
    assert state.cue.kind == "language"
    assert state.coach_feedback["language-event"] == 2


@pytest.mark.parametrize("at", [0, 90, 101])
def test_stale_invalid_future_language_event_is_not_advice(at):
    state = speaking()
    language_event(state, at=at)
    assert choose_coach_cue(state, 100).kind == "progress"


def test_unknown_issue_never_echoes_user_content():
    state = speaking()
    language_event(state, issue="confidentialsensitiveword")
    refresh(state, 100)
    assert "confidentialsensitiveword" not in state.cue.text
    assert state.cue.kind == "progress"


def test_manual_hold_wins_and_expired_language_is_not_replayed():
    state = speaking()
    state.cue = CoachCue(
        "Manual", kind="manual", expires_in_seconds=10,
        created_at=datetime.fromtimestamp(100, timezone.utc).isoformat(),
    )
    language_event(state, at=101)
    refresh(state, 101)
    assert state.cue.kind == "manual"
    refresh(state, 109)
    assert state.cue.kind == "manual"
    refresh(state, 110)
    assert state.cue.kind == "progress"
    language_event(state, event_id=2, at=111, issue="agreement-we")
    refresh(state, 111)
    assert state.cue.kind == "language"


def test_manual_hold_then_still_fresh_language_is_shown():
    state = speaking()
    state.cue = CoachCue(
        "Manual", kind="manual", expires_in_seconds=10,
        created_at=datetime.fromtimestamp(100, timezone.utc).isoformat(),
    )
    language_event(state, at=108)
    refresh(state, 109)
    assert state.cue.kind == "manual"
    refresh(state, 110)
    assert state.cue.kind == "language"
    assert state.cue.expires_in_seconds == 6


def test_language_does_not_require_eight_word_phrase_or_current_speaking():
    state = speaking()
    state.metrics.speech_recent_word_count = 2
    state.metrics.speech_last_word_age = 3
    language_event(state, at=98)
    refresh(state, 100)
    assert state.cue.kind == "language"


def test_failed_speech_producer_drops_language_cue():
    state = speaking()
    language_event(state)
    refresh(state, 100)
    state.metrics.speech_active = False
    refresh(state, 101)
    assert state.cue.kind == "progress"


def test_pause_and_stop_suppress_language_and_start_can_reuse_event_id():
    state = speaking()
    language_event(state)
    refresh(state, 100)
    state.paused = True
    refresh(state, 101)
    assert state.cue.kind == "paused"
    state.running = False
    state.paused = False
    refresh(state, 102)
    assert state.cue.kind == "ready"
    assert state.coach_feedback == {}
    state.running = True
    language_event(state, at=103)
    refresh(state, 103)
    assert state.cue.kind == "language"
