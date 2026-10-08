from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from analysis import levels as levels_mod
from analysis.barrier_map import (
    _Anchor,
    _Cluster,
    _barrier_level,
    _clusters,
    _range_context,
    _reaction_score,
)
from analysis.decision import _barrier_scores as decision_barrier_scores
from analysis.market_intelligence import _structure_event
from models import BarrierMapLevel, MarketSpeedContext


def _level(label: str, side: str, lo: float, hi: float, *, strength: float, break_pressure: float) -> BarrierMapLevel:
    return BarrierMapLevel(
        label=label,
        side=side,
        lower=lo,
        upper=hi,
        midpoint=(lo + hi) / 2.0,
        strength=strength,
        break_pressure=break_pressure,
        distance_points=0.0,
        state="TESTING",
        sources=("TEST",),
        explanation="test",
    )


def _speed() -> MarketSpeedContext:
    return MarketSpeedContext(
        score=20.0,
        state="NORMAL",
        direction="MIXED",
        move_1m_points=None,
        move_3m_points=None,
        move_5m_points=None,
        vix_change_5m_pct=None,
        vix_change_15m_pct=None,
        volume_ratio=None,
        option_shock_score=0.0,
        reasons=(),
        status="READY",
    )


def test_cluster_is_not_promoted_by_live_wick_before_completed_close():
    anchors = [
        _Anchor(100.0, 80.0, "R1", "RESISTANCE"),
        _Anchor(120.0, 80.0, "R2", "RESISTANCE"),
    ]
    clusters = _clusters(
        anchors,
        side="RESISTANCE",
        width=8.0,
        spot=105.0,              # live spot has wicked through R1
        confirmation_spot=99.0,  # completed 3m close has not accepted above
    )
    assert clusters
    assert clusters[0].midpoint == 100.0


def test_cluster_promotes_only_after_completed_close_accepts_above_zone():
    anchors = [
        _Anchor(100.0, 80.0, "R1", "RESISTANCE"),
        _Anchor(120.0, 80.0, "R2", "RESISTANCE"),
    ]
    clusters = _clusters(
        anchors,
        side="RESISTANCE",
        width=8.0,
        spot=106.0,
        confirmation_spot=106.0,
    )
    assert clusters
    assert clusters[0].midpoint == 120.0


def test_barrier_level_explicitly_reports_awaiting_completed_close():
    cluster = _Cluster(
        side="RESISTANCE",
        lower=98.0,
        upper=102.0,
        midpoint=100.0,
        structural_score=80.0,
        sources=("TEST",),
    )
    pa = SimpleNamespace(
        three_minute=SimpleNamespace(bullish_score=70.0, bearish_score=20.0, atr14=10.0),
        fifteen_minute=SimpleNamespace(bullish_score=60.0, bearish_score=25.0),
    )
    core = SimpleNamespace(bullish_score=68.0, bearish_score=22.0)
    volume = SimpleNamespace(three_minute=SimpleNamespace(status="UNAVAILABLE", relative_volume=None, price_direction="MIXED"))
    wall = SimpleNamespace(strike=None, cluster_center=None)
    options = SimpleNamespace(flow_rows=(), ce_wall=wall, pe_wall=wall)
    heavy = SimpleNamespace(status="UNAVAILABLE", weighted_move_pct=None)
    level = _barrier_level(
        cluster=cluster,
        label="R1",
        spot=103.0,
        confirmation_spot=101.0,
        candles_1m=pd.DataFrame(),
        price_action=pa,
        core=core,
        volume=volume,
        options=options,
        heavyweights=heavy,
        vix_risk_score=30.0,
    )
    assert level.state == "ABOVE ZONE / AWAITING 3M CLOSE"


def test_breakout_bias_uses_net_vulnerability_not_raw_pressure_only():
    # Raw pressure is almost tied (70 vs 65), but support is much weaker.
    resistance = _level("R1", "RESISTANCE", 120, 124, strength=90, break_pressure=70)
    support = _level("S1", "SUPPORT", 96, 100, strength=30, break_pressure=65)
    ctx = _range_context(
        spot=110.0,
        support=support,
        resistance=resistance,
        next_support=None,
        next_resistance=None,
        options=SimpleNamespace(range_score=40.0),
        core=SimpleNamespace(range_score=40.0),
        speed=_speed(),
    )
    assert ctx.breakout_bias == "DOWNSIDE RISK"
    assert "Net vulnerability UP -20, DOWN +35" in ctx.explanation


