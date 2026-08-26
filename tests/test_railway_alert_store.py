from services.railway_alert_store import PremiumAlertMonitor, RailwayAlertStore


def test_touch_above_below_modes():
    assert PremiumAlertMonitor._triggered(
        {"target_premium": 100, "mode": "TOUCH", "tolerance": 0.5}, 100.4
    )
    assert PremiumAlertMonitor._triggered(
        {"target_premium": 100, "mode": "ABOVE"}, 101
    )
    assert PremiumAlertMonitor._triggered(
        {"target_premium": 100, "mode": "BELOW"}, 99
    )


def test_store_add_cancel(tmp_path):
    store = RailwayAlertStore(str(tmp_path / "alerts.json"))
    row = store.add(
        {
            "security_id": 123,
            "side": "CE",
            "position": "BUY",
            "strike": 24000,
            "target_premium": 50,
        }
    )
    assert len(store.list(active_only=True)) == 1
    assert store.cancel(row["id"])
    assert store.list(active_only=True) == []
