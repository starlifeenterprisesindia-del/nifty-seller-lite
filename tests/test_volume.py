import pandas as pd

from analysis.volume import calculate_timeframe_volume, calculate_volume_bundle


def same_slot_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    timestamps = [
        pd.Timestamp(f"2026-07-{day:02d} 09:15:00", tz="Asia/Kolkata")
        for day in range(13, 19)
    ]
    volume = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [100] * 6,
            "high": [102] * 6,
            "low": [99] * 6,
            "close": [101] * 6,
            "volume": [1000, 1050, 950, 1000, 1000, 2000],
            "open_interest": [10] * 6,
            "is_complete": [True] * 6,
        }
    )
    price = volume.copy()
    price.loc[price.index[-1], ["open", "close", "high", "low"]] = [100, 102, 103, 99]
    return volume, price


def test_time_normalized_future_volume_detects_surge():
    volume, price = same_slot_frames()
    result = calculate_timeframe_volume(volume, price, "3 Minute")
    assert result.status == "READY"
    assert result.relative_volume >= 1.8
    assert result.volume_state == "SURGE"
    assert result.move_support == "BULLISH MOVE CONFIRMED"
    assert result.baseline_samples == 5


def test_volume_bundle_uses_nifty_futures_source():
    volume, price = same_slot_frames()
    result = calculate_volume_bundle(volume, volume, price, price)
    assert result.source == "NIFTY FUTURES"
    assert result.status == "READY"
    assert result.overall_view == "BULLISH PARTICIPATION"
