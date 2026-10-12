"""Architectural invariant: OpenSERP (live web search) is PHASE 3 ONLY.

Phase 2 is limited to Grokipedia/Wikipedia + local-corpus work; all
non-Grokipedia/Wikipedia internet search (OpenSERP) happens in Phase 3, so
OpenSERP/NAT come up exactly once and don't bounce between phases. These tests
guard the rule against regression.
"""

# pylint: disable=missing-function-docstring,import-error

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import phase2_extract as p2

_P2 = Path("phase2_extract.py").read_text(encoding="utf-8")
_P3 = Path("phase3_enrich_data.py").read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    # Drop full-line comments + docstrings-ish lines so we test CODE, not prose
    # mentioning OpenSERP. Good enough: remove lines whose stripped form starts
    # with '#' and collapse triple-quoted blocks heuristically is overkill — the
    # key tokens we assert on are call expressions, not comments.
    out = []
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def test_phase2_source_has_no_openserp_call_path():
    code = _strip_comments(_P2)
    # Assert on FUNCTIONAL call-path tokens — not the bare word "openserp", which
    # legitimately appears in docstrings explaining WHY it moved to Phase 3.
    assert "7001" not in code, "Phase 2 must not probe the OpenSERP port"
    assert (
        "import_openserp_maps" not in code
    ), "Phase 2 must not call OpenSERP map import (moved to Phase 3)"
    assert "get_openserp_url" not in code, "Phase 2 must not resolve an OpenSERP URL"
    assert (
        "openserp_url" not in code
    ), "Phase 2 must not pass an openserp_url to any call"
    assert (
        "/mega/search" not in code
    ), "Phase 2 must not hit the OpenSERP search endpoint"


def test_phase3_owns_the_external_maps_openserp_import():
    # The moved capability lives in Phase 3 now.
    assert "import_openserp_maps" in _P3
    assert "openserp_external_maps" in _P3


def test_extract_external_maps_does_no_openserp_probe(tmp_path):
    """Behavioral: with external_maps enabled but only a local YAML present,
    _extract_external_maps imports the local corpus and never touches the
    network / OpenSERP."""
    paths = {"output_root": tmp_path}
    config = {"external_maps": {"enabled": True}}
    logger = MagicMock()
    # If any OpenSERP/requests path were hit, this patch target would be called.
    with (
        patch("requests.get") as req_get,
        patch("src.extraction.external_maps.import_maps", return_value=0),
    ):
        # base_dir has no external_maps.yaml -> nothing imported, no network.
        p2._extract_external_maps(tmp_path, MagicMock(), paths, config, logger)
    req_get.assert_not_called()


def test_entrypoint_starts_openserp_only_for_phase3():
    """OpenSERP (browser service) is PHASE 3 ONLY — _start_openserp_if_needed is a
    no-op for phase0/1/2 (no AWS client touched)."""
    import os

    os.environ.setdefault("S3_BUCKET", "test-bucket")
    os.environ.setdefault("ENV_NAME", "test")
    import ecs_entrypoint as ee

    with patch.object(ee, "boto3") as b3:
        for phase in ("phase0_ingest.py", "phase1_parse.py", "phase2_extract.py"):
            ee._start_openserp_if_needed(phase)
        b3.client.assert_not_called()


def test_entrypoint_ensures_nat_for_phase2_and_phase3_not_phase01():
    """NAT is DECOUPLED from OpenSERP: it must come up for phase2 AND phase3
    (Phase 2 needs the internet for Grokipedia/Wikipedia + the Grok API — there
    is no VPC endpoint for those), but NOT for the local phase0/1."""
    import os
    import io
    import json as _json

    os.environ.setdefault("S3_BUCKET", "test-bucket")
    os.environ.setdefault("ENV_NAME", "test")
    import ecs_entrypoint as ee

    def _ready_resp():
        # Mimic a successful nat-manager invoke: no FunctionError, status=ready.
        return {"Payload": io.BytesIO(_json.dumps({"status": "ready"}).encode())}

    # phase0/1 -> no NAT (no AWS client touched at all)
    with patch.object(ee, "boto3") as b3:
        ee._ensure_nat_if_needed("phase0_ingest.py")
        ee._ensure_nat_if_needed("phase1_parse.py")
        b3.client.assert_not_called()

    # phase2 -> NAT create invoked (Grok/Wiki need internet)
    with patch.object(ee, "boto3") as b3:
        b3.client.return_value.invoke.return_value = _ready_resp()
        ee._ensure_nat_if_needed("phase2_extract.py")
        b3.client.return_value.invoke.assert_called_once()
        payload = b3.client.return_value.invoke.call_args.kwargs["Payload"]
        assert b"create" in payload

    # phase3 -> NAT create invoked too
    with patch.object(ee, "boto3") as b3:
        b3.client.return_value.invoke.return_value = _ready_resp()
        ee._ensure_nat_if_needed("phase3_enrich_data.py")
        b3.client.return_value.invoke.assert_called_once()


def test_ensure_nat_fail_loud_on_not_ready(caplog):
    """A not_ready nat-manager result must be surfaced LOUDLY (ERROR), not
    swallowed — otherwise phase egress failure shows up only as opaque downstream
    Grok/Wiki errors."""
    import os
    import io
    import json as _json
    import logging

    os.environ.setdefault("S3_BUCKET", "test-bucket")
    os.environ.setdefault("ENV_NAME", "test")
    import ecs_entrypoint as ee

    with patch.object(ee, "boto3") as b3:
        b3.client.return_value.invoke.return_value = {
            "Payload": io.BytesIO(
                _json.dumps({"status": "not_ready", "missing": ["nat"]}).encode()
            )
        }
        with caplog.at_level(logging.ERROR):
            ee._ensure_nat_if_needed("phase2_extract.py")
    assert any("NAT NOT READY" in r.message for r in caplog.records)


def test_phase2_handler_has_no_openserp_startup():
    handler_src = Path("lambda_handlers/phase2_handler.py").read_text(encoding="utf-8")
    code = _strip_comments(handler_src)
    assert "get_openserp_url" not in code
    assert "openserp_url" not in code
    assert "7001" not in code
