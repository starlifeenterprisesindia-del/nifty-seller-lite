from dataclasses import replace
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
    PatternEvidenceBundle,
    PatternSignal,
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
            latest_fii_index_futures_net=1200,
            fii_5d_net=2500,
            fii_10d_net=4000,
            fii_15d_net=5500,
            dii_5d_net=1500,
            dii_10d_net=2600,
            dii_15d_net=3500,
            fii_index_futures_5d_net=3000,
            fii_index_futures_10d_net=4500,
            fii_index_futures_15d_net=6000,
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
    assert result.instant_action == "PE SELL WITH HEDGE"
    assert result.final_action == "WAIT"
    assert result.pe_sell.score > result.ce_sell.score
    assert result.wait_need.score >= 65
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


def test_level_cautions_are_strategy_specific():
    kwargs = common_kwargs()
    kwargs["levels"] = LevelBundle(
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
        upside_room=5,
        downside_room=5,
        current_position="BETWEEN SUPPORT AND RESISTANCE",
        zone_width=5,
        status="READY",
    )
    result = calculate_final_decision(**kwargs)
    assert "Support paas hai; bounce se CE Sell ko risk" in result.ce_sell.cautions
    assert (
        "Resistance paas hai; rejection se PE Sell ko risk"
        not in result.ce_sell.cautions
    )
    assert (
        "Resistance paas hai; rejection se PE Sell ko risk" in result.pe_sell.cautions
    )
    assert (
        "Support paas hai; bounce se CE Sell ko risk"
        not in result.pe_sell.cautions
    )
    assert any("balanced room" in item for item in result.iron_condor.cautions)


def _signal(
    *,
    captured_at: datetime,
    direction: str,
    state: str,
    ce: float,
    pe: float,
    condor: float,
) -> dict[str, object]:
    action = {
        "BULLISH": "PE SELL WITH HEDGE",
        "BEARISH": "CE SELL WITH HEDGE",
        "RANGE": "IRON CONDOR WITH HEDGE",
    }[direction]
    return {
        "captured_at": captured_at.isoformat(),
        "action": action,
        "execution_status": "READY",
        "ce_score": ce,
        "pe_score": pe,
        "condor_score": condor,
        "wait_need": 15,
        "signal_state": state,
        "market_direction": direction,
        "fake_move_risk": 20,
        "spot": 24350,
    }


def _bearish_kwargs():
    kwargs = common_kwargs()
    kwargs["core"] = CoreMarketEvidence(
        bullish_score=8,
        bearish_score=88,
        range_score=15,
        confidence=86,
        market_state="BEARISH",
        move_stage="DEVELOPING",
        status="READY",
        reasons=("BEARISH LH/LL",),
        blockers=(),
    )
    bearish_options = option_intelligence()
    kwargs["options"] = OptionIntelligence(
        **{
            **bearish_options.__dict__,
            "bullish_score": 6,
            "bearish_score": 88,
            "range_score": 6,
            "market_bias": "BEARISH",
            "persistence": "BEARISH PERSISTENT ×3",
        }
    )
    kwargs["heavyweights"] = HeavyweightBundle(
        **{
            **kwargs["heavyweights"].__dict__,
            "state": "BROAD BEARISH",
        }
    )
    kwargs["as_of"] = NOW
    kwargs["current_price"] = 24320.0
    return kwargs


def test_first_direction_is_developing_but_execution_guard_can_confirm_later():
    kwargs = common_kwargs()
    kwargs["as_of"] = NOW
    result = calculate_final_decision(**kwargs)
    assert result.instant_action == "PE SELL WITH HEDGE"
    assert result.final_action == "WAIT"
    assert result.signal_state == "BULLISH DEVELOPING"
    assert "WARMING UP" in result.outlook.signal_memory


def test_three_minute_three_snapshot_stability_confirms_direction():
    kwargs = common_kwargs()
    kwargs["as_of"] = NOW
    kwargs["signal_history"] = (
        _signal(
            captured_at=NOW.replace(hour=10, minute=57),
            direction="BULLISH",
            state="BULLISH DEVELOPING",
            ce=18,
            pe=78,
            condor=25,
        ),
        _signal(
            captured_at=NOW.replace(hour=10, minute=58),
            direction="BULLISH",
            state="BULLISH DEVELOPING",
            ce=18,
            pe=78,
            condor=25,
        ),
    )
    result = calculate_final_decision(**kwargs)
    assert result.final_action == "PE SELL WITH HEDGE"
    assert result.signal_state == "BULLISH CONFIRMED"
    assert result.outlook.signal_memory.startswith("3/3 BULLISH")


