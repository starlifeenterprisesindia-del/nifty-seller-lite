import pandas as pd

from analysis.indicators import calculate_indicator_bundle
from analysis.levels import calculate_levels


def multi_day_frame(freq: str, bars_per_day: int) -> pd.DataFrame:
    rows = []
    for day, base in [(16, 100.0), (17, 110.0)]:
        timestamps = pd.date_range(
            f"2026-07-{day} 09:15:00",
            periods=bars_per_day,
            freq=freq,
            tz="Asia/Kolkata",
        )
        for index, timestamp in enumerate(timestamps):
            close = base + index * 0.4 + (1.2 if index % 4 == 1 else 0.0)
            rows.append(
                {
                    "timestamp": timestamp,
                    "open": close - 0.2,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 1000 + index * 10,
                    "open_interest": 0,
                    "is_complete": True,
                }
            )
    return pd.DataFrame(rows)


def test_levels_include_previous_day_and_opening_range():
    three = multi_day_frame("3min", 70)
    fifteen = multi_day_frame("15min", 55)
    indicators = calculate_indicator_bundle(three, fifteen)
    price = 120.0
    result = calculate_levels(three, fifteen, indicators, price)
    assert result.status == "READY"
    assert result.previous_day_high is not None
    assert result.previous_day_low is not None
    assert result.opening_range_high is not None
    assert result.opening_range_low is not None
    assert result.immediate_support is not None
    assert result.immediate_resistance is not None
    assert result.upside_room is not None
    assert result.downside_room is not None


def test_current_spot_inside_resistance_zone_is_testing_not_broken():
    from analysis.levels import _level_status

    last_candle = pd.Series({"close": 110.0, "high": 112.0, "low": 104.0})
    status = _level_status(
        "RESISTANCE",
        lower=105.0,
        upper=108.0,
        current_price=106.5,
        last_candle=last_candle,
        width=3.0,
    )
    assert status == "TESTING / INSIDE ZONE"
