"""Guard: deploy_all.sh config profiles fail LOUD and never clobber config.yaml.

Before: `cp config.<profile>.yaml config.yaml && echo ...` under `set -e` — the profile files are
gitignored and absent, so cp failed inside an && list (set -e does not fire) and the deploy
continued silently on the old config. The tracked .example copies were 84+ keys stale.
"""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import apply_config_profile as acp

ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = ROOT / "config" / "profiles"
BASELINE = ROOT / "config.yaml.example"


def _write(p: Path, data) -> Path:
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


@pytest.fixture
def env(tmp_path):
    """Minimal world: base config, baseline, and 4 overlays (balanced changes a.b)."""
    base = {
        "a": {"b": 1, "keep": "x"},
        "api": {"nara_api_key": "s3cr3t"},
        "dedup": {"k": True},
    }
    cfg = _write(tmp_path / "config.yaml", base)
    baseline = _write(tmp_path / "baseline.yaml", base)
    prof = tmp_path / "profiles"
    prof.mkdir()
    for p in acp.PROFILES:
        _write(prof / f"{p}.yaml", {})
    _write(prof / "balanced.yaml", {"a": {"b": 2}})
    _write(prof / "review-all-data.yaml", {"a": {"b": 9, "keep": "y"}})
    return cfg, prof, baseline


def _apply(name, env):
    cfg, prof, baseline = env
    return acp.apply_profile(name, cfg, prof, baseline)


def test_overlay_merges_only_profile_keys(env):
    cfg = env[0]
    assert _apply("balanced", env) == 0
    out = yaml.safe_load(cfg.read_text())
    assert out["a"] == {"b": 2, "keep": "x"} and out["dedup"] == {"k": True}
    assert out["api"]["nara_api_key"] == "s3cr3t"
    assert list(cfg.parent.glob("config.yaml.bak-*"))


def test_switching_profiles_does_not_accumulate(env):
    cfg = env[0]
    assert _apply("review-all-data", env) == 0
    assert yaml.safe_load(cfg.read_text())["a"] == {"b": 9, "keep": "y"}
    assert _apply("balanced", env) == 0  # keep is managed -> reset to baseline "x"
    assert yaml.safe_load(cfg.read_text())["a"] == {"b": 2, "keep": "x"}


def test_mode_preserved_and_backup_private(env):
    cfg = env[0]
    os.chmod(cfg, 0o600)
    assert _apply("balanced", env) == 0
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    bak = next(cfg.parent.glob("config.yaml.bak-*"))
    assert stat.S_IMODE(bak.stat().st_mode) == 0o600
    assert not list(cfg.parent.glob(".config.yaml.*.tmp"))


def test_secret_values_are_masked(env, capsys):
    cfg, prof, _ = env
    _write(prof / "balanced.yaml", {"api": {"nara_api_key": "NEWSECRET"}})
    assert _apply("balanced", env) == 0
    out = capsys.readouterr().out
    assert "s3cr3t" not in out and "NEWSECRET" not in out and "***" in out


@pytest.mark.parametrize(
    "overlay,name",
    [
        (None, "balanced"),  # overlay file absent
        ({"a": {"gone": 1}}, "balanced"),  # key absent from config.yaml
        ({"a": 5}, "balanced"),  # scalar onto a mapping (would wipe the subtree)
        ({"a": {"b": {"x": 1}}}, "balanced"),  # mapping onto a scalar
        ({"a": {"b": 2}}, "bogus"),  # unknown profile
    ],
)
def test_failures_are_loud_and_leave_config_untouched(env, overlay, name):
    cfg, prof, _ = env
    before = cfg.read_text()
    if overlay is None:
        (prof / "balanced.yaml").unlink()
    else:
        _write(prof / "balanced.yaml", overlay)
    assert _apply(name, env) == 1
    assert cfg.read_text() == before
    assert not list(cfg.parent.glob("config.yaml.bak-*"))


def test_no_change_writes_no_backup(env):
    cfg = env[0]
    assert _apply("cost-optimized", env) == 0
    assert not list(cfg.parent.glob("config.yaml.bak-*"))


@pytest.mark.parametrize("name", acp.PROFILES)
def test_every_shipped_overlay_applies_to_current_example(tmp_path, name):
    """Every tracked overlay key must exist in the tracked base config (no stale keys)."""
    cfg = tmp_path / "config.yaml"
    shutil.copy(BASELINE, cfg)
    assert acp.apply_profile(name, cfg, PROFILES_DIR, BASELINE) == 0


def _case_block() -> str:
    src = (ROOT / "scripts" / "deploy_all.sh").read_text()
    start = src.index("_apply_profile() {")
    end = src.index("esac", start) + len("esac")
    return src[start:end]


def _run_case(tmp_path, choice, script_rc):
    """Run deploy_all.sh's REAL case block from a fake repo whose apply script exits script_rc."""
    fake = tmp_path / "repo"
    (fake / "scripts").mkdir(parents=True)
    (fake / "scripts" / "apply_config_profile.py").write_text(
        f"import sys; print('APPLY', sys.argv[1:]); sys.exit({script_rc})\n"
    )
    (fake / "scripts" / "deploy_all.sh").write_text(
        f'set -e\nprofile_choice="{choice}"\n{_case_block()}\necho REACHED\n'
    )
    return subprocess.run(
        ["bash", str(fake / "scripts" / "deploy_all.sh")],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("choice", ["9", "x", "0"])
def test_unknown_choice_aborts(tmp_path, choice):
    r = _run_case(tmp_path, choice, 0)
    assert r.returncode == 1 and "REACHED" not in r.stdout


@pytest.mark.parametrize("choice", ["5", ""])
def test_keep_current_does_not_apply(tmp_path, choice):
    r = _run_case(tmp_path, choice, 0)
    assert r.returncode == 0 and "REACHED" in r.stdout and "APPLY" not in r.stdout


def test_profile_choice_invokes_apply_and_continues(tmp_path):
    r = _run_case(tmp_path, "1", 0)
    assert (
        r.returncode == 0 and "APPLY ['balanced']" in r.stdout and "REACHED" in r.stdout
    )


def test_apply_failure_aborts_deploy(tmp_path):
    r = _run_case(tmp_path, "1", 1)
    assert r.returncode == 1 and "REACHED" not in r.stdout


def test_no_full_file_cp_of_profiles_remains():
    src = (ROOT / "scripts" / "deploy_all.sh").read_text()
    assert not re.search(r"cp config\.[a-z-]+\.yaml config\.yaml", src)


def test_backup_never_world_readable(env, monkeypatch):
    """The backup must be created 0600 — not copied with umask perms then chmod'ed."""
    cfg = env[0]
    seen = []
    real_open = os.open

    def spy(path, flags, mode=0o777, *a, **k):
        if ".bak-" in str(path):
            seen.append(mode)
        return real_open(path, flags, mode, *a, **k)

    monkeypatch.setattr(os, "open", spy)
    old = os.umask(0o022)
    try:
        assert _apply("balanced", env) == 0
    finally:
        os.umask(old)
    assert seen == [0o600]


def test_stale_config_error_suggests_resync(env, capsys):
    cfg, prof, _ = env
    _write(prof / "balanced.yaml", {"a": {"gone": 1}})
    assert _apply("balanced", env) == 1
    assert "config.yaml.example" in capsys.readouterr().out
