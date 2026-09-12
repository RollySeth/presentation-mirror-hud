import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest

from presenter_hud import api, security


TEST_TOKEN = "synthetic-test-only-controller-token-not-for-production"
AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
JSON_POSTS = [
    "/api/display", "/api/session/start", "/api/session/pause", "/api/session/stop",
    "/api/slides/next", "/api/slides/previous", "/api/cue", "/api/metrics",
]
PROTECTED = [("GET", "/api/state"), *(("POST", route) for route in JSON_POSTS), ("POST", "/api/slides")]


@pytest.fixture
def app(tmp_path):
    return api.create_app(tmp_path / "state.json", tmp_path / "slides", token=TEST_TOKEN)


@pytest.fixture
def private_token_dir(tmp_path, monkeypatch):
    # Model an outside-repository private directory inside the test workspace.
    monkeypatch.setattr(security, "REPOSITORY_ROOT", tmp_path / "source-repository")
    return tmp_path


def test_every_api_route_is_covered(app):
    assert {rule.rule for rule in app.url_map.iter_rules() if rule.rule.startswith("/api")} == {
        route for _, route in PROTECTED
    }


@pytest.mark.parametrize("method,path", PROTECTED)
@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic not-a-token", "Bearer café"])
def test_all_api_routes_require_bearer(app, method, path, authorization):
    headers = {} if authorization is None else {"Authorization": authorization}
    response = app.test_client().open(path, method=method, headers=headers, json={})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert TEST_TOKEN not in response.get_data(as_text=True)


@pytest.mark.parametrize("path", ["/api", "/api/future", "/api/state"])
def test_unknown_routes_and_preflight_are_not_an_auth_bypass(app, path):
    for method in ("GET", "HEAD", "OPTIONS"):
        assert app.test_client().open(path, method=method).status_code == 401


@pytest.mark.parametrize("path", ["/", "/health"])
def test_public_endpoints_are_generic_and_have_security_headers(app, path):
    response = app.test_client().get(path)
    assert response.status_code == 200
    assert TEST_TOKEN not in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "Set-Cookie" not in response.headers
    if path == "/health":
        assert response.get_json() == {"ok": True}


@pytest.mark.parametrize("method,path", PROTECTED)
@pytest.mark.parametrize("origin", ["https://attacker.example", "null", "http://localhost:8765.attacker.example"])
def test_foreign_origin_cannot_read_or_mutate(app, method, path, origin):
    options = {"data": {"slides": (io.BytesIO(b"synthetic"), "slide.png")}} if path == "/api/slides" else {"json": {}}
    response = app.test_client().open(path, method=method, headers={**AUTH, "Origin": origin}, **options)
    assert response.status_code == 403
    assert "Access-Control-Allow-Origin" not in response.headers
    assert response.headers["Cache-Control"] == "no-store"


def test_explicit_origins_and_trusted_hosts_are_not_inferred_from_host(tmp_path):
    app = api.create_app(tmp_path / "state.json", tmp_path / "slides", token=TEST_TOKEN,
                         allowed_origins=["https://mirror.example.com"])
    client = app.test_client()
    assert client.get("/api/state", base_url="https://mirror.example.com",
                      headers={**AUTH, "Origin": "https://mirror.example.com"}).status_code == 200
    assert client.get("/api/state", headers={**AUTH, "Origin": "http://localhost:8765"}).status_code == 403
    assert client.get("/health", base_url="http://attacker.example").status_code == 400
    assert client.get("/api/state", base_url="https://attacker.example",
                      headers={**AUTH, "Origin": "https://attacker.example"}).status_code in (400, 403)
    assert client.get("/api/state", headers=AUTH).status_code == 200


@pytest.mark.parametrize("origin", ["http://localhost:8765", "http://127.0.0.1:8765", "http://[::1]:8765"])
def test_default_loopback_origins(app, origin):
    assert app.test_client().get("/api/state", base_url=origin, headers={**AUTH, "Origin": origin}).status_code == 200


@pytest.mark.parametrize("path", JSON_POSTS)
@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data", "application/problem+json"])
def test_json_actions_reject_other_content_types(app, path, content_type):
    response = app.test_client().post(path, headers=AUTH, data="{}", content_type=content_type)
    assert response.status_code == 415


@pytest.mark.parametrize("path", JSON_POSTS)
@pytest.mark.parametrize("body", ["[]", "null", '"text"', "123", "true", "{invalid", ""])
def test_json_actions_require_an_object(app, path, body):
    response = app.test_client().post(path, headers=AUTH, data=body, content_type="application/json")
    assert response.status_code == 400
    assert response.get_json()["error"] == "JSON body must be an object."


