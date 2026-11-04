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


# --- cmd_deploy -> update_stack wiring (the helper must actually be plugged in) ----------------
class _ClientError(Exception):
    pass


class _FakeCf:
    class exceptions:  # noqa: N801 - mirrors boto3's client.exceptions
        ClientError = _ClientError

    def __init__(self, params, exists=True, describe_error=None):
        self._params, self._exists, self._err = params, exists, describe_error
        self.calls = {}

    def describe_stacks(self, StackName):
        if self._err:
            raise _ClientError(self._err)
        if not self._exists:
            raise _ClientError(f"Stack with id {StackName} does not exist")
        return {"Stacks": [{"Parameters": self._params}]}

    def update_stack(self, **kw):
        self.calls["update"] = kw

    def create_stack(self, **kw):
        self.calls["create"] = kw


def _deploy(monkeypatch, cf, batch):
    monkeypatch.setattr(deploy_aws, "cmd_validate", lambda a: True)
    monkeypatch.setattr(deploy_aws, "_get_cf_client", lambda *a, **k: cf)
    monkeypatch.setattr(deploy_aws, "_probe_gpu_azs", lambda *a, **k: ["a", "b"])
    monkeypatch.setattr(deploy_aws, "_wait_for_stack", lambda *a, **k: None)
    args = SimpleNamespace(
        env="dev",
        region="us-east-1",
        profile=None,
        template_bucket="b",
        openserp_image="o",
        pipeline_image="p",
        video_image=None,
        av_image=None,
        notification_email="e@x",
        multi_doc="false",
        dry_run=False,
        batch_phase2=batch,
    )
    deploy_aws.cmd_deploy(args)


def _param(cf, action):
    return next(
        p for p in cf.calls[action]["Parameters"] if p["ParameterKey"] == "BatchPhase2"
    )


def test_update_with_flag_sets_value(monkeypatch):
    cf = _FakeCf([{"ParameterKey": "BatchPhase2", "ParameterValue": "false"}])
    _deploy(monkeypatch, cf, "true")
    assert _param(cf, "update") == {
        "ParameterKey": "BatchPhase2",
        "ParameterValue": "true",
    }


def test_update_without_flag_keeps_live_value(monkeypatch):
    cf = _FakeCf([{"ParameterKey": "BatchPhase2", "ParameterValue": "true"}])
    _deploy(monkeypatch, cf, None)
    assert _param(cf, "update") == {
        "ParameterKey": "BatchPhase2",
        "UsePreviousValue": True,
    }


def test_first_update_without_flag_is_explicit_false(monkeypatch):
    cf = _FakeCf([{"ParameterKey": "EnvironmentName", "ParameterValue": "dev"}])
    _deploy(monkeypatch, cf, None)
    assert _param(cf, "update") == {
        "ParameterKey": "BatchPhase2",
        "ParameterValue": "false",
    }


def test_missing_stack_creates(monkeypatch):
    cf = _FakeCf([], exists=False)
    _deploy(monkeypatch, cf, "true")
    assert _param(cf, "create")["ParameterValue"] == "true"


def test_access_denied_is_not_mistaken_for_missing_stack(monkeypatch):
    cf = _FakeCf([], describe_error="AccessDenied: not authorized")
    with pytest.raises(_ClientError):
        _deploy(monkeypatch, cf, None)
    assert cf.calls == {}  # neither create nor update was attempted


def test_batch_phase2_is_not_exported_to_the_qa_test_run():
    """BATCH_PHASE2 is consumed into BATCH_ARG and UNSET before the QA pytest step: left exported
    it made tests/test_batch_routing_guard.py fail and aborted the deploy before it deployed.
    """
    src = (ROOT / "scripts" / "deploy_all.sh").read_text()
    unset = src.index("unset BATCH_PHASE2")
    assert unset < src.index("=== 0. Select config profile")
    assert unset < src.index("python3 -m pytest tests/")
    assert "$BATCH_PHASE2" not in src[src.index("python3 -m pytest tests/") :]


def test_unset_after_parse_hides_value_from_child_processes():
    """Runs the script's OWN parse + unset segment (not a test-side unset)."""
    src = (ROOT / "scripts" / "deploy_all.sh").read_text()
    start = src.index('BATCH_ARG=""')
    end = src.index("unset BATCH_PHASE2", start) + len(
        "unset BATCH_PHASE2"
    )  # absent -> ValueError
    script = (
        "set -e\n" + src[start:end] + "\n"
        "bash -c 'echo \"CHILD=[${BATCH_PHASE2:-}]\"'\n"
        'echo "ARG=[$BATCH_ARG]"'
    )
    env = {"PATH": "/usr/bin:/bin", "BATCH_PHASE2": "true"}
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert "CHILD=[]" in r.stdout and "ARG=[--batch-phase2 true]" in r.stdout


def test_invalid_value_is_rejected_before_anything_destructive():
    src = (ROOT / "scripts" / "deploy_all.sh").read_text()
    assert src.index("hard stop", src.index('BATCH_ARG=""')) < src.index(
        "=== 1. Stopping running tasks"
    )
    r = _run("bogus")
    assert r.returncode == 1 and "hard stop" in r.stdout


def test_batch_phase3_is_scrubbed_from_the_qa_environment():
    src = (ROOT / "scripts" / "deploy_all.sh").read_text()
    start = src.index('BATCH_ARG=""')
    end = src.index("unset BATCH_PHASE2", start)
    end = src.index("\n", end)
    script = (
        "set -e\n"
        + src[start:end]
        + "\nbash -c 'echo \"P3=[${BATCH_PHASE3:-}] P2=[${BATCH_PHASE2:-}]\"'"
    )
    env = {"PATH": "/usr/bin:/bin", "BATCH_PHASE2": "true", "BATCH_PHASE3": "true"}
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert "P3=[] P2=[]" in r.stdout
