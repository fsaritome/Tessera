import json
import os
import zipfile
from datetime import UTC, datetime
from types import SimpleNamespace

from tessera import extract
from tessera.adapters import claude_export, cowork
from tessera.models import WorkEvent
from tessera.store import Store

SINCE = datetime(2026, 1, 1, tzinfo=UTC)


def _line(**kw):
    base = {"isSidechain": False, "cwd": "/sessions/vm-name", "timestamp": "2026-08-10T09:00:00Z"}
    base.update(kw)
    return json.dumps(base) + "\n"


def _cowork_session(root, sid, title, cli, prompt, ts="2026-08-10T09:00:00Z"):
    (root / f"{sid}.json").write_text(
        json.dumps({"sessionId": sid, "title": title, "createdAt": 1786000000000}),
        encoding="utf-8",
    )
    t = root / sid / ".claude" / "projects" / "-sessions-vm" / f"{cli}.jsonl"
    t.parent.mkdir(parents=True)
    t.write_text(
        _line(type="user", sessionId=cli, timestamp=ts, message={"role": "user", "content": prompt})
        + _line(type="assistant", sessionId=cli, timestamp=ts, message={"content": []}),
        encoding="utf-8",
    )


def test_cowork_reads_sessions_and_tags_recurring(cfg, tmp_path):
    root = tmp_path / "cw"
    root.mkdir()
    _cowork_session(root, "local_a", "Plan the vegetable beds", "c1", "How many tomato plants fit?")
    for i in range(6):
        _cowork_session(root, f"local_r{i}", "Download check", f"r{i}", "check it")
    evs, cur = cowork.ingest(cfg, {}, {}, SINCE, roots=[root])
    by_title = {}
    for e in evs:
        by_title.setdefault(e.title, []).append(e)
    a = by_title["Plan the vegetable beds"][0]
    assert a.source == "cowork" and a.id == "cw:c1:2026-08-10" and a.project == "Cowork"
    assert a.details["prompts"] == ["How many tomato plants fit?"]
    assert a.sensitivity == "internal" and "recurring" not in a.tags
    assert all("recurring" in e.tags for e in by_title["Download check"])
    again, _ = cowork.ingest(cfg, cur, {e.id: e for e in evs}, SINCE, roots=[root])
    assert again == []  # incremental


def _export(path, convs, mtime=None):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("conversations.json", json.dumps(convs))
        zf.writestr("users.json", "[]")
    if mtime:
        os.utime(path, (mtime, mtime))


CONV = {
    "uuid": "u1",
    "name": "Kitchen: compare ovens",
    "created_at": "2026-10-01T10:00:00Z",
    "updated_at": "2026-10-02T10:00:00Z",
    "chat_messages": [
        {"sender": "human", "content": [{"type": "text", "text": "Which oven fits?"}],
         "attachments": [{"file_name": "C:/x/angebot.pdf"}]},
        {"sender": "assistant", "content": [{"type": "text", "text": "never stored"}]},
    ],
}  # fmt: skip


def test_claude_export_finds_newest_zip_and_is_incremental(cfg, tmp_path):
    dl = tmp_path / "Downloads"
    dl.mkdir()
    (dl / "random.zip").write_bytes(b"not a zip")
    _export(dl / "data-old.zip", [dict(CONV, uuid="old")], mtime=1_700_000_000)
    _export(dl / "data-new.zip", [CONV, dict(CONV, uuid="u0", updated_at="2025-01-01T00:00:00Z")])
    evs, cur, z = claude_export.ingest(cfg, {}, SINCE, dirs=[dl])
    assert z.name == "data-new.zip"
    assert [e.id for e in evs] == ["ai:u1"]  # u0 is older than the window
    ev = evs[0]
    assert ev.title == "Kitchen: compare ovens" and ev.source == "claude_ai"
    assert ev.details["prompts"] == ["Which oven fits?"] and ev.details["files"] == [
        "angebot.pdf"
    ]
    assert "never stored" not in json.dumps(ev.to_dict())
    assert claude_export.ingest(cfg, cur, SINCE, dirs=[dl])[0] == []  # same zip again
    assert claude_export.ingest(cfg, {}, SINCE, dirs=[tmp_path / "empty"])[2] is None


