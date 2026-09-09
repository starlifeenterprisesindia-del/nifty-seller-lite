from types import SimpleNamespace as NS

from analysis.simple_brain import calculate_simple_brain


def _feed(use_state="LIVE", ok=True):
    return NS(use_state=use_state, ok=ok)


def _snapshot(*, rsi=26.0, support_state="APPROACHING", support_strength=58.0, support_break=62.0,
              support_distance=5.0, future=None):
    pa3 = NS(
        event="BEARISH CONTINUATION", structure="BEARISH LH/LL", atr14=9.0,
    )
    pa15 = NS(
        event="BREAKDOWN CONFIRMED", structure="RANGE", atr14=30.0,
    )
    price_action = NS(three_minute=pa3, fifteen_minute=pa15)
    core = NS(bullish_score=12.0, bearish_score=68.0, range_score=38.0, confidence=90.0)
    options = NS(
        bullish_score=18.0, bearish_score=72.0, range_score=10.0,
        confidence=84.0, status="READY",
    )
    volume3 = NS(status="READY", confidence=82.0, move_support="BEARISH MOVE CONFIRMED", price_direction="DOWN")
    volume15 = NS(status="READY", confidence=84.0, move_support="BEARISH MOVE CONFIRMED", price_direction="DOWN")
    volume = NS(three_minute=volume3, fifteen_minute=volume15)
    rows = tuple(NS(change_3m_pct=-0.5, change_pct=-0.8, official_weight_pct=5.0) for _ in range(9))
    heavyweights = NS(rows=rows)
    big_player = NS(direction="SELLING", score=70.0)
    support = NS(
        lower=23447.0, upper=23454.0, midpoint=23450.5,
        distance_points=support_distance, strength=support_strength,
        break_pressure=support_break, state=support_state,
    )
    next_support = NS(midpoint=23420.0)
    resistance = NS(lower=23466.0, upper=23481.0, midpoint=23473.0,
                    distance_points=11.0, strength=60.0, break_pressure=45.0, state="APPROACHING")
    barrier_map = NS(
        status="READY", current_price=23462.0,
        nearest_support=support, next_support=next_support,
        nearest_resistance=resistance, next_resistance=NS(midpoint=23500.0),
    )
    indicators = NS(three_minute=NS(rsi14=rsi))
    market_session = NS(is_live=True)
    feed_status = {
        "quotes": _feed(), "candles": _feed(), "option_chain": _feed(),
        "price_progression": _feed(ok=True),
    }
    return NS(
        price_action=price_action, core_evidence=core, option_intelligence=options,
        volume=volume, heavyweights=heavyweights, big_player_activity=big_player,
        barrier_map=barrier_map, indicators=indicators, nifty_quote={"last_price": 23462.0},
        market_session=market_session, feed_status=feed_status,
    ), (future or {"next_direction": "MIXED", "down_15m": 44.0, "up_15m": 35.0, "range_15m": 21.0})


def test_confirmed_breakdown_is_directional_not_range_blocked():
    snapshot, future = _snapshot()
    result = calculate_simple_brain(snapshot, future)
    assert result["regime"] == "BREAKDOWN CONTINUATION"
    assert result["direction"] == "DOWN"
    assert result["candidate_action"] == "CE SELL"
    assert result["direction_strength"] >= 54
    assert result["blocks"]["barrier_entry"]["state"] == "UNDER ATTACK"
    assert result["entry_state"] == "READY / BREAK TRIGGER"
    assert result["final_action"] == "WAIT"


def test_oversold_rsi_is_risk_note_not_direction_flip():
    snapshot, future = _snapshot(rsi=24.0)
    result = calculate_simple_brain(snapshot, future)
    assert result["direction"] == "DOWN"
    assert any("oversold" in note.lower() for note in result["risk_notes"])


def test_future_opposite_is_advisory_not_hard_veto():
    snapshot, _ = _snapshot(support_state="BROKEN", support_distance=14.0)
    future = {"next_direction": "UP", "up_15m": 61.0, "down_15m": 23.0, "range_15m": 16.0}
    result = calculate_simple_brain(snapshot, future)
    assert result["direction"] == "DOWN"
    assert not result["hard_blockers"]
    assert any("warning only" in note.lower() for note in result["risk_notes"])


def test_holding_support_returns_reason_based_wait():
    snapshot, future = _snapshot(
        support_state="HOLDING / STRONG", support_strength=82.0, support_break=38.0,
        support_distance=5.0,
    )
    result = calculate_simple_brain(snapshot, future)
    assert result["direction"] == "DOWN"
    assert result["entry_state"] == "WAIT FOR BREAK / PULLBACK"
    assert result["final_action"] == "WAIT"
    assert "Support" in result["trigger"]


def test_broken_support_can_release_take_now():
    snapshot, future = _snapshot(support_state="BROKEN", support_distance=14.0)
    snapshot.barrier_map.current_price = 23440.0
    snapshot.nifty_quote["last_price"] = 23440.0
    result = calculate_simple_brain(snapshot, future)
    assert result["direction"] == "DOWN"
    assert result["blocks"]["barrier_entry"]["state"] == "BROKEN"
    assert result["entry_state"] == "TAKE NOW"
    assert result["final_action"] == "CE SELL"
