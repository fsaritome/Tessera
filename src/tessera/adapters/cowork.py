"""Cowork sessions of the Claude desktop app (agent mode), read straight from disk.

Layout under <Claude-Data>/<profile>/local-agent-mode-sessions/<org>/<user>/:
  local_<id>.json                          session metadata (title, createdAt, ...)
  local_<id>/.claude/projects/*/*.jsonl    transcript, same format as Claude Code

Transcripts are parsed by the Claude Code adapter (incremental, prompts only). The
session title comes from the metadata. Scheduled tasks that run the same title over and
over are tagged "recurring" so later steps can count them once.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Config
from ..models import WorkEvent
from . import claude_code

SOURCE = "cowork"
RECURRING_MIN = 5  # same title this often = a scheduled task


def default_dirs(cfg: Config) -> list[Path]:
    """All <org>/<user> folders holding Cowork sessions."""
    if env := os.environ.get("TESSERA_COWORK_DIR"):
        return [Path(env)]
    if conf := cfg.get("sources.cowork.dir"):
        return [Path(conf).expanduser()]
    home = Path.home()
    bases = [
        Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local")) / "Claude-Data",  # Windows
        Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "Claude",
        home / "Library" / "Application Support" / "Claude",  # macOS
        home / ".config" / "Claude",  # Linux
    ]
    found = set()
    for base in bases:  # layouts differ by app version: with or without a profile folder
        for pattern in ("local-agent-mode-sessions/*/*/local_*.json",
                        "*/local-agent-mode-sessions/*/*/local_*.json"):  # fmt: skip
            found |= {p.parent for p in base.glob(pattern)}
    return sorted(found)


def long_path(p: Path) -> Path:
    """Cowork transcript paths exceed Windows' 260-char limit; use the extended-length form."""
    if os.name != "nt":
        return p
    s = str(p.resolve())
    prefix = "\\" * 2 + "?" + "\\"
    return Path(s if s.startswith(prefix) else prefix + s)


def _metadata(root: Path) -> dict[str, dict[str, Any]]:
    out = {}
    for m in root.glob("local_*.json"):
        try:
            d = json.loads(m.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out[m.stem] = d
    return out


def ingest(
    cfg: Config,
    cursors: dict[str, Any],
    existing: dict[str, WorkEvent],
    since: datetime,
    roots: list[Path] | None = None,
) -> tuple[list[WorkEvent], dict[str, Any]]:
    events: list[WorkEvent] = []
    cur = {"offsets": dict(cursors.get("offsets", {})), "titles": dict(cursors.get("titles", {}))}
    for root in roots or default_dirs(cfg):
        meta = _metadata(root)
        counts = Counter((d.get("title") or "").strip() for d in meta.values())
        for sid, d in meta.items():
            sess_dir = long_path(root / sid / ".claude" / "projects")
            if not sess_dir.is_dir():
                continue
            evs, cur = claude_code.ingest(
                cfg, cur, existing, since, projects_dir=sess_dir, source=SOURCE, prefix="cw"
            )
            title = (d.get("title") or "").strip()
            for ev in evs:
                ev.project = "Cowork"
                ev.title = title or ev.title
                ev.tags = ["cowork"]
                ev.details["cowork_session"] = sid
                if title and counts[title] >= RECURRING_MIN:
                    ev.tags.append("recurring")
                if ev.sensitivity == "drop" and ev.drop_reasons == ["project_excluded"]:
                    ev.sensitivity, ev.drop_reasons = "internal", []  # VM cwd is not a project
            events += evs
    return events, cur