def _ev(id, source, title, day, **details):
    d = {"session_id": details.pop("sid", id), "prompts": ["p"], "prompt_count": 1, **details}
    return WorkEvent(id=id, source=source, timestamp=datetime(2026, 11, day, 9, tzinfo=UTC),
                     project=details.get("project", "GardenPlanner"), title=title,
                     details=d)  # fmt: skip


def test_build_units_groups_redacts_and_generalizes(cfg):
    evs = [
        _ev("cc:s1:a", "claude_code", "Sync on srv01", 2, sid="s1"),
        _ev("cc:s1:b", "claude_code", "Sync on srv01", 3, sid="s1"),
        _ev("git:R:1", "git", "fix a", 2, repo="R", insertions=1, deletions=0),
        _ev("git:R:2", "git", "fix b", 3, repo="R", insertions=2, deletions=1),
        _ev("cw:x:1", "cowork", "Mail for Example Widgets GmbH", 4),
        _ev("cc:k:1", "claude_code", "key", 4, prompts=["password=Spring2026!x"]),
    ]
    for i in range(3):
        e = _ev(f"cw:r{i}:1", "cowork", "Download check", 2 + i)
        e.tags = ["cowork", "recurring"]
        evs.append(e)
    units, stats = extract.build_units(cfg, evs)
    by = {u.id: u for u in units}
    assert by["claude_code:s1"].event_ids == ["cc:s1:a", "cc:s1:b"]
    assert "srv01" not in by["claude_code:s1"].text and "[Host]" in by["claude_code:s1"].text
    git_unit = next(u for u in units if u.source == "git")
    assert git_unit.event_ids == ["git:R:1", "git:R:2"]  # same ISO week
    gen = by["cowork:cw:x:1"]
    assert gen.generalized and "Example Widgets" not in gen.text
    rec = by["recurring:Download check"]
    assert rec.event_ids == ["cw:r2:1"]  # latest run only
    assert stats["dropped"] == 1 and stats["recurring_collapsed"] == 1
    assert not any("Spring2026" in u.text for u in units)


class FakeClient:
    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        unit = kw["messages"][0]["content"]
        self.calls.append(unit)
        if self.fail_on and self.fail_on in unit:
            raise RuntimeError("boom")
        act = {"project": "GardenPlanner", "life_area": "software", "what": "w",
               "how": ["parallel Claude sessions"], "outcome": "", "lesson": "",
               "public_ok": True, "story": 2}  # fmt: skip
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps({"activities": [act]}))],
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        )


def test_run_caches_by_hash_and_survives_failures(cfg):
    now = datetime.now(UTC)
    a = WorkEvent(
        id="cc:s1:d",
        source="claude_code",
        timestamp=now,
        project="GardenPlanner",
        title="one",
        details={"session_id": "s1", "prompts": ["p1"], "prompt_count": 1},
    )
    b = WorkEvent(id="cc:s2:d", source="claude_code", timestamp=now, project="StarChart",
                  title="two FAILME",
                  details={"session_id": "s2", "prompts": ["p"], "prompt_count": 1})  # fmt: skip
    Store(cfg.state_dir).upsert([a, b])
    fake = FakeClient(fail_on="FAILME")
    res = extract.run(cfg, days=30, client=fake, workers=1)
    assert (res.extracted, len(res.failed), res.tokens_in) == (1, 1, 100)
    assert "KNOWN PROJECTS: (none yet)" in fake.calls[0]

    fake2 = FakeClient()
    res2 = extract.run(cfg, days=30, client=fake2, workers=1)
    assert (res2.cached, res2.extracted) == (1, 1)  # only the failed one is retried
    assert "KNOWN PROJECTS: GardenPlanner" in fake2.calls[0]

    a.details["prompts"].append("p2")  # unit changed -> extracted again
    Store(cfg.state_dir).upsert([a])
    assert extract.run(cfg, days=30, client=FakeClient(), workers=1).extracted == 1

    e = extract.essence(cfg)
    assert e["projects"]["GardenPlanner"]["n"] == 2
    assert e["shared_methods"] == {}  # only one project uses it so far
    assert extract.run(cfg, days=30, dry_run=True).units == 2
