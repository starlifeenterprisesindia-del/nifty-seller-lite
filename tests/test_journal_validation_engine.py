from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from analysis.position_guardian import _close_price, _entry_price, calculate_position_guardian
from models import DisciplineState, MarketSession
from services.journal_research import (
    classify_alignment,
    diagnose_closed_trade,
    market_intelligence_candidate,
    seller_setup_for_direction,
)
from services.shadow_journal import _cooldown_ready, _lane_cap

IST = ZoneInfo("Asia/Kolkata")


def _snapshot_with_mie(mie):
    return SimpleNamespace(metadata={"market_intelligence": mie})


def test_market_intelligence_open_window_maps_bullish_to_pe_sell():
    snapshot = _snapshot_with_mie(
        {
            "direction": "BULLISH",
            "institutional_window": {
                "state": "OPEN",
                "direction": "BULLISH",
                "gates_ready": 6,
                "alert_eligible": True,
                "opportunity_score": 74,
            },
            "pressure_integrity": {},
        }
    )
    candidate = market_intelligence_candidate(snapshot)
    assert candidate is not None
    assert candidate["setup"] == "PE SELL"
    assert candidate["trigger_type"] == "INSTITUTIONAL WINDOW"


def test_market_intelligence_open_window_maps_bearish_to_ce_sell():
    snapshot = _snapshot_with_mie(
        {
            "institutional_window": {
                "state": "STRONG",
                "direction": "BEARISH",
                "gates_ready": 6,
                "alert_eligible": True,
                "opportunity_score": 81,
            },
            "pressure_integrity": {},
        }
    )
    candidate = market_intelligence_candidate(snapshot)
    assert candidate is not None
    assert candidate["setup"] == "CE SELL"


def test_pressure_research_trigger_requires_verified_plus_attack_support():
    good = _snapshot_with_mie(
        {
            "institutional_window": {"state": "CLOSED", "gates_ready": 3},
            "pressure_integrity": {
                "quality_state": "VERIFIED",
                "direction": "BEARISH",
                "quality_score": 78,
                "move_attack_state": "ATTACK",
                "barrier_attack_score": 72,
            },
        }
    )
    candidate = market_intelligence_candidate(good)
    assert candidate is not None
    assert candidate["trigger_type"] == "PRESSURE INTEGRITY"
    assert candidate["setup"] == "CE SELL"

    weak = _snapshot_with_mie(
        {
            "institutional_window": {"state": "CLOSED", "gates_ready": 3},
            "pressure_integrity": {
                "quality_state": "BUILDING",
                "direction": "BEARISH",
                "quality_score": 65,
                "move_attack_state": "ATTACK",
                "barrier_attack_score": 72,
            },
        }
    )
    assert market_intelligence_candidate(weak) is None


def test_liquidity_magnet_alone_never_creates_trade():
    snapshot = _snapshot_with_mie(
        {
            "institutional_window": {"state": "FORMING", "gates_ready": 5, "alert_eligible": False},
            "pressure_integrity": {"quality_state": "BUILDING", "direction": "BULLISH"},
            "liquidity": {"money_concentration": {"bias": "UPSIDE", "confidence": 95}},
        }
    )
    assert market_intelligence_candidate(snapshot) is None


def test_alignment_classification_and_seller_mapping():
    assert classify_alignment("UP", "BULLISH") == "ALIGNED"
    assert classify_alignment("DOWN", "BULLISH") == "CONFLICT"
    assert classify_alignment("RANGE", "BULLISH") == "INDEPENDENT"
    assert seller_setup_for_direction("UP") == "PE SELL"
    assert seller_setup_for_direction("DOWN") == "CE SELL"


def test_lane_caps_are_25_each():
    assert _lane_cap("ONE BRAIN") == 25
    assert _lane_cap("MARKET INTELLIGENCE") == 25


