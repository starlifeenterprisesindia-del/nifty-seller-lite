from datetime import datetime
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

from analysis.ai_move_tracker import apply_completed_3m_close, barrier_status, new_prediction, update_prediction

IST = ZoneInfo("Asia/Kolkata")


def snap(direction="UP", strength=68.0):
    level_r = NS(label="R1", side="RESISTANCE", lower=23370.0, upper=23380.0, midpoint=23375.0)
    level_s = NS(label="S1", side="SUPPORT", lower=23320.0, upper=23330.0, midpoint=23325.0)
    pa3 = NS(atr14=10.0, invalidation_level=23325.0)
    pa15 = NS(invalidation_level=23310.0)
    return NS(
        market_session=NS(is_live=True),
        metadata={"simple_brain": {"direction": direction, "direction_strength": strength}},
        barrier_map=NS(current_price=23350.0, nearest_resistance=level_r, next_resistance=None,
                       nearest_support=level_s, next_support=None),
        price_action=NS(three_minute=pa3, fifteen_minute=pa15),
        nifty_quote={"last_price": 23350.0},
        created_at=datetime(2026, 9, 21, 10, 0, tzinfo=IST),
        snapshot_id="S1",
    )


def test_prediction_locks_barrier_and_tracks_price_only():
    state = new_prediction(snap())
    assert state is not None
    assert state["barrier"]["lower"] == 23370.0
    out = update_prediction(state, current_price=23362.0, at=datetime(2026, 9, 21, 10, 6, tzinfo=IST))
    assert out["move_points"] == 12.0
    assert out["mfe_points"] == 12.0
    assert out["barrier"]["lower"] == 23370.0
    assert out["status"] == "ON TRACK"


def test_prediction_invalidates_on_structural_level():
    state = new_prediction(snap())
    out = update_prediction(state, current_price=23324.0, at=datetime(2026, 9, 21, 10, 6, tzinfo=IST))
    assert out["status"] == "INVALIDATED"
    assert out["closed"] is True


def test_no_prediction_outside_clean_journal_window():
    s = snap()
    s.created_at = datetime(2026, 9, 21, 9, 20, tzinfo=IST)
    assert new_prediction(s) is None


def test_barrier_touch_does_not_end_prediction_and_completed_close_confirms_break():
    state = new_prediction(snap())
    assert state is not None
    assert state["target_price"] > state["barrier"]["upper"]
    touched = update_prediction(
        state, current_price=23381.0, at=datetime(2026, 9, 21, 10, 6, tzinfo=IST)
    )
    assert touched["closed"] is False
    assert barrier_status(touched, 23381.0)[0] == "BREAK PENDING"
    confirmed = apply_completed_3m_close(
        touched, completed_close=23382.0, observed_at=datetime(2026, 9, 21, 10, 6, tzinfo=IST)
    )
    assert confirmed["barrier_confirmed"] is True
    assert barrier_status(confirmed, 23382.0)[0] == "BROKEN"
