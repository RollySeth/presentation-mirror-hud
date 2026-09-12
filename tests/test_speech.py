import json
from types import SimpleNamespace
from unittest.mock import MagicMock
import threading

import pytest

from presenter_hud.speech import (
    AudioBlock,
    BoundedAudioQueue,
    MicrophoneCapture,
    OfflineRecognizer,
    SpeechError,
    SpeechProcessor,
    SpeechTracker,
    load_vosk_model,
)
from presenter_hud import speech_runner
from presenter_hud.models import HudState


def state(running=False, paused=False, elapsed=0, generation=0):
    return HudState(
        running=running, paused=paused, elapsed_seconds=elapsed,
        session_id=str(generation) if generation else "",
    )


def result(*words):
    return {"result": [
        {"start": end - 0.1, "end": end, "word": token}
        for token, end in words
    ]}


def test_word_count_and_rolling_wpm_use_recognized_words_and_actual_elapsed_time():
    tracker = SpeechTracker(window_seconds=30)
    tracker.synchronize(state(), 100)
    tracker.accept_final(result(("hello", 1), ("world", 2), ("today", 3)), 0)
    assert tracker.metrics(106)["words_per_minute"] == 30
    assert tracker.metrics(112)["words_per_minute"] == 15
    assert tracker.metrics(132)["words_per_minute"] == 2
    assert tracker.metrics(134)["words_per_minute"] == 0
    assert tracker.metrics(134)["speech_word_count"] == 3


def test_loud_audio_is_not_a_word_rate_estimate():
    tracker = SpeechTracker()
    tracker.synchronize(state(), 10)
    tracker.observe_level(0.8, 12, 0.12)
    assert tracker.metrics(13)["words_per_minute"] == 0
    assert tracker.metrics(13)["speech_word_count"] == 0
    assert tracker.metrics(13)["volume"] == 0.8
    assert tracker.metrics(13)["silence_seconds"] == 1
    tracker.observe_level(0.01, 14, 0.12)
    assert tracker.metrics(16)["silence_seconds"] == 4


def test_phrase_pace_excludes_long_startup_silence_and_earlier_phrase():
    tracker = SpeechTracker()
    tracker.synchronize(state(running=True), 100)
    tracker.accept_final(result(*(("word", 1 + index * 0.4) for index in range(10))), 0)
    tracker.accept_final(result(*(("word", 20 + index * 0.5) for index in range(10))), 0)
    metrics = tracker.metrics(125)
    assert metrics["speech_recent_word_count"] == 10
    assert metrics["speech_recent_wpm"] == 120
    assert metrics["words_per_minute"] == 48
    assert metrics["speech_last_word_age"] == 0.5
    assert tracker.metrics(128)["speech_last_word_age"] == 3.5


def test_phrase_partials_are_provisional_and_never_duplicate_final_timings():
    tracker = SpeechTracker()
    tracker.synchronize(state(running=True), 100)
    words = result(*(("word", 1 + index * 0.5) for index in range(10)))
    tracker.accept_partial({"partial_result": words["result"]}, 0)
    metrics = tracker.metrics(106)
    assert metrics["speech_word_count"] == 0
    assert metrics["speech_recent_word_count"] == 10
    assert metrics["speech_recent_wpm"] == 120
    tracker.accept_final(words, 0)
    metrics = tracker.metrics(106)
    assert metrics["speech_word_count"] == 10
    assert metrics["speech_recent_word_count"] == 10


def test_phrase_timings_are_cleared_at_pause_and_new_session():
    tracker = SpeechTracker()
    tracker.synchronize(state(running=True, generation=1), 100)
    tracker.accept_final(result(*(("word", 1 + index * 0.5) for index in range(10))), 0)
    tracker.synchronize(state(running=True, paused=True, generation=1, elapsed=6), 106)
    assert tracker.metrics(110)["speech_recent_word_count"] == 0
    tracker.synchronize(state(running=True, generation=1, elapsed=6), 120)
    assert tracker.metrics(120)["speech_recent_wpm"] == 0
    tracker.synchronize(state(running=True, generation=2), 130)
    assert tracker.metrics(130)["speech_last_word_age"] is None
    assert tracker.metrics(130)["speech_word_count"] == 0


