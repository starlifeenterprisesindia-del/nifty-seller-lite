from dataclasses import replace

from analysis.trade_plan import calculate_trade_plan
from test_trade_plan import decision, levels, live_session, option_frame, option_intelligence


def _theta_frame():
    frame = option_frame()
    frame["theta"] = -2.0
    # Nearer usable strikes carry more decay in this deterministic fixture.
    frame["theta"] = -(
        (400 - (frame["strike"] - 24350).abs()).clip(lower=1) / 40
    )
    return frame


def _plan(direction: str):
    options = replace(
        option_intelligence(), market_bias="MIXED", persistence="RANGE"
    )
    return calculate_trade_plan(
        frame=_theta_frame(),
        spot=24350,
        expiry="2026-07-21",
        levels=levels(),
        options=options,
        decision=decision("WAIT"),
        market_session=live_session(),
        future_direction=direction,
        future_strength=70,
    )


def test_pair_audit_contains_decay_and_reward_aware_score():
    plan = _plan("UP").pe_sell
    assert plan.pair_comparison
    assert "net_theta_edge" in plan.pair_comparison[0]
    assert "theta_15m_points" in plan.pair_comparison[0]
    assert any("decay 25%" in reason for reason in plan.reasons)
    assert any("Future Brain alignment" in reason for reason in plan.reasons)


def test_condor_joint_ranking_skews_room_away_from_forecast_risk():
    up = _plan("UP").iron_condor
    down = _plan("DOWN").iron_condor
    up_pe, up_ce = up.short_legs
    down_pe, down_ce = down.short_legs
    assert up_ce.strike - 24350 >= 24350 - up_pe.strike
    assert 24350 - down_pe.strike >= down_ce.strike - 24350
    assert any("Jointly compared" in reason for reason in up.reasons)


def test_condor_does_not_use_equal_premium_as_objective():
    plan = _plan("UP").iron_condor
    assert any("premium equality was not used" in reason for reason in plan.reasons)
    assert plan.estimated_credit_points > 0

