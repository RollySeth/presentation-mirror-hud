"""Private, offline speech aggregates; no transcripts or audio are persisted.

Finalized timings contribute to committed counts; provisional timings provide
live pace without incrementing committed totals. Fillers are a lower bound: a model
may omit "um"/"uh". "You know" is optional and is counted as one filler.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import queue
import re
import threading
import time

from .language import LanguageAnalyzer, recognized_filler


class SpeechError(RuntimeError):
    """An actionable failure whose message contains no recognized content."""


def timed_words(result: dict, key: str):
    words = result.get(key, [])
    if not isinstance(words, list):
        raise SpeechError("Vosk returned invalid word timings; verify the model and Vosk version")
    for word in words:
        try:
            start, end = float(word["start"]), float(word["end"])
            token = word["word"]
            if (
                not isinstance(token, str) or not math.isfinite(start)
                or not math.isfinite(end) or start < 0 or end < start
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError):
            raise SpeechError("Vosk returned invalid word timings; verify the model and Vosk version") from None
        normalized = re.sub(r"[^a-z']", "", token.lower()).strip("'")
        if normalized:
            yield end, normalized


def control_signature(state) -> tuple:
    """Change session_id on each fresh start, never on pause/resume.

    Without it, identical stop/start operations between polls cannot be detected.
    """
    return (
        bool(state.running),
        bool(state.paused),
        getattr(state, "session_id", None),
    )


class SpeechTracker:
    """A rolling active-time clock, independent of audio hardware and Vosk.

    Before the first rehearsal, continuous preview counts normally. A fresh
    start resets preview/session totals. Pause freezes pace, counts and silence;
    stop preserves totals but reports zero WPM. Volume remains live in all modes.
    Resuming excludes paused wall-clock time. Session identity is preferred;
    old states fall back to running transitions and elapsed-time rewind.
    """

    def __init__(self, window_seconds: float = 30.0, count_you_know: bool = False):
        if not math.isfinite(window_seconds) or window_seconds <= 0:
            raise ValueError("WPM window must be positive and finite")
        self.window_seconds = window_seconds
        self.count_you_know = count_you_know
        self.mode = "preview"
        self.word_count = 0
        self.filler_count = 0
        self.language = LanguageAnalyzer()
        self.volume = 0.0
        self._history: deque[float] = deque()
        self._phrase: deque[float] = deque(maxlen=80)
        self._partial_ends: list[float] = []
        self._previous_token: tuple[str, float] | None = None
        self._last_word_end = -1.0
        self._last_voice = 0.0
        self._accumulated = 0.0
        self._active_since = 0.0
        self._control: tuple | None = None
        self._session_elapsed = 0.0
        self._has_session = False

    @property
    def accepting(self) -> bool:
        return self.mode in ("preview", "running")

    def clock(self, now: float) -> float:
        return max(0.0, self._accumulated + (
            now - self._active_since if self.accepting else 0.0
        ))

    def is_fresh_session(self, state) -> bool:
        if self._control is None:
            return bool(state.running)
        signature = control_signature(state)
        identity_changed = signature[2] != self._control[2]
        return identity_changed or (
            bool(state.running)
            and (
                not self._control[0]
                or float(state.elapsed_seconds) < self._session_elapsed - 0.01
            )
        )

    def needs_boundary(self, state) -> bool:
        return self._control != control_signature(state) or self.is_fresh_session(state)

    def synchronize(self, state, now: float) -> bool:
        boundary = self.needs_boundary(state)
        fresh = self.is_fresh_session(state)
        first = self._control is None
        elapsed = 0.0 if first or fresh else self.clock(now)
        if first or fresh:
            self.language = LanguageAnalyzer()
            self.word_count = self.filler_count = 0
            self._history.clear()
            self._phrase.clear()
            self._last_voice = 0.0
            self._has_session = (
                bool(state.running) or float(state.elapsed_seconds) > 0
                or bool(getattr(state, "session_id", ""))
            )
            if first and self._has_session:
                previous = getattr(state, "metrics", None)
                self.word_count = max(0, int(getattr(previous, "speech_word_count", 0)))
                self.filler_count = max(0, int(getattr(previous, "speech_filler_count", 0)))
                self.language.event_id = max(
                    0, int(getattr(previous, "speech_language_event_id", 0)),
                    int(getattr(state, "coach_feedback", {}).get("language-event", 0)),
                )
        if state.running:
            self._has_session = True
        self._accumulated = elapsed
        self._active_since = now
        self.mode = (
            "paused" if state.paused
            else "running" if state.running
            else "stopped" if self._has_session
            else "preview"
        )
        if boundary:
            self.language.clear_context()
            if not state.running and self._has_session:
                self.language = LanguageAnalyzer()
            self._phrase.clear()
            self._previous_token = None
            self._last_word_end = -1.0
            self._partial_ends.clear()
        self._control = control_signature(state)
        self._session_elapsed = float(state.elapsed_seconds)
        return boundary

    def observe_level(self, volume: float, now: float, silence_threshold: float) -> None:
        self.volume = min(1.0, max(0.0, volume))
        if self.accepting and self.volume >= silence_threshold:
            self._last_voice = self.clock(now)

    def accept_final(self, result: dict, origin: float) -> None:
        """Consume finalized timed words; origin is the decoder's active clock.

        A repeated final response, or a partial response, never adds words twice.
        Timings, not the 'text' field, identify committed words.
        """
        if not self.accepting:
            return
        self._partial_ends.clear()
        for end, normalized in timed_words(result, "result"):
            if end <= self._last_word_end:
                continue
            self._last_word_end = end
            timestamp = origin + end
            self.word_count += 1
            self._history.append(timestamp)
            self._phrase.append(timestamp)
            self._last_voice = max(self._last_voice, timestamp)
            self.language.accept(normalized, timestamp)
            if recognized_filler(normalized):
                self.filler_count += 1
            if (
                self.count_you_know
                and normalized == "know"
                and self._previous_token is not None
                and self._previous_token[0] == "you"
                and 0 <= timestamp - self._previous_token[1] <= 1.5
            ):
                self.filler_count += 1
            self._previous_token = (normalized, timestamp)

    def accept_partial(self, result: dict, origin: float) -> None:
        if not self.accepting:
            return
        self._partial_ends = [
            origin + end for end, _ in timed_words(result, "partial_result")
            if end > self._last_word_end
        ]
        if self._partial_ends:
            self._last_voice = max(self._last_voice, self._partial_ends[-1])

    def metrics(self, now: float) -> dict[str, float | int | bool | str | None]:
        elapsed = self.clock(now)
        while self._history and self._history[0] <= elapsed - self.window_seconds:
            self._history.popleft()
        denominator = min(self.window_seconds, elapsed)
        pending_recent = sum(end > elapsed - self.window_seconds for end in self._partial_ends)
        wpm = 60.0 * (len(self._history) + pending_recent) / denominator if denominator > 0 else 0.0
        # A short contiguous phrase excludes start-up silence and long pauses.
        # Keep rolling WPM unchanged for the HUD/session totals.
        recent = sorted(set(
            end for end in (*self._phrase, *self._partial_ends)
            if elapsed - 12 < end <= elapsed
        ))
        for index in range(len(recent) - 1, 0, -1):
            if recent[index] - recent[index - 1] > 2:
                recent = recent[index:]
                break
        last_age = max(0.0, elapsed - recent[-1]) if recent else None
        span = recent[-1] - recent[0] if len(recent) > 1 else 0.0
        recent_wpm = 60.0 * (len(recent) - 1) / span if span >= 2 else 0.0
        if not self.accepting:
            recent, recent_wpm, last_age = [], 0.0, None
        return {
            **self.language.metrics(elapsed),
            "speech_active": True,
            "microphone_active": True,
            "speech_word_count": self.word_count,
            "speech_pending_word_count": len(self._partial_ends),
            "speech_filler_count": self.filler_count,
            "speech_recent_word_count": len(recent),
            "speech_recent_wpm": round(recent_wpm, 2),
            "speech_last_word_age": None if last_age is None else round(last_age, 2),
            "words_per_minute": round(0.0 if self.mode == "stopped" else wpm, 2),
            "volume": round(self.volume, 4),
            "silence_seconds": round(max(0.0, elapsed - self._last_voice), 2),
        }


def load_vosk_model(model_path: str | Path):
    path = Path(model_path).expanduser()
    if not (path / "am" / "final.mdl").is_file() or not (path / "conf" / "mfcc.conf").is_file():
        raise SpeechError(
            "Local Vosk model is missing/incomplete. Run deploy/speech_prepare.py "
            "and pass --model models/vosk-model-small-en-us-0.15"
        )
    # Avoid BLAS thread oversubscription on the Pi's four shared CPU cores.
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    try:
        import vosk
    except ImportError:
        raise SpeechError("Install requirements-speech.txt with the app's Python interpreter") from None
    vosk.SetLogLevel(-1)
    try:
        return vosk.Model(str(path))
    except Exception:
        raise SpeechError("Vosk could not load the local model; check free RAM and re-download the model") from None


class OfflineRecognizer:
    def __init__(self, model, sample_rate: int):
        self._model = model
        self._sample_rate = float(sample_rate)
        self.reset()

    def reset(self) -> None:
        import vosk

        # Vosk 0.3.45 Reset() retains timing offsets. Recreate only the decoder,
        # sharing the loaded model, so each rehearsal/resume starts at time zero.
        self._recognizer = vosk.KaldiRecognizer(self._model, self._sample_rate)
        self._recognizer.SetWords(True)
        self._recognizer.SetPartialWords(True)

    @staticmethod
    def _parse(value: str) -> dict:
        try:
            result = json.loads(value)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (TypeError, ValueError):
            raise SpeechError("Invalid Vosk response; check the installed Vosk version") from None

    def feed(self, pcm: bytes) -> dict | None:
        if self._recognizer.AcceptWaveform(pcm):
            return self._parse(self._recognizer.Result())
        partial = self._parse(self._recognizer.PartialResult())
        partial.setdefault("partial_result", [])
        return partial

    def finish(self) -> dict:
        return self._parse(self._recognizer.FinalResult())

@dataclass(frozen=True)
class AudioBlock:
    pcm: bytes
    started_at: float
    ended_at: float


class BoundedAudioQueue:
    """The callback fails closed on overflow; it never silently sheds audio."""

    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("Audio queue capacity must be at least one")
        self.blocks: queue.Queue[AudioBlock] = queue.Queue(maxsize=capacity)
        self.failed = threading.Event()
        self.failure: str | None = None

    def fail(self, reason: str) -> None:
        if not self.failed.is_set():
            self.failure = reason
            self.failed.set()

    def put(self, block: AudioBlock) -> None:
        if self.failed.is_set():
            return
        try:
            self.blocks.put_nowait(block)
        except queue.Full:
            self.fail("Audio capture queue overflow: recognition cannot keep up. Use the small model and reduce Pi CPU load")

    def check(self) -> None:
        if self.failed.is_set():
            raise SpeechError(self.failure or "Audio capture failed")

    def get(self, timeout: float, max_backlog_seconds: float) -> AudioBlock | None:
        self.check()
        try:
            block = self.blocks.get(timeout=timeout)
        except queue.Empty:
            self.check()
            return None
        self.check()
        if time.monotonic() - block.ended_at > max_backlog_seconds:
            raise SpeechError("Audio CPU backlog exceeded the limit; use the small model and reduce Pi CPU load")
        return block


class MicrophoneCapture:
    def __init__(self, device=None, block_seconds: float = 0.25, backlog_seconds: float = 4.0):
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError:
            raise SpeechError("Install requirements-speech.txt with the app's Python interpreter") from None
        self.np = np
        self.sd = sd
        self.device = device
        self.sample_rate = int(sd.query_devices(device, "input")["default_samplerate"])
        if self.sample_rate < 16000:
            raise SpeechError("Microphone native rate is below 16 kHz; select the C920 input device")
        self.block_size = max(1, int(self.sample_rate * block_seconds))
        self.audio = BoundedAudioQueue(max(1, math.ceil(backlog_seconds / block_seconds)))
        sd.check_input_settings(device=device, channels=1, dtype="int16", samplerate=self.sample_rate)
        self.stream = None

    def _callback(self, data, frames, timing, status) -> None:
        if status:
            self.audio.fail("PortAudio input overflow/status error: check the microphone and reduce CPU load")
            raise self.sd.CallbackAbort
        try:
            now = time.monotonic()
            # PortAudio ADC time preserves acquisition age even if callback scheduling slips.
            age = max(0.0, float(timing.currentTime) - float(timing.inputBufferAdcTime))
            started_at = now - age
            self.audio.put(AudioBlock(bytes(data), started_at, started_at + frames / self.sample_rate))
        except Exception:
            self.audio.fail("Audio callback failed; verify the input device and sounddevice installation")
            raise self.sd.CallbackAbort from None
        if self.audio.failed.is_set():
            raise self.sd.CallbackAbort

    def __enter__(self):
        self.stream = self.sd.RawInputStream(
            device=self.device,
            channels=1,
            samplerate=self.sample_rate,
            dtype="int16",
            blocksize=self.block_size,
            callback=self._callback,
        )
        try:
            self.stream.start()
        except BaseException:
            self.stream.close()
            raise
        return self

    def volume(self, pcm: bytes) -> float:
        samples = self.np.frombuffer(pcm, dtype="<i2").astype(self.np.float32)
        if not samples.size:
            raise SpeechError("Microphone returned an empty audio block")
        rms = float(self.np.sqrt(self.np.mean(samples * samples))) / 32768.0
        return min(1.0, rms * 8.0)

    def __exit__(self, *_):
        if self.stream is not None:
            try:
                self.stream.abort()
            finally:
                self.stream.close()


class SpeechProcessor:
    """Synchronize recognition boundaries with rehearsal controls."""

    def __init__(self, recognizer, tracker: SpeechTracker, silence_threshold: float = 0.12):
        self.recognizer = recognizer
        self.tracker = tracker
        self.silence_threshold = silence_threshold
        self.origin: float | None = None
        self.accept_after = float("-inf")

    def synchronize(self, state, now: float) -> None:
        if self.tracker.needs_boundary(state):
            fresh = self.tracker.is_fresh_session(state)
            if self.origin is not None and self.tracker.accepting and not fresh:
                self.tracker.accept_final(self.recognizer.finish(), self.origin)
            self.recognizer.reset()
            # Exclude buffered preview/paused audio from the new context intentionally.
            # This is a control boundary, never an overflow recovery strategy.
            if self.tracker._control is not None:
                self.accept_after = now
            self.origin = None
        self.tracker.synchronize(state, now)

    def process(self, block: AudioBlock, volume: float) -> None:
        if block.started_at < self.accept_after:
            self.tracker.volume = min(1.0, max(0.0, volume))
            return
        self.tracker.observe_level(volume, block.ended_at, self.silence_threshold)
        if not self.tracker.accepting:
            return
        if self.origin is None:
            self.origin = self.tracker.clock(block.started_at)
        result = self.recognizer.feed(block.pcm)
        if result is not None:
            if "partial_result" in result:
                self.tracker.accept_partial(result, self.origin)
            else:
                self.tracker.accept_final(result, self.origin)

    def finish(self) -> None:
        if self.origin is not None and self.tracker.accepting:
            self.tracker.accept_final(self.recognizer.finish(), self.origin)
            self.origin = None
