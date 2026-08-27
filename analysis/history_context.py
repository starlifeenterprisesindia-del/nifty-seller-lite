"""History explains the current decision; it never votes or overrides a guard."""
from datetime import datetime


def history_context(snapshot, report):
    result = {"status": "UNAVAILABLE", "extra_weight": 0, "lines": []}
    if not report or report.get("last_error"):
        result["lines"] = ["History unavailable/data gap — current evidence se decision."]
        return result
    now = snapshot.created_at
    if not snapshot.market_session.is_live:
        return {**result, "status": "REFERENCE", "lines": ["Market closed: saved history reference-only hai."]}
    rows = []
    for row in report.get("recent_context", []):
        try:
            at = datetime.fromisoformat(row["at"])
            age = (now-at).total_seconds()
            if (at.date() == now.date() and 0 <= age <= 900 and row.get("expiry") == snapshot.expiry
                    and row.get("version") == snapshot.metadata.get("version")):
                rows.append((at,row))
        except (ValueError, TypeError, KeyError):
            continue
    rows.sort(key=lambda x:x[0])
    if not rows or (now-rows[-1][0]).total_seconds() > 120:
        result["lines"] = ["Fresh matching history pending — purane cycle/day ko live confirmation nahi maana."]
        return result
    if any((b[0]-a[0]).total_seconds()>120 for a,b in zip(rows,rows[1:])):
        return {**result, "status": "GAPS", "lines": ["Recent history mein gap; sustained move confirm nahi."]}
    direction = snapshot.decision.market_direction
    tail = []
    for at,row in reversed(rows):
        if row.get("direction") != direction:
            break
        tail.append((at,row))
    if len(tail)>=3 and (tail[0][0]-tail[-1][0]).total_seconds()>=120:
        minutes = (tail[0][0]-tail[-1][0]).total_seconds()/60
        line = f"Background reference {minutes:.0f} minute se {direction}; current AI direction bhi same. Ye extra confirmation vote nahi."
    else:
        line = "Recent direction mixed/short-lived hai; abhi sustained agreement ka evidence kam."
    result.update(status="READY", lines=[line])
    current_flow = (rows[-1][1].get("activity") or {}).get("direction")
    if (direction == "BEARISH" and current_flow == "BUYING") or (direction == "BULLISH" and current_flow == "SELLING"):
        result["lines"].append("Recent flow main direction ke opposite hai: recovery/pullback possible, trend reversal abhi prove nahi.")
    return result
