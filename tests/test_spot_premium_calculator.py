import pandas as pd
import pytest

from analysis.spot_premium_calculator import (
    calculate_spot_premium_range,
    calculate_target_premium,
    estimate_target_reach,
)


def test_target_reach_returns_bounded_eta_and_probability():
    result = estimate_target_reach(
        current_spot=24570,
        target_spot=24620,
        speed_score=40,
        speed_direction="UP",
        move_1m_points=2,
        move_3m_points=8,
        move_5m_points=12,
        expected_remaining_move_points=150,
        barrier_strength=60,
        break_pressure=50,
    )
    assert 1 <= result.minutes_low < result.minutes_high <= 240
    assert 5 <= result.probability_pct <= 95


def sample_chain() -> pd.DataFrame:
    rows = []
    ce = [
        (23900, 140.0, 0.80),
        (23950, 100.0, 0.68),
        (24000, 65.0, 0.55),
        (24050, 40.0, 0.40),
        (24100, 22.0, 0.27),
        (24150, 11.0, 0.16),
    ]
    pe = [
        (23900, 12.0, -0.17),
        (23950, 24.0, -0.28),
        (24000, 43.0, -0.42),
        (24050, 72.0, -0.58),
        (24100, 108.0, -0.72),
        (24150, 151.0, -0.84),
    ]
    for side, data in (("CE", ce), ("PE", pe)):
        for strike, price, delta in data:
            rows.append(
                {
                    "strike": strike,
                    "side": side,
                    "last_price": price,
                    "top_bid_price": price - 0.5,
                    "top_ask_price": price + 0.5,
                    "implied_volatility": 14.0,
                    "delta": delta,
                    "gamma": 0.005,
                    "theta": -12.0,
                    "vega": 8.0,
                }
            )
    return pd.DataFrame(rows)


def test_single_barrier_target_reuses_manual_premium_engine():
    kwargs = dict(
        option_chain=sample_chain(),
        side="CE",
        position="SELL",
        strike=24000,
        current_spot=24000,
        current_premium=65,
        entry_premium=65,
        target_minutes=15,
        lot_size=65,
        lots=2,
        feed_state="LIVE",
        iv_change_points=0,
    )
    target = calculate_target_premium(target_spot=24050, **kwargs)
    manual = calculate_spot_premium_range(
        lower_spot=23999.99,
        upper_spot=24050,
        **kwargs,
    )
    assert target.best_price == manual.upper.best_price
    assert target.total_pnl == manual.upper.total_pnl


def test_ce_premium_rises_with_spot_and_sell_pnl_moves_opposite():
    result = calculate_spot_premium_range(
        option_chain=sample_chain(),
        side="CE",
        position="SELL",
        strike=24050,
        current_spot=24010,
        current_premium=40,
        entry_premium=40,
        lower_spot=23980,
        upper_spot=24060,
        target_minutes=15,
        lot_size=65,
        lots=1,
        feed_state="LIVE",
    )
    assert result.lower.best_price < result.current.best_price < result.upper.best_price
    assert result.lower.total_pnl > 0
    assert result.upper.total_pnl < 0
    assert result.lower.exit_action == "BUY BACK"


def test_pe_premium_rises_when_spot_falls_for_buyer():
    result = calculate_spot_premium_range(
        option_chain=sample_chain(),
        side="PE",
        position="BUY",
        strike=24050,
        current_spot=24010,
        current_premium=72,
        entry_premium=72,
        lower_spot=23980,
        upper_spot=24060,
        target_minutes=15,
        lot_size=65,
        lots=2,
        feed_state="LIVE",
    )
    assert result.lower.best_price > result.current.best_price > result.upper.best_price
    assert result.lower.total_pnl > 0
    assert result.upper.total_pnl < 0
    assert result.lower.exit_action == "SELL EXIT"
    assert result.lower.total_pnl == pytest.approx(result.lower.pnl_per_lot * 2, abs=0.02)


def test_current_scenario_is_anchored_to_manual_current_premium():
    result = calculate_spot_premium_range(
        option_chain=sample_chain(),
        side="CE",
        position="BUY",
        strike=24050,
        current_spot=24010,
        current_premium=51.95,
        entry_premium=50,
        lower_spot=24000,
        upper_spot=24050,
        target_minutes=5,
        lot_size=65,
        lots=1,
        feed_state="LIVE",
    )
    assert result.current.best_price == 51.95
    assert result.current.pnl_per_quantity == 1.95


