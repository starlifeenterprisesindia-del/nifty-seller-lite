from datetime import datetime
from zoneinfo import ZoneInfo

from analysis.market_session import classify_market_session, feed_use_state


IST = ZoneInfo("Asia/Kolkata")


def test_weekend_is_reference_only():
    session = classify_market_session(
        datetime(2026, 7, 19, 13, 37, tzinfo=IST),
        quote_age_seconds=153000,
        has_current_day_candle=False,
        candle_age_seconds=None,
    )
    assert session.code == "CLOSED_WEEKEND"
    assert not session.is_live
    assert feed_use_state(available=True, market_session=session) == "REFERENCE"


def test_live_requires_fresh_quote_and_current_candle():
    session = classify_market_session(
        datetime(2026, 7, 20, 10, 0, tzinfo=IST),
        quote_age_seconds=2,
        has_current_day_candle=True,
        candle_age_seconds=30,
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
        candle_age_seconds=600,
    )
    assert session.code == "CLOSED_OR_STALE_SESSION"
    assert not session.is_live


def test_open_clock_with_fresh_quote_but_stale_candle_is_not_live():
    session = classify_market_session(
        datetime(2026, 7, 20, 14, 0, tzinfo=IST),
        quote_age_seconds=2,
        has_current_day_candle=True,
        candle_age_seconds=1200,
    )
    assert session.code == "CLOSED_OR_STALE_SESSION"
    assert not session.is_live
    assert "fresh completed candle" in session.message


def test_midnight_is_closed_not_preopen():
    session = classify_market_session(
        datetime(2026, 7, 28, 0, 25, tzinfo=IST),
        quote_age_seconds=10000,
        has_current_day_candle=False,
        candle_age_seconds=None,
    )
    assert session.code == "CLOSED_BEFORE_OPEN"
    assert "NEXT SESSION NOT OPEN" in session.label


def test_official_preopen_window_is_separate():
    session = classify_market_session(
        datetime(2026, 7, 28, 9, 5, tzinfo=IST),
        quote_age_seconds=2,
        has_current_day_candle=False,
        candle_age_seconds=None,
    )
    assert session.code == "PRE_OPEN"
    assert not session.is_live


def test_closing_auction_is_reference_only_and_blocks_fresh_entry():
    session = classify_market_session(
        datetime(2026, 8, 17, 15, 20, tzinfo=IST),
        quote_age_seconds=2,
        has_current_day_candle=True,
        candle_age_seconds=30,
    )
    assert session.code == "CLOSING_AUCTION"
    assert not session.is_live
    assert "CLOSING AUCTION" in session.label
    assert feed_use_state(available=True, market_session=session) == "REFERENCE"


def test_last_continuous_market_minute_before_cas_remains_live():
    session = classify_market_session(
        datetime(2026, 8, 17, 15, 14, 59, tzinfo=IST),
        quote_age_seconds=2,
        has_current_day_candle=True,
        candle_age_seconds=30,
    )
    assert session.code == "LIVE"
    assert session.is_live
