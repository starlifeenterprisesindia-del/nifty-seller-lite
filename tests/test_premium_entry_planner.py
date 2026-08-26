from analysis.premium_entry_planner import build_premium_entry_plan


def test_buy_plan_splits_lots_and_never_exceeds_three_entries():
    plan = build_premium_entry_plan(
        position="BUY",
        current_premium=100.0,
        bid=99.0,
        ask=101.0,
        total_lots=5,
        entries=3,
    )
    assert len(plan.entries) == 3
    assert sum(item.lots for item in plan.entries) == 5
    assert plan.entries[0].premium >= plan.entries[1].premium >= plan.entries[2].premium
    assert "automatic averaging nahi" in plan.warning


def test_sell_plan_uses_higher_staged_limits():
    plan = build_premium_entry_plan(
        position="SELL",
        current_premium=80.0,
        bid=79.5,
        ask=80.5,
        total_lots=3,
        entries=3,
    )
    assert plan.entries[0].premium <= plan.entries[1].premium <= plan.entries[2].premium
