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
    weight_pct: float = 0.0


@dataclass(frozen=True)
class AppConfig:
    app_name: str = "Nifty Seller Lite"
    version: str = "2.7.0_INSTITUTIONAL_JOURNAL_INTEGRITY"
    request_timeout_seconds: int = 12
    snapshot_min_refresh_seconds: int = 5
    quote_max_age_seconds: int = 12
    context_quote_max_age_seconds: int = 60
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
        security_id="33",
        exchange_segment="IDX_I",
        instrument="INDEX",
        symbol="INDIA VIX",
    )

    # Official NIFTY 50 top-seven weights as at 30-Jun-2026. Security IDs are
    # Dhan/NSE instrument identifiers used by the already grouped quote request.
    top7_weight_date: str = "2026-06-30"
    top7: tuple[InstrumentRef, ...] = (
        InstrumentRef("HDFC Bank", "1333", "NSE_EQ", "EQUITY", "HDFCBANK", 11.18),
        InstrumentRef("ICICI Bank", "4963", "NSE_EQ", "EQUITY", "ICICIBANK", 9.01),
        InstrumentRef(
            "Reliance Industries", "2885", "NSE_EQ", "EQUITY", "RELIANCE", 8.00
        ),
        InstrumentRef("Bharti Airtel", "10604", "NSE_EQ", "EQUITY", "BHARTIARTL", 5.15),
        InstrumentRef("Larsen & Toubro", "11483", "NSE_EQ", "EQUITY", "LT", 4.44),
        InstrumentRef("State Bank of India", "3045", "NSE_EQ", "EQUITY", "SBIN", 3.88),
        InstrumentRef("Axis Bank", "5900", "NSE_EQ", "EQUITY", "AXISBANK", 3.54),
    )
    option_strikes_each_side: int = 7
    candle_lookback_days: int = 7
    minimum_one_minute_candles: int = 30
    minimum_indicator_candles: int = 50

    # Core-market settings.
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

    # Options-intelligence settings. History is bounded and same-session only.
    option_state_path: str = "data/option_state.json"
    option_state_max_snapshots: int = 180
    option_state_dedupe_seconds: int = 20
    option_min_price_move_pct: float = 0.50
    option_min_price_move_points: float = 0.25
    option_min_oi_move_pct: float = 0.20
    option_window_tolerance_ratio: float = 0.55
    option_persistence_lookback: int = 5
    option_max_comparison_age_seconds: int = 600
    option_min_integrity_rows: int = 6
    option_spot_max_divergence_pct: float = 0.35
    option_spot_max_divergence_points: float = 50.0

    # Instrument master cache is refreshed daily; a stale cache remains a safe
    # fallback when the remote master is temporarily unavailable.
    instrument_master_cache_max_age_hours: int = 24

    # Durable background context and final one-brain decision settings.
    market_context_path: str = "data/market_context.json"
    market_context_max_entries: int = 15
    decision_minimum_score: float = 62.0
    decision_minimum_margin: float = 8.0
    decision_wait_block_threshold: float = 60.0
    decision_min_option_confidence: float = 58.0
    decision_min_core_confidence: float = 55.0

    # Same-brain market memory, anti-flip confirmation and fake-move risk.
    # These settings do not create a second strategy engine; they gate the output
    # of calculate_final_decision using bounded same-session evidence memory.
    decision_memory_lookback: int = 5
    decision_memory_max_age_seconds: int = 900
    decision_confirmation_snapshots: int = 2
    decision_flip_confirmations: int = 2
    decision_flip_margin: float = 15.0
    decision_emergency_flip_margin: float = 35.0
    decision_stability_wait_floor: float = 65.0
    fake_move_medium_threshold: float = 40.0
    fake_move_high_threshold: float = 65.0
    outlook_current_weight: float = 0.65

    # Read-only strike-planner settings. This planner consumes the final one-brain
    # action and cannot change strategy scores or place orders.
    trade_target_abs_delta: float = 0.20
    trade_min_abs_delta: float = 0.08
    trade_max_abs_delta: float = 0.38
    trade_target_distance_pct: float = 1.0
    trade_distance_tolerance_pct: float = 0.9
    trade_hedge_steps: int = 2
    trade_min_option_premium: float = 3.0
    trade_min_credit_points: float = 1.0
    trade_min_plan_quality: float = 55.0
    trade_level_clearance_points: float = 10.0

    # Read-only execution guard and one-trade discipline. The guard consumes the
    # final decision and protected strike plan; it cannot choose a strategy or place
    # orders. Defaults reflect a conservative seller workflow and stay editable in UI.
    discipline_state_path: str = "data/discipline_state.json"
    discipline_state_max_signals: int = 60
    discipline_signal_dedupe_seconds: int = 20
    discipline_signal_max_gap_seconds: int = 420
    execution_required_confirmations: int = 2
    risk_default_capital: float = 250000.0
    risk_default_pct: float = 0.5
    risk_default_lot_size: int = 65
    risk_default_max_lots: int = 1
    risk_default_target_capture_pct: float = 35.0
    risk_default_stop_loss_pct: float = 40.0
    risk_default_entry_start: time = time(10, 15)
    risk_default_entry_end: time = time(11, 30)
    risk_default_forced_exit: time = time(14, 30)

    # Manual post-entry position guardian. Alerts are deterministic and read-only.
    position_profit_protect_pct: float = 70.0
    position_risk_warning_pct: float = -70.0

    @property
    def top7_symbols(self) -> tuple[str, ...]:
        return tuple(item.symbol for item in self.top7)

    @property
    def top7_weight_map(self) -> dict[str, float]:
        return {item.symbol: item.weight_pct for item in self.top7}


CONFIG = AppConfig()
