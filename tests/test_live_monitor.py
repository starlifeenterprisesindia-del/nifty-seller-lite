import pandas as pd

from services.live_monitor import fetch_fast_quotes


class FakeClient:
    def market_quote(self, grouped):
        assert grouped == {"IDX_I": [13], "NSE_FNO": [101, 102]}
        return {
            "data": {
                "IDX_I": {"13": {"last_price": 24305.0, "last_trade_time": "live"}},
                "NSE_FNO": {
                    "101": {"last_price": 110.0, "last_trade_time": "live"},
                    "102": {"last_price": 95.0, "last_trade_time": "live"},
                },
            }
        }


class Snapshot:
    nifty_quote = {"last_price": 24300.0}
    option_chain = pd.DataFrame(
        [
            {"strike": 24300.0, "side": "CE", "security_id": 101, "last_price": 108.0},
            {"strike": 24300.0, "side": "PE", "security_id": 102, "last_price": 98.0},
            {"strike": 24350.0, "side": "CE", "security_id": 103, "last_price": 80.0},
        ]
    )


def test_fast_monitor_fetches_nifty_and_atm_pair_in_one_batch():
    rows = fetch_fast_quotes(FakeClient(), Snapshot())
    assert [row.label for row in rows] == ["NIFTY Live", "24,300 CE", "24,300 PE"]
    assert [row.change for row in rows] == [5.0, 2.0, -3.0]
