from __future__ import annotations

from datetime import datetime

from config import CONFIG
from models import MarketSession


def classify_market_session(
    now: datetime,
    *,
    quote_age_seconds: float | None,
    has_current_day_candle: bool,
) -> MarketSession:
    """Classify whether current data is live or only last-available reference data."""
    if now.weekday() >= 5:
        return MarketSession(
            code="CLOSED_WEEKEND",
            label="MARKET CLOSED — LAST AVAILABLE DATA",
            is_live=False,
            message="Weekend session. Data is reference-only and must not be treated as live.",
        )

    current_time = now.time().replace(tzinfo=None)
    if current_time < CONFIG.market_open:
        return MarketSession(
            code="PRE_MARKET",
            label="PRE-MARKET — LAST AVAILABLE DATA",
            is_live=False,
            message="Market has not opened yet. Previous-session values are reference-only.",
        )
    if current_time > CONFIG.market_close:
        return MarketSession(
            code="CLOSED_AFTER_HOURS",
            label="MARKET CLOSED — LAST AVAILABLE DATA",
            is_live=False,
            message="Cash market session has ended. Data is reference-only.",
        )

    quote_is_fresh = (
        quote_age_seconds is not None
        and quote_age_seconds <= CONFIG.quote_max_age_seconds
    )
    if quote_is_fresh and has_current_day_candle:
        return MarketSession(
            code="LIVE",
            label="MARKET OPEN — LIVE DATA",
            is_live=True,
            message="Quote and current-session candle evidence are fresh.",
        )

    return MarketSession(
        code="CLOSED_OR_STALE_SESSION",
        label="LIVE SESSION NOT CONFIRMED — REFERENCE DATA",
        is_live=False,
        message=(
            "Market-time clock is open, but fresh quote/current-session candles are missing. "
            "This may be a holiday, feed delay, or stale session."
        ),
    )


def feed_use_state(
    *,
    available: bool,
    market_session: MarketSession,
    age_seconds: float | None = None,
    max_live_age_seconds: float | None = None,
) -> str:
    if not available:
        return "UNAVAILABLE"
    if not market_session.is_live:
        return "REFERENCE"
    if max_live_age_seconds is not None:
        if age_seconds is None or age_seconds > max_live_age_seconds:
            return "STALE"
    return "LIVE"