def test_single_opposite_snapshot_is_held_at_wait_instead_of_flipping():
    kwargs = _bearish_kwargs()
    kwargs["core"] = CoreMarketEvidence(**{**kwargs["core"].__dict__, "confidence": 65})
    kwargs["signal_history"] = (
        _signal(
            captured_at=NOW.replace(hour=10, minute=58),
            direction="BULLISH",
            state="BULLISH CONFIRMED",
            ce=18,
            pe=78,
            condor=25,
        ),
    )
    result = calculate_final_decision(**kwargs)
    assert result.instant_action == "CE SELL WITH HEDGE"
    assert result.final_action == "WAIT"
    assert result.signal_state == "TRANSITION / WAIT"
    assert result.wait_need.score >= 65


def test_persistent_opposite_direction_can_confirm_reversal():
    kwargs = _bearish_kwargs()
    kwargs["signal_history"] = (
        _signal(
            captured_at=NOW.replace(hour=10, minute=56),
            direction="BULLISH",
            state="BULLISH CONFIRMED",
            ce=18,
            pe=78,
            condor=25,
        ),
        _signal(
            captured_at=NOW.replace(hour=10, minute=57),
            direction="BEARISH",
            state="TRANSITION / WAIT",
            ce=76,
            pe=24,
            condor=20,
        ),
        _signal(
            captured_at=NOW.replace(hour=10, minute=58),
            direction="BEARISH",
            state="TRANSITION / WAIT",
            ce=76,
            pe=24,
            condor=20,
        ),
    )
    result = calculate_final_decision(**kwargs)
    assert result.final_action == "CE SELL WITH HEDGE"
    assert "BEARISH CONFIRMED" in result.signal_state


def test_outlook_paths_are_normalized_to_one_hundred():
    kwargs = common_kwargs()
    kwargs["as_of"] = NOW
    result = calculate_final_decision(**kwargs)
    total = (
        result.outlook.bullish_path_pct
        + result.outlook.range_path_pct
        + result.outlook.bearish_path_pct
    )
    assert total == 100.0
    assert 0 <= result.outlook.fake_move_risk <= 100


def test_unavailable_option_chain_zeroes_all_seller_setup_scores():
    kwargs = common_kwargs()
    unavailable = replace(
        kwargs["options"],
        bullish_score=0.0,
        bearish_score=0.0,
        range_score=100.0,
        market_bias="UNAVAILABLE",
        persistence="UNAVAILABLE",
        confidence=0.0,
        status="UNAVAILABLE",
    )
    kwargs.update(options=unavailable, option_chain_live=False)
    result = calculate_final_decision(**kwargs)
    assert result.ce_sell.score == 0.0
    assert result.pe_sell.score == 0.0
    assert result.iron_condor.score == 0.0
    assert result.ce_sell.status == "UNAVAILABLE"
    assert result.iron_condor.status == "UNAVAILABLE"
    assert result.final_action == "WAIT"
    assert result.outlook.range_path_pct >= 90.0
    assert result.outlook.bullish_path_pct > 0
    assert result.outlook.bearish_path_pct > 0


def _pattern_signal(
    *,
    name: str,
    direction: str,
    confidence: float,
    stage: str = "CONFIRMED",
) -> PatternSignal:
    bullish = 80.0 if direction == "BULLISH" else 8.0
    bearish = 80.0 if direction == "BEARISH" else 8.0
    neutral = 12.0 if direction in {"BULLISH", "BEARISH"} else 100.0
    return PatternSignal(
        family="TEST",
        name=name,
        direction=direction,
        stage=stage,
        strength="VERY STRONG",
        confidence=confidence,
        bullish_score=bullish if direction != "NEUTRAL" else 0.0,
        bearish_score=bearish if direction != "NEUTRAL" else 0.0,
        neutral_score=neutral,
        level_label="S" if direction == "BULLISH" else "R",
        level_value=24300.0,
        neckline=24340.0 if name in {"W", "M"} else None,
        age_candles=0,
        reasons=("test pattern",),
        status="READY",
    )


def _pattern_bundle(wm_direction: str, candle_direction: str) -> PatternEvidenceBundle:
    wm = _pattern_signal(name="W" if wm_direction == "BULLISH" else "M", direction=wm_direction, confidence=90.0)
    candle_name = "BULL ENGULF" if candle_direction == "BULLISH" else "BEAR ENGULF"
    candle = _pattern_signal(name=candle_name, direction=candle_direction, confidence=90.0)
    combined = wm_direction if wm_direction == candle_direction else "MIXED"
    return PatternEvidenceBundle(
        as_of=NOW,
        wm_3m=wm,
        candle_3m=candle,
        combined_direction=combined,
        combined_confidence=90.0,
        status="READY",
    )


