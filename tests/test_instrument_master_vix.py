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
