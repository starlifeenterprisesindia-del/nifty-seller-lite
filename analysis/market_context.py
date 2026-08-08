from __future__ import annotations

from datetime import date
from typing import Any

from models import EventRiskContext, InstitutionalContext


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _window_sum(values: list[float | None], size: int) -> float | None:
    window = values[-size:]
    usable = [item for item in window if item is not None]
    if not usable:
        return None
    return round(sum(usable), 2)


def _window_avg(values: list[float | None], size: int) -> float | None:
    window = values[-size:]
    usable = [item for item in window if item is not None]
    if not usable:
        return None
    return round(sum(usable) / len(usable), 2)


def _futures_bias(long_pct: float | None, short_pct: float | None, legacy_net: float | None) -> str:
    if long_pct is not None or short_pct is not None:
        long_value = long_pct if long_pct is not None else 100.0 - float(short_pct or 0.0)
        short_value = short_pct if short_pct is not None else 100.0 - float(long_pct or 0.0)
        if short_value >= 65.0:
            return "STRONGLY SHORT"
        if long_value >= 65.0:
            return "STRONGLY LONG"
        if short_value >= 55.0:
            return "SHORT"
        if long_value >= 55.0:
            return "LONG"
        return "BALANCED"
    if legacy_net is not None:
        if legacy_net >= 1000:
            return "LONG"
        if legacy_net <= -1000:
            return "SHORT"
    return "UNAVAILABLE"


