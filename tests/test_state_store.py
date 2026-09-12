import multiprocessing

from presenter_hud.models import CoachCue
from presenter_hud.state_store import StateStore


def test_state_store_round_trip(tmp_path):
    store = StateStore(tmp_path / "state.json")
    state = store.update(lambda value: setattr(value, "cue", CoachCue("Keep going")))
    assert state.cue.text == "Keep going"
    assert store.load().cue.text == "Keep going"


def _increment_elapsed(path, ready):
    store = StateStore(path)
    ready.wait(timeout=15)
    for _ in range(30):
        store.update(lambda state: setattr(state, "elapsed_seconds", state.elapsed_seconds + 1))


def test_state_updates_are_atomic_across_processes(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    context = multiprocessing.get_context("spawn")
    ready = context.Barrier(2)
    processes = [context.Process(target=_increment_elapsed, args=(path, ready)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=30)
            assert process.exitcode == 0
        assert store.load().elapsed_seconds == 60
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
