import pandas as pd

from analysis.price_action import (
    calculate_price_action_bundle,
    calculate_timeframe_price_action,
)


def swing_frame(periods: int = 30, *, bearish: bool = False) -> pd.DataFrame:
    values: list[float] = []
    cycles = max(6, periods // 5)
    for cycle in range(cycles):
        base = 100.0 + cycle * 3.0
        wave = [base, base + 2.0, base + 4.0, base + 2.0, base + 1.0]
        values.extend(wave)
    values = values[:periods]
    if bearish:
        values = [200.0 - value for value in values]
    timestamps = pd.date_range(
        "2026-07-16 09:15:00",
        periods=len(values),
        freq="3min",
        tz="Asia/Kolkata",
    )
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [value - 0.2 for value in values],
            "high": [value + 0.8 for value in values],
            "low": [value - 0.8 for value in values],
            "close": values,
            "volume": [1000] * len(values),
            "open_interest": [0] * len(values),
            "is_complete": [True] * len(values),
        }
    )


def test_bullish_higher_high_higher_low_structure():
    result = calculate_timeframe_price_action(swing_frame(), "3 Minute")
    assert result.status == "READY"
    assert result.structure == "BULLISH HH/HL"
    assert result.bullish_score > result.bearish_score
    assert result.last_swing_high > result.prior_swing_high
    assert result.last_swing_low > result.prior_swing_low


def test_bearish_lower_high_lower_low_structure():
    result = calculate_timeframe_price_action(swing_frame(bearish=True), "15 Minute")
    assert result.status == "READY"
    assert result.structure == "BEARISH LH/LL"
    assert result.bearish_score > result.bullish_score


def test_cross_timeframe_pullback_relationship():
    bundle = calculate_price_action_bundle(swing_frame(bearish=True), swing_frame())
    assert bundle.combined_state == "BULLISH PULLBACK"
    assert bundle.relationship == "15M BULLISH / 3M PULLBACK"