def test_partial_and_repeated_final_results_are_not_double_counted():
    tracker = SpeechTracker()
    tracker.synchronize(state(), 0)
    tracker.accept_final({"partial": "hello world", "partial_result": [
        {"word": "hello", "start": 0, "end": 1}
    ]}, 0)
    assert tracker.word_count == 0
    final = result(("hello", 1), ("world", 2))
    tracker.accept_final(final, 0)
    tracker.accept_final(final, 0)
    tracker.accept_final(result(("world", 2), ("world", 3)), 0)
    assert tracker.word_count == 3


def test_fillers_normalize_punctuation_and_optional_phrase():
    tracker = SpeechTracker(count_you_know=True)
    tracker.synchronize(state(), 0)
    tracker.accept_final(result(("Um,", 0.2), ("uh.", 0.5), ("you", 1)), 0)
    tracker.accept_final(result(("know!", 1.2), ("you", 2), ("know", 5)), 0)
    assert tracker.filler_count == 3
    assert tracker.word_count == 6
    disabled = SpeechTracker()
    disabled.synchronize(state(), 0)
    disabled.accept_final(result(("you", 1), ("know", 2)), 0)
    assert disabled.filler_count == 0


def test_recognized_aliases_are_final_only_and_never_count_twice():
    tracker = SpeechTracker()
    tracker.synchronize(state(running=True), 100)
    aliases = ("Um,", "UH.", "umm", "uhm", "uhmm", "HMM!", "hm", "mm", "mmm", "uhmmmm")
    words = result(*((token, (index + 1) * 0.2) for index, token in enumerate(aliases)))
    tracker.accept_partial({"partial_result": words["result"]}, 0)
    assert tracker.filler_count == 0
    tracker.accept_final(words, 0)
    tracker.accept_final(words, 0)
    tracker.accept_final(result(("hum", 3), ("human", 4), ("summer", 5), ("umami", 6)), 0)
    assert tracker.filler_count == len(aliases)
    assert tracker.word_count == len(aliases) + 4


def test_language_uses_only_new_finals_not_partial_or_loud_audio():
    tracker = SpeechTracker()
    tracker.synchronize(state(running=True), 100)
    words = result(("more", 1), ("better", 1.5))
    tracker.observe_level(0.8, 101, 0.12)
    tracker.accept_partial({"partial_result": words["result"]}, 0)
    assert tracker.metrics(102)["speech_language_event_id"] == 0
    tracker.accept_final(words, 0)
    tracker.accept_final(words, 0)
    metrics = tracker.metrics(104)
    assert metrics["speech_language_event_id"] == 1
    assert metrics["speech_language_issue"] == "comparative-better"
    assert metrics["speech_language_age"] == 0


def test_language_context_and_events_clear_at_pause_resume_stop_and_fresh_start():
    tracker = SpeechTracker()
    tracker.synchronize(state(running=True, generation=1), 100)
    tracker.accept_final(result(("more", 1), ("better", 1.5), ("more", 2)), 0)
    tracker.synchronize(state(running=True, paused=True, generation=1, elapsed=3), 103)
    assert tracker.metrics(104)["speech_language_issue"] == ""
    assert not tracker.language.context
    tracker.synchronize(state(running=True, generation=1, elapsed=3), 110)
    tracker.accept_final(result(("easier", 0.5)), 3)
    assert tracker.language.event_id == 1
    tracker.synchronize(state(generation=1, elapsed=4), 111)
    assert not tracker.language.context
    assert tracker.metrics(111)["speech_language_event_id"] == 0
    tracker.synchronize(state(running=True, generation=2), 112)
    assert tracker.language.event_id == 0
    assert tracker.metrics(112)["speech_language_issue"] == ""


