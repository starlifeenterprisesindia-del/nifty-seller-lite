from datetime import datetime
from zoneinfo import ZoneInfo

from analysis.core_market import calculate_core_market_evidence
from models import (
    IndicatorBundle,
    LevelBundle,
    MarketSession,
    PriceActionBundle,
    TimeframeIndicators,
    TimeframePriceAction,
    TimeframeVolume,
    VolumeBundle,
)


IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 7, 20, 11, 0, tzinfo=IST)


def indicators(timeframe: str) -> TimeframeIndicators:
    return TimeframeIndicators(
        timeframe=timeframe,
        as_of=NOW,
        close=110,
        ema20=108,
        ema50=105,
        ema_state="BULLISH ALIGNED",
        macd=3,
        macd_signal=2,
        macd_histogram=1,
        macd_state="BULLISH",
        rsi14=62,
        rsi_state="BULLISH STRENGTH",
        status="READY",
    )


def price_action(timeframe: str) -> TimeframePriceAction:
    return TimeframePriceAction(
        timeframe=timeframe,
        as_of=NOW,
        structure="BULLISH HH/HL",
        event="BULLISH CONTINUATION",
        move_stage="DEVELOPING",
        last_swing_high=112,
        prior_swing_high=108,
        last_swing_low=106,
        prior_swing_low=102,
        invalidation_level=106,
        atr14=4,
        bullish_score=82,
        bearish_score=12,
        range_score=20,
        confidence=85,
        reasons=("BULLISH HH/HL",),
        status="READY",
    )


def volume(timeframe: str) -> TimeframeVolume:
    return TimeframeVolume(
        timeframe=timeframe,
        as_of=NOW,
        current_volume=2000,
        baseline_volume=1000,
        relative_volume=2,
        volume_state="SURGE",
        volume_trend="RISING",
        price_direction="UP",
        move_support="BULLISH MOVE CONFIRMED",
        baseline_samples=5,
        confidence=85,
        status="READY",
    )


def test_core_market_combines_modules_without_strategy_action():
    result = calculate_core_market_evidence(
        PriceActionBundle(
            price_action("3 Minute"),
            price_action("15 Minute"),
            "TIMEFRAMES ALIGNED",
            "BULLISH HH/HL",
            85,
        ),
        IndicatorBundle(indicators("3 Minute"), indicators("15 Minute")),
        LevelBundle(
            NOW,
            110,
            None,
            None,
            None,
            None,
            120,
            100,
            115,
            105,
            20,
            20,
            "BETWEEN SUPPORT AND RESISTANCE",
            5,
            "READY",
        ),
        VolumeBundle(
            "NIFTY FUTURES",
            volume("3 Minute"),
            volume("15 Minute"),
            "BULLISH PARTICIPATION",
            85,
            "READY",
        ),
        MarketSession("LIVE", "MARKET OPEN — LIVE DATA", True, "fresh"),
    )
    assert result.bullish_score > result.bearish_score
    assert result.market_state == "BULLISH"
    assert result.status == "READY"
