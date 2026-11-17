"""The OCR chunk manifest: the merge promotes a book only once EVERY chunk of its OCR
submission is in, merges only those chunks, and claims the merge once."""

import pytest

from src.ingestion import ocr_chunk_manifest as cm
from tests.fake_s3 import FakeS3


def test_chunk_dirs_and_keys():
    assert cm.chunk_dir("1-50") == "chunk-p0001-0050"
    assert cm.chunk_dir("") == ""
    assert (
        cm.chunk_markdown_key("B", "chunk-p0001-0050")
        == "ocr-output/B/chunk-p0001-0050/input/input.md"
    )
    assert cm.chunk_markdown_key("B", "") == "ocr-output/B/input/input.md"
    assert cm.first_page("chunk-p0051-0100") == 51
    assert cm.first_page("") == 1
    assert cm.first_page("chunk-003") is None  # a legacy dir carries no page range


def test_manifest_round_trip():
    m = cm.build_manifest(
        "s1", ["1-50", "51-80"], "contentrepository/x/B.pdf", "https://h.org/i.html"
    )
    back = cm.Manifest.from_json(m.to_json())
    assert back == m
    assert back.chunks == ["chunk-p0001-0050", "chunk-p0051-0080"]


@pytest.mark.parametrize(
    "raw",
    [
        b"[]",
        b'{"submission": "", "chunks": ["a"]}',
        b'{"submission": "s", "chunks": []}',
        b'{"submission": "s", "chunks": ["a", "a"]}',
        b'{"submission": "s", "chunks": ["", "a"]}',
        b'{"submission": "s", "chunks": [1]}',
        b'{"submission": "s", "chunks": ["a"], "validation_url": 3}',
    ],
)
def test_malformed_manifest_is_refused(raw):
    with pytest.raises(ValueError):
        cm.Manifest.from_json(raw)


def test_absent_manifest_is_none_and_malformed_raises():
    s3 = FakeS3()
    assert cm.read_manifest(s3, "buck", "B") is None
    s3.put_object(Bucket="buck", Key=cm.manifest_key("B"), Body=b"not json")
    with pytest.raises(ValueError):
        cm.read_manifest(s3, "buck", "B")


def test_missing_chunks_lists_those_without_markdown():
    m = cm.build_manifest("s1", ["1-50", "51-80"])
    s3 = FakeS3({cm.chunk_markdown_key("B", "chunk-p0001-0050"): "text"})
    assert cm.missing_chunks(s3, "buck", "B", m) == ["chunk-p0051-0080"]


def test_a_chunk_from_an_earlier_submission_is_not_done():
    """A re-OCR's chunk directories still hold the earlier run's markdown until each new job
    overwrites it: only output written after the manifest counts."""
    key = cm.chunk_markdown_key("B", "chunk-p0001-0050")
    s3 = FakeS3({key: "OLD"})
    s3.age(key, 3600)
    m = cm.build_manifest("s2", ["1-50"])  # written now
    assert cm.missing_chunks(s3, "buck", "B", m) == ["chunk-p0001-0050"]
    s3.put_object(Bucket="buck", Key=key, Body=b"NEW")  # the new job overwrites it
    assert cm.missing_chunks(s3, "buck", "B", m) == []


def test_a_manifest_without_a_creation_time_accepts_any_output():
    key = cm.chunk_markdown_key("B", "")
    s3 = FakeS3({key: "x"})
    s3.age(key, 10**6)
    m = cm.Manifest.from_json(b'{"submission": "s", "chunks": [""]}')
    assert m.created == 0 and cm.missing_chunks(s3, "buck", "B", m) == []


def test_merge_is_claimed_once():
    m = cm.build_manifest("s1", [""])
    s3 = FakeS3()
    assert cm.claim_merge(s3, "buck", "B", m) is True
    assert (
        cm.claim_merge(s3, "buck", "B", m) is False
    )  # the second last chunk does not merge


def test_only_the_owning_submission_releases_its_claim():
    s3 = FakeS3()
    old, new = cm.build_manifest("s1", [""]), cm.build_manifest("s2", [""])
    assert cm.claim_merge(s3, "buck", "B", new)
    assert (
        cm.release_merge(s3, "buck", "B", "s1") is False
    )  # a late task of the old submission
    assert cm.merged_key("B") in s3.keys()
    assert cm.release_merge(s3, "buck", "B", "s2") is True
    assert cm.claim_merge(s3, "buck", "B", old)  # free again


def test_a_claim_of_an_earlier_submission_is_taken_over():
    s3 = FakeS3()
    assert cm.claim_merge(s3, "buck", "B", cm.build_manifest("s1", [""]))
    assert cm.claim_merge(s3, "buck", "B", cm.build_manifest("s2", [""])) is True
    assert '"s2"' in s3.text(cm.merged_key("B"))


