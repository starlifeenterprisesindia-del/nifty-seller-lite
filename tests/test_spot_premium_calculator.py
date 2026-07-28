import pandas as pd
import pytest

from analysis.spot_premium_calculator import calculate_spot_premium_range


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
    assert app.count("render_spot_premium_calculator(snapshot)") == 1
