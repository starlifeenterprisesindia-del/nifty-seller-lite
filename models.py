from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time
from typing import Any

import pandas as pd


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat(timespec="minutes")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


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


@dataclass(frozen=True)
class InstitutionalContext:
    as_of_date: str | None
    latest_fii_net: float | None
    latest_dii_net: float | None
    latest_fii_index_futures_net: float | None
    fii_5d_net: float | None
    fii_10d_net: float | None
    fii_15d_net: float | None
    dii_5d_net: float | None
    dii_10d_net: float | None
    dii_15d_net: float | None
    fii_index_futures_5d_net: float | None
    fii_index_futures_10d_net: float | None
    fii_index_futures_15d_net: float | None
    observations: int
    state: str
    confidence: float
    status: str


@dataclass(frozen=True)
class EventRiskContext:
    as_of_date: str | None
    level: str
    note: str
    verified: bool
    status: str


@dataclass(frozen=True)
class StrategyEvaluation:
    name: str
    score: float
    status: str
    reasons: tuple[str, ...]
    cautions: tuple[str, ...]


@dataclass(frozen=True)
class MarketOutlook:
    bullish_path_pct: float
    range_path_pct: float
    bearish_path_pct: float
    fake_move_risk: float
    fake_move_state: str
    signal_state: str
    signal_memory: str
    invalidation_low: float | None
    invalidation_high: float | None
    invalidation_text: str
    reasons: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class FinalDecision:
    ce_sell: StrategyEvaluation
    pe_sell: StrategyEvaluation
    iron_condor: StrategyEvaluation
    wait_need: StrategyEvaluation
    final_action: str
    execution_status: str
    decision_confidence: float
    hedge_required: bool
    reasons: tuple[str, ...]
    blocker: str
    status: str
    instant_action: str = ""
    signal_state: str = "UNAVAILABLE"
    market_direction: str = "RANGE"
    outlook: MarketOutlook = field(
        default_factory=lambda: MarketOutlook(
            bullish_path_pct=33.4,
            range_path_pct=33.3,
            bearish_path_pct=33.3,
            fake_move_risk=100.0,
            fake_move_state="HIGH",
            signal_state="UNAVAILABLE",
            signal_memory="0/0",
            invalidation_low=None,
            invalidation_high=None,
            invalidation_text="Unavailable",
            reasons=(),
            status="UNAVAILABLE",
        )
    )


@dataclass(frozen=True)
class OptionLeg:
    role: str
    side: str
    strike: float
    last_price: float | None
    delta: float | None
    oi: float | None
    volume: float | None
    bid: float | None
    ask: float | None
    spread_pct: float | None
    distance_points: float
    liquidity_score: float
    status: str


@dataclass(frozen=True)
class SetupPlan:
    name: str
    short_legs: tuple[OptionLeg, ...]
    hedge_legs: tuple[OptionLeg, ...]
    estimated_credit_points: float | None
    width_points: float | None
    max_risk_points: float | None
    lower_breakeven: float | None
    upper_breakeven: float | None
    quality_score: float
    status: str
    reasons: tuple[str, ...]
    blocker: str

    @property
    def available(self) -> bool:
        return bool(self.short_legs and self.hedge_legs)

    @classmethod
    def unavailable(cls, name: str, blocker: str) -> "SetupPlan":
        return cls(
            name=name,
            short_legs=(),
            hedge_legs=(),
            estimated_credit_points=None,
            width_points=None,
            max_risk_points=None,
            lower_breakeven=None,
            upper_breakeven=None,
            quality_score=0.0,
            status="UNAVAILABLE",
            reasons=(),
            blocker=blocker,
        )


@dataclass(frozen=True)
class TradePlanBundle:
    as_of: datetime
    expiry: str | None
    spot: float | None
    ce_sell: SetupPlan
    pe_sell: SetupPlan
    iron_condor: SetupPlan
    selected_setup: str
    status: str
    blocker: str


@dataclass(frozen=True)
class RiskProfile:
    capital_rupees: float
    risk_pct: float
    lot_size: int
    max_lots_cap: int
    target_capture_pct: float
    stop_loss_pct: float
    entry_start: time
    entry_end: time
    forced_exit: time

    @property
    def risk_budget_rupees(self) -> float:
        return round(max(0.0, self.capital_rupees) * max(0.0, self.risk_pct) / 100.0, 2)


