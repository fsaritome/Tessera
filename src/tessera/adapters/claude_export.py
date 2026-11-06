"""claude.ai chats (web, app, Chrome extension) from the official data export.

claude.ai keeps chats online only, so the export zip is the one way in. To keep it hands-
off, the adapter looks for the newest zip containing conversations.json in inbox/ and in
~/Downloads; nothing has to be unpacked or moved. Exports are cumulative, so only the
newest zip is read, and only conversations changed since the last run become events.
Like the other adapters, only the human side (prompts) and attachment file names are kept.
"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Config
from ..models import WorkEvent
from .claude_code import MAX_PROMPTS, clean_prompt

SOURCE = "claude_ai"


def search_dirs(cfg: Config) -> list[Path]:
    dirs = [cfg.root / "inbox"]
    dirs += [Path(p).expanduser() for p in cfg.get("sources.claude_export.dirs") or []]
    dirs.append(Path.home() / "Downloads")
    return [d for d in dirs if d.is_dir()]


def find_export(dirs: list[Path]) -> Path | None:
    """Newest *.zip that holds a conversations.json."""
    zips = sorted((z for d in dirs for z in d.glob("*.zip")), key=lambda z: z.stat().st_mtime)
    for z in reversed(zips):
        try:
            with zipfile.ZipFile(z) as zf:
                if any(n.endswith("conversations.json") for n in zf.namelist()):
                    return z
        except (zipfile.BadZipFile, OSError):
            continue
    return None


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _text(msg: dict[str, Any]) -> str:
    parts = [c.get("text", "") for c in msg.get("content") or [] if c.get("type") == "text"]
    return "\n".join(p for p in parts if p) or msg.get("text", "")


def parse(conversations: list[dict[str, Any]], since: datetime) -> list[WorkEvent]:
    events = []
    for conv in conversations:
        updated = _ts(conv.get("updated_at")) or _ts(conv.get("created_at"))
        if not updated or updated < since:
            continue
        prompts, files, answers = [], [], 0
        for msg in conv.get("chat_messages") or []:
            if msg.get("sender") == "human":
                if p := clean_prompt(_text(msg)):
                    prompts.append(p)
                for a in (msg.get("attachments") or []) + (msg.get("files") or []):
                    if name := a.get("file_name"):
                        files.append(Path(name).name)
            else:
                answers += 1
        if not prompts:
            continue
        events.append(
            WorkEvent(
                id=f"ai:{conv.get('uuid')}",
                source=SOURCE,
                timestamp=updated,
                project="Chat",
                title=(conv.get("name") or prompts[0][:100]).strip(),
                details={
                    "session_id": conv.get("uuid"),
                    "first": conv.get("created_at"),
                    "last": conv.get("updated_at"),
                    "prompts": prompts[:MAX_PROMPTS],
                    "prompt_count": len(prompts),
                    "assistant_turns": answers,
                    "files": sorted(set(files))[:40],
                    "tools": {},
                },
                tags=["chat"],
            )
        )
    return events


def ingest(
    cfg: Config, cursors: dict[str, Any], since: datetime, dirs: list[Path] | None = None
) -> tuple[list[WorkEvent], dict[str, Any], Path | None]:
    z = find_export(dirs if dirs is not None else search_dirs(cfg))
    if z is None:
        return [], cursors, None
    stamp = f"{z.name}:{int(z.stat().st_mtime)}"
    if cursors.get("zip") == stamp:
        return [], cursors, z  # already read this export
    with zipfile.ZipFile(z) as zf:
        name = next(n for n in zf.namelist() if n.endswith("conversations.json"))
        conversations = json.loads(zf.read(name).decode("utf-8"))
    seen: dict[str, str] = dict(cursors.get("seen", {}))
    fresh = [c for c in conversations if seen.get(c.get("uuid")) != c.get("updated_at")]
    events = parse(fresh, since)
    for c in conversations:
        seen[c.get("uuid")] = c.get("updated_at")
    return events, {"zip": stamp, "seen": seen}, z