def test_reference_feed_caps_reliability_and_sets_status():
    result = calculate_spot_premium_range(
        option_chain=sample_chain(),
        side="PE",
        position="SELL",
        strike=24050,
        current_spot=24010,
        current_premium=72,
        entry_premium=72,
        lower_spot=23980,
        upper_spot=24060,
        target_minutes=15,
        lot_size=65,
        lots=1,
        feed_state="REFERENCE",
    )
    assert result.status == "REFERENCE ONLY"
    assert result.overall_reliability <= 48
    assert any("Live option-chain unavailable" in warning for warning in result.warnings)


def test_invalid_manual_range_is_rejected():
    with pytest.raises(ValueError, match="Lower range"):
        calculate_spot_premium_range(
            option_chain=sample_chain(),
            side="CE",
            position="SELL",
            strike=24050,
            current_spot=24010,
            current_premium=40,
            entry_premium=40,
            lower_spot=24060,
            upper_spot=23980,
            target_minutes=15,
            lot_size=65,
            lots=1,
            feed_state="LIVE",
        )


def test_missing_greeks_falls_back_without_crashing():
    frame = sample_chain().drop(columns=["delta", "gamma", "theta", "vega"])
    result = calculate_spot_premium_range(
        option_chain=frame,
        side="CE",
        position="SELL",
        strike=24050,
        current_spot=24010,
        current_premium=40,
        entry_premium=40,
        lower_spot=23980,
        upper_spot=24060,
        target_minutes=15,
        lot_size=65,
        lots=1,
        feed_state="LIVE",
    )
    assert result.lower.best_price >= 0
    assert result.upper.best_price >= 0
    assert "OPTION CHAIN SHIFT" in result.lower.methods


def test_calculator_is_read_only_and_not_a_second_strategy_brain():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    module = (root / "analysis" / "spot_premium_calculator.py").read_text(encoding="utf-8")
    app = (root / "app.py").read_text(encoding="utf-8")
    assert "calculate_final_decision(" not in module
    assert "DhanClient(" not in module
    assert "requests." not in module
    assert "PLACE_ORDER" not in module.upper()
    assert app.count("render_spot_premium_calculator(view_snapshot)") == 1


def test_invalid_iv_is_ignored_safely():
    frame = sample_chain()
    frame.loc[(frame["side"] == "CE") & (frame["strike"] == 24050), "implied_volatility"] = 0.0
    result = calculate_spot_premium_range(
        option_chain=frame,
        side="CE",
        position="SELL",
        strike=24050,
        current_spot=24010,
        current_premium=40,
        entry_premium=40,
        lower_spot=23980,
        upper_spot=24060,
        target_minutes=15,
        lot_size=65,
        lots=1,
        feed_state="LIVE",
    )
    assert result.current_iv is None
    assert any("IV invalid/out-of-range" in warning for warning in result.warnings)


def test_premium_breakdown_separates_intrinsic_and_time_value():
    result = calculate_spot_premium_range(
        option_chain=sample_chain(),
        side="CE",
        position="BUY",
        strike=24000,
        current_spot=24010,
        current_premium=65,
        entry_premium=65,
        lower_spot=23980,
        upper_spot=24060,
        target_minutes=15,
        lot_size=65,
        lots=1,
        feed_state="LIVE",
    )
    assert result.current_intrinsic_value == 10
    assert result.current_time_value == 55
    assert result.current_time_value_share_pct == pytest.approx(84.6, abs=0.1)
    assert result.current.intrinsic_value == 10
    assert result.current.time_value == 55


def test_positive_iv_change_raises_target_premium_when_vega_is_available():
    base = calculate_spot_premium_range(
        option_chain=sample_chain(),
        side="CE",
        position="BUY",
        strike=24050,
        current_spot=24010,
        current_premium=40,
        entry_premium=40,
        lower_spot=23980,
        upper_spot=24060,
        target_minutes=15,
        lot_size=65,
        lots=1,
        feed_state="LIVE",
        iv_change_points=0,
    )
    higher_iv = calculate_spot_premium_range(
        option_chain=sample_chain(),
        side="CE",
        position="BUY",
        strike=24050,
        current_spot=24010,
        current_premium=40,
        entry_premium=40,
        lower_spot=23980,
        upper_spot=24060,
        target_minutes=15,
        lot_size=65,
        lots=1,
        feed_state="LIVE",
        iv_change_points=2,
    )
    assert higher_iv.upper.best_price > base.upper.best_price
    assert higher_iv.upper.iv_effect == 16
    assert higher_iv.target_iv == 16


