"""Git: commit subjects/bodies and diff stats from configured repos.

Commits are immutable, so no cursor is needed: every run re-reads the window and
upserts by commit hash.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

from ..config import Config
from ..models import WorkEvent
from . import project_name

SOURCE = "git"
_RS, _US = "\x1e", "\x1f"
_FMT = f"{_RS}%H{_US}%aI{_US}%ae{_US}%D{_US}%s{_US}%b{_US}"


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or f"git {args[0]} failed")
    return res.stdout


def read_repo(cfg: Config, repo: Path, since: datetime, authors: list[str]) -> list[WorkEvent]:
    if not authors:
        try:
            email = _git(repo, "config", "user.email").strip()
        except RuntimeError:
            email = ""
        authors = [email] if email else []
    args = ["log", "--all", "--no-merges", f"--since={since.isoformat()}", f"--format={_FMT}"]
    args += ["--numstat"]
    args += [f"--author={a}" for a in authors]
    out = _git(repo, *args)

    project = project_name(cfg, str(repo))
    events = []
    for block in out.split(_RS)[1:]:
        parts = block.split(_US)
        if len(parts) < 7:
            continue
        sha, date, _email, refs, subject, body, numstat = parts[:7]
        ins = dels = files = 0
        for line in numstat.strip().splitlines():
            cols = line.split("\t")
            if len(cols) >= 3:
                files += 1
                ins += int(cols[0]) if cols[0].isdigit() else 0
                dels += int(cols[1]) if cols[1].isdigit() else 0
        ev = WorkEvent(
            id=f"git:{repo.name}:{sha[:12]}",
            source=SOURCE,
            timestamp=datetime.fromisoformat(date),
            project=project or "(excluded)",
            title=subject.strip(),
            details={
                "repo": repo.name,
                "sha": sha[:12],
                "body": body.strip()[:1000],
                "files_changed": files,
                "insertions": ins,
                "deletions": dels,
                "refs": refs.strip(),
            },
            tags=["git"],
        )
        if project is None:
            ev.sensitivity = "drop"
            ev.drop_reasons = ["project_excluded"]
        events.append(ev)
    return events


def ingest(cfg: Config, since: datetime) -> list[WorkEvent]:
    authors = list(cfg.get("sources.git.authors") or [])
    events: list[WorkEvent] = []
    for r in cfg.get("sources.git.repos") or []:
        repo = Path(r).expanduser()
        if not (repo / ".git").exists():
            print(f"warn: git repo not found, skipped: {repo.name}", file=sys.stderr)
            continue
        try:
            events += read_repo(cfg, repo, since, authors)
        except RuntimeError as e:
            print(f"warn: git log failed for {repo.name}: {e}", file=sys.stderr)
    return events
