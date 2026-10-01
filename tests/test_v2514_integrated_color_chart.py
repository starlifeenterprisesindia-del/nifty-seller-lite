from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from ui.live_barrier_chart import build_live_barrier_chart_payload


def _level(label, side, lower, upper, strength, pressure):
    return SimpleNamespace(
        label=label, side=side, lower=lower, upper=upper,
        midpoint=(lower + upper) / 2, strength=strength,
        break_pressure=pressure, state="TESTING",
    )


def _wall(side, strike, oi, cluster, cluster_oi):
    return SimpleNamespace(
        side=side, strike=strike, oi=oi, previous_strike=strike,
        migration_points=0, cluster_center=cluster, cluster_oi=cluster_oi,
        status="READY",
    )


def test_integrated_chart_payload_reuses_existing_snapshot_evidence_only():
    times = pd.date_range("2026-09-29 09:15", periods=40, freq="min", tz="Asia/Kolkata")
    frame = pd.DataFrame({
        "timestamp": times, "open": range(100, 140), "high": range(101, 141),
        "low": range(99, 139), "close": range(100, 140),
    })
    barriers = SimpleNamespace(
        current_price=22684.0,
        nearest_resistance=_level("R1", "RESISTANCE", 22700, 22710, 80, 43),
        next_resistance=_level("R2", "RESISTANCE", 22745, 22755, 82, 25),
        nearest_support=_level("S1", "SUPPORT", 22655, 22666, 75, 32),
        next_support=_level("S2", "SUPPORT", 22620, 22633, 88, 22),
    )
    options = SimpleNamespace(
        ce_wall=_wall("CE", 22700, 27_000_000, 22750, 52_000_000),
        pe_wall=_wall("PE", 22600, 21_000_000, 22600, 54_000_000),
        market_bias="BULLISH", confidence=84.0, persistence="MIXED",
    )
    big = SimpleNamespace(
        direction="BUYING", state="ACTIVE", score=58.1,
        confirmation_count=1, confirmation_total=2, persistence="WARMING UP",
        reversal_risk="MEDIUM", futures_volume_ratio=1.77,
        futures_oi_change_pct=-0.17, futures_setup="SHORT COVERING",
        option_confirmation="BULLISH", level_reaction="R1 TESTING", status="READY",
    )
    snapshot = SimpleNamespace(
        snapshot_id="SNAP-test",
        created_at=datetime(2026, 9, 29, 15, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        barrier_map=barriers, nifty_quote={"last_price": 22684.0},
        candles_1m=frame, candles_3m=frame.iloc[::3].copy(), candles_15m=frame.iloc[::15].copy(),
        option_intelligence=options, big_player_activity=big,
        decision=SimpleNamespace(final_action="WAIT", market_direction="MIXED", reasons=("No clear edge",)),
        metadata={
            "global_oi_walls": {"CE": {"oi": 27_000_000}, "PE": {"oi": 30_000_000}},
            "simple_brain": {
                "final_action": "WAIT", "direction": "MIXED", "entry_readiness": 44.5,
                "trigger": "22,710 ke upar 3m close", "reasons": ("R1 strong; confirmation pending",),
                "entry_state": "NO CLEAR EDGE",
            },
        },
    )
    payload = build_live_barrier_chart_payload(snapshot)
    assert payload["spot"] == 22684.0
    assert [item["label"] for item in payload["barriers"]] == ["R1", "R2", "S1", "S2"]
    assert payload["moneyWalls"][0]["side"] == "CE"
    assert payload["moneyWalls"][0]["tag"] == "VERY HIGH"
    assert payload["moneyWalls"][1]["relativePct"] == 70.0
    assert payload["bigPlayer"]["direction"] == "BUYING"
    assert payload["bigPlayer"]["score"] == 58.1
    assert payload["aiBrain"]["action"] == "WAIT"
    assert payload["aiBrain"]["trigger"] == "22,710 ke upar 3m close"
    assert payload["optionFlow"]["confidence"] == 84.0
    assert payload["candles"]["1m"] and payload["candles"]["3m"] and payload["candles"]["15m"]