def test_research_cooldown_blocks_fast_duplicate_and_then_releases():
    now = datetime(2026, 10, 9, 10, 30, tzinfo=IST)
    snapshot = SimpleNamespace(created_at=now)
    rows = [{"opened_at": datetime(2026, 10, 9, 10, 28, tzinfo=IST).isoformat()}]
    ok, _ = _cooldown_ready(rows, snapshot)
    assert not ok
    rows = [{"opened_at": datetime(2026, 10, 9, 10, 24, tzinfo=IST).isoformat()}]
    ok, _ = _cooldown_ready(rows, snapshot)
    assert ok


def test_executable_entry_and_close_sides_are_conservative():
    leg = SimpleNamespace(bid=20.0, ask=20.5, last_price=20.25)
    assert _entry_price("SHORT", leg) == 20.0
    assert _entry_price("HEDGE", leg) == 20.5
    row = pd.Series({"top_bid_price": 19.5, "top_ask_price": 20.0})
    assert _close_price("SHORT", row) == 20.0
    assert _close_price("HEDGE", row) == 19.5


def test_sell_spread_guardian_pnl_uses_short_ask_and_hedge_bid():
    record = {
        "status": "OPEN",
        "action": "CE SELL",
        "expiry": "2026-10-13",
        "opened_at": datetime(2026, 10, 9, 10, 30, tzinfo=IST).isoformat(),
        "lots": 1,
        "lot_size": 65,
        "entry_spot": 22500.0,
        "entry_credit_points": 10.0,
        "entry_debit_points": None,
        "target_capture_points": 8.0,
        "target_exit_debit_points": 1.0,
        "stop_exit_debit_points": 20.0,
        "forced_exit_time": "15:15",
        "spot_invalidation_low": None,
        "spot_invalidation_high": None,
        "legs": [
            {"role": "SHORT", "side": "CE", "strike": 22600.0, "entry_price": 20.0},
            {"role": "HEDGE", "side": "CE", "strike": 22700.0, "entry_price": 10.0},
        ],
    }
    chain = pd.DataFrame(
        [
            {"side": "CE", "strike": 22600.0, "top_bid_price": 14.5, "top_ask_price": 15.0},
            {"side": "CE", "strike": 22700.0, "top_bid_price": 8.0, "top_ask_price": 8.5},
        ]
    )
    state = DisciplineState("2026-10-09", 1, False, "OPEN", "CE SELL", (), "READY", record)
    guardian = calculate_position_guardian(
        discipline_state=state,
        option_chain=chain,
        current_expiry="2026-10-13",
        current_spot=22490.0,
        market_session=MarketSession("LIVE", "LIVE", True, ""),
        option_chain_live=True,
        as_of=datetime(2026, 10, 9, 10, 35, tzinfo=IST),
    )
    # Close debit = 15 short ask - 8 hedge bid = 7; profit = (10-7)*65 = 195.
    assert guardian.current_debit_points == 7.0
    assert guardian.unrealized_pnl_rupees == 195.0


def test_buy_spread_guardian_pnl_uses_long_bid_and_short_ask():
    record = {
        "status": "OPEN",
        "action": "CE BUY",
        "expiry": "2026-10-13",
        "opened_at": datetime(2026, 10, 9, 10, 30, tzinfo=IST).isoformat(),
        "lots": 1,
        "lot_size": 65,
        "entry_spot": 22500.0,
        "entry_credit_points": None,
        "entry_debit_points": 10.0,
        "target_capture_points": 8.0,
        "target_exit_debit_points": 20.0,
        "stop_exit_debit_points": 1.0,
        "forced_exit_time": "15:15",
        "spot_invalidation_low": None,
        "spot_invalidation_high": None,
        "legs": [
            {"role": "LONG", "side": "CE", "strike": 22500.0, "entry_price": 15.0},
            {"role": "SHORT", "side": "CE", "strike": 22600.0, "entry_price": 5.0},
        ],
    }
    chain = pd.DataFrame(
        [
            {"side": "CE", "strike": 22500.0, "top_bid_price": 18.0, "top_ask_price": 18.5},
            {"side": "CE", "strike": 22600.0, "top_bid_price": 5.5, "top_ask_price": 6.0},
        ]
    )
    state = DisciplineState("2026-10-09", 1, False, "OPEN", "CE BUY", (), "READY", record)
    guardian = calculate_position_guardian(
        discipline_state=state,
        option_chain=chain,
        current_expiry="2026-10-13",
        current_spot=22520.0,
        market_session=MarketSession("LIVE", "LIVE", True, ""),
        option_chain_live=True,
        as_of=datetime(2026, 10, 9, 10, 35, tzinfo=IST),
    )
    # Close value = 18 long bid - 6 short ask = 12; profit = (12-10)*65 = 130.
    assert guardian.current_debit_points == 12.0
    assert guardian.unrealized_pnl_rupees == 130.0