def test_market_intelligence_uses_same_barrier_bias():
    resistance = _level("R1", "RESISTANCE", 120, 124, strength=90, break_pressure=70)
    support = _level("S1", "SUPPORT", 96, 100, strength=30, break_pressure=65)
    barrier = SimpleNamespace(
        nearest_resistance=resistance,
        nearest_support=support,
        trading_range=SimpleNamespace(breakout_bias="DOWNSIDE RISK"),
    )
    pa = SimpleNamespace(
        three_minute=SimpleNamespace(event="STRUCTURE MIXED"),
        fifteen_minute=SimpleNamespace(event="STRUCTURE MIXED", structure="MIXED / TRANSITION"),
    )
    snapshot = SimpleNamespace(barrier_map=barrier, price_action=pa, candles_1m=pd.DataFrame())
    _, breakout_direction, _, _, _, reasons = _structure_event(
        snapshot, bull=50.0, bear=58.0, range_score=35.0
    )
    assert breakout_direction == "BEARISH"
    assert any("Downside net barrier vulnerability" in reason for reason in reasons)


def test_reaction_history_gives_more_weight_to_recent_touch():
    rows = []
    start = pd.Timestamp("2026-10-08 10:00:00", tz="Asia/Kolkata")
    for i in range(10):
        rows.append({
            "timestamp": start + pd.Timedelta(minutes=i),
            "open": 95.0,
            "high": 96.0,
            "low": 94.0,
            "close": 95.0,
            "is_complete": True,
        })
    # Old resistance touch with a strong 12-point rejection.
    rows[0].update(open=100.0, high=100.5, low=99.8, close=100.0)
    rows[1].update(high=96.0, low=88.0, close=90.0)
    rows[2].update(high=96.0, low=90.0, close=92.0)
    rows[3].update(high=96.0, low=92.0, close=94.0)
    # Recent touch with only a 2-point rejection.
    rows[6].update(open=100.0, high=100.5, low=99.8, close=100.0)
    rows[7].update(high=99.0, low=98.0, close=98.5)
    rows[8].update(high=99.0, low=98.5, close=98.8)
    rows[9].update(high=99.0, low=98.7, close=98.9)
    cluster = _Cluster("RESISTANCE", 100.0, 101.0, 100.5, 75.0, ("TEST",))
    score, recent_touch, weakening = _reaction_score(pd.DataFrame(rows), cluster, 20.0)
    # Simple unweighted average would be ~41.2/100; recency weighting should reduce it.
    assert score is not None and score < 41.2
    assert recent_touch is True
    assert weakening is True


def test_level_bundle_keeps_same_resistance_for_all_level_consumers_until_close(monkeypatch):
    # Isolate the side/promotion logic from the large candidate library.
    monkeypatch.setattr(
        levels_mod,
        "_merge_candidates",
        lambda candidates, width: [
            levels_mod._MergedZone(98.0, 102.0, 100.0, 80.0, ("TEST R1",)),
            levels_mod._MergedZone(118.0, 122.0, 120.0, 75.0, ("TEST R2",)),
            levels_mod._MergedZone(78.0, 82.0, 80.0, 70.0, ("TEST S1",)),
        ],
    )
    monkeypatch.setattr(levels_mod, "confirmed_swings", lambda frame: ([], []))
    monkeypatch.setattr(levels_mod, "atr_value", lambda frame: 20.0)
    monkeypatch.setattr(levels_mod, "_opening_range", lambda frame: (None, None))

    indicators = SimpleNamespace(
        three_minute=SimpleNamespace(ema20=None, ema50=None),
        fifteen_minute=SimpleNamespace(ema20=None, ema50=None),
    )
    ts = pd.Timestamp("2026-10-08 10:00:00", tz="Asia/Kolkata")
    three = pd.DataFrame([{  # completed close still below R1
        "timestamp": ts, "open": 99.0, "high": 101.0, "low": 98.0,
        "close": 99.0, "is_complete": True,
    }])
    fifteen = pd.DataFrame([{  # only needs to be available for this isolated test
        "timestamp": ts, "open": 99.0, "high": 101.0, "low": 98.0,
        "close": 99.0, "is_complete": True,
    }])
    bundle = levels_mod.calculate_levels(three, fifteen, indicators, current_price=105.0)
    assert bundle.immediate_resistance is not None
    assert bundle.immediate_resistance.midpoint == 100.0
    assert bundle.immediate_resistance.status == "ABOVE ZONE / AWAITING CLOSE"
    assert bundle.upside_room == 0.0
    # One Brain's barrier-space component consumes this same LevelBundle, so it cannot
    # prematurely see the next resistance as fresh room during the wick.
    bull, _, _ = decision_barrier_scores(bundle, None)
    assert bull == 0.0


