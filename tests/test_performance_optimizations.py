from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from services.dhan_gateway import DhanGateway
from services.instrument_master import InstrumentMaster


class CountingClient:
    def __init__(self):
        self.calls = []
        self.timeout = 12

    def intraday_candles(self, **kwargs):
        self.calls.append((kwargs["security_id"], kwargs["interval"], kwargs["to_date"]))
        return {"data": {"timestamp": [], "open": [], "high": [], "low": [], "close": [], "volume": []}}


def make_gateway(monkeypatch):
    client = CountingClient()
    monkeypatch.setattr("services.dhan_gateway.DhanClient", lambda credentials: client)
    gateway = DhanGateway("id", "token")
    return gateway, client


def payload(at: datetime, interval: int = 1):
    return {
        "security_id": "13",
        "exchange_segment": "IDX_I",
        "instrument": "INDEX",
        "interval": interval,
        "from_date": (at - timedelta(days=7)).isoformat(),
        "to_date": at.isoformat(),
        "include_oi": False,
    }


def test_intraday_cache_ignores_snapshot_seconds(monkeypatch):
    gateway, client = make_gateway(monkeypatch)
    first = datetime(2026, 9, 7, 10, 31, 5)
    second = datetime(2026, 9, 7, 10, 31, 48)
    gateway.intraday(payload(first, 1))
    gateway.intraday(payload(second, 1))
    assert len(client.calls) == 1


def test_15m_cache_refreshes_only_at_next_bucket(monkeypatch):
    gateway, client = make_gateway(monkeypatch)
    gateway.intraday(payload(datetime(2026, 9, 7, 10, 31, 5), 15))
    gateway.intraday(payload(datetime(2026, 9, 7, 10, 44, 50), 15))
    assert len(client.calls) == 1
    gateway.intraday(payload(datetime(2026, 9, 7, 10, 45, 1), 15))
    assert len(client.calls) == 2


def test_instrument_master_reuses_raw_and_normalized_frames(tmp_path: Path):
    path = tmp_path / "master.csv"
    pd.DataFrame(
        {
            "SECURITY_ID": [13, 33, 999],
            "EXCH_ID": ["NSE", "NSE", "NSE"],
            "SEGMENT": ["I", "I", "D"],
            "INSTRUMENT": ["INDEX", "INDEX", "FUTIDX"],
            "SYMBOL_NAME": ["NIFTY", "INDIAVIX", "NIFTY"],
            "DISPLAY_NAME": ["NIFTY", "INDIA VIX", "NIFTY SEP FUT"],
            "SM_EXPIRY_DATE": [None, None, "2026-09-24"],
            "UNDERLYING_SYMBOL": ["", "", "NIFTY"],
        }
    ).to_csv(path, index=False)
    InstrumentMaster.clear_memory_cache()
    master = InstrumentMaster(path)
    raw1 = master.load(allow_download=False)
    raw2 = master.load(allow_download=False)
    assert raw1 is raw2
    normalized1 = master.normalize(raw1)
    normalized2 = master.normalize(raw2)
    assert normalized1 is normalized2
