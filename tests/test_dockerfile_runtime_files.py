"""The pipeline image must contain, and the startup check must cover, every curated lookup table
the code reads at runtime.

Until 2026-11-06 the Dockerfile copied neither config/ nor data/, so in ECS every loader of
config/*.yaml and data/*.yaml silently fell back to defaults (the deployed 2026-11-03 image loaded
0 of 97 equipment aliases and 0 of 6 surname-frequency entries) and nothing logged it. These
tests close both halves: the image ships the tables, and an omission is observed at startup.
"""

import logging
import re
from pathlib import Path

import pytest

from src.utils.runtime_tables import (
    OPTIONAL_RUNTIME_TABLES,
    REQUIRED_RUNTIME_TABLES,
    check_runtime_tables,
)

REPO = Path(__file__).resolve().parents[1]
# scripts/ is deliberately NOT scanned: it ships in the image only for operator CLIs (its
# config/profiles and people_group_aliases readers are not on the ECS run path). If a pipeline path
# starts reading a table from scripts/, add it here.
PIPELINE_CODE = ["src", "ecs_modules", "ecs_entrypoint.py"] + [
    p.name for p in REPO.glob("phase*.py")
]
RUNTIME_DATA_DIRS = ("config", "data")
# "config/x.yaml" literals, and Path(...) / "config" / "x.yaml" path-part forms.
_LITERAL = re.compile(r"""["']((?:config|data)/[\w.-]+\.ya?ml)["']""")
_PARTS = re.compile(r"""["'](config|data)["']\s*/\s*["']([\w.-]+\.ya?ml)["']""")


def _referenced_tables() -> set:
    found = set()
    for entry in PIPELINE_CODE:
        root = REPO / entry
        for f in [root] if root.is_file() else root.rglob("*.py"):
            text = f.read_text(encoding="utf-8", errors="ignore")
            found.update(_LITERAL.findall(text))
            found.update(f"{d}/{n}" for d, n in _PARTS.findall(text))
    return found


def _copied_dirs() -> set:
    copied = set()
    for line in (REPO / "Dockerfile").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*COPY\s+(?!--from)(.+)$", line)
        if m:
            for src in m.group(1).split()[:-1]:
                copied.add(src.rstrip("/").split("/")[0])
    return copied


def test_scan_is_not_vacuous():
    assert "config/equipment_aliases.yaml" in _referenced_tables()
    assert "data/surname_frequency.yaml" in _referenced_tables()


def test_every_referenced_table_is_registered():
    registered = set(REQUIRED_RUNTIME_TABLES) | set(OPTIONAL_RUNTIME_TABLES)
    missing = _referenced_tables() - registered
    assert (
        not missing
    ), f"add to src/utils/runtime_tables.REQUIRED_RUNTIME_TABLES: {sorted(missing)}"


def test_dockerfile_copies_every_runtime_table_dir():
    dirs = {t.split("/")[0] for t in REQUIRED_RUNTIME_TABLES}
    assert dirs == set(RUNTIME_DATA_DIRS)
    missing = dirs - _copied_dirs()
    assert (
        not missing
    ), f"Dockerfile does not COPY runtime lookup dirs: {sorted(missing)}"


def test_runtime_dirs_not_dockerignored():
    ignored = {
        ln.strip().rstrip("/")
        for ln in (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    }
    assert not (set(RUNTIME_DATA_DIRS) & ignored)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)


def test_repo_tables_all_usable(caplog):
    caplog.set_level(logging.INFO)
    assert check_runtime_tables(REPO) == []
    assert any("OBSERV runtime.tables status=ok" in r.message for r in caplog.records)


def test_omission_is_observed(tmp_path, caplog):
    """The 2026-11-06 failure: an image with no config/ or data/ must be loud, not silent."""
    caplog.set_level(logging.INFO)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "equipment_aliases.yaml").write_text("aliases: {}\n")
    bad = check_runtime_tables(tmp_path)
    assert len(bad) == len(REQUIRED_RUNTIME_TABLES)
    assert "config/equipment_aliases.yaml=empty_key:aliases" in bad
    assert "data/surname_frequency.yaml=missing" in bad
    errors = [
        r.message
        for r in caplog.records
        if "OBSERV runtime.tables status=error" in r.message
    ]
    # Exactly ONE escalated line (Slack is rate-limited per scope.event) naming every bad table.
    assert len(errors) == 1
    for rel in REQUIRED_RUNTIME_TABLES:
        assert Path(rel).stem in errors[0], rel
    assert "..." not in errors[0].split(" — ")[0], "escalated table list was truncated"
    warns = [
        r.message
        for r in caplog.records
        if "OBSERV runtime.table_unusable status=warn" in r.message
    ]
    assert len(warns) == len(REQUIRED_RUNTIME_TABLES)


def test_empty_required_key_is_unusable(tmp_path):
    """A table that parses but has an empty required key (e.g. nicknames: {}) is not usable."""
    for rel, key in REQUIRED_RUNTIME_TABLES.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{key}: {{}}\nother: 1\n", encoding="utf-8")
    bad = check_runtime_tables(tmp_path)
    assert sorted(bad) == sorted(
        f"{rel}=empty_key:{key}" for rel, key in REQUIRED_RUNTIME_TABLES.items()
    )
