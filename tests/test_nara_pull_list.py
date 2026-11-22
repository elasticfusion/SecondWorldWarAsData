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


# the shape of a live catalog reply (naId 403177795, fetched 2026-11-22), trimmed
LIVE_RECORD = {
    "naId": 403177795,
    "title": "XIV Corps: 1-009",
    "levelOfDescription": "fileUnit",
    "ancestors": [
        {"distance": 1, "levelOfDescription": "series", "title": "World War II Operations Reports", "naId": 305275},
        {"distance": 2, "levelOfDescription": "recordGroup", "recordGroupNumber": 407,
         "title": "Records of the Adjutant General's Office", "naId": 703},
    ],
    "physicalOccurrences": [
        {
            "copyStatus": "Preservation",
            "referenceUnits": [{"name": "National Archives at College Park - Textual Reference",
                                "email": "Archives2reference@nara.gov"}],
            "mediaOccurrences": [{"specificMediaType": "Microfilm", "containerId": "Reel 1-009"}],
        }
    ],
    "digitalObjects": [],
}  # fmt: skip


def test_the_catalog_reply_gives_record_group_series_container_and_reading_room(
    monkeypatch,
):
    from src.enrichment import acquisition_fetchers as af

    monkeypatch.setattr(af, "_fetch_nara_record", lambda n, k: {"record": LIVE_RECORD})
    got = af.nara_digital_objects("403177795", "k")
    assert got["series_path"] == [
        "Records of the Adjutant General's Office",
        "World War II Operations Reports",
    ]
    assert got["record_group_number"] == 407
    assert got["series"] == {
        "title": "World War II Operations Reports",
        "naId": "305275",
    }
    assert got["containers"] == [
        {"container": "Reel 1-009", "media": "Microfilm", "copy_status": "Preservation"}
    ]
    assert got["reference_unit"]["email"] == "Archives2reference@nara.gov"


def test_the_pull_list_shows_the_box_or_reel_and_the_reading_room(
    tmp_path, monkeypatch
):
    from src.enrichment import acquisition_fetchers as af
    from src.enrichment import bibliography_finds as bf

    monkeypatch.setattr(af, "_fetch_nara_record", lambda n, k: {"record": LIVE_RECORD})
    details = bf.nara_download_details("https://catalog.archives.gov/id/403177795", "k")
    event = {
        "endpoint": "https://catalog.archives.gov/id/403177795",
        "request_params": {"download": details},
    }
    rec = _paper(A, naid="403177795", rg=None)  # the catalog supplies the record group
    (item,) = npl.build_items([rec], [event])
    assert item.record_group == 407 and item.reading_room.startswith(
        "National Archives at College Park"
    )
    npl.export_csv([item], tmp_path / "l.csv")
    npl.export_markdown([item], tmp_path / "l.md")
    row = next(csv.DictReader(open(tmp_path / "l.csv", encoding="utf-8")))
    assert row["containers"] == "Reel 1-009 (Microfilm, Preservation)"
    assert "Reel 1-009" in (tmp_path / "l.md").read_text()


def test_items_in_one_series_sort_by_container():
    def item(box):
        return npl.PullItem(item_id=f"nara-{box}", kind=npl.EXACT, record_group=407,
                            series_path=["RG 407", "Ops Reports"],
                            containers=[{"container": f"Box {box}", "media": "Paper"}])  # fmt: skip

    assert [i.item_id for i in sorted([item(9), item(3)], key=npl._visit_order)] == [
        "nara-3",
        "nara-9",
    ]


def test_box_ten_sorts_after_box_nine():
    def item(box):
        return npl.PullItem(item_id=f"nara-{box}", kind=npl.EXACT, record_group=407,
                            containers=[{"container": f"Box {box}"}])  # fmt: skip

    assert [i.item_id for i in sorted([item(10), item(9)], key=npl._visit_order)] == [
        "nara-9",
        "nara-10",
    ]


@pytest.mark.parametrize(
    "value,expected",
    [(407, 407), ("407", 407), (" 407 ", 407), ("²", None), ("①", None), ("1" * 5000, None),
     ("RG 407", None), ("1.5", None), ([407], None), (True, None), (-3, None), (None, None)],
)  # fmt: skip
def test_small_int_never_raises(value, expected):
    from src.enrichment.acquisition_fetchers import small_int

    assert small_int(value) == expected


@pytest.mark.parametrize(
    "odd",
    [
        {"ancestors": [{"distance": "abc", "title": "x", "levelOfDescription": "series"}]},
        {"ancestors": [{"distance": [1], "levelOfDescription": "recordGroup", "recordGroupNumber": "²"}]},
        {"ancestors": "nope", "physicalOccurrences": "nope"},
        {"ancestors": [None, 3, "x"], "physicalOccurrences": [None, {"mediaOccurrences": [None, {"containerId": 5}]}]},
        {"ancestors": [{"title": {"a": 1}}]},
        {},
    ],
)  # fmt: skip
def test_an_odd_catalog_reply_never_aborts_the_lookup(odd, monkeypatch):
    from src.enrichment import acquisition_fetchers as af

    record = {"naId": 1, "title": "t", **odd}
    monkeypatch.setattr(af, "_fetch_nara_record", lambda n, k: {"record": record})
    got = af.nara_digital_objects("1", "k")
    assert got is not None and isinstance(got["containers"], list)


