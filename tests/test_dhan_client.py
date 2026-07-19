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
