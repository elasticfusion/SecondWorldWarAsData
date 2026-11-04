"""Guard: Phase-2 batch mode (BATCH_PHASE2) is a durable deploy setting, not a template default.

Before: BatchPhase2 existed only on the nested ComputeStack (default 'false'); the parent stack did
not pass it, so a redeploy always reset it, and there was no deploy option to set it. Also,
`deploy_aws.py deploy` returned (exit 0) on a validation failure, defeating deploy_all.sh's
`|| hard stop`.
"""

import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import deploy_aws

ROOT = Path(__file__).resolve().parent.parent


class _CfnLoader(yaml.SafeLoader):
    pass


_CfnLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _tpl(name):
    return yaml.load((ROOT / "cloudformation" / name).read_text(), Loader=_CfnLoader)


# --- parameter logic -------------------------------------------------------------------------
def test_explicit_value_wins():
    for v in ("true", "false"):
        p = deploy_aws.batch_phase2_param(
            "update", v, [{"ParameterKey": "BatchPhase2"}]
        )
        assert p == {"ParameterKey": "BatchPhase2", "ParameterValue": v}


def test_update_keeps_live_value_when_parent_has_param():
    p = deploy_aws.batch_phase2_param("update", None, [{"ParameterKey": "BatchPhase2"}])
    assert p == {"ParameterKey": "BatchPhase2", "UsePreviousValue": True}


def test_first_update_after_param_added_falls_back_to_false_not_usepreviousvalue():
    # UsePreviousValue for a parameter new to the parent stack makes CloudFormation error.
    p = deploy_aws.batch_phase2_param("update", None, [{"ParameterKey": "Other"}])
    assert p == {"ParameterKey": "BatchPhase2", "ParameterValue": "false"}


def test_create_defaults_false():
    assert deploy_aws.batch_phase2_param("create", None, []) == {
        "ParameterKey": "BatchPhase2",
        "ParameterValue": "false",
    }


def test_invalid_value_rejected():
    with pytest.raises(ValueError):
        deploy_aws.batch_phase2_param("update", "yes", [])


# --- template wiring -------------------------------------------------------------------------
def test_parent_template_declares_and_passes_batch_phase2():
    main = _tpl("main.yaml")
    assert main["Parameters"]["BatchPhase2"]["AllowedValues"] == ["true", "false"]
    props = main["Resources"]["ComputeStack"]["Properties"]["Parameters"]
    assert "BatchPhase2" in props


def test_compute_template_still_consumes_it():
    assert "BatchPhase2" in _tpl("compute.yaml")["Parameters"]


# --- deploy_aws CLI --------------------------------------------------------------------------
def test_cli_exposes_batch_phase2_option():
    out = subprocess.run(
        ["python3", "scripts/deploy_aws.py", "deploy", "--help"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    ).stdout
    assert "--batch-phase2" in out


def test_validation_failure_exits_nonzero(monkeypatch):
    monkeypatch.setattr(deploy_aws, "cmd_validate", lambda a: False)
    with pytest.raises(SystemExit) as e:
        deploy_aws.cmd_deploy(SimpleNamespace(notification_email="x", dry_run=True))
    assert e.value.code == 1


# --- deploy_all.sh wiring --------------------------------------------------------------------
def _block():
    src = (ROOT / "scripts" / "deploy_all.sh").read_text()
    a = src.index('BATCH_ARG=""')
    b = src.index("esac", a) + len("esac")
    return src[a:b]


def _run(value):
    env = {"PATH": "/usr/bin:/bin"}
    if value is not None:
        env["BATCH_PHASE2"] = value
    script = "set -e\n" + _block() + '\necho "ARG=[$BATCH_ARG]"'
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env
    )


@pytest.mark.parametrize(
    "v,arg", [("true", "--batch-phase2 true"), ("false", "--batch-phase2 false")]
)
def test_deploy_all_passes_explicit_value(v, arg):
    r = _run(v)
    assert r.returncode == 0 and f"ARG=[{arg}]" in r.stdout


def test_deploy_all_unset_keeps_live_value():
    r = _run(None)
    assert r.returncode == 0 and "ARG=[]" in r.stdout and "keeping the live" in r.stdout


@pytest.mark.parametrize("v", ["yes", "1", "TRUE"])
def test_deploy_all_rejects_invalid(v):
    r = _run(v)
    assert r.returncode == 1 and "hard stop" in r.stdout


def test_batch_arg_is_on_the_deploy_aws_command_line():
    src = (ROOT / "scripts" / "deploy_all.sh").read_text()
    line = next(
        l
        for l in src.splitlines()
        if l.startswith("python3 scripts/deploy_aws.py deploy")
    )
    assert "$BATCH_ARG" in line and re.search(r"\$AV_ARG \$BATCH_ARG", line)
