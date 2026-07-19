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
    version: str = "0.5.0_CORE_MARKET_ENGINE"
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
    candle_lookback_days: int = 7
    minimum_one_minute_candles: int = 30
    minimum_indicator_candles: int = 50

    # Core-market-engine settings. These values live in one central config so
    # price action, levels and volume cannot quietly drift apart.
    swing_left_bars: int = 2
    swing_right_bars: int = 2
    minimum_structure_swings: int = 2
    atr_period: int = 14
    breakout_atr_tolerance: float = 0.08
    level_zone_atr_fraction: float = 0.20
    minimum_level_zone_points: float = 3.0
    maximum_level_zone_points: float = 15.0
    opening_range_minutes: int = 15
    volume_baseline_sessions: int = 5
    volume_recent_fallback_bars: int = 20
    volume_low_ratio: float = 0.70
    volume_high_ratio: float = 1.20
    volume_surge_ratio: float = 1.80

    @property
    def top7_symbols(self) -> tuple[str, ...]:
        return tuple(item.symbol for item in self.top7)


CONFIG = AppConfig()
