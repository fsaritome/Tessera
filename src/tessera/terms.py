"""The terms a user agrees to before Tessera reads anything, and where that agreement is kept."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from .config import Config

TERMS = """\
Before you use Tessera, please read and agree:

- Tessera is free software under the MIT license, provided "as is", without warranty of
  any kind. You use it entirely at your own risk.
- It reads the Claude session files on this computer and lets your own Claude read
  redacted versions of them. Redaction catches patterns (keys, passwords, hosts, emails,
  paths and more), not everything: names or confidential details can remain.
- Reading uses your own Claude plan and its limits. You decide when it runs.
- You are responsible for what you run, for complying with your employer's and Anthropic's
  rules, and for anything you share, including screenshots of your map.
- The authors and contributors are not liable for any damage, data loss, costs, account
  restrictions or disclosure of information arising from its use.
"""


def _file(cfg: Config):
    return cfg.root / "config" / "accepted.json"


def accepted(cfg: Config) -> bool:
    try:
        return bool(json.loads(_file(cfg).read_text(encoding="utf-8")).get("accepted"))
    except (OSError, ValueError):
        return False


def accept(cfg: Config, version: str) -> None:
    f = _file(cfg)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"accepted": True, "version": version,
                             "at": datetime.now(UTC).isoformat(timespec="seconds")}),
                 encoding="utf-8")  # fmt: skip
