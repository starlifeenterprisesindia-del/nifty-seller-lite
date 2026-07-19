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
