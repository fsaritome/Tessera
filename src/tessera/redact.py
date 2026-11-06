"""Rule-based redaction (config/redact/). Rules always win over anything an LLM says.

Order per text:
  1. drop rules   -> any hit drops the whole event (credentials etc.)
  2. pseudonyms   -> readable stand-ins (web01 -> WEB-A), simple line format
                     (`old==>new`, `regex:pattern==>new`)
  3. term lists   -> clients, colleagues, hosts, internal terms
  4. replace rules in file order
Final gate (leftovers): all of the above must be a no-op on the output, and no pattern
from the forbidden lists for that channel level may match.

Reports only rule ids and counts, never the matched text, so logs stay clean.
"""

from __future__ import annotations

import copy
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import WorkEvent

INTERNAL = "internal"
PUBLIC = "public"
LEVELS = {"internal": INTERNAL, "public": PUBLIC}


@dataclass
class Rule:
    id: str
    regex: re.Pattern[str]
    token: str = ""
    channels: list[str] = field(default_factory=lambda: [INTERNAL, PUBLIC])
    examples: dict[str, list[str]] = field(default_factory=dict)


def _read_terms(path: Path) -> list[str]:
    if not path.is_file():
        return []
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            terms.append(line)
    return terms


Pseudonym = tuple[str | re.Pattern[str], str]


def read_pseudonyms(path: Path) -> list[Pseudonym]:
    """`literal==>new` / `regex:pattern==>new` per line; other lines are ignored."""
    out: list[Pseudonym] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "==>" not in line:
            continue
        if line.startswith("regex:"):
            pat, new = line[len("regex:") :].split("==>", 1)
            out.append((re.compile(pat), new))
        else:
            old, new = line.split("==>", 1)
            out.append((old, new))
    return out