def test_loss_diagnosis_explains_opposite_move_and_pressure_failure():
    entry = {
        "setup": "CE SELL",
        "signal_direction": "BEARISH",
        "entry_spot": 22500.0,
        "opened_at": datetime(2026, 10, 9, 10, 30, tzinfo=IST).isoformat(),
        "entry_context": {
            "liquidity_magnet_bias": "UPSIDE",
            "liquidity_magnet_confidence": 70,
            "pressure_quality_state": "VERIFIED",
        },
    }
    result = diagnose_closed_trade(
        entry,
        exit_context={"pressure_quality_state": "BUILD-UP FAILED", "flip_state": "FLIP WATCH"},
        current_spot=22530.0,
        gross_pnl=-1000.0,
        net_pnl=-1040.0,
        outcome="SL TRIGGERED",
        closed_at=datetime(2026, 10, 9, 10, 45, tzinfo=IST),
    )
    assert result["result_class"] == "LOSS"
    text = " ".join(result["diagnosis_factors"])
    assert "Direction failed" in text
    assert "BUILD-UP FAILED" in text
    assert "Liquidity conflict" in text


def test_profit_diagnosis_records_follow_through_and_alignment_evidence():
    entry = {
        "setup": "PE SELL",
        "signal_direction": "BULLISH",
        "entry_spot": 22500.0,
        "opened_at": datetime(2026, 10, 9, 10, 30, tzinfo=IST).isoformat(),
        "entry_context": {
            "pressure_quality_state": "VERIFIED",
            "institutional_window_state": "OPEN",
            "liquidity_magnet_bias": "UPSIDE",
            "liquidity_magnet_confidence": 70,
        },
    }
    result = diagnose_closed_trade(
        entry,
        exit_context={},
        current_spot=22540.0,
        gross_pnl=1200.0,
        net_pnl=1160.0,
        outcome="TARGET REACHED",
        closed_at=datetime(2026, 10, 9, 10, 50, tzinfo=IST),
    )
    assert result["result_class"] == "PROFIT"
    text = " ".join(result["diagnosis_factors"])
    assert "Direction follow-through" in text
    assert "Institutional Window" in text
    assert "Liquidity Magnet aligned" in text


def test_paper_monitor_keeps_closed_state_and_accepts_later_outcome_backfill(tmp_path):
    from services.paper_monitor import PaperMonitor

    monitor = PaperMonitor(tmp_path / "paper.json")
    opened = {
        "trade_id": "SH-MI-20261009-103000-1",
        "status": "OPEN",
        "session_date": "2026-10-09",
        "opened_at": "2026-10-09T10:30:00+05:30",
    }
    monitor.register([opened])
    closed = dict(opened, status="CLOSED", net_pnl_rupees=500.0, closed_at="2026-10-09T10:40:00+05:30")
    monitor.register([closed])
    # Later validation horizon arrives after the trade was already closed.
    later = dict(closed, directional_outcome_30m_points=22.0)
    rows = monitor.register([later])
    assert rows[0]["status"] == "CLOSED"
    assert rows[0]["directional_outcome_30m_points"] == 22.0

    # A stale open payload must not reopen the closed server record.
    rows = monitor.register([dict(opened, last_pnl_rupees=100.0)])
    assert rows[0]["status"] == "CLOSED"
