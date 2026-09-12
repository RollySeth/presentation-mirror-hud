"""Observation-driven coaching using only bounded numeric aggregates.

Corrections last at most eight seconds, once per unresolved issue. Silence is
not a delivery fault. Phrase pace needs fresh word timings, not microphone
noise or the pause-depressed rolling WPM displayed in session statistics.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import time

from .models import CoachCue, HudState
from .language import EVENT_TTL, LANGUAGE_ADVICE, LANGUAGE_COOLDOWN
from .signals import speech_is_current as _speaking


def _language_cue(state: HudState, now: float) -> CoachCue | None:
    metrics = state.metrics
    text = LANGUAGE_ADVICE.get(metrics.speech_language_issue)
    age = now - metrics.speech_language_at
    if (
        state.running and not state.paused and metrics.speech_active
        and text and metrics.speech_language_event_id > 0
        and metrics.speech_language_at > 0 and 0 <= age < EVENT_TTL
    ):
        return CoachCue(text, "guide", kind="language", expires_in_seconds=EVENT_TTL - age)
    return None


def _new_language_cue(state: HudState, now: float) -> CoachCue | None:
    if (
        state.metrics.speech_language_event_id > state.coach_feedback.get("language-event", 0)
        and now - state.coach_last_shown.get("language", float("-inf")) >= LANGUAGE_COOLDOWN
    ):
        return _language_cue(state, now)
    return None


def _candidates(state: HudState, now: float) -> list[CoachCue]:
    if not state.running or state.paused:
        return []
    metrics = state.metrics
    cues = []
    speaking = _speaking(state, now)
    paced = speaking and metrics.speech_recent_word_count >= 8
    pace = metrics.speech_recent_wpm
    if paced and pace > 175:
        cues.append(CoachCue(
            f"Recent phrase: {pace:.0f} WPM. Slow your next sentence; pause at its end.",
            "guide", kind="pace-fast",
        ))
    baseline = next((row for row in state.coach_history if now - row[0] <= 30), None)
    if baseline is not None and speaking:
        new_fillers = metrics.speech_filler_count - baseline[2]
        new_words = metrics.speech_word_count - baseline[1]
        if new_fillers >= 2 and new_words >= 8 and new_fillers / new_words > 0.08:
            cues.append(CoachCue(
                f"{int(new_fillers)} new possible fillers in {int(new_words)} words. Try a silent beat instead.",
                "guide", kind="fillers",
            ))
    if metrics.camera_active:
        if not metrics.face_present and metrics.face_missing_seconds >= 6:
            cues.append(CoachCue(
                f"Face out of view for {metrics.face_missing_seconds:.0f}s. Step back into camera view.",
                "guide", kind="camera",
            ))
        elif metrics.face_present:
            if not metrics.centered:
                cues.append(CoachCue("Face off-center. Move gently toward the camera's center.", "guide", kind="position"))
            if metrics.face_motion is not None and metrics.face_motion > 0.12:
                cues.append(CoachCue("Face movement is high. Hold a steadier position for your next sentence.", "guide", kind="movement"))
    if speaking and metrics.microphone_active and 0 < metrics.volume < 0.12:
        cues.append(CoachCue(
            f"Mic level {metrics.volume:.0%} while speaking. Try a clearer, slightly louder sentence.",
            "guide", kind="volume",
        ))
    if paced and 0 < pace < 95:
        cues.append(CoachCue(
            f"Recent phrase: {pace:.0f} WPM. Link the words within your next sentence.",
            "guide", kind="pace-slow",
        ))
    if 0 < state.duration_seconds - state.elapsed_seconds <= 60:
        cues.insert(0, CoachCue("One minute or less left. Lead with your final takeaway.", "time", kind="time"))
    return cues


def choose_coach_cue(state: HudState, now: float | None = None) -> CoachCue:
    now = time.time() if now is None else now
    return _new_language_cue(state, now) or next((
        cue for cue in _candidates(state, now)
        if cue.kind not in state.coach_feedback
        and now - state.coach_last_shown.get(cue.kind, float("-inf")) >= 30
    ), progress_cue(state, now))


def progress_cue(state: HudState, now: float | None = None) -> CoachCue:
    now = time.time() if now is None else now
    metrics = state.metrics
    if state.paused:
        return CoachCue("Rehearsal paused. Resume when ready.", kind="paused")
    if not state.running:
        return CoachCue("Ready when you are. Start a rehearsal for live coaching.", kind="ready")
    words = metrics.speech_word_count + metrics.speech_pending_word_count
    if _speaking(state, now) and metrics.speech_recent_word_count >= 8 and metrics.speech_recent_wpm > 0:
        return CoachCue(
            f"{words} words so far. Recent phrase: {metrics.speech_recent_wpm:.0f} WPM.",
            kind="progress",
        )
    if words:
        return CoachCue(f"{words} words so far. Listening for your next phrase.", kind="progress")
    return CoachCue("Listening. Pace feedback starts after a few recognized words.", kind="progress")


def _recovery(state: HudState, now: float) -> CoachCue | None:
    metrics = state.metrics
    speaking = _speaking(state, now)
    measured_pace = speaking and metrics.speech_recent_word_count >= 8
    feedback = state.coach_feedback
    recovered = {
        "pace-fast": measured_pace and 105 <= metrics.speech_recent_wpm <= 165,
        "pace-slow": measured_pace and 105 <= metrics.speech_recent_wpm <= 165,
        "camera": metrics.camera_active and metrics.face_present,
        "position": metrics.camera_active and metrics.face_present and metrics.centered,
        "movement": (
            metrics.camera_active and metrics.face_present
            and metrics.face_motion is not None and metrics.face_motion <= 0.06
        ),
        "volume": speaking and metrics.microphone_active and metrics.volume >= 0.16,
    }
    if "fillers" in feedback:
        if metrics.speech_filler_count > feedback["fillers"]:
            feedback["fillers"] = metrics.speech_filler_count
            feedback["filler-words"] = metrics.speech_word_count
        recovered["fillers"] = (
            speaking and metrics.speech_word_count - feedback.get("filler-words", metrics.speech_word_count) >= 20
            and metrics.speech_filler_count == feedback["fillers"]
        )
    messages = {
        "pace-fast": f"Pace settled to {metrics.speech_recent_wpm:.0f} WPM. Keep this rhythm.",
        "pace-slow": f"Pace now {metrics.speech_recent_wpm:.0f} WPM. Keep this rhythm.",
        "camera": "Face back in view. Continue from here.",
        "position": "You're centered again. Keep this position comfortable.",
        "movement": "Face movement settled. Keep this steadier position.",
        "volume": f"Mic level is up to {metrics.volume:.0%}. Keep this clearer level.",
        "fillers": "20 more words without another detected filler. Keep leaving space between thoughts.",
    }
    cue = None
    for kind, fixed in recovered.items():
        if fixed and kind in feedback:
            del feedback[kind]
            if kind == "fillers":
                feedback.pop("filler-words", None)
            cue = cue or CoachCue(messages[kind], kind="improvement", expires_in_seconds=6)
    return cue


def refresh_coach(state: HudState, now: float | None = None) -> None:
    """Run inside StateStore.update: shared latches survive separate producers."""
    now = time.time() if now is None else now
    try:
        created = datetime.fromisoformat(state.cue.created_at)
        age = now - created.replace(tzinfo=created.tzinfo or timezone.utc).timestamp()
    except (TypeError, ValueError):
        logging.getLogger(__name__).warning("Replacing a coach cue with an invalid timestamp")
        age = float("inf")
    if not state.running:
        state.coach_history.clear()
        state.coach_feedback.clear()
        state.coach_last_shown.clear()
    elif state.paused:
        state.coach_history.clear()
    else:
        history = state.coach_history
        history[:] = [row for row in history if 0 <= now - row[0] <= 30][-15:]
        if not history or now - history[-1][0] >= 2:
            history.append([now, state.metrics.speech_word_count, state.metrics.speech_filler_count])
    if state.cue.kind == "manual" and 0 <= age < state.cue.expires_in_seconds:
        return
    if not state.running or state.paused:
        candidate = progress_cue(state, now)
    else:
        recovery = _recovery(state, now)
        active = {cue.kind for cue in _candidates(state, now)}
        language = _new_language_cue(state, now)
        candidate = language or recovery or choose_coach_cue(state, now)
        urgent = language is not None or (
            candidate.kind in ("pace-fast", "time") and candidate.kind != state.cue.kind
        )
        if (
            state.cue.kind == "language" and state.metrics.speech_active
            and 0 <= age < state.cue.expires_in_seconds
        ):
            if age < LANGUAGE_COOLDOWN or (language is None and not recovery):
                return
        current_valid = (
            state.cue.kind in active
            or state.cue.kind in ("progress", "improvement")
        )
        if current_valid and 0 <= age < min(8, state.cue.expires_in_seconds) and not recovery and not urgent:
            return
    if (
        candidate.text == state.cue.text and candidate.kind == state.cue.kind
        and candidate.kind != "language"
    ):
        return
    candidate.created_at = datetime.fromtimestamp(now, timezone.utc).isoformat()
    if candidate.kind == "language":
        state.coach_last_shown["language"] = now
        state.coach_feedback["language-event"] = state.metrics.speech_language_event_id
    elif candidate.kind not in ("ready", "paused", "progress", "improvement"):
        candidate.expires_in_seconds = 8
        state.coach_last_shown[candidate.kind] = now
        state.coach_feedback[candidate.kind] = (
            state.metrics.speech_filler_count if candidate.kind == "fillers" else 1.0
        )
        if candidate.kind == "fillers":
            state.coach_feedback["filler-words"] = state.metrics.speech_word_count
    state.cue = candidate
