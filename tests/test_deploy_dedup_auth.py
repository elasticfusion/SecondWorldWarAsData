"""Guard: the dedup UI login comes only from Secrets Manager, never a hard-coded fallback (task 0.5).

The real block between `>>> dedup-auth` and `<<< dedup-auth` in scripts/deploy_all.sh runs with a
fake `aws` on PATH (no AWS call is made). Checks: a missing or empty secret stops the deploy before
any build; the value reaches the Lambda through a private temp file, never on a command line; no
fallback credential remains in the script.
"""

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "deploy_all.sh").read_text(encoding="utf-8")
SECRET = "op:Correct-Horse-9"


def _block() -> str:
    return SCRIPT[SCRIPT.index("# >>> dedup-auth") : SCRIPT.index("# <<< dedup-auth")]


def _fake_aws(bin_dir: Path, log: Path, secret_rc: int, secret_out: str) -> None:
    """aws stub: logs argv; for update-function-configuration copies the --environment file."""
    bin_dir.mkdir()
    aws = bin_dir / "aws"
    aws.write_text(
        "#!/bin/bash\n"
        f'echo "ARGV: $*" >> "{log}"\n'
        'if [ "$1" = "secretsmanager" ]; then\n'
        f"  printf '%s' '{secret_out}'; exit {secret_rc}\n"
        "fi\n"
        'if [ "$1" = "lambda" ]; then\n'
        '  for a in "$@"; do case "$a" in file://*) f="${a#file://}";\n'
        f'    stat -c "MODE %a" "$f" >> "{log}"; cat "$f" >> "{log}"; echo >> "{log}";;\n'
        "  esac; done; exit 0\n"
        "fi\n"
    )
    aws.chmod(aws.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path: Path, tail: str, secret_rc: int = 0, secret_out: str = SECRET):
    log = tmp_path / "aws.log"
    _fake_aws(tmp_path / "bin", log, secret_rc, secret_out)
    script = f"set -e\nENV=dev\nREGION=us-east-1\n{_block()}\n{tail}\necho REACHED\n"
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"}
    r = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, check=False
    )
    return r, (log.read_text() if log.exists() else "")


def test_a_present_secret_passes_the_check(tmp_path):
    r, _ = _run(tmp_path, "require_dedup_auth")
    assert r.returncode == 0 and "REACHED" in r.stdout
    assert "found in Secrets Manager" in r.stdout
    assert SECRET not in r.stdout + r.stderr


@pytest.mark.parametrize(
    "rc,out", [(255, ""), (0, ""), (0, "None")], ids=["missing", "empty", "None"]
)
def test_a_missing_or_empty_secret_stops(tmp_path, rc, out):
    r, _ = _run(tmp_path, "require_dedup_auth", secret_rc=rc, secret_out=out)
    assert r.returncode == 1 and "REACHED" not in r.stdout
    assert "dev-wwii-pipeline/dedup-auth is missing or empty" in r.stderr


def test_the_push_uses_a_private_file_not_the_command_line(tmp_path):
    r, log = _run(tmp_path, "push_dedup_auth")
    assert r.returncode == 0, r.stdout + r.stderr
    lambda_argv = [l for l in log.splitlines() if l.startswith("ARGV: lambda")]
    assert len(lambda_argv) == 1
    assert SECRET not in lambda_argv[0]
    assert "--environment file://" in lambda_argv[0]
    assert "MODE 600" in log
    payload = next(l for l in log.splitlines() if l.startswith("{"))
    assert json.loads(payload) == {"Variables": {"AUTH_TOKEN": SECRET}}
    temp = lambda_argv[0].split("file://", 1)[1].split()[0]
    assert not Path(temp).exists(), "temp file must be removed"


def test_the_push_stops_when_the_secret_vanished(tmp_path):
    r, log = _run(tmp_path, "push_dedup_auth", secret_rc=255, secret_out="")
    assert r.returncode == 1 and "REACHED" not in r.stdout
    assert "ARGV: lambda" not in log


def test_no_fallback_credential_remains():
    assert "ReviewPass" not in SCRIPT
    assert "AUTH_TOKEN=$AUTH_TOKEN" not in SCRIPT


def test_the_check_runs_before_any_build():
    call = SCRIPT.index("\nrequire_dedup_auth\n")
    assert call < SCRIPT.index("=== 4. Building and pushing container ===")
    assert SCRIPT.index("\npush_dedup_auth\n") > SCRIPT.index("=== 7. Fixing auth ===")


