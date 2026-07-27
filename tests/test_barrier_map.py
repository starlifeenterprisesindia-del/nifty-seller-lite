from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from analysis.barrier_map import calculate_barrier_map
from models import (
    CoreMarketEvidence,
    FlowWindow,
    HeavyweightBundle,
    IndicatorBundle,
    LevelBundle,
    MarketLevel,
    MarketSession,
    OIWall,
    OptionIntelligence,
    PCRBundle,
    PriceActionBundle,
    TimeframeIndicators,
    TimeframePriceAction,
    TimeframeVolume,
    VixContext,
    VolumeBundle,
)

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 7, 27, 11, 0, tzinfo=IST)


def _tf_pa(label: str, bullish: float, bearish: float) -> TimeframePriceAction:
    return TimeframePriceAction(
        timeframe=label,
        as_of=NOW,
        structure="BULLISH HH/HL" if bullish > bearish else "BEARISH LH/LL",
        event="BULLISH CONTINUATION" if bullish > bearish else "BEARISH CONTINUATION",
        move_stage="DEVELOPING",
        last_swing_high=25205,
        prior_swing_high=25160,
        last_swing_low=25005,
        prior_swing_low=24960,
        invalidation_level=25005,
        atr14=24,
        bullish_score=bullish,
        bearish_score=bearish,
        range_score=25,
        confidence=82,
        reasons=(),
        status="READY",
    )


def _ind(label: str) -> TimeframeIndicators:
    return TimeframeIndicators(label, NOW, 25120, 25100, 25080, "BULLISH", 3, 2, 1, "BULLISH", 58, "NEUTRAL", "READY")


def _volume(label: str, ratio: float, direction: str) -> TimeframeVolume:
    return TimeframeVolume(label, NOW, 1000, 600, ratio, "HIGH", "RISING", direction, "NORMAL PARTICIPATION", 5, 85, "READY")


def _options() -> OptionIntelligence:
    windows = (
        FlowWindow("1 minute", 60, 60, -1200, 1500, 16, -10, 1000, 1100, "BULLISH", "READY"),
        FlowWindow("3 minute", 180, 180, -2200, 2600, 32, -24, 2100, 2400, "BULLISH", "READY"),
        FlowWindow("5 minute", 300, 300, -3500, 4100, 55, -42, 3200, 3700, "BULLISH", "READY"),
    )
    rows = (
        {"strike": 25200, "side": "CE", "classification": "SHORT COVERING", "flow_strength": 2.5},
        {"strike": 25000, "side": "PE", "classification": "SHORT BUILDUP", "flow_strength": 2.8},
    )
    return OptionIntelligence(
        as_of=NOW,
        basis="INTRADAY SNAPSHOT DELTA",
        snapshot_count=10,
        bullish_score=76,
        bearish_score=24,
        range_score=40,
        confidence=85,
        market_bias="BULLISH",
        persistence="BULLISH PERSISTENT ×3",
        ce_wall=OIWall("CE", 25200, 150000, None, None, 25200, 300000, "READY"),
        pe_wall=OIWall("PE", 25000, 160000, None, None, 25000, 320000, "READY"),
        pcr=PCRBundle(1.2, 1.1, 1.15, 1.05, "BULLISH", "READY"),
        windows=windows,
        flow_rows=rows,
        reasons=(),
        blockers=(),
        status="READY",
    )


def _candles() -> pd.DataFrame:
    rows = []
    start = NOW - timedelta(minutes=60)
    price = 25070.0
    for idx in range(60):
        close = price + idx * 0.8
        rows.append(
            {
                "timestamp": start + timedelta(minutes=idx),
                "open": close - 1,
                "high": close + 3,
                "low": close - 3,
                "close": close,
                "volume": 1000 + idx * 10,
                "is_complete": True,
            }
        )
    return pd.DataFrame(rows)


def test_barrier_map_builds_range_next_barriers_and_speed():
    support = MarketLevel("S1", "SUPPORT", 24990, 25010, 25000, 82, "ACTIVE", 120, ("Previous Day Low",))
    resistance = MarketLevel("R1", "RESISTANCE", 25190, 25210, 25200, 84, "ACTIVE", 80, ("Previous Day High",))
    levels = LevelBundle(NOW, 25120, support, support, resistance, resistance, 25200, 25000, 25205, 25005, 70, 110, "BETWEEN SUPPORT AND RESISTANCE", 10, "READY")
    price_action = PriceActionBundle(_tf_pa("3 Minute", 78, 20), _tf_pa("15 Minute", 70, 25), "TIMEFRAMES ALIGNED", "BULLISH HH/HL", 82)
    core = CoreMarketEvidence(74, 22, 35, 82, "BULLISH", "DEVELOPING", "READY", (), ())
    volume = VolumeBundle("NIFTY FUTURES", _volume("3 Minute", 1.9, "UP"), _volume("15 Minute", 1.4, "UP"), "BULLISH MOVE CONFIRMED", 85, "READY")
    heavy = HeavyweightBundle(NOW, (), 45.2, 0.52, 0.24, 6, 1, 0, "BROAD BULLISH", 88, "READY")
    vix = VixContext(NOW, 15.0, 14.2, 5.63, "ELEVATED", "RISING FAST", "HIGH PREMIUM / HIGH GAP RISK", "READY")
    history = []
    for sec, spot, vix_value in ((60, 25100, 14.8), (180, 25070, 14.5), (300, 25045, 14.2), (900, 25020, 13.9)):
        history.append({"captured_at": (NOW - timedelta(seconds=sec)).isoformat(), "spot": spot, "vix": vix_value})

    result = calculate_barrier_map(
        spot=25120,
        captured_at=NOW,
        market_session=MarketSession("LIVE", "MARKET OPEN", True, "fresh"),
        expiry="2026-07-30",
        candles_1m=_candles(),
        levels=levels,
        indicators=IndicatorBundle(_ind("3 Minute"), _ind("15 Minute")),
        price_action=price_action,
        core=core,
        volume=volume,
        options=_options(),
        heavyweights=heavy,
        vix=vix,
        option_history=history,
    )

    assert result.status == "READY"
    assert result.nearest_resistance is not None
    assert result.nearest_support is not None
    assert result.trading_range.lower < result.current_price < result.trading_range.upper
    assert result.market_speed.direction == "UP"
    assert result.market_speed.move_3m_points == 50.0
    assert result.market_speed.vix_change_5m_pct is not None
    assert result.vix_expected_daily_move_points is not None
    assert 0 <= result.nearest_resistance.strength <= 100
    assert 0 <= result.nearest_resistance.break_pressure <= 100
