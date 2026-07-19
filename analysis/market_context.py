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
    latest = clean[-1] if clean else {}

    fii_values = [_number(item.get("fii_cash_net")) for item in clean]
    dii_values = [_number(item.get("dii_cash_net")) for item in clean]
    futures_values = [_number(item.get("fii_index_futures_net")) for item in clean]
    latest_fii = _number(latest.get("fii_cash_net"))
    latest_dii = _number(latest.get("dii_cash_net"))
    latest_futures = _number(latest.get("fii_index_futures_net"))
    observations = sum(
        1
        for fii, dii, futures in zip(
            fii_values, dii_values, futures_values, strict=False
        )
        if fii is not None or dii is not None or futures is not None
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
        elif latest_futures is not None and latest_futures >= 1000:
            state = "MIXED CASH / FII FUTURES LONG"
        elif latest_futures is not None and latest_futures <= -1000:
            state = "MIXED CASH / FII FUTURES SHORT"
        else:
            state = "MIXED / NEUTRAL"
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
    )

    level = str(latest.get("event_risk") or "NONE").upper() if latest else "NONE"
    verified = bool(latest.get("verified")) if latest else False
    note = str(latest.get("event_note") or "") if latest else ""
    if level not in {"NONE", "LOW", "MEDIUM", "HIGH"}:
        level = "NONE"
    if level in {"MEDIUM", "HIGH"} and not verified:
        event_status = "UNVERIFIED — IGNORED"
        level = "NONE"
    elif latest:
        event_status = "READY"
    else:
        event_status = "NOT PROVIDED"
    event = EventRiskContext(
        as_of_date=str(latest.get("date")) if latest else None,
        level=level,
        note=note,
        verified=verified,
        status=event_status,
    )
    return institutional, event
