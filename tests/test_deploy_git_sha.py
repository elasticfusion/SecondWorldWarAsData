"""Guard: deploy_all.sh builds the pipeline image with the version it deploys and refuses a dirty tree.

Task 0.2 (2026-11-18): the image's GIT_SHA (Dockerfile ARG -> `OBSERV pipeline.version sha=`) is
how a run's logs name the code that ran. Version = <commit>+cfg-<8 hex of sha256(config.yaml)>,
because config.yaml is gitignored (not pinned by the commit) but ships. Uncommitted or untracked
files in any other deployed path stop the deploy. The real block between the `>>> deploy-version`
and `<<< deploy-version` markers is extracted from the script and run in a scratch git repo that
mirrors the real layout (config.yaml gitignored).
"""

import hashlib
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "deploy_all.sh").read_text(encoding="utf-8")


def _block() -> str:
    start = SCRIPT.index("# >>> deploy-version")
    end = SCRIPT.index("# <<< deploy-version")
    return SCRIPT[start:end]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    files = {
        "src/a.py": "x\n",
        "tests/test_a.py": "x\n",
        "docs/x.md": "x\n",
        "main.py": "x\n",
        ".gitignore": "config.yaml\n",
        "config.yaml": "batch:\n  phase3: false\n",
    }
    for rel, text in files.items():
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        (r / rel).write_text(text)
    (r / "scripts").mkdir()
    _git(r, "init", "-q")
    # the test harness writes these into the scratch repo; keep them out of git status
    (r / ".git" / "info" / "exclude").write_text(
        "scripts/deploy_all.sh\nscripts/apply_config_profile.py\n"
    )
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    return r


def _run(repo: Path, tail: str = 'echo "VERSION=$GIT_SHA"'):
    # the block resolves the repo from $0 (scripts/deploy_all.sh), like the real script
    script = repo / "scripts" / "deploy_all.sh"
    script.write_text(f"set -e\n{_block()}\nrequire_committed_tree\n{tail}\n")
    return subprocess.run(
        ["bash", str(script)], cwd="/", capture_output=True, text=True, check=False
    )


def _cfg_hash(repo: Path) -> str:
    return hashlib.sha256((repo / "config.yaml").read_bytes()).hexdigest()[:8]


def test_a_clean_tree_sets_commit_plus_config_hash(repo):
    r = _run(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (
        f"VERSION={_git(repo, 'rev-parse', 'HEAD')}+cfg-{_cfg_hash(repo)}" in r.stdout
    )


def test_an_edited_gitignored_config_changes_the_version_not_the_verdict(repo):
    before = _run(repo).stdout
    (repo / "config.yaml").write_text("batch:\n  phase3: true\n")
    r = _run(repo)
    assert r.returncode == 0
    assert f"+cfg-{_cfg_hash(repo)}" in r.stdout
    assert r.stdout != before


@pytest.mark.parametrize(
    "change",
    [
        ("modify", "src/a.py"),
        ("modify", "main.py"),
        ("stage", "src/a.py"),
        ("delete", "src/a.py"),
        ("untracked", "src/new.py"),
        ("untracked", "Dockerfile.extra"),
        ("untracked", "requirements-new.txt"),
    ],
)
def test_a_change_in_a_deployed_path_stops_the_deploy(repo, change):
    kind, rel = change
    path = repo / rel
    if kind == "delete":
        path.unlink()
    else:
        path.write_text("changed\n")
    if kind == "stage":
        _git(repo, "add", rel)
    r = _run(repo)
    assert r.returncode == 1
    assert "uncommitted changes in deployed paths" in r.stdout
    assert rel in r.stdout
    assert "VERSION=" not in r.stdout


@pytest.mark.parametrize(
    "rel", ["tests/test_a.py", "docs/x.md", "tmp/scratch.txt", "tests/new_test.py"]
)
def test_changes_outside_deployed_paths_do_not_block(repo, rel):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("changed\n")
    r = _run(repo)
    assert r.returncode == 0, r.stdout
    assert "VERSION=" in r.stdout


def test_more_than_twenty_changes_are_counted(repo):
    for i in range(25):
        (repo / "src" / f"n{i}.py").write_text("x\n")
    r = _run(repo)
    assert r.returncode == 1
    assert "... and 5 more" in r.stdout


def test_a_missing_config_stops(repo):
    (repo / "config.yaml").unlink()
    r = _run(repo)
    assert r.returncode == 1 and "config.yaml is missing" in r.stdout


def test_outside_a_git_checkout_stops(tmp_path):
    d = tmp_path / "plain"
    (d / "scripts").mkdir(parents=True)
    script = d / "scripts" / "deploy_all.sh"
    script.write_text(f"set -e\n{_block()}\nrequire_committed_tree\necho REACHED\n")
    r = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "GIT_CEILING_DIRECTORIES": str(tmp_path)},
    )
    assert r.returncode == 1 and "REACHED" not in r.stdout
    assert "not a git checkout" in r.stdout


def test_a_profile_restamps_the_version(repo):
    """A config profile rewrites config.yaml after the check: the version names the new content.
    Runs the script's real _apply_profile against a fake apply script that edits config.yaml.
    """
    body = SCRIPT[SCRIPT.index("_apply_profile() {") :]
    body = body[: body.index("\n}\n") + 3]
    (repo / "scripts" / "apply_config_profile.py").write_text(
        "import pathlib, sys\n"
        "p = pathlib.Path(__file__).resolve().parents[1] / 'config.yaml'\n"
        "p.write_text(p.read_text() + 'profile: ' + sys.argv[1] + '\\n')\n"
    )
    before = _cfg_hash(repo)
    r = _run(repo, f'{body}\n_apply_profile balanced\necho "VERSION=$GIT_SHA"')
    assert r.returncode == 0, r.stdout + r.stderr
    after = _cfg_hash(repo)
    assert after != before
    assert r.stdout.strip().splitlines()[-1].endswith(f"+cfg-{after}")


def test_the_guard_runs_before_any_build():
    """Called once at the top level, before the OCR-standalone branch and the main build."""
    call = SCRIPT.index("\nrequire_committed_tree\n")
    assert call < SCRIPT.index('if [ "$1" = "--ocr-standalone" ]')
    assert call < SCRIPT.index("=== 4. Building and pushing container ===")


def test_the_profile_step_runs_before_the_main_builds():
    """stamp_version after a profile must precede the main builds, or an image carries a stale
    hash. (The --ocr-standalone branch builds earlier but exits before the profile step.)
    """
    profile = SCRIPT.index('case "${profile_choice:-5}" in')
    assert profile > SCRIPT.index('if [ "$1" = "--ocr-standalone" ]')
    assert profile < SCRIPT.index("=== 4. Building and pushing container ===")
    assert profile < SCRIPT.index("=== 4b. Optional separate images")


def test_the_builds_use_the_checked_tree():
    assert '\nrequire_committed_tree\ncd "$DEPLOY_ROOT"' in SCRIPT


def test_the_pipeline_dockerfile_declares_the_version():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG GIT_SHA=" in dockerfile and "ENV GIT_SHA=${GIT_SHA}" in dockerfile


def test_every_docker_build_passes_the_version():
    builds = [
        line for line in SCRIPT.splitlines() if re.match(r"\s*docker build\b", line)
    ]
    assert builds, "no docker build lines found"
    for line in builds:
        assert '--build-arg GIT_SHA="$GIT_SHA"' in line, line
