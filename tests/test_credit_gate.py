"""Tests for credit-aware submission gating (M4.2, spec §9.0/§9.1).

Correctness focus: an overspend means work is silently NOT processed, so the gate
must HOLD (return False) rather than let a batch submit beyond budget. When gating
is off or no budget is set, the gate is a no-op (True) so serial behavior is
unchanged.
"""

from unittest.mock import MagicMock, patch

from src.utils import credit_gate

# --- §9.1 cost estimation ---


def test_estimate_applies_batch_discount_for_grok_420():
    # 1M input @ $1.25 + 1M output @ $2.50 = $3.75, minus 20% batch = $3.00
    cost = credit_gate.estimate_cost_usd(
        1_000_000, 1_000_000, model="grok-4.20-0309-reasoning", is_batch=True
    )
    assert abs(cost - 3.00) < 1e-6


def test_estimate_no_discount_when_not_batch():
    cost = credit_gate.estimate_cost_usd(
        1_000_000, 1_000_000, model="grok-4.20-0309-reasoning", is_batch=False
    )
    assert abs(cost - 3.75) < 1e-6


def test_estimate_grok_46_has_no_batch_discount():
    # grok-4.6: 1M in @ $2 + 1M out @ $6 = $8.00, no batch discount
    cost = credit_gate.estimate_cost_usd(
        1_000_000, 1_000_000, model="grok-4.6", is_batch=True
    )
    assert abs(cost - 8.00) < 1e-6


def test_estimate_unknown_model_falls_back_to_default():
    cost = credit_gate.estimate_cost_usd(
        1_000_000, 0, model="mystery-model", is_batch=False
    )
    assert abs(cost - 1.25) < 1e-6  # default grok-4.20 input price


def test_config_pricing_override():
    cfg = {
        "api": {
            "grok": {
                "pricing": {
                    "grok-4.6": {"input": 1.0, "output": 1.0, "batch_discount": 0.0}
                }
            }
        }
    }
    cost = credit_gate.estimate_cost_usd(
        1_000_000, 1_000_000, model="grok-4.6", is_batch=True, config=cfg
    )
    assert abs(cost - 2.0) < 1e-6


# --- §9.0 reservation gate ---


def test_reserve_noop_when_gating_disabled():
    cfg = {"cost": {"credit_gating": False, "budget_usd": 10}}
    assert credit_gate.reserve(999.0, config=cfg) is True


def test_reserve_noop_when_no_budget():
    cfg = {"cost": {"credit_gating": True}}  # no budget_usd
    assert credit_gate.reserve(5.0, config=cfg) is True


def test_reserve_true_when_within_budget():
    cfg = {"cost": {"credit_gating": True, "budget_usd": 10}}
    table = MagicMock()
    with patch.object(credit_gate, "_table", return_value=table):
        assert credit_gate.reserve(3.0, config=cfg) is True
    table.update_item.assert_called_once()


def test_reserve_false_when_would_exceed_budget():
    cfg = {"cost": {"credit_gating": True, "budget_usd": 10}}
    table = MagicMock()

    class _CondFail(Exception):
        pass

    _CondFail.__name__ = "ConditionalCheckFailedException"
    table.update_item.side_effect = _CondFail()
    with patch.object(credit_gate, "_table", return_value=table):
        assert credit_gate.reserve(50.0, config=cfg) is False


def test_reserve_holds_on_unexpected_error():
    """Any non-conditional error => cannot confirm budget => HOLD (False)."""
    cfg = {"cost": {"credit_gating": True, "budget_usd": 10}}
    table = MagicMock()
    table.update_item.side_effect = RuntimeError("dynamo down")
    with patch.object(credit_gate, "_table", return_value=table):
        assert credit_gate.reserve(1.0, config=cfg) is False


def test_env_budget_override():
    cfg = {"cost": {"credit_gating": True, "budget_usd": 10}}
    table = MagicMock()
    with patch.dict("os.environ", {"GROK_BUDGET_USD": "100"}):
        with patch.object(credit_gate, "_table", return_value=table):
            assert credit_gate.reserve(50.0, config=cfg) is True
    # the condition value passed should reflect the env budget (100), not 10
    kwargs = table.update_item.call_args.kwargs
    assert kwargs["ExpressionAttributeValues"][":b"] == __import__("decimal").Decimal(
        "100"
    )


def test_current_spend_reads_counter():
    table = MagicMock()
    table.get_item.return_value = {
        "Item": {"cache_key": credit_gate.SPEND_KEY, "spend_usd": "4.25"}
    }
    with patch.object(credit_gate, "_table", return_value=table):
        assert credit_gate.current_spend_usd() == 4.25