def test_an_untitled_record_group_still_gives_its_number():
    from src.enrichment.acquisition_fetchers import nara_location

    rec = {
        "ancestors": [
            {
                "distance": 2,
                "levelOfDescription": "recordGroup",
                "recordGroupNumber": 407,
            }
        ]
    }
    assert nara_location(rec)["record_group_number"] == 407


def test_several_occurrences_are_listed_once_each_with_their_copy_status():
    from src.enrichment.acquisition_fetchers import nara_location

    rec = {
        "physicalOccurrences": [
            {"copyStatus": "Preservation", "mediaOccurrences": [{"containerId": "Reel 1-009", "specificMediaType": "Microfilm"}]},
            {"copyStatus": "Reference", "mediaOccurrences": [{"containerId": "Box 5", "specificMediaType": "Paper"},
                                                             {"containerId": "Box 5", "specificMediaType": "Paper"}]},
        ]
    }  # fmt: skip
    got = nara_location(rec)["containers"]
    assert [c["container"] for c in got] == ["Reel 1-009", "Box 5"]
    item = npl.PullItem(
        item_id="nara-1", kind=npl.EXACT, record_group=407, containers=got
    )
    assert (
        npl._container_label(item)
        == "Reel 1-009 (Microfilm, Preservation); Box 5 (Paper, Reference)"
    )


def test_more_than_twenty_containers_are_capped_and_said(caplog):
    from src.enrichment.acquisition_fetchers import MAX_CONTAINERS, nara_location

    rec = {
        "physicalOccurrences": [
            {"mediaOccurrences": [{"containerId": f"Box {i}"} for i in range(30)]}
        ]
    }
    assert len(nara_location(rec, "77")["containers"]) == MAX_CONTAINERS
    assert "OBSERV nara.containers_capped naId=77" in caplog.text


def test_a_file_unit_with_no_container_is_said(caplog):
    from src.enrichment.acquisition_fetchers import nara_location

    nara_location(
        {
            "levelOfDescription": "fileUnit",
            "physicalOccurrences": [{"copyStatus": "Reference"}],
        },
        "88",
    )
    assert "OBSERV nara.no_container naId=88" in caplog.text
    caplog.clear()
    nara_location({"levelOfDescription": "item"}, "89")  # online only: nothing to pull
    assert "no_container" not in caplog.text


def test_the_nearest_series_is_the_one_shown():
    from src.enrichment.acquisition_fetchers import nara_location

    rec = {"ancestors": [
        {"distance": 2, "levelOfDescription": "series", "title": "Outer", "naId": 1},
        {"distance": 1, "levelOfDescription": "series", "title": "Inner", "naId": 2},
    ]}  # fmt: skip
    assert nara_location(rec)["series"] == {"title": "Inner", "naId": "2"}


def test_a_find_queued_before_locations_were_kept_still_builds_and_exports(tmp_path):
    old_event = {
        "endpoint": NARA,
        "request_params": {"download": {"naId": "305271", "object_count": 0}},
    }
    newer = {"endpoint": NARA, "request_params": {"download": {"containers": [{"container": "Box 7"}],
                                                                "reference_unit": {"name": "Textual Reference"}}}}  # fmt: skip
    (item,) = npl.build_items([_paper(A)], [old_event])
    assert item.containers == [] and item.reading_room == ""
    npl.export_csv([item], tmp_path / "l.csv")
    npl.export_markdown([item], tmp_path / "l.md")
    assert "| - |" in (tmp_path / "l.md").read_text()
    # a later citation of the same record, whose find carries a location, fills it in
    items = {}
    npl._add_exact(
        items, _paper(A), {"naId": "305271"}, old_event["request_params"]["download"]
    )
    npl._add_exact(
        items, _paper(B), {"naId": "305271"}, newer["request_params"]["download"]
    )
    assert items["nara-305271"].containers[0]["container"] == "Box 7"
    assert items["nara-305271"].reading_room == "Textual Reference"


def test_natural_sort_handles_non_ascii_digits_and_no_digits():
    assert npl._natural("Box ²") and npl._natural("Reel") == [(1, 0, "reel")]


def test_non_text_reply_values_are_empty_and_copies_of_one_reel_both_listed():
    from src.enrichment.acquisition_fetchers import nara_location

    rec = {
        "ancestors": [{"distance": 1, "levelOfDescription": "series", "title": {"a": 1}}],
        "physicalOccurrences": [
            {"copyStatus": "Preservation", "mediaOccurrences": [{"containerId": "Reel 1", "specificMediaType": ["x"]}]},
            {"copyStatus": "Reference", "mediaOccurrences": [{"containerId": "Reel 1", "specificMediaType": ["x"]}]},
        ],
    }  # fmt: skip
    got = nara_location(rec)
    assert got["series"]["title"] == ""
    assert [(c["media"], c["copy_status"]) for c in got["containers"]] == [
        ("", "Preservation"),
        ("", "Reference"),
    ]
