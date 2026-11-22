"""The NARA pull list and scans added after a visit (owner 2026-11-21)."""

import csv
import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from src.enrichment import nara_pull_list as npl

A, B, C = (
    "01HX7YZABCDEFGHJKMNPQRSTVA",
    "01HX7YZABCDEFGHJKMNPQRSTVB",
    "01HX7YZABCDEFGHJKMNPQRSTVC",
)
NARA = "https://catalog.archives.gov/id/305271"


def _paper(bid, verbatim="XIV Corps AAR, Jul 44.", naid="305271", rg="RG 407"):
    return {
        "BibliographyID": bid,
        "title": "Unknown",
        "verbatim_reference": verbatim,
        "archive_reference_number": rg,
        "archive_physical_address": "NARA, College Park, MD",
        "resource_urls": [f"https://catalog.archives.gov/id/{naid}"],
        "search_status": "resolved",
        "availability": "archive",
        "mentions": [{"book": "USA-E-XChannel"}],
        "source_metadata": {
            "findings": [
                {
                    "url": f"https://catalog.archives.gov/id/{naid}",
                    "source": "nara_catalog",
                    "verified_by": "grok+human",
                    "naId": naid,
                    "digital_objects": 0,
                }
            ]
        },
    }


def _locate(
    bid, rg="RG 407", verbatim="Combat Interview, 35th Inf Div, Mortain, Aug 44."
):
    return {
        "BibliographyID": bid,
        "title": "Unknown",
        "verbatim_reference": verbatim,
        "archive_reference_number": rg,
        "search_status": "reference_identified",
        "mentions": [{"book": "USA-E-XChannel"}, {"book": "USA-E-XChannel"}],
    }


EVENT = {
    "endpoint": NARA,
    "provider": "nara-find",
    "request_params": {
        "download": {
            "nara_title": "XIV Corps: 4-004",
            "level": "fileUnit",
            "series_path": ["RG 407", "World War II Operations Reports"],
        }
    },
}


def test_exact_and_locate_first_items_in_visit_order():
    items = npl.build_items(
        [
            _locate(C, rg="RG 331"),
            _paper(A),
            _paper(B, verbatim="XIV Corps rpt."),
            _locate(B, rg="RG 165"),
        ],
        [EVENT],
    )
    assert [i.kind for i in items] == ["exact", "locate_first", "locate_first"]
    exact = items[0]
    assert (
        exact.item_id == "nara-305271" and exact.cited_by == 2
    )  # one record, two citations
    assert exact.series_path == ["RG 407", "World War II Operations Reports"]
    assert exact.title == "XIV Corps: 4-004" and exact.record_group == 407
    assert [i.record_group for i in items[1:]] == [165, 331]  # by record group
    assert items[1].item_id == f"rg165-{B}"


def test_records_with_a_copy_or_scans_are_not_on_the_list():
    online = _paper(A)
    online["source_metadata"]["findings"][0]["digital_objects"] = 12
    held = _locate(B)
    held["download_path"] = "contentrepository/nara-scans/x.pdf"
    no_rg = _locate(C, rg=None)
    assert npl.build_items([online, held, no_rg]) == []


def test_statuses_history_and_bad_input(tmp_path):
    with mock_aws():
        table = _table()
        npl.set_status(table, "nara-305271", "requested", "D. C.")
        row = npl.set_status(table, "nara-305271", "pulled", "D. C.", "Box 12")
        assert [h["status"] for h in row["history"]] == ["requested", "pulled"]
        (item,) = npl.apply_statuses(npl.build_items([_paper(A)], [EVENT]), table)
        assert (item.status, item.notes) == ("pulled", "Box 12")
        for bad in (
            ("x", "pulled", "D"),
            ("nara-1", "lost", "D"),
            ("nara-1", "pulled", " "),
        ):
            with pytest.raises(ValueError):
                npl.set_status(table, *bad)


