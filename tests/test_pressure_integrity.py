from types import SimpleNamespace as NS
import pandas as pd

from analysis.market_intelligence import ExpertEvidence
from analysis.pressure_integrity import calculate_pressure_integrity


def candles(price=100.0):
    rows=[]
    for i in range(12):
        c=price + i*0.05
        rows.append({"timestamp":i,"open":c-0.5,"high":c+2.5,"low":c-2.5,"close":c,"is_complete":True})
    return pd.DataFrame(rows)


def expert(name, bull, bear, available=True):
    return ExpertEvidence(name, available, bull, bear, 10.0, 0.9, 0.95, ())


def experts(direction="BULLISH", strong=True):
    if direction == "BULLISH":
        b, d = (80, 12) if strong else (58, 35)
    else:
        b, d = (12, 80) if strong else (35, 58)
    names=("Structure","Futures","Options Flow","Barriers / Walls","Heavyweight Breadth","Momentum Acceleration")
    return {n: expert(n,b,d) for n in names}


def snap(spot, *, move_1m=0.0, resistance=(5,80,45), support=(5,80,45), expansion=None):
    rdist, rbreak, rstrength=resistance
    sdist, sbreak, sstrength=support
    metadata={}
    if expansion is not None:
        metadata["market_intelligence"]={"expansion_pressure": expansion}
    return NS(
        nifty_quote={"last_price":spot},
        candles_1m=candles(spot),
        patterns=None,
        metadata=metadata,
        barrier_map=NS(
            market_speed=NS(move_1m_points=move_1m),
            nearest_resistance=NS(distance_points=rdist,break_pressure=rbreak,strength=rstrength),
            nearest_support=NS(distance_points=sdist,break_pressure=sbreak,strength=sstrength),
        ),
    )


def test_patterns_are_supportive_not_required_for_fast_real_pressure():
    previous=snap(100.0, move_1m=2.0, expansion=50)
    current=snap(110.0, move_1m=10.0)
    result=calculate_pressure_integrity(
        current, previous, direction="BULLISH", expansion_pressure=74,
        pressure_velocity=19, persistence=0, coverage=82, conflict="LOW",
        experts=experts("BULLISH"), breakout_quality=60, reversal_quality=20,
        structure_event="DEVELOPING", previous_integrity=None,
    )
    assert result.move_risk_state in {"BUILDING","HIGH"}
    assert result.quality_state in {"VERIFIED","REALIZED"}
    assert result.realized_move is True
    assert result.supportive_pattern_score == 50.0  # no pattern, no penalty


def test_first_fast_warning_is_not_immediately_labelled_fake():
    previous=snap(100.0, move_1m=0.0, expansion=40)
    current=snap(100.2, move_1m=1.0)
    result=calculate_pressure_integrity(
        current, previous, direction="BULLISH", expansion_pressure=64,
        pressure_velocity=20, persistence=0, coverage=75, conflict="LOW",
        experts=experts("BULLISH", strong=False), breakout_quality=45, reversal_quality=30,
        structure_event="DEVELOPING", previous_integrity=None,
    )
    assert result.move_risk_state in {"BUILDING","HIGH"}
    assert result.quality_state != "BUILD-UP FAILED"
    assert result.fake_pressure_score < 80


def test_established_pressure_against_price_and_strong_barrier_becomes_absorption_risk():
    previous=snap(100.0, move_1m=1.0, expansion=78)
    current=snap(96.0, move_1m=-7.0, resistance=(4,30,88))
    prior={"direction":"BULLISH","quality_state":"BUILDING","anchor_spot":100.0,"best_progress_points":0.0,"realized_move":False}
    result=calculate_pressure_integrity(
        current, previous, direction="BULLISH", expansion_pressure=76,
        pressure_velocity=4, persistence=2, coverage=78, conflict="MEDIUM",
        experts=experts("BULLISH", strong=False), breakout_quality=35, reversal_quality=55,
        structure_event="DEVELOPING", previous_integrity=prior,
    )
    assert result.quality_state == "ABSORPTION RISK"
    assert result.fake_pressure_score >= result.real_pressure_score


def test_realized_wave_then_pressure_collapse_is_exhausting_not_fake_failure():
    previous=snap(111.0, move_1m=3.0, expansion=84)
    current=snap(112.0, move_1m=1.0)
    prior={"direction":"BULLISH","quality_state":"REALIZED","anchor_spot":100.0,"best_progress_points":11.0,"realized_move":True}
    result=calculate_pressure_integrity(
        current, previous, direction="BULLISH", expansion_pressure=48,
        pressure_velocity=-25, persistence=2, coverage=80, conflict="LOW",
        experts=experts("BULLISH", strong=False), breakout_quality=45, reversal_quality=55,
        structure_event="DEVELOPING", previous_integrity=prior,
    )
    assert result.quality_state == "EXHAUSTING"
    assert result.realized_move is True
    assert result.quality_state != "BUILD-UP FAILED"


def test_opposite_pressure_uses_flip_watch_then_can_confirm():
    previous=snap(105.0, move_1m=2.0, expansion=75)
    prior={"direction":"BULLISH","quality_state":"VERIFIED","anchor_spot":100.0,"best_progress_points":5.0,"realized_move":False}
    weak=snap(103.5, move_1m=-1.0)
    watch=calculate_pressure_integrity(
        weak, previous, direction="BEARISH", expansion_pressure=62,
        pressure_velocity=12, persistence=0, coverage=72, conflict="MEDIUM",
        experts=experts("BEARISH", strong=False), breakout_quality=40, reversal_quality=55,
        structure_event="REVERSAL DEVELOPING", previous_integrity=prior,
    )
    assert watch.flip_state in {"FLIP WATCH","FLIP CONFIRMED"}

    strong=snap(94.0, move_1m=-10.0)
    confirmed=calculate_pressure_integrity(
        strong, previous, direction="BEARISH", expansion_pressure=78,
        pressure_velocity=22, persistence=1, coverage=88, conflict="LOW",
        experts=experts("BEARISH"), breakout_quality=70, reversal_quality=70,
        structure_event="BREAKOUT", previous_integrity=prior,
    )
    assert confirmed.flip_state == "FLIP CONFIRMED"
