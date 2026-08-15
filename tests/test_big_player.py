from datetime import datetime
from types import SimpleNamespace as NS

import pandas as pd

from analysis.big_player import calculate_big_player_activity


def _inputs(direction: str = "DOWN") -> dict:
    closes = [24350.0, 24345.0, 24338.0] if direction == "DOWN" else [24338.0, 24345.0, 24350.0]
    future = pd.DataFrame(
        [
            {"timestamp": f"2026-08-13 13:2{index}", "close": close, "open_interest": 100000 + index * 1500, "is_complete": True}
            for index, close in enumerate(closes)
        ]
    )
    selling = direction == "DOWN"
    return {
        "as_of": datetime(2026, 8, 13, 13, 30),
        "market_session": NS(is_live=True),
        "volume": NS(
            status="READY",
            three_minute=NS(status="READY", relative_volume=2.1, price_direction=direction),
        ),
        "future_candles_1m": future,
        "options": NS(
            bullish_score=20 if selling else 75,
            bearish_score=75 if selling else 20,
            confidence=80,
            market_bias="BEARISH" if selling else "BULLISH",
            status="READY",
        ),
        "heavyweights": NS(
            state="BROAD BEARISH" if selling else "BROAD BULLISH",
            weighted_move_pct=-0.4 if selling else 0.4,
        ),
        "barrier_map": NS(
            nearest_resistance=NS(distance_points=8),
            nearest_support=NS(distance_points=8),
        ),
        "core": NS(market_state="BULLISH" if selling else "BEARISH"),
    }


def test_confirmed_large_selling_flags_reversal_danger():
    result = calculate_big_player_activity(
        **_inputs("DOWN"),
        history=[
            {"direction": "SELLING", "score": 82},
            {"direction": "SELLING", "score": 88},
        ],
    )
    assert result.direction == "SELLING"
    assert result.confirmation_count == 2
    assert result.score >= 75
    assert result.reversal_risk in {"HIGH", "DANGER"}


def test_large_buying_uses_green_direction_and_same_brain_inputs():
    result = calculate_big_player_activity(
        **_inputs("UP"),
        history=[{"direction": "BUYING", "score": 80}],
    )
    assert result.direction == "BUYING"
    assert result.confirmation_count == 2
    assert result.futures_setup == "LONG BUILD-UP"
    assert result.activity_type == "LONG BUILD-UP"


def test_single_snapshot_stays_warming_up():
    result = calculate_big_player_activity(**_inputs("DOWN"), history=[])
    assert result.confirmation_count == 1
    assert result.persistence == "WARMING UP"


def test_same_completed_minute_is_not_counted_twice():
    result = calculate_big_player_activity(
        **_inputs("UP"),
        observation_key="bar-1",
        history=[
            {
                "direction": "BUYING",
                "score": 80,
                "observation_key": "bar-1",
                "spot": 24350,
            }
        ],
    )
    assert result.confirmation_count == 1
    assert result.confirmation_total == 1


def test_small_opposite_move_does_not_flip_direction_immediately():
    result = calculate_big_player_activity(
        **_inputs("DOWN"),
        history=[
            {
                "direction": "BUYING",
                "score": 78,
                "observation_key": "older-bar",
                "spot": 24340,
            }
        ],
        observation_key="new-bar",
    )
    assert result.direction == "BUYING"
    assert result.state == "FADING"
    assert "CHHOTA ULTA MOVE" in result.move_state


def test_normal_activity_never_shows_confirmed_persistence():
    inputs = _inputs("DOWN")
    inputs["volume"].three_minute.relative_volume = 0.36
    inputs["options"].bullish_score = 35
    inputs["options"].bearish_score = 39
    inputs["heavyweights"].state = "MIXED / FLAT"
    result = calculate_big_player_activity(
        **inputs,
        history=[
            {"direction": "SELLING", "score": 25},
            {"direction": "SELLING", "score": 30},
        ],
    )
    assert result.state == "NORMAL"
    assert result.confirmation_count == 0
    assert result.persistence == "NORMAL"


def test_short_covering_is_not_labelled_as_fresh_long_build_up():
    inputs = _inputs("UP")
    inputs["future_candles_1m"]["open_interest"] = [103000, 101500, 100000]
    inputs["options"].market_bias = "MIXED"
    inputs["options"].bullish_score = 40
    inputs["options"].bearish_score = 40
    inputs["heavyweights"].state = "BROAD BEARISH"
    result = calculate_big_player_activity(
        **inputs,
        history=[{"direction": "BUYING", "score": 75}],
    )
    assert result.direction == "BUYING"
    assert result.activity_type == "SHORT COVERING"
    assert "fresh long buying confirm nahi" in " | ".join(result.cautions)
    assert "Purane sellers" in result.participant_explanation
    assert "fresh long buying" in result.next_confirmation