def test_negative_iv_change_lowers_time_value_but_not_below_intrinsic():
    result = calculate_spot_premium_range(
        option_chain=sample_chain(),
        side="CE",
        position="BUY",
        strike=24000,
        current_spot=24010,
        current_premium=65,
        entry_premium=65,
        lower_spot=24005,
        upper_spot=24150,
        target_minutes=60,
        lot_size=65,
        lots=1,
        feed_state="LIVE",
        iv_change_points=-10,
    )
    assert result.upper.best_price >= result.upper.intrinsic_value
    assert result.upper.time_value >= 0


def test_sideways_decay_shows_15_30_60_minutes_and_respects_intrinsic_floor():
    result = calculate_spot_premium_range(
        option_chain=sample_chain(),
        side="CE",
        position="SELL",
        strike=24000,
        current_spot=24010,
        current_premium=65,
        entry_premium=65,
        lower_spot=23980,
        upper_spot=24060,
        target_minutes=15,
        lot_size=65,
        lots=1,
        feed_state="LIVE",
    )
    assert [item.minutes for item in result.decay_scenarios] == [15, 30, 60]
    assert result.decay_scenarios[0].estimated_premium > result.decay_scenarios[-1].estimated_premium
    assert all(item.estimated_premium >= item.intrinsic_value for item in result.decay_scenarios)
    assert result.decay_scenarios[-1].total_pnl > result.decay_scenarios[0].total_pnl


def test_sideways_decay_uses_expiry_aware_theta_curve():
    far = calculate_spot_premium_range(
        option_chain=sample_chain(), side="CE", position="SELL", strike=24000,
        current_spot=24010, current_premium=65, entry_premium=65,
        lower_spot=23980, upper_spot=24060, target_minutes=15,
        lot_size=65, lots=1, feed_state="LIVE", minutes_to_expiry=1440,
    )
    near = calculate_spot_premium_range(
        option_chain=sample_chain(), side="CE", position="SELL", strike=24000,
        current_spot=24010, current_premium=65, entry_premium=65,
        lower_spot=23980, upper_spot=24060, target_minutes=15,
        lot_size=65, lots=1, feed_state="LIVE", minutes_to_expiry=90,
    )
    assert near.decay_scenarios[0].total_pnl > far.decay_scenarios[0].total_pnl
    assert "expiry-aware" in near.decay_scenarios[0].note


def test_driver_breakdown_exposes_spot_theta_iv_and_chain_components():
    result = calculate_spot_premium_range(
        option_chain=sample_chain(),
        side="PE",
        position="BUY",
        strike=24050,
        current_spot=24010,
        current_premium=72,
        entry_premium=72,
        lower_spot=23980,
        upper_spot=24060,
        target_minutes=30,
        lot_size=65,
        lots=1,
        feed_state="LIVE",
        iv_change_points=1.5,
    )
    assert result.lower.spot_move_effect > 0
    assert result.lower.theta_effect < 0
    assert result.lower.iv_effect == 12
    assert isinstance(result.lower.chain_smile_effect, float)


def test_iv_change_that_makes_target_iv_non_positive_is_rejected():
    with pytest.raises(ValueError, match="target IV zero/negative"):
        calculate_spot_premium_range(
            option_chain=sample_chain(),
            side="CE",
            position="SELL",
            strike=24050,
            current_spot=24010,
            current_premium=40,
            entry_premium=40,
            lower_spot=23980,
            upper_spot=24060,
            target_minutes=15,
            lot_size=65,
            lots=1,
            feed_state="LIVE",
            iv_change_points=-14,
        )


def test_v219_ui_is_compact_and_keeps_optional_manual_and_iv_controls():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ui = (root / "ui" / "premium_calculator.py").read_text(encoding="utf-8")
    assert "Calculate Premium at R1/R2/S1/S2" in ui
    assert "Apna Upper/Lower target bhi check karo" in ui
    assert "Advanced IV/Time details" in ui
    assert "IV change scenario (optional)" in ui
    assert "Premium Breakdown — Abhi" not in ui
    assert "Selected strike OI" not in ui
