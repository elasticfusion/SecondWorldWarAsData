"""Guard: deploy_all.sh config profiles fail LOUD and never clobber config.yaml.

Before: `cp config.<profile>.yaml config.yaml && echo ...` under `set -e` — the profile files are
gitignored and absent, so cp failed inside an && list (set -e does not fire) and the deploy
continued silently on the old config. The tracked .example copies were 84+ keys stale.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import apply_config_profile as acp

ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = ROOT / "config" / "profiles"


def _cfg(tmp_path, data):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def test_overlay_merges_only_profile_keys(tmp_path):
    cfg = _cfg(tmp_path, {"a": {"b": 1, "keep": "x"}, "dedup": {"k": True}})
    prof = tmp_path / "profiles"
    prof.mkdir()
    (prof / "balanced.yaml").write_text("a:\n  b: 2\n", encoding="utf-8")
    assert acp.apply_profile("balanced", cfg, prof) == 0
    out = yaml.safe_load(cfg.read_text())
    assert out == {"a": {"b": 2, "keep": "x"}, "dedup": {"k": True}}
    assert list(tmp_path.glob("config.yaml.bak-*"))  # backup written


@pytest.mark.parametrize(
    "setup,name",
    [
        ("missing", "balanced"),  # overlay file absent
        ("stale", "balanced"),  # key absent from config.yaml
        ("ok", "bogus"),  # unknown profile
    ],
)
def test_failures_are_loud_and_leave_config_untouched(tmp_path, setup, name):
    cfg = _cfg(tmp_path, {"a": {"b": 1}})
    before = cfg.read_text()
    prof = tmp_path / "profiles"
    prof.mkdir()
    if setup == "stale":
        (prof / "balanced.yaml").write_text("a:\n  gone: 1\n", encoding="utf-8")
    elif setup == "ok":
        (prof / "balanced.yaml").write_text("a:\n  b: 2\n", encoding="utf-8")
    assert acp.apply_profile(name, cfg, prof) == 1
    assert cfg.read_text() == before
    assert not list(tmp_path.glob("config.yaml.bak-*"))


@pytest.mark.parametrize("name", acp.PROFILES)
def test_every_shipped_overlay_applies_to_current_example(tmp_path, name):
    """Every tracked overlay key must exist in the tracked base config (no stale keys)."""
    cfg = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.yaml.example", cfg)
    assert acp.apply_profile(name, cfg, PROFILES_DIR) == 0


def _case_block() -> str:
    src = (ROOT / "scripts" / "deploy_all.sh").read_text()
    start = src.index("_apply_profile() {")
    end = src.index("esac", start) + len("esac")
    return src[start:end]


@pytest.mark.parametrize("choice,rc", [("9", 1), ("x", 1), ("5", 0), ("", 0)])
def test_deploy_all_case_block_rejects_unknown_choice(tmp_path, choice, rc):
    script = f'set -e\nprofile_choice="{choice}"\n' + _case_block().replace(
        '"$(dirname "$0")/apply_config_profile.py"', "/bin/false"
    )
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert r.returncode == rc, r.stdout + r.stderr


def test_deploy_all_case_block_aborts_when_apply_fails():
    script = (
        'set -e\nprofile_choice="1"\n'
        + _case_block().replace(
            'python3 "$(dirname "$0")/apply_config_profile.py"', "false"
        )
        + "\necho REACHED"
    )
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert r.returncode == 1 and "REACHED" not in r.stdout


def test_no_full_file_cp_of_profiles_remains():
    src = (ROOT / "scripts" / "deploy_all.sh").read_text()
    assert not re.search(r"cp config\.[a-z-]+\.yaml config\.yaml", src)
