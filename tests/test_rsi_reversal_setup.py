from types import SimpleNamespace
from datetime import datetime, time
from zoneinfo import ZoneInfo
import pandas as pd

from analysis.rsi_reversal_setup import evaluate_rsi_reversal_setup


def ns(**values):
    return SimpleNamespace(**values)


def level(side: str, strength: float = 78, pressure: float = 30, distance: float = 12):
    return ns(
        side=side,
        lower=24420 if side == "RESISTANCE" else 24180,
        upper=24430 if side == "RESISTANCE" else 24190,
        strength=strength,
        break_pressure=pressure,
        distance_points=distance,
    )


def plan(available: bool = True):
    return ns(
        available=available,
        short_legs=(
            ns(strike=24450, side="CE", role="SHORT", delta=0.25, status="READY"),
        ),
        hedge_legs=(
            ns(strike=24550, side="CE", role="HEDGE", delta=0.15, status="READY"),
        ),
        estimated_credit_points=50.0,
        max_risk_points=50.0,
        width_points=100.0,
        name="CE SELL",
    )


def snapshot(rsi: float, direction: str, *, score: float = 80, confirmations: int = 2):
    ce = plan()
    pe = plan()
    condor = plan()
    now = datetime(2026, 8, 27, 12, 58, tzinfo=ZoneInfo("Asia/Kolkata"))
    closes = [100.0 + i for i in range(80)]
    if rsi < 40:
        closes = [200.0 - i for i in range(80)]
        closes.append(closes[-1] + 6)
    elif rsi < 70:
        closes.append(closes[-1] - 6)
    stamps = pd.date_range(end=now.replace(minute=54), periods=len(closes), freq="3min")
    candle_frame = pd.DataFrame(
        {"timestamp": stamps, "close": closes, "is_complete": True}
    )
    return ns(
        created_at=now,
        nifty_quote={"last_price": 24425.0},
        candles_3m=candle_frame,
        candles_15m=candle_frame.copy(),
        indicators=ns(
            three_minute=ns(rsi14=rsi, as_of=stamps[-1]),
            fifteen_minute=ns(rsi14=rsi, as_of=stamps[-1]),
        ),
        barrier_map=ns(
            nearest_resistance=level("RESISTANCE"),
            nearest_support=level("SUPPORT"),
            trading_range=ns(state="STRONG RANGE"),
        ),
        big_player_activity=ns(
            direction=direction,
            score=score,
            confirmation_count=confirmations,
            confirmation_total=3,
            status="READY",
        ),
        trade_plan=ns(ce_sell=ce, pe_sell=pe, iron_condor=condor, expiry="2026-09-01"),
        market_session=ns(is_live=True),
        risk_profile=ns(
            lot_size=65,
            max_lots_cap=1,
            risk_budget_rupees=5000,
            entry_start=time(9, 30),
            entry_end=time(14, 30),
            forced_exit=time(15),
        ),
        feed_status={
            k: ns(ok=True, use_state="LIVE")
            for k in ("quotes", "candles", "option_chain", "future_volume")
        },
        discipline_state=ns(day_locked=False, trades_taken=0),
        option_intelligence=ns(status="READY"),
    )


def test_top_turn_with_selling_selects_ce_sell():
    current = snapshot(68, "SELLING")
    previous = snapshot(74, "SELLING")
    result = evaluate_rsi_reversal_setup(current, previous)
    assert result.action == "CE SELL"
    assert result.status == "ENTRY READY"
    assert result.suggested_lots == 1


def test_bottom_turn_with_buying_selects_pe_sell():
    current = snapshot(33, "BUYING")
    previous = snapshot(27, "BUYING")
    result = evaluate_rsi_reversal_setup(current, previous)
    assert result.action == "PE SELL"
    assert result.status == "ENTRY READY"


def test_medium_confirmation_and_two_barriers_selects_condor():
    current = snapshot(68, "MIXED", score=35, confirmations=1)
    previous = snapshot(73, "MIXED", score=35, confirmations=1)
    result = evaluate_rsi_reversal_setup(current, previous)
    assert result.action == "IRON CONDOR"
    assert result.confidence < 70


def test_rsi_extreme_without_turn_waits():
    current = snapshot(75, "SELLING")
    previous = snapshot(72, "SELLING")
    result = evaluate_rsi_reversal_setup(current, previous)
    assert result.action == "WAIT"


def test_opposite_big_player_flow_blocks_directional_sell():
    current = snapshot(68, "BUYING")
    previous = snapshot(74, "BUYING")
    result = evaluate_rsi_reversal_setup(current, previous)
    assert result.action == "WAIT"
