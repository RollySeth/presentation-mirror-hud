"""Small optional wording suggestions, not a grammar/content review.

Only finalized ASR tokens enter this bounded, memory-only context. Recognition
errors, unmarked quotations and dialect can defeat these deliberately few rules.
Only fixed issue codes and numeric event metadata leave the analyzer.
"""
from __future__ import annotations

from collections import deque
import re


LANGUAGE_ADVICE = {
    "comparative-better": "Possible formal wording: 'more better' -> 'better'.",
    "comparative-easier": "Possible formal wording: 'more easier' -> 'easier'.",
    "agreement-i": "Possible standard English: 'I has' -> 'I have'.",
    "agreement-they": "Possible standard English: 'they was' -> 'they were'.",
    "agreement-we": "Possible standard English: 'we was' -> 'we were'.",
    "agreement-you": "Possible standard English: 'you was' -> 'you were'.",
    "redundant-return": "For concise wording, consider 'return' instead of 'return back'.",
    "redundant-repeat": "For concise wording, consider 'repeat' instead of 'repeat again'.",
    "repeat-basically": "Repeated 'basically': try starting directly with your point.",
    "repeat-actually": "Repeated 'actually': omit it unless you are making a contrast.",
    "repeat-really": "Repeated 'really': try a specific detail instead of an intensifier.",
}
_PAIRS = {
    ("more", "better"): "comparative-better",
    ("more", "easier"): "comparative-easier",
    ("i", "has"): "agreement-i",
    ("they", "was"): "agreement-they",
    ("we", "was"): "agreement-we",
    ("you", "was"): "agreement-you",
    ("return", "back"): "redundant-return",
    ("repeat", "again"): "redundant-repeat",
}
_VAGUE = ("basically", "actually", "really")
_QUOTATION = {
    "say", "says", "said", "saying", "quote", "quoted", "quoting",
    "word", "words", "phrase", "title", "called", "wrote", "writes", "letter",
}
_FILLER = re.compile(r"(?:uh|u(?:h)?m+|hm+|m{2,})\Z")
EVENT_TTL = 8.0
LANGUAGE_COOLDOWN = 3.0


def recognized_filler(token: str) -> bool:
    """Match a whole normalized recognized alias, never infer an acoustic event."""
    return _FILLER.fullmatch(token) is not None


class LanguageAnalyzer:
    def __init__(self, event_id: int = 0):
        self.context: deque[str] = deque(maxlen=40)
        self.event_id = event_id
        self.issue = ""
        self.event_end: float | None = None
        self._observed_at: float | None = None
        self._last_end: float | None = None
        self._last_emitted: dict[str, float] = {}
        self._repeated: set[str] = set()

    def clear_context(self) -> None:
        self.context.clear()
        self.issue = ""
        self.event_end = None
        self._observed_at = None
        self._last_end = None
        self._repeated.clear()

    def accept(self, token: str, end: float) -> None:
        if self._last_end is not None and end - self._last_end > 2:
            self.clear_context()
        self._last_end = end
        # A pathological decoder token must not make the memory bound unbounded.
        self.context.append(token if len(token) <= 32 else "")
        recent = list(self.context)[-8:]
        quoted = bool(_QUOTATION.intersection(recent))
        issue = _PAIRS.get(tuple(recent[-2:]), "") if not quoted else ""
        for word in _VAGUE:
            count = self.context.count(word)
            if count < 3:
                self._repeated.discard(word)
            elif word == token and word not in self._repeated:
                self._repeated.add(word)
                if not quoted and not issue:
                    issue = f"repeat-{word}"
        if issue and end - self._last_emitted.get(issue, float("-inf")) >= 30:
            self.event_id += 1
            self.issue = issue
            self.event_end = end
            self._observed_at = None
            self._last_emitted[issue] = end

    def metrics(self, elapsed: float) -> dict:
        # Finalization can lag the spoken pattern by an entire sentence. Start
        # readability once when the finalized event is first observed, not spoken.
        if self.event_end is not None and self._observed_at is None:
            self._observed_at = elapsed
        age = None if self._observed_at is None else max(0.0, elapsed - self._observed_at)
        return {
            "speech_language_event_id": self.event_id,
            "speech_language_issue": self.issue if age is not None and age < EVENT_TTL else "",
            "speech_language_age": age,
        }
