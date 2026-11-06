"""Every rule in config/redact/rules.yaml is tested via its own positive/negative examples."""

from datetime import UTC, datetime

import pytest
import yaml

from tessera.config import DEFAULTS
from tessera.models import WorkEvent
from tessera.redact import PUBLIC, Redactor

RULES_DIR = DEFAULTS / "redact"
SPEC = yaml.safe_load((RULES_DIR / "rules.yaml").read_text(encoding="utf-8"))
R = Redactor.from_config(RULES_DIR)  # real term lists are not loaded in CI if absent
R_NOTERMS = Redactor(R.drop, R.replace)

DROP = {r.id: r for r in R.drop}
REPL = {r.id: r for r in R.replace}


def _cases(section: str):
    for rule in SPEC[section]:
        ex = rule.get("examples") or {}
        for s in ex.get("match", []):
            yield pytest.param(rule["id"], s, True, id=f"{rule['id']}+{s[:25]}")
        for s in ex.get("no_match", []):
            yield pytest.param(rule["id"], s, False, id=f"{rule['id']}-{s[:25]}")


@pytest.mark.parametrize("section", ["drop", "replace"])
def test_every_rule_has_positive_and_negative_examples(section):
    for rule in SPEC[section]:
        ex = rule.get("examples") or {}
        assert ex.get("match"), f"{rule['id']}: missing examples.match"
        assert ex.get("no_match"), f"{rule['id']}: missing examples.no_match"


def test_rule_ids_unique():
    ids = [r["id"] for r in SPEC["drop"] + SPEC["replace"]]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(("rule_id", "text", "should_match"), list(_cases("drop")))
def test_drop_rule(rule_id, text, should_match):
    assert bool(DROP[rule_id].regex.search(text)) is should_match


@pytest.mark.parametrize(("rule_id", "text", "should_match"), list(_cases("replace")))
def test_replace_rule(rule_id, text, should_match):
    rule = REPL[rule_id]
    assert (rule.regex.sub(rule.token, text) != text) is should_match


@pytest.mark.parametrize(("rule_id", "text", "should_match"), list(_cases("replace")))
def test_redaction_is_idempotent_and_passes_gate(rule_id, text, should_match):
    once, _ = R_NOTERMS.redact_text(text)
    twice, _ = R_NOTERMS.redact_text(once)
    assert once == twice
    assert R_NOTERMS.leftovers(once) == []


def test_term_lists_and_whitelist(tmp_path):
    d = tmp_path / "redact"
    (d / "terms").mkdir(parents=True)
    (d / "rules.yaml").write_text(
        yaml.safe_dump(
            {
                "drop": [],
                "replace": [],
                "terms": {"clients.txt": "[Client]", "colleagues.txt": "[Person]"},
                "whitelist": "whitelist.txt",
            }
        ),
        encoding="utf-8",
    )
    (d / "terms" / "clients.txt").write_text(
        "# comment\nAcme Robotics GmbH\nAcme\n", encoding="utf-8"
    )
    (d / "terms" / "colleagues.txt").write_text("Jane Roe\nJohn Example\n", encoding="utf-8")
    (d / "terms" / "whitelist.txt").write_text("John Example\n", encoding="utf-8")
    r = Redactor.from_config(d)

    out, hits = r.redact_text("Call with acme robotics gmbh and Jane Roe; John Example attended.")
    assert out == "Call with [Client] and [Person]; John Example attended."
    assert hits["term:[Client]"] == 1
    # whole words only
    assert r.redact_text("Acmeville")[0] == "Acmeville"
    assert r.leftovers(out) == []
    assert r.leftovers("acme again") == ["term"]


def test_internal_domains_from_settings():
    r = Redactor.from_config(RULES_DIR, internal_domains=["example-corp.com"])
    out, _ = r.redact_text("login at portal.example-corp.com, mail it@example-corp.com")
    assert out == "login at [Host], mail [Email]"
    assert r.redact_text("example.org is public")[0] == "example.org is public"


def _ev(**kw) -> WorkEvent:
    base = dict(
        id="x", source="git", timestamp=datetime(2026, 11, 2, tzinfo=UTC), project="Proj", title="t"
    )
    base.update(kw)
    return WorkEvent(**base)


def test_event_with_credential_is_dropped_and_scrubbed():
    ev = _ev(title="rotate key", details={"body": "password=Hunter2024!"})
    red, _ = R_NOTERMS.redact_event(ev)
    assert red.sensitivity == "drop"
    assert "secret_assignment" in red.drop_reasons
    assert "Hunter" not in str(red.to_dict())
    assert ev.details["body"] == "password=Hunter2024!"  # original untouched


def test_event_fields_are_redacted_recursively():
    ev = _ev(
        title="Patch srv01 at 10.0.0.5",
        details={"files": ["C:\\Users\\jdoe\\x.txt"], "nested": {"m": "for Acme Inc."}},
    )
    red, hits = R_NOTERMS.redact_event(ev)
    assert red.title == "Patch [Host] at [IP]"
    assert red.details["files"] == ["C:\\Users\\<user>\\x.txt"]
    assert red.details["nested"]["m"] == "for [Company]"
    assert hits["ipv4"] == 1


def test_channel_scoped_rule():
    import re

    from tessera.redact import Rule

    r = Redactor([], [Rule("pub_only", re.compile("Acme"), "[Product]", channels=[PUBLIC])])
    assert r.redact_text("Acme job")[0] == "Acme job"
    assert r.redact_text("Acme job", PUBLIC)[0] == "[Product] job"
