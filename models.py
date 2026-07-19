from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class Credentials:
    client_id: str
    access_token: str

    def validate(self) -> None:
        if not self.client_id.strip():
            raise ValueError("Dhan client_id is missing")
        if not self.access_token.strip():
            raise ValueError("Dhan access_token is missing")


@dataclass
class FeedStatus:
    name: str
    ok: bool
    fetched_at: datetime
    age_seconds: float | None = None
    message: str = ""
    source: str = "DhanHQ"
    use_state: str = "UNAVAILABLE"


@dataclass(frozen=True)
class MarketSession:
    code: str
    label: str
    is_live: bool
    message: str


@dataclass(frozen=True)
class TimeframeIndicators:
    timeframe: str
    as_of: datetime | None
    close: float | None
    ema20: float | None
    ema50: float | None
    ema_state: str
    macd: float | None
    macd_signal: float | None
    macd_histogram: float | None
    macd_state: str
    rsi14: float | None
    rsi_state: str
    status: str


@dataclass(frozen=True)
class IndicatorBundle:
    three_minute: TimeframeIndicators
    fifteen_minute: TimeframeIndicators


@dataclass
class MarketSnapshot:
    snapshot_id: str
    created_at: datetime
    market_session: MarketSession
    nifty_quote: dict[str, Any]
    vix_quote: dict[str, Any] | None
    nifty_future_quote: dict[str, Any] | None
    heavyweight_quotes: list[dict[str, Any]]
    candles_1m: pd.DataFrame
    candles_3m: pd.DataFrame
    candles_15m: pd.DataFrame
    indicators: IndicatorBundle
    expiry: str | None
    option_chain: pd.DataFrame
    feed_status: dict[str, FeedStatus]
    metadata: dict[str, Any] = field(default_factory=dict)

    def public_summary(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at.isoformat(),
            "market_session": asdict(self.market_session),
            "nifty_last_price": self.nifty_quote.get("last_price"),
            "expiry": self.expiry,
            "option_rows": int(len(self.option_chain)),
            "candles_1m": int(len(self.candles_1m)),
            "candles_3m": int(len(self.candles_3m)),
            "candles_15m": int(len(self.candles_15m)),
            "indicators": {
                "3m": asdict(self.indicators.three_minute),
                "15m": asdict(self.indicators.fifteen_minute),
            },
            "feeds": {name: asdict(status) for name, status in self.feed_status.items()},
        }
