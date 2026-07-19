from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from analysis.trade_plan import calculate_trade_plan
from models import (
    FinalDecision,
    FlowWindow,
    LevelBundle,
    MarketLevel,
    MarketSession,
    OIWall,
    OptionIntelligence,
    PCRBundle,
    StrategyEvaluation,
)

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 7, 20, 11, 0, tzinfo=IST)


def option_intelligence() -> OptionIntelligence:
    windows = tuple(
        FlowWindow(
            label, seconds, float(seconds), 1, 2, 1, 2, 10, 20, "BULLISH", "READY"
        )
        for label, seconds in (("1m", 60), ("3m", 180), ("5m", 300))
    )
    return OptionIntelligence(
        as_of=NOW,
        basis="INTRADAY SNAPSHOT DELTA",
        snapshot_count=5,
        bullish_score=80,
        bearish_score=10,
        range_score=10,
        confidence=82,
        market_bias="BULLISH",
        persistence="BULLISH PERSISTENT ×3",
        ce_wall=OIWall("CE", 24600, 10_000, 24600, 0, 24600, 25_000, "READY"),
        pe_wall=OIWall("PE", 24100, 12_000, 24100, 0, 24100, 28_000, "READY"),
        pcr=PCRBundle(1.2, 1.1, 1.2, 1.0, "BULLISH SUPPORT", "READY"),
        windows=windows,
        flow_rows=(),
        reasons=("BULLISH",),
        blockers=(),
        status="READY",
    )


def levels() -> LevelBundle:
    support = MarketLevel(
        "Immediate", "SUPPORT", 24285, 24295, 24290, 80, "ACTIVE", 60, ("swing",)
    )
    resistance = MarketLevel(
        "Immediate", "RESISTANCE", 24405, 24415, 24410, 80, "ACTIVE", 60, ("swing",)
    )
    return LevelBundle(
        as_of=NOW,
        current_price=24350,
        immediate_support=support,
        strong_support=support,
        immediate_resistance=resistance,
        strong_resistance=resistance,
        previous_day_high=24450,
        previous_day_low=24200,
        opening_range_high=24380,
        opening_range_low=24310,
        upside_room=60,
        downside_room=60,
        current_position="BETWEEN SUPPORT AND RESISTANCE",
        zone_width=10,
        status="READY",
    )


def decision(action: str) -> FinalDecision:
    blank = StrategyEvaluation("X", 70, "READY", ("reason",), ())
    wait_score = 15 if action != "WAIT" else 80
    return FinalDecision(
        ce_sell=blank,
        pe_sell=blank,
        iron_condor=blank,
        wait_need=StrategyEvaluation("WAIT NEED", wait_score, "LOW", (), ()),
        final_action=action,
        execution_status="READY" if action != "WAIT" else "BLOCKED",
        decision_confidence=75,
        hedge_required=True,
        reasons=("reason",),
        blocker="Option-flow continuity is warming up" if action == "WAIT" else "None",
        status="READY",
    )


def option_frame() -> pd.DataFrame:
    rows = []
    for strike in range(24000, 24701, 50):
        distance = abs(strike - 24350) / 50
        for side in ("CE", "PE"):
            if side == "CE":
                delta = max(0.05, 0.50 - max(0, strike - 24350) / 1000)
                ltp = max(5.0, 120 - max(0, strike - 24350) * 0.22)
            else:
                delta = -max(0.05, 0.50 - max(0, 24350 - strike) / 1000)
                ltp = max(5.0, 120 - max(0, 24350 - strike) * 0.22)
            rows.append(
                {
                    "strike": float(strike),
                    "side": side,
                    "last_price": ltp,
                    "delta": delta,
                    "oi": 20_000 - distance * 500,
                    "volume": 10_000 - distance * 250,
                    "top_bid_price": max(0.5, ltp - 0.5),
                    "top_ask_price": ltp + 0.5,
                }
            )
    return pd.DataFrame(rows)


def live_session() -> MarketSession:
    return MarketSession("LIVE", "MARKET OPEN — LIVE DATA", True, "fresh")


def test_selected_pe_plan_is_ready_and_has_farther_otm_hedge():
    result = calculate_trade_plan(
        frame=option_frame(),
        spot=24350,
        expiry="2026-07-21",
        levels=levels(),
        options=option_intelligence(),
        decision=decision("PE SELL WITH HEDGE"),
        market_session=live_session(),
    )
    assert result.status == "READY"
    assert result.selected_setup == "PE SELL"
    assert result.pe_sell.status == "READY"
    assert result.pe_sell.short_legs[0].side == "PE"
    assert result.pe_sell.hedge_legs[0].strike < result.pe_sell.short_legs[0].strike
    assert result.pe_sell.estimated_credit_points > 0
    assert result.pe_sell.max_risk_points >= 0


def test_reference_market_keeps_candidates_reference_only():
    result = calculate_trade_plan(
        frame=option_frame(),
        spot=24350,
        expiry="2026-07-21",
        levels=levels(),
        options=option_intelligence(),
        decision=decision("WAIT"),
        market_session=MarketSession("WEEKEND", "MARKET CLOSED", False, "reference"),
    )
    assert result.status == "REFERENCE ONLY"
    assert result.ce_sell.status == "REFERENCE ONLY"
    assert result.pe_sell.status == "REFERENCE ONLY"
    assert result.iron_condor.status == "REFERENCE ONLY"


def test_wait_decision_blocks_execution_but_keeps_watch_candidates():
    result = calculate_trade_plan(
        frame=option_frame(),
        spot=24350,
        expiry="2026-07-21",
        levels=levels(),
        options=option_intelligence(),
        decision=decision("WAIT"),
        market_session=live_session(),
    )
    assert result.status == "BLOCKED"
    assert result.selected_setup == "WAIT"
    assert result.pe_sell.status == "WATCH ONLY"
    assert "warming up" in result.blocker.lower()


def test_condor_has_two_short_legs_and_two_hedges():
    result = calculate_trade_plan(
        frame=option_frame(),
        spot=24350,
        expiry="2026-07-21",
        levels=levels(),
        options=option_intelligence(),
        decision=decision("IRON CONDOR WITH HEDGE"),
        market_session=live_session(),
    )
    plan = result.iron_condor
    assert result.status == "READY"
    assert len(plan.short_legs) == 2
    assert len(plan.hedge_legs) == 2
    assert plan.lower_breakeven < plan.upper_breakeven


def test_missing_farther_otm_hedge_makes_plan_unavailable():
    frame = option_frame()
    frame = frame[frame["strike"].between(24300, 24450)]
    result = calculate_trade_plan(
        frame=frame,
        spot=24350,
        expiry="2026-07-21",
        levels=levels(),
        options=option_intelligence(),
        decision=decision("CE SELL WITH HEDGE"),
        market_session=live_session(),
    )
    assert result.ce_sell.status == "UNAVAILABLE"
    assert "hedge" in result.ce_sell.blocker.lower()
    assert result.status == "BLOCKED"
