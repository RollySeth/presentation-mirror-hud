import time

from presenter_hud.api import create_app
from presenter_hud.state_store import StateStore


TEST_TOKEN = "synthetic-test-only-controller-token-not-for-production"


def authenticated_client(state_path, slides_path):
    app = create_app(state_path, slides_path, token=TEST_TOKEN)
    client = app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {TEST_TOKEN}"
    return client


def test_api_session_and_metrics(tmp_path):
    client = authenticated_client(tmp_path / "state.json", tmp_path / "slides")

    assert client.get("/health").get_json() == {"ok": True}
    assert "Presenter Mirror" in client.get("/").get_data(as_text=True)
    state = client.post(
        "/api/session/start",
        json={"title": "Demo", "duration_seconds": 300},
    ).get_json()
    assert state["running"] is True
    assert state["session_title"] == "Demo"
    first_session = state["session_id"]
    assert first_session
    assert client.post("/api/session/start", json={}).get_json()["session_id"] != first_session

    state = client.post(
        "/api/metrics",
        json={
            "face_present": True, "words_per_minute": 190,
            "speech_active": True, "speech_recent_word_count": 12,
            "speech_recent_wpm": 190, "speech_last_word_age": 0,
            "speech_observed_at": time.time(),
        },
    ).get_json()
    assert state["cue"]["kind"] == "pace-fast"


def test_non_object_metrics_are_rejected(tmp_path):
    client = authenticated_client(tmp_path / "state.json", tmp_path / "slides")
    assert client.post("/api/metrics", json=[]).status_code == 400


def test_controller_exposes_live_coach_avatar(tmp_path):
    client = authenticated_client(tmp_path / "state.json", tmp_path / "slides")
    assert "Live coach avatar" in client.get("/").get_data(as_text=True)
    avatar = client.get("/api/state").get_json()["coach_avatar"]
    assert avatar["mode"] == "ready"
    assert avatar["emoji"]


def test_metrics_preserve_manual_coach_message_and_start_clears_pending(tmp_path):
    client = authenticated_client(tmp_path / "state.json", tmp_path / "slides")
    client.post("/api/session/start", json={})
    client.post("/api/cue", json={"text": "My custom reminder"})
    response = client.post("/api/metrics", json={
        "words_per_minute": 200, "speech_pending_word_count": 4,
        "speech_active": True, "speech_recent_word_count": 12,
        "speech_recent_wpm": 200, "speech_last_word_age": 0,
        "speech_observed_at": time.time(),
    })
    assert response.get_json()["cue"]["text"] == "My custom reminder"
    response = client.post("/api/session/start", json={})
    assert response.get_json()["metrics"]["speech_pending_word_count"] == 0


def test_start_and_stop_reset_coaching_history_and_start_resets_phrase_metrics(tmp_path):
    path = tmp_path / "state.json"
    client = authenticated_client(path, tmp_path / "slides")
    store = StateStore(path)

    def seed(state):
        state.coach_feedback = {"pace-slow": 1}
        state.coach_last_shown = {"pace-slow": 100}
        state.coach_history = [[100, 40, 3]]
        state.metrics.speech_recent_word_count = 12
        state.metrics.speech_recent_wpm = 60
        state.metrics.speech_last_word_age = 0.4
        state.metrics.speech_observed_at = 100

    store.update(seed)
    state = client.post("/api/session/start", json={}).get_json()
    assert state["coach_history"] == []
    assert state["coach_feedback"] == {}
    assert state["coach_last_shown"] == {}
    assert state["metrics"]["speech_recent_word_count"] == 0
    assert state["metrics"]["speech_recent_wpm"] == 0
    assert state["metrics"]["speech_last_word_age"] is None
    assert state["metrics"]["speech_observed_at"] == 0
    store.update(seed)
    state = client.post("/api/session/stop", json={}).get_json()
    assert state["coach_history"] == []
    assert state["coach_feedback"] == {}
    assert state["coach_last_shown"] == {}


def test_language_state_clears_on_start_pause_resume_and_stop(tmp_path):
    path = tmp_path / "state.json"
    client = authenticated_client(path, tmp_path / "slides")
    store = StateStore(path)

    def seed(state):
        state.metrics.speech_language_event_id = 5
        state.metrics.speech_language_issue = "comparative-better"
        state.metrics.speech_language_at = time.time()

    store.update(seed)
    current = client.post("/api/session/start", json={}).get_json()
    assert current["metrics"]["speech_language_event_id"] == 0
    assert current["metrics"]["speech_language_issue"] == ""
    assert current["metrics"]["speech_language_at"] == 0
    for endpoint in ("pause", "pause", "stop"):
        store.update(seed)
        current = client.post(f"/api/session/{endpoint}", json={}).get_json()
        assert current["metrics"]["speech_language_issue"] == ""
        assert current["metrics"]["speech_language_at"] == 0
        assert current["metrics"]["speech_language_event_id"] == (0 if endpoint == "stop" else 5)
