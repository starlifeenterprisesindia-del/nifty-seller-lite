from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from analysis.execution_guard import calculate_execution_guard
from models import (
    DisciplineState,
    ExecutionGuard,
    FeedStatus,
    FinalDecision,
    MarketSession,
    OptionLeg,
    PriceActionBundle,
    RiskProfile,
    SetupPlan,
    StrategyEvaluation,
    TimeframePriceAction,
    TradePlanBundle,
)

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 7, 20, 10, 30, tzinfo=IST)


def evaluation(name: str, score: float) -> StrategyEvaluation:
    return StrategyEvaluation(name, score, "READY", ("reason",), ())


def decision(action: str = "PE SELL WITH HEDGE") -> FinalDecision:
    return FinalDecision(
        ce_sell=evaluation("CE SELL", 20),
        pe_sell=evaluation("PE SELL", 80),
        iron_condor=evaluation("IRON CONDOR", 30),
        wait_need=evaluation("WAIT NEED", 15 if action != "WAIT" else 80),
        final_action=action,
        execution_status="READY" if action != "WAIT" else "BLOCKED",
        decision_confidence=78,
        hedge_required=True,
        reasons=("reason",),
        blocker="None" if action != "WAIT" else "No clear edge",
        status="READY",
    )


def leg(role: str, side: str, strike: float) -> OptionLeg:
    return OptionLeg(
        role=role,
        side=side,
        strike=strike,
        last_price=20,
        delta=-0.2 if side == "PE" else 0.2,
        oi=10000,
        volume=5000,
        bid=19.5,
        ask=20.5,
        spread_pct=5,
        distance_points=150,
        liquidity_score=80,
        status="READY",
    )


def plan() -> SetupPlan:
    return SetupPlan(
        name="PE SELL",
        short_legs=(leg("SHORT", "PE", 24200),),
        hedge_legs=(leg("HEDGE", "PE", 24100),),
        estimated_credit_points=12,
        width_points=100,
        max_risk_points=88,
        lower_breakeven=24188,
        upper_breakeven=None,
        quality_score=78,
        status="READY",
        reasons=("protected",),
        blocker="None",
    )


def trade_plan() -> TradePlanBundle:
    unavailable = SetupPlan.unavailable("CE SELL", "not selected")
    return TradePlanBundle(
        as_of=NOW,
        expiry="2026-07-21",
        spot=24350,
        ce_sell=unavailable,
        pe_sell=plan(),
        iron_condor=SetupPlan.unavailable("IRON CONDOR", "not selected"),
        selected_setup="PE SELL",
        status="READY",
        blocker="None",
    )


def pa_item(label: str, invalidation: float) -> TimeframePriceAction:
    return TimeframePriceAction(
        timeframe=label,
        as_of=NOW,
        structure="BULLISH HH/HL",
        event="BULLISH CONTINUATION",
        move_stage="DEVELOPING",
        last_swing_high=24400,
        prior_swing_high=24380,
        last_swing_low=invalidation,
        prior_swing_low=invalidation - 20,
        invalidation_level=invalidation,
        atr14=20,
        bullish_score=80,
        bearish_score=10,
        range_score=20,
        confidence=85,
        reasons=("bullish",),
        status="READY",
    )


def price_action() -> PriceActionBundle:
    return PriceActionBundle(
        three_minute=pa_item("3 Minute", 24300),
        fifteen_minute=pa_item("15 Minute", 24280),
        relationship="ALIGNED",
        combined_state="BULLISH",
        confidence=85,
    )


def risk_profile(capital: float = 2_500_000) -> RiskProfile:
    return RiskProfile(
        capital_rupees=capital,
        risk_pct=0.5,
        lot_size=65,
        max_lots_cap=1,
        target_capture_pct=35,
        stop_loss_pct=40,
        entry_start=time(10, 15),
        entry_end=time(11, 30),
        forced_exit=time(14, 30),
    )


def feeds(live: bool = True) -> dict[str, FeedStatus]:
    use = "LIVE" if live else "REFERENCE"
    return {
        name: FeedStatus(name, True, NOW, 1, "ok", "test", use)
        for name in ("quotes", "candles", "option_chain")
    }


def discipline(confirmations: int = 2, locked: bool = False) -> DisciplineState:
    history = tuple(
        {
            "captured_at": (NOW - timedelta(minutes=confirmations - i - 1)).isoformat(),
            "action": "PE SELL WITH HEDGE",
            "execution_status": "READY",
        }
        for i in range(confirmations)
    )
    return DisciplineState(
        session_date=NOW.date().isoformat(),
        trades_taken=1 if locked else 0,
        day_locked=locked,
        last_outcome="OPEN" if locked else "",
        last_action="PE SELL WITH HEDGE",
        signal_history=history,
        status="READY",
    )


def run(**overrides) -> ExecutionGuard:
    kwargs = {
        "decision": decision(),
        "trade_plan": trade_plan(),
        "market_session": MarketSession("LIVE", "LIVE", True, "fresh"),
        "price_action": price_action(),
        "risk_profile": risk_profile(),
        "discipline_state": discipline(),
        "feed_status": feeds(),
        "as_of": NOW,
    }
    kwargs.update(overrides)
    return calculate_execution_guard(**kwargs)


def test_two_fresh_confirmations_and_risk_budget_make_entry_ready():
    result = run()
    assert result.readiness == "ENTRY READY"
    assert result.confirmations >= 2
    assert result.allowed_lots == 1
    assert result.target_profit_rupees > 0
    assert result.stop_loss_rupees > 0
    assert result.spot_invalidation_low == 24300


def test_first_signal_is_blocked_until_persistence_is_ready():
    result = run(discipline_state=discipline(confirmations=1))
    assert result.readiness == "BLOCKED"
    assert "confirmations" in " ".join(result.blockers).lower()


def test_reference_session_never_becomes_entry_ready():
    result = run(
        market_session=MarketSession("WEEKEND", "CLOSED", False, "reference"),
        feed_status=feeds(live=False),
    )
    assert result.readiness == "REFERENCE ONLY"
    assert result.allowed_lots == 1


def test_one_trade_used_locks_the_day():
    result = run(discipline_state=discipline(locked=True))
    assert result.readiness == "BLOCKED"
    assert "one-trade" in " ".join(result.blockers).lower()


def test_after_entry_window_blocks_new_trade():
    result = run(as_of=NOW.replace(hour=12, minute=0))
    assert result.readiness == "BLOCKED"
    assert "window" in " ".join(result.blockers).lower()


def test_small_risk_budget_permits_zero_lots():
    result = run(risk_profile=risk_profile(capital=50_000))
    assert result.allowed_lots == 0
    assert result.readiness == "BLOCKED"
    assert "risk budget" in " ".join(result.blockers).lower()


def test_wait_action_cannot_be_overridden_by_guard():
    result = run(decision=decision("WAIT"))
    assert result.readiness == "BLOCKED"
    assert result.selected_setup == "PE SELL"
    assert "final one-brain action is wait" in " ".join(result.blockers).lower()
