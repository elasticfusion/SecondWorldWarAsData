"""Shared EcsClamavScanner in av_scan (same code all phases): the ClamAV launcher+poller, with the
ECS run-task fn + s3 client + task def INJECTED so Phase-0 (trigger_handler) and Phase-3
(ecs_entrypoint) use the identical scanner. Verdict mapping + fail-closed verified.
"""

import json

from src.ingestion.av_scan import EcsClamavScanner, Verdict


class _S3:
    def __init__(self, verdicts):
        self._verdicts = verdicts

    def get_object(self, Bucket, Key):
        body = json.dumps(self._verdicts).encode()

        class B:
            def read(self_inner):
                return body

        return {"Body": B()}


def test_verdict_mapping_and_task_launch():
    launched = {}

    def run_task(td, group, extra_env=None):
        launched["td"] = td
        launched["env"] = {e["name"]: e["value"] for e in (extra_env or [])}

    s3 = _S3(
        {
            "k1": {"verdict": "clean"},
            "k2": {"verdict": "infected", "signature": "Eicar"},
        }
    )
    res = EcsClamavScanner(run_task, s3, "dev-wwii-av-scan").scan(
        "bucket", ["k1", "k2", "k3"]
    )
    assert launched["td"] == "dev-wwii-av-scan"
    assert "AV_KEYS" in launched["env"] and "AV_RESULT_KEY" in launched["env"]
    byk = {r.key: r.verdict for r in res}
    assert byk["k1"] is Verdict.CLEAN
    assert byk["k2"] is Verdict.INFECTED
    assert byk["k3"] is Verdict.ERROR  # unreported key -> fail-closed ERROR


def test_fail_closed_on_launch_error():
    def boom(*a, **k):
        raise RuntimeError("ecs down")

    res = EcsClamavScanner(boom, _S3({}), "x").scan("b", ["k1", "k2"])
    assert all(r.verdict is Verdict.ERROR for r in res)


def test_infected_signature_carried():
    s3 = _S3({"k1": {"verdict": "infected", "signature": "Win.Test.EICAR"}})
    res = EcsClamavScanner(lambda *a, **k: None, s3, "x").scan("b", ["k1"])
    assert res[0].signature == "Win.Test.EICAR"