def test_authenticated_upload_actions_and_no_cookie_architecture(app):
    client = app.test_client()
    response = client.post("/api/slides", headers=AUTH,
                           data={"slides": (io.BytesIO(b"synthetic-image"), "slide.png")})
    assert response.status_code == 200
    assert len(response.get_json()["slide_paths"]) == 1
    for path in ["/api/session/start", "/api/session/pause", "/api/session/stop",
                 "/api/slides/next", "/api/slides/previous"]:
        assert client.post(path, headers=AUTH, json={}).status_code == 200
    assert client.post("/api/slides", headers=AUTH, json={}).status_code == 415
    client.set_cookie("controller-token", TEST_TOKEN)
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", query_string={"token": TEST_TOKEN}).status_code == 401


def test_factory_requires_explicit_valid_token(tmp_path):
    with pytest.raises(TypeError):
        api.create_app(tmp_path / "state", tmp_path / "slides")
    for token in ("", None, "short", "x" * 32 + "\n"):
        with pytest.raises(ValueError):
            api.create_app(tmp_path / "state", tmp_path / "slides", token=token)


def test_bearer_uses_constant_time_comparison(app, monkeypatch):
    comparisons = []
    original = api.secrets.compare_digest

    def compare(left, right):
        comparisons.append((type(left), type(right)))
        return original(left, right)

    monkeypatch.setattr(api.secrets, "compare_digest", compare)
    assert app.test_client().get("/api/state", headers=AUTH).status_code == 200
    assert comparisons == [(bytes, bytes)]


def test_token_generation_is_random_private_and_preserves_existing(private_token_dir):
    tmp_path = private_token_dir
    path = tmp_path / "private" / "controller.token"
    token = security.load_or_create_token(path)
    assert len(token) >= 64
    assert security.validate_token(token) == token
    assert security.load_or_create_token(path) == token
    assert path.read_text().strip() == token
    assert security.load_or_create_token(tmp_path / "second.token") != token
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_invalid_existing_token_is_not_overwritten(private_token_dir):
    tmp_path = private_token_dir
    path = tmp_path / "controller.token"
    path.write_text("invalid")
    path.chmod(0o600)
    with pytest.raises(ValueError):
        security.load_or_create_token(path)
    assert path.read_text() == "invalid"


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership and permission enforcement")
def test_existing_public_token_is_rejected(private_token_dir):
    tmp_path = private_token_dir
    path = tmp_path / "controller.token"
    path.write_text(TEST_TOKEN)
    path.chmod(0o644)
    with pytest.raises(ValueError, match="permissions 0600"):
        security.load_or_create_token(path)
    assert path.read_text() == TEST_TOKEN


@pytest.mark.parametrize("directory", [False, True])
def test_symbolic_link_token_paths_are_rejected(private_token_dir, directory):
    tmp_path = private_token_dir
    target = tmp_path / "real"
    target.mkdir()
    token_path = target / "controller.token"
    token_path.write_text(TEST_TOKEN)
    token_path.chmod(0o600)
    link = tmp_path / "linked"
    try:
        link.symlink_to(target if directory else token_path, target_is_directory=directory)
    except OSError:
        pytest.skip("Creating symbolic links requires privileges on this platform")
    with pytest.raises(ValueError, match="symbolic links"):
        security.load_or_create_token(link / "controller.token" if directory else link)
    assert token_path.read_text() == TEST_TOKEN


def test_repo_token_and_non_regular_file_are_rejected(private_token_dir):
    tmp_path = private_token_dir
    with pytest.raises(ValueError, match="outside the repository"):
        security.load_or_create_token(security.REPOSITORY_ROOT / "forbidden.token")
    directory = tmp_path / "not-a-token"
    directory.mkdir()
    with pytest.raises(ValueError, match="regular"):
        security.load_or_create_token(directory)


@pytest.mark.parametrize("origin", ["*", "null", "https://user:password@example.com", "https://example.com/path",
                                    "https://example.com/", "https://example.com?query", "ftp://example.com",
                                    "https://.example.com", "https://example.com:invalid"])
def test_invalid_origin_configuration_rejected(origin):
    with pytest.raises(ValueError):
        security.normalize_origin(origin)