def test_publish_language_anchor_is_first_final_observation_and_stable_across_polls(monkeypatch):
    current = state(running=True, generation=1)
    store = SimpleNamespace(update=lambda mutate: mutate(current))
    tracker = SpeechTracker()
    tracker.synchronize(current, 100)
    tracker.accept_final(result(("confidentialsensitiveword", 1), ("more", 2), ("better", 2.5)), 0)
    monkeypatch.setattr(speech_runner.time, "time", lambda: 204)
    speech_runner.publish(store, tracker, current, 104)
    assert current.metrics.speech_language_at == 204
    assert current.cue.kind == "language"
    assert current.cue.expires_in_seconds == 8
    monkeypatch.setattr(speech_runner.time, "time", lambda: 205.01)
    speech_runner.publish(store, tracker, current, 105)
    assert current.metrics.speech_language_at == 204
    assert current.metrics.speech_language_event_id == 1
    encoded = json.dumps(current.to_dict())
    assert "confidentialsensitiveword" not in encoded
    assert "speech_language_age" not in encoded
    assert set(vars(current.metrics)) == set(current.to_dict()["metrics"])
    speech_runner.mark_inactive(current)
    assert current.metrics.speech_language_issue == ""
    assert current.metrics.speech_language_at == 0
    assert current.metrics.speech_language_event_id == 1


def test_language_restart_preserves_event_sequence_without_old_context():
    current = state(running=True, generation=1, elapsed=30)
    current.metrics.speech_language_event_id = 4
    current.metrics.speech_language_issue = "comparative-better"
    current.metrics.speech_language_at = 99
    current.coach_feedback["language-event"] = 5
    tracker = SpeechTracker()
    tracker.synchronize(current, 100)
    assert tracker.language.event_id == 5
    assert not tracker.language.context
    assert tracker.metrics(100)["speech_language_issue"] == ""
    tracker.accept_final(result(("more", 1), ("better", 1.5)), 0)
    assert tracker.language.event_id == 6


def test_language_producer_persists_only_fixed_metadata_and_template(tmp_path, monkeypatch):
    from presenter_hud.coach import refresh_coach
    from presenter_hud.state_store import StateStore

    path = tmp_path / "state.json"
    store = StateStore(path)

    def start(current):
        current.running = True
        current.session_id = "language-test"

    store.update(start)
    current = store.load()
    tracker = SpeechTracker()
    tracker.synchronize(current, 100)
    tracker.accept_final(result(("confidentialsensitiveword", 1), ("more", 2), ("better", 2.5)), 0)
    monkeypatch.setattr(speech_runner.time, "time", lambda: 204)
    speech_runner.publish(store, tracker, current, 104)
    assert "confidentialsensitiveword" not in path.read_text()
    assert store.load().cue.kind == "language"
    assert store.load().metrics.speech_language_at == 204
    store.update(lambda saved: refresh_coach(saved, 206))
    assert store.load().metrics.speech_language_at == 204
    store.update(lambda saved: refresh_coach(saved, 212))
    assert store.load().cue.kind == "progress"


def test_long_sentence_final_delivered_at_fifteen_has_eight_readable_seconds(monkeypatch):
    current = state(running=True, generation=1)
    store = SimpleNamespace(update=lambda mutate: mutate(current))
    final = result(("more", 1), ("better", 2), *(("detail", end) for end in range(3, 15)))
    recognizer = MagicMock()
    recognizer.feed.return_value = final
    tracker = SpeechTracker()
    processor = SpeechProcessor(recognizer, tracker)
    processor.synchronize(current, 100)
    processor.process(AudioBlock(b"long-sentence", 100, 115), 0.3)
    monkeypatch.setattr(speech_runner.time, "time", lambda: 215)
    speech_runner.publish(store, tracker, current, 115)
    assert current.metrics.speech_language_at == 215
    assert current.cue.kind == "language"
    assert current.cue.expires_in_seconds == 8
    created = current.cue.created_at
    tracker.accept_final(final, 0)
    for elapsed in (16, 19, 22.9):
        monkeypatch.setattr(speech_runner.time, "time", lambda: 200 + elapsed)
        speech_runner.publish(store, tracker, current, 100 + elapsed)
        assert current.metrics.speech_language_at == 215
        assert current.cue.created_at == created
        assert current.cue.kind == "language"
        assert current.metrics.speech_language_event_id == 1
    monkeypatch.setattr(speech_runner.time, "time", lambda: 223)
    speech_runner.publish(store, tracker, current, 123)
    assert current.metrics.speech_language_issue == ""
    assert current.cue.kind == "progress"