def test_a_claim_older_than_the_ttl_is_taken_over(monkeypatch):
    m = cm.build_manifest("s1", [""])
    s3 = FakeS3()
    assert cm.claim_merge(s3, "buck", "B", m)
    monkeypatch.setenv("OCR_MERGE_CLAIM_TTL", "60")
    real = cm.time.time
    monkeypatch.setattr(cm.time, "time", lambda: real() + 3600)
    assert (
        cm.claim_merge(s3, "buck", "B", m) is True
    )  # its merge died without releasing


def test_a_live_claim_of_the_same_submission_is_respected(monkeypatch):
    m = cm.build_manifest("s1", [""])
    s3 = FakeS3()
    assert cm.claim_merge(s3, "buck", "B", m)
    monkeypatch.setenv("OCR_MERGE_CLAIM_TTL", "3600")
    assert cm.claim_merge(s3, "buck", "B", m) is False


def test_a_legacy_bare_claim_is_stale():
    s3 = FakeS3(
        {cm.merged_key("B"): "s1"}
    )  # the earlier format: just the submission id
    assert cm.claim_merge(s3, "buck", "B", cm.build_manifest("s1", [""])) is True


def test_two_takers_of_a_stale_claim_cannot_both_win():
    s3 = FakeS3({cm.merged_key("B"): "s0"})
    real_get = s3.get_object
    seen = []

    def get_then_lose_the_race(**kw):
        out = real_get(**kw)
        if kw["Key"] == cm.merged_key("B") and not seen:
            seen.append(1)
            s3.put_object(
                Bucket="buck",
                Key=kw["Key"],
                Body=b'{"submission": "s9", "claimed_at": 1e12}',
            )
        return out

    s3.get_object = get_then_lose_the_race
    assert cm.claim_merge(s3, "buck", "B", cm.build_manifest("s1", [""])) is False


def test_a_new_submission_clears_nothing_it_does_not_own():
    s3 = FakeS3({cm.merged_key("B"): "old"})
    cm.write_manifest(s3, "buck", "B", cm.build_manifest("s2", [""]))
    assert cm.read_manifest(s3, "buck", "B").submission == "s2"
    assert (
        cm.merged_key("B") in s3.keys()
    )  # taken over by the new merge, never deleted blind


def test_the_redrive_command_names_the_book(monkeypatch):
    monkeypatch.setenv("ENV_NAME", "dev")
    cmd = cm.redrive_command("CXA")
    assert "dev-wwii-ocr-merge" in cmd and "chandra-CXA" in cmd


def test_an_operator_redrive_with_force_takes_over_a_live_claim():
    m = cm.build_manifest("s1", [""])
    s3 = FakeS3()
    assert cm.claim_merge(s3, "buck", "B", m)
    assert cm.claim_merge(s3, "buck", "B", m) is False
    assert cm.claim_merge(s3, "buck", "B", m, force=True) is True
    import json

    claim = json.loads(s3.text(cm.merged_key("B")))
    assert claim["submission"] == "s1" and cm.claim_age(claim) < 5  # a fresh claim


def test_the_redrive_command_forces():
    assert '"force": true' in cm.redrive_command("CXA")


def test_release_does_not_delete_a_claim_taken_over_in_between(monkeypatch):
    s3 = FakeS3()
    assert cm.claim_merge(s3, "buck", "B", cm.build_manifest("s1", [""]))
    real = cm.read_claim

    def read_then_lose_it(*a):
        out = real(*a)  # the claim as release sees it ...
        s3.put_object(
            Bucket="buck",
            Key=cm.merged_key("B"),
            Body=b'{"submission": "s2", "claimed_at": 1e12}',
        )
        return out  # ... then a newer submission takes it over

    monkeypatch.setattr(cm, "read_claim", read_then_lose_it)
    assert cm.release_merge(s3, "buck", "B", "s1") is False
    assert '"s2"' in s3.text(cm.merged_key("B"))


def test_an_old_boto3_without_conditional_delete_still_releases():
    s3 = FakeS3()
    assert cm.claim_merge(s3, "buck", "B", cm.build_manifest("s1", [""]))
    real = s3.delete_object

    def old_boto3(Bucket, Key, **kw):  # noqa: N803
        if "IfMatch" in kw:
            raise cm.ParamValidationError(report="Unknown parameter IfMatch")
        return real(Bucket=Bucket, Key=Key)

    s3.delete_object = old_boto3
    assert cm.release_merge(s3, "buck", "B", "s1") is True
    assert cm.merged_key("B") not in s3.keys()


def test_a_chunk_written_long_before_the_manifest_is_not_this_submissions():
    key = cm.chunk_markdown_key("B", "chunk-p0001-0050")
    s3 = FakeS3({key: "x"})
    m = cm.build_manifest("s1", ["1-50"])
    s3.age(key, 30)  # within the 60 s skew allowance: counts
    assert cm.missing_chunks(s3, "buck", "B", m) == []
    s3.age(key, 120)  # well before the manifest: an earlier submission's
    assert cm.missing_chunks(s3, "buck", "B", m) == ["chunk-p0001-0050"]
