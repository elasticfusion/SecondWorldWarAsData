"""Bucket lifecycle policy (cloudformation/storage.yaml): superseded object versions are the
preservation mechanism for source text (e.g. the uncorrected OCR merge a correction supersedes),
so they may expire ONLY under high-churn scratch prefixes - never bucket-wide, and never under
contentrepository/ or output/ (owner 2026-11-08)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

# Prefixes whose superseded versions must be kept forever.
KEPT = (
    "contentrepository/", "output/", "ocr-output/", "source/", "artifacts/",
    "dedup/", "needs-review/", "person_photos/",
)  # fmt: skip
# High-churn prefixes whose superseded versions must expire (else they pile up).
CHURN = ("tmp/", "cache/", "scratch/", "av-test/", "relocated/", "manifests/")


class _CfnLoader(yaml.SafeLoader):
    pass


_CfnLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _rules():
    tpl = yaml.load(
        (ROOT / "cloudformation" / "storage.yaml").read_text(), Loader=_CfnLoader
    )
    buckets = [
        r["Properties"]
        for r in tpl["Resources"].values()
        if r.get("Type") == "AWS::S3::Bucket"
        and "LifecycleConfiguration" in r.get("Properties", {})
    ]
    assert buckets, "no bucket with a lifecycle configuration"
    return [rule for b in buckets for rule in b["LifecycleConfiguration"]["Rules"]]


def _noncurrent_rules():
    return [
        r
        for r in _rules()
        if r.get("Status") == "Enabled" and "NoncurrentVersionExpiration" in r
    ]


def _prefix(rule) -> str:
    return rule.get("Prefix") or (rule.get("Filter") or {}).get("Prefix") or ""


def test_versioning_enabled():
    tpl = (ROOT / "cloudformation" / "storage.yaml").read_text()
    assert "VersioningConfiguration:\n        Status: Enabled" in tpl


def test_no_bucket_wide_noncurrent_expiry():
    for rule in _noncurrent_rules():
        assert _prefix(rule), f"{rule['Id']} expires old versions bucket-wide"
        assert not rule.get("TagFilters"), f"{rule['Id']} is tag-scoped, not prefix"


def test_kept_prefixes_never_expire_old_versions():
    for rule in _noncurrent_rules():
        p = _prefix(rule)
        for kept in KEPT:
            # neither the kept prefix itself, a parent of it, nor anything inside it
            assert not (
                p.startswith(kept) or kept.startswith(p)
            ), f"{rule['Id']} ({p}) would expire superseded versions under {kept}"


def test_churn_prefixes_expire_old_versions():
    covered = {_prefix(r) for r in _noncurrent_rules()}
    for churn in CHURN:
        assert churn in covered, f"superseded versions under {churn} never expire"
