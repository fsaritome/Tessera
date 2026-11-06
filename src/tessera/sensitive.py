"""Generalized variant for critical topics.

Some work must never be described concretely (client work, confidential projects), even
after redaction: rules can hide a name, but not what a document is about. Such events keep
only a fixed, abstract description plus counts. An event is generalized when
  * its project is listed in sensitive.projects (with its own description), or
  * its raw text matches one of sensitive.topics, or
  * redaction had to hide something from sensitive.trigger_rules (company names,
    client terms, your own extra rules ...): proof that it touched confidential work.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from .config import Config
from .models import WorkEvent

TAG = "generalized"
DEFAULT_LABEL = "Confidential work"


class Sensitivity:
    def __init__(self, cfg: Config):
        self.projects: dict[str, str] = dict(cfg.get("sensitive.projects") or {})
        self.topics = [re.compile(p) for p in cfg.get("sensitive.topics") or []]
        self.trigger_rules = set(cfg.get("sensitive.trigger_rules") or [])
        self.label = cfg.get("sensitive.label") or DEFAULT_LABEL

    def reason(self, raw: WorkEvent, hits: Counter[str]) -> str | None:
        """Why `raw` must be generalized (rule id / 'project' / 'topic'), or None."""
        if raw.project in self.projects:
            return "project"
        if self.topics:
            text = json.dumps([raw.title, raw.details], ensure_ascii=False)
            if any(rx.search(text) for rx in self.topics):
                return "topic"
        for rule in sorted(self.trigger_rules):
            if hits.get(rule):
                return f"rule:{rule}"
        return None

    def generalize(self, ev: WorkEvent, reason: str) -> WorkEvent:
        """In place: drop every concrete detail, keep project, time and counts."""
        d = ev.details
        ev.title = self.projects.get(ev.project, self.label)
        if ev.source == "claude_code":
            ev.details = {
                "session_id": d.get("session_id"),
                "prompt_count": d.get("prompt_count", 0),
                "file_count": len(d.get("files", [])),
            }
            ev.summary = f"{ev.details['prompt_count']} requests"
        else:
            ev.details = {k: d.get(k, 0) for k in ("files_changed", "insertions", "deletions")}
            ev.summary = f"{d.get('files_changed', 0)} files changed"
        ev.details["generalized"] = reason
        ev.kind = "task"
        if TAG not in ev.tags:
            ev.tags.append(TAG)
        return ev
