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


def test_alert_history_records_delivery_without_extra_market_work():
    messages: list[str] = []
    engine = LiveAlertEngine(
        ReadyNotifier(), confirmations=2, cooldown_seconds=180, sender=messages.append,
        async_delivery=False,
    )
    changes = {5: 5.0, 15: 12.0, 30: 20.0, 60: 35.0}
    engine.observe(changes=changes, ltp=24300, now_ts=1000, enforce_market_hours=False)
    assert engine.observe(changes=changes, ltp=24305, now_ts=1002, enforce_market_hours=False)
    history = engine.alert_history()
    assert history and history[0]["kind"] == "FAST_MOVE"
    assert history[0]["status"] == "SENT"
    assert history[0]["latency_seconds"] >= 0


def test_market_intelligence_alert_accepts_mixed_direction_and_dedupes():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    messages: list[str] = []
    engine = LiveAlertEngine(
        ReadyNotifier(), sender=messages.append, async_delivery=False
    )
    now = datetime(2026, 10, 6, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    payload = {
        "captured_at": now.isoformat(),
        "alert_id": "PRESSURE_WATCH:MIXED:65",
        "kind": "PRESSURE_WATCH",
        "title": "Large move building",
        "direction": "MIXED",
        "nifty_ltp": 25000,
        "score": 66,
        "coverage": 80,
        "alignment": "NO CLEAR ALIGNMENT",
        "message": "Large move building — direction unclear",
    }
    assert engine.observe_market_intelligence(payload, now_ts=now.timestamp())
    assert len(messages) == 1
    assert not engine.observe_market_intelligence(payload, now_ts=now.timestamp() + 1)
    history = engine.alert_history()
    assert history[0]["kind"] == "MARKET_INTELLIGENCE"
    assert history[0]["direction"] == "MIXED"
