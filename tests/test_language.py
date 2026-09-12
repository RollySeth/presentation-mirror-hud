import json

import pytest

from presenter_hud.language import LANGUAGE_ADVICE, LanguageAnalyzer, recognized_filler


def feed(analyzer, text, start=0):
    for index, token in enumerate(text.lower().split(), 1):
        analyzer.accept(token, start + index * 0.25)


@pytest.mark.parametrize("token", [
    "um", "uh", "umm", "uhm", "uhmm", "hmm", "hm", "mm", "mmm",
    "ummmmm", "uhmmmm", "hmmmmmm", "mmmmmmm",
])
def test_whole_recognized_filler_aliases(token):
    assert recognized_filler(token)


@pytest.mark.parametrize("token", [
    "hum", "human", "humans", "humming", "summer", "summary", "uhura",
    "hmmword", "wordhmm", "umami", "m", "h", "u", "", "you know",
])
def test_normal_words_are_not_filler_aliases(token):
    assert not recognized_filler(token)


@pytest.mark.parametrize(("text", "issue"), [
    ("this is more better", "comparative-better"),
    ("this is more easier", "comparative-easier"),
    ("i has a plan", "agreement-i"),
    ("they was ready", "agreement-they"),
    ("we was ready", "agreement-we"),
    ("you was ready", "agreement-you"),
    ("please return back tomorrow", "redundant-return"),
    ("let me repeat again", "redundant-repeat"),
])
def test_small_specific_final_token_patterns(text, issue):
    analyzer = LanguageAnalyzer()
    feed(analyzer, text)
    assert analyzer.issue == issue
    assert analyzer.event_id == 1
    assert len(LANGUAGE_ADVICE[issue]) < 100


@pytest.mark.parametrize("text", [
    "does he have enough time", "did she go there", "will he have time",
    "could she have more", "he has more", "they were ready", "i have a plan",
    "say i has", "she said they was", "the phrase more better",
    "quote you was", "the letter i has", "she wrote return back",
    "the title we was", "this is better", "this is easier",
])
def test_helpers_quotations_and_standard_phrases_do_not_trigger(text):
    analyzer = LanguageAnalyzer()
    feed(analyzer, text)
    assert analyzer.event_id == 0


@pytest.mark.parametrize("word", ["basically", "actually", "really"])
def test_repeated_word_threshold_and_latched_repetition(word):
    analyzer = LanguageAnalyzer()
    feed(analyzer, f"{word} one {word} two")
    assert analyzer.event_id == 0
    feed(analyzer, word, start=1)
    assert analyzer.issue == f"repeat-{word}"
    assert analyzer.event_id == 1
    feed(analyzer, " ".join([word] * 200), start=1.25)
    assert analyzer.event_id == 1
    assert len(analyzer.context) == 40


def test_repetition_is_last_forty_not_lifetime_and_rearms_after_leaving_window():
    analyzer = LanguageAnalyzer()
    feed(analyzer, "basically basically")
    feed(analyzer, " ".join(["detail"] * 40), start=0.5)
    feed(analyzer, "basically", start=10.5)
    assert analyzer.event_id == 0
    feed(analyzer, "basically basically", start=10.75)
    assert analyzer.event_id == 1
    feed(analyzer, " ".join(["detail"] * 130), start=11.25)
    feed(analyzer, "basically basically basically", start=43.75)
    assert analyzer.event_id == 2


def test_same_pattern_has_thirty_second_evidence_cooldown():
    analyzer = LanguageAnalyzer()
    feed(analyzer, "more better")
    feed(analyzer, "more better", start=1)
    assert analyzer.event_id == 1
    feed(analyzer, "more better", start=31)
    assert analyzer.event_id == 2


def test_delayed_final_event_ages_from_first_observation_not_trigger_word():
    analyzer = LanguageAnalyzer()
    feed(analyzer, "more better today")
    assert analyzer.event_end == 0.5
    assert analyzer.metrics(15)["speech_language_age"] == 0
    assert analyzer.metrics(16)["speech_language_age"] == 1
    assert analyzer.metrics(22.99)["speech_language_issue"] == "comparative-better"
    assert analyzer.metrics(23)["speech_language_issue"] == ""
    assert analyzer.metrics(30)["speech_language_issue"] == ""
    assert analyzer.event_id == 1


def test_phrase_gap_and_explicit_boundary_clear_context_but_not_identity():
    analyzer = LanguageAnalyzer()
    analyzer.accept("more", 1)
    analyzer.accept("better", 4)
    assert analyzer.event_id == 0
    feed(analyzer, "more easier", start=4)
    assert analyzer.event_id == 1
    analyzer.clear_context()
    assert not analyzer.context
    assert analyzer.metrics(5)["speech_language_issue"] == ""
    assert analyzer.event_id == 1


def test_only_generic_bounded_event_metadata_leaves_memory():
    analyzer = LanguageAnalyzer()
    feed(analyzer, "confidentialsensitiveword more better")
    assert "confidentialsensitiveword" not in json.dumps(analyzer.metrics(1))
    for index in range(200):
        analyzer.accept("x" * 1000, 1 + index * 0.1)
    assert len(analyzer.context) == 40
    assert all(len(token) <= 32 for token in analyzer.context)
    assert set(analyzer._last_emitted) <= LANGUAGE_ADVICE.keys()


def test_every_template_is_fixed_bounded_and_actionable():
    assert all(0 < len(text) < 100 for text in LANGUAGE_ADVICE.values())
