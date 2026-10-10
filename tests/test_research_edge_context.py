from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from analysis.research_edge_context import (
    build_iv_percentile_context,
    build_mean_reversion_context,
    build_vix_expected_move_context,
    dte_matched_iv_history,
)

IST = ZoneInfo("Asia/Kolkata")


def _barrier_level(distance: float, lower: float, upper: float):
    return SimpleNamespace(distance_points=distance, lower=lower, upper=upper)


def test_vix_expected_move_uses_existing_barrier_move_and_session_anchor():
    created = datetime(2026, 10, 12, 11, 0, tzinfo=IST)
    one_min = pd.DataFrame(
        [
            {"timestamp": created.replace(hour=9, minute=16), "open": 25000.0, "close": 25010.0, "is_complete": True},
            {"timestamp": created.replace(hour=9, minute=17), "open": 25010.0, "close": 25020.0, "is_complete": True},
        ]
    )
    barrier = SimpleNamespace(
        current_price=25100.0,
        vix_expected_daily_move_points=200.0,
        vix_expected_remaining_move_points=120.0,
        nearest_resistance=_barrier_level(90.0, 25190.0, 25200.0),
        nearest_support=_barrier_level(70.0, 25020.0, 25030.0),
    )
    snapshot = SimpleNamespace(
        created_at=created,
        candles_1m=one_min,
        barrier_map=barrier,
        nifty_quote={"last_price": 25100.0, "ohlc": {"close": 24980.0}},
    )

    result = build_vix_expected_move_context(snapshot)

    assert result["status"] == "READY"
    assert result["anchor"] == 25000.0
    assert result["anchor_source"] == "SESSION OPEN"
    assert result["lower_1sigma"] == 24800.0
    assert result["upper_1sigma"] == 25200.0
    assert result["utilization_pct"] == 50.0
    assert result["state"] == "INSIDE EXPECTED ENVELOPE"
    # 90 points is inside both the remaining ±120 point move and the daily envelope.
    assert result["upside_barrier_fit"]["state"] == "NEAR EXPECTED LIMIT"
    assert result["decision_weight"] == 0


def _option_chain(atm_iv: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"strike": 25000.0, "side": "CE", "implied_volatility": atm_iv},
            {"strike": 25000.0, "side": "PE", "implied_volatility": atm_iv},
            {"strike": 25100.0, "side": "CE", "implied_volatility": atm_iv + 1.0},
            {"strike": 24900.0, "side": "PE", "implied_volatility": atm_iv + 1.0},
        ]
    )


def _same_bucket_history(current: datetime, count: int, *, start_iv: float = 10.0):
    rows = []
    # Current setup is 1 calendar day to expiry. Create prior observations with the
    # same 1D relationship, regardless of weekday, so bucket matching is deterministic.
    for i in range(count):
        day = (current.date() - timedelta(days=(i + 1) * 2))
        expiry = day + timedelta(days=1)
        rows.append(
            {
                "date": day.isoformat(),
                "expiry": expiry.isoformat(),
                "last": start_iv + i * 0.5,
                "updated_at": f"{day.isoformat()}T15:20:00+05:30",
            }
        )
    return rows


def test_dte_matched_iv_percentile_excludes_other_buckets_and_current_day():
    current = datetime(2026, 10, 14, 13, 0, tzinfo=IST)
    snapshot = SimpleNamespace(
        created_at=current,
        expiry="2026-10-15",
        nifty_quote={"last_price": 25010.0},
        option_chain=_option_chain(40.0),
    )
    history = _same_bucket_history(current, 20, start_iv=12.0)
    # Other-DTE rows must not contaminate the percentile.
    history.append({"date": "2026-10-13", "expiry": "2026-10-20", "last": 80.0})
    # Current-day row must also be excluded.
    history.append({"date": "2026-10-14", "expiry": "2026-10-15", "last": 99.0})

    values, rows, bucket, days = dte_matched_iv_history(snapshot, history)
    result = build_iv_percentile_context(snapshot, history, minimum_bucket_sessions=20)

    assert bucket == "0-1D"
    assert days == 1
    assert len(values) == len(rows) == 20
    assert result["status"] == "READY"
    assert result["history_sessions"] == 20
    assert result["state"] == "RICH IV"
    assert result["iv_percentile"] == 100.0
    assert result["decision_weight"] == 0