def test_language_pause_flush_counts_final_words_but_discards_advice_context():
    recognizer = MagicMock()
    recognizer.feed.return_value = None
    recognizer.finish.return_value = result(("more", 1), ("better", 1.5))
    tracker = SpeechTracker()
    processor = SpeechProcessor(recognizer, tracker)
    processor.synchronize(state(running=True, generation=1), 100)
    processor.process(AudioBlock(b"pcm", 100, 102), 0.5)
    processor.synchronize(state(running=True, paused=True, generation=1, elapsed=3), 103)
    assert tracker.word_count == 2
    assert tracker.metrics(103)["speech_language_issue"] == ""
    assert not tracker.language.context


def test_pause_freezes_active_time_resume_excludes_pause_and_stop_preserves_totals():
    tracker = SpeechTracker()
    tracker.synchronize(state(running=True), 100)
    tracker.accept_final(result(("hello", 1), ("world", 2)), 0)
    tracker.synchronize(state(running=True, paused=True, elapsed=4), 104)
    paused = tracker.metrics(104)
    tracker.accept_final(result(("ignored", 9)), 0)
    assert tracker.metrics(164) == paused
    tracker.synchronize(state(running=True, elapsed=4), 164)
    assert tracker.metrics(164)["words_per_minute"] == 30
    assert tracker.metrics(168)["words_per_minute"] == 15
    assert tracker.metrics(168)["silence_seconds"] == 6
    tracker.synchronize(state(elapsed=8), 168)
    assert tracker.metrics(300)["words_per_minute"] == 0
    assert tracker.metrics(300)["speech_word_count"] == 2
    tracker.accept_final(result(("ignored", 10)), 0)
    assert tracker.word_count == 2


def test_new_rehearsal_resets_preview_and_prior_session_aggregates():
    tracker = SpeechTracker()
    tracker.synchronize(state(), 10)
    tracker.accept_final(result(("um", 1), ("hello", 2)), 0)
    tracker.synchronize(state(running=True, generation=1), 20)
    assert tracker.metrics(20)["speech_word_count"] == 0
    assert tracker.metrics(20)["speech_filler_count"] == 0
    assert tracker.metrics(20)["silence_seconds"] == 0
    tracker.accept_final(result(("hello", 1)), 0)
    tracker.synchronize(state(running=True, generation=2), 22)
    assert tracker.word_count == 0


def test_old_state_elapsed_rewind_detects_restart_without_session_identity():
    tracker = SpeechTracker()
    current = state(running=True, elapsed=3)
    del current.session_id
    tracker.synchronize(current, 10)
    tracker.accept_final(result(("hello", 1)), 0)
    current.elapsed_seconds = 0
    assert tracker.synchronize(current, 11)
    assert tracker.word_count == 0


def test_service_restart_preserves_committed_session_totals():
    current = state(running=True, generation=1, elapsed=30)
    current.metrics.speech_word_count = 60
    current.metrics.speech_filler_count = 3
    tracker = SpeechTracker()
    tracker.synchronize(current, 100)
    assert tracker.word_count == 60
    assert tracker.filler_count == 3
    tracker.accept_final(result(("hello", 1)), 0)
    assert tracker.word_count == 61
    tracker.synchronize(state(running=True, generation=2), 102)
    assert tracker.word_count == 0


