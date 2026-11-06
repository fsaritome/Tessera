"""Ingest adapters. One module per source; each returns a list of WorkEvents."""

from __future__ import annotations

import re
from pathlib import PurePath

from ..config import Config

_SCRATCH = re.compile(r"scratch-workspaces|scratch-\d{4}-\d{2}-\d{2}", re.I)


def project_name(cfg: Config, folder: str | None) -> str | None:
    """Display name for a working folder, or None if the project must be dropped."""
    if not folder:
        return cfg.get("projects.scratch_name", "Ad-hoc")
    if _SCRATCH.search(folder):
        base = cfg.get("projects.scratch_name", "Ad-hoc")
    else:
        base = PurePath(folder.replace("\\", "/")).name or folder
    if base in (cfg.get("projects.exclude") or []):
        return None
    aliases = cfg.get("projects.aliases") or {}
    if base in aliases:
        return aliases[base]
    if not cfg.get("projects.include_unknown", True) and base != cfg.get("projects.scratch_name"):
        return None
    return base
