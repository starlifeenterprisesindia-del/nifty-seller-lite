from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from analysis.patterns import detect_special_candle, detect_wm_pattern
from models import LevelBundle, MarketLevel, TimeframeVolume, VolumeBundle


IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 8, 1, 11, 0, tzinfo=IST)


def _frame(values: list[float]) -> pd.DataFrame:
    timestamps = pd.date_range(
        "2026-08-01 09:15:00",
        periods=len(values),
        freq="3min",
        tz=IST,
    )
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [value - 0.3 for value in values],
            "high": [value + 1.0 for value in values],
            "low": [value - 1.0 for value in values],
            "close": [float(value) for value in values],
            "volume": [1000.0] * len(values),
            "open_interest": [0.0] * len(values),
            "is_complete": [True] * len(values),
        }
    )


def _volume(direction: str) -> VolumeBundle:
    view = (
        "BULLISH PARTICIPATION"
        if direction == "BULLISH"
        else "BEARISH PARTICIPATION"
    )
    timeframe = TimeframeVolume(
        timeframe="3 Minute",
        as_of=NOW,
        current_volume=1500.0,
        baseline_volume=1000.0,
        relative_volume=1.5,
        volume_state="HIGH",
        volume_trend="RISING",
        price_direction="UP" if direction == "BULLISH" else "DOWN",
        move_support="CONFIRMED",
        baseline_samples=10,
        confidence=80.0,
        status="READY",
    )
    return VolumeBundle(
        source="NIFTY FUTURE",
        three_minute=timeframe,
        fifteen_minute=timeframe,
        overall_view=view,
        confidence=80.0,
        status="READY",
    )


def _levels(*, support: float = 100.0, resistance: float = 110.0) -> LevelBundle:
    support_level = MarketLevel(
        label="Immediate Support",
        side="SUPPORT",
        lower=support - 2.0,
        upper=support + 2.0,
        midpoint=support,
        strength=80.0,
        status="HOLDING",
        distance_points=1.0,
        sources=("TEST",),
    )
    resistance_level = MarketLevel(
        label="Immediate Resistance",
        side="RESISTANCE",
        lower=resistance - 2.0,
        upper=resistance + 2.0,
        midpoint=resistance,
        strength=80.0,
        status="ACTIVE",
        distance_points=1.0,
        sources=("TEST",),
    )
    return LevelBundle(
        as_of=NOW,
        current_price=105.0,
        immediate_support=support_level,
        strong_support=support_level,
        immediate_resistance=resistance_level,
        strong_resistance=resistance_level,
        previous_day_high=resistance + 10.0,
        previous_day_low=support - 1.0,
        opening_range_high=None,
        opening_range_low=None,
        upside_room=15.0,
        downside_room=15.0,
        current_position="BETWEEN SUPPORT AND RESISTANCE",
        zone_width=4.0,
        status="READY",
    )


def test_confirmed_w_near_support_is_bullish_evidence():
    values = [110, 108, 104, 100, 104, 108, 112, 108, 104, 101, 104, 109, 113, 114, 115, 116]
    result = detect_wm_pattern(_frame(values), _levels(), _volume("BULLISH"))

    assert result.name == "W"
    assert result.direction == "BULLISH"
    assert result.stage == "CONFIRMED"
    assert result.strength in {"STRONG", "VERY STRONG"}
    assert result.level_label == "S"
    assert result.neckline is not None
    assert result.bullish_score > result.bearish_score


def test_confirmed_m_near_resistance_is_bearish_evidence():
    values = [100, 102, 106, 110, 106, 102, 98, 102, 106, 109, 106, 101, 97, 96, 95, 94]
    result = detect_wm_pattern(
        _frame(values),
        _levels(support=98.0, resistance=110.0),
        _volume("BEARISH"),
    )

    assert result.name == "M"
    assert result.direction == "BEARISH"
    assert result.stage == "CONFIRMED"
    assert result.level_label == "R"
    assert result.bearish_score > result.bullish_score


def test_bullish_engulfing_near_support_is_important_candle():
    timestamps = pd.date_range(
        "2026-08-01 09:15:00", periods=8, freq="3min", tz=IST
    )
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [110, 109, 108, 107, 106, 105, 105, 100.5],
            "high": [111, 110, 109, 108, 107, 106, 106, 107],
            "low": [108, 107, 106, 105, 104, 103, 100, 99.5],
            "close": [109, 108, 107, 106, 105, 104, 101, 106],
            "volume": [1000.0] * 7 + [1800.0],
            "open_interest": [0.0] * 8,
            "is_complete": [True] * 8,
        }
    )
    result = detect_special_candle(frame, _levels(), _volume("BULLISH"))

    assert result.name == "BULL ENGULF"
    assert result.direction == "BULLISH"
    assert result.level_label == "S"
    assert result.confidence >= 65.0
    assert result.bullish_score > result.bearish_score


def test_mid_range_doji_is_suppressed_to_keep_ui_quiet():
    values = [100, 101, 102, 103, 104, 105]
    frame = _frame(values)
    frame.loc[frame.index[-1], ["open", "close", "high", "low"]] = [105.0, 105.05, 106.0, 104.0]
    levels = _levels(support=90.0, resistance=120.0)
    result = detect_special_candle(frame, levels, _volume("BULLISH"))

    assert result.name == "NO IMPORTANT CANDLE"
    assert result.direction == "NEUTRAL"
