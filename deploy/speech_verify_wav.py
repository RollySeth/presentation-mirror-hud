"""TEST ONLY: verify offline recognition of a known synthetic PCM WAV.

Never point this helper at user recordings. Output contains aggregate results
only, not recognized words. It never opens the microphone or changes state.json.
Example: PYTHONPATH=src .venv/bin/python deploy/speech_verify_wav.py
  --model models/vosk-model-small-en-us-0.15
  --synthetic-test-wav models/speech-test-synthetic.wav --expect-word hello
"""
from __future__ import annotations

import argparse
import json
import math
import time
from types import SimpleNamespace
import wave

from presenter_hud.speech import OfflineRecognizer, SpeechTracker, load_vosk_model


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--synthetic-test-wav", required=True)
    parser.add_argument("--expect-word", action="append", required=True)
    parser.add_argument("--realtime", action="store_true", help="Pace synthetic chunks like a microphone and measure backlog")
    parser.add_argument("--repeat", type=int, default=1, help="Repeat the fixture to test sustained decoding (1-20)")
    parser.add_argument("--max-backlog", type=float, default=4.0)
    args = parser.parse_args()
    if not 1 <= args.repeat <= 20:
        parser.error("--repeat must be between 1 and 20")
    if not math.isfinite(args.max_backlog) or args.max_backlog <= 0:
        parser.error("--max-backlog must be positive and finite")
    started = time.monotonic()
    model = load_vosk_model(args.model)
    loaded = time.monotonic()
    loaded_cpu = time.process_time()
    words = []
    max_chunk = max_backlog = 0.0
    tracker = SpeechTracker()
    tracker.synchronize(SimpleNamespace(running=True, paused=False, elapsed_seconds=0), 0)
    with wave.open(args.synthetic_test_wav, "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getcomptype() != "NONE":
            raise SystemExit("Synthetic fixture must be uncompressed mono signed 16-bit PCM")
        sample_rate = source.getframerate()
        duration = args.repeat * source.getnframes() / sample_rate
        recognizer = OfflineRecognizer(model, sample_rate)
        inference_started = time.monotonic()
        frames = 0
        for _ in range(args.repeat):
            source.rewind()
            while pcm := source.readframes(max(1, sample_rate // 4)):
                frames += len(pcm) // 2
                ready = inference_started + frames / sample_rate
                if args.realtime:
                    time.sleep(max(0.0, ready - time.monotonic()))
                chunk_started = time.monotonic()
                result = recognizer.feed(pcm)
                completed = time.monotonic()
                max_chunk = max(max_chunk, completed - chunk_started)
                if args.realtime:
                    max_backlog = max(max_backlog, completed - ready)
                if result:
                    tracker.accept_final(result, 0)
                    words.extend(word["word"].lower() for word in result.get("result", []))
        final = recognizer.finish()
        tracker.accept_final(final, 0)
        words.extend(word["word"].lower() for word in final.get("result", []))
    finished = time.monotonic()
    cpu_seconds = time.process_time() - loaded_cpu
    matched = sum(expected.lower() in words for expected in args.expect_word)
    output = {
        "sample_rate": sample_rate,
        "audio_seconds": round(duration, 3),
        "recognized_word_count": len(words),
        "aggregate_word_count": tracker.word_count,
        "aggregate_wpm": tracker.metrics(duration)["words_per_minute"],
        "expected_words_matched": matched,
        "expected_words_total": len(args.expect_word),
        "model_load_seconds": round(loaded - started, 3),
        "inference_seconds": round(finished - loaded, 3),
        "inference_cpu_seconds": round(cpu_seconds, 3),
        "cpu_real_time_factor": round(cpu_seconds / max(duration, 0.001), 3),
        "real_time_factor": round((finished - loaded) / max(duration, 0.001), 3),
        "max_chunk_seconds": round(max_chunk, 3),
    }
    if args.realtime:
        output["max_backlog_seconds"] = round(max_backlog, 3)
    try:
        import resource
        output["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except ImportError:
        pass
    print(json.dumps(output, sort_keys=True))
    if matched != len(args.expect_word) or not words or tracker.word_count != len(words):
        raise SystemExit("Synthetic speech verification failed; no transcript was logged")
    if args.realtime and max_backlog > args.max_backlog:
        raise SystemExit("Synthetic realtime verification exceeded the configured backlog limit")


if __name__ == "__main__":
    main()