def test_stopped_zero_length_rehearsal_does_not_become_preview_on_service_restart():
    current = state(generation=1)
    current.metrics.speech_word_count = 2
    tracker = SpeechTracker()
    tracker.synchronize(current, 100)
    assert tracker.mode == "stopped"
    assert tracker.word_count == 2
    assert tracker.metrics(200)["words_per_minute"] == 0


def test_periodic_control_polls_do_not_distort_acquisition_clock():
    tracker = SpeechTracker()
    tracker.synchronize(state(), 100)
    tracker.synchronize(state(), 105)
    assert tracker.clock(104.75) == 4.75


def test_processor_finalizes_pause_once_and_excludes_buffered_paused_audio():
    recognizer = MagicMock()
    recognizer.feed.return_value = None
    recognizer.finish.return_value = result(("hello", 1), ("world", 2))
    tracker = SpeechTracker()
    processor = SpeechProcessor(recognizer, tracker)
    processor.synchronize(state(running=True), 100)
    processor.process(AudioBlock(b"pcm", 100, 102), 0.5)
    processor.synchronize(state(running=True, paused=True, elapsed=3), 103)
    assert tracker.word_count == 2
    processor.synchronize(state(running=True, paused=True, elapsed=3), 110)
    recognizer.finish.assert_called_once()
    processor.synchronize(state(running=True, elapsed=3), 120)
    silence = tracker.metrics(120)["silence_seconds"]
    processor.process(AudioBlock(b"old", 119.75, 120), 0.2)
    assert recognizer.feed.call_count == 1
    assert tracker.metrics(120)["silence_seconds"] == silence
    recognizer.feed.return_value = result(("um", 0.25))
    processor.process(AudioBlock(b"new", 120.25, 120.5), 0.2)
    assert tracker.word_count == 3
    assert tracker.filler_count == 1


def test_fresh_start_discards_uncommitted_preview_hypothesis():
    recognizer = MagicMock()
    recognizer.feed.return_value = None
    tracker = SpeechTracker()
    processor = SpeechProcessor(recognizer, tracker)
    processor.synchronize(state(), 100)
    processor.process(AudioBlock(b"preview", 100, 101), 0.5)
    processor.synchronize(state(running=True, generation=1), 102)
    recognizer.finish.assert_not_called()
    assert tracker.word_count == 0
    processor.process(AudioBlock(b"queued-preview", 101.5, 102), 0.2)
    assert recognizer.feed.call_count == 1


@pytest.mark.parametrize("bad", [
    {"result": None},
    {"result": [{"word": "private utterance", "start": 0, "end": float("nan")}]},
    {"result": [{"word": "private utterance", "start": 3, "end": 2}]},
    {"result": [{"word": "private utterance"}]},
])
def test_invalid_recognizer_results_fail_without_exposing_text(bad):
    tracker = SpeechTracker()
    tracker.synchronize(state(), 0)
    with pytest.raises(SpeechError) as caught:
        tracker.accept_final(bad, 0)
    assert "private utterance" not in str(caught.value)


def test_queue_overflow_and_cpu_backlog_are_explicit_failures(monkeypatch):
    audio = BoundedAudioQueue(1)
    audio.put(AudioBlock(b"one", 0, 1))
    audio.put(AudioBlock(b"two", 1, 2))
    with pytest.raises(SpeechError, match="queue overflow"):
        audio.get(0, 2)
    audio = BoundedAudioQueue(1)
    audio.put(AudioBlock(b"one", 0, 1))
    monkeypatch.setattr("presenter_hud.speech.time.monotonic", lambda: 4)
    with pytest.raises(SpeechError, match="CPU backlog"):
        audio.get(0, 2)


