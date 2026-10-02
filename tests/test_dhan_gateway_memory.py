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

class OptionClient:
    def __init__(self):
        self.timeout = 12
        self.calls = []

    def expiry_list(self, underlying_security_id, segment):
        self.calls.append(("expiry", underlying_security_id, segment))
        return ["2026-10-06"]

    def option_chain(self, *, expiry, underlying_security_id, segment):
        self.calls.append(("chain", expiry, underlying_security_id, segment))
        return {"status": "success", "data": {}}

    def market_quote(self, instruments):
        self.calls.append(("quote", instruments))
        return {"status": "success", "data": {}}


def test_expiry_and_option_chain_share_one_rate_limit_family(monkeypatch):
    client = OptionClient()
    monkeypatch.setattr("services.dhan_gateway.DhanClient", lambda credentials: client)

    now = {"value": 100.0}
    sleeps = []

    def fake_monotonic():
        return now["value"]

    def fake_sleep(seconds):
        sleeps.append(seconds)
        now["value"] += seconds

    monkeypatch.setattr("services.dhan_gateway.time.monotonic", fake_monotonic)
    monkeypatch.setattr("services.dhan_gateway.time.sleep", fake_sleep)

    item = DhanGateway("id", "token")
    item.expiry_list(13, "IDX_I")
    item.option_chain("2026-10-06", 13, "IDX_I")

    assert len(client.calls) == 2
    assert sleeps and sleeps[-1] >= 3.14
    assert item.status()["option_family_shared_limiter"] is True


def test_background_quote_yields_to_recent_foreground(monkeypatch):
    client = OptionClient()
    monkeypatch.setattr("services.dhan_gateway.DhanClient", lambda credentials: client)
    item = DhanGateway("id", "token")
    item.mark_foreground()
    import pytest
    with pytest.raises(RuntimeError, match="Foreground request priority"):
        item.background_market_quote({"IDX_I": [13]})
    assert client.calls == []


def test_429_backoff_grows_before_next_success(monkeypatch):
    client = OptionClient()
    monkeypatch.setattr("services.dhan_gateway.DhanClient", lambda credentials: client)
    monkeypatch.setenv("DHAN_GATEWAY_429_BASE_COOLDOWN_SECONDS", "20")
    monkeypatch.setenv("DHAN_GATEWAY_429_MAX_COOLDOWN_SECONDS", "120")

    now = {"value": 100.0}
    monkeypatch.setattr("services.dhan_gateway.time.monotonic", lambda: now["value"])
    monkeypatch.setattr("services.dhan_gateway.time.sleep", lambda seconds: now.__setitem__("value", now["value"] + seconds))
    item = DhanGateway("id", "token")

    def limited():
        raise RuntimeError("DhanHQ HTTP 429: Too many requests")

    import pytest
    with pytest.raises(RuntimeError, match="429"):
        item._run("limited", {"n": 1}, limited, cache_seconds=0, min_spacing_seconds=0)
    first = item.status()
    assert first["last_429_cooldown_seconds"] == 20.0
    assert first["consecutive_429"] == 1

    now["value"] += 21.0
    with pytest.raises(RuntimeError, match="429"):
        item._run("limited", {"n": 2}, limited, cache_seconds=0, min_spacing_seconds=0)
    second = item.status()
    assert second["last_429_cooldown_seconds"] == 40.0
    assert second["consecutive_429"] == 2
