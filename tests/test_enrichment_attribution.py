"""Owner finding (2026-10-19): enrichment_data must carry source attribution for historians."""

import json
import tempfile
import pathlib

from src.extraction import enrich_groups as eg


def test_group_enrichment_stamps_source():
    d = pathlib.Path(tempfile.mkdtemp())
    gf = d / "g.json"
    rec = {"name": "2nd Infantry Division", "GroupID": "01G"}
    gf.write_text(json.dumps(rec))
    enrichment = {
        "full_name": "2nd Infantry Division (US)",
        "unit_type": "division",
        "nationality": "USA",
    }
    eg._apply_group_enrichment(rec, enrichment, gf, "2nd Infantry Division")
    out = json.loads(gf.read_text())
    src = (out.get("enrichment_data") or {}).get("_source")
    assert src and src["type"] == "grok" and src["query"] == "2nd Infantry Division"
    assert "retrieved_at" in src
