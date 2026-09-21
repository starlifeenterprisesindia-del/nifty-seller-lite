"""Read-only market observations; never shares trade/confirmation state."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def bounded(rows, now, key, expiry=None):
    result = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            stamp = datetime.fromisoformat(row[key])
            if stamp.tzinfo is None or now.tzinfo is None:
                continue
            age = (now - stamp).total_seconds()
            if not 0 < age <= 1800 or stamp.astimezone(IST).date() != now.astimezone(IST).date():
                continue
            if expiry is not None and row.get("expiry") != expiry:
                continue
            result[stamp.isoformat()] = row
        except (ValueError, TypeError, KeyError):
            continue
    return sorted(result.values(), key=lambda row: datetime.fromisoformat(row[key]))[-400:]


def read_history(root, now, expiry):
    def read(name, fallback):
        try:
            return json.loads((root / name).read_text())
        except (OSError, ValueError):
            return fallback
    options = read("options.json", {})
    sessions = options.get("sessions", {}) if isinstance(options, dict) else {}
    if not isinstance(sessions, dict):
        sessions = {}
    rows = sessions.get(f"{now.astimezone(IST).date().isoformat()}|{expiry}", [])
    return {"options": bounded(rows, now, "captured_at", expiry),
            "top9": bounded(read("top9.json", []), now, "at")}



def append_observation(root, now, payload):
    """Persist compact foreground option/Top-9 history without another Dhan call.

    This is intentionally small: the Streamlit snapshot already paid for the market
    data, so Railway only stores the validated comparison fields. It keeps restart
    continuity while avoiding a second heavy background snapshot pipeline.
    """
    import os
    from pathlib import Path

    if not isinstance(payload, dict):
        return {"option_stored": False, "top9_stored": False}
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    option_stored = False
    top9_stored = False

    option_snapshot = payload.get("option_snapshot")
    if isinstance(option_snapshot, dict):
        try:
            stamp = datetime.fromisoformat(str(option_snapshot.get("captured_at") or ""))
            expiry = str(option_snapshot.get("expiry") or "")
            rows = option_snapshot.get("rows")
            fingerprint = str(option_snapshot.get("fingerprint") or "")
            if (
                stamp.tzinfo is not None
                and expiry
                and isinstance(rows, list)
                and fingerprint
                and 0 <= (now - stamp).total_seconds() <= 180
            ):
                path = root / "options.json"
                lock_path = root / "options.json.lock"
                # Linux Railway uses fcntl; fallback remains atomic via os.replace.
                try:
                    import fcntl
                except ImportError:  # pragma: no cover
                    fcntl = None
                with lock_path.open("a+", encoding="utf-8") as handle:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    try:
                        try:
                            state = json.loads(path.read_text())
                        except (OSError, ValueError):
                            state = {"schema_version": 1, "sessions": {}}
                        if not isinstance(state, dict) or state.get("schema_version") != 1:
                            state = {"schema_version": 1, "sessions": {}}
                        sessions = state.get("sessions")
                        if not isinstance(sessions, dict):
                            sessions = {}
                        date_prefix = stamp.date().isoformat() + "|"
                        sessions = {k: v for k, v in sessions.items() if str(k).startswith(date_prefix)}
                        key = f"{stamp.date().isoformat()}|{expiry}"
                        history = sessions.setdefault(key, [])
                        if not isinstance(history, list):
                            history = []
                            sessions[key] = history
                        append = True
                        if history:
                            previous = history[-1]
                            try:
                                age = max(0.0, (stamp - datetime.fromisoformat(str(previous.get("captured_at")))).total_seconds())
                            except (ValueError, TypeError):
                                age = 999.0
                            if previous.get("fingerprint") == fingerprint and age < 20:
                                append = False
                        if append:
                            history.append(option_snapshot)
                            del history[:-180]
                            state["sessions"] = sessions
                            temp = path.with_suffix(".json.tmp")
                            temp.write_text(json.dumps(state, separators=(",", ":"), sort_keys=True))
                            os.replace(temp, path)
                            option_stored = True
                    finally:
                        if fcntl is not None:
                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (ValueError, TypeError, KeyError, OSError):
            option_stored = False

    top9 = payload.get("top9")
    if isinstance(top9, dict):
        try:
            stamp = datetime.fromisoformat(str(top9.get("at") or ""))
            if stamp.tzinfo is not None and 0 <= (now - stamp).total_seconds() <= 180:
                path = root / "top9.json"
                try:
                    rows = json.loads(path.read_text())
                except (OSError, ValueError):
                    rows = []
                rows = bounded(rows if isinstance(rows, list) else [], now, "at")
                if not rows or (stamp - datetime.fromisoformat(rows[-1]["at"])).total_seconds() >= 5:
                    clean_prices = {}
                    for key, value in (top9.get("prices") or {}).items():
                        try:
                            clean_prices[str(key)] = float(value)
                        except (TypeError, ValueError):
                            continue
                    row = {
                        "at": stamp.isoformat(),
                        "nifty": float(top9["nifty"]) if top9.get("nifty") is not None else None,
                        "universe": sorted(clean_prices),
                        "prices": clean_prices,
                    }
                    rows.append(row)
                    temp = path.with_suffix(".json.tmp")
                    temp.write_text(json.dumps(rows[-400:], separators=(",", ":")))
                    os.replace(temp, path)
                    top9_stored = True
        except (ValueError, TypeError, KeyError, OSError):
            top9_stored = False
    return {"option_stored": option_stored, "top9_stored": top9_stored}
