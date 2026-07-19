from datetime import datetime
from zoneinfo import ZoneInfo

from analysis.market_session import classify_market_session, feed_use_state


IST = ZoneInfo("Asia/Kolkata")


def test_weekend_is_reference_only():
    session = classify_market_session(
        datetime(2026, 7, 19, 13, 37, tzinfo=IST),
        quote_age_seconds=153000,
        has_current_day_candle=False,
    )
    assert session.code == "CLOSED_WEEKEND"
    assert not session.is_live
    assert feed_use_state(available=True, market_session=session) == "REFERENCE"


def test_live_requires_fresh_quote_and_current_candle():
    session = classify_market_session(
        datetime(2026, 7, 20, 10, 0, tzinfo=IST),
        quote_age_seconds=2,
        has_current_day_candle=True,
    )
    assert session.code == "LIVE"
    assert session.is_live
    assert (
        feed_use_state(
            available=True,
            market_session=session,
            age_seconds=2,
            max_live_age_seconds=12,
        )
        == "LIVE"
    )


def test_open_clock_with_stale_data_is_not_live():
    session = classify_market_session(
        datetime(2026, 7, 20, 10, 0, tzinfo=IST),
        quote_age_seconds=600,
        has_current_day_candle=False,
    )
    assert session.code == "CLOSED_OR_STALE_SESSION"
    assert not session.is_live
