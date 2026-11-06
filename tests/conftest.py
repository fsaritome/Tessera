import shutil
from pathlib import Path

import pytest

from tessera import config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """A throwaway project root with the real rules but empty term lists."""
    shutil.copytree(
        ROOT / "config", tmp_path / "config", ignore=shutil.ignore_patterns("*.local.yaml")
    )
    for f in (tmp_path / "config" / "redact" / "terms").glob("*.txt"):
        if not f.name.endswith(".example.txt"):
            f.unlink()
    # Test profile: confidential client work as an example of sensitive topics.
    (tmp_path / "config" / "settings.yaml").write_text(TEST_SETTINGS, encoding="utf-8")
    return tmp_path


TEST_SETTINGS = r"""
timezone: UTC
sensitive:
  topics:
    - '(?i)\b(project falcon|merger|acquisition)\b'
  trigger_rules: [company, "term:[Client]"]
  label: Confidential work
"""


@pytest.fixture
def cfg(tmp_root: Path) -> config.Config:
    return config.load(tmp_root)
