from datetime import datetime
from zoneinfo import ZoneInfo

from analysis.decision import calculate_final_decision
from models import (
    CoreMarketEvidence,
    EventRiskContext,
    FlowWindow,
    HeavyweightBundle,
    InstitutionalContext,
    LevelBundle,
    MarketSession,
    OIWall,
    OptionIntelligence,
    PCRBundle,
    VixContext,
)

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 7, 20, 11, 0, tzinfo=IST)


def option_intelligence() -> OptionIntelligence:
    windows = tuple(
        FlowWindow(
            label,
            seconds,
            float(seconds),
            100,
            200,
            -2,
            -4,
            1000,
            1200,
            "BULLISH",
            "READY",
        )
        for label, seconds in (("1 minute", 60), ("3 minute", 180), ("5 minute", 300))
    )
    return OptionIntelligence(
        as_of=NOW,
        basis="INTRADAY SNAPSHOT DELTA",
        snapshot_count=5,
        bullish_score=82,
        bearish_score=8,
        range_score=10,
        confidence=84,
        market_bias="BULLISH",
        persistence="BULLISH PERSISTENT ×3",
        ce_wall=OIWall("CE", 24500, 1000, 24500, 0, 24500, 2500, "READY"),
        pe_wall=OIWall("PE", 24200, 1800, 24200, 0, 24200, 4000, "READY"),
        pcr=PCRBundle(1.3, 1.4, 1.2, 1.1, "BULLISH SUPPORT", "READY"),
        windows=windows,
        flow_rows=(),
        reasons=("Option flow mix is BULLISH",),
        blockers=(),
        status="READY",
    )


def common_kwargs():
    return {
        "core": CoreMarketEvidence(
            bullish_score=80,
            bearish_score=12,
            range_score=20,
            confidence=82,
            market_state="BULLISH",
            move_stage="DEVELOPING",
            status="READY",
            reasons=("BULLISH HH/HL",),
            blockers=(),
        ),
        "options": option_intelligence(),
        "heavyweights": HeavyweightBundle(
            as_of=NOW,
            rows=(),
            covered_weight_pct=45.2,
            weighted_move_pct=0.5,
            estimated_index_contribution_pct=0.2,
            advancing=6,
            declining=1,
            unchanged=0,
            state="BROAD BULLISH",
            confidence=92,
            status="READY",
        ),
        "vix": VixContext(
            as_of=NOW,
            last_price=13,
            previous_close=13.1,
            change_pct=-0.7,
            regime="NORMAL",
            movement="STABLE",
            seller_environment="BALANCED PREMIUM ENVIRONMENT",
            status="READY",
        ),
        "levels": LevelBundle(
            as_of=NOW,
            current_price=24350,
            immediate_support=None,
            strong_support=None,
            immediate_resistance=None,
            strong_resistance=None,
            previous_day_high=24450,
            previous_day_low=24200,
            opening_range_high=24380,
            opening_range_low=24300,
            upside_room=35,
            downside_room=30,
            current_position="BETWEEN SUPPORT AND RESISTANCE",
            zone_width=5,
            status="READY",
        ),
        "institutional": InstitutionalContext(
            as_of_date="2026-07-19",
            latest_fii_net=800,
            latest_dii_net=600,
            fii_5d_net=2500,
            fii_10d_net=4000,
            fii_15d_net=5500,
            dii_5d_net=1500,
            dii_10d_net=2600,
            dii_15d_net=3500,
            observations=15,
            state="NET INSTITUTIONAL SUPPORT",
            confidence=85,
            status="READY",
        ),
        "event_risk": EventRiskContext(
            as_of_date="2026-07-20",
            level="NONE",
            note="",
            verified=False,
            status="READY",
        ),
        "market_session": MarketSession(
            "LIVE", "MARKET OPEN — LIVE DATA", True, "fresh"
        ),
        "quote_live": True,
        "candles_live": True,
        "option_chain_live": True,
    }


def test_live_bullish_setup_selects_pe_sell_with_hedge():
    result = calculate_final_decision(**common_kwargs())
    assert result.final_action == "PE SELL WITH HEDGE"
    assert result.pe_sell.score > result.ce_sell.score
    assert result.wait_need.score < 60
    assert result.hedge_required is True


def test_reference_session_forces_wait():
    kwargs = common_kwargs()
    kwargs["market_session"] = MarketSession(
        "WEEKEND", "MARKET CLOSED", False, "reference"
    )
    result = calculate_final_decision(**kwargs)
    assert result.final_action == "WAIT"
    assert result.wait_need.score == 100
    assert result.status == "REFERENCE ONLY"


def test_verified_high_event_risk_forces_wait():
    kwargs = common_kwargs()
    kwargs["event_risk"] = EventRiskContext(
        as_of_date="2026-07-20",
        level="HIGH",
        note="Verified scheduled event",
        verified=True,
        status="READY",
    )
    result = calculate_final_decision(**kwargs)
    assert result.final_action == "WAIT"
    assert result.wait_need.score >= 60
    assert "event risk" in result.blocker.lower()


def test_unavailable_vix_adds_wait_and_caution():
    kwargs = common_kwargs()
    kwargs["vix"] = VixContext(
        as_of=NOW,
        last_price=None,
        previous_close=None,
        change_pct=None,
        regime="UNAVAILABLE",
        movement="UNAVAILABLE",
        seller_environment="VIX DATA UNAVAILABLE",
        status="INVALID / UNAVAILABLE",
    )
    result = calculate_final_decision(**kwargs)
    assert result.wait_need.score >= 16
    assert "India VIX data is unavailable" in result.pe_sell.cautions
