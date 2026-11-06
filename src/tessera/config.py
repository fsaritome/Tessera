"""Settings: built-in defaults, overridden by the user's own files.

Where Tessera keeps its data (the "home"): $TESSERA_HOME if set, else ~/.tessera.
Tessera never writes into its own source folder; tests/test_isolation.py checks that.

Settings = package defaults (tessera/defaults/settings.yaml)
         <- <home>/config/settings.yaml        (optional)
         <- <home>/config/settings.local.yaml  (optional, never committed)
Redaction rules come from <home>/config/redact/rules.yaml if present, else the defaults.
Term lists (clients, colleagues, hosts ...) always live in <home>/config/redact/terms/.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

DEFAULTS = Path(__file__).parent / "defaults"


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def find_root() -> Path:
    """Tessera's data folder. Never the source checkout: code and data stay apart."""
    if env := os.environ.get("TESSERA_HOME"):
        return Path(env).expanduser().resolve()
    return Path.home() / ".tessera"


@dataclass
class Config:
    root: Path
    settings: dict[str, Any]

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def redact_dir(self) -> Path:
        """Folder with rules.yaml; the user's own if they made one, else the defaults."""
        own = self.config_dir / "redact"
        return own if (own / "rules.yaml").is_file() else DEFAULTS / "redact"

    @property
    def terms_dir(self) -> Path:
        return self.config_dir / "redact" / "terms"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def out_dir(self) -> Path:
        return self.root / "out"

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.settings
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur


def tz(cfg: Config) -> tzinfo:
    """Configured IANA time zone, or this computer's local zone."""
    if name := cfg.get("timezone"):
        return ZoneInfo(name)
    return datetime.now().astimezone().tzinfo  # type: ignore[return-value]


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load(root: Path | None = None) -> Config:
    root = root or find_root()
    settings = _read(DEFAULTS / "settings.yaml")
    for name in ("settings.yaml", "settings.local.yaml"):
        settings = _deep_merge(settings, _read(root / "config" / name))
    return Config(root=root, settings=settings)
