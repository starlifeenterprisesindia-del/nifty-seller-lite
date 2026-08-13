from datetime import datetime
from types import SimpleNamespace as NS

import pandas as pd

from analysis.big_player import calculate_big_player_activity


def _inputs(direction: str = "DOWN") -> dict:
    closes = [24350.0, 24345.0, 24338.0] if direction == "DOWN" else [24338.0, 24345.0, 24350.0]
    future = pd.DataFrame(
        [
            {"timestamp": f"2026-08-13 13:2{index}", "close": close, "oi": 100000 + index * 1500, "is_complete": True}
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
        history=[{"direction": "SELLING"}, {"direction": "SELLING"}],
    )
    assert result.direction == "SELLING"
    assert result.confirmation_count == 3
    assert result.score >= 75
    assert result.reversal_risk in {"HIGH", "DANGER"}


def test_large_buying_uses_green_direction_and_same_brain_inputs():
    result = calculate_big_player_activity(
        **_inputs("UP"),
        history=[{"direction": "BUYING"}],
    )
    assert result.direction == "BUYING"
    assert result.confirmation_count == 2
    assert result.futures_setup == "LONG BUILD-UP"


def test_single_snapshot_stays_warming_up():
    result = calculate_big_player_activity(**_inputs("DOWN"), history=[])
    assert result.confirmation_count == 1
    assert result.persistence == "WARMING UP"
