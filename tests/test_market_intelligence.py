from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pandas as pd

from analysis.market_intelligence import calculate_market_intelligence


NOW = datetime(2026, 10, 6, 11, 0)


def _feed(state="LIVE"):
    return NS(ok=True, use_state=state, status="READY")


def _candles(direction=1, *, compression=False):
    rows = []
    price = 25000.0
    for i in range(18):
        if compression and 10 <= i <= 15:
            step = direction * (0.5 + (i - 10) * 0.05)
            span = 2.0
        elif i == 17:
            step = direction * 7.0
            span = 11.0
        else:
            step = direction * 2.0
            span = 5.0
        old = price
        price += step
        rows.append({
            "timestamp": NOW - timedelta(minutes=17 - i),
            "open": old,
            "high": max(old, price) + span / 2,
            "low": min(old, price) - span / 2,
            "close": price,
            "volume": 1000 + i * 30,
            "open_interest": 100000 + direction * i * 120,
            "is_complete": True,
        })
    return pd.DataFrame(rows)


def _option_chain(spot=25040, bullish=True):
    rows = []
    for strike in (24900, 24950, 25000, 25050, 25100):
        for side in ("CE", "PE"):
            base = max(25.0, 120.0 - abs(strike - spot) * 0.6)
            if bullish:
                price = base + (10 if side == "CE" else -5)
            else:
                price = base + (10 if side == "PE" else -5)
            rows.append({
                "strike": float(strike), "side": side, "last_price": price,
                "top_bid_price": max(0.5, price - 0.5), "top_ask_price": price + 0.5,
                "oi": 100000 + (strike % 100) * 100, "volume": 10000,
                "implied_volatility": 14.0 + (0.6 if bullish else 0.2),
            })
    return pd.DataFrame(rows)


def _snapshot(*, bullish=True, one_brain="UP", expansion=True):
    d = 1 if bullish else -1
    bull, bear = ((78.0, 16.0) if bullish else (16.0, 78.0))
    bias = "BULLISH" if bullish else "BEARISH"
    futures_setup = "LONG BUILD-UP" if bullish else "SHORT BUILD-UP"
    future_direction = "BUYING" if bullish else "SELLING"
    pa3 = NS(status="READY", bullish_score=bull, bearish_score=bear, range_score=20.0,
             structure=bias, event="BREAKOUT DEVELOPING" if bullish else "BREAKDOWN DEVELOPING",
             invalidation_level=24990.0 if bullish else 25090.0, confidence=85.0)
    pa15 = NS(status="READY", bullish_score=bull - 5, bearish_score=bear + 2, range_score=24.0,
              structure=bias + " TREND", event="TREND CONTINUATION",
              invalidation_level=24970.0 if bullish else 25110.0, confidence=82.0)
    three_ind = NS(status="READY", ema_state="BULLISH ALIGNED" if bullish else "BEARISH ALIGNED",
                   rsi14=62.0 if bullish else 38.0, previous_rsi14=56.0 if bullish else 44.0,
                   macd_histogram=2.0 if bullish else -2.0, previous_macd_histogram=0.4 if bullish else -0.4)
    fifteen_ind = NS(status="READY", ema_state="BULLISH ALIGNED" if bullish else "BEARISH ALIGNED")
    windows = (
        NS(status="READY", target_seconds=60, bias=bias),
        NS(status="READY", target_seconds=180, bias=bias),
        NS(status="READY", target_seconds=300, bias=bias),
    )
    ce_wall = NS(oi=120000.0, migration_points=50.0 if bullish else -50.0, strike=25100.0)
    pe_wall = NS(oi=130000.0, migration_points=50.0 if bullish else -50.0, strike=25000.0)
    options = NS(status="READY", bullish_score=bull, bearish_score=bear, range_score=18.0,
                 confidence=86.0, market_bias=bias, windows=windows, ce_wall=ce_wall, pe_wall=pe_wall)
    resistance = NS(break_pressure=82.0 if bullish else 35.0, strength=55.0,
                    lower=25050.0, upper=25060.0, midpoint=25055.0, state="WEAKENING / BREAK RISK")
    support = NS(break_pressure=35.0 if bullish else 82.0, strength=55.0,
                 lower=24980.0, upper=24990.0, midpoint=24985.0, state="WEAKENING / BREAK RISK")
    barrier = NS(status="READY", nearest_resistance=resistance, nearest_support=support,
                 trading_range=NS(confidence=42.0, state="RANGE BREAK RISK"),
                 market_speed=NS(score=78.0 if expansion else 30.0))
    rows = tuple(NS(change_3m_pct=(0.10 if bullish else -0.10)) for _ in range(9))
    heavy = NS(status="READY", recent_3m_move_pct=0.18 if bullish else -0.18,
               recent_15m_move_pct=0.24 if bullish else -0.24,
               recent_coverage_pct=55.0, covered_weight_pct=58.0, rows=rows)
    bp = NS(futures_setup=futures_setup, futures_oi_change_pct=0.8,
            futures_volume_ratio=2.0 if expansion else 1.0, status="READY",
            direction=future_direction, score=82.0)
    spot_candles = _candles(d, compression=expansion)
    future_candles = _candles(d, compression=False)
    spot = float(spot_candles.iloc[-1].close)
    chain = _option_chain(spot, bullish)
    return NS(
        created_at=NOW, market_session=NS(is_live=True), nifty_quote={"last_price": spot},
        candles_1m=spot_candles, future_candles_1m=future_candles,
        indicators=NS(three_minute=three_ind, fifteen_minute=fifteen_ind),
        price_action=NS(three_minute=pa3, fifteen_minute=pa15, relationship="ALIGNED", confidence=84.0),
        option_intelligence=options, barrier_map=barrier, heavyweights=heavy,
        big_player_activity=bp, option_chain=chain,
        vix_context=NS(movement="RISING" if expansion else "STABLE", status="READY"),
        event_risk=NS(level="NORMAL", verified=False),
        news_context=NS(risk_level="LOW", status="READY", newest_age_minutes=20.0),
        levels=NS(immediate_support=NS(midpoint=24985.0), immediate_resistance=NS(midpoint=25055.0)),
        feed_status={"quotes": _feed(), "candles": _feed(), "option_chain": _feed(),
                     "future_volume": _feed(), "vix": _feed()},
        metadata={"simple_brain": {"direction": one_brain, "final_action": "PE SELL" if one_brain == "UP" else "CE SELL"},
                  "common_decision": {"final_action": "PE SELL" if one_brain == "UP" else "CE SELL"}},
        decision=NS(final_action="WAIT"),
    )