@dataclass(frozen=True)
class DisciplineState:
    session_date: str
    trades_taken: int
    day_locked: bool
    last_outcome: str
    last_action: str
    signal_history: tuple[dict[str, Any], ...]
    status: str
    trade_record: dict[str, Any] | None = None


@dataclass(frozen=True)
class ExecutionGuard:
    as_of: datetime
    selected_setup: str
    readiness: str
    signal_state: str
    confirmations: int
    required_confirmations: int
    entry_window: str
    risk_budget_rupees: float
    risk_per_lot_rupees: float | None
    allowed_lots: int
    max_lots_by_budget: int
    max_lots_cap: int
    target_capture_points: float | None
    target_exit_debit_points: float | None
    target_profit_rupees: float | None
    stop_loss_points: float | None
    stop_exit_debit_points: float | None
    stop_loss_rupees: float | None
    forced_exit_time: str
    spot_invalidation_low: float | None
    spot_invalidation_high: float | None
    trade_taken_today: bool
    day_locked: bool
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class PositionLegMonitor:
    role: str
    side: str
    strike: float
    entry_price: float
    current_price: float | None
    pnl_contribution_points: float | None
    status: str


@dataclass(frozen=True)
class PositionGuardian:
    as_of: datetime
    action: str
    expiry: str | None
    opened_at: str
    lots: int
    lot_size: int
    entry_spot: float | None
    current_spot: float | None
    entry_credit_points: float | None
    current_debit_points: float | None
    unrealized_pnl_points: float | None
    unrealized_pnl_rupees: float | None
    target_exit_debit_points: float | None
    stop_exit_debit_points: float | None
    target_progress_pct: float | None
    forced_exit_time: str
    spot_invalidation_low: float | None
    spot_invalidation_high: float | None
    instruction: str
    legs: tuple[PositionLegMonitor, ...]
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    status: str

    @classmethod
    def idle(cls, *, as_of: datetime, current_spot: float | None) -> "PositionGuardian":
        return cls(
            as_of=as_of,
            action="",
            expiry=None,
            opened_at="",
            lots=0,
            lot_size=0,
            entry_spot=None,
            current_spot=current_spot,
            entry_credit_points=None,
            current_debit_points=None,
            unrealized_pnl_points=None,
            unrealized_pnl_rupees=None,
            target_exit_debit_points=None,
            stop_exit_debit_points=None,
            target_progress_pct=None,
            forced_exit_time="",
            spot_invalidation_low=None,
            spot_invalidation_high=None,
            instruction="NO OPEN TRADE",
            legs=(),
            reasons=("No trade is marked open in the one-trade journal",),
            blockers=(),
            status="IDLE",
        )


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
    institutional_context: InstitutionalContext
    event_risk: EventRiskContext
    decision: FinalDecision
    trade_plan: TradePlanBundle
    execution_guard: ExecutionGuard
    position_guardian: PositionGuardian
    risk_profile: RiskProfile
    discipline_state: DisciplineState
    expiry: str | None
    option_chain: pd.DataFrame
    feed_status: dict[str, FeedStatus]
    metadata: dict[str, Any] = field(default_factory=dict)

    def public_summary(self) -> dict[str, Any]:
        return _json_safe(
            {
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
                "institutional_context": asdict(self.institutional_context),
                "event_risk": asdict(self.event_risk),
                "decision": asdict(self.decision),
                "trade_plan": asdict(self.trade_plan),
                "execution_guard": asdict(self.execution_guard),
                "position_guardian": asdict(self.position_guardian),
                "risk_profile": {
                    **asdict(self.risk_profile),
                    "entry_start": self.risk_profile.entry_start.isoformat(
                        timespec="minutes"
                    ),
                    "entry_end": self.risk_profile.entry_end.isoformat(
                        timespec="minutes"
                    ),
                    "forced_exit": self.risk_profile.forced_exit.isoformat(
                        timespec="minutes"
                    ),
                },
                "discipline_state": asdict(self.discipline_state),
                "feeds": {
                    name: asdict(status) for name, status in self.feed_status.items()
                },
            }
        )
