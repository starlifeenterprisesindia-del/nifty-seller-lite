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
    version: str = "0.2.0_FOUNDATION_INDICATORS"
    request_timeout_seconds: int = 12
    quote_max_age_seconds: int = 12
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
    india_vix: InstrumentRef = InstrumentRef(
        name="INDIA VIX",
        security_id="21",
        exchange_segment="IDX_I",
        instrument="INDEX",
        symbol="INDIA VIX",
    )
    top7: tuple[InstrumentRef, ...] = (
        InstrumentRef("HDFC Bank", "1333", "NSE_EQ", "EQUITY", "HDFCBANK"),
        InstrumentRef("Reliance Industries", "2885", "NSE_EQ", "EQUITY", "RELIANCE"),
        InstrumentRef("ICICI Bank", "4963", "NSE_EQ", "EQUITY", "ICICIBANK"),
        InstrumentRef("Bharti Airtel", "10604", "NSE_EQ", "EQUITY", "BHARTIARTL"),
        InstrumentRef("Infosys", "1594", "NSE_EQ", "EQUITY", "INFY"),
        InstrumentRef("Larsen & Toubro", "11483", "NSE_EQ", "EQUITY", "LT"),
        InstrumentRef("Tata Consultancy Services", "11536", "NSE_EQ", "EQUITY", "TCS"),
    )
    option_strikes_each_side: int = 5
    candle_lookback_days: int = 5
    minimum_one_minute_candles: int = 30
    minimum_indicator_candles: int = 50

    @property
    def top7_symbols(self) -> tuple[str, ...]:
        return tuple(item.symbol for item in self.top7)


CONFIG = AppConfig()
