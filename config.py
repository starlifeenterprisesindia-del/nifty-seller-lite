from __future__ import annotations

from dataclasses import dataclass
from datetime import time

IST_TIMEZONE = "Asia/Kolkata"
DHAN_BASE_URL = "https://api.dhan.co/v2"
INSTRUMENT_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"


@dataclass(frozen=True)
class InstrumentRef:
    name: str
    security_id: str
    exchange_segment: str
    instrument: str
    symbol: str = ""


@dataclass(frozen=True)
class AppConfig:
    app_name: str = "Nifty Seller Lite"
    version: str = "0.1.0_FOUNDATION"
    request_timeout_seconds: int = 12
    quote_max_age_seconds: int = 12
    option_chain_max_age_seconds: int = 12
    candle_max_age_minutes: int = 5
    market_open: time = time(9, 15)
    market_close: time = time(15, 30)
    nifty: InstrumentRef = InstrumentRef(
        name="NIFTY 50",
        security_id="13",
        exchange_segment="IDX_I",
        instrument="INDEX",
        symbol="NIFTY",
    )
    top7_symbols: tuple[str, ...] = (
        "HDFCBANK",
        "RELIANCE",
        "ICICIBANK",
        "BHARTIARTL",
        "INFY",
        "LT",
        "TCS",
    )
    option_strikes_each_side: int = 5
    candle_lookback_days: int = 5
    minimum_one_minute_candles: int = 30


CONFIG = AppConfig()
