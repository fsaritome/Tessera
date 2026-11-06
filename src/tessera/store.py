"""Local, git-ignored state: normalized events (upserted by id) and adapter cursors.

Raw-ish event data lives here, so state/ must never be committed or synced to a cloud.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import WorkEvent


class Store:
    def __init__(self, state_dir: Path):
        self.dir = state_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.dir / "events.jsonl"
        self.cursors_path = self.dir / "cursors.json"

    # --- cursors -------------------------------------------------------------
    def cursors(self, adapter: str) -> dict[str, Any]:
        if not self.cursors_path.is_file():
            return {}
        return json.loads(self.cursors_path.read_text(encoding="utf-8")).get(adapter, {})

    def save_cursors(self, adapter: str, data: dict[str, Any]) -> None:
        allc = {}
        if self.cursors_path.is_file():
            allc = json.loads(self.cursors_path.read_text(encoding="utf-8"))
        allc[adapter] = data
        self._atomic_write(self.cursors_path, json.dumps(allc, indent=1))

    def reset(self) -> None:
        for p in (self.events_path, self.cursors_path):
            p.unlink(missing_ok=True)

    # --- events --------------------------------------------------------------
    def load_events(self) -> dict[str, WorkEvent]:
        out: dict[str, WorkEvent] = {}
        if not self.events_path.is_file():
            return out
        with self.events_path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    ev = WorkEvent.from_dict(json.loads(line))
                    out[ev.id] = ev
        return out

    def upsert(self, events: list[WorkEvent]) -> int:
        if not events:
            return 0
        current = self.load_events()
        for ev in events:
            current[ev.id] = ev
        body = "".join(json.dumps(e.to_dict(), ensure_ascii=False) + "\n" for e in current.values())
        self._atomic_write(self.events_path, body)
        return len(events)

    def window(self, start: datetime, end: datetime) -> list[WorkEvent]:
        evs = [e for e in self.load_events().values() if start <= e.timestamp <= end]
        return sorted(evs, key=lambda e: e.timestamp)

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
