from dataclasses import replace
from datetime import datetime

from analysis.decision_workspace import build_common_decision
from services.snapshot_service import SnapshotService
from test_snapshot_service import StubClient, StubMaster


NOW = datetime.fromisoformat("2026-07-20T11:30:00+05:30")


def _snapshot():
    return SnapshotService(StubClient(), StubMaster()).build(NOW)


def test_future_direction_owns_strategy_family():
    item = _snapshot()
    item.metadata["future_brain"] = {
        "current_direction": "UP", "current_strength": 70,
        "preferred_direction": "UP", "final_gate": "UP CONTINUATION WATCH",
        "up_15m": 67, "down_15m": 18, "range_15m": 15,
        "historical_matches": 0,
    }
    result = build_common_decision(item)
    assert result["best_strategy"] in {"PE SELL", "CE BUY"}
    assert result["direction"] == "UP"


def test_common_gate_never_enters_when_future_brain_says_wait():
    item = _snapshot()
    item.metadata["future_brain"] = {
        "current_direction": "UP", "current_strength": 80,
        "preferred_direction": "WAIT", "final_gate": "WAIT — NO CLEAR FUTURE EDGE",
        "up_15m": 40, "down_15m": 35, "range_15m": 25,
    }
    result = build_common_decision(item)
    assert result["entry_allowed"] is False
    assert result["final_action"] == "WAIT"
    assert result["trade_confidence"] < 55


def test_common_gate_is_reference_only_when_market_closed():
    item = _snapshot()
    item = replace(item, market_session=replace(item.market_session, is_live=False))
    item.metadata["future_brain"] = {
        "current_direction": "DOWN", "current_strength": 75,
        "preferred_direction": "DOWN", "final_gate": "DOWN CONTINUATION WATCH",
        "up_15m": 10, "down_15m": 75, "range_15m": 15,
    }
    result = build_common_decision(item)
    assert result["entry_allowed"] is False
    assert result["status"] == "REFERENCE ONLY"

