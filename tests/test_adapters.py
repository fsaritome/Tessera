import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from tessera.adapters import claude_code, git, project_name

SINCE = datetime(2026, 11, 1, tzinfo=UTC)
SID = "11111111-2222-3333-4444-555555555555"


def _rec(**kw):
    base = {
        "sessionId": SID,
        "cwd": "C:\\code\\GardenPlanner",
        "gitBranch": "main",
        "isSidechain": False,
        "timestamp": "2026-11-03T09:00:00.000Z",
    }
    base.update(kw)
    return json.dumps(base) + "\n"


def _write(path: Path, *lines: str, mode="w"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode, encoding="utf-8", newline="\n") as f:
        f.writelines(lines)


def test_claude_code_parses_and_is_incremental(cfg, tmp_path):
    base = tmp_path / "projects"
    f = base / "C--code-GardenPlanner" / f"{SID}.jsonl"
    _write(
        f,
        _rec(
            type="user",
            message={
                "role": "user",
                "content": "<system-reminder>ignore me</system-reminder>Fix the upload retry bug",
            },
        ),
        _rec(type="user", isMeta=True, message={"role": "user", "content": "meta stuff"}),
        _rec(
            type="assistant",
            timestamp="2026-11-03T09:01:00.000Z",
            message={
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "assistant text is never ingested"},
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": "C:\\code\\GardenPlanner\\src\\upload.py"},
                    },
                    {"type": "tool_use", "name": "mcp__Claude_Browser__navigate", "input": {}},
                ],
            },
        ),
        _rec(
            type="user",
            message={
                "role": "user",
                "content": [{"type": "tool_result", "content": "tool output never ingested"}],
            },
        ),
        _rec(type="user", isSidechain=True, message={"role": "user", "content": "subagent"}),
        _rec(
            type="user",
            timestamp="2026-10-20T09:00:00.000Z",
            message={"role": "user", "content": "too old"},
        ),
        '{"partial": ',  # incomplete trailing line is not consumed
    )
    events, cur = claude_code.ingest(cfg, {}, {}, SINCE, projects_dir=base)
    assert len(events) == 1
    ev = events[0]
    assert ev.id == f"cc:{SID}:2026-11-03"
    assert ev.project == "GardenPlanner"
    assert ev.details["prompts"] == ["Fix the upload retry bug"]
    assert ev.details["files"] == ["upload.py"]
    assert ev.details["tools"] == {"Edit": 1, "mcp:Claude_Browser": 1}
    assert "never ingested" not in json.dumps(ev.to_dict())
    assert ev.title == "Fix the upload retry bug"

    # second run: nothing new
    again, cur2 = claude_code.ingest(cfg, cur, {ev.id: ev}, SINCE, projects_dir=base)
    assert again == []

    # complete the partial line, add a title and a new prompt -> merged into same event
    _write(
        f,
        '"x": 1}\n',
        json.dumps({"type": "custom-title", "customTitle": "Upload-Retry fixen", "sessionId": SID})
        + "\n",
        _rec(
            type="user",
            timestamp="2026-11-03T15:00:00.000Z",
            message={"role": "user", "content": "now add tests"},
        ),
        mode="a",
    )
    merged, _ = claude_code.ingest(cfg, cur2, {ev.id: ev}, SINCE, projects_dir=base)
    assert len(merged) == 1
    assert merged[0].details["prompts"] == ["Fix the upload retry bug", "now add tests"]
    assert merged[0].title == "Upload-Retry fixen"


def test_project_name_rules(cfg):
    assert project_name(cfg, "C:\\code\\StarChart") == "StarChart"
    assert project_name(cfg, "C:\\x\\scratch-workspaces\\a\\scratch-2026-11-06-077e3b") == "Ad-hoc"
    cfg.settings["projects"]["exclude"] = ["StarChart"]
    cfg.settings["projects"]["aliases"] = {"GardenPlanner": "Garden-Planner"}
    assert project_name(cfg, "/c/code/StarChart") is None
    assert project_name(cfg, "/c/code/GardenPlanner") == "Garden-Planner"
    cfg.settings["projects"]["include_unknown"] = False
    assert project_name(cfg, "/c/code/Other") is None


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_git_adapter_reads_commits(cfg, tmp_path):
    repo = tmp_path / "DemoRepo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "me@example.test")
    _git(repo, "config", "user.name", "Me")
    (repo / "a.txt").write_text("one\ntwo\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "Add backup check\n\nlonger body")
    _git(repo, "config", "user.email", "other@example.test")
    (repo / "a.txt").write_text("one\n")
    _git(repo, "commit", "-q", "-am", "someone else's commit")

    evs = git.read_repo(cfg, repo, datetime(2000, 1, 1, tzinfo=UTC), ["me@example.test"])
    assert len(evs) == 1
    ev = evs[0]
    assert ev.title == "Add backup check"
    assert ev.project == "DemoRepo"
    assert ev.details["body"] == "longer body"
    assert (ev.details["files_changed"], ev.details["insertions"], ev.details["deletions"]) == (
        1,
        2,
        0,
    )
    assert ev.id.startswith("git:DemoRepo:")
