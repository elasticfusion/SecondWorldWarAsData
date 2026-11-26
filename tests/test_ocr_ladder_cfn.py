"""Structure of the GPU OCR price ladder in cloudformation/ (task 1.1d): a Spot queue per rung, the
on-demand fallback at the ORIGINAL type only, a reclaim is not retried on the same queue, the merge
rule matches every ladder queue, and the controller can read prices and submit to every rung.
"""

from pathlib import Path

import yaml

from src.utils import gpu_ladder

CFN = Path(__file__).resolve().parent.parent / "cloudformation"


class _Loader(yaml.SafeLoader):
    pass


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {suffix: loader.construct_sequence(node, deep=True)}
    return {suffix: loader.construct_mapping(node, deep=True)}


_Loader.add_multi_constructor("!", _tag)


def _load(name):
    return yaml.load((CFN / name).read_text(), Loader=_Loader)


OCR = _load("ocr.yaml")
RES = OCR["Resources"]


def _queue_names():
    out = []
    for r in RES.values():
        if r["Type"] == "AWS::Batch::JobQueue":
            n = r["Properties"]["JobQueueName"]
            out.append(n["Sub"].replace("${EnvironmentName}", "dev"))
    return sorted(out)


def test_one_job_queue_per_rung_plus_ondemand_matching_gpu_ladder_names():
    assert _queue_names() == sorted(gpu_ladder.all_queues("dev"))


def test_rung_queues_are_spot_only_and_use_their_own_type():
    for k, (comp, queue) in enumerate(
        [
            ("ChandraComputeEnv", "ChandraJobQueue"),
            ("ChandraComputeEnvR1", "ChandraJobQueueR1"),
            ("ChandraComputeEnvR2", "ChandraJobQueueR2"),
            ("ChandraComputeEnvR3", "ChandraJobQueueR3"),
        ]
    ):
        cr = RES[comp]["Properties"]["ComputeResources"]
        assert cr["InstanceTypes"] == [{"Ref": f"GpuRung{k}Type"}], comp
        order = RES[queue]["Properties"]["ComputeEnvironmentOrder"]
        assert [o["ComputeEnvironment"] for o in order] == [{"Ref": comp}], queue
    for r in ("R1", "R2", "R3"):  # the upper rungs exist only on Spot
        assert RES[f"ChandraComputeEnv{r}"]["Condition"] == "IsSpot"
        assert RES[f"ChandraJobQueue{r}"]["Condition"] == "IsSpot"


def test_ondemand_fallback_holds_the_original_type_only():
    cr = RES["ChandraOnDemandComputeEnv"]["Properties"]["ComputeResources"]
    assert cr["InstanceTypes"] == [{"Ref": "GpuRung0Type"}]
    assert cr["Type"] == "EC2"


def test_ladder_types_are_validated_parameters():
    for k in range(4):
        p = OCR["Parameters"][f"GpuRung{k}Type"]
        assert p["AllowedPattern"] and p["Default"]
    assert OCR["Parameters"]["GpuRung0Type"]["Default"] == "g5.xlarge"


def test_reclaim_is_not_retried_on_the_same_queue():
    retry = RES["ChandraJobDef"]["Properties"]["RetryStrategy"]["If"]
    assert retry[0] == "HasLambdaCode"
    with_ctl, without = retry[1], retry[2]
    assert with_ctl["Attempts"] == 2
    assert with_ctl["EvaluateOnExit"] == [
        {"Action": "EXIT", "OnStatusReason": "Host EC2*"}
    ]
    assert without == {"Attempts": 2}


def test_controller_gets_the_ladder_cap_and_subnets():
    env = RES["OcrSpotControllerFunction"]["Properties"]["Environment"]["Variables"]
    assert {"OCR_GPU_LADDER", "OCR_MAX_FRACTION", "PRIVATE_SUBNET_IDS"} <= set(env)
    assert (
        env["OCR_MAX_FRACTION"] == "1.0"
    )  # the cap is the original on-demand price, no more


def _statements(role):
    pol = RES[role]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    return pol


def test_controller_may_read_prices_and_submit_to_every_rung():
    stmts = _statements("OcrSpotControllerRole")
    actions = {
        a
        for s in stmts
        for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])
    }
    assert {
        "pricing:GetProducts",
        "ec2:DescribeSpotPriceHistory",
        "ec2:DescribeSubnets",
        "ec2:DescribeInstanceTypes",
    } <= actions
    submit = next(s for s in stmts if s["Action"] == "batch:SubmitJob")
    resources = " ".join(r["Sub"] for r in submit["Resource"])
    for q in gpu_ladder.all_queues("${EnvironmentName}"):
        assert (
            f"job-queue/{q}\n" in resources + "\n"
            or f"job-queue/{q} " in resources + " "
        )


def test_failed_event_rule_targets_the_controller_with_permission():
    rule = RES["OcrSpotControllerFailedRule"]["Properties"]
    detail = rule["EventPattern"]["detail"]
    assert detail["status"] == ["FAILED"]
    assert "prefix" in detail["jobQueue"][0]
    assert rule["Targets"][0]["Arn"] == {"GetAtt": "OcrSpotControllerFunction.Arn"}
    perm = RES["OcrSpotControllerFailedPermission"]["Properties"]
    assert perm["SourceArn"] == {"GetAtt": "OcrSpotControllerFailedRule.Arn"}


def test_merge_rule_matches_every_ladder_queue_by_prefix():
    ev = _load("events.yaml")["Resources"]["OcrJobSucceededRule"]["Properties"]
    queues = ev["EventPattern"]["detail"]["jobQueue"]
    assert len(queues) == 1 and set(queues[0]) == {"prefix"}
    prefix = (
        queues[0]["prefix"]["Sub"]
        .rsplit("/", 1)[-1]
        .replace("${EnvironmentName}", "dev")
    )
    assert all(q.startswith(prefix) for q in gpu_ladder.all_queues("dev"))