def test_bullish_market_intelligence_is_shadow_only_and_aligned():
    snap = _snapshot(bullish=True, one_brain="UP")
    before_metadata = deepcopy(snap.metadata)
    before_decision = snap.decision.final_action
    result = calculate_market_intelligence(snap)
    assert result.direction == "BULLISH"
    assert result.bull_pressure > result.bear_pressure
    assert result.expansion_pressure >= 60
    assert result.one_brain_alignment in {"ALIGNMENT WATCH", "STRONG EVIDENCE ALIGNMENT"}
    assert result.mode == "SHADOW_ONLY_ZERO_CORE_WEIGHT"
    # Pure calculation: it does not mutate One Brain or snapshot metadata.
    assert snap.metadata == before_metadata
    assert snap.decision.final_action == before_decision


def test_bearish_market_intelligence_detects_direction():
    result = calculate_market_intelligence(_snapshot(bullish=False, one_brain="DOWN"))
    assert result.direction == "BEARISH"
    assert result.bear_pressure > result.bull_pressure
    assert result.institutional_pressure in {"SELLING", "MIXED"}


def test_one_brain_disagreement_creates_system_conflict():
    result = calculate_market_intelligence(_snapshot(bullish=True, one_brain="DOWN"))
    assert result.direction == "BULLISH"
    assert result.one_brain_alignment == "SYSTEM CONFLICT"
    assert any(row["kind"] == "SYSTEM_CONFLICT" for row in result.alerts)


def test_previous_pressure_produces_velocity_without_recalculating_history():
    previous = _snapshot(bullish=True, one_brain="UP", expansion=False)
    previous_result = calculate_market_intelligence(previous)
    previous.metadata["market_intelligence"] = previous_result.to_dict()
    current = _snapshot(bullish=True, one_brain="UP", expansion=True)
    current.created_at = NOW + timedelta(seconds=30)
    result = calculate_market_intelligence(current, previous)
    assert result.pressure_velocity is not None
    assert result.expansion_pressure >= previous_result.expansion_pressure


def test_closed_market_forces_wait_but_keeps_reference_scores():
    snap = _snapshot(bullish=True)
    snap.market_session = NS(is_live=False)
    result = calculate_market_intelligence(snap)
    assert result.system_status == "WAIT"
    assert result.bull_pressure > 0
    assert result.alerts == ()


def test_market_intelligence_is_not_imported_into_canonical_core_engines():
    from pathlib import Path
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
        text = (root / relative).read_text(encoding="utf-8")
        assert "market_intelligence" not in text.lower(), relative


def test_market_intelligence_module_has_no_broker_or_network_client():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    text = (root / "analysis/market_intelligence.py").read_text(encoding="utf-8")
    forbidden = ("DhanClient", "RailwayDhanClient", "requests.", "urlopen(", "http://", "https://")
    assert not any(token in text for token in forbidden)
