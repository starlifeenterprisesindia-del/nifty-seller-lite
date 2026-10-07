from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
import pandas as pd

from analysis.liquidity_intelligence import _sweep_state


def snapshot(closes, now):
    rows=[]
    for i,c in enumerate(closes):
        rows.append({"timestamp":i,"open":c-0.5,"high":c+1.0,"low":c-1.0,"close":c,"is_complete":True})
    return NS(candles_1m=pd.DataFrame(rows), created_at=now)


def prior_liq(now, state):
    zone={"side":"DOWNSIDE","lower":94.0,"upper":96.0,"midpoint":95.0,"attraction_score":75.0,"distance_points":2.0,"strength":"HIGH","sources":["support"]}
    return {"liquidity":{"sweep_state":state,"sweep_anchor_zone":zone,"sweep_anchor_at":(now-timedelta(minutes=1)).isoformat(),"primary_zone":zone}}


def test_first_reclaim_is_reversal_watch_not_favored():
    now=datetime(2026,10,7,14,45,tzinfo=timezone.utc)
    s=snapshot([93.0, 93.5, 95.0, 98.0], now)
    state,outcome,*rest=_sweep_state(s, prior_liq(now,"DOWNSIDE LIQUIDITY BREACHED"), direction="BULLISH", early_direction="BULLISH", bull_pressure=65, bear_pressure=35, expansion_pressure=65, breakout_quality=45, reversal_quality=70)
    assert "RECLAIM" in state or "REJECTED" in state
    assert outcome == "REVERSAL WATCH"


def test_reclaim_follow_through_can_upgrade_to_reversal_favored():
    now=datetime(2026,10,7,14,48,tzinfo=timezone.utc)
    s=snapshot([93.0, 97.0, 97.5, 98.0], now)
    state,outcome,*rest=_sweep_state(s, prior_liq(now,"DOWNSIDE BREACH RECLAIMED"), direction="BULLISH", early_direction="BULLISH", bull_pressure=68, bear_pressure=30, expansion_pressure=58, breakout_quality=42, reversal_quality=68)
    assert "FOLLOW-THROUGH" in state
    assert outcome == "REVERSAL FAVORED"