def test_pattern_confirmation_is_bounded_inside_same_brain():
    baseline = calculate_final_decision(**common_kwargs())
    kwargs = common_kwargs()
    kwargs["patterns"] = _pattern_bundle("BULLISH", "BULLISH")
    result = calculate_final_decision(**kwargs)

    increase = result.pe_sell.score - baseline.pe_sell.score
    assert 0.0 < increase <= 12.0
    assert result.final_action in {"PE SELL WITH HEDGE", "WAIT"}
    assert result.hedge_required is True


def test_conflicting_wm_and_candle_add_wait_caution_not_a_second_action():
    kwargs = common_kwargs()
    kwargs["patterns"] = _pattern_bundle("BULLISH", "BEARISH")
    result = calculate_final_decision(**kwargs)

    assert result.wait_need.score >= 14.0
    cautions = result.ce_sell.cautions + result.pe_sell.cautions + result.iron_condor.cautions
    assert any("W/M and candle evidence conflict" in item for item in cautions)
    assert result.final_action in {"PE SELL WITH HEDGE", "CE SELL WITH HEDGE", "IRON CONDOR WITH HEDGE", "WAIT"}


def test_strong_aligned_momentum_can_select_ce_buy_from_same_brain():
    from types import SimpleNamespace

    kwargs = common_kwargs()
    kwargs["price_action"] = SimpleNamespace(
        combined_state="BULLISH",
        relationship="3m/15m BULLISH ALIGNED",
        three_minute=SimpleNamespace(invalidation_level=24310),
        fifteen_minute=SimpleNamespace(invalidation_level=24290),
    )
    kwargs["volume"] = SimpleNamespace(
        status="READY", overall_view="BULLISH PARTICIPATION"
    )
    result = calculate_final_decision(**kwargs)
    assert result.instant_action == "WAIT"
    assert result.final_action == "WAIT"
    assert result.ce_buy.score > result.pe_sell.score
    assert result.hedge_required is False


def test_strong_aligned_bearish_momentum_can_select_pe_buy_from_same_brain():
    from types import SimpleNamespace

    kwargs = _bearish_kwargs()
    kwargs["price_action"] = SimpleNamespace(
        combined_state="BEARISH",
        relationship="3m/15m BEARISH ALIGNED",
        three_minute=SimpleNamespace(invalidation_level=24390),
        fifteen_minute=SimpleNamespace(invalidation_level=24410),
    )
    kwargs["volume"] = SimpleNamespace(
        status="READY", overall_view="BEARISH PARTICIPATION"
    )
    result = calculate_final_decision(**kwargs)
    assert result.instant_action == "WAIT"
    assert result.final_action == "WAIT"
    assert result.pe_buy.score > result.ce_sell.score
    assert result.hedge_required is False


def test_buy_alignment_gate_does_not_revote_core_price_action_and_volume():
    from types import SimpleNamespace

    from analysis.decision import _directional_momentum_adjustments

    ce_adjust, pe_adjust, ce_cautions, pe_cautions = _directional_momentum_adjustments(
        core=SimpleNamespace(move_stage="EARLY"),
        price_action=SimpleNamespace(
            combined_state="BULLISH",
            relationship="3m/15m BULLISH ALIGNED",
        ),
        volume=SimpleNamespace(status="READY", overall_view="BULLISH PARTICIPATION"),
    )

    # CE BUY receives only the bounded timing bonus. Bullish price action and
    # volume are already inside Core Market Evidence and therefore do not add a
    # second positive direction vote here.
    assert ce_adjust == 11.0
    assert pe_adjust == -5.0
    assert ce_cautions == []
    assert "Price action is not bearish-aligned" in pe_cautions
    assert "Futures volume is not bearish-aligned" in pe_cautions


def test_reference_session_does_not_call_data_block_fake_move_100_percent():
    kwargs = common_kwargs()
    kwargs["market_session"] = MarketSession(
        "CLOSED_OR_STALE_SESSION",
        "LIVE SESSION NOT CONFIRMED — REFERENCE DATA",
        False,
        "fresh quote unavailable",
    )
    result = calculate_final_decision(**kwargs)
    assert result.final_action == "WAIT"
    assert result.outlook.fake_move_risk < 100.0
    assert result.outlook.fake_move_state == "REFERENCE"
    assert result.outlook.bullish_path_pct > 0
    assert result.outlook.bearish_path_pct > 0
    assert result.outlook.range_path_pct > 0
