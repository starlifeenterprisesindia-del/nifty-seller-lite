from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from analysis.position_guardian import (
    calculate_position_guardian,
    create_trade_record,
)
from models import (
    DisciplineState,
    ExecutionGuard,
    FinalDecision,
    MarketSession,
    OptionLeg,
    SetupPlan,
    StrategyEvaluation,
    TradePlanBundle,
)

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 7, 20, 11, 0, tzinfo=IST)


def evaluation(name: str, score: float) -> StrategyEvaluation:
    return StrategyEvaluation(name, score, "READY", ("reason",), ())


def decision() -> FinalDecision:
    return FinalDecision(
        ce_sell=evaluation("CE SELL", 20),
        pe_sell=evaluation("PE SELL", 80),
        iron_condor=evaluation("IRON CONDOR", 30),
        wait_need=evaluation("WAIT NEED", 15),
        final_action="PE SELL WITH HEDGE",
        execution_status="READY",
        decision_confidence=80,
        hedge_required=True,
        reasons=("reason",),
        blocker="None",
        status="READY",
    )


def leg(role: str, strike: float, bid: float, ask: float) -> OptionLeg:
    return OptionLeg(
        role=role,
        side="PE",
        strike=strike,
        last_price=(bid + ask) / 2,
        delta=-0.2,
        oi=10000,
        volume=5000,
        bid=bid,
        ask=ask,
        spread_pct=5,
        distance_points=150,
        liquidity_score=80,
        status="READY",
    )


def setup() -> SetupPlan:
    return SetupPlan(
        name="PE SELL",
        short_legs=(leg("SHORT", 24200, 20, 21),),
        hedge_legs=(leg("HEDGE", 24100, 7, 8),),
        estimated_credit_points=12,
        width_points=100,
        max_risk_points=88,
        lower_breakeven=24188,
        upper_breakeven=None,
        quality_score=80,
        status="READY",
        reasons=("protected",),
        blocker="None",
    )


def bundle() -> TradePlanBundle:
    return TradePlanBundle(
        as_of=NOW,
        expiry="2026-07-21",
        spot=24350,
        ce_sell=SetupPlan.unavailable("CE SELL", "not selected"),
        pe_sell=setup(),
        iron_condor=SetupPlan.unavailable("IRON CONDOR", "not selected"),
        selected_setup="PE SELL",
        status="READY",
        blocker="None",
    )


def guard(readiness: str = "ENTRY READY") -> ExecutionGuard:
    return ExecutionGuard(
        as_of=NOW,
        selected_setup="PE SELL",
        readiness=readiness,
        signal_state="CONFIRMED ×2",
        confirmations=2,
        required_confirmations=2,
        entry_window="OPEN",
        risk_budget_rupees=1250,
        risk_per_lot_rupees=5720,
        allowed_lots=1,
        max_lots_by_budget=1,
        max_lots_cap=1,
        target_capture_points=4.2,
        target_exit_debit_points=7.8,
        target_profit_rupees=273,
        stop_loss_points=4.8,
        stop_exit_debit_points=16.8,
        stop_loss_rupees=312,
        forced_exit_time="14:30",
        spot_invalidation_low=24300,
        spot_invalidation_high=None,
        trade_taken_today=False,
        day_locked=False,
        reasons=("ready",),
        blockers=(),
        status=readiness,
    )


def trade_record() -> dict:
    return create_trade_record(
        captured_at=NOW,
        decision=decision(),
        trade_plan=bundle(),
        execution_guard=guard(),
        lots=1,
        lot_size=65,
        spot=24350,
    )


def discipline(record: dict | None) -> DisciplineState:
    return DisciplineState(
        session_date=NOW.date().isoformat(),
        trades_taken=1 if record else 0,
        day_locked=bool(record),
        last_outcome="OPEN" if record else "",
        last_action="PE SELL WITH HEDGE" if record else "",
        signal_history=(),
        status="READY",
        trade_record=record,
    )


