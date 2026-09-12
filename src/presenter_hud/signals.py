from .models import HudState


def speech_is_current(state: HudState, now: float) -> bool:
    metrics = state.metrics
    return (
        state.running and not state.paused and metrics.speech_active
        and metrics.speech_last_word_age is not None
        and 0 <= now - metrics.speech_observed_at <= 3
        and metrics.speech_last_word_age + max(0, now - metrics.speech_observed_at) < 2
    )
