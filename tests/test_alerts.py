from types import SimpleNamespace as NS

from analysis.alerts import (
    early_activity_alert_qualifies,
    heavy_activity_alert_qualifies,
    target_crossed,
)


def activity(**updates):
    values = {
        "status": "READY",
        "direction": "SELLING",
        "score": 79,
        "confirmation_count": 3,
        "state": "VERY STRONG",
        "futures_volume_ratio": 2.0,
        "option_confirmation": "BULLISH",
        "top7_confirmation": "NARROW BULLISH",
    }
    values.update(updates)
    return NS(**values)


def test_heavy_alert_requires_70_and_two_confirmations():
    assert heavy_activity_alert_qualifies(activity())
    assert not heavy_activity_alert_qualifies(activity(score=69.9))
    assert not heavy_activity_alert_qualifies(activity(confirmation_count=1))
    # A presentation-state label must not suppress numeric confirmed evidence.
    assert heavy_activity_alert_qualifies(activity(state="STRONG", score=71))


def test_early_alert_catches_one_of_three_directional_surge():
    assert early_activity_alert_qualifies(
        activity(score=75, confirmation_count=1, state="STRONG")
    )
    assert not early_activity_alert_qualifies(
        activity(score=64.9, confirmation_count=1, state="STRONG")
    )
    assert not early_activity_alert_qualifies(
        activity(
            score=70,
            confirmation_count=1,
            futures_volume_ratio=1.1,
            option_confirmation="MIXED",
            top7_confirmation="MIXED",
        )
    )


def test_target_crosses_from_below_or_above():
    assert target_crossed(armed_spot=24300, current_spot=24355, target=24350)
    assert target_crossed(armed_spot=24400, current_spot=24345, target=24350)
    assert not target_crossed(armed_spot=24300, current_spot=24340, target=24350)