def chain(short_ask: float, hedge_bid: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "strike": 24200.0,
                "side": "PE",
                "last_price": short_ask,
                "top_bid_price": short_ask - 0.5,
                "top_ask_price": short_ask,
            },
            {
                "strike": 24100.0,
                "side": "PE",
                "last_price": hedge_bid,
                "top_bid_price": hedge_bid,
                "top_ask_price": hedge_bid + 0.5,
            },
        ]
    )


def calculate(record: dict | None, frame: pd.DataFrame, **overrides):
    kwargs = {
        "discipline_state": discipline(record),
        "option_chain": frame,
        "current_expiry": "2026-07-21",
        "current_spot": 24350,
        "market_session": MarketSession("LIVE", "LIVE", True, "fresh"),
        "option_chain_live": True,
        "as_of": NOW,
    }
    kwargs.update(overrides)
    return calculate_position_guardian(**kwargs)


def test_trade_record_freezes_exact_protected_legs_and_risk_rules():
    record = trade_record()
    assert record["status"] == "OPEN"
    assert record["entry_credit_points"] == pytest.approx(12.0)
    assert record["lots"] == 1
    assert [(item["role"], item["strike"]) for item in record["legs"]] == [
        ("SHORT", 24200.0),
        ("HEDGE", 24100.0),
    ]


def test_only_entry_ready_plan_can_be_recorded():
    with pytest.raises(ValueError, match="ENTRY READY"):
        create_trade_record(
            captured_at=NOW,
            decision=decision(),
            trade_plan=bundle(),
            execution_guard=guard("BLOCKED"),
            lots=1,
            lot_size=65,
            spot=24350,
        )


def test_target_alert_uses_combination_close_debit():
    result = calculate(trade_record(), chain(short_ask=10, hedge_bid=3))
    assert result.current_debit_points == 7
    assert result.unrealized_pnl_points == 5
    assert result.unrealized_pnl_rupees == 325
    assert result.instruction == "TARGET REACHED"
    assert result.status == "TARGET ALERT"


def test_stop_alert_uses_combination_close_debit():
    result = calculate(trade_record(), chain(short_ask=20, hedge_bid=2))
    assert result.current_debit_points == 18
    assert result.instruction == "SL TRIGGERED"
    assert result.status == "EXIT ALERT"


def test_spot_invalidation_and_forced_exit_are_hard_alerts():
    invalidated = calculate(
        trade_record(), chain(short_ask=15, hedge_bid=5), current_spot=24290
    )
    assert "SPOT INVALIDATION" in invalidated.instruction

    after_time = calculate(
        trade_record(),
        chain(short_ask=15, hedge_bid=5),
        as_of=NOW.replace(hour=14, minute=31),
    )
    assert after_time.instruction == "EXIT NOW — TIME"


def test_reference_session_never_shows_live_hold_or_exit_instruction():
    result = calculate(
        trade_record(),
        chain(short_ask=10, hedge_bid=3),
        market_session=MarketSession("WEEKEND", "CLOSED", False, "reference"),
        option_chain_live=False,
    )
    assert result.instruction == "REFERENCE ONLY"
    assert result.status == "REFERENCE ONLY"


def test_missing_exact_leg_blocks_position_math():
    result = calculate(trade_record(), chain(short_ask=10, hedge_bid=3).iloc[:1])
    assert result.status == "DATA BLOCKED"
    assert "24,100" in " ".join(result.blockers)


def test_no_manual_trade_returns_idle_guardian():
    result = calculate(None, pd.DataFrame())
    assert result.status == "IDLE"
    assert result.instruction == "NO OPEN TRADE"


def test_entry_credit_uses_exact_plan_value_not_rounded_target_reconstruction():
    from dataclasses import replace

    rounded_guard = replace(
        guard(), target_capture_points=4.2, target_exit_debit_points=7.79
    )
    record = create_trade_record(
        captured_at=NOW,
        decision=decision(),
        trade_plan=bundle(),
        execution_guard=rounded_guard,
        lots=1,
        lot_size=65,
        spot=24350,
    )
    assert record["entry_credit_points"] == pytest.approx(12.0)
