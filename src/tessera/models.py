"""The one data type every adapter returns."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

Kind = Literal["task", "learning", "win", "incident"]
Sensitivity = Literal["internal", "public-ok", "drop"]


@dataclass
class WorkEvent:
    id: str
    source: str
    timestamp: datetime  # timezone-aware, UTC
    project: str
    title: str
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    kind: Kind = "task"
    sensitivity: Sensitivity = "internal"
    # Why an event was dropped (rule ids only, never the matched text).
    drop_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkEvent:
        d = dict(d)
        d["timestamp"] = datetime.fromisoformat(d["timestamp"])
        return cls(**d)
