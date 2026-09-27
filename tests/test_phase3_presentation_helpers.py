from __future__ import annotations

from types import SimpleNamespace

from ui.presentation_helpers import (
    public_action_label,
    ready_banner_label,
    smart_focus_tags,
    strategy_status_label,
)


def _leg(strike):
    return SimpleNamespace(strike=strike)


def test_public_wording_is_neutral_and_internal_is_unchanged():
    assert public_action_label("CE SELL", public_mode=False) == "CE SELL"
    assert public_action_label("CE SELL", public_mode=True) == "CE SELL SETUP"
    assert ready_banner_label(public_mode=False) == "TAKE NOW"
    assert ready_banner_label(public_mode=True) == "SETUP READY"
    assert strategy_status_label("BEST • ENTRY READY", public_mode=True) == "BEST • CONDITIONS MET"


def test_smart_focus_uses_existing_snapshot_only():
    plan = SimpleNamespace(
        available=True,
        long_legs=(),
        short_legs=(_leg(25250),),
        hedge_legs=(_leg(25450),),
    )
    bundle = SimpleNamespace(
        ce_buy=None,
        pe_buy=None,
        ce_sell=plan,
        pe_sell=None,
        iron_condor=None,
    )
    snapshot = SimpleNamespace(
        option_intelligence=SimpleNamespace(
            ce_wall=SimpleNamespace(strike=25300, cluster_center=25350),
            pe_wall=SimpleNamespace(strike=25100, cluster_center=25050),
        ),
        metadata={"common_decision": {"best_strategy": "CE SELL"}},
        trade_plan=bundle,
    )
    tags = smart_focus_tags(snapshot, atm=25200)
    assert "ATM" in tags[25200.0]
    assert "FOCUS SHORT" in tags[25250.0]
    assert "HEDGE" in tags[25450.0]
    assert "CE WALL" in tags[25300.0]
    assert "PE CLUSTER" in tags[25050.0]
