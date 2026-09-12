"""Facial-expression estimates and observable activity, not a reading of feelings."""
import time
from typing import TypedDict

from .models import HudState
from .signals import speech_is_current


class ExpressionAvatar(TypedDict):
    mode: str
    label: str
    emoji: str
    voice: str
    activity: str
    mouth: float
    eyes: str
    brows: str
    mouth_shape: str
    estimated: bool


EXPRESSION_LABELS = {
    "angry": ("Angry (est.)", "\U0001f620"),
    "disgust": ("Disgust (est.)", "\U0001f616"),
    "fearful": ("Fearful (est.)", "\U0001f628"),
    "happy": ("Happy (est.)", "\U0001f604"),
    "neutral": ("Neutral (est.)", "\U0001f610"),
    "sad": ("Sad (est.)", "\U0001f61e"),
    "surprised": ("Surprised (est.)", "\U0001f632"),
}
FACE_STYLES = {
    "angry": ("narrow", "angry", "frown"),
    "disgust": ("narrow", "asymmetric", "slant"),
    "fearful": ("wide", "worried", "open"),
    "happy": ("smile", "none", "smile"),
    "neutral": ("normal", "none", "flat"),
    "sad": ("normal", "worried", "frown"),
    "surprised": ("wide", "raised", "round"),
    "smile": ("smile", "none", "smile"),
    "no-smile": ("normal", "none", "flat"),
    "checking": ("normal", "none", "flat"),
}


def coach_avatar(state: HudState) -> dict[str, str | float]:
    metrics = state.metrics
    if state.paused:
        mode, label, emoji = "paused", "Paused", "\u23f8\ufe0f"
    elif metrics.microphone_active and metrics.volume >= 0.08:
        mode, label, emoji = "speaking", "Mic activity", "\U0001f399\ufe0f"
    elif metrics.camera_active and not metrics.face_present:
        mode, label, emoji = "looking", "Finding you", "\U0001f440"
    elif metrics.face_present and not metrics.centered:
        mode, label, emoji = "align", "Re-center", "\u2194\ufe0f"
    elif metrics.speech_active:
        mode, label, emoji = "listening", "Listening", "\U0001f442"
    else:
        mode, label, emoji = "ready", "Ready", "\U0001f642"
    return {
        "mode": mode, "label": label, "emoji": emoji,
        "mouth": min(1.0, max(0.0, metrics.volume)) if mode == "speaking" else 0.0,
    }


def expression_avatar(state: HudState, now: float | None = None) -> ExpressionAvatar:
    metrics = state.metrics
    now = time.time() if now is None else now
    if not metrics.camera_active:
        mode, label, emoji = "off", "Camera off", "\u2014"
    elif not metrics.expression_active:
        mode, label, emoji = "unavailable", "No detector", "\u2014"
    elif not metrics.face_present:
        mode, label, emoji = "no-face", "No face in view", "\U0001f50e"
    elif metrics.expression_model_active:
        if (
            metrics.expression_label in EXPRESSION_LABELS
            and .55 <= metrics.expression_confidence <= 1
            and 0 <= now - metrics.expression_observed_at <= 5
        ):
            mode = metrics.expression_label
            label, emoji = EXPRESSION_LABELS[mode]
        else:
            mode, label, emoji = "uncertain", "Expression unclear", "\u2754"
    elif metrics.smile_detected is True:
        mode, label, emoji = "smile", "Smiling", "\U0001f642"
    elif metrics.smile_detected is False:
        mode, label, emoji = "no-smile", "Face visible", "\U0001f610"
    else:
        mode, label, emoji = "checking", "Face visible", "\u2026"
    if state.paused:
        voice, activity = "paused", "Paused"
    elif speech_is_current(state, now):
        voice, activity = "speaking", "Speaking"
        if mode not in FACE_STYLES:
            emoji = "\U0001f5e3\ufe0f"
    elif not metrics.microphone_active:
        voice, activity = "off", "Mic off"
    elif metrics.volume >= .08:
        voice, activity = "sound", "Sound detected"
    else:
        voice, activity = "quiet", "Quiet"
    eyes, brows, mouth_shape = FACE_STYLES.get(mode, ("unknown", "none", "unknown"))
    if voice == "speaking" and eyes == "unknown":
        eyes = "normal"
    return {
        "mode": mode, "label": label, "emoji": emoji,
        "voice": voice, "activity": activity,
        "mouth": min(1.0, max(.35, metrics.volume)) if voice == "speaking" else 0.0,
        "eyes": eyes, "brows": brows, "mouth_shape": mouth_shape,
        "estimated": mode in EXPRESSION_LABELS,
    }
