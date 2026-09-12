"""Microphone levels while idle; offline word recognition only during rehearsals."""
from __future__ import annotations

import argparse
import logging
import math
import signal
import threading
import time

from .speech import (
    MicrophoneCapture,
    OfflineRecognizer,
    SpeechError,
    SpeechProcessor,
    SpeechTracker,
    control_signature,
    load_vosk_model,
)
from .state_store import StateStore
from .coach import refresh_coach


def device_argument(value: str) -> int | str:
    return int(value) if value.isdecimal() else value


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be positive and finite")
    return number


def mark_inactive(state) -> None:
    state.metrics.speech_active = False
    state.metrics.microphone_active = False
    state.metrics.volume = 0.0
    state.metrics.words_per_minute = 0.0
    state.metrics.silence_seconds = 0.0
    state.metrics.speech_pending_word_count = 0
    state.metrics.speech_recent_word_count = 0
    state.metrics.speech_recent_wpm = 0.0
    state.metrics.speech_last_word_age = None
    state.metrics.speech_observed_at = 0.0
    state.metrics.speech_language_issue = ""
    state.metrics.speech_language_at = 0.0


def publish(store, tracker: SpeechTracker, observed_state, now: float) -> None:
    signature = control_signature(observed_state)
    elapsed = float(observed_state.elapsed_seconds)
    updates = tracker.metrics(now)
    language_age = updates.pop("speech_language_age")
    observed_at = time.time()

    def mutate(state):
        # Never overwrite a fresh start/pause with recognition from its predecessor.
        if control_signature(state) != signature or float(state.elapsed_seconds) < elapsed - 0.01:
            return
        if not updates["speech_language_issue"]:
            state.metrics.speech_language_at = 0.0
        elif updates["speech_language_event_id"] != state.metrics.speech_language_event_id:
            # Anchor once to first observation of the finalized event, not its
            # earlier acoustic word-end or every subsequent camera/HUD poll.
            state.metrics.speech_language_at = observed_at - language_age
        for name, value in updates.items():
            setattr(state.metrics, name, value)
        state.metrics.speech_observed_at = observed_at
        refresh_coach(state)

    store.update(mutate)


def publish_level(store, volume: float) -> None:
    def mutate(state):
        if state.running:
            return
        state.metrics.speech_active = False
        state.metrics.microphone_active = True
        state.metrics.volume = volume
        state.metrics.words_per_minute = 0.0
        state.metrics.silence_seconds = 0.0
        state.metrics.speech_pending_word_count = 0
        state.metrics.speech_recent_word_count = 0
        state.metrics.speech_recent_wpm = 0.0
        state.metrics.speech_last_word_age = None
        state.metrics.speech_observed_at = 0.0
        state.metrics.speech_language_issue = ""
        state.metrics.speech_language_at = 0.0

    store.update(mutate)


def capture_mode(store, args, stopped: threading.Event, recognizing: bool) -> None:
    processor = None
    observed = None
    finished_normally = False
    try:
        store.update(mark_inactive)
        model = load_vosk_model(args.model) if recognizing else None
        if stopped.is_set():
            return
        capture = MicrophoneCapture(device=args.audio_device, backlog_seconds=args.max_backlog)
        if model is not None:
            recognizer = OfflineRecognizer(model, capture.sample_rate)
            tracker = SpeechTracker(args.window, args.count_you_know)
            processor = SpeechProcessor(recognizer, tracker, args.silence_threshold)
        observed = store.load()
        if processor is not None:
            processor.synchronize(observed, time.monotonic())
        if bool(observed.running) != recognizing:
            return
        with capture:
            logging.info(
                "Microphone ready at %s Hz; word recognition %s. No audio/transcripts saved.",
                capture.sample_rate, "active" if recognizing else "waiting for rehearsal",
            )
            last_audio = time.monotonic()
            while not stopped.is_set():
                block = capture.audio.get(timeout=0.2, max_backlog_seconds=args.max_backlog)
                now = time.monotonic()
                observed = store.load()
                if processor is not None:
                    processor.synchronize(observed, now)
                if bool(observed.running) != recognizing:
                    if processor is not None:
                        publish(store, processor.tracker, observed, now)
                    break
                if block is None:
                    if now - last_audio > max(3.0, args.max_backlog):
                        raise SpeechError("Microphone stopped delivering audio; check the C920 connection and audio device")
                else:
                    last_audio = now
                    volume = capture.volume(block.pcm)
                    if processor is not None:
                        processor.process(block, volume)
                    else:
                        publish_level(store, volume)
                capture.audio.check()
                if block is not None and time.monotonic() - block.ended_at > args.max_backlog:
                    raise SpeechError("Recognition CPU backlog exceeded the limit; reduce Pi CPU load or use the small model")
                if processor is not None:
                    publish(store, processor.tracker, observed, time.monotonic())
        finished_normally = True
    finally:
        try:
            if processor is not None and finished_normally:
                observed = store.load()
                processor.synchronize(observed, time.monotonic())
                processor.finish()
                publish(store, processor.tracker, observed, time.monotonic())
        finally:
            store.update(mark_inactive)
            logging.info("Offline speech stopped; microphone closed and active flags cleared.")


def run(store, args, stopped: threading.Event) -> None:
    while not stopped.is_set():
        recognizing = bool(store.load().running)
        capture_mode(store, args, stopped, recognizing)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default="data/state.json")
    parser.add_argument("--model", required=True, help="Unpacked local Vosk small English model directory")
    parser.add_argument("--audio-device", type=device_argument, help="Input device index/name; omitted uses the system default")
    parser.add_argument("--window", type=positive_float, default=30.0, help="Rolling active-time WPM window in seconds")
    parser.add_argument("--max-backlog", type=positive_float, default=4.0, help="Fail rather than silently drop delayed audio")
    parser.add_argument("--silence-threshold", type=positive_float, default=0.12, help="Volume threshold (RMS times eight)")
    parser.add_argument("--count-you-know", action="store_true")
    args = parser.parse_args()
    if args.silence_threshold > 1:
        parser.error("--silence-threshold must be at most 1")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    try:
        run(StateStore(args.state), args, stopped)
    except SpeechError as error:
        logging.error("%s", error)
        raise SystemExit(1) from None
    except Exception as error:
        # Third-party exception text can contain decoder content. Report type only.
        logging.error(
            "Offline speech failed (%s). Check model/RAM, input device access, and requirements-speech.txt.",
            type(error).__name__,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
