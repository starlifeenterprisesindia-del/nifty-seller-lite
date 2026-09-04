from services.dhan_gateway import DhanGateway


class Client:
    def intraday_candles(self, **kwargs):
        return {"rows": [kwargs["to_date"].isoformat()]}


def gateway(monkeypatch, maximum="8"):
    monkeypatch.setenv("DHAN_GATEWAY_CACHE_MAX_ENTRIES", maximum)
    monkeypatch.setattr("services.dhan_gateway.DhanClient", lambda credentials: Client())
    item = DhanGateway("id", "token")
    item.intraday = lambda payload: item._run(
        "intraday", payload, lambda: {"payload": payload},
        cache_seconds=18, min_spacing_seconds=0,
    )
    return item


def test_gateway_cache_is_hard_bounded(monkeypatch):
    item = gateway(monkeypatch)
    for minute in range(30):
        item.intraday({"minute": minute})
    assert len(item._cache) == 8
    assert item.status()["cache_max_entries"] == 8


def test_foreground_idle_marker(monkeypatch):
    item = gateway(monkeypatch)
    assert item.foreground_idle_seconds() > 100
    item.mark_foreground()
    assert item.foreground_idle_seconds() < 1
