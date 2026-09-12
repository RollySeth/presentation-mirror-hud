from __future__ import annotations

import json
import os
from pathlib import Path

from filelock import FileLock

from .models import HudState, utc_now


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self.path) + ".lock", timeout=10)
        with self._lock:
            if not self.path.exists():
                self.save(HudState())

    def load(self) -> HudState:
        with self._lock:
            try:
                return HudState.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                state = HudState()
                self.save(state)
                return state

    def save(self, state: HudState) -> HudState:
        with self._lock:
            state.updated_at = utc_now()
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
            return state

    def update(self, mutator) -> HudState:
        with self._lock:
            state = self.load()
            mutator(state)
            return self.save(state)
