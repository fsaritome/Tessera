"""`tessera suggest`: what still gets through redaction, so your lists can grow.

Scans the *redacted* text of every event in the window (title, prompts, commit body) for
things that look like names, hosts, domains or identifiers. Terms in your whitelist are
skipped, so marking something as fine once makes it disappear from later suggestions.
Prints to the terminal only; nothing is written.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta

from .config import Config
from .redact import INTERNAL, Redactor, _read_terms
from .sensitive import TAG, Sensitivity
from .store import Store

PATTERNS = {
    "Names (First Last?)": re.compile(r"\b[A-Z][a-z]{2,}\s[A-Z][a-z]{2,}\b"),
    "Hosts and ids (letters + digits)": re.compile(
        r"\b[A-Za-z]{2,}[-_]?[A-Za-z]*\d{1,4}[A-Za-z]?\b"
    ),
    "Domains": re.compile(r"\b(?:[\w-]+\.)+[a-z]{2,6}\b"),
    "Upper-case ids": re.compile(r"\b[A-Z]{2,}(?:[-_][A-Z0-9]{2,})+\b"),
}
# Common false positives: sentence starts, product and tool words.
NOISE = {
    w.lower()
    for w in [
        "The",
        "This",
        "That",
        "These",
        "Those",
        "New",
        "Open",
        "Run",
        "Press",
        "Please",
        "Thanks",
        "Hello",
        "Good",
        "Bad",
        "Request",
        "Claude",
        "Code",
        "Windows",
        "Linux",
        "Mac",
        "Microsoft",
        "Google",
        "Apple",
        "Domain",
        "Admin",
        "Task",
        "Manager",
        "utf-8",
        "sha-256",
        "utf-16",
        "ipv4",
        "ipv6",
        "x86",
        "x64",
        "arm64",
        "win32",
        "win64",
        "http2",
        "mp4",
        "mp3",
        "v1",
        "v2",
        "v3",
    ]
}
_VERSIONISH = re.compile(r"^(?:v?\d|[a-z]{1,3}\d$|py3)", re.I)
_CODE_CONST = re.compile(r"^[A-Z]{2,}_[A-Z0-9_]+$")  # LIKE_THIS: code constants
_FILES = re.compile(r"\.(?:py|md|json|yaml|yml|toml|txt|js|ts|html|css|rs|go|ps1|sh)$", re.I)


def candidates(cfg: Config, days: int) -> dict[str, Counter[str]]:
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    redactor = Redactor.for_config(cfg)
    sens = Sensitivity(cfg)
    ok = {t.lower() for t in _read_terms(cfg.terms_dir / "whitelist.txt")}
    ok |= {p.lower() for p in cfg.get("projects.aliases", {}) or {}}

    texts: list[str] = []
    for ev in Store(cfg.state_dir).window(start, end):
        if ev.sensitivity == "drop":
            continue
        red, hits = redactor.redact_event(ev, INTERNAL)
        if red.sensitivity == "drop":
            continue
        if reason := sens.reason(ev, hits):
            sens.generalize(red, reason)
        if TAG in red.tags:
            continue  # nothing concrete left to leak
        texts += [red.title, *red.details.get("prompts", []), red.details.get("body", "")]
    blob = "\n".join(t for t in texts if t)

    found: dict[str, Counter[str]] = {}
    for label, rx in PATTERNS.items():
        c: Counter[str] = Counter()
        for m in rx.findall(blob):
            low = m.lower()
            if low in ok or low in NOISE or any(w in NOISE for w in low.split()):
                continue
            if label.startswith("Hosts") and _VERSIONISH.match(m):
                continue
            if _CODE_CONST.match(m) or _FILES.search(m):
                continue
            c[m] += 1
        found[label] = c
    return found


def run(cfg: Config, days: int, top: int = 25) -> str:
    out = [f"Candidates from the last {days} days that still get through redaction:", ""]
    for label, c in candidates(cfg, days).items():
        out.append(f"## {label}")
        out.append(", ".join(f"{t} x{n}" for t, n in c.most_common(top)) or "- none -")
        out.append("")
    out += [
        "Where to put them:",
        "  hide       -> ~/.tessera/config/redact/terms/"
        "{clients,colleagues,hosts,internal_terms}.txt",
        "  stand-in   -> ~/.tessera/config/redact/pseudonyms.txt  (e.g. web01==>WEBSITE-A)",
        "  harmless   -> ~/.tessera/config/redact/terms/whitelist.txt",
    ]
    return "\n".join(out)
