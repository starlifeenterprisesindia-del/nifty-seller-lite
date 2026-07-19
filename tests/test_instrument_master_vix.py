import pandas as pd

from services.instrument_master import InstrumentMaster


def test_instrument_master_resolves_india_vix_from_current_master_shape():
    raw = pd.DataFrame(
        [
            {
                "SECURITY_ID": 33,
                "EXCH_ID": "NSE",
                "SEGMENT": "I",
                "INSTRUMENT": "INDEX",
                "SYMBOL_NAME": "INDIA VIX",
                "DISPLAY_NAME": "INDIA VIX",
            },
            {
                "SECURITY_ID": 13,
                "EXCH_ID": "NSE",
                "SEGMENT": "I",
                "INSTRUMENT": "INDEX",
                "SYMBOL_NAME": "NIFTY",
                "DISPLAY_NAME": "NIFTY 50",
            },
        ]
    )
    resolved = InstrumentMaster().resolve_india_vix(raw)
    assert resolved is not None
    assert resolved.security_id == 33
    assert resolved.exchange_segment == "IDX_I"
    assert resolved.instrument == "INDEX"


def test_stale_instrument_cache_refreshes_and_falls_back_safely(tmp_path, monkeypatch):
    import os
    import time

    cache = tmp_path / "master.csv"
    stale = pd.DataFrame([{"SECURITY_ID": 33, "SYMBOL_NAME": "INDIA VIX"}])
    stale.to_csv(cache, index=False)
    old = time.time() - 48 * 3600
    os.utime(cache, (old, old))
    master = InstrumentMaster(cache)

    refreshed = pd.DataFrame([{"SECURITY_ID": 13, "SYMBOL_NAME": "NIFTY"}])
    monkeypatch.setattr(master, "download", lambda: refreshed)
    assert master.load().equals(refreshed)

    monkeypatch.setattr(
        master, "download", lambda: (_ for _ in ()).throw(RuntimeError("down"))
    )
    fallback = master.load()
    assert fallback.equals(stale)
