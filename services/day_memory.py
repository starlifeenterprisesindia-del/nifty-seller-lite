"""Expiry-cycle evidence archive. No orders, alerts, or strategy votes."""
from __future__ import annotations

import json
import math
import sqlite3
import gzip
import os
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from config import CONFIG

IST = ZoneInfo("Asia/Kolkata")


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        return clean(value.item())
    return value


def encode(value):
    return json.dumps(clean(value), separators=(",", ":"), allow_nan=False)


def recording_time(now):
    now = now.astimezone(IST)
    return now.weekday() < 5 and time(9, 15) <= now.time().replace(tzinfo=None) <= time(15, 31)


def candle_reaction(level, candle):
    """A retest requires a previously observed break, never a first-touch inference."""
    lo, hi = level["lower"], level["upper"]
    close, high, low = (float(candle[k]) for k in ("close", "high", "low"))
    resistance = level["side"] == "RESISTANCE"
    crossed = close > hi if resistance else close < lo
    touched = low <= hi and high >= lo
    rejected = touched and (close < lo if resistance else close > hi)
    broken = level.get("broken", False)
    if crossed:
        return ("RETEST HOLD — 3m CLOSE" if broken and touched else "BREAK KE BAAD HOLD" if broken else "BREAK — 3m CLOSE"), True
    if broken and (close < lo if resistance else close > hi):
        return "BREAK FAILED — 3m CLOSE", False
    return ("REJECTION — 3m CLOSE" if rejected else "TESTING" if touched else "LEVEL SE DOOR"), broken


def compact(snapshot, tracked_strikes=()):
    """Keep contracts within 500 points plus no credentials/raw HTTP payloads."""
    summary = snapshot.public_summary()
    spot = summary.get("nifty_last_price")
    frame = snapshot.option_chain
    if getattr(snapshot.feed_status.get("option_chain"), "use_state", "") != "LIVE":
        frame = frame.iloc[:0]
    if spot is not None and "strike" in frame:
        relevant = (frame["strike"] - float(spot)).abs() <= 500
        relevant |= frame["strike"].isin(tracked_strikes)
        for name in ("nearest_resistance", "next_resistance", "nearest_support", "next_support"):
            zone = summary["barrier_map"].get(name)
            if zone:
                relevant |= (frame["strike"] - (zone["lower"] + zone["upper"]) / 2).abs() <= 50
        for name in ("ce_sell", "pe_sell", "iron_condor", "ce_buy", "pe_buy"):
            plan = summary.get("trade_plan", {}).get(name, {})
            for leg in list(plan.get("short_legs", [])) + list(plan.get("hedge_legs", [])) + list(plan.get("long_legs", [])):
                relevant |= frame["strike"] == leg.get("strike")
        frame = frame[relevant]
    fields = [x for x in ("security_id", "strike", "side", "last_price", "oi", "volume",
                          "implied_volatility", "top_bid_price", "top_ask_price",
                          "delta", "gamma", "theta", "vega", "greeks_quality", "greeks_reason",
                          "previous_oi", "previous_volume", "previous_close_price",
                          "source_implied_volatility", "source_delta", "source_gamma", "source_theta", "source_vega",
                          "iv_pair_ratio", "delta_pair_gap") if x in frame]
    frame = frame.copy()
    if "greeks_quality" in frame:
        for field in ("delta", "gamma", "theta", "vega"):
            if field in frame:
                frame.loc[~frame.greeks_quality.isin(["READY", "IV WARNING"]), field] = None
    else:
        for field in ("delta", "gamma", "theta", "vega"):
            if field in frame:
                frame[field] = None
    def quotes(rows):
        return [{k: row.get(k) for k in ("symbol", "security_id", "last_price", "volume", "oi",
                                        "last_trade_time", "timestamp")} for row in rows]
    return clean({
        "record_schema": 2,
        "at": summary["created_at"], "spot": spot, "expiry": summary["expiry"],
        "version": snapshot.metadata.get("version"), "session": summary["market_session"],
        "feeds": {k: {f: v.get(f) for f in ("ok", "use_state", "fetched_at", "age_seconds")}
                  for k, v in summary["feeds"].items()},
        "options": frame[fields].to_dict("records"),
        "quotes": quotes([snapshot.nifty_future_quote or {}, snapshot.vix_quote or {}]
                         + list(snapshot.heavyweight_quotes)),
        "indicators": summary["indicators"], "barriers": summary["barrier_map"],
        "activity": summary["big_player_activity"],
        "direction": summary["decision"].get("market_direction"),
        "background_action": summary["decision"].get("final_action"),
        "days_to_expiry": (date.fromisoformat(str(summary["expiry"])) - snapshot.created_at.astimezone(IST).date()).days,
        "hours_to_expiry": max(0,(datetime.combine(date.fromisoformat(str(summary["expiry"])),time(15,30),IST)-snapshot.created_at.astimezone(IST)).total_seconds()/3600),
        "future_contract": {"security_id": snapshot.metadata.get("future_security_id"), "expiry": snapshot.metadata.get("future_expiry")},
        "institutional_context": summary.get("institutional_context", {}),
        "history_analytics": snapshot.metadata.get("history_analytics", {}),
        # Canonical background inputs/results for later diagnosis, not extra votes.
        "evidence": {k: summary.get(k) for k in (
            "core_evidence", "price_action", "patterns", "option_intelligence",
            "heavyweights", "volume", "vix_context", "news_context", "event_risk",
            "decision", "trade_plan", "execution_guard", "risk_profile")},
    })


