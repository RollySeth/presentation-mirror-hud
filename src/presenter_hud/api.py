from __future__ import annotations

import argparse
import math
import secrets
from uuid import uuid4
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request
from werkzeug.utils import secure_filename

from .coach import refresh_coach
from .models import CoachCue, LiveMetrics
from .presentation import IMAGE_SUFFIXES, discover_slides
from .state_store import StateStore
from .avatar import coach_avatar, expression_avatar
from .security import default_token_file, load_or_create_token, loopback_origins, normalize_origin, origin_hosts, validate_token


CONTROL_PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Presenter Mirror</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    body { margin: 0; background: #05040c; color: #f1edff; }
    main { max-width: 720px; margin: auto; padding: 24px; }
    h1 { color: #b89aff; margin-bottom: 4px; }
    .card { background: #100b1d; border: 1px solid #382758; border-radius: 18px; padding: 18px; margin: 16px 0; }
    label { display: block; margin: 12px 0 6px; color: #c6b8e5; }
    input, button { box-sizing: border-box; border-radius: 10px; border: 1px solid #574176; padding: 12px; font: inherit; }
    input { width: 100%; background: #090713; color: white; }
    button { background: linear-gradient(135deg, #7138ce, #343b9d); color: white; font-weight: 700; cursor: pointer; }
    button.secondary { background: #162034; border-color: #467db0; }
    button:disabled { opacity: .45; cursor: not-allowed; }
    fieldset { border: 0; padding: 0; margin: 0; min-width: 0; }
    .row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 14px; }
    #status { color: #b7eaff; white-space: pre-wrap; }
    small { color: #b2a3c7; }
    .live-coach { display: flex; align-items: center; gap: 18px; }
    .live-coach svg { width: 88px; height: 88px; flex-shrink: 0; }
    .live-coach strong { color: #69efff; }
    #coach-emoji { font-size: 26px; margin-left: 8px; }
    .avatar-eyes { transform-box: fill-box; transform-origin: center; animation: blink 4.8s infinite; }
    @keyframes blink { 0%, 46%, 50%, 100% { transform: scaleY(1); } 48% { transform: scaleY(.1); } }
    .talking { transform-box: fill-box; transform-origin: center; animation: talk .18s infinite alternate; }
    @keyframes talk { from { transform: scaleY(.45); } to { transform: scaleY(1); } }
    @media (prefers-reduced-motion: reduce) { .avatar-eyes, .talking { animation: none; } }
  </style>
</head>
<body><main>
  <h1>Presenter Mirror</h1>
  <small>Local rehearsal controller</small>
  <section class="card">
    <label for="controller-token">Controller token</label>
    <input id="controller-token" type="password" autocomplete="off" spellcheck="false"
      placeholder="Paste the private token from this installation">
    <div class="row">
      <button id="connect" onclick="connectController()">Connect</button>
      <button class="secondary" onclick="disconnectController()">Disconnect</button>
    </div>
    <p id="pairing-status" role="status">Locked. Enter your controller token to connect.</p>
    <small>The token stays only in this page's memory. Reloading requires pairing again. Use a trusted HTTPS connection for remote access.</small>
  </section>
  <section class="card live-coach">
    <svg viewBox="0 0 88 88" role="img" aria-label="Live coach avatar: visible expression">
      <circle cx="44" cy="44" r="40" fill="#160d2b" stroke="#ab70ff" stroke-width="2"/>
      <circle cx="44" cy="44" r="35" fill="none" stroke="#463565"/>
      <g class="avatar-eyes" fill="#69efff">
        <rect x="25" y="28" width="10" height="15" rx="4"/>
        <rect x="53" y="28" width="10" height="15" rx="4"/>
      </g>
      <g id="avatar-brows" fill="none" stroke="#69efff" stroke-width="2.5" stroke-linecap="round"></g>
      <text id="avatar-unknown" x="44" y="51" text-anchor="middle" fill="#b2a3c7" font-size="24" style="display:none">?</text>
      <rect id="avatar-mouth" x="31" y="55" width="26" height="4" rx="2" fill="#f1edff"/>
      <path id="smile-mouth" d="M29 52 Q44 72 59 52" fill="none" stroke="#69efff" stroke-width="3" style="display:none"/>
    </svg>
    <div>
      <strong id="avatar-label">Locked</strong><span id="coach-emoji" aria-label="Visible expression"></span>
      <p id="live-cue">Pair the controller to view live coaching.</p>
      <small>Facial-expression estimates, not certainty about feelings. Talking is measured separately; laughter is not a supported expression class.</small>
    </div>
  </section>
  <fieldset id="controller-controls" disabled>
  <section class="card">
    <label for="title">Session title</label>
    <input id="title" value="Presentation Rehearsal">
    <label for="minutes">Minutes</label>
    <input id="minutes" type="number" min="1" value="10">
    <div class="row">
      <button onclick="start()">Start</button>
      <button class="secondary" onclick="post('/api/session/pause')">Pause</button>
      <button onclick="post('/api/session/stop')">Stop</button>
    </div>
  </section>
  <section class="card">
    <label>Slides exported as PNG/JPEG</label>
    <input id="slides" type="file" accept="image/*" multiple>
    <div class="row">
      <button onclick="upload()">Upload</button>
      <button onclick="post('/api/slides/previous')">Previous</button>
      <button onclick="post('/api/slides/next')">Next</button>
    </div>
    <small>For private PowerPoint or SharePoint links, export on the signed-in companion device and upload the slide images.</small>
  </section>
  <section class="card">
    <label for="brightness">Slide brightness boost: <span id="brightness-value">1.4</span></label>
    <input id="brightness" type="range" min="1" max="2.5" step="0.1" value="1.4"
      oninput="document.querySelector('#brightness-value').textContent=this.value"
      onchange="post('/api/display', {slide_brightness:Number(this.value)})">
    <small>Brightens the slide while preserving black. Does not change the monitor's backlight.</small>
  </section>
  <section class="card">
    <label for="cue">Coach message</label>
    <input id="cue" placeholder="Remember to pause after the key number.">
    <button style="margin-top:12px;width:100%" onclick="cue()">Show cue</button>
  </section>
  </fieldset>
  <p id="control-error" role="alert" hidden></p>
  <section class="card"><strong>Live state</strong><p id="status">Locked. Connect to view live state.</p></section>
  <script>
    let controllerToken = '';
    function showError(error) {
      const errorBox = document.querySelector('#control-error');
      errorBox.textContent = error.message || 'Could not reach the controller.';
      errorBox.hidden = false;
    }
    function disconnectController() {
      controllerToken = '';
      document.querySelector('#controller-token').value = '';
      document.querySelector('#controller-controls').disabled = true;
      document.querySelector('#pairing-status').textContent = 'Locked. Enter your controller token to connect.';
      document.querySelector('#avatar-label').textContent = 'Locked';
      document.querySelector('#coach-emoji').textContent = '';
      document.querySelector('#avatar-mouth').classList.remove('talking');
      document.querySelector('.live-coach svg').style.opacity = '.25';
      document.querySelector('#live-cue').textContent = 'Pair the controller to view live coaching.';
      document.querySelector('#status').textContent = 'Locked. Connect to view live state.';
      document.querySelector('#control-error').hidden = true;
    }
    async function apiFetch(path, options = {}) {
      if (!controllerToken) throw new Error('Connect with your controller token first.');
      const headers = new Headers(options.headers);
      headers.set('Authorization', `Bearer ${controllerToken}`);
      const response = await fetch(path, {...options, headers, credentials: 'omit', cache: 'no-store'});
      if (response.status === 401) {
        disconnectController();
        throw new Error('Token rejected. Enter the correct controller token and connect again.');
      }
      if (!response.ok) throw new Error(`Controller request failed (${response.status}). Check the connection and allowed origin.`);
      return response;
    }
    async function connectController() {
      const candidate = document.querySelector('#controller-token').value.trim();
      disconnectController();
      if (!candidate) {
        showError(new Error('Enter your controller token first.'));
        return;
      }
      controllerToken = candidate;
      document.querySelector('#pairing-status').textContent = 'Connecting…';
      if (await refresh()) {
        document.querySelector('#controller-controls').disabled = false;
        document.querySelector('#pairing-status').textContent = 'Connected. Reloading this page will lock the controller.';
      } else {
        controllerToken = '';
        document.querySelector('#pairing-status').textContent = 'Connection failed. Enter your token to retry.';
      }
    }
    async function post(path, body = {}) {
      try {
      const response = await apiFetch(path, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify(body)
      });
      document.querySelector('#control-error').hidden = true;
      await refresh();
      return response;
      } catch (error) {
        showError(error);
      }
    }
    function start() {
      return post('/api/session/start', {
        title: document.querySelector('#title').value,
        duration_seconds: Number(document.querySelector('#minutes').value) * 60
      });
    }
    function cue() {
      return post('/api/cue', {text: document.querySelector('#cue').value});
    }
    async function upload() {
      try {
      const data = new FormData();
      for (const file of document.querySelector('#slides').files) data.append('slides', file);
      await apiFetch('/api/slides', {method:'POST', body:data});
      document.querySelector('#control-error').hidden = true;
      await refresh();
      } catch (error) {
        showError(error);
      }
    }
    async function refresh() {
      if (!controllerToken) return false;
      try {
      const response = await apiFetch('/api/state');
      const state = await response.json();
      if (!controllerToken) return false;
      const brightness = document.querySelector('#brightness');
      if (document.activeElement !== brightness) {
        brightness.value = state.slide_brightness;
        document.querySelector('#brightness-value').textContent = state.slide_brightness.toFixed(1);
      }
      const avatar = state.expression_avatar;
      document.querySelector('#avatar-label').textContent = `${avatar.label} · ${avatar.activity}`;
      document.querySelector('#coach-emoji').textContent = avatar.emoji;
      document.querySelector('.live-coach svg').style.opacity = '1';
      const speaking = avatar.voice === 'speaking';
      const eyes = {
        normal: '<rect x="27" y="29" width="7" height="12" rx="3"/><rect x="54" y="29" width="7" height="12" rx="3"/>',
        narrow: '<path d="M24 37 H36 M52 37 H64" fill="none" stroke="#69efff" stroke-width="3"/>',
        smile: '<path d="M24 39 Q30 29 36 39 M52 39 Q58 29 64 39" fill="none" stroke="#69efff" stroke-width="3"/>',
        wide: '<ellipse cx="30" cy="35" rx="5" ry="7" fill="none" stroke="#69efff" stroke-width="3"/><ellipse cx="58" cy="35" rx="5" ry="7" fill="none" stroke="#69efff" stroke-width="3"/>'
      };
      const brows = {
        angry: 'M23 22 L37 28 M51 28 L65 22',
        worried: 'M23 28 L37 22 M51 22 L65 28',
        raised: 'M23 25 Q30 16 37 25 M51 25 Q58 16 65 25',
        asymmetric: 'M23 22 L37 28 M51 24 L65 24'
      };
      const mouths = {
        smile: 'M29 52 Q44 72 59 52', frown: 'M29 64 Q44 48 59 64',
        flat: 'M31 58 H57', slant: 'M31 55 L57 61',
        open: 'M29 58 a15 5 0 1 0 30 0 a15 5 0 1 0 -30 0',
        round: 'M37 60 a7 9 0 1 0 14 0 a7 9 0 1 0 -14 0'
      };
      document.querySelector('.avatar-eyes').innerHTML = eyes[avatar.eyes] || '';
      document.querySelector('#avatar-brows').innerHTML = brows[avatar.brows] ? `<path d="${brows[avatar.brows]}"/>` : '';
      document.querySelector('#avatar-unknown').style.display = avatar.eyes === 'unknown' ? '' : 'none';
      const mouth = document.querySelector('#avatar-mouth');
      mouth.style.display = speaking ? '' : 'none';
      mouth.setAttribute('height', speaking ? 4 + Math.round(avatar.mouth * 14) : 4);
      mouth.classList.toggle('talking', speaking);
      const expressionMouth = document.querySelector('#smile-mouth');
      expressionMouth.setAttribute('d', mouths[avatar.mouth_shape] || '');
      expressionMouth.style.display = !speaking && mouths[avatar.mouth_shape] ? '' : 'none';
      document.querySelector('#live-cue').textContent = state.cue.text;
      document.querySelector('#status').textContent =
        `${state.running ? (state.paused ? 'Paused' : 'Running') : 'Stopped'} · ` +
        `${Math.floor(state.elapsed_seconds)}s / ${state.duration_seconds}s · ` +
        `slide ${state.slide_paths.length ? state.slide_index + 1 : 0}/${state.slide_paths.length}\\n` +
        `Coach: ${state.cue.text}\\n` +
        `Camera: ${state.metrics.camera_active ? (state.metrics.face_present ? 'face visible' : 'no face detected') : 'off'} · ` +
        `Mic: ${state.metrics.microphone_active ? Math.round(state.metrics.volume * 100) + '%' : 'off'}\\n` +
        `Speech: ${state.metrics.speech_active ? 'offline recognition active' : (state.running ? 'starting' : 'starts with rehearsal')} · ` +
        `${state.metrics.speech_pending_word_count ? '~' : ''}${state.metrics.speech_word_count + state.metrics.speech_pending_word_count} words · ${Math.round(state.metrics.words_per_minute)} WPM`;
      return true;
      } catch (error) {
        document.querySelector('#avatar-label').textContent = 'Disconnected';
        document.querySelector('#coach-emoji').textContent = '';
        document.querySelector('#avatar-mouth').classList.remove('talking');
        document.querySelector('.live-coach svg').style.opacity = '.25';
        document.querySelector('#status').textContent = controllerToken ? 'Cannot reach the controller. Check the connection.' : 'Locked. Connect to view live state.';
        showError(error);
        return false;
      }
    }
    setInterval(refresh, 1000);
  </script>
</main></body></html>
"""


def create_app(
    state_path: str | Path,
    slides_directory: str | Path,
    *,
    token: str,
    allowed_origins: list[str] | None = None,
    trusted_hosts: list[str] | None = None,
) -> Flask:
    token = validate_token(token)
    origins = [normalize_origin(origin) for origin in (
        loopback_origins(8765) if allowed_origins is None else allowed_origins
    )]
    app = Flask(__name__)
    app.config["TRUSTED_HOSTS"] = trusted_hosts if trusted_hosts is not None else list(
        dict.fromkeys(["localhost", "127.0.0.1", "[::1]", *origin_hosts(origins)])
    )
    store = StateStore(state_path)
    slides_path = Path(slides_directory)
    slides_path.mkdir(parents=True, exist_ok=True)

    @app.before_request
    def protect_controller():
        origin = request.headers.get("Origin")
        if origin is not None:
            try:
                accepted = normalize_origin(origin) in origins
            except ValueError:
                accepted = False
            if not accepted:
                return {"error": "Origin is not allowed."}, 403
        if request.path == "/api" or request.path.startswith("/api/"):
            authorization = request.headers.get("Authorization", "")
            scheme, _, supplied = authorization.partition(" ")
            if scheme.lower() != "bearer" or not secrets.compare_digest(
                supplied.encode("utf-8"), token.encode("ascii")
            ):
                return {"error": "Pair the controller with a valid bearer token."}, 401, {"WWW-Authenticate": "Bearer"}
            if request.method == "POST":
                if request.path == "/api/slides":
                    if request.mimetype != "multipart/form-data":
                        return {"error": "Slide upload requires multipart/form-data."}, 415
                else:
                    if request.mimetype != "application/json":
                        return {"error": "This endpoint requires application/json."}, 415
                    if not isinstance(request.get_json(silent=True), dict):
                        return {"error": "JSON body must be an object."}, 400

    @app.after_request
    def private_response(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/")
    def controller():
        return render_template_string(CONTROL_PAGE)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/api/state")
    def get_state():
        state = store.load()
        payload = state.to_dict()
        payload["coach_avatar"] = coach_avatar(state)
        payload["expression_avatar"] = expression_avatar(state)
        return jsonify(payload)

    @app.post("/api/display")
    def display_settings():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return {"error": "display settings must be an object"}, 400
        brightness = body.get("slide_brightness")
        if (
            isinstance(brightness, bool) or not isinstance(brightness, (int, float))
            or not math.isfinite(brightness) or not 1 <= brightness <= 2.5
        ):
            return {"error": "slide_brightness must be a number between 1 and 2.5"}, 400

        def mutate(state):
            state.slide_brightness = float(brightness)
            state.slide_opacity = 255

        return jsonify(store.update(mutate).to_dict())

    @app.post("/api/session/start")
    def start_session():
        body = request.get_json()

        def mutate(state):
            state.session_id = uuid4().hex
            state.metrics.speech_word_count = 0
            state.metrics.speech_pending_word_count = 0
            state.metrics.speech_filler_count = 0
            state.metrics.speech_recent_word_count = 0
            state.metrics.speech_recent_wpm = 0.0
            state.metrics.speech_last_word_age = None
            state.metrics.speech_observed_at = 0.0
            state.metrics.speech_language_event_id = 0
            state.metrics.speech_language_issue = ""
            state.metrics.speech_language_at = 0.0
            state.metrics.words_per_minute = 0.0
            state.metrics.silence_seconds = 0.0
            state.coach_last_shown = {}
            state.coach_feedback = {}
            state.coach_history = []
            state.running = True
            state.paused = False
            state.elapsed_seconds = 0
            state.slide_index = 0
            state.session_title = str(body.get("title", state.session_title))
            state.duration_seconds = max(30, int(body.get("duration_seconds", state.duration_seconds)))
            state.slide_paths = discover_slides(slides_path)
            state.cue = CoachCue("Stand tall. Breathe. Your first sentence sets the room.")

        return jsonify(store.update(mutate).to_dict())

    @app.post("/api/session/pause")
    def pause_session():
        def mutate(state):
            state.paused = not state.paused
            state.metrics.speech_pending_word_count = 0
            state.metrics.speech_recent_word_count = 0
            state.metrics.speech_recent_wpm = 0.0
            state.metrics.speech_last_word_age = None
            state.metrics.speech_observed_at = 0.0
            state.metrics.speech_language_issue = ""
            state.metrics.speech_language_at = 0.0
            refresh_coach(state)

        return jsonify(store.update(mutate).to_dict())

    @app.post("/api/session/stop")
    def stop_session():
        def mutate(state):
            state.running = False
            state.paused = False
            state.metrics.speech_language_event_id = 0
            state.metrics.speech_language_issue = ""
            state.metrics.speech_language_at = 0.0
            state.coach_last_shown = {}
            state.coach_feedback = {}
            state.coach_history = []
            state.cue = CoachCue("Rehearsal complete. Notice one thing you improved.", "encourage", expires_in_seconds=10)

        return jsonify(store.update(mutate).to_dict())

    @app.post("/api/slides/next")
    def next_slide():
        def mutate(state):
            if state.slide_paths:
                state.slide_index = min(state.slide_index + 1, len(state.slide_paths) - 1)

        return jsonify(store.update(mutate).to_dict())

    @app.post("/api/slides/previous")
    def previous_slide():
        def mutate(state):
            state.slide_index = max(0, state.slide_index - 1)

        return jsonify(store.update(mutate).to_dict())

    @app.post("/api/cue")
    def set_cue():
        body = request.get_json()
        text = str(body.get("text", "")).strip()
        if not text:
            return {"error": "text is required"}, 400

        def mutate(state):
            state.cue = CoachCue(text, str(body.get("level", "guide")), expires_in_seconds=10, kind="manual")

        return jsonify(store.update(mutate).to_dict())

    @app.post("/api/metrics")
    def update_metrics():
        body = request.get_json()
        if not isinstance(body, dict):
            return {"error": "metrics must be an object"}, 400
        def mutate(state):
            current = state.metrics.__dict__
            current.update({key: value for key, value in body.items() if key in current})
            state.metrics = LiveMetrics(**current)
            refresh_coach(state)

        return jsonify(store.update(mutate).to_dict())

    @app.post("/api/slides")
    def upload_slides():
        uploads = request.files.getlist("slides")
        if not uploads:
            return {"error": "attach one or more files using the slides field"}, 400
        for existing in slides_path.iterdir():
            if existing.is_file() and existing.suffix.lower() in IMAGE_SUFFIXES:
                existing.unlink()
        for index, upload in enumerate(uploads, start=1):
            original = secure_filename(upload.filename or "")
            suffix = Path(original).suffix.lower()
            if suffix not in IMAGE_SUFFIXES:
                return {"error": f"unsupported image type: {suffix}"}, 400
            upload.save(slides_path / f"slide-{index:03d}{suffix}")

        def mutate(state):
            state.slide_paths = discover_slides(slides_path)
            state.slide_index = 0

        return jsonify(store.update(mutate).to_dict())

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default="data/state.json")
    parser.add_argument("--slides", default="data/slides")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token-file", type=Path, default=default_token_file(),
                        help="Private controller token file outside the repository (created if absent).")
    parser.add_argument("--allowed-origin", action="append",
                        help="Explicit trusted browser origin, repeatable; defaults to loopback origins.")
    args = parser.parse_args()
    try:
        token = load_or_create_token(args.token_file)
        app = create_app(args.state, args.slides, token=token,
                         allowed_origins=args.allowed_origin if args.allowed_origin is not None else loopback_origins(args.port))
    except (OSError, ValueError):
        parser.error("Cannot initialize controller security. Check the private token file, permissions, and allowed origins.")
    app.run(host=args.host, port=args.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
