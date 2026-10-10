"""Tests for the S3 validation-backstop Lambda (block b-1)."""

import copy
import json

import lambda_handlers.s3_validation_backstop as bs


class FakeS3:
    def __init__(self, objects, tags=None):
        self.objects = objects  # key -> dict
        self.tags = tags or {}  # key -> {k:v}
        self.put_tags = {}

    def get_object(self, Bucket, Key):
        return {"Body": _Body(json.dumps(self.objects[Key]).encode())}

    def get_object_tagging(self, Bucket, Key):
        t = self.tags.get(Key, {})
        return {"TagSet": [{"Key": k, "Value": v} for k, v in t.items()]}

    def put_object_tagging(self, Bucket, Key, Tagging):
        self.put_tags[Key] = {t["Key"]: t["Value"] for t in Tagging["TagSet"]}


class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


def _eb(bucket, key):  # EventBridge 'Object Created' shape
    return {"detail": {"bucket": {"name": bucket}, "object": {"key": key}}}


def _valid_place():
    return {"PlaceID": "01HX7YZABCDEFGHJKMNPQRSTVW", "name": "Caen"}


def test_valid_record_no_tag_no_alert(monkeypatch):
    s3 = FakeS3({"output/places/a.json": _valid_place()})
    monkeypatch.setattr(bs.boto3, "client", lambda *a, **k: s3)
    alerts = []
    monkeypatch.setattr(bs, "_alert", lambda *a, **k: alerts.append(a))
    bs.handler(_eb("b", "output/places/a.json"))
    assert s3.put_tags == {}  # not quarantined
    assert alerts == []  # no alert on a valid record


def test_invalid_record_tagged_and_alerted(monkeypatch):
    # A places record whose required PlaceID can't be repaired-away and has a bad-typed field.
    bad = {"PlaceID": "01HX7YZABCDEFGHJKMNPQRSTVW", "latitude": "not-a-number"}
    s3 = FakeS3({"output/places/bad.json": bad})
    monkeypatch.setattr(bs.boto3, "client", lambda *a, **k: s3)
    monkeypatch.setattr(bs, "_validate_record", lambda *a, **k: False)  # force invalid
    alerts = []
    monkeypatch.setattr(bs, "_alert", lambda key, reason: alerts.append(key))
    bs.handler(_eb("b", "output/places/bad.json"))
    assert s3.put_tags["output/places/bad.json"]["quarantine"] == "true"
    assert alerts and "output/places/bad.json" in alerts[0]


def test_non_entity_file_skipped(monkeypatch):
    s3 = FakeS3({"output/places/index.json": {"anything": 1}})
    monkeypatch.setattr(bs.boto3, "client", lambda *a, **k: s3)
    # index.json is a non-entity file -> _validate_record returns True (skip), no tag.
    bs.handler(_eb("b", "output/places/index.json"))
    assert s3.put_tags == {}


def test_already_quarantined_skipped(monkeypatch):
    s3 = FakeS3(
        {"output/places/q.json": {"bad": 1}},
        tags={"output/places/q.json": {"quarantine": "true"}},
    )
    monkeypatch.setattr(bs.boto3, "client", lambda *a, **k: s3)
    called = []
    monkeypatch.setattr(
        bs, "_validate_record", lambda *a, **k: called.append(1) or False
    )
    bs.handler(_eb("b", "output/places/q.json"))
    assert called == []  # short-circuited before validation


def test_s3_notification_shape_also_supported(monkeypatch):
    s3 = FakeS3({"output/places/a.json": _valid_place()})
    monkeypatch.setattr(bs.boto3, "client", lambda *a, **k: s3)
    ev = {
        "Records": [
            {"s3": {"bucket": {"name": "b"}, "object": {"key": "output/places/a.json"}}}
        ]
    }
    out = bs.handler(ev)
    assert out["checked"] == 1


def test_validate_record_is_read_only():
    """_validate_record must not mutate the caller's dict (deep-copy + record_stats=False)."""
    rec = {"name": "X", "latitude": 0.0, "longitude": 0.0}
    before = copy.deepcopy(rec)
    bs._validate_record("output/places/p.json", rec)
    assert rec == before
