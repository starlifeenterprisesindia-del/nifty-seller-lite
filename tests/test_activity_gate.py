from types import SimpleNamespace as NS
import pytest
from analysis.activity_gate import activity_gate, confirmed_activity
from test_execution_guard import run, feeds, risk_profile


def activity(**changes):
    values = dict(status="READY", direction="SELLING", score=89,
                  confirmation_count=2, price_response="FOLLOW-THROUGH")
    values.update(changes)
    return NS(**values)


@pytest.mark.parametrize("setup, direction, blocked", [
    ("CE SELL", "SELLING", False), ("CE SELL", "BUYING", True),
    ("PE SELL", "BUYING", False), ("PE SELL", "SELLING", True),
    ("CE BUY", "BUYING", False), ("CE BUY", "SELLING", True),
    ("PE BUY", "SELLING", False), ("PE BUY", "BUYING", True),
    ("IRON CONDOR", "BUYING", True), ("IRON CONDOR", "SELLING", True),
])
def test_shared_activity_policy(setup, direction, blocked):
    assert activity_gate(setup, activity(direction=direction))[0] is blocked
    assert activity_gate(setup + " WITH HEDGE", activity(direction=direction))[0] is blocked


@pytest.mark.parametrize("changes", [
    {"score": 59}, {"confirmation_count": 1}, {"status": "PARTIAL"},
    {"direction": "MIXED"}, {"price_response": "PRICE HOLDING / STALLED"},
    {"price_response": "OPPOSITE RESPONSE"}, {"price_response": "UNCONFIRMED"},
])
def test_unconfirmed_pressure_not_a_second_mandatory_vote(changes):
    bp = activity(**changes)
    assert not confirmed_activity(bp)
    assert not activity_gate("PE SELL", bp)[0]
    result = run(big_player=bp)
    assert result.readiness == "ENTRY READY"
    assert any("context only" in x for x in result.reasons)


def test_real_guard_blocks_confirmed_opposite_and_keeps_other_safety():
    bp = activity()
    result = run(big_player=bp)
    assert result.readiness == "BLOCKED"
    assert any("opposes" in x for x in result.blockers)
    stalled = activity(price_response="PRICE HOLDING / STALLED")
    assert run(big_player=stalled, feed_status=feeds(live=False)).readiness == "BLOCKED"
    assert run(big_player=stalled, risk_profile=risk_profile(capital=100)).allowed_lots == 0


def test_missing_activity_remains_context_only():
    assert not activity_gate("PE SELL", None)[0]
    assert not confirmed_activity(None)
