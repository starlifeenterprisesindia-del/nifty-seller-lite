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
    state = store.mark_trade(session_date=NOW.date(), action="PE SELL WITH HEDGE")
    assert state.trades_taken == 1
    assert state.day_locked is True
    assert state.last_outcome == "OPEN"

    with pytest.raises(ValueError, match="already used"):
        store.mark_trade(session_date=NOW.date(), action="CE SELL WITH HEDGE")

    state = store.mark_outcome(session_date=NOW.date(), outcome="TARGET / MANUAL EXIT")
    assert state.last_outcome == "TARGET / MANUAL EXIT"
    assert state.day_locked is True


def test_wait_cannot_be_marked_as_trade(tmp_path):
    store = DisciplineStore(tmp_path / "discipline.json")
    with pytest.raises(ValueError, match="WAIT"):
        store.mark_trade(session_date=NOW.date(), action="WAIT")
