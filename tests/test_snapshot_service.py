from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import CONFIG
from services.snapshot_service import SnapshotService
from services.instrument_master import ResolvedInstrument


IST = ZoneInfo("Asia/Kolkata")


class StubMaster:
    def load(self):
        return None

    def resolve_nearest_nifty_future(self, raw):
        return None


class StubClient:
    def market_quote(self, grouped):
        assert set(grouped["IDX_I"]) == {
            int(CONFIG.nifty.security_id),
            int(CONFIG.india_vix.security_id),
        }
        assert set(grouped["NSE_EQ"]) == {int(item.security_id) for item in CONFIG.top7}
        quote_time = int(datetime(2026, 7, 17, 15, 29, tzinfo=IST).timestamp())
        equities = {
            item.security_id: {
                "last_price": 100.0,
                "last_trade_time": quote_time,
                "ohlc": {"open": 99, "high": 101, "low": 98, "close": 99.5},
                "volume": 1000,
            }
            for item in CONFIG.top7
        }
        return {
            "status": "success",
            "data": {
                "IDX_I": {
                    CONFIG.nifty.security_id: {
                        "last_price": 24334.3,
                        "last_trade_time": quote_time,
                        "ohlc": {
                            "open": 24200,
                            "high": 24400,
                            "low": 24150,
                            "close": 24300,
                        },
                    },
                    CONFIG.india_vix.security_id: {
                        "last_price": 12.5,
                        "last_trade_time": quote_time,
                        "ohlc": {"open": 13, "high": 13, "low": 12, "close": 12.8},
                    },
                },
                "NSE_EQ": equities,
            },
        }

    def intraday_candles(self, **kwargs):
        interval = int(kwargs["interval"])
        count = 180 if interval == 1 else 80
        step = timedelta(minutes=interval)
        start = datetime(2026, 7, 17, 9, 15, tzinfo=IST)
        timestamps = [int((start + step * index).timestamp()) for index in range(count)]
        closes = [24000 + index for index in range(count)]
        return {
            "open": closes,
            "high": [item + 2 for item in closes],
            "low": [item - 2 for item in closes],
            "close": closes,
            "volume": [1000] * count,
            "timestamp": timestamps,
            "open_interest": [0] * count,
        }

    def expiry_list(self, *args, **kwargs):
        return ["2026-07-21"]

    def option_chain(self, **kwargs):
        return {
            "status": "success",
            "data": {
                "last_price": 24334.3,
                "oc": {
                    "24350.000000": {
                        "ce": {
                            "last_price": 100,
                            "oi": 1000,
                            "previous_oi": 900,
                            "volume": 100,
                            "previous_volume": 50,
                            "previous_close_price": 90,
                            "security_id": 1,
                        },
                        "pe": {
                            "last_price": 110,
                            "oi": 1200,
                            "previous_oi": 1000,
                            "volume": 120,
                            "previous_volume": 60,
                            "previous_close_price": 120,
                            "security_id": 2,
                        },
                    }
                },
            },
        }


def test_weekend_snapshot_is_reference_and_top7_are_present():
    service = SnapshotService(StubClient(), StubMaster())
    snapshot = service.build(datetime(2026, 7, 19, 13, 37, tzinfo=IST))
    assert snapshot.market_session.code == "CLOSED_WEEKEND"
    assert snapshot.feed_status["quotes"].use_state == "REFERENCE"
    assert snapshot.feed_status["option_chain"].use_state == "REFERENCE"
    assert len(snapshot.heavyweight_quotes) == 7
    assert snapshot.feed_status["instruments"].use_state == "READY"
    assert snapshot.indicators.three_minute.status == "READY"
    assert snapshot.indicators.fifteen_minute.status == "READY"


class StubFutureMaster:
    def load(self):
        return None

    def resolve_nearest_nifty_future(self, raw):
        return ResolvedInstrument(
            symbol="NIFTY_FUT",
            security_id=999,
            exchange_segment="NSE_FNO",
            instrument="FUTIDX",
            display_name="NIFTY JUL FUT",
            expiry="2026-07-30",
        )


class StubFutureClient(StubClient):
    def market_quote(self, grouped):
        assert grouped["NSE_FNO"] == [999]
        response = super().market_quote(
            {key: value for key, value in grouped.items() if key != "NSE_FNO"}
        )
        response["data"]["NSE_FNO"] = {
            "999": {
                "last_price": 24370.0,
                "last_trade_time": int(
                    datetime(2026, 7, 17, 15, 29, tzinfo=IST).timestamp()
                ),
                "ohlc": {"open": 24220, "high": 24420, "low": 24180, "close": 24310},
                "volume": 200000,
            }
        }
        return response


def test_snapshot_connects_nifty_future_volume_to_core_engine():
    service = SnapshotService(StubFutureClient(), StubFutureMaster())
    snapshot = service.build(datetime(2026, 7, 19, 13, 37, tzinfo=IST))
    assert snapshot.nifty_future_quote is not None
    assert not snapshot.future_candles_3m.empty
    assert not snapshot.future_candles_15m.empty
    assert snapshot.feed_status["future_volume"].ok
    assert snapshot.volume.source == "NIFTY FUTURES"
    assert snapshot.core_evidence.status == "REFERENCE ONLY"
