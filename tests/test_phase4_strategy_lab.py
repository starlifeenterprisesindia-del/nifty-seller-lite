from types import SimpleNamespace

import pandas as pd
import pytest

from analysis.strategy_lab import (
    build_strategy_lab_payload,
    combined_greeks,
    expiry_pnl_points,
    payoff_curve,
    plan_summary,
    what_if_scenario,
)
from models import OptionLeg, SetupPlan


def _leg(role, side, strike, *, bid, ask, ltp=None, liq=90):
    return OptionLeg(
        role=role, side=side, strike=float(strike),
        last_price=float(ltp if ltp is not None else (bid+ask)/2),
        delta=None, oi=100000, volume=50000,
        bid=float(bid), ask=float(ask), spread_pct=1.0,
        distance_points=100.0, liquidity_score=float(liq), status="READY",
    )


def _ce_credit_plan():
    short = _leg("SHORT", "CE", 22700, bid=100, ask=102)
    hedge = _leg("HEDGE", "CE", 22800, bid=39, ask=40)
    return SetupPlan(
        name="CE SELL", short_legs=(short,), hedge_legs=(hedge,),
        estimated_credit_points=60.0, width_points=100.0, max_risk_points=40.0,
        lower_breakeven=None, upper_breakeven=22760.0,
        quality_score=82.0, status="READY", reasons=("test",), blocker="None",
    )


def _ce_buy_plan():
    long = _leg("LONG", "CE", 22700, bid=99, ask=100)
    short_hedge = _leg("SHORT HEDGE", "CE", 22800, bid=40, ask=41)
    return SetupPlan(
        name="CE BUY", short_legs=(short_hedge,), hedge_legs=(), long_legs=(long,),
        estimated_credit_points=None, estimated_debit_points=60.0,
        width_points=100.0, max_risk_points=60.0,
        lower_breakeven=None, upper_breakeven=22760.0,
        quality_score=78.0, status="READY", reasons=("test",), blocker="None",
    )


def _frame():
    return pd.DataFrame([
        {"side":"CE","strike":22700,"last_price":101,"top_bid_price":100,"top_ask_price":102,
         "delta":0.35,"gamma":0.0015,"theta":-8.0,"vega":11.0},
        {"side":"CE","strike":22800,"last_price":39.5,"top_bid_price":39,"top_ask_price":40,
         "delta":0.16,"gamma":0.0010,"theta":-5.0,"vega":7.0},
    ])


def test_credit_spread_expiry_payoff_and_limits_are_exact():
    plan = _ce_credit_plan()
    assert expiry_pnl_points(plan, 22600) == pytest.approx(60.0)
    assert expiry_pnl_points(plan, 22760) == pytest.approx(0.0)
    assert expiry_pnl_points(plan, 22900) == pytest.approx(-40.0)
    summary = plan_summary(plan, 22650, 65, _frame())
    assert summary["max_profit_points"] == 60.0
    assert summary["max_loss_points"] == 40.0
    assert summary["max_profit_rupees_per_lot"] == 3900.0
    assert summary["max_loss_rupees_per_lot"] == 2600.0
    assert summary["breakevens"] == [22760.0]
    assert len(summary["curve"]) == 161


def test_debit_spread_payoff_uses_long_plus_short_hedge_roles():
    plan = _ce_buy_plan()
    assert expiry_pnl_points(plan, 22600) == pytest.approx(-60.0)
    assert expiry_pnl_points(plan, 22760) == pytest.approx(0.0)
    assert expiry_pnl_points(plan, 22900) == pytest.approx(40.0)
    summary = plan_summary(plan, 22650, 65, _frame())
    assert summary["entry_type"] == "DEBIT"
    assert summary["max_profit_points"] == 40.0
    assert summary["max_loss_points"] == 60.0


def test_combined_greeks_use_position_signs():
    greeks = combined_greeks(_ce_credit_plan(), _frame())
    # short 22700 + long 22800
    assert greeks["delta"] == pytest.approx(-0.19)
    assert greeks["gamma"] == pytest.approx(-0.0005)
    assert greeks["theta"] == pytest.approx(3.0)
    assert greeks["vega"] == pytest.approx(-4.0)
    assert greeks["coverage_pct"] == 100.0


def test_what_if_is_local_and_read_only_math():
    result = what_if_scenario(
        _ce_credit_plan(), _frame(), spot=22650,
        spot_change_points=50, iv_change_points=1.0, minutes_forward=60, lot_size=65,
    )
    assert result["status"] == "READY"
    assert result["new_spot"] == 22700
    assert result["coverage_pct"] == 100.0
    assert isinstance(result["pnl_change_rupees_per_lot"], float)
    assert "not a forecast" in result["note"]


def test_payload_is_display_only_and_prefers_existing_selected_setup():
    ce = _ce_credit_plan()
    buy = _ce_buy_plan()
    snap = SimpleNamespace(
        snapshot_id="S1",
        nifty_quote={"last_price":22650},
        risk_profile=SimpleNamespace(lot_size=65),
        option_chain=_frame(),
        trade_plan=SimpleNamespace(
            ce_sell=ce, pe_sell=SetupPlan.unavailable("PE SELL","x"),
            iron_condor=SetupPlan.unavailable("IRON CONDOR","x"),
            ce_buy=buy, pe_buy=SetupPlan.unavailable("PE BUY","x"),
            selected_setup="CE SELL", candidate_setup="CE BUY",
        ),
    )
    payload = build_strategy_lab_payload(snap)
    assert payload["status"] == "READY"
    assert payload["preferred"] == "CE SELL"
    assert set(payload["plans"]) == {"CE SELL", "CE BUY"}
    assert payload["safety"] == {
        "broker_calls": 0, "brain_writes": 0, "threshold_tuning": False,
        "mode": "DISPLAY ONLY / ON DEMAND",
    }
