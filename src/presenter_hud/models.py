from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LiveMetrics:
    camera_active: bool = False
    microphone_active: bool = False
    speech_active: bool = False
    speech_word_count: int = 0
    speech_pending_word_count: int = 0
    speech_filler_count: int = 0
    speech_recent_word_count: int = 0
    speech_recent_wpm: float = 0.0
    speech_last_word_age: float | None = None
    speech_observed_at: float = 0.0
    speech_language_event_id: int = 0
    speech_language_issue: str = ""
    speech_language_at: float = 0.0
    words_per_minute: float = 0.0
    volume: float = 0.0
    silence_seconds: float = 0.0
    face_present: bool = False
    expression_active: bool = False
    expression_model_active: bool = False
    expression_label: str = ""
    expression_confidence: float = 0.0
    expression_observed_at: float = 0.0
    smile_detected: bool | None = None
    face_missing_seconds: float = 0.0
    face_center_x: float = 0.5
    face_center_y: float = 0.5
    face_motion: float | None = None
    centered: bool = True
    eye_line_score: float = 1.0
    gesture_energy: float = 0.0


@dataclass
class CoachCue:
    text: str
    level: str = "encourage"
    created_at: str = field(default_factory=utc_now)
    expires_in_seconds: float = 5.0
    kind: str = "encourage"


@dataclass
class HudState:
    running: bool = False
    paused: bool = False
    mirror: bool = True
    session_title: str = "Presentation Rehearsal"
    duration_seconds: int = 600
    elapsed_seconds: float = 0.0
    slide_index: int = 0
    slide_paths: list[str] = field(default_factory=list)
    slide_opacity: int = 255
    slide_brightness: float = 1.4
    cue: CoachCue = field(default_factory=lambda: CoachCue("You have this. Start when you are ready."))
    metrics: LiveMetrics = field(default_factory=LiveMetrics)
    updated_at: str = field(default_factory=utc_now)
    session_id: str = ""
    coach_last_shown: dict[str, float] = field(default_factory=dict)
    coach_feedback: dict[str, float] = field(default_factory=dict)
    coach_history: list[list[float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "HudState":
        data = dict(value)
        data["metrics"] = LiveMetrics(**data.get("metrics", {}))
        data["cue"] = CoachCue(**data.get("cue", {"text": "You have this."}))
        return cls(**data)
