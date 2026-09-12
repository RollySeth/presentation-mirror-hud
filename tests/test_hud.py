import os
from pathlib import Path
import subprocess
import sys
import time

from presenter_hud.hud import advance_timer
from presenter_hud.models import HudState
from presenter_hud.state_store import StateStore


def test_timer_stops_at_duration():
    state = HudState(running=True, duration_seconds=30, elapsed_seconds=29.8)
    advance_timer(state, 0.6)
    assert state.elapsed_seconds == 30
    assert not state.running


def test_timer_does_not_advance_while_paused():
    state = HudState(running=True, paused=True, elapsed_seconds=12)
    advance_timer(state, 3)
    assert state.elapsed_seconds == 12


def test_hud_timer_tracks_wall_time(tmp_path):
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    store.update(lambda state: setattr(state, "running", True))
    environment = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    process = subprocess.Popen(
        [
            sys.executable, "-m", "presenter_hud.hud",
            "--state", str(state_path), "--size", "320x240", "--windowed",
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while store.load().elapsed_seconds == 0 and process.poll() is None:
            assert time.monotonic() < deadline, "HUD did not start its timer"
            time.sleep(0.1)
        assert process.poll() is None, process.communicate()
        before = store.load().elapsed_seconds
        started = time.monotonic()
        time.sleep(3)
        elapsed = store.load().elapsed_seconds - before
        assert process.poll() is None, process.communicate()
        assert abs(elapsed - (time.monotonic() - started)) < 1.0
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=10)