def calculate_market_context(
    entries: list[dict[str, Any]], current_date: date
) -> tuple[InstitutionalContext, EventRiskContext]:
    clean = [
        item
        for item in entries
        if isinstance(item, dict)
        and str(item.get("date", "")) <= current_date.isoformat()
    ]
    clean.sort(key=lambda item: str(item.get("date", "")))
    clean = clean[-15:]
    institutional_rows = [
        item
        for item in clean
        if any(
            _number(item.get(field)) is not None
            for field in (
                "fii_cash_net",
                "dii_cash_net",
                "fii_index_futures_net",
                "fii_index_futures_contracts",
                "fii_futures_long_pct",
                "fii_futures_short_pct",
            )
        )
    ]
    latest = institutional_rows[-1] if institutional_rows else {}
    event_latest = clean[-1] if clean else {}

    fii_values = [_number(item.get("fii_cash_net")) for item in clean]
    dii_values = [_number(item.get("dii_cash_net")) for item in clean]
    futures_values = [_number(item.get("fii_index_futures_net")) for item in clean]
    futures_long_values = [_number(item.get("fii_futures_long_pct")) for item in clean]
    futures_short_values = [_number(item.get("fii_futures_short_pct")) for item in clean]
    latest_fii = _number(latest.get("fii_cash_net"))
    latest_dii = _number(latest.get("dii_cash_net"))
    latest_futures = _number(latest.get("fii_index_futures_net"))
    latest_futures_contracts = _number(latest.get("fii_index_futures_contracts"))
    latest_futures_long = _number(latest.get("fii_futures_long_pct"))
    latest_futures_short = _number(latest.get("fii_futures_short_pct"))
    futures_bias = _futures_bias(latest_futures_long, latest_futures_short, latest_futures)
    observations = sum(
        1
        for item in clean
        if any(
            _number(item.get(field)) is not None
            for field in (
                "fii_cash_net", "dii_cash_net", "fii_index_futures_net",
                "fii_index_futures_contracts", "fii_futures_long_pct", "fii_futures_short_pct"
            )
        )
    )

    fii_5 = _window_sum(fii_values, 5)
    fii_10 = _window_sum(fii_values, 10)
    fii_15 = _window_sum(fii_values, 15)
    dii_5 = _window_sum(dii_values, 5)
    dii_10 = _window_sum(dii_values, 10)
    dii_15 = _window_sum(dii_values, 15)
    futures_5 = _window_sum(futures_values, 5)
    futures_10 = _window_sum(futures_values, 10)
    futures_15 = _window_sum(futures_values, 15)
    futures_long_5 = _window_avg(futures_long_values, 5)
    futures_short_5 = _window_avg(futures_short_values, 5)
    futures_long_10 = _window_avg(futures_long_values, 10)
    futures_short_10 = _window_avg(futures_short_values, 10)
    futures_long_15 = _window_avg(futures_long_values, 15)
    futures_short_15 = _window_avg(futures_short_values, 15)

    combined_latest = None
    if latest_fii is not None or latest_dii is not None:
        combined_latest = (latest_fii or 0.0) + (latest_dii or 0.0)
    combined_5 = None
    if fii_5 is not None or dii_5 is not None:
        combined_5 = (fii_5 or 0.0) + (dii_5 or 0.0)

    if observations == 0:
        state = "MISSING"
        confidence = 0.0
        status = "MISSING"
    else:
        if (combined_latest or 0.0) >= 1000 and (combined_5 or 0.0) >= 2000:
            state = "NET INSTITUTIONAL SUPPORT"
        elif (combined_latest or 0.0) <= -1000 and (combined_5 or 0.0) <= -2000:
            state = "NET INSTITUTIONAL PRESSURE"
        elif (
            latest_fii is not None and latest_fii < -1000 and (latest_dii or 0.0) > 1000
        ):
            state = "FII SELLING / DII ABSORPTION"
        elif (
            latest_fii is not None and latest_fii > 1000 and (latest_dii or 0.0) < -1000
        ):
            state = "FII BUYING / DII SELLING"
        elif futures_bias in {"LONG", "STRONGLY LONG"}:
            state = "MIXED CASH"
        elif futures_bias in {"SHORT", "STRONGLY SHORT"}:
            state = "MIXED CASH"
        else:
            state = "MIXED / NEUTRAL"
        if futures_bias != "UNAVAILABLE":
            state = f"{state} | FUTURES {futures_bias}"
        confidence = min(85.0, 35.0 + observations * 4.0)
        status = "READY" if observations >= 5 else "LIMITED HISTORY"

    institutional = InstitutionalContext(
        as_of_date=str(latest.get("date")) if latest else None,
        latest_fii_net=latest_fii,
        latest_dii_net=latest_dii,
        latest_fii_index_futures_net=latest_futures,
        fii_5d_net=fii_5,
        fii_10d_net=fii_10,
        fii_15d_net=fii_15,
        dii_5d_net=dii_5,
        dii_10d_net=dii_10,
        dii_15d_net=dii_15,
        fii_index_futures_5d_net=futures_5,
        fii_index_futures_10d_net=futures_10,
        fii_index_futures_15d_net=futures_15,
        observations=observations,
        state=state,
        confidence=round(confidence, 1),
        status=status,
        latest_fii_index_futures_contracts=latest_futures_contracts,
        latest_fii_futures_long_pct=latest_futures_long,
        latest_fii_futures_short_pct=latest_futures_short,
        fii_futures_bias=futures_bias,
        fii_futures_5d_long_avg_pct=futures_long_5,
        fii_futures_5d_short_avg_pct=futures_short_5,
        fii_futures_10d_long_avg_pct=futures_long_10,
        fii_futures_10d_short_avg_pct=futures_short_10,
        fii_futures_15d_long_avg_pct=futures_long_15,
        fii_futures_15d_short_avg_pct=futures_short_15,
    )

    level = (
        str(event_latest.get("event_risk") or "NONE").upper()
        if event_latest
        else "NONE"
    )
    verified = bool(event_latest.get("verified")) if event_latest else False
    note = str(event_latest.get("event_note") or "") if event_latest else ""
    if level not in {"NONE", "LOW", "MEDIUM", "HIGH"}:
        level = "NONE"
    if not event_latest:
        event_status = "NOT PROVIDED"
    elif not verified:
        event_status = "UNVERIFIED — IGNORED"
        level = "NONE"
    elif event_latest:
        event_status = "READY"
    event = EventRiskContext(
        as_of_date=str(event_latest.get("date")) if event_latest else None,
        level=level,
        note=note,
        verified=verified,
        status=event_status,
    )
    return institutional, event
