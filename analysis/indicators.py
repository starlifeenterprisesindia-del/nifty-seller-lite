from __future__ import annotations

import pandas as pd

from config import CONFIG
from models import IndicatorBundle, TimeframeIndicators


def _empty(timeframe: str, status: str) -> TimeframeIndicators:
    return TimeframeIndicators(
        timeframe=timeframe,
        as_of=None,
        close=None,
        ema20=None,
        ema50=None,
        ema_state="UNAVAILABLE",
        macd=None,
        macd_signal=None,
        macd_histogram=None,
        macd_state="UNAVAILABLE",
        rsi14=None,
        rsi_state="UNAVAILABLE",
        status=status,
    )


def _rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    average_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    average_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    relative_strength = average_gain / average_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + relative_strength))
    no_loss = average_loss.eq(0) & average_gain.gt(0)
    no_gain = average_gain.eq(0) & average_loss.gt(0)
    flat = average_gain.eq(0) & average_loss.eq(0)
    return rsi.mask(no_loss, 100.0).mask(no_gain, 0.0).mask(flat, 50.0)


def _ema_state(close: float, ema20: float, ema50: float) -> str:
    if close > ema20 > ema50:
        return "BULLISH ALIGNED"
    if close < ema20 < ema50:
        return "BEARISH ALIGNED"
    if ema20 > ema50:
        return "BULLISH STRUCTURE / PRICE MIXED"
    if ema20 < ema50:
        return "BEARISH STRUCTURE / PRICE MIXED"
    return "FLAT / MIXED"


def _macd_state(macd: float, signal: float, histogram: float) -> str:
    if macd > signal and histogram > 0:
        return "BULLISH"
    if macd < signal and histogram < 0:
        return "BEARISH"
    if histogram > 0:
        return "BULLISH BUT WEAKENING"
    if histogram < 0:
        return "BEARISH BUT WEAKENING"
    return "FLAT"


def _rsi_state(value: float) -> str:
    if value >= 70:
        return "BULLISH / OVEREXTENDED CAUTION"
    if value >= 55:
        return "BULLISH STRENGTH"
    if value > 45:
        return "NEUTRAL"
    if value > 30:
        return "BEARISH STRENGTH"
    return "BEARISH / OVERSOLD CAUTION"


def calculate_timeframe_indicators(
    frame: pd.DataFrame,
    timeframe: str,
) -> TimeframeIndicators:
    if frame.empty or "close" not in frame:
        return _empty(timeframe, "NO CANDLES")

    source = frame.copy()
    if "is_complete" in source:
        source = source[source["is_complete"].fillna(False)]
    source = source.dropna(subset=["timestamp", "close"]).sort_values("timestamp")
    if len(source) < CONFIG.minimum_indicator_candles:
        return _empty(
            timeframe,
            f"INSUFFICIENT COMPLETED CANDLES ({len(source)}/{CONFIG.minimum_indicator_candles})",
        )

    close = pd.to_numeric(source["close"], errors="coerce").dropna()
    if len(close) < CONFIG.minimum_indicator_candles:
        return _empty(timeframe, "INSUFFICIENT VALID CLOSE VALUES")

    ema20_series = close.ewm(span=20, adjust=False).mean()
    ema50_series = close.ewm(span=50, adjust=False).mean()
    macd_series = (
        close.ewm(span=12, adjust=False).mean()
        - close.ewm(
            span=26,
            adjust=False,
        ).mean()
    )
    signal_series = macd_series.ewm(span=9, adjust=False).mean()
    histogram_series = macd_series - signal_series
    rsi_series = _rsi_wilder(close, 14)

    values = {
        "close": float(close.iloc[-1]),
        "ema20": float(ema20_series.iloc[-1]),
        "ema50": float(ema50_series.iloc[-1]),
        "macd": float(macd_series.iloc[-1]),
        "signal": float(signal_series.iloc[-1]),
        "histogram": float(histogram_series.iloc[-1]),
        "rsi": float(rsi_series.iloc[-1]),
    }
    as_of_raw = source.iloc[-1]["timestamp"]
    as_of = pd.Timestamp(as_of_raw).to_pydatetime()
    return TimeframeIndicators(
        timeframe=timeframe,
        as_of=as_of,
        close=values["close"],
        ema20=values["ema20"],
        ema50=values["ema50"],
        ema_state=_ema_state(values["close"], values["ema20"], values["ema50"]),
        macd=values["macd"],
        macd_signal=values["signal"],
        macd_histogram=values["histogram"],
        macd_state=_macd_state(values["macd"], values["signal"], values["histogram"]),
        rsi14=values["rsi"],
        rsi_state=_rsi_state(values["rsi"]),
        status="READY",
    )


def calculate_indicator_bundle(
    candles_3m: pd.DataFrame,
    candles_15m: pd.DataFrame,
) -> IndicatorBundle:
    return IndicatorBundle(
        three_minute=calculate_timeframe_indicators(candles_3m, "3 Minute"),
        fifteen_minute=calculate_timeframe_indicators(candles_15m, "15 Minute"),
    )