def test_level_bundle_promotes_after_completed_3m_close(monkeypatch):
    monkeypatch.setattr(
        levels_mod,
        "_merge_candidates",
        lambda candidates, width: [
            levels_mod._MergedZone(98.0, 102.0, 100.0, 80.0, ("TEST R1",)),
            levels_mod._MergedZone(118.0, 122.0, 120.0, 75.0, ("TEST R2",)),
            levels_mod._MergedZone(78.0, 82.0, 80.0, 70.0, ("TEST S1",)),
        ],
    )
    monkeypatch.setattr(levels_mod, "confirmed_swings", lambda frame: ([], []))
    monkeypatch.setattr(levels_mod, "atr_value", lambda frame: 20.0)
    monkeypatch.setattr(levels_mod, "_opening_range", lambda frame: (None, None))
    indicators = SimpleNamespace(
        three_minute=SimpleNamespace(ema20=None, ema50=None),
        fifteen_minute=SimpleNamespace(ema20=None, ema50=None),
    )
    ts = pd.Timestamp("2026-10-08 10:03:00", tz="Asia/Kolkata")
    three = pd.DataFrame([{
        "timestamp": ts, "open": 101.0, "high": 107.0, "low": 100.0,
        "close": 106.0, "is_complete": True,
    }])
    fifteen = pd.DataFrame([{
        "timestamp": ts, "open": 101.0, "high": 107.0, "low": 100.0,
        "close": 106.0, "is_complete": True,
    }])
    bundle = levels_mod.calculate_levels(three, fifteen, indicators, current_price=106.0)
    assert bundle.immediate_support is not None
    assert bundle.immediate_support.midpoint == 100.0
    assert bundle.immediate_resistance is not None
    assert bundle.immediate_resistance.midpoint == 120.0


def test_confluence_cluster_does_not_shrink_while_completed_close_is_inside_zone():
    anchors = [
        _Anchor(100.0, 80.0, "EMA", "RESISTANCE"),
        _Anchor(106.0, 85.0, "OI WALL", "RESISTANCE"),
        _Anchor(130.0, 75.0, "NEXT", "RESISTANCE"),
    ]
    clusters = _clusters(
        anchors,
        side="RESISTANCE",
        width=8.0,
        spot=108.0,
        confirmation_spot=105.0,  # inside original 96-110 confluence zone
    )
    assert clusters
    assert clusters[0].lower == 96.0
    assert clusters[0].upper == 110.0
    assert set(clusters[0].sources) == {"EMA", "OI WALL"}


def test_level_role_stays_resistance_while_completed_close_remains_inside_zone():
    raw = levels_mod._MergedZone(98.0, 102.0, 100.0, 80.0, ("TEST",))
    ts = pd.Timestamp("2026-10-08 10:00:00", tz="Asia/Kolkata")
    completed = pd.DataFrame([
        {"timestamp": ts, "close": 97.0},
        {"timestamp": ts + pd.Timedelta(minutes=3), "close": 101.5},
    ])
    assert levels_mod._confirmed_zone_side(raw, completed) == "RESISTANCE"
