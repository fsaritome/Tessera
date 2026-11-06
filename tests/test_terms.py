from tessera import cli, terms


def test_terms_must_be_accepted_explicitly(cfg, monkeypatch, capsys):
    monkeypatch.setenv("TESSERA_HOME", str(cfg.root))
    assert not terms.accepted(cfg)
    assert cli.main(["accept"]) == 1  # shows the terms, records nothing
    assert "at your own risk" in capsys.readouterr().out
    assert not terms.accepted(cfg)
    assert cli.main(["accept", "--yes"]) == 0
    assert terms.accepted(cfg)
