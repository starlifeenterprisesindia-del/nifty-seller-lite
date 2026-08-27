"""Read-only observed history. No strategy votes, orders or inferred trader counts."""
from datetime import datetime, timedelta
import math
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def stamp(value):
    try:
        at = datetime.fromisoformat(str(value))
        return at.astimezone(IST) if at.tzinfo else None
    except (ValueError, TypeError):
        return None


def live(row, *names):
    return all((row.get("feeds", {}).get(name) or {}).get("use_state") == "LIVE" for name in names)


def recent_history(snapshot, report):
    """Use distinct same-day/expiry/version observations; report measured windows."""
    result = {"status": "PENDING", "extra_weight": 0, "windows": [], "barriers": [],
              "message": "Fresh matching records ka wait; final AI par extra vote 0."}
    if not snapshot.market_session.is_live:
        return {**result, "status": "REFERENCE", "message": "Market closed — live comparison nahi. Saved events neeche reference ke liye hain."}
    if not report or report.get("last_error"):
        return {**result, "status": "UNAVAILABLE", "message": "History unavailable/data gap — fresh comparison nahi."}
    now = stamp(snapshot.created_at)
    if now is None:
        return result
    rows = {}
    for row in report.get("recent_context", []):
        at = stamp(row.get("at"))
        if (at is not None and at.date() == now.date() and 0 <= (now-at).total_seconds() <= 1080
                and row.get("expiry") == snapshot.expiry
                and row.get("version") == snapshot.metadata.get("version")
                and live(row, "quotes", "candles") and number(row.get("spot")) is not None):
            # A repeated minute never becomes a new confirmation.
            minute = at.replace(second=0, microsecond=0)
            if minute not in rows or at < rows[minute][0]:
                rows[minute] = (at, row)
    rows = sorted(rows.values(), key=lambda pair: pair[0])
    if not rows or (now-rows[-1][0]).total_seconds() > 120:
        return result
    end, last = rows[-1]
    result.update(status="READY", message=f"Last record {end:%H:%M:%S} IST · background evidence, trader count nahi.")
    for minutes in (5, 15):
        target = end-timedelta(minutes=minutes)
        starts = [i for i, (at, _) in enumerate(rows) if 0 <= (target-at).total_seconds() <= 90]
        if not starts:
            result["windows"].append({"Window": f"{minutes}m", "Price reaction": "History abhi kam hai", "Flow": "PENDING"})
            continue
        segment = rows[max(starts):]
        if any((b[0]-a[0]).total_seconds() > 120 for a, b in zip(segment, segment[1:])):
            result["windows"].append({"Window": f"{minutes}m", "Price reaction": "Data gap — comparison blocked", "Flow": "UNAVAILABLE"})
            continue
        begin, first = segment[0]
        change = number(last["spot"])-number(first["spot"])
        # 4 points is a display deadband, not an entry/exit threshold.
        reaction = "Price upar" if change >= 4 else "Price neeche" if change <= -4 else "Price lagbhag flat"
        direction = snapshot.decision.market_direction
        if change >= 4 and direction == "BEARISH":
            reaction = "Bearish direction ke andar recovery; reversal prove nahi"
        elif change <= -4 and direction == "BULLISH":
            reaction = "Bullish direction ke andar pullback; reversal prove nahi"
        flow = "Flow history unavailable / futures contract changed"
        contract = first.get("future_contract") or {}
        valid_flow = bool(contract.get("security_id") and contract.get("expiry")) and all(
            r.get("future_contract") == contract and live(r, "future_volume", "option_chain")
            and not (r.get("activity") or {}).get("frozen_after_close")
            and all(number((r.get("activity") or {}).get(k)) is not None for k in ("buy_score", "sell_score"))
            for _, r in segment)
        if valid_flow:
            old, new = first["activity"], last["activity"]
            buy, sell = number(new["buy_score"]), number(new["sell_score"])
            flow = (f"Buying {buy:.0f}/100 ({buy-number(old['buy_score']):+.0f}); "
                    f"Selling {sell:.0f}/100 ({sell-number(old['sell_score']):+.0f})")
            if sell-buy >= 10 and change > -4:
                reaction += " · Selling evidence ko price fall ka saath nahi; absorption confirmed nahi"
            elif buy-sell >= 10 and change < 4:
                reaction += " · Buying evidence ko price rise ka saath nahi; reversal confirmed nahi"
        result["windows"].append({"Window": f"{minutes}m", "Observed": f"{begin:%H:%M:%S}–{end:%H:%M:%S} IST",
                                  "Nifty change": round(change, 2), "Price reaction": reaction, "Flow": flow})
    # Only latest recorder zones; explicitly not a separate/current AI barrier map.
    for name in ("nearest_resistance", "nearest_support"):
        zone = (last.get("barriers") or {}).get(name)
        if not zone:
            continue
        lo, hi = number(zone.get("lower")), number(zone.get("upper"))
        if lo is None or hi is None or lo > hi:
            continue
        label = f"{lo:,.0f}–{hi:,.0f}"
        key = f"{zone.get('side')}:{zone.get('lower')}:{zone.get('upper')}"
        events = []
        for event in report.get("events", []):
            at = stamp(event.get("at"))
            if (event.get("kind") == "3m REACTION" and event.get("identity") == key
                    and event.get("expiry") == snapshot.expiry
                    and event.get("version") == snapshot.metadata.get("version")
                    and at is not None and at.date() == now.date() and at <= end):
                events.append((at, event))
        events.sort(key=lambda pair: pair[0])
        position = "Zone ke upar" if number(last["spot"]) > hi else "Zone ke neeche" if number(last["spot"]) < lo else "Zone ke andar"
        latest = f"{events[-1][0]:%H:%M:%S} IST: {events[-1][1].get('status')}" if events else "Matching recorded reaction nahi mila"
        result["barriers"].append({"Level": f"{zone.get('side')} {label}", "Last recorded price": position,
                                   "Latest recorded reaction": latest})
    return result