def test_exports_are_safe_and_complete(tmp_path):
    rec = _locate(A, verbatim='=HYPERLINK("http://evil") Combat Interview, Aug 44.')
    items = npl.build_items([_paper(B), rec], [EVENT])
    npl.export_csv(items, tmp_path / "l.csv")
    npl.export_markdown(items, tmp_path / "l.md")
    rows = list(csv.DictReader(open(tmp_path / "l.csv", encoding="utf-8")))
    assert rows[0]["catalog_url"] == NARA and rows[0]["BibliographyIDs"] == B
    assert all(
        not r["citations"].startswith("=") for r in rows
    )  # no spreadsheet formula
    md = (tmp_path / "l.md").read_text()
    assert "### RG 407" in md and "Locate first" in md and "nara-305271" in md


def test_scan_slug_and_file_checks(tmp_path):
    (item,) = npl.build_items([_paper(A)], [EVENT])
    assert npl.scan_slug(item, A, None) == "NARA-RG407-305271"
    assert npl.scan_slug(item, A, 2) == "NARA-RG407-305271-p2"
    assert npl.scan_slug(None, A, None) == "NARA-RG-" + A[-10:]
    f = tmp_path / "s.pdf"
    f.write_bytes(b"%PDF-1.4 x")
    info = npl.check_scan_file(f)
    assert info["suffix"] == ".pdf" and len(info["sha256"]) == 64
    for bad in (tmp_path / "none.pdf", tmp_path / "s.exe"):
        if bad.suffix == ".exe":
            bad.write_bytes(b"x")
        with pytest.raises(ValueError):
            npl.check_scan_file(bad)


def test_binding_validates_its_provenance(tmp_path):
    (item,) = npl.build_items([_paper(A)], [EVENT])
    kw = dict(
        slug="NARA-RG407-305271",
        key="contentrepository/nara-scans/NARA-RG407-305271.pdf",
        bibliography_id=A,
        item=item,
        file_info={"sha256": "a" * 64, "size": 10},
        by="D. C.",
        scanned_on="2026-12-03",
        us_government_record=True,
    )
    row = npl.binding(**kw)
    assert row["license"] == "public_domain" and row["naId"] == "305271"
    assert npl.binding(**{**kw, "us_government_record": False})["license"] is None
    for bad in (
        {"bibliography_id": "x"},
        {"scanned_on": "Dec 3"},
        {"by": ""},
        {"key": "contentrepository/nara-scans/other.pdf"},
    ):
        with pytest.raises(ValueError):
            npl.binding(**{**kw, **bad})


def _scan_row(bid, slug, suffix=".pdf", **kw):
    return {
        "BibliographyID": bid,
        "slug": slug,
        "key": f"contentrepository/nara-scans/{slug}{suffix}",
        "scanned_on": "2026-12-03",
        "sha256": "a" * 64,
        "by": "D. C.",
        "license": None,
        "naId": None,
        **kw,
    }


def test_a_scan_binds_its_record_and_an_absorbed_id():
    scans = {B: [_scan_row(B, "NARA-RG407-1", naId="1", license="public_domain")]}
    rec = {**_locate(A), "merged_from": [B]}
    assert npl.apply_scan(rec, scans) is True
    assert rec["search_status"] == "held_unprocessed" and rec["availability"] == "local"
    assert (
        rec["download_path"].endswith("NARA-RG407-1.pdf")
        and rec["license"] == "public_domain"
    )
    assert rec["source_metadata"]["findings"][0]["verified_by"] == "human"
    assert npl.apply_scan(rec, scans) is False  # already bound: nothing to write


