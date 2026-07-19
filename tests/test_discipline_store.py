from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.discipline_store import DisciplineStore

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 7, 20, 10, 30, tzinfo=IST)


def test_signal_history_is_same_day_bounded_and_deduped(tmp_path):
    store = DisciplineStore(tmp_path / "discipline.json")
    state, appended = store.append_signal(
        captured_at=NOW,
        action="PE SELL WITH HEDGE",
        execution_status="READY",
    )
    assert appended is True
    assert len(state.signal_history) == 1

    state, appended = store.append_signal(
        captured_at=NOW,
        action="PE SELL WITH HEDGE",
        execution_status="READY",
    )
    assert appended is False
    assert len(state.signal_history) == 1

    later = NOW.replace(minute=31)
    state, appended = store.append_signal(
        captured_at=later,
        action="PE SELL WITH HEDGE",
        execution_status="READY",
    )
    assert appended is True
    assert len(state.signal_history) == 2


def test_one_trade_locks_day_and_outcome_is_recorded(tmp_path):
    store = DisciplineStore(tmp_path / "discipline.json")
    record = {
        "status": "OPEN",
        "opened_at": NOW.isoformat(),
        "action": "PE SELL WITH HEDGE",
        "lots": 1,
        "lot_size": 65,
        "legs": [{"role": "SHORT", "side": "PE", "strike": 24200, "entry_price": 20}],
    }
    state = store.mark_trade(
        session_date=NOW.date(),
        action="PE SELL WITH HEDGE",
        trade_record=record,
    )
    assert state.trades_taken == 1
    assert state.day_locked is True
    assert state.last_outcome == "OPEN"
    assert state.trade_record is not None
    assert state.trade_record["legs"][0]["strike"] == 24200

    with pytest.raises(ValueError, match="already used"):
        store.mark_trade(session_date=NOW.date(), action="CE SELL WITH HEDGE")

    state = store.mark_outcome(
        session_date=NOW.date(),
        outcome="TARGET / MANUAL EXIT",
        exit_debit_points=7.5,
        realized_pnl_rupees=292.5,
        captured_at=NOW,
    )
    assert state.last_outcome == "TARGET / MANUAL EXIT"
    assert state.day_locked is True
    assert state.trade_record is not None
    assert state.trade_record["status"] == "TARGET / MANUAL EXIT"
    assert state.trade_record["exit_debit_points"] == 7.5
    assert state.trade_record["realized_pnl_rupees"] == 292.5


def test_wait_cannot_be_marked_as_trade(tmp_path):
    store = DisciplineStore(tmp_path / "discipline.json")
    with pytest.raises(ValueError, match="WAIT"):
        store.mark_trade(session_date=NOW.date(), action="WAIT")


def test_signal_history_persists_market_memory_fields(tmp_path):
    store = DisciplineStore(tmp_path / "discipline.json")
    state, appended = store.append_signal(
        captured_at=NOW,
        action="PE SELL WITH HEDGE",
        execution_status="READY",
        ce_score=18.4,
        pe_score=76.2,
        condor_score=24.8,
        wait_need=22.0,
        signal_state="BULLISH DEVELOPING",
        market_direction="BULLISH",
        fake_move_risk=31.5,
        spot=24352.75,
    )
    assert appended is True
    sample = state.signal_history[-1]
    assert sample["market_direction"] == "BULLISH"
    assert sample["signal_state"] == "BULLISH DEVELOPING"
    assert sample["pe_score"] == 76.2
    assert sample["fake_move_risk"] == 31.5
    assert sample["spot"] == 24352.75
