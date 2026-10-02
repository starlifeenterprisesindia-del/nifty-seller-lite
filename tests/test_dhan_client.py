import pytest

from models import Credentials
from services.dhan_client import DhanClient


def test_credentials_required():
    with pytest.raises(ValueError):
        DhanClient(Credentials(client_id="", access_token="token"))


def test_market_quote_deduplicates_ids():
    class Session:
        def post(self, url, headers, json, timeout):
            assert json == {"NSE_EQ": [1, 2]}

            class Response:
                status_code = 200
                content = b"{}"
                text = "{}"

                def json(self):
                    return {"status": "success", "data": {}}

            return Response()

    client = DhanClient(Credentials("1", "token"), session=Session())
    result = client.market_quote({"NSE_EQ": [2, 1, 2]})
    assert result["status"] == "success"


def test_rate_limit_is_not_retried():
    class Session:
        calls = 0

        def post(self, url, headers, json, timeout):
            self.calls += 1

            class Response:
                status_code = 429
                content = b'{"data":{"805":"Too many requests"},"status":"failed"}'
                text = content.decode()

                def json(self):
                    return {"data": {"805": "Too many requests"}, "status": "failed"}

            return Response()

    session = Session()
    client = DhanClient(Credentials("1", "token"), session=session)
    with pytest.raises(Exception, match="Too many requests"):
        client.expiry_list()
    assert session.calls == 1


def test_expiry_list_and_option_chain_share_client_rate_slot(monkeypatch):
    class Session:
        def __init__(self):
            self.urls = []

        def post(self, url, headers, json, timeout):
            self.urls.append(url)

            class Response:
                status_code = 200
                content = b"{}"
                text = "{}"

                def json(self_inner):
                    if url.endswith("/optionchain/expirylist"):
                        return {"status": "success", "data": ["2026-10-06"]}
                    return {"status": "success", "data": {}}

            return Response()

    now = {"value": 100.0}
    sleeps = []

    def fake_monotonic():
        return now["value"]

    def fake_sleep(seconds):
        sleeps.append(seconds)
        now["value"] += seconds

    monkeypatch.setattr("services.dhan_client.time.monotonic", fake_monotonic)
    monkeypatch.setattr("services.dhan_client.time.sleep", fake_sleep)
    session = Session()
    client = DhanClient(Credentials("1", "token"), session=session)
    client.expiry_list()
    client.option_chain(expiry="2026-10-06")

    assert len(session.urls) == 2
    assert sleeps and sleeps[-1] >= 3.04
