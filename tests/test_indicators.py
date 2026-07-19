import pandas as pd

from analysis.indicators import calculate_indicator_bundle, calculate_timeframe_indicators


def candle_frame(periods: int = 80, rising: bool = True) -> pd.DataFrame:
    timestamps = pd.date_range(
        "2026-07-16 09:15:00",
        periods=periods,
        freq="3min",
        tz="Asia/Kolkata",
    )
    if rising:
        closes = [100 + item * 0.5 for item in range(periods)]
    else:
        closes = [140 - item * 0.5 for item in range(periods)]
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": closes,
            "high": [item + 1 for item in closes],
            "low": [item - 1 for item in closes],
            "close": closes,
            "volume": [1000] * periods,
            "open_interest": [0] * periods,
            "is_complete": [True] * periods,
        }
    )


def test_rising_series_produces_bullish_indicators():
    result = calculate_timeframe_indicators(candle_frame(), "3 Minute")
    assert result.status == "READY"
    assert result.ema20 is not None and result.ema50 is not None
    assert result.ema20 > result.ema50
    assert result.macd_histogram is not None
    assert result.rsi14 is not None and result.rsi14 >= 55


def test_incomplete_candles_are_excluded():
    frame = candle_frame()
    frame.loc[frame.index[-10:], "is_complete"] = False
    result = calculate_timeframe_indicators(frame, "3 Minute")
    assert result.status == "READY"
    assert result.as_of == frame.iloc[-11]["timestamp"].to_pydatetime()


def test_indicator_bundle_has_both_timeframes():
    frame = candle_frame()
    result = calculate_indicator_bundle(frame, frame)
    assert result.three_minute.status == "READY"
    assert result.fifteen_minute.status == "READY"
