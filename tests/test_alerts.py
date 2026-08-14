from types import SimpleNamespace as NS

from analysis.alerts import heavy_activity_alert_qualifies, target_crossed


def activity(**updates):
    values = {
        "status": "READY",
        "direction": "SELLING",
        "score": 79,
        "confirmation_count": 3,
        "state": "VERY STRONG",
    }
    values.update(updates)
    return NS(**values)


def test_heavy_alert_requires_75_and_two_confirmations():
    assert heavy_activity_alert_qualifies(activity())
    assert not heavy_activity_alert_qualifies(activity(score=74.9))
    assert not heavy_activity_alert_qualifies(activity(confirmation_count=1))
    assert not heavy_activity_alert_qualifies(activity(state="STRONG"))


def test_target_crosses_from_below_or_above():
    assert target_crossed(armed_spot=24300, current_spot=24355, target=24350)
    assert target_crossed(armed_spot=24400, current_spot=24345, target=24350)
    assert not target_crossed(armed_spot=24300, current_spot=24340, target=24350)
