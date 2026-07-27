from datetime import datetime
from zoneinfo import ZoneInfo

from analysis.pre_touch_barriers import calculate_pre_touch_barriers
from models import (
    FlowWindow,
    LevelBundle,
    MarketLevel,
    OIWall,
    OptionIntelligence,
    PCRBundle,
)

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 7, 27, 11, 0, tzinfo=IST)


def _options() -> OptionIntelligence:
    windows = tuple(
        FlowWindow(label, seconds, seconds, 0, 0, 0, 0, 0, 0, "MIXED", "READY")
        for label, seconds in (("1m", 60), ("3m", 180), ("5m", 300))
    )
    return OptionIntelligence(
        as_of=NOW,
        basis="test",
        snapshot_count=5,
        bullish_score=40,
        bearish_score=40,
        range_score=20,
        confidence=80,
        market_bias="MIXED",
        persistence="MIXED",
        ce_wall=OIWall("CE", 25200, 10000, None, None, 25200, 25000, "READY"),
        pe_wall=OIWall("PE", 25000, 12000, None, None, 25000, 28000, "READY"),
        pcr=PCRBundle(1.0, 1.0, 1.0, 1.0, "NEUTRAL", "READY"),
        windows=windows,
        flow_rows=(),
        reasons=(),
        blockers=(),
        status="READY",
    )


def _levels() -> LevelBundle:
    support = MarketLevel(
        "S1", "SUPPORT", 24995, 25005, 25000, 75, "ACTIVE", 80, ("Previous Day Low",)
    )
    resistance = MarketLevel(
        "R1", "RESISTANCE", 25185, 25195, 25190, 78, "ACTIVE", 110, ("Previous Day High",)
    )
    return LevelBundle(
        as_of=NOW,
        current_price=25080,
        immediate_support=support,
        strong_support=support,
        immediate_resistance=resistance,
        strong_resistance=resistance,
        previous_day_high=25190,
        previous_day_low=25000,
        opening_range_high=25195,
        opening_range_low=25010,
        upside_room=110,
        downside_room=80,
        current_position="BETWEEN SUPPORT AND RESISTANCE",
        zone_width=10,
        status="READY",
    )


def test_pre_touch_combines_structure_and_oi_wall_before_touch():
    result = calculate_pre_touch_barriers(levels=_levels(), options=_options(), spot=25080)
    assert result.status == "READY"
    assert result.resistance is not None
    assert result.support is not None
    assert "CE OI Wall" in result.resistance.sources
    assert result.resistance.midpoint >= 25190
    assert result.resistance.distance_points > 0
    assert "resistance aa sakta hai" in result.resistance.message
    assert "PE OI Wall" in result.support.sources