def test_native_c920_rate_mono_pcm_and_capture_cleanup(monkeypatch):
    pytest.importorskip("numpy")
    import sys

    stream = MagicMock()
    backend = SimpleNamespace(
        query_devices=MagicMock(return_value={"default_samplerate": 32000.0}),
        check_input_settings=MagicMock(), RawInputStream=MagicMock(return_value=stream),
        CallbackAbort=type("CallbackAbort", (Exception,), {}),
    )
    monkeypatch.setitem(sys.modules, "sounddevice", backend)
    capture = MicrophoneCapture(device="C920")
    with pytest.raises(RuntimeError, match="test failure"):
        with capture:
            assert capture.sample_rate == 32000
            assert capture.volume(b"\x00\x00" * 8) == 0
            raise RuntimeError("test failure")
    backend.check_input_settings.assert_called_once_with(
        device="C920", channels=1, dtype="int16", samplerate=32000
    )
    assert backend.RawInputStream.call_args.kwargs["blocksize"] == 8000
    stream.abort.assert_called_once()
    stream.close.assert_called_once()
    with pytest.raises(backend.CallbackAbort):
        capture._callback(b"", 0, None, True)
    with pytest.raises(SpeechError, match="PortAudio"):
        capture.audio.check()


def test_offline_recognizer_requests_timed_partials_and_final_results(monkeypatch):
    import sys

    native = MagicMock()
    native.AcceptWaveform.side_effect = [False, True]
    native.Result.return_value = json.dumps(result(("hello", 1)))
    native.PartialResult.return_value = "{}"
    backend = SimpleNamespace(KaldiRecognizer=MagicMock(return_value=native))
    monkeypatch.setitem(sys.modules, "vosk", backend)
    recognizer = OfflineRecognizer(object(), 32000)
    assert recognizer.feed(b"first") == {"partial_result": []}
    assert len(recognizer.feed(b"second")["result"]) == 1
    native.PartialResult.assert_called_once()
    native.SetWords.assert_called_once_with(True)
    native.SetPartialWords.assert_called_once_with(True)


def test_partial_pace_updates_before_final_without_double_counting():
    tracker = SpeechTracker()
    tracker.synchronize(state(running=True), 100)
    partial = {"partial_result": result(("hello", 1), ("world", 2))["result"]}
    tracker.accept_partial(partial, 0)
    preview = tracker.metrics(104)
    assert preview["words_per_minute"] == 30
    assert preview["speech_pending_word_count"] == 2
    assert preview["speech_word_count"] == 0
    tracker.accept_partial(partial, 0)
    assert tracker.metrics(104)["speech_pending_word_count"] == 2
    tracker.accept_final(result(("hello", 1), ("world", 2)), 0)
    final = tracker.metrics(104)
    assert final["words_per_minute"] == 30
    assert final["speech_word_count"] == 2
    assert final["speech_pending_word_count"] == 0


def test_revised_partial_can_remove_words_without_changing_committed_total():
    tracker = SpeechTracker()
    tracker.synchronize(state(running=True), 100)
    tracker.accept_partial({"partial_result": result(("hello", 1), ("world", 2))["result"]}, 0)
    tracker.accept_partial({"partial_result": result(("hello", 1))["result"]}, 0)
    assert tracker.metrics(104)["speech_pending_word_count"] == 1
    assert tracker.word_count == 0
    tracker.accept_partial({"partial_result": []}, 0)
    assert tracker.metrics(104)["speech_pending_word_count"] == 0
    assert tracker.metrics(104)["words_per_minute"] == 0


def test_new_session_clears_uncommitted_words():
    tracker = SpeechTracker()
    tracker.synchronize(state(running=True, generation=1), 100)
    tracker.accept_partial({"partial_result": result(("hello", 1))["result"]}, 0)
    tracker.synchronize(state(running=True, generation=2), 104)
    assert tracker.metrics(105)["speech_pending_word_count"] == 0
    assert tracker.word_count == 0


def test_decoder_reset_recreates_timing_epoch_but_reuses_loaded_model(monkeypatch):
    import sys

    first, second = MagicMock(), MagicMock()
    backend = SimpleNamespace(KaldiRecognizer=MagicMock(side_effect=[first, second]))
    monkeypatch.setitem(sys.modules, "vosk", backend)
    model = object()
    recognizer = OfflineRecognizer(model, 32000)
    recognizer.reset()
    assert backend.KaldiRecognizer.call_count == 2
    assert all(call.args == (model, 32000.0) for call in backend.KaldiRecognizer.call_args_list)
    first.Reset.assert_not_called()
    second.SetWords.assert_called_once_with(True)


