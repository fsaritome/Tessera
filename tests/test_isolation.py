"""Canary test: Tessera writes into its data folder and nowhere else.

A fake session carrying a unique marker goes through the whole pipeline, run from the
source folder itself. Afterwards the marker must be in the data folder, and the source
folder must be byte-for-byte unchanged and contain the marker nowhere.
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from tessera import config, handoff
from tessera.cli import main

REPO = Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", ".pytest_cache", ".ruff_cache", "__pycache__"}


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and not (set(p.relative_to(root).parts) & SKIP)
    }


def test_pipeline_writes_only_into_its_home(tmp_path, monkeypatch, capsys):
    canary = "CANARY" + uuid.uuid4().hex
    sid = str(uuid.uuid4())
    sessions = tmp_path / "claude" / "C--work-canaryproj"
    sessions.mkdir(parents=True)
    rec = {"sessionId": sid, "cwd": "/work/canaryproj", "isSidechain": False,
           "timestamp": datetime.now(UTC).isoformat()}  # fmt: skip
    (sessions / f"{sid}.jsonl").write_text(
        json.dumps(rec | {"type": "user", "message": {"content": f"please build {canary}"}})
        + "\n"
        + json.dumps(rec | {"type": "assistant", "message": {"content": []}})
        + "\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "settings.yaml").write_text(
        "sources: {cowork: {enabled: false}, claude_export: {enabled: false}, "
        "git: {enabled: false}}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TESSERA_HOME", str(home))
    monkeypatch.setenv("TESSERA_CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.chdir(REPO)  # run from inside the source folder on purpose
    before = _tree(REPO)

    assert main(["scan", "--days", "36500"]) == 0
    assert main(["prepare", "--days", "36500"]) == 0
    batch = next(handoff.workdir(config.load()).glob("batch-*.md"))
    act = {"project": f"proj {canary}", "life_area": "software", "what": f"built {canary}",
           "how": [f"method {canary}"], "outcome": "", "lesson": "", "public_ok": True,
           "story": 1}  # fmt: skip
    batch.with_name(batch.stem + ".answer.json").write_text(
        json.dumps({"units": [{"unit_id": f"claude_code:{sid}", "activities": [act]}]}),
        encoding="utf-8",
    )
    assert main(["import"]) == 0
    merge = handoff.prepare_merge(config.load())
    (merge.parent / "merge.answer.json").write_text(json.dumps({
        "projects": [{"name": f"proj {canary}", "area": "software", "kind": "private",
                      "summary": canary, "headline": "Canaries", "raw": [f"proj {canary}"]}],
        "methods": [{"name": f"m {canary}", "summary": canary, "items": [0]}],
        "headlines": [{"name": "Canaries", "summary": canary}],
    }), encoding="utf-8")  # fmt: skip
    assert main(["import-merge"]) == 0

    # the data is where it belongs ...
    in_home = [p for p in home.rglob("*") if p.is_file() and canary in p.read_text("utf-8")]
    assert {p.name for p in in_home} >= {"events.jsonl", "activities.jsonl", "consolidated.json"}
    # ... and the source folder is untouched and clean
    after = _tree(REPO)
    assert after.keys() == before.keys(), set(after) ^ set(before)
    assert after == before
    assert not [f for f, b in after.items() if canary.encode() in b]
