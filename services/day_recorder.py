"""Opt-in Railway background observer using existing analysis, isolated from app state."""
from __future__ import annotations

import fcntl
import os
import threading
from datetime import datetime
from pathlib import Path
import time as clock
import gc

from services.day_memory import DayMemory, IST, recording_time


class GatewayReader:
    def __init__(self, gateway):
        self.gateway = gateway

    def market_quote(self, instruments):
        return self._checked(self.gateway.market_quote, instruments)

    def _checked(self, method, *args):
        with self.gateway._lock:
            result = method(*args)
            if self.gateway.last_error:
                raise RuntimeError("Gateway returned cached data after failure")
            return result

    def intraday_candles(self, **kwargs):
        payload = dict(kwargs)
        for key in ("from_date", "to_date"):
            payload[key] = payload[key].isoformat()
        return self._checked(self.gateway.intraday, payload)

    def expiry_list(self, underlying_security_id=13, segment="IDX_I"):
        return self._checked(self.gateway.expiry_list, underlying_security_id, segment)

    def option_chain(self, *, expiry, underlying_security_id=13, segment="IDX_I"):
        return self._checked(self.gateway.option_chain, expiry, underlying_security_id, segment)


class DayRecorder:
    def __init__(self, gateway_factory):
        self.gateway_factory = gateway_factory
        self.store = None
        self.history_root = None
        self.stop_event = threading.Event()
        self.thread = None
        self.lock_file = None
        self.status = "OFF — DAY_MEMORY_ENABLED=1 aur volume chahiye"
        self.last_build_seconds = None
        self.paper_monitor = None
        self.paper_status = "NOT REGISTERED"

    def start(self):
        if os.getenv("DAY_MEMORY_ENABLED") != "1":
            return
        # Require an explicit persistent path; never silently promise /tmp durability.
        mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
        if not mount or not Path(mount).is_dir():
            self.status = "BLOCKED — Railway persistent volume mount missing"
            return
        root = Path(mount) / "nifty_day_memory"
        root.mkdir(parents=True, exist_ok=True)
        self.lock_file = (root / "recorder.lock").open("a+")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock_file.close()
            self.lock_file = None
            self.status = "BLOCKED — recorder already running; use one replica"
            return
        self.store = DayMemory(root / "session.sqlite3")
        from services.paper_monitor import PaperMonitor
        self.paper_monitor = PaperMonitor(root / "paper_monitor.json")
        self.history_root = root
        self.status = "READY — market-session ka wait"
        self.thread = threading.Thread(target=self._run, args=(root,), daemon=True)
        self.thread.start()

    def _run(self, root):
        from services.snapshot_service import SnapshotService
        service = None
        while not self.stop_event.is_set():
            now = datetime.now(IST)
            if recording_time(now):
                started = clock.monotonic()
                try:
                    gateway = self.gateway_factory()
                    idle_seconds = gateway.foreground_idle_seconds()
                    minimum_idle = max(10.0, float(os.getenv("DAY_MEMORY_MIN_IDLE_SECONDS", "25") or 25))
                    if idle_seconds < minimum_idle:
                        self.status = f"WAIT — app request priority ({idle_seconds:.0f}s idle)"
                        self.stop_event.wait(max(1.0, 60.0 - clock.time() % 60.0))
                        continue
                    if service is None:
                        service = SnapshotService.background_observer(GatewayReader(gateway), root)
                    snapshot = service.build()
                    stored = self.store.record(snapshot)
                    if self.paper_monitor:
                        try:
                            self.paper_monitor.observe(snapshot)
                            self.paper_status = "OBSERVING REGISTERED PAPER POSITIONS"
                        except Exception as exc:
                            self.paper_status = "PAPER MONITOR GAP — " + type(exc).__name__
                    self.status = "RECORDING" if stored else "WAIT — duplicate / data not fresh"
                    # Snapshot contains several pandas frames. Never retain a full
                    # minute snapshot in the long-lived Railway worker.
                    del snapshot
                    gc.collect()
                except Exception as exc:
                    # Never persist credentials, API error bodies or arbitrary exception text.
                    self.status = "DATA GAP — " + type(exc).__name__
                    self.store.gap(now, self.status)
                finally:
                    self.last_build_seconds = round(clock.monotonic() - started, 2)
            else:
                self.status = "SESSION CLOSED — saved record available"
            # No overlapping builds or catch-up bursts. One sample per minute maximum.
            # Align to the next wall-clock minute; build time must not accumulate
            # into a 70/80-second sampling interval. Never replay missed slots.
            self.stop_event.wait(max(1.0, 60.0 - clock.time() % 60.0))

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        # Keep the single-worker lock until an in-flight request has actually stopped.
        if self.lock_file and (not self.thread or not self.thread.is_alive()):
            self.lock_file.close()

    def report(self):
        data = self.store.report() if self.store else {"events": [], "counts": {}}
        now = datetime.now(IST)
        last = (data.get("recording_coverage") or {}).get("sample_at") or data.get("last")
        age = None
        if last:
            try:
                stamp = datetime.fromisoformat(last)
                if stamp.tzinfo is not None:
                    age = (now - stamp).total_seconds()
            except (TypeError, ValueError):
                pass
        health = "NO SAVED SAMPLE" if age is None else "REFERENCE — SESSION CLOSED" if not recording_time(now) else "RECENT SAMPLE" if 0 <= age <= 180 else "RECORDING GAP / STALE"
        return {**data, "recorder_status": self.status, "interval_seconds": 60,
                "last_build_seconds": self.last_build_seconds,
                "paper_monitor_status": self.paper_status,
                "recording_health": health, "last_sample_age_seconds": age,
                "note": "Background reference, app AI alag. Samples/events limited to observation times; no full replay."}
