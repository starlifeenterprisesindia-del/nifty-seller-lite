from __future__ import annotations

import importlib.util
from datetime import timedelta
from pathlib import Path

from analysis.market_intelligence import calculate_market_intelligence


def _helpers():
    path = Path(__file__).resolve().parent / "test_market_intelligence.py"
    spec = importlib.util.spec_from_file_location("mi_helpers", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_liquidity_map_reuses_shadow_result_without_core_feedback():
    h = _helpers()
    snap = h._snapshot(bullish=True, one_brain="UP", expansion=True)
    result = calculate_market_intelligence(snap)
    liq = result.liquidity
    assert liq.hunt_bias == "UPSIDE"
    assert liq.primary_zone is not None
    assert liq.primary_zone.side == "UPSIDE"
    assert liq.upside_hunt_pressure > liq.downside_hunt_pressure
    assert liq.reach_state in {"LIKELY", "POSSIBLE", "NOT YET SUPPORTED"}
    assert result.mode == "SHADOW_ONLY_ZERO_CORE_WEIGHT"


def test_move_radar_blinks_green_for_strong_bullish_build_up():
    h = _helpers()
    result = calculate_market_intelligence(h._snapshot(bullish=True, one_brain="UP", expansion=True))
    assert result.move_radar["visual"] == "GREEN"
    assert result.move_radar["blink"] is True
    assert "BULLISH" in result.move_radar["state"]


def test_move_radar_blinks_red_for_strong_bearish_build_up():
    h = _helpers()
    result = calculate_market_intelligence(h._snapshot(bullish=False, one_brain="DOWN", expansion=True))
    assert result.move_radar["visual"] == "RED"
    assert result.move_radar["blink"] is True
    assert "BEARISH" in result.move_radar["state"]


def test_prior_liquidity_target_breach_detects_continuation_without_future_leakage():
    h = _helpers()
    previous = h._snapshot(bullish=True, one_brain="UP", expansion=True)
    previous_result = calculate_market_intelligence(previous)
    previous.metadata["market_intelligence"] = previous_result.to_dict()
    target = previous_result.liquidity.primary_zone
    assert target is not None

    current = h._snapshot(bullish=True, one_brain="UP", expansion=True)
    current.created_at = h.NOW + timedelta(seconds=30)
    idx = current.candles_1m.index[-1]
    current.candles_1m.loc[idx, "high"] = target.upper + 12.0
    current.candles_1m.loc[idx, "close"] = target.upper + 6.0
    current.nifty_quote["last_price"] = target.upper + 6.0

    result = calculate_market_intelligence(current, previous)
    assert result.liquidity.sweep_state == "UPSIDE LIQUIDITY BREACHED"
    assert result.liquidity.sweep_outcome == "CONTINUATION FAVORED"
    assert any(row["kind"] == "LIQUIDITY_SWEEP" for row in result.alerts)


def test_core_engine_files_still_do_not_reference_market_or_liquidity_intelligence():
    root = Path(__file__).resolve().parents[1]
    protected = (
        "analysis/decision.py",
        "analysis/simple_brain.py",
        "analysis/future_brain.py",
        "analysis/execution_guard.py",
        "analysis/trade_plan.py",
        "analysis/position_guardian.py",
        "services/snapshot_service.py",
    )
    for relative in protected:
        text = (root / relative).read_text(encoding="utf-8").lower()
        assert "market_intelligence" not in text, relative
        assert "liquidity_intelligence" not in text, relative
