"""fetch_review._load_events in DynamoDB mode (the Lambda environment)."""

import json
import src.enrichment.fetch_review as FR
from src.utils import fetch_provenance as fp


class _Table:
    def __init__(self, items):
        self._items = items

    def scan(self, **kwargs):
        return {"Items": self._items}


def test_load_events_dynamo_filters_textual_pending(monkeypatch):
    rec_pending = {
        "fetch_event_id": "e1",
        "kind": fp.TEXTUAL,
        "review_status": fp.PENDING,
        "provider": "wayback",
        "endpoint": "http://x",
    }
    rec_struct = {"fetch_event_id": "e2", "kind": "structured", "review_status": None}
    items = [
        {"cache_key": fp._KEY_PREFIX + "e1", "response": json.dumps(rec_pending)},
        {"cache_key": fp._KEY_PREFIX + "e2", "response": json.dumps(rec_struct)},
    ]
    monkeypatch.setattr(fp, "_table", lambda: _Table(items))
    pend = FR.list_pending()
    assert [p["fetch_event_id"] for p in pend] == ["e1"]  # only TEXTUAL+pending


def test_load_events_local_when_no_table(monkeypatch, tmp_path):
    monkeypatch.setattr(fp, "_table", lambda: None)
    d = tmp_path / "ev"
    d.mkdir()
    (d / "wayback.jsonl").write_text(
        json.dumps(
            {"fetch_event_id": "e3", "kind": fp.TEXTUAL, "review_status": fp.PENDING}
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(fp, "_LOCAL_DIR", d)
    assert [p["fetch_event_id"] for p in FR.list_pending()] == ["e3"]


def test_resolve_table_uses_cache_table_env(monkeypatch):
    """Lambda path: _resolve_table must use CACHE_TABLE env even when config.aws.enabled is false
    (the config flag is only set inside ECS, never in the review Lambda)."""
    import src.enrichment.fetch_review as FR2

    captured = {}

    class _Res:
        def Table(self, name):
            captured["table"] = name
            return "TABLE_HANDLE"

    class _Boto:
        @staticmethod
        def resource(svc, region_name=None):
            captured["svc"] = svc
            captured["region"] = region_name
            return _Res()

    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "dev-wwii-urlreview-ui")
    monkeypatch.setenv("CACHE_TABLE", "dev-wwii-api-cache")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setitem(__import__("sys").modules, "boto3", _Boto)
    # fp._table must NOT be consulted when CACHE_TABLE env is present
    monkeypatch.setattr(
        fp,
        "_table",
        lambda: (_ for _ in ()).throw(AssertionError("should not call fp._table")),
    )
    handle = FR2._resolve_table(fp)
    assert handle == "TABLE_HANDLE"
    assert captured["table"] == "dev-wwii-api-cache" and captured["svc"] == "dynamodb"


def test_resolve_table_falls_back_to_config(monkeypatch):
    """No CACHE_TABLE env -> fall back to the config-driven fp._table()."""
    import src.enrichment.fetch_review as FR2

    monkeypatch.delenv("CACHE_TABLE", raising=False)
    monkeypatch.setattr(fp, "_table", lambda: "CONFIG_HANDLE")
    assert FR2._resolve_table(fp) == "CONFIG_HANDLE"
