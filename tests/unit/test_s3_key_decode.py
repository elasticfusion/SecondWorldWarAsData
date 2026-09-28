"""Test that _extract_records URL-decodes S3 object keys.

S3 event notifications URL-encode the key (space -> '+', other chars -> %XX).
The trigger must decode it so downstream OCR/parse use the real key (e.g.
'B 400-499/...'), not the encoded 'B+400-499/...' which doesn't exist.
"""

import json
import os

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("ECS_CLUSTER", "dev-wwii-pipeline")
os.environ.setdefault("CACHE_TABLE", "dev-wwii-api-cache")

from lambda_handlers import trigger_handler as th


def _sqs_event_with_key(encoded_key):
    s3ev = {"Records": [{"s3": {"object": {"key": encoded_key}}}]}
    sns = {
        "TopicArn": "arn:aws:sns:us-east-1:111:dev-wwii-content-uploaded",
        "Message": json.dumps(s3ev),
    }
    return {"Records": [{"body": json.dumps(sns)}]}


def test_extract_records_decodes_plus_to_space():
    ev = _sqs_event_with_key("contentrepository/NARA/B-Series/B+400-499/B421.pdf")
    _topics, keys = th._extract_records(ev)
    assert keys == ["contentrepository/NARA/B-Series/B 400-499/B421.pdf"]


def test_extract_records_decodes_percent_encoding():
    ev = _sqs_event_with_key("contentrepository/a%20b/doc.pdf")
    _topics, keys = th._extract_records(ev)
    assert keys == ["contentrepository/a b/doc.pdf"]


def test_extract_records_plain_key_unchanged():
    ev = _sqs_event_with_key("contentrepository/B405/B405.md")
    _topics, keys = th._extract_records(ev)
    assert keys == ["contentrepository/B405/B405.md"]
