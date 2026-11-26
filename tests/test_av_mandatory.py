"""ClamAV is mandatory (owner 2026-11-23): the template requires AvImageUri, creates the AV
resources unconditionally and always turns the trigger's scan on; the deploy CLI requires it.
"""

import sys
from pathlib import Path

import pytest
import yaml


class _Loader(yaml.SafeLoader):
    pass


def _cfn_tag(loader, suffix, node):  # !Ref, !Sub, !If ... -> plain data
    if isinstance(node, yaml.ScalarNode):
        return {suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {suffix: loader.construct_sequence(node, deep=True)}
    return {suffix: loader.construct_mapping(node, deep=True)}


_Loader.add_multi_constructor("!", _cfn_tag)


def _template(name):
    return yaml.load(Path(f"cloudformation/{name}").read_text(), Loader=_Loader)


@pytest.mark.parametrize("name", ["compute.yaml", "main.yaml"])
def test_the_av_image_parameter_is_required(name):
    param = _template(name)["Parameters"]["AvImageUri"]
    assert "Default" not in param and param["MinLength"] == 1


def test_the_av_resources_always_exist_and_the_scan_is_always_on():
    t = _template("compute.yaml")
    assert "HasAvImage" not in (t.get("Conditions") or {})
    for name in ("AvScanTaskDef", "AvSignatureUpdateTaskDef", "AvFreshclamSchedule"):
        assert "Condition" not in t["Resources"][name], name
    env = t["Resources"]["TriggerFunction"]["Properties"]["Environment"]["Variables"]
    assert env["AV_SCAN_ENABLED"] == "true"


def test_the_deploy_cli_requires_the_av_image(monkeypatch, capsys):
    sys.path.insert(0, "scripts")
    import deploy_aws

    monkeypatch.setattr(
        sys,
        "argv",
        ["deploy_aws.py", "deploy", "--template-bucket", "b", "--pipeline-image", "p",
         "--openserp-image", "o"],
    )  # fmt: skip
    with pytest.raises(SystemExit):
        deploy_aws.main()
    assert "--av-image" in capsys.readouterr().err
