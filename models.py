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


@dataclass(frozen=True)
class TimeframePriceAction:
    timeframe: str
    as_of: datetime | None
    structure: str
    event: str
    move_stage: str
    last_swing_high: float | None
    prior_swing_high: float | None
    last_swing_low: float | None
    prior_swing_low: float | None
    invalidation_level: float | None
    atr14: float | None
    bullish_score: float
    bearish_score: float
    range_score: float
    confidence: float
    reasons: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class PriceActionBundle:
    three_minute: TimeframePriceAction
    fifteen_minute: TimeframePriceAction
    relationship: str
    combined_state: str
    confidence: float


@dataclass(frozen=True)
class MarketLevel:
    label: str
    side: str
    lower: float
    upper: float
    midpoint: float
    strength: float
    status: str
    distance_points: float
    sources: tuple[str, ...]


@dataclass(frozen=True)
class LevelBundle:
    as_of: datetime | None
    current_price: float | None
    immediate_support: MarketLevel | None
    strong_support: MarketLevel | None
    immediate_resistance: MarketLevel | None
    strong_resistance: MarketLevel | None
    previous_day_high: float | None
    previous_day_low: float | None
    opening_range_high: float | None
    opening_range_low: float | None
    upside_room: float | None
    downside_room: float | None
    current_position: str
    zone_width: float | None
    status: str


@dataclass(frozen=True)
class TimeframeVolume:
    timeframe: str
    as_of: datetime | None
    current_volume: float | None
    baseline_volume: float | None
    relative_volume: float | None
    volume_state: str
    volume_trend: str
    price_direction: str
    move_support: str
    baseline_samples: int
    confidence: float
    status: str


@dataclass(frozen=True)
class VolumeBundle:
    source: str
    three_minute: TimeframeVolume
    fifteen_minute: TimeframeVolume
    overall_view: str
    confidence: float
    status: str


@dataclass(frozen=True)
class CoreMarketEvidence:
    bullish_score: float
    bearish_score: float
    range_score: float
    confidence: float
    market_state: str
    move_stage: str
    status: str
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class FlowWindow:
    label: str
    target_seconds: int
    actual_age_seconds: float | None
    ce_oi_delta: float | None
    pe_oi_delta: float | None
    ce_premium_delta: float | None
    pe_premium_delta: float | None
    ce_volume_delta: float | None
    pe_volume_delta: float | None
    bias: str
    status: str


@dataclass(frozen=True)
class OIWall:
    side: str
    strike: float | None
    oi: float | None
    previous_strike: float | None
    migration_points: float | None
    cluster_center: float | None
    cluster_oi: float | None
    status: str


@dataclass(frozen=True)
class PCRBundle:
    near_atm_oi_pcr: float | None
    day_addition_pcr: float | None
    intraday_addition_pcr: float | None
    volume_pcr: float | None
    state: str
    status: str


@dataclass(frozen=True)
class OptionIntelligence:
    as_of: datetime
    basis: str
    snapshot_count: int
    bullish_score: float
    bearish_score: float
    range_score: float
    confidence: float
    market_bias: str
    persistence: str
    ce_wall: OIWall
    pe_wall: OIWall
    pcr: PCRBundle
    windows: tuple[FlowWindow, ...]
    flow_rows: tuple[dict[str, Any], ...]
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class HeavyweightContribution:
    symbol: str
    name: str
    official_weight_pct: float
    last_price: float | None
    change_pct: float | None
    index_contribution_pct: float | None
    direction: str


@dataclass(frozen=True)
class HeavyweightBundle:
    as_of: datetime
    rows: tuple[HeavyweightContribution, ...]
    covered_weight_pct: float
    weighted_move_pct: float | None
    estimated_index_contribution_pct: float | None
    advancing: int
    declining: int
    unchanged: int
    state: str
    confidence: float
    status: str


@dataclass(frozen=True)
class VixContext:
    as_of: datetime
    last_price: float | None
    previous_close: float | None
    change_pct: float | None
    regime: str
    movement: str
    seller_environment: str
    status: str


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
    future_candles_1m: pd.DataFrame
    future_candles_3m: pd.DataFrame
    future_candles_15m: pd.DataFrame
    indicators: IndicatorBundle
    price_action: PriceActionBundle
    levels: LevelBundle
    volume: VolumeBundle
    core_evidence: CoreMarketEvidence
    option_intelligence: OptionIntelligence
    heavyweights: HeavyweightBundle
    vix_context: VixContext
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
            "future_candles_1m": int(len(self.future_candles_1m)),
            "future_candles_3m": int(len(self.future_candles_3m)),
            "future_candles_15m": int(len(self.future_candles_15m)),
            "indicators": {
                "3m": asdict(self.indicators.three_minute),
                "15m": asdict(self.indicators.fifteen_minute),
            },
            "price_action": asdict(self.price_action),
            "levels": asdict(self.levels),
            "volume": asdict(self.volume),
            "core_evidence": asdict(self.core_evidence),
            "option_intelligence": asdict(self.option_intelligence),
            "heavyweights": asdict(self.heavyweights),
            "vix_context": asdict(self.vix_context),
            "feeds": {
                name: asdict(status) for name, status in self.feed_status.items()
            },
        }
