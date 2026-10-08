from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd

from analysis.smart_entry_advisor import build_smart_entry_advisor
from analysis.strike_entry import StrikeEntry


def _snapshot(*, fast=False):
    now = datetime.now(timezone.utc)
    chain = pd.DataFrame([
        {
            "side": "CE", "strike": 22650.0, "last_price": 136.5,
            "top_bid_price": 136.4, "top_ask_price": 136.6,
            "greeks_quality": "UNAVAILABLE",
        },
        {
            "side": "CE", "strike": 22750.0, "last_price": 124.8,
            "top_bid_price": 124.7, "top_ask_price": 124.9,
            "greeks_quality": "UNAVAILABLE",
        },
    ])
    intensity = 90.0 if fast else 35.0
    iw_state = "OPEN" if fast else "FORMING"
    metadata = {
        "market_intelligence": {
            "expansion_pressure": intensity,
            "pressure_integrity": {
                "quality_score": 90.0 if fast else 55.0,
                "barrier_attack_score": 90.0 if fast else 40.0,
                "move_attack_state": "ATTACK" if fast else "BUILDING",
            },
            "institutional_window": {
                "state": iw_state,
                "opportunity_score": 84.0 if fast else 62.0,
            },
            "liquidity": {
                "money_concentration": {
                    "bias": "DOWNSIDE",
                    "downside_score": 84.0,
                    "upside_score": 52.0,
                }
            },
        }
    }
    barrier = SimpleNamespace(
        nearest_support=None, next_support=None,
        nearest_resistance=None, next_resistance=None,
    )
    return SimpleNamespace(
        option_chain=chain,
        nifty_quote={"last_price": 22621.4},
        metadata=metadata,
        barrier_map=barrier,
        risk_profile=SimpleNamespace(lot_size=65, risk_budget_rupees=4500.0),
        market_session=SimpleNamespace(is_live=True),
        created_at=now,
        expiry=(now + timedelta(days=3)).date().isoformat(),
        feed_status={"option_chain": SimpleNamespace(use_state="LIVE")},
    )


def test_smart_entry_waits_when_move_not_urgent():
    planner = StrikeEntry(
        "WAIT FOR RETEST", "Retest pending", zone=(22640.0, 22660.0),
        invalidation=None, premium_range=(136.0, 142.0), net_credit=11.5,
        worst_case_rupees=2500.0,
    )
    advisor = build_smart_entry_advisor(
        _snapshot(fast=False), side="CE", position="SELL", strike=22650.0,
        lots=1, planner=planner, hedge_strike=22750.0,
    )
    assert advisor.status in {"WAIT — BETTER PRICE", "ARMED — WAIT FOR TRIGGER"}
    assert advisor.price_quality_state in {"GOOD", "FAIR"}
    assert advisor.move_urgency_state in {"LOW", "RISING"}


def test_smart_entry_fast_move_can_use_acceptable_price_without_wm_candle_gate():
    planner = StrikeEntry(
        "WAIT FOR RETEST", "Retest pending", zone=(22640.0, 22660.0),
        invalidation=None, premium_range=(136.0, 142.0), net_credit=11.5,
        worst_case_rupees=2500.0,
    )
    advisor = build_smart_entry_advisor(
        _snapshot(fast=True), side="CE", position="SELL", strike=22650.0,
        lots=1, planner=planner, hedge_strike=22750.0,
    )
    assert advisor.status == "FAST MOVE — CURRENT PRICE ACCEPTABLE"
    assert advisor.move_urgency_state == "HIGH"
    assert advisor.institutional_state == "OPEN"
    assert advisor.liquidity_magnet_bias == "DOWNSIDE"
