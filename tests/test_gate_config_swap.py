"""Guard: the gate's config.yaml swap always puts the local config back (task 0.4, 2026-11-18).

config.yaml is gitignored and is what deploys ship; the gate swaps in config.yaml.example for the
tests. The real block between `>>> gate-config-swap` and `<<< gate-config-swap` in scripts/gate.sh
runs here in a scratch directory: normal restore, restore on SIGTERM, recovery after a SIGKILL'd
gate, one gate at a time, a private backup.
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE = (ROOT / "scripts" / "gate.sh").read_text(encoding="utf-8")
REAL = "batch:\n  phase3: false\nmine: true\n"
EXAMPLE = "batch:\n  phase3: false\n"


def _block() -> str:
    return GATE[
        GATE.index("# >>> gate-config-swap") : GATE.index("# <<< gate-config-swap")
    ]


@pytest.fixture
def work(tmp_path):
    (tmp_path / "tmp").mkdir()
    (tmp_path / "config.yaml").write_text(REAL)
    (tmp_path / "config.yaml.example").write_text(EXAMPLE)
    return tmp_path


def _run(work: Path, body: str):
    script = f"fail=0\n{_block()}\n{body}\necho FAIL=$fail\n"
    return subprocess.run(
        ["bash", "-c", script], cwd=work, capture_output=True, text=True, check=False
    )


def _backups(work: Path):
    return sorted(p.name for p in (work / "tmp").glob(".gate_config.??????"))


def test_swap_then_restore_puts_the_config_back(work):
    r = _run(
        work,
        "gate_swap_config\ncat config.yaml > tmp/during\n"
        'stat -c %a "$GATE_CFG_BACKUP" > tmp/mode\ngate_restore_config',
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert (work / "tmp" / "during").read_text() == EXAMPLE
    assert (work / "tmp" / "mode").read_text().strip() == "600"
    assert (work / "config.yaml").read_text() == REAL
    assert _backups(work) == []
    assert "FAIL=0" in r.stdout


def test_a_script_that_exits_early_still_restores(work):
    r = _run(work, "gate_swap_config\nexit 3")
    assert r.returncode == 3
    assert (work / "config.yaml").read_text() == REAL
    assert _backups(work) == []


def test_sigterm_restores_and_stops(work):
    r = _run(work, "gate_swap_config\nkill -TERM $$\nsleep 5\necho NOT-REACHED")
    assert r.returncode == 130
    assert "NOT-REACHED" not in r.stdout
    assert (work / "config.yaml").read_text() == REAL


def test_a_backup_left_by_a_killed_gate_is_restored_first(work):
    # a SIGKILL'd gate: the example is in place and the real config is in the backup
    (work / "tmp" / ".gate_config.abc123").write_text(REAL)
    (work / "config.yaml").write_text(EXAMPLE)
    r = _run(work, "gate_swap_config\ngate_restore_config")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "restoring config.yaml from tmp/.gate_config.abc123" in r.stdout
    assert (work / "config.yaml").read_text() == REAL
    assert _backups(work) == []


def test_only_one_gate_swaps_at_a_time(work):
    import fcntl

    with open(work / "tmp" / ".gate_config.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)  # another gate holds it
        r = _run(work, "gate_swap_config\necho SWAPPED")
    assert r.returncode == 1
    assert "another gate is running" in r.stdout
    assert "SWAPPED" not in r.stdout
    assert (work / "config.yaml").read_text() == REAL


def test_a_planted_symlink_is_not_restored(work):
    target = work / "evil.yaml"
    target.write_text("evil: true\n")
    (work / "tmp" / ".gate_config.zzzzzz").symlink_to(target)
    r = _run(work, "gate_swap_config\ngate_restore_config")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "restoring config.yaml" not in r.stdout
    assert (work / "config.yaml").read_text() == REAL


def test_only_the_restored_backup_is_removed(work):
    older = work / "tmp" / ".gate_config.old111"
    older.write_text("older\n")
    os.utime(older, (1_600_000_000, 1_600_000_000))
    (work / "tmp" / ".gate_config.new222").write_text(REAL)
    (work / "config.yaml").write_text(EXAMPLE)
    r = _run(work, "gate_swap_config\ngate_restore_config")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "from tmp/.gate_config.new222" in r.stdout
    assert (work / "config.yaml").read_text() == REAL
    assert older.exists()


def test_the_scratch_dir_is_owner_only(work):
    r = _run(work, "gate_swap_config\ngate_restore_config")
    assert r.returncode == 0
    assert oct((work / "tmp").stat().st_mode)[-3:] == "700"


def test_no_config_before_means_none_after(work):
    (work / "config.yaml").unlink()
    r = _run(work, "gate_swap_config\ngate_restore_config")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (work / "config.yaml").exists()


def test_the_config_keeps_its_timestamp(work):
    os.utime(work / "config.yaml", (1_700_000_000, 1_700_000_000))
    r = _run(work, "gate_swap_config\ngate_restore_config")
    assert r.returncode == 0
    assert int((work / "config.yaml").stat().st_mtime) == 1_700_000_000


def test_the_gate_no_longer_uses_a_shared_tmp_copy():
    assert "/tmp/gate_config" not in GATE


def test_an_empty_leftover_is_not_restored(work):
    """A gate killed between mktemp and the backup copy leaves an empty file: never restore it."""
    (work / "tmp" / ".gate_config.empty0").write_text("")
    r = _run(work, "gate_swap_config\ngate_restore_config")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "restoring config.yaml" not in r.stdout
    assert (work / "config.yaml").read_text() == REAL
