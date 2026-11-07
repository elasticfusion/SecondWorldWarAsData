"""DRY P2.7 guard: the 3-way _mark_metadata_gap (ocr_merge_handler/phase0_convert/phase0_video) now
delegates to review_reject.mark_metadata_gap. Pin the shared marker + fail-safe + that all 3
delegate (video with its title-less note)."""

import src.ingestion.review_reject as rr


def test_mark_metadata_gap_calls_reject(monkeypatch):
    calls = []
    monkeypatch.setattr(
        rr,
        "reject_to_review",
        lambda s3, bucket, key, book, note, **k: calls.append(
            (bucket, key, book, note, k)
        ),
    )
    rr.mark_metadata_gap("s3", "mybucket", "k.md", "Book1", "us-east-1")
    assert len(calls) == 1
    bucket, key, book, note, kw = calls[0]
    assert (bucket, key, book) == ("mybucket", "k.md", "Book1")
    assert kw["category"] == "metadata" and kw["region"] == "us-east-1"
    assert "metadata not yet extracted" in note


def test_mark_metadata_gap_fail_safe(monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("s3 down")

    monkeypatch.setattr(rr, "reject_to_review", boom)
    # must not raise
    rr.mark_metadata_gap("s3", "b", "k", "Book", "r")  # no exception


def test_custom_note_passthrough(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        rr, "reject_to_review", lambda s3, b, k, bk, note, **kw: seen.update(note=note)
    )
    rr.mark_metadata_gap("s3", "b", "k", "Bk", "r", note="custom gap note")
    assert seen["note"] == "custom gap note"


def test_all_three_callers_delegate(monkeypatch):
    seen = []
    monkeypatch.setattr(rr, "mark_metadata_gap", lambda *a, **k: seen.append((a, k)))
    import lambda_handlers.ocr_merge_handler as ocr
    import phase0_convert as pc
    import phase0_video as pv

    ocr._mark_metadata_gap("s3", "k1", "B")
    pc._mark_metadata_gap("s3", "k2", "B")
    pv._mark_metadata_gap("s3", "k3", "B")
    assert len(seen) == 3
    # video passes a title-less note
    assert any("author/series/license empty" in (k.get("note") or "") for _a, k in seen)


# --- the ALERT must be actionable (owner rule: benign alerts are disallowed) ---------------------


def _alert_for(**kw):
    """Run the real mark_metadata_gap -> reject_to_review path; return (sns publish kwargs, marker)."""
    import json
    import os
    from unittest.mock import MagicMock, patch

    s3 = MagicMock()
    sns = MagicMock()
    with (
        patch.dict(os.environ, {"NOTIFICATION_TOPIC_ARN": "arn:aws:sns:us-east-1:1:t"}),
        patch("boto3.client", return_value=sns),
    ):
        rr.mark_metadata_gap(
            s3,
            "dev-wwii-data-pipeline",
            "contentrepository/USA-E-XChannel-8/USA-E-XChannel-8.html",
            "USA-E-XChannel-8",
            "us-east-1",
            **kw,
        )
    return sns.publish.call_args.kwargs, json.loads(
        s3.put_object.call_args.kwargs["Body"]
    )


def test_alert_names_the_fields_the_consequence_and_the_fix():
    pub, marker = _alert_for()
    msg = pub["Message"]
    assert pub["Subject"] == "WWII Pipeline: metadata missing for USA-E-XChannel-8"
    for field in ("author", "series", "chapter_title", "license", "copyright_date"):
        assert field in msg
    # what it breaks (claims verified against the code and the dev E2E)
    assert "Processing CONTINUES" in msg and "weather" in msg
    assert "Missing required book metadata" in msg
    # exactly what to do, with the real S3 path and the RIGHT phase: the parse hashes the author,
    # so Phase 1 must re-run to re-parse; re-running only Phase 2 would reuse the empty author.
    assert (
        "s3://dev-wwii-data-pipeline/contentrepository/USA-E-XChannel-8/chapter1/chapter1-meta.yaml"
        in msg
    )
    assert "re-run Phase 1" in msg and "re-run Phase 2" not in msg
    assert "do NOT re-upload the source" in msg  # re-upload would overwrite the fix
    assert '"book": "USA-E-XChannel-8"' in msg and '"phase": "1"' in msg
    assert "dev-wwii-trigger" in msg
    assert "Nothing needs re-routing" in msg
    # the old generic text told the operator to verify the type of a file that was never rejected
    assert "verify its true type" not in msg
    # marker is machine-readable too
    assert marker["missing_fields"][0] == "author"
    assert marker["meta_key"].endswith("chapter1-meta.yaml")
    assert marker["disposition"] == "rejected-metadata"


def test_long_book_name_cannot_make_the_alert_fail():
    """SNS rejects a Subject over 100 chars; publish would raise and the swallowed error would
    silently lose the alert. A real slug can be >65 chars."""
    import os
    from unittest.mock import MagicMock, patch

    book = "Army_Air_Forces_in_World_War_II_Volume_1_Plans_and_Early_Operations_January_1939"
    sns = MagicMock()
    with (
        patch.dict(os.environ, {"NOTIFICATION_TOPIC_ARN": "arn:aws:sns:us-east-1:1:t"}),
        patch("boto3.client", return_value=sns),
    ):
        rr.mark_metadata_gap(MagicMock(), "dev-wwii-x", "k.html", book, "us-east-1")
    subject = sns.publish.call_args.kwargs["Subject"]
    assert len(subject) <= 100 and subject.isascii() and "\n" not in subject
    assert book in sns.publish.call_args.kwargs["Message"]  # full name is in the body


def test_sns_subject_is_capped_ascii_single_line():
    out = rr._sns_subject("a\nb \u2014 " + "x" * 300)
    assert len(out) == 100 and out.isascii() and "\n" not in out and out.endswith("...")


def test_extra_cannot_overwrite_core_marker_fields():
    import json
    from unittest.mock import MagicMock

    s3 = MagicMock()
    rr.reject_to_review(
        s3,
        "b",
        "k",
        "B",
        "why",
        category="metadata",
        extra={"disposition": "evil", "x": 1},
    )
    body = json.loads(s3.put_object.call_args.kwargs["Body"])
    assert body["disposition"] == "rejected-metadata" and body["x"] == 1


def test_video_passes_its_own_field_list():
    pub, marker = _alert_for(fields=("author", "series", "license"))
    assert "chapter_title" not in pub["Message"].split("TO FIX")[0]
    assert marker["missing_fields"] == ["author", "series", "license"]


def test_real_rejects_keep_the_generic_text():
    """Genuine rejects (media mismatch, empty OCR) still get the 'verify and re-route' alert."""
    import os
    from unittest.mock import MagicMock, patch

    sns = MagicMock()
    with (
        patch.dict(os.environ, {"NOTIFICATION_TOPIC_ARN": "arn:aws:sns:us-east-1:1:t"}),
        patch("boto3.client", return_value=sns),
    ):
        rr.reject_to_review(
            MagicMock(),
            "b",
            "k.pdf",
            "B",
            "bytes say html",
            category="ocr-media-mismatch",
        )
    assert "verify its true type" in sns.publish.call_args.kwargs["Message"]
    assert sns.publish.call_args.kwargs["Subject"].endswith(
        "ocr-media-mismatch rejected to review"
    )


def test_unsafe_book_name_never_becomes_a_shell_command():
    """Defence in depth: the message embeds a copy-paste command, so an unsafe/unknown book must
    yield NO command (callers pass the slug, but the alert must not trust that)."""
    import os
    from unittest.mock import MagicMock, patch

    for book in ("x'; rm -rf ~ #", "unknown", "../../evil"):
        sns = MagicMock()
        with (
            patch.dict(
                os.environ, {"NOTIFICATION_TOPIC_ARN": "arn:aws:sns:us-east-1:1:t"}
            ),
            patch("boto3.client", return_value=sns),
        ):
            rr.mark_metadata_gap(MagicMock(), "dev-wwii-x", "k.html", book, "us-east-1")
        msg = sns.publish.call_args.kwargs["Message"]
        assert "aws lambda invoke" not in msg
        assert "could not be identified safely" in msg
