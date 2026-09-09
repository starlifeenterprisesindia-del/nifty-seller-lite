from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from services.shadow_journal import ShadowJournalStore


def _snapshot(at, spot, entry_state="WAIT FOR BREAK / PULLBACK"):
    simple = {
        "regime": "BREAKDOWN CONTINUATION",
        "direction": "DOWN",
        "direction_strength": 72.0,
        "entry_readiness": 58.0,
        "entry_state": entry_state,
        "candidate_action": "CE SELL",
        "final_action": "WAIT",
        "trigger": "3m close below support",
        "blocks": {"barrier_entry": {"state": "UNDER ATTACK"}},
    }
    return NS(
        created_at=at,
        metadata={"simple_brain": simple, "common_decision": {"final_action": "WAIT"}},
        levels=NS(current_price=spot),
        nifty_quote={"last_price": spot},
        decision=NS(final_action="WAIT", market_direction="DOWN", decision_confidence=72.0),
        trade_plan=NS(selected_setup="CE SELL"),
        option_intelligence=NS(market_bias="BEARISH", confidence=80.0),
        big_player_activity=NS(direction="SELLING", score=68.0),
    )


def test_wait_decision_is_recorded_and_outcome_backfilled(tmp_path):
    store = ShadowJournalStore(tmp_path / "shadow.json")
    start = datetime(2026, 9, 9, 9, 30, tzinfo=timezone.utc)
    store.record_check(_snapshot(start, 23500.0), "WAIT FOR BREAK")
    rows = store.load_decisions()
    assert len(rows) == 1
    assert rows[0]["final_action"] == "WAIT"
    assert rows[0]["candidate_action"] == "CE SELL"

    store.record_check(_snapshot(start + timedelta(minutes=6), 23470.0), "WAIT FOR BREAK")
    rows = store.load_decisions()
    assert rows[0]["outcome_5m_points"] == -30.0
    assert rows[0]["outcome_5m_label"] == "DOWN"
