from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from analysis.sl_target_planner import (
    build_sl_target_plan,
    directional_intent,
    expiry_context,
    lots_for_max_loss,
    stop_buffer_points,
)
from analysis.spot_premium_calculator import (
    PremiumRangeEstimate,
    _expiry_aware_theta_effect,
)


def _premium(best: float, low: float, high: float) -> PremiumRangeEstimate:
    return PremiumRangeEstimate(
        label="TEST",
        target_spot=0.0,
        best_price=best,
        low_price=low,
        high_price=high,
        pnl_per_quantity=0.0,
        pnl_per_lot=0.0,
        total_pnl=0.0,
        outcome="TEST",
        exit_action="TEST",
        reliability=80.0,
        methods=("TEST",),
        notes=(),
        intrinsic_value=0.0,
        time_value=best,
        spot_move_effect=0.0,
        theta_effect=0.0,
        iv_effect=0.0,
        chain_smile_effect=0.0,
    )


def _level(label: str, lower: float, upper: float):
    return SimpleNamespace(
        label=label,
        lower=lower,
        upper=upper,
        midpoint=(lower + upper) / 2,
        strength=70.0,
        break_pressure=50.0,
    )


def test_expiry_context_uses_exact_1530_remaining_minutes():
    captured = datetime(2026, 8, 13, 14, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    result = expiry_context(captured_at=captured, expiry="2026-08-13")
    assert result.regime == "EXPIRY DAY"
    assert result.minutes_remaining == 60


def test_direction_maps_buy_and_sell_without_a_second_signal_brain():
    assert directional_intent(side="CE", position="BUY") == "BULLISH"
    assert directional_intent(side="PE", position="SELL") == "BULLISH"
    assert directional_intent(side="PE", position="BUY") == "BEARISH"
    assert directional_intent(side="CE", position="SELL") == "BEARISH"


def test_expiry_buffer_is_wider_for_same_atr():
    captured = datetime(2026, 8, 13, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    expiry_day = expiry_context(captured_at=captured, expiry="2026-08-13")
    non_expiry = expiry_context(captured_at=captured, expiry="2026-08-20")
    assert stop_buffer_points(atr3=20, zone_width=8, expiry=expiry_day) == 6.0
    assert stop_buffer_points(atr3=20, zone_width=8, expiry=non_expiry) == 4.0


def test_ce_sell_plan_uses_resistance_and_conservative_ask_side():
    captured = datetime(2026, 8, 10, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    expiry = expiry_context(captured_at=captured, expiry="2026-08-13")
    barrier = SimpleNamespace(
        nearest_support=_level("S1", 24558, 24568),
        nearest_resistance=_level("R1", 24587, 24597),
    )
    plan = build_sl_target_plan(
        side="CE",
        position="SELL",
        entry_premium=86.05,
        lot_size=65,
        lots=10,
        entry_spot=24571,
        current_spot=24571,
        barrier_map=barrier,
        atr3=20,
        zone_width=10,
        expiry=expiry,
        stop_estimate=_premium(95.13, 93.5, 97.0),
        target_estimates=[
            ("S1", 24563, _premium(82.66, 81.5, 83.8), 25),
            ("S2", 24538, _premium(72.06, 70.8, 73.3), 104),
        ],
        holding_limit_minutes=180,
    )
    assert plan.direction == "BEARISH"
    assert plan.stop_level_label == "R1"
    assert plan.stop_spot == 24601.0
    assert plan.stop_premium == 97.0
    assert plan.total_risk == 7117.5
    assert plan.targets[1].risk_reward > plan.targets[0].risk_reward


def test_expiry_theta_curve_is_bounded_by_available_time_value():
    effect = _expiry_aware_theta_effect(
        theta=-100.0,
        target_minutes=60,
        minutes_to_expiry=60,
        current_time_value=5.0,
    )
    assert effect == -5.0


def test_lots_are_derived_from_rupee_loss_cap():
    assert lots_for_max_loss(max_loss_rupees=4000, risk_per_quantity=10, lot_size=65) == 6