def test_the_resolver_binds_a_scan_and_does_not_search(tmp_path):
    from unittest.mock import patch

    import src.enrichment.bibliography_resolver as br

    f = tmp_path / "bib_1.json"
    f.write_text(json.dumps(_locate(A)))
    scans = {A: [_scan_row(A, "NARA-RG407-x")]}
    with patch.object(br, "_search_online_sources") as search:
        stats = br.resolve_bibliography_dir(
            tmp_path,
            object(),
            {"holdings_index": {}, "bib_finds": {}, "bib_scans": scans},
        )
    assert search.call_count == 0 and stats["held_unprocessed"] == 1
    assert json.loads(f.read_text())["download_path"].endswith("NARA-RG407-x.pdf")


# --- the operator script, end to end against mocked AWS ----------------------------------------


def _table():
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    ddb.create_table(
        TableName="dev-wwii-api-cache",
        KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
        TableName="dev-wwii-id-aliases",
        KeySchema=[{"AttributeName": "alias_key", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "alias_key", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    return ddb.Table("dev-wwii-api-cache")


@pytest.fixture
def aws(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        table = _table()
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="dev-wwii-data-pipeline")
        for n, rec in enumerate((_paper(A), _locate(B))):
            s3.put_object(
                Bucket="dev-wwii-data-pipeline",
                Key=f"output/bibliography/bib_{n}.json",
                Body=json.dumps(rec),
            )
        from src.utils import id_aliases

        id_aliases._TABLE.clear()
        monkeypatch.setenv("ID_ALIAS_TABLE", "dev-wwii-id-aliases")
        yield table, s3


def _run(*argv):
    import scripts.nara_pull_list as cli

    return cli.main(list(argv))


def test_build_writes_the_lists(aws, tmp_path):
    assert _run("build", "--env", "dev", "--out", str(tmp_path / "v")) == 0
    rows = list(
        csv.DictReader(open(tmp_path / "v" / "nara_pull_list.csv", encoding="utf-8"))
    )
    assert [r["item_id"] for r in rows] == ["nara-305271", f"rg407-{B}"]


def test_add_scan_writes_provenance_then_uploads_and_marks_the_item(aws, tmp_path):
    table, s3 = aws
    scan = tmp_path / "box.pdf"
    scan.write_bytes(b"%PDF-1.4 scan")
    args = ("add-scan", "--env", "dev", "--bib", A, "--file", str(scan), "--by", "D. C.",
            "--scanned-on", "2026-12-03", "--item", "nara-305271", "--us-government-record")  # fmt: skip
    assert _run(*args, "--dry-run") == 0
    assert not table.get_item(Key={"cache_key": "pullscan#NARA-RG407-305271"}).get(
        "Item"
    )
    assert _run(*args) == 0
    row = json.loads(
        table.get_item(Key={"cache_key": "pullscan#NARA-RG407-305271"})["Item"][
            "response"
        ]
    )
    assert (
        row["BibliographyID"] == A
        and row["license"] == "public_domain"
        and len(row["sha256"]) == 64
    )
    head = s3.head_object(
        Bucket="dev-wwii-data-pipeline",
        Key="contentrepository/nara-scans/NARA-RG407-305271.pdf",
    )
    assert head["Metadata"]["bibliography-id"] == A
    status = json.loads(
        table.get_item(Key={"cache_key": "pull#nara-305271"})["Item"]["response"]
    )
    assert status["status"] == "scanned"
    with pytest.raises(SystemExit, match="in use"):
        _run(*args)  # a slug is immutable: a second file needs --part
    assert npl.scan_bindings(table)[A][0]["slug"] == "NARA-RG407-305271"


def test_add_scan_refuses_a_record_the_item_does_not_serve(aws, tmp_path):
    scan = tmp_path / "box.pdf"
    scan.write_bytes(b"%PDF")
    with pytest.raises(SystemExit, match="does not serve"):
        _run("add-scan", "--env", "dev", "--bib", B, "--file", str(scan), "--by", "D",
             "--scanned-on", "2026-12-03", "--item", "nara-305271")  # fmt: skip


def test_a_failed_upload_removes_the_provenance_row(aws, tmp_path, monkeypatch):
    table, s3 = aws
    scan = tmp_path / "box.pdf"
    scan.write_bytes(b"%PDF")
    import botocore.client

    real = botocore.client.BaseClient._make_api_call

    def fail_put(self, op, params):
        if op in ("PutObject", "CreateMultipartUpload"):
            raise RuntimeError("upload failed")
        return real(self, op, params)

    monkeypatch.setattr(botocore.client.BaseClient, "_make_api_call", fail_put)
    with pytest.raises(Exception):
        _run("add-scan", "--env", "dev", "--bib", B, "--file", str(scan), "--by", "D",
             "--scanned-on", "2026-12-03")  # fmt: skip
    monkeypatch.setattr(botocore.client.BaseClient, "_make_api_call", real)
    assert not table.get_item(Key={"cache_key": f"pullscan#NARA-RG-{B[-10:]}"}).get(
        "Item"
    )


def test_a_merged_away_id_is_bound_to_the_record_that_absorbed_it(aws, tmp_path):
    from src.utils import id_aliases

    assert id_aliases.record("bibliography", C, B, "test")
    scan = tmp_path / "box.pdf"
    scan.write_bytes(b"%PDF")
    assert _run("add-scan", "--env", "dev", "--bib", C, "--file", str(scan), "--by", "D",
                "--scanned-on", "2026-12-03") == 0  # fmt: skip
    table, _ = aws
    assert npl.scan_bindings(table)[B][0]["BibliographyID"] == B


def test_a_bound_record_stays_held_and_unsearched_every_run(tmp_path):
    from unittest.mock import patch

    import src.enrichment.bibliography_resolver as br

    f = tmp_path / "bib_1.json"
    f.write_text(json.dumps(_locate(A)))
    cfg = {
        "holdings_index": {},
        "bib_finds": {},
        "bib_scans": {A: [_scan_row(A, "NARA-RG407-x")]},
    }
    with patch.object(br, "_search_online_sources") as search:
        for _ in range(3):
            stats = br.resolve_bibliography_dir(tmp_path, object(), dict(cfg))
            assert stats["held_unprocessed"] == 1
    assert search.call_count == 0
    assert json.loads(f.read_text())["search_status"] == "held_unprocessed"


def test_a_better_copy_on_the_record_is_kept(caplog):
    rec = {**_paper(A), "download_path": "source/archive_org/x.pdf"}
    assert npl.apply_scan(rec, {A: [_scan_row(A, "NARA-RG407-y")]}) is True
    assert rec["download_path"] == "source/archive_org/x.pdf"  # kept
    assert rec["search_status"] == "resolved"
    assert any(
        f.get("source") == "nara_research_visit"
        for f in rec["source_metadata"]["findings"]
    )
    assert "OBSERV nara_pull.scan_not_primary" in caplog.text


def test_every_part_of_a_scan_is_a_finding():
    rec = _locate(A)
    rows = [_scan_row(A, "NARA-RG407-z-p1"), _scan_row(A, "NARA-RG407-z-p2")]
    npl.apply_scan(rec, {A: rows})
    urls = [f["url"] for f in rec["source_metadata"]["findings"]]
    assert len(urls) == 2 and rec["download_path"].endswith("-p1.pdf")


def test_a_record_holding_its_scan_leaves_the_pull_list():
    rec = {
        **_paper(A),
        "search_status": "held_unprocessed",
        "download_path": "contentrepository/nara-scans/NARA-RG407-305271.pdf",
    }
    assert npl.build_items([rec], [EVENT]) == []


@pytest.mark.parametrize(
    "bad",
    [
        {"key": "contentrepository/nara-scans/../../etc/x.pdf"},
        {"key": "contentrepository/nara-scans/a/b.pdf"},
        {"key": "contentrepository/nara-scans/NARA-RG407-x.exe"},
        {"slug": "other", "key": "contentrepository/nara-scans/other.pdf"},
        {"license": "cc-by"},
        {"scanned_on": "soon"},
        {"sha256": "x"},
        {"naId": "../1"},
        {"BibliographyID": "x"},
    ],
)
def test_a_forged_or_malformed_row_is_never_used(bad, caplog):
    with mock_aws():
        table = _table()
        row = {**_scan_row(A, "NARA-RG407-x"), **bad}
        table.put_item(Item={"cache_key": "pullscan#x", "response": json.dumps(row)})
        assert npl.scan_bindings(table) == {}
    assert "OBSERV nara_pull.scan_row_invalid" in caplog.text


def test_the_same_file_already_ingested_under_another_slug_is_refused(aws, tmp_path):
    table, _ = aws
    scan = tmp_path / "box.pdf"
    scan.write_bytes(b"%PDF dup")
    import hashlib

    sha = hashlib.sha256(b"%PDF dup").hexdigest()
    table.put_item(
        Item={"cache_key": f"source#sha256:{sha}", "title": "Some_Other_Book"}
    )
    with pytest.raises(SystemExit, match="already ingested"):
        _run("add-scan", "--env", "dev", "--bib", B, "--file", str(scan), "--by", "D",
             "--scanned-on", "2026-12-03")  # fmt: skip


def test_an_interrupted_upload_removes_the_provenance_row(aws, tmp_path, monkeypatch):
    table, s3 = aws
    scan = tmp_path / "box.pdf"
    scan.write_bytes(b"%PDF x")
    import scripts.nara_pull_list as cli

    real_aws = cli._aws

    def interrupted(env, region):
        t, client, bucket = real_aws(env, region)

        class S3:
            def __getattr__(self, name):
                return getattr(client, name)

            def upload_file(self, *a, **k):
                raise KeyboardInterrupt

        return t, S3(), bucket

    monkeypatch.setattr(cli, "_aws", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _run("add-scan", "--env", "dev", "--bib", B, "--file", str(scan), "--by", "D",
             "--scanned-on", "2026-12-03")  # fmt: skip
    assert not table.get_item(Key={"cache_key": f"pullscan#NARA-RG-{B[-10:]}"}).get(
        "Item"
    )


def test_part_must_be_positive(aws, tmp_path):
    with pytest.raises(SystemExit):
        _run("add-scan", "--env", "dev", "--bib", B, "--file", "x.pdf", "--by", "D",
             "--scanned-on", "2026-12-03", "--part", "0")  # fmt: skip


def test_markdown_cells_cannot_carry_markup(tmp_path):
    rec = _locate(A, verbatim="<script>x</script> [link](http://evil) | pipe Aug 44.")
    npl.export_markdown(npl.build_items([rec]), tmp_path / "l.md")
    md = (tmp_path / "l.md").read_text()
    assert "<script>" not in md and "](http" not in md and "\\|" in md


def test_an_approved_online_copy_resolves_a_held_record(tmp_path):
    import src.enrichment.bibliography_resolver as br

    rec = {**_locate(A), "search_status": "held_unprocessed",
           "download_path": "contentrepository/nara-scans/NARA-RG407-x.pdf"}  # fmt: skip
    f = tmp_path / "bib_1.json"
    f.write_text(json.dumps(rec))
    approved = {
        "endpoint": "https://archive.org/details/x",
        "review_status": "affirmed",
        "request_params": {"search_source": "archive_org"},
    }
    cfg = {
        "holdings_index": {},
        "bib_finds": {A: [approved]},
        "bib_scans": {A: [_scan_row(A, "NARA-RG407-x")]},
    }
    br.resolve_bibliography_dir(tmp_path, object(), cfg)
    out = json.loads(f.read_text())
    assert out["search_status"] == "resolved" and out["resource_urls"] == [
        "https://archive.org/details/x"
    ]


def test_a_null_source_metadata_does_not_crash_the_binding():
    rec = {**_locate(A), "source_metadata": None}
    assert npl.apply_scan(rec, {A: [_scan_row(A, "NARA-RG407-x")]}) is True