def test_iv_percentile_stays_no_vote_until_same_bucket_history_is_large_enough():
    current = datetime(2026, 10, 14, 13, 0, tzinfo=IST)
    snapshot = SimpleNamespace(
        created_at=current,
        expiry="2026-10-15",
        nifty_quote={"last_price": 25000.0},
        option_chain=_option_chain(20.0),
    )
    result = build_iv_percentile_context(
        snapshot,
        _same_bucket_history(current, 8),
        minimum_bucket_sessions=20,
    )
    assert result["status"] == "WARMING UP"
    assert result["state"] == "NO VOTE"
    assert result["history_sessions"] == 8
    assert result["seller_context"] == "NO VOTE"


def _mean_reversion_snapshot() -> SimpleNamespace:
    created = datetime(2026, 10, 12, 12, 0, tzinfo=IST)
    # Final two selloffs force RSI(2) to an extreme while the external 15m EMA trend
    # remains bullish. This is intentionally a pullback inside a dominant trend.
    closes = [100, 101, 102, 103, 104, 105, 100, 95]
    frame = pd.DataFrame(
        [
            {
                "timestamp": created - timedelta(minutes=15 * (len(closes) - i)),
                "close": float(value),
                "is_complete": True,
            }
            for i, value in enumerate(closes)
        ]
    )
    ind15 = SimpleNamespace(status="READY", ema20=105.0, ema50=100.0)
    pa15 = SimpleNamespace(atr14=8.0)
    pa3 = SimpleNamespace(bullish_score=65.0, bearish_score=30.0)
    support = _barrier_level(5.0, 94.0, 96.0)
    pattern = SimpleNamespace(direction="NEUTRAL", confidence=0.0)
    patterns = SimpleNamespace(
        candle_3m=pattern,
        candle_5m=pattern,
        candle_15m=pattern,
        wm_3m=pattern,
    )
    return SimpleNamespace(
        created_at=created,
        candles_15m=frame,
        indicators=SimpleNamespace(fifteen_minute=ind15),
        price_action=SimpleNamespace(fifteen_minute=pa15, three_minute=pa3),
        barrier_map=SimpleNamespace(nearest_support=support, nearest_resistance=None),
        patterns=patterns,
    )


def test_mean_reversion_marks_confirmed_pullback_without_opposing_verified_pressure():
    snapshot = _mean_reversion_snapshot()
    mi = {
        "pressure_integrity": {
            "direction": "MIXED",
            "quality_state": "UNVERIFIED",
            "move_attack_state": "NORMAL",
            "barrier_state": "HOLD",
        }
    }
    result = build_mean_reversion_context(snapshot, mi)

    assert result["status"] == "READY"
    assert result["trend"] == "BULLISH"
    assert result["rsi2_15m"] <= 10.0
    assert result["near_trend_barrier"] is True
    assert result["three_minute_confirmation"] is True
    assert result["state"] == "PULLBACK COMPLETION CONFIRMED"
    assert result["verdict"] == "SUPPORTS TREND CONTINUATION"
    assert result["decision_weight"] == 0


def test_mean_reversion_prefers_genuine_reversal_risk_when_opposing_pressure_is_verified_and_attacking():
    snapshot = _mean_reversion_snapshot()
    mi = {
        "pressure_integrity": {
            "direction": "BEARISH",
            "quality_state": "VERIFIED",
            "move_attack_state": "ATTACK",
            "barrier_state": "BREAK PRESSURE",
        }
    }
    result = build_mean_reversion_context(snapshot, mi)

    assert result["state"] == "GENUINE REVERSAL RISK"
    assert result["verdict"] == "DO NOT ASSUME MEAN REVERSION"
    assert result["context_strength"] <= 35.0


def test_missing_inputs_are_no_vote_not_fake_neutral():
    snapshot = SimpleNamespace(
        created_at=datetime(2026, 10, 12, 10, 0, tzinfo=IST),
        candles_1m=pd.DataFrame(),
        candles_15m=pd.DataFrame(),
        barrier_map=SimpleNamespace(
            current_price=None,
            vix_expected_daily_move_points=None,
            vix_expected_remaining_move_points=None,
        ),
        nifty_quote={},
        indicators=None,
        price_action=None,
        patterns=None,
        expiry=None,
        option_chain=pd.DataFrame(),
    )
    assert build_vix_expected_move_context(snapshot)["state"] == "NO VOTE"
    assert build_mean_reversion_context(snapshot)["state"] == "NO VOTE"
    assert build_iv_percentile_context(snapshot, [])["state"] == "NO VOTE"
