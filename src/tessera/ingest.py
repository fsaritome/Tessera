"""Scan all sources into the local event store (<home>/state/events.jsonl)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .adapters import claude_code, claude_export, cowork, git
from .config import Config
from .store import Store


def window(days: int) -> tuple[datetime, datetime]:
    end = datetime.now(UTC)
    return end - timedelta(days=days), end


def run_ingest(cfg: Config, days: int, full: bool = False) -> dict[str, int]:
    """Returns new/updated events per source. Sources that found nothing report 0."""
    store = Store(cfg.state_dir)
    if full:
        store.reset()
    start, _ = window(days)
    counts: dict[str, int] = {}
    if cfg.get("sources.claude_code.enabled", True):
        evs, cur = claude_code.ingest(cfg, store.cursors("claude_code"), store.load_events(), start)
        counts["claude_code"] = store.upsert(evs)
        store.save_cursors("claude_code", cur)
    if cfg.get("sources.cowork.enabled", True):
        evs, cur = cowork.ingest(cfg, store.cursors("cowork"), store.load_events(), start)
        counts["cowork"] = store.upsert(evs)
        store.save_cursors("cowork", cur)
    if cfg.get("sources.claude_export.enabled", True):
        evs, cur, z = claude_export.ingest(cfg, store.cursors("claude_export"), start)
        counts["claude_ai" if z else "claude_ai (no export zip found)"] = store.upsert(evs)
        store.save_cursors("claude_export", cur)
    if cfg.get("sources.git.enabled", True):
        counts["git"] = store.upsert(git.ingest(cfg, start))
    return counts
