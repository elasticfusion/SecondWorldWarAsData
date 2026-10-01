"""Tests for the soft spend-alert in the credit gate (§9).

The gate already HOLDs work over budget (hard). This adds a SOFT heads-up as
accrued spend crosses each spend_alert_usd multiple ($10, $20, ...) — logged as
'SPEND ALERT', picked up by a CloudWatch metric filter -> alarm -> Slack.
"""

import logging
from unittest.mock import MagicMock, patch

from src.utils import credit_gate

_CFG = {"cost": {"credit_gating": True, "budget_usd": 100, "spend_alert_usd": 10}}


def _reserve_with_new_total(new_total, reserved, caplog):
    """Run reserve() with a table whose update returns new_total, capture logs."""
    table = MagicMock()
    table.update_item.return_value = {"Attributes": {"spend_usd": str(new_total)}}
    with patch.object(credit_gate, "_table", return_value=table):
        with caplog.at_level(logging.WARNING):
            credit_gate.reserve(reserved, config=_CFG)


def test_spend_alert_fires_on_crossing_threshold(caplog):
    # prev 8 -> new 12 crosses $10
    _reserve_with_new_total(12.0, 4.0, caplog)
    assert any("SPEND ALERT" in r.message for r in caplog.records)


def test_no_alert_when_within_same_band(caplog):
    # prev 2 -> new 6, both under $10, no crossing
    _reserve_with_new_total(6.0, 4.0, caplog)
    assert not any("SPEND ALERT" in r.message for r in caplog.records)


def test_spend_alert_rearms_at_next_multiple(caplog):
    # prev 19 -> new 21 crosses $20
    _reserve_with_new_total(21.0, 2.0, caplog)
    assert any("SPEND ALERT" in r.message for r in caplog.records)


def test_no_alert_within_higher_band(caplog):
    # prev 21 -> new 24, both in the $20-$30 band, no new crossing
    _reserve_with_new_total(24.0, 3.0, caplog)
    assert not any("SPEND ALERT" in r.message for r in caplog.records)


def test_spend_alert_threshold_none_disables(caplog):
    cfg = {"cost": {"credit_gating": True, "budget_usd": 100}}  # no spend_alert_usd
    table = MagicMock()
    table.update_item.return_value = {"Attributes": {"spend_usd": "50"}}
    with patch.object(credit_gate, "_table", return_value=table):
        with caplog.at_level(logging.WARNING):
            credit_gate.reserve(50.0, config=cfg)
    assert not any("SPEND ALERT" in r.message for r in caplog.records)


def test_spend_alert_never_breaks_reserve():
    """A malformed update response must not fail the reservation."""
    table = MagicMock()
    table.update_item.return_value = {"Attributes": {}}  # no spend_usd
    with patch.object(credit_gate, "_table", return_value=table):
        assert credit_gate.reserve(5.0, config=_CFG) is True