def read_forbidden(path: Path) -> list[re.Pattern[str]]:
    """One case-insensitive regex per line; `#` starts a comment."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(re.compile(line, re.I))
    return out


class Redactor:
    def __init__(
        self,
        drop: list[Rule],
        replace: list[Rule],
        terms: dict[str, str] | None = None,
        pseudonyms: list[Pseudonym] | None = None,
        forbidden: dict[str, list[re.Pattern[str]]] | None = None,
    ):
        self.drop = drop
        self.replace = replace
        self.terms = terms or {}  # lowercased term -> token
        self.pseudonyms = pseudonyms or []
        self.forbidden = forbidden or {}  # level -> patterns
        self._term_re = None
        if self.terms:
            alts = sorted(self.terms, key=len, reverse=True)
            self._term_re = re.compile(
                r"(?<!\w)(?:" + "|".join(re.escape(t) for t in alts) + r")(?!\w)", re.I
            )

    # --- construction ---------------------------------------------------------
    @classmethod
    def for_config(cls, cfg: Any) -> Redactor:
        """Build from a Config: rules.yaml + settings (domains, pseudonym and forbidden files)."""

        def paths(key: str) -> list[Path]:
            out = []
            for p in cfg.get(key) or []:
                path = Path(p).expanduser()
                path = path if path.is_absolute() else cfg.root / path
                if path.is_file():
                    out.append(path)
                elif not str(p).startswith("config/redact/"):  # defaults are optional
                    print(f"warn: redaction file not found, skipped: {path.name}", file=sys.stderr)
            return out

        return cls.from_config(
            cfg.redact_dir,
            cfg.get("internal_domains"),
            pseudonym_files=paths("redact.pseudonym_files"),
            forbidden_files={
                INTERNAL: paths("redact.forbidden.internal"),
                PUBLIC: paths("redact.forbidden.public"),
            },
            terms_dir=cfg.terms_dir,
            extra_rule_files=paths("redact.extra_rule_files"),
        )

    @classmethod
    def from_config(
        cls,
        redact_dir: Path,
        internal_domains: list[str] | None = None,
        pseudonym_files: list[Path] | None = None,
        forbidden_files: dict[str, list[Path]] | None = None,
        terms_dir: Path | None = None,
        extra_rule_files: list[Path] | None = None,
    ) -> Redactor:
        spec = yaml.safe_load((redact_dir / "rules.yaml").read_text(encoding="utf-8"))
        terms_dir = terms_dir or redact_dir / "terms"
        for extra in extra_rule_files or []:  # the user's own rules, kept with their data
            more = yaml.safe_load(extra.read_text(encoding="utf-8")) or {}
            for section in ("drop", "replace"):
                spec[section] = spec.get(section, []) + (more.get(section) or [])
            spec["terms"] = {**(spec.get("terms") or {}), **(more.get("terms") or {})}

        def mk(r: dict[str, Any]) -> Rule:
            return Rule(
                id=r["id"],
                regex=re.compile(r["pattern"]),
                token=r.get("token", ""),
                channels=r.get("channels", [INTERNAL, PUBLIC]),
                examples=r.get("examples", {}),
            )

        drop = [mk(r) for r in spec.get("drop", [])]
        replace = [mk(r) for r in spec.get("replace", [])]

        if internal_domains:
            alts = "|".join(re.escape(d.lstrip(".")) for d in internal_domains)
            dom = Rule(
                id="internal_domain",
                regex=re.compile(rf"(?i)\b(?:[\w-]+\.)*(?:{alts})\b"),
                token="[Host]",
            )
            # after email, so addresses become [Email] rather than name@[Host]
            idx = next((i for i, r in enumerate(replace) if r.id == "email"), -1) + 1
            replace.insert(idx, dom)

        whitelist = {t.lower() for t in _read_terms(terms_dir / spec.get("whitelist", ""))}
        terms: dict[str, str] = {}
        for fname, token in (spec.get("terms") or {}).items():
            for t in _read_terms(terms_dir / fname):
                if t.lower() not in whitelist:
                    terms[t.lower()] = token
        pseudonyms = [p for f in pseudonym_files or [] for p in read_pseudonyms(f)]
        forbidden = {
            level: [rx for f in files for rx in read_forbidden(f)]
            for level, files in (forbidden_files or {}).items()
        }
        # public is stricter: it also gets everything forbidden internally
        if INTERNAL in forbidden:
            forbidden[PUBLIC] = forbidden.get(PUBLIC, []) + forbidden[INTERNAL]
        return cls(drop, replace, terms, pseudonyms, forbidden)

    def _apply_pseudonyms(self, text: str) -> tuple[str, int]:
        n = 0
        for old, new in self.pseudonyms:
            if isinstance(old, str):
                c = text.count(old)
                if c:
                    text, n = text.replace(old, new), n + c
            else:
                text, c = old.subn(new, text)
                n += c
        return text, n

    # --- text API -------------------------------------------------------------
    def drop_hits(self, text: str) -> list[str]:
        return [r.id for r in self.drop if r.regex.search(text)]

    def redact_text(self, text: str, level: str = INTERNAL) -> tuple[str, Counter[str]]:
        hits: Counter[str] = Counter()
        if self.pseudonyms:
            text, n = self._apply_pseudonyms(text)
            if n:
                hits["pseudonym"] += n
        if self._term_re is not None:

            def term_sub(m: re.Match[str]) -> str:
                tok = self.terms[m.group(0).lower()]
                hits[f"term:{tok}"] += 1
                return tok

            text = self._term_re.sub(term_sub, text)
        for r in self.replace:
            if level not in r.channels:
                continue
            text, n = r.regex.subn(r.token, text)
            if n:
                hits[r.id] += n
        return text, hits

    def leftovers(self, text: str, level: str = INTERNAL) -> list[str]:
        """Final gate: rule ids that would still fire on already-redacted text."""
        issues = self.drop_hits(text)
        if self.pseudonyms and self._apply_pseudonyms(text)[0] != text:
            issues.append("pseudonym")
        if any(rx.search(text) for rx in self.forbidden.get(level, [])):
            issues.append("forbidden")
        if self._term_re is not None and self._term_re.search(text):
            issues.append("term")
        for r in self.replace:
            if level in r.channels and r.regex.sub(r.token, text) != text:
                issues.append(r.id)
        return issues

    # --- event API ------------------------------------------------------------
    def redact_event(self, ev: WorkEvent, level: str = INTERNAL) -> tuple[WorkEvent, Counter[str]]:
        """Return a redacted copy. Credential hits mark the copy as sensitivity=drop."""
        out = copy.deepcopy(ev)
        hits: Counter[str] = Counter()
        drops: set[str] = set()

        def walk(v: Any) -> Any:
            if isinstance(v, str):
                drops.update(self.drop_hits(v))
                red, h = self.redact_text(v, level)
                hits.update(h)
                return red
            if isinstance(v, list):
                return [walk(x) for x in v]
            if isinstance(v, dict):
                return {k: walk(x) for k, x in v.items()}
            return v

        out.title = walk(out.title)
        out.summary = walk(out.summary)
        out.details = walk(out.details)
        out.tags = walk(out.tags)
        out.project = walk(out.project)
        if drops:
            out.sensitivity = "drop"
            out.drop_reasons = sorted(set(out.drop_reasons) | drops)
            # never carry the offending content further, even in memory
            out.title, out.summary, out.details = "[dropped]", "", {}
        return out, hits
