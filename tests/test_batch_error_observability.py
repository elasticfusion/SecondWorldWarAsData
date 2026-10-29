"""A dropped entity batch must emit a LOUD canonical OBSERV <entity>.batch_error status=error
(not just a generic ERROR), so a parse-failure data-loss surfaces immediately rather than only
being caught by the end-of-phase yield gate (groups=0 fail_stop, XChannel-8 2026-10-29).
"""

import asyncio
import logging
from pathlib import Path
from unittest.mock import MagicMock

from src.extraction import batch_parallel as BP


def test_batch_failure_emits_canonical_observ(caplog):
    event_data = {"Event": {"EventID": "E123", "Sub-events": [{"Sub-eventID": "S1"}]}}
    grok = MagicMock()
    grok.extract_json.side_effect = ValueError(
        "Failed to parse JSON response: Extra data: line 1 column 4970"
    )

    with caplog.at_level(logging.ERROR):  # root — catches the 'observe' sink logger
        result = asyncio.run(
            BP._batch_extract(
                event_data,
                grok,
                Path("/tmp"),
                "people_groups",
                "peoplegroups",
                "header",
                "groups",
                "groups",
                (lambda o: "k"),
                (lambda o: {}),
            )
        )
    # dropped -> empty result (behavior preserved) BUT the canonical anomaly MUST have fired
    assert "OBSERV people_groups.batch_error" in caplog.text
    assert "status=error" in caplog.text and "event_id=E123" in caplog.text