def test_missing_model_has_actionable_error_without_importing_vosk(monkeypatch):
    monkeypatch.setattr("presenter_hud.speech.Path.is_file", lambda _: False)
    with pytest.raises(SpeechError, match="speech_prepare.py"):
        load_vosk_model("models/missing-test-model")


def test_publish_is_aggregate_only_and_preserves_unrelated_state():
    current = state(running=True)
    current.metrics.camera_active = True
    store = SimpleNamespace(update=lambda mutate: mutate(current))
    tracker = SpeechTracker()
    tracker.synchronize(current, 100)
    tracker.accept_final(result(("private", 1), ("utterance", 2)), 0)
    speech_runner.publish(store, tracker, current, 104)
    output = vars(current.metrics)
    assert output["camera_active"] is True
    assert output["speech_word_count"] == 2
    assert "private" not in json.dumps(output)
    assert "utterance" not in json.dumps(output)


def test_publish_does_not_overwrite_a_concurrent_fresh_start():
    observed = state(running=True, generation=1, elapsed=2)
    current = state(running=True, generation=2)
    current.metrics.speech_word_count = 0
    store = SimpleNamespace(update=lambda mutate: mutate(current))
    tracker = SpeechTracker()
    tracker.synchronize(observed, 10)
    tracker.accept_final(result(("hello", 1)), 0)
    speech_runner.publish(store, tracker, observed, 12)
    assert current.metrics.speech_word_count == 0
    assert current.metrics.speech_recent_word_count == 0
    assert current.metrics.speech_observed_at == 0


def test_speech_publication_carries_freshness_and_idle_clears_advisory_timings(monkeypatch):
    current = state(running=True)
    store = SimpleNamespace(update=lambda mutate: mutate(current))
    tracker = SpeechTracker()
    tracker.synchronize(current, 100)
    tracker.accept_final(result(*(("word", 1 + index * 0.5) for index in range(10))), 0)
    monkeypatch.setattr(speech_runner.time, "time", lambda: 200)
    speech_runner.publish(store, tracker, current, 106)
    assert current.metrics.speech_recent_word_count == 10
    assert current.metrics.speech_observed_at == 200
    speech_runner.mark_inactive(current)
    assert current.metrics.speech_recent_word_count == 0
    assert current.metrics.speech_recent_wpm == 0
    assert current.metrics.speech_last_word_age is None
    assert current.metrics.speech_observed_at == 0
    assert current.metrics.speech_word_count == 10
    current.running = False
    current.metrics.speech_recent_wpm = 200
    current.metrics.speech_observed_at = 200
    speech_runner.publish_level(store, 0.2)
    assert current.metrics.speech_recent_wpm == 0
    assert current.metrics.speech_observed_at == 0
    assert current.metrics.speech_word_count == 10


def test_aggregate_contract_serializes_with_real_hud_state():
    from presenter_hud.models import HudState

    current = HudState(running=True, session_id="test-session")
    current.metrics.camera_active = True
    store = SimpleNamespace(update=lambda mutate: mutate(current))
    tracker = SpeechTracker()
    tracker.synchronize(current, 100)
    tracker.accept_final(result(("private", 1), ("utterance", 2)), 0)
    speech_runner.publish(store, tracker, current, 104)
    encoded = json.dumps(current.to_dict())
    assert "private" not in encoded
    assert "utterance" not in encoded
    assert current.to_dict()["metrics"]["speech_word_count"] == 2
    assert current.to_dict()["metrics"]["speech_active"] is True
    assert current.metrics.camera_active is True
    speech_runner.mark_inactive(current)
    assert current.to_dict()["metrics"]["speech_active"] is False
    assert current.to_dict()["metrics"]["microphone_active"] is False


