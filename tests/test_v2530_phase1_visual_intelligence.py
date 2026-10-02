from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import inspect

import pandas as pd

from ui.live_barrier_chart import build_live_barrier_chart_payload, render_live_barrier_chart


def _level(label, side, lower, upper, strength, pressure, state="TESTING"):
    return SimpleNamespace(
        label=label,
        side=side,
        lower=lower,
        upper=upper,
        midpoint=(lower + upper) / 2,
        strength=strength,
        break_pressure=pressure,
        state=state,
    )


def _wall(side, strike, oi, previous, migration, cluster, cluster_oi):
    return SimpleNamespace(
        side=side,
        strike=strike,
        oi=oi,
        previous_strike=previous,
        migration_points=migration,
        cluster_center=cluster,
        cluster_oi=cluster_oi,
        status="READY",
    )


def _window(seconds, ce_oi, pe_oi, ce_prem, pe_prem, ce_vol, pe_vol):
    return SimpleNamespace(
        label=f"{seconds}s",
        target_seconds=seconds,
        actual_age_seconds=float(seconds),
        ce_oi_delta=ce_oi,
        pe_oi_delta=pe_oi,
        ce_premium_delta=ce_prem,
        pe_premium_delta=pe_prem,
        ce_volume_delta=ce_vol,
        pe_volume_delta=pe_vol,
        bias="MIXED",
        status="READY",
    )


def test_phase1_payload_adds_visual_intelligence_without_new_market_inputs():
    prev = pd.date_range("2026-09-28 12:00", periods=80, freq="min", tz="Asia/Kolkata")
    current = pd.date_range("2026-09-29 09:15", periods=180, freq="min", tz="Asia/Kolkata")
    times = prev.append(current)
    closes = pd.Series(range(22000, 22000 + len(times)), dtype=float)
    frame = pd.DataFrame(
        {
            "timestamp": times,
            "open": closes - 1,
            "high": closes + 2,
            "low": closes - 2,
            "close": closes,
        }
    )
    barriers = SimpleNamespace(
        current_price=22684.0,
        nearest_resistance=_level("R1", "RESISTANCE", 22690, 22710, 82, 41),
        next_resistance=_level("R2", "RESISTANCE", 22745, 22760, 88, 24, "AHEAD"),
        nearest_support=_level("S1", "SUPPORT", 22590, 22610, 79, 29, "HOLDING / STRONG"),
        next_support=_level("S2", "SUPPORT", 22540, 22555, 86, 20, "AHEAD"),
    )
    options = SimpleNamespace(
        ce_wall=_wall("CE", 22700, 30_000_000, 22650, 50, 22700, 55_000_000),
        pe_wall=_wall("PE", 22600, 20_000_000, 22650, -50, 22600, 45_000_000),
        market_bias="MIXED",
        confidence=82.0,
        persistence="PERSISTENT",
        windows=(
            _window(60, 100, 50, -8, -4, 200, 100),
            _window(180, 200, 100, -14, -7, 400, 200),
            _window(300, 300, 150, -20, -10, 600, 300),
        ),
    )
    snapshot = SimpleNamespace(
        snapshot_id="SNAP-phase1",
        created_at=datetime(2026, 9, 29, 12, 15, tzinfo=ZoneInfo("Asia/Kolkata")),
        market_session=SimpleNamespace(code="LIVE", label="MARKET OPEN", is_live=True),
        barrier_map=barriers,
        nifty_quote={"last_price": 22684.0},
        candles_1m=frame,
        candles_3m=frame.iloc[::3].copy(),
        candles_15m=frame.iloc[::15].copy(),
        option_intelligence=options,
        big_player_activity=SimpleNamespace(
            direction="SELLING",
            state="ACTIVE",
            score=72.0,
            confirmation_count=2,
            confirmation_total=2,
            persistence="CONFIRMED",
            reversal_risk="LOW",
            futures_volume_ratio=2.1,
            futures_oi_change_pct=0.4,
            futures_setup="SHORT BUILD-UP",
            option_confirmation="BEARISH",
            level_reaction="R1 REJECTION",
            status="READY",
            activity_type="DIRECTIONAL ACTIVITY",
            move_state="ACTIVE",
            price_shock_state="NONE",
            next_confirmation="R1 rejection hold",
        ),
        decision=SimpleNamespace(final_action="CE SELL", market_direction="DOWN", reasons=("R1 rejection",)),
        metadata={
            "global_oi_walls": {"CE": {"oi": 30_000_000}, "PE": {"oi": 25_000_000}},
            "simple_brain": {
                "final_action": "CE SELL",
                "direction": "DOWN",
                "entry_readiness": 78.0,
                "trigger": "R1 rejection confirm",
                "reasons": ("R1 + CE wall + Big Player selling",),
                "entry_state": "READY",
            },
        },
    )

    payload = build_live_barrier_chart_payload(snapshot)

    assert payload["session"]["isLive"] is True
    assert payload["ema"]["1m"]["20"]
    assert payload["ema"]["1m"]["50"]
    assert payload["ema"]["3m"]["20"]
    ce = next(item for item in payload["moneyWalls"] if item["side"] == "CE")
    assert ce["behavior"] == "WRITING"
    assert ce["migrationPoints"] == 50.0
    assert 60.0 <= ce["moneyScore"] <= 65.0
    assert ce["moneyTag"] == "HIGH"
    r1 = next(item for item in payload["barriers"] if item["label"] == "R1")
    assert r1["moneyAligned"] is True
    assert r1["moneySide"] == "CE"
    assert r1["moneyDistance"] == 0.0
    assert payload["bigPlayer"]["setup"] == "SHORT BUILD-UP"
    assert payload["aiBrain"]["action"] == "CE SELL"


def test_phase1_chart_renderer_stays_display_only():
    source = inspect.getsource(render_live_barrier_chart)
    lowered = source.lower()
    assert "requests." not in lowered
    assert "dhan_client" not in lowered
    assert "option_state_store" not in lowered
    assert "journal" in source  # docstring explicitly states there is no journal/history read
    assert "components.html" in source
