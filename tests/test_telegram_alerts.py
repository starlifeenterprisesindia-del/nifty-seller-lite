from services.telegram_alerts import LiveAlertEngine


class ReadyNotifier:
    configured = True


def test_live_alert_requires_confirmation_and_dedupes():
    messages: list[str] = []
    engine = LiveAlertEngine(
        ReadyNotifier(), confirmations=2, cooldown_seconds=180, sender=messages.append,
        async_delivery=False,
    )
    changes = {5: 5.0, 15: 12.0, 30: 20.0, 60: 35.0}
    assert not engine.observe(
        changes=changes, ltp=24300, now_ts=1000, enforce_market_hours=False
    )
    assert engine.observe(
        changes=changes, ltp=24305, now_ts=1002, enforce_market_hours=False
    )
    assert len(messages) == 1
    assert not engine.observe(
        changes=changes, ltp=24306, now_ts=1004, enforce_market_hours=False
    )


def test_mixed_move_does_not_alert():
    messages: list[str] = []
    engine = LiveAlertEngine(
        ReadyNotifier(), sender=messages.append, async_delivery=False
    )
    changes = {5: 3.0, 15: -5.0, 30: 2.0, 60: -4.0}
    for timestamp in (1000, 1002, 1004):
        assert not engine.observe(
            changes=changes, ltp=24300, now_ts=timestamp, enforce_market_hours=False
        )
    assert messages == []