def test_startup_failure_clears_active_flags(monkeypatch):
    current = state(running=True)
    current.metrics.speech_active = current.metrics.microphone_active = True
    store = SimpleNamespace(update=lambda mutate: mutate(current), load=lambda: current)
    args = SimpleNamespace(model="missing")
    with pytest.raises(SpeechError):
        speech_runner.run(store, args, threading.Event())
    assert current.metrics.speech_active is False
    assert current.metrics.microphone_active is False
    assert current.metrics.words_per_minute == 0


def test_idle_microphone_does_not_load_a_speech_model(monkeypatch):
    current = state()
    store = SimpleNamespace(update=lambda mutate: mutate(current), load=lambda: current)
    stopped = threading.Event()
    load = MagicMock()
    capture = MagicMock()
    capture.sample_rate = 32000
    capture.volume.return_value = 0.2

    def get(**_):
        stopped.set()
        now = speech_runner.time.monotonic()
        return AudioBlock(b"synthetic", now - 0.25, now)

    capture.audio.get.side_effect = get
    monkeypatch.setattr(speech_runner, "load_vosk_model", load)
    monkeypatch.setattr(speech_runner, "MicrophoneCapture", lambda **_: capture)
    args = SimpleNamespace(audio_device="C920", max_backlog=4)
    speech_runner.run(store, args, stopped)
    load.assert_not_called()
    capture.__exit__.assert_called_once()


@pytest.mark.parametrize("fail", [False, True])
def test_runner_closes_capture_and_clears_flags_on_stop_or_capture_failure(monkeypatch, fail):
    current = state(running=True)
    store = SimpleNamespace(update=lambda mutate: mutate(current), load=lambda: current)
    stopped = threading.Event()
    recognizer = MagicMock()
    recognizer.feed.return_value = None
    recognizer.finish.return_value = {}
    capture = MagicMock()
    capture.sample_rate = 32000
    capture.volume.return_value = 0.1

    def get(**_):
        if fail:
            raise SpeechError("test input overflow")
        stopped.set()
        now = speech_runner.time.monotonic()
        return AudioBlock(b"synthetic", now - 0.25, now)

    capture.audio.get.side_effect = get
    monkeypatch.setattr(speech_runner, "load_vosk_model", lambda _: object())
    monkeypatch.setattr(speech_runner, "MicrophoneCapture", lambda **_: capture)
    monkeypatch.setattr(speech_runner, "OfflineRecognizer", lambda *_: recognizer)
    args = SimpleNamespace(
        model="fake", audio_device="C920", max_backlog=2,
        window=30, count_you_know=False, silence_threshold=0.12,
    )
    if fail:
        with pytest.raises(SpeechError, match="test input overflow"):
            speech_runner.run(store, args, stopped)
        recognizer.finish.assert_not_called()
    else:
        speech_runner.run(store, args, stopped)
        recognizer.finish.assert_called_once()
    capture.__exit__.assert_called_once()
    assert current.metrics.speech_active is False
    assert current.metrics.microphone_active is False
    assert current.metrics.volume == 0


def test_main_registers_both_termination_signals(monkeypatch):
    import signal
    import sys

    handlers = {}
    monkeypatch.setattr(sys, "argv", ["speech_runner", "--model", "fake"])
    monkeypatch.setattr(speech_runner.signal, "signal", lambda number, handler: handlers.update({number: handler}))
    monkeypatch.setattr(speech_runner, "StateStore", lambda _: object())

    def run(_store, args, stopped):
        assert args.audio_device is None
        for number in (signal.SIGTERM, signal.SIGINT):
            stopped.clear()
            handlers[number](number, None)
            assert stopped.is_set()

    monkeypatch.setattr(speech_runner, "run", run)
    speech_runner.main()


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_cli_rejects_unbounded_or_nonpositive_settings(value):
    with pytest.raises(Exception, match="positive and finite"):
        speech_runner.positive_float(value)