def test_cli_defaults_are_loopback_and_custom_port_origins(private_token_dir, monkeypatch, capsys):
    tmp_path = private_token_dir
    captured = {}
    token_path = tmp_path / "private" / "controller.token"
    monkeypatch.setattr(sys, "argv", ["presenter-hud-api", "--state", str(tmp_path / "state.json"),
                                    "--slides", str(tmp_path / "slides"), "--token-file", str(token_path),
                                    "--port", "9911"])

    def run(app, **kwargs):
        captured.update(kwargs)
        client = app.test_client()
        assert client.get("/health").get_json() == {"ok": True}
        assert client.get("/api/state", headers={
            "Authorization": f"Bearer {security.load_or_create_token(token_path)}",
            "Origin": "http://localhost:9911",
        }).status_code == 200
        assert client.get("/api/state", headers={"Origin": "http://localhost:8765"}).status_code == 403

    monkeypatch.setattr(api.Flask, "run", run)
    api.main()
    assert captured == {"host": "127.0.0.1", "port": 9911, "threaded": True, "debug": False}
    output = capsys.readouterr()
    assert security.load_or_create_token(token_path) not in output.out + output.err


def test_controller_pairing_and_all_fetches_use_memory_only_bearer(app):
    page = app.test_client().get("/").get_data(as_text=True)
    assert '<fieldset id="controller-controls" disabled>' in page
    assert 'type="password" autocomplete="off"' in page
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "console.", TEST_TOKEN):
        assert forbidden not in page
    assert page.count("await fetch(") == 1
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the browser pairing test")
    state = app.test_client().get("/api/state", headers=AUTH).get_json()
    script = page.split("<script>", 1)[1].split("</script>", 1)[0]
    harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const elements = new Map();
const element = selector => {
  if (!elements.has(selector)) elements.set(selector, {
    value: '', textContent: '', hidden: true, disabled: true, style: {}, files: [],
    classList: {remove() {}, toggle() {}}, setAttribute() {}
  });
  return elements.get(selector);
};
let responseStatus = 200;
let networkFailure = false;
const calls = [];
const context = vm.createContext({
  document: {querySelector: element}, Headers, FormData,
  setInterval(fn) { this.poll = fn; },
  fetch: async (path, options) => {
    calls.push({path, ...options});
    if (networkFailure) throw new Error('Disconnected');
    return {status: responseStatus, ok: responseStatus === 200, json: async () => STATE};
  }
});
vm.runInContext(SCRIPT, context);
(async () => {
  assert.equal(calls.length, 0);
  await context.refresh();
  await context.post('/api/session/pause');
  await context.upload();
  assert.equal(calls.length, 0);
  element('#controller-token').value = TOKEN;
  await context.connectController();
  assert.equal(element('#controller-token').value, '');
  assert.equal(element('#controller-controls').disabled, false);
  await context.post('/api/session/pause');
  await context.upload();
  assert(calls.some(call => call.path === '/api/slides'));
  const post = calls.find(call => call.path === '/api/session/pause');
  assert.equal(post.body, '{}');
  assert.equal(post.headers.get('Content-Type'), 'application/json');
  const upload = calls.find(call => call.path === '/api/slides');
  assert.equal(upload.headers.get('Content-Type'), null);
  for (const call of calls) {
    assert.equal(call.headers.get('Authorization'), `Bearer ${TOKEN}`);
    assert.equal(call.credentials, 'omit');
    assert.equal(call.cache, 'no-store');
    assert(!call.path.includes(TOKEN));
  }
  responseStatus = 401;
  await context.refresh();
  assert.equal(element('#controller-controls').disabled, true);
  assert.match(element('#control-error').textContent, /Token rejected/);
  const count = calls.length;
  await context.refresh();
  assert.equal(calls.length, count);
  responseStatus = 200;
  networkFailure = true;
  element('#controller-token').value = TOKEN;
  await context.connectController();
  assert.equal(element('#controller-controls').disabled, true);
  assert.equal(element('#controller-token').value, '');
  networkFailure = false;
  element('#controller-token').value = TOKEN;
  await context.connectController();
  assert.equal(element('#controller-controls').disabled, false);
  context.disconnectController();
  assert.equal(element('#controller-controls').disabled, true);
  assert.match(element('#status').textContent, /Locked/);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    program = f"const STATE = {json.dumps(state)};\nconst SCRIPT = {json.dumps(script)};\nconst TOKEN = {json.dumps(TEST_TOKEN)};\n" + harness
    result = subprocess.run([node, "-"], input=program, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
