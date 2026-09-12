import pytest

from presenter_hud.api import create_app


TEST_TOKEN = "synthetic-test-only-controller-token-not-for-production"


def authenticated_client(tmp_path):
    client = create_app(tmp_path / "state.json", tmp_path / "slides", token=TEST_TOKEN).test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {TEST_TOKEN}"
    return client


def test_brightness_control_persists_full_opacity_and_preserves_session(tmp_path):
    client = authenticated_client(tmp_path)
    before = client.get("/api/state").get_json()
    response = client.post("/api/display", json={"slide_brightness": 1.8})
    assert response.status_code == 200
    after = client.get("/api/state").get_json()
    assert after["slide_brightness"] == 1.8
    assert after["slide_opacity"] == 255
    for key in ("session_id", "elapsed_seconds", "slide_index", "slide_paths", "mirror"):
        assert after[key] == before[key]
    assert after["expression_avatar"]["mode"] == "off"


def test_controller_hides_filler_counts_and_exposes_voice_activity(tmp_path):
    client = authenticated_client(tmp_path)
    page = client.get("/").get_data(as_text=True)
    assert "possible fillers" not in page
    assert "speech_filler_count" not in page
    avatar = client.get("/api/state").get_json()["expression_avatar"]
    assert avatar["voice"] == "off"
    assert avatar["activity"] == "Mic off"


def test_default_heading_uses_requested_capitalization(tmp_path):
    client = authenticated_client(tmp_path)
    assert client.get("/api/state").get_json()["session_title"] == "Presentation Rehearsal"
    assert 'value="Presentation Rehearsal"' in client.get("/").get_data(as_text=True)


@pytest.mark.parametrize("value", [None, True, "1.5", 0.9, 2.6, float("inf"), float("nan"), []])
def test_invalid_brightness_is_rejected(tmp_path, value):
    client = authenticated_client(tmp_path)
    response = client.post("/api/display", json={"slide_brightness": value})
    assert response.status_code == 400
    assert "error" in response.get_json()
