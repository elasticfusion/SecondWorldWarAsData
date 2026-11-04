"""DRY P2.6 guard: the phase Lambda handlers share lambda_handlers._handler_common for the SNS->S3
record extraction (phase1/2/3) and the Grok API-key fetch (phase2/3)."""

from lambda_handlers._handler_common import extract_s3_records, get_api_key


def test_extract_s3_records_unwraps_sns():
    event = {
        "Records": [
            {
                "Sns": {
                    "Message": '{"Records":[{"s3":{"bucket":{"name":"b"},"object":{"key":"k.json"}}}]}'
                }
            }
        ]
    }
    assert extract_s3_records(event) == [("b", "k.json")]


def test_extract_s3_records_empty():
    assert extract_s3_records({}) == []


def test_get_api_key_env_fallback(monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "env-key")
    assert get_api_key({"aws": {}}) == "env-key"  # no secrets_id -> env


def test_phase_handlers_delegate(monkeypatch):
    import lambda_handlers._handler_common as hc
    import lambda_handlers.phase1_handler as p1
    import lambda_handlers.phase2_handler as p2
    import lambda_handlers.phase3_handler as p3

    monkeypatch.setattr(hc, "extract_s3_records", lambda e: [("B", "K")])
    monkeypatch.setattr(hc, "get_api_key", lambda c: "KEY")
    assert p1._extract_s3_records({}) == [("B", "K")]
    assert p2._extract_s3_records({}) == [("B", "K")]
    assert p3._extract_s3_records({}) == [("B", "K")]
    assert p2._get_api_key({}) == "KEY"
    assert p3._get_api_key({}) == "KEY"
