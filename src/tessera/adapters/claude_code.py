"""Claude Code transcripts (JSONL under ~/.claude/projects/).

Incremental: remembers the byte offset per file and only reads appended lines.
One WorkEvent per (session, local day); later reads merge into the stored event.
Only user prompts, session titles, tool names and edited file *basenames* are kept —
assistant text and tool output are never ingested.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Config
from ..config import tz as config_tz
from ..models import WorkEvent
from . import project_name

SOURCE = "claude_code"
MAX_PROMPTS = 40
MAX_PROMPT_CHARS = 600
MAX_FILES = 60
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

_TAG_BLOCK = re.compile(
    r"<(system-reminder|task-notification|side-sessions-event|ci-monitor-event|"
    r"command-[\w-]+|local-command-[\w-]+|ide_[\w-]+)"
    r"[^>]*>.*?</\1>",
    re.S,
)
_ANY_TAG = re.compile(r"</?[\w-]+(?:\s[^>]*)?>")
_SKIP_PREFIXES = (
    "This session is being continued",
    "Caveat:",
    "[Request interrupted",
    "None of the sessions you started",
    "[SYSTEM NOTIFICATION",
)


def default_dir(cfg: Config) -> Path:
    if env := os.environ.get("TESSERA_CLAUDE_DIR"):
        return Path(env)
    if conf := cfg.get("sources.claude_code.projects_dir"):
        return Path(conf).expanduser()
    return Path.home() / ".claude" / "projects"


def clean_prompt(text: str) -> str:
    text = _TAG_BLOCK.sub("", text)
    text = _ANY_TAG.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text or text.startswith(_SKIP_PREFIXES):
        return ""
    return text[:MAX_PROMPT_CHARS]


def _user_texts(msg: dict[str, Any]) -> list[str]:
    content = msg.get("content")
    if isinstance(content, str):
        return [content]
    out = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                out.append(item.get("text", ""))
    return out


class _Agg:
    """Per (session, day) accumulator; serializable into WorkEvent.details."""

    def __init__(self, details: dict[str, Any] | None = None):
        d = details or {}
        self.prompts: list[str] = list(d.get("prompts", []))
        self.tools: Counter[str] = Counter(d.get("tools", {}))
        self.files: list[str] = list(d.get("files", []))
        self.assistant_turns: int = d.get("assistant_turns", 0)
        self.first: str | None = d.get("first")
        self.last: str | None = d.get("last")
        self.branch: str | None = d.get("branch")
        self.cwd: str | None = d.get("cwd")
        self.session_id: str | None = d.get("session_id")

    def touch(self, ts: str) -> None:
        if not self.first or ts < self.first:
            self.first = ts
        if not self.last or ts > self.last:
            self.last = ts

    def details(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "cwd": self.cwd,
            "branch": self.branch,
            "first": self.first,
            "last": self.last,
            "prompts": self.prompts[:MAX_PROMPTS],
            "prompt_count": len(self.prompts),
            "assistant_turns": self.assistant_turns,
            "tools": dict(self.tools),
            "files": self.files[:MAX_FILES],
        }


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def ingest(
    cfg: Config,
    cursors: dict[str, Any],
    existing: dict[str, WorkEvent],
    since: datetime,
    projects_dir: Path | None = None,
    source: str = SOURCE,
    prefix: str = "cc",
) -> tuple[list[WorkEvent], dict[str, Any]]:
    """Read new lines from all transcripts. Returns (events to upsert, new cursors)."""
    base = projects_dir or default_dir(cfg)
    tz = config_tz(cfg)
    include_side = cfg.get("sources.claude_code.include_sidechains", False)
    offsets: dict[str, int] = dict(cursors.get("offsets", {}))
    titles: dict[str, str] = dict(cursors.get("titles", {}))
    aggs: dict[str, _Agg] = {}
    touched_sessions: set[str] = set()

    if not base.is_dir():
        return [], cursors

    for path in sorted(base.glob("*/*.jsonl")):
        key = str(path)
        size = path.stat().st_size
        offset = offsets.get(key, 0)
        if size < offset:  # file was rewritten
            offset = 0
        if size == offset:
            continue
        if offset == 0 and datetime.fromtimestamp(path.stat().st_mtime, since.tzinfo) < since:
            offsets[key] = size  # old file, nothing in window
            continue

        with path.open("rb") as f:
            f.seek(offset)
            chunk = f.read()
        end = chunk.rfind(b"\n")
        if end < 0:
            continue
        offsets[key] = offset + end + 1

        for raw in chunk[: end + 1].splitlines():
            try:
                rec = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            rtype = rec.get("type")
            sid = rec.get("sessionId")
            if not sid:
                continue
            if rtype == "custom-title" and rec.get("customTitle"):
                titles[sid] = str(rec["customTitle"])[:200]
                touched_sessions.add(sid)
                continue
            if rtype not in ("user", "assistant"):
                continue
            if rec.get("isSidechain") and not include_side:
                continue
            ts = rec.get("timestamp")
            if not ts:
                continue
            when = _parse_ts(ts)
            if when < since:
                continue
            day = when.astimezone(tz).date().isoformat()
            eid = f"{prefix}:{sid}:{day}"
            agg = aggs.get(eid)
            if agg is None:
                prev = existing.get(eid)
                agg = aggs[eid] = _Agg(prev.details if prev else None)
            agg.session_id = sid
            agg.cwd = rec.get("cwd") or agg.cwd
            agg.branch = rec.get("gitBranch") or agg.branch
            agg.touch(when.isoformat())
            touched_sessions.add(sid)
            msg = rec.get("message") or {}

            if rtype == "user":
                if (
                    rec.get("isMeta")
                    or rec.get("isCompactSummary")
                    or rec.get("isVisibleInTranscriptOnly")
                ):
                    continue
                for t in _user_texts(msg):
                    if p := clean_prompt(t):
                        agg.prompts.append(p)
            else:
                agg.assistant_turns += 1
                for item in msg.get("content") or []:
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        name = str(item.get("name", "?"))
                        if name.startswith("mcp__") and name.count("__") >= 2:
                            name = "mcp:" + name.split("__")[1]
                        agg.tools[name] += 1
                        fp = (item.get("input") or {}).get("file_path")
                        if name in EDIT_TOOLS and isinstance(fp, str):
                            bn = Path(fp.replace("\\", "/")).name
                            if bn and bn not in agg.files:
                                agg.files.append(bn)

    # Re-title stored events whose session got a (new) title without new messages.
    for eid, prev in existing.items():
        if prev.source == source and eid not in aggs:
            sid = prev.details.get("session_id")
            if sid in touched_sessions and sid in titles:
                aggs[eid] = _Agg(prev.details)

    events: list[WorkEvent] = []
    for eid, agg in aggs.items():
        if agg.assistant_turns == 0 and not agg.prompts:
            continue
        project = project_name(cfg, agg.cwd)
        title = titles.get(agg.session_id or "") or (agg.prompts[0][:100] if agg.prompts else "")
        ev = WorkEvent(
            id=eid,
            source=source,
            timestamp=_parse_ts(agg.last or agg.first or since.isoformat()),
            project=project or "(excluded)",
            title=title or "Claude Code session",
            details=agg.details(),
            tags=["claude-code"],
        )
        if project is None:
            ev.sensitivity = "drop"
            ev.drop_reasons = ["project_excluded"]
        events.append(ev)

    return events, {"offsets": offsets, "titles": titles}