class DayMemory:
    EXPORT_TABLES = ("meta", "samples", "candles", "events", "zones", "cycle_summaries", "signals", "outcomes")

    def _write_export(self, db, destination):
        """Allowlisted market evidence, transaction-consistent, no runtime secrets."""
        with gzip.open(destination, "wt", encoding="utf-8") as output:
            output.write(encode({"format": "nifty-evidence-jsonl", "schema": 1}) + "\n")
            for table in self.EXPORT_TABLES:
                cursor = db.execute(f"SELECT * FROM {table}")
                columns = [d[0] for d in cursor.description]
                for row in cursor:
                    output.write(encode({"table": table, "row": dict(zip(columns, row))}) + "\n")

    def export_bytes(self):
        # Stream into a temporary file to avoid building the uncompressed DB in RAM.
        with tempfile.TemporaryDirectory(prefix="nifty-export-") as directory:
            target = Path(directory) / "evidence.jsonl.gz"
            with self.connect() as db:
                db.execute("BEGIN")
                self._write_export(db, target)
            if target.stat().st_size > 40 * 1024 * 1024:
                raise ValueError("Export exceeds 40 MB; use the persistent-volume archive")
            return target.read_bytes()

    def _archive_cycle(self, db, expiry):
        # An archive failure must abort the rollover BEFORE deleting any rows.
        directory = self.pathpath.parent / "archives"
        directory.mkdir(parents=True, exist_ok=True)
        fd, raw = tempfile.mkstemp(prefix=expiry + "-", suffix=".jsonl.gz", dir=directory)
        os.close(fd)
        target = Path(raw)
        try:
            self._write_export(db, target)
            with target.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(target, directory / (expiry + "-evidence.jsonl.gz"))
        except Exception:
            target.unlink(missing_ok=True)
            raise

    def __init__(self, path):
        self.pathpath = Path(path)
        self.pathpath.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS samples (at TEXT PRIMARY KEY, body TEXT);
                CREATE TABLE IF NOT EXISTS candles (instrument TEXT, at TEXT, body TEXT,
                    PRIMARY KEY(instrument, at));
                CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at TEXT, kind TEXT,
                    identity TEXT, body TEXT);
                CREATE TABLE IF NOT EXISTS state (identity TEXT PRIMARY KEY, body TEXT);
                CREATE TABLE IF NOT EXISTS zones (identity TEXT PRIMARY KEY, body TEXT);
                CREATE TABLE IF NOT EXISTS cycle_summaries (expiry TEXT PRIMARY KEY, body TEXT);
                CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY, at TEXT, body TEXT);
                CREATE TABLE IF NOT EXISTS outcomes (signal_id INTEGER, horizon INTEGER, body TEXT,
                    PRIMARY KEY(signal_id,horizon));
                CREATE INDEX IF NOT EXISTS events_time ON events(at);
            """)
        self.prune_archives()

    def prune_archives(self):
        """Bound full-cycle files; compact summaries in SQLite are preserved."""
        directory = self.pathpath.parent / "archives"
        if not directory.exists():
            return {"removed": 0, "retained": 0, "bytes": 0}
        files = sorted(
            (item for item in directory.glob("*-evidence.jsonl.gz") if item.is_file()),
            key=lambda item: (item.stat().st_mtime, item.name),
            reverse=True,
        )
        keep_count = max(1, int(CONFIG.day_memory_archive_keep_cycles))
        max_bytes = max(1, int(CONFIG.day_memory_archive_max_mb)) * 1024 * 1024
        retained = []
        total = 0
        removed = 0
        for item in files:
            try:
                size = item.stat().st_size
            except OSError:
                continue
            # Always retain the newest valid archive even if it alone exceeds
            # the cap; deleting the only recovery copy would be unsafe.
            allowed = len(retained) < keep_count and (not retained or total + size <= max_bytes)
            if allowed:
                retained.append(item)
                total += size
                continue
            try:
                item.unlink()
                removed += 1
            except OSError:
                pass
        return {"removed": removed, "retained": len(retained), "bytes": total}

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.pathpath, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _roll(self, db, day, expiry):
        if date.fromisoformat(expiry) < date.fromisoformat(day):
            raise ValueError("Expired contract cannot start a new recording session")
        row = db.execute("SELECT value FROM meta WHERE key='day'").fetchone()
        if row and day < row[0]:
            raise ValueError("Older session cannot replace current memory")
        cycle = db.execute("SELECT value FROM meta WHERE key='cycle'").fetchone()
        if not cycle:
            # Migrate the one-day database without erasing existing data.
            last = db.execute("SELECT body FROM samples ORDER BY at DESC LIMIT 1").fetchone()
            prior_expiry = json.loads(last[0]).get("expiry") if last else expiry
            date.fromisoformat(str(prior_expiry))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('cycle',?)", (prior_expiry,))
            cycle = (prior_expiry,)
        if day > cycle[0]:
            from services.cycle_outcomes import cycle_summary
            summary = cycle_summary(db, cycle[0])
            db.execute("INSERT OR REPLACE INTO cycle_summaries VALUES (?,?)", (cycle[0], encode(summary)))
            self._archive_cycle(db, cycle[0])
            # Archive + purge are one transaction. Failure rolls both back.
            db.execute("DELETE FROM cycle_summaries WHERE expiry NOT IN (SELECT expiry FROM cycle_summaries ORDER BY expiry DESC LIMIT 8)")
            for table in ("samples", "candles", "events", "state", "zones", "signals", "outcomes"):
                db.execute(f"DELETE FROM {table}")
            db.execute("INSERT OR REPLACE INTO meta VALUES ('cycle',?)", (expiry,))
            db.execute("DELETE FROM meta WHERE key='cycle_price_strikes'")
        if not row or day != row[0]:
            # Preserve historical observations; restart live pattern phases each session.
            for table in ("state", "zones"):
                db.execute(f"DELETE FROM {table}")
            db.execute("INSERT OR REPLACE INTO meta VALUES ('day',?)", (day,))

    def _event(self, db, at, kind, identity, body):
        encoded = encode(body)
        key = kind + ":" + identity
        previous = db.execute("SELECT body FROM state WHERE identity=?", (key,)).fetchone()
        if previous and previous[0] == encoded:
            return
        db.execute("INSERT INTO events(at,kind,identity,body) VALUES (?,?,?,?)", (at, kind, identity, encoded))
        db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, encoded))

    def gap(self, now, reason):
        # Do not clear yesterday on a holiday/token failure without fresh session evidence.
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('last_error',?)", (encode({"at": now.isoformat(), "reason": reason}),))
            day = db.execute("SELECT value FROM meta WHERE key='day'").fetchone()
            if day and day[0] == now.astimezone(IST).date().isoformat():
                self._event(db, now.isoformat(), "DATA", "feed", {"status": "GAP", "reason": reason})

    def record(self, snapshot):
        from analysis.technical_utils import completed_candles
        at = snapshot.created_at.astimezone(IST)
        if not recording_time(at):
            return False
        # Fresh price and current-day completed candles required, not merely wall clock.
        if not all(snapshot.feed_status.get(k) and snapshot.feed_status[k].use_state == "LIVE"
                   for k in ("quotes", "candles")):
            self.gap(at, "Price/candle data fresh nahi; sample skip hua")
            return False
        with self.connect() as db:
            tracked = {leg["strike"] for raw, in db.execute("SELECT body FROM signals WHERE (SELECT COUNT(*) FROM outcomes WHERE signal_id=signals.id)<3")
                       for leg in json.loads(raw).get("legs", [])}
            pinned = db.execute("SELECT value FROM meta WHERE key='cycle_price_strikes'").fetchone()
            if pinned:
                saved = json.loads(pinned[0])
                if saved.get("expiry") == str(snapshot.public_summary()["expiry"]):
                    tracked.update(saved.get("strikes", []))
        body = compact(snapshot, tracked)
        stamp = at.isoformat()
        slot = at.replace(second=0, microsecond=0).isoformat()
        with self.connect() as db:
            self._roll(db, at.date().isoformat(), str(body["expiry"]))
            if body["options"] and not db.execute("SELECT 1 FROM meta WHERE key='cycle_price_strikes'").fetchone():
                # Retain the initial observed strike set when spot moves away.
                # No extra broker request; absent source rows still remain missing.
                strikes = sorted({r["strike"] for r in body["options"] if r.get("strike") is not None})[:64]
                db.execute("INSERT INTO meta VALUES ('cycle_price_strikes',?)", (encode({"expiry": str(body["expiry"]), "strikes": strikes}),))
            if db.execute("SELECT 1 FROM samples WHERE at=?", (slot,)).fetchone():
                return False
            last = db.execute("SELECT at FROM samples ORDER BY at DESC LIMIT 1").fetchone()
            if last and last[0][:10] == stamp[:10] and (at - datetime.fromisoformat(last[0])).total_seconds() > 120:
                self._event(db, stamp, "DATA", "interval", {"status": "GAP", "from": last[0], "to": stamp})
                # Missed observations cannot support a subsequent retest claim.
                for identity, raw in db.execute("SELECT identity,body FROM zones").fetchall():
                    zone = json.loads(raw)
                    zone["broken"] = False
                    zone["recovery_after"] = stamp
                    db.execute("UPDATE zones SET body=? WHERE identity=?", (encode(zone), identity))
            db.execute("INSERT INTO samples VALUES (?,?)", (slot, encode(body)))
            db.execute("DELETE FROM meta WHERE key='last_error'")
            self._event(db, stamp, "DATA", "feed", {"status": "RECORDING"})
            for name, frame in (("NIFTY", snapshot.candles_1m), ("FUTURES", snapshot.future_candles_1m)):
                if name == "FUTURES" and snapshot.metadata.get("future_security_id") is not None:
                    name += ":" + str(snapshot.metadata["future_security_id"])
                for row in completed_candles(frame).to_dict("records"):
                    raw = row.get("timestamp")
                    if raw is None:
                        continue
                    dt = datetime.fromisoformat(str(raw))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=IST)
                    dt = dt.astimezone(IST)
                    if dt.date() != at.date() or not time(9,15) <= dt.time().replace(tzinfo=None) < time(15,30):
                        continue
                    fields = {k: row.get(k) for k in ("open", "high", "low", "close", "volume", "open_interest")}
                    db.execute("INSERT OR IGNORE INTO candles VALUES (?,?,?)", (name, dt.isoformat(), encode(fields)))
            # Preserve old zones even when nearest level changes after a break.
            for name in ("nearest_resistance", "next_resistance", "nearest_support", "next_support"):
                level = body["barriers"].get(name)
                if not level or body["spot"] is None:
                    continue
                lo, hi = level["lower"], level["upper"]
                identity = f'{level["side"]}:{lo}:{hi}'
                db.execute("INSERT OR IGNORE INTO zones VALUES (?,?)", (identity, encode({"lower": lo, "upper": hi, "side": level["side"], "first_seen": stamp})))
            for identity, raw in db.execute("SELECT identity,body FROM zones"):
                level = json.loads(raw)
                lo, hi = level["lower"], level["upper"]
                p = body["spot"]
                status = "ZONE KE ANDAR" if lo <= p <= hi else "ZONE KE UPAR" if p > hi else "ZONE KE NEECHE"
                self._event(db, stamp, "BARRIER", identity, {"zone": f"{lo:,.0f}–{hi:,.0f}", "side": level["side"], "status": status})
                candles = completed_candles(snapshot.candles_3m)
                if not candles.empty:
                    candle = candles.iloc[-1]
                    candle_at = datetime.fromisoformat(str(candle["timestamp"]))
                    if candle_at.tzinfo is None:
                        candle_at = candle_at.replace(tzinfo=IST)
                    # Only evaluate a candle formed after the zone was first observed.
                    evidence_start = level.get("recovery_after", level["first_seen"])
                    if candle_at >= datetime.fromisoformat(evidence_start) and candle_at.isoformat() != level.get("last_candle"):
                        reaction, broken = candle_reaction(level, candle)
                        level.update(broken=broken, last_candle=candle_at.isoformat())
                        db.execute("UPDATE zones SET body=? WHERE identity=?", (encode(level), identity))
                        self._event(db, stamp, "3m REACTION", identity, {"zone": f"{lo:,.0f}–{hi:,.0f}", "side": level["side"], "status": reaction,
                            "expiry": body["expiry"], "version": body["version"]})
            from analysis.pattern_alerts import aligned_pattern_alert
            pattern = aligned_pattern_alert(snapshot)
            if pattern:
                self._event(db, stamp, "STRONG PATTERN", "background", {k: pattern[k] for k in ("direction", "names", "pattern_ids")})
            activity = body.get("activity") or {}
            self._event(db, stamp, "FLOW", "background", {k: activity.get(k) for k in ("direction", "state", "confirmation_count")})
            self._event(db, stamp, "DIRECTION", "background", {"direction": body["direction"], "source": "Background reference; app AI nahi"})
            from services.cycle_outcomes import update_outcomes
            update_outcomes(db, body)
        self.prune_archives()
        return True

    def app_event(self, now, body):
        at = datetime.fromisoformat(str(body["at"]))
        if at.tzinfo is None or not 0 <= (now - at).total_seconds() <= 120 or not recording_time(now):
            return False
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='day'").fetchone()
            if not row or row[0] != at.astimezone(IST).date().isoformat():
                return False
            self._event(db, at.isoformat(), "APP AI", "actual", {"action": str(body.get("action", ""))[:80],
                "reason": str(body.get("reason", ""))[:400], "version": str(body.get("version", ""))[:80]})
            db.execute("INSERT OR REPLACE INTO meta VALUES ('app_heartbeat',?)", (at.isoformat(),))
            from services.cycle_outcomes import record_signal
            record_signal(db, body)
        return True

    def report(self):
        with self.connect() as db:
            meta = dict(db.execute("SELECT key,value FROM meta"))
            counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("samples", "candles", "events")}
            span = db.execute("SELECT MIN(at),MAX(at) FROM samples").fetchone()
            events = [{"at": a, "kind": k, "identity": identity, **json.loads(b)} for a, k, identity, b in db.execute(
                "SELECT at,kind,identity,body FROM events ORDER BY id DESC LIMIT 100")]
            summaries = [json.loads(r[0]) for r in db.execute("SELECT body FROM cycle_summaries ORDER BY expiry DESC")]
            outcomes = [{"at": a, "horizon_minutes": h, **json.loads(b)} for a,h,b in db.execute(
                "SELECT signals.at,outcomes.horizon,outcomes.body FROM outcomes JOIN signals ON signals.id=outcomes.signal_id ORDER BY signals.id DESC,horizon LIMIT 30")]
            recent = [json.loads(r[0]) for r in db.execute("SELECT body FROM samples ORDER BY at DESC LIMIT 20")]
            from analysis.cycle_prices import cycle_prices
            # Read only price fields, not full evidence payloads; no new feed calls.
            cycle_view = cycle_prices((json.loads(r[0]) for r in db.execute(
                "SELECT json_object('at',json_extract(body,'$.at'),'expiry',json_extract(body,'$.expiry'),"
                "'spot',json_extract(body,'$.spot'),'feeds',json_extract(body,'$.feeds'),"
                "'options',json_extract(body,'$.options')) FROM samples ORDER BY at")), meta.get("cycle"))
            latest = recent[0] if recent else {}
            latest_options = latest.get("options", [])
            coverage = {
                "record_schema": latest.get("record_schema", 1) if latest else None,
                "sample_at": latest.get("at"),
                "option_rows": len(latest_options),
                "raw_greeks_rows": sum(all(r.get("source_" + key) is not None for key in ("implied_volatility", "delta", "gamma", "theta", "vega")) for r in latest_options),
                "evidence_fields_saved": [k for k, v in (latest.get("evidence") or {}).items() if v is not None],
                "note": "Saved fields != valid/fresh evidence. Feed states and module status must also pass. Older records are not backfilled.",
            }
            # Keep the last valid Top-9 observation for diagnostics. The live
            # brain still uses only fresh evidence; this is historical display.
            last_valid_top9 = None
            for item in recent:
                heavy = ((item.get("evidence") or {}).get("heavyweights") or {})
                state = heavy.get("recent_state")
                move = heavy.get("recent_15m_move_pct")
                if state and state != "WARMING UP" and move is not None:
                    last_valid_top9 = {
                        "at": item.get("at"),
                        "state": state,
                        "move_pct": move,
                    }
                    break
            coverage["last_valid_top9"] = last_valid_top9
            last_app = db.execute("SELECT MAX(at) FROM events WHERE kind='APP AI'").fetchone()[0]
            coverage["last_app_ai_at"] = last_app
            coverage["last_app_heartbeat_at"] = meta.get("app_heartbeat")
            archive_files = list((self.pathpath.parent / "archives").glob("*-evidence.jsonl.gz"))
            coverage["archive_files"] = len(archive_files)
            coverage["archive_bytes"] = sum(item.stat().st_size for item in archive_files if item.is_file())
            coverage["archive_policy"] = (
                f"newest {CONFIG.day_memory_archive_keep_cycles} full cycles; "
                f"max {CONFIG.day_memory_archive_max_mb} MB; 8 compact summaries retained"
            )
            slots = [a for (a,) in db.execute("SELECT at FROM samples WHERE at LIKE ? ORDER BY at", (str(meta.get("day", "")) + "%",))]
            if slots:
                expected = int((datetime.fromisoformat(slots[-1]) - datetime.fromisoformat(slots[0])).total_seconds() // 60) + 1
                coverage.update(session_samples=len(slots), observed_span_slots=expected,
                                missing_slots=max(0, expected-len(slots)),
                                slot_coverage_pct=round(100*len(slots)/max(1,expected), 1))
            zone_history = []
            if recent:
                for name in ("nearest_resistance", "next_resistance", "nearest_support", "next_support"):
                    zone = recent[0].get("barriers", {}).get(name)
                    if not zone:
                        continue
                    key = f'{zone["side"]}:{zone["lower"]}:{zone["upper"]}'
                    reactions = [(a,json.loads(b)) for a,b in db.execute("SELECT at,body FROM events WHERE kind='3m REACTION' AND identity=? ORDER BY at", (key,))]
                    zone_history.append({"side": zone["side"], "lower": zone["lower"], "upper": zone["upper"],
                        "rejections": sum(r.get("status") == "REJECTION — 3m CLOSE" for _,r in reactions),
                        "breaks": sum(r.get("status") == "BREAK — 3m CLOSE" for _,r in reactions),
                        "retest_holds": sum(r.get("status") == "RETEST HOLD — 3m CLOSE" for _,r in reactions),
                        "last": reactions[-1][0] if reactions else None})
        return {"day": meta.get("day"), "counts": counts, "first": span[0], "last": span[1],
                "last_error": json.loads(meta.get("last_error", "null")), "events": events,
                "cycle_expiry": meta.get("cycle"), "cycle_summaries": summaries, "outcomes": outcomes,
                "zone_history": zone_history,
                "cycle_prices": cycle_view,
                "recording_coverage": coverage,
                "recent_context": [{k: s.get(k) for k in ("at", "expiry", "version", "spot", "direction", "activity", "feeds", "barriers", "future_contract")} for s in recent],
                "bytes": self.pathpath.stat().st_size}