def test_the_aws_error_is_shown_without_the_value(tmp_path):
    log = tmp_path / "aws.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    aws = bin_dir / "aws"
    aws.write_text(
        "#!/bin/bash\necho 'An error occurred (AccessDeniedException)' >&2\nexit 255\n"
    )
    aws.chmod(aws.stat().st_mode | stat.S_IEXEC)
    script = f"set -e\nENV=dev\nREGION=us-east-1\n{_block()}\nrequire_dedup_auth\n"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    r = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, check=False
    )
    assert r.returncode == 1
    assert "aws: An error occurred (AccessDeniedException)" in r.stderr
    assert not log.exists()


def test_a_failed_lambda_update_removes_the_temp_file(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    seen = tmp_path / "seen"
    aws = bin_dir / "aws"
    aws.write_text(
        "#!/bin/bash\n"
        f"if [ \"$1\" = secretsmanager ]; then printf '%s' '{SECRET}'; exit 0; fi\n"
        f'for a in "$@"; do case "$a" in file://*) echo "${{a#file://}}" > "{seen}";; esac; done\n'
        "exit 1\n"
    )
    aws.chmod(aws.stat().st_mode | stat.S_IEXEC)
    script = f"set -e\nENV=dev\nREGION=us-east-1\n{_block()}\npush_dedup_auth\n"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    r = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, check=False
    )
    assert r.returncode == 1 and "Hard stop" in r.stdout
    assert not Path(seen.read_text().strip()).exists()


# --- the authorizer Lambda (inline code in cloudformation/compute.yaml) -------------------------


def _authorizer():
    import base64
    import re as _re

    tpl = (ROOT / "cloudformation" / "compute.yaml").read_text(encoding="utf-8")
    start = tpl.index("import hmac")
    block = tpl[tpl.rindex("ZipFile: |", 0, start) :]
    lines = block.splitlines()[1:]
    indent = len(lines[0]) - len(lines[0].lstrip())
    code = []
    for line in lines:
        if line.strip() and (len(line) - len(line.lstrip())) < indent:
            break
        code.append(line[indent:])
    ns: dict = {}
    exec("\n".join(code), ns)  # noqa: S102 - our own template code, under test
    assert _re.search(r"def handler", "\n".join(code))
    return ns["handler"], base64


def _call(handler, b64, token, expected, monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", expected)
    out = handler(
        {
            "authorizationToken": token,
            "methodArn": "arn:aws:execute-api:us-east-1:1:abc/app/GET/x",
        },
        None,
    )
    return out["policyDocument"]["Statement"][0]["Effect"]


def test_the_authorizer_allows_the_right_login(monkeypatch):
    h, b64 = _authorizer()
    tok = "Basic " + b64.b64encode(b"op:pw").decode()
    assert _call(h, b64, tok, "op:pw", monkeypatch) == "Allow"


@pytest.mark.parametrize(
    "token,expected",
    [
        ("Basic " + __import__("base64").b64encode(b"op:wrong").decode(), "op:pw"),
        ("Basic not*base64!", "op:pw"),
        ("Bearer x", "op:pw"),
        ("Basic ", ""),  # no configured login: "" must not match ""
        ("Basic " + __import__("base64").b64encode(b"").decode(), ""),
    ],
    ids=["wrong", "bad-base64", "not-basic", "empty-config", "empty-both"],
)
def test_the_authorizer_denies(monkeypatch, token, expected):
    h, b64 = _authorizer()
    assert _call(h, b64, token, expected, monkeypatch) == "Deny"


def test_the_authorizer_compares_in_constant_time():
    tpl = (ROOT / "cloudformation" / "compute.yaml").read_text(encoding="utf-8")
    assert "hmac.compare_digest(" in tpl
    assert "decoded == expected" not in tpl


def test_a_signal_during_the_push_removes_the_temp_file(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    seen = tmp_path / "seen"
    aws = bin_dir / "aws"
    aws.write_text(
        "#!/bin/bash\n"
        f"if [ \"$1\" = secretsmanager ]; then printf '%s' '{SECRET}'; exit 0; fi\n"
        f'for a in "$@"; do case "$a" in file://*) echo "${{a#file://}}" > "{seen}";; esac; done\n'
        "kill -TERM $PPID; sleep 2; exit 0\n"
    )
    aws.chmod(aws.stat().st_mode | stat.S_IEXEC)
    script = f"set -e\nENV=dev\nREGION=us-east-1\n{_block()}\npush_dedup_auth\necho REACHED\n"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    r = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, check=False
    )
    assert r.returncode == 130 and "REACHED" not in r.stdout
    assert not Path(seen.read_text().strip()).exists()
