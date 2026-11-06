from collections import Counter
from datetime import UTC, datetime

import pytest

from tessera import suggest
from tessera.config import DEFAULTS
from tessera.models import WorkEvent
from tessera.redact import INTERNAL, PUBLIC, Redactor
from tessera.sensitive import Sensitivity
from tessera.store import Store

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)

RULES_TXT = r"""# stand-ins: literal==>new and regex:pattern==>new
this line has no arrow and is ignored
regex:\bweb(\d)==>WEBSITE-\1
Springfield==>Site A
<item id===><doc id=
"""


def _cc(sid, title, prompts, project="P", day=4):
    return WorkEvent(
        id=f"cc:{sid}:2026-11-0{day}",
        source="claude_code",
        timestamp=datetime(2026, 11, day, 9, tzinfo=UTC),
        project=project,
        title=title,
        details={
            "session_id": sid,
            "prompts": prompts,
            "prompt_count": len(prompts),
            "files": ["a.py"],
        },
    )


@pytest.fixture
def red_dir(tmp_root):
    d = tmp_root / "config" / "redact"
    (d / "pseudonyms.txt").write_text(RULES_TXT, encoding="utf-8")
    (d / "forbidden.txt").write_text("# c\nexamplecorp\n", encoding="utf-8")
    (d / "public_forbidden.txt").write_text("secretword\n", encoding="utf-8")
    return d


def test_pseudonyms_line_format(red_dir):
    r = Redactor.from_config(DEFAULTS / "redact", pseudonym_files=[red_dir / "pseudonyms.txt"])
    out, hits = r.redact_text("copy web1 to web2 in Springfield; <item id=1>")
    assert out == "copy WEBSITE-1 to WEBSITE-2 in Site A; <doc id=1>"
    assert hits["pseudonym"] == 4
    assert r.leftovers(out) == []
    assert r.leftovers("web3 left") != []


def test_forbidden_lists_per_level(red_dir):
    r = Redactor.from_config(
        DEFAULTS / "redact",
        forbidden_files={
            INTERNAL: [red_dir / "forbidden.txt"],
            PUBLIC: [red_dir / "public_forbidden.txt"],
        },
    )
    assert r.leftovers("a secretword draft", INTERNAL) == []
    assert "forbidden" in r.leftovers("a secretword draft", PUBLIC)
    assert "forbidden" in r.leftovers("Examplecorp news", PUBLIC)  # public inherits internal
    assert "forbidden" in r.leftovers("Examplecorp news", INTERNAL)


def test_sensitivity_reasons(cfg):
    cfg.settings["sensitive"]["projects"] = {"Client X": "Internal tool for a client"}
    s = Sensitivity(cfg)
    assert s.reason(_cc("a", "x", [], project="Client X"), Counter()) == "project"
    assert s.reason(_cc("b", "Project Falcon kickoff", []), Counter()) == "topic"
    assert s.reason(_cc("c", "x", []), Counter({"company": 1})) == "rule:company"
    assert s.reason(_cc("d", "fix the build", ["client.rs"]), Counter({"ipv4": 2})) is None

    ev = s.generalize(
        _cc("a", "Example Widgets claims", ["secret text"], project="Client X"),
        "project",
    )
    assert ev.title == "Internal tool for a client"
    assert "secret" not in str(ev.to_dict()) and "Example Widgets" not in str(ev.to_dict())
    assert ev.details["prompt_count"] == 1


def test_suggest_lists_leftovers_but_not_whitelisted(cfg, monkeypatch):
    import tessera.suggest as sg

    monkeypatch.setattr(sg, "datetime", type("D", (), {"now": staticmethod(lambda tz: NOW)}))
    (cfg.config_dir / "redact" / "terms" / "whitelist.txt").write_text(
        "Ann Example\n", encoding="utf-8"
    )
    Store(cfg.state_dir).upsert(
        [
            _cc("a", "Mail to Carl Coder", ["deploy to bigbox7, ask Ann Example"]),
            _cc(
                "b", "Notes for Example Widgets GmbH", ["Ernie Secret"]
            ),  # generalized -> not scanned
        ]
    )
    out = suggest.run(cfg, days=7)
    assert "Carl Coder x1" in out
    assert "bigbox7 x1" in out
    assert "Ann Example" not in out
    assert "Ernie" not in out
