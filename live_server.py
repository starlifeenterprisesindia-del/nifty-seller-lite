from __future__ import annotations

import os
import resource
import tempfile
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, time as wall_time
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Body, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse

from services.dhan_gateway import DhanGateway
from services.railway_alert_store import PremiumAlertMonitor, RailwayAlertStore
from services.telegram_alerts import LiveAlertEngine
from services.day_recorder import DayRecorder


IST = ZoneInfo("Asia/Kolkata")


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _first(payload: dict[str, Any], *names: str) -> Any:
    lowered = {str(key).lower(): value for key, value in payload.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


class LiveFeedState:
    """Thread-safe, read-only Dhan WebSocket state for the Railway API."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._feed: Any = None
        self._thread: threading.Thread | None = None
        self._supervisor_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._history: deque[dict[str, Any]] = deque(maxlen=720)
        self.started_at: float | None = None
        self.last_tick_at: float | None = None
        self.last_error_at: float | None = None
        self.last_error = ""
        self.connected = False
        self.tick_count = 0
        self.latest: dict[str, dict[str, Any]] = {}

    def _on_connect(self, *_: Any) -> None:
        with self._lock:
            self.connected = True
            self.last_error = ""

    def _on_close(self, *_: Any) -> None:
        with self._lock:
            self.connected = False

    def _on_error(self, _feed: Any, error: Any) -> None:
        with self._lock:
            self.connected = False
            self.last_error = str(error)[:300]
            self.last_error_at = time.time()

    def _on_message(self, _feed: Any, raw: Any) -> None:
        if not isinstance(raw, dict):
            return
        security_id = str(
            _first(raw, "security_id", "securityId", "SecurityId") or ""
        )
        ltp = _number(_first(raw, "LTP", "ltp", "last_price", "LastTradedPrice"))
        if not security_id or ltp is None:
            return
        captured = time.time()
        normalized = {
            "security_id": security_id,
            "ltp": ltp,
            "volume": _number(_first(raw, "volume", "volume_traded", "Vol")),
            "open_interest": _number(_first(raw, "OI", "oi", "open_interest")),
            "last_trade_time": _first(raw, "LTT", "ltt", "last_trade_time"),
            "captured_at": datetime.fromtimestamp(captured, IST).isoformat(),
        }
        with self._lock:
            self.connected = True
            self.last_tick_at = captured
            self.tick_count += 1
            self.latest[security_id] = normalized
            if security_id == "13":
                self._history.append(
                    {"captured_ts": captured, "ltp": ltp}
                )
        if security_id == "13":
            ALERTS.observe(
                changes={
                    seconds: self._change(list(self._history), captured, seconds)
                    for seconds in (5, 15, 30, 60)
                },
                ltp=ltp,
                now_ts=captured,
            )

    @staticmethod
    def _market_window() -> bool:
        now = datetime.now(IST)
        return now.weekday() < 5 and wall_time(9, 0) <= now.time() <= wall_time(15, 45)

    def _connect_once(self) -> None:
        client_id = os.getenv("DHAN_CLIENT_ID", "").strip()
        access_token = os.getenv("DHAN_ACCESS_TOKEN", "").strip()
        if not client_id or not access_token:
            with self._lock:
                self.last_error = "DHAN_CLIENT_ID or DHAN_ACCESS_TOKEN is missing"
                self.last_error_at = time.time()
            return
        try:
            from dhanhq import DhanContext, MarketFeed

            context = DhanContext(client_id, access_token)
            instruments = [(MarketFeed.IDX, "13", MarketFeed.Full)]
            self._feed = MarketFeed(
                context,
                instruments,
                "v2",
                on_connect=self._on_connect,
                on_message=self._on_message,
                on_close=self._on_close,
                on_error=self._on_error,
            )
            self.started_at = time.time()
            self._thread = self._feed.start()
        except Exception as exc:
            self._on_error(None, exc)

    def _supervise(self) -> None:
        while not self._stop.is_set():
            if self._market_window() and (
                self._thread is None or not self._thread.is_alive()
            ):
                # Railway rolling restart can briefly overlap the old Dhan socket
                # and receive HTTP 429. Backoff and retry instead of permanently
                # losing the live feed thread.
                if self._feed is not None:
                    try:
                        self._feed.close_connection()
                    except Exception:
                        pass
                self._connect_once()
            self._stop.wait(45.0)

    def start(self) -> None:
        if self._supervisor_thread and self._supervisor_thread.is_alive():
            return
        self._stop.clear()
        self._supervisor_thread = threading.Thread(
            target=self._supervise, daemon=True, name="dhan-feed-supervisor"
        )
        self._supervisor_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._feed is not None:
            try:
                self._feed.close_connection()
            except Exception:
                pass

    @staticmethod
    def _change(history: list[dict[str, Any]], now: float, seconds: int) -> float | None:
        target = now - seconds
        candidates = [row for row in history if float(row["captured_ts"]) <= target]
        if not candidates or not history:
            return None
        return float(history[-1]["ltp"]) - float(candidates[-1]["ltp"])

    def public_state(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            history = list(self._history)
            latest = dict(self.latest)
            last_tick_at = self.last_tick_at
            payload = {
                "service": "nifty-seller-live",
                "version": "1.0.0",
                "connected": self.connected,
                "tick_count": self.tick_count,
                "started_at": (
                    datetime.fromtimestamp(self.started_at, IST).isoformat()
                    if self.started_at else None
                ),
                "last_tick_age_seconds": (
                    round(now - last_tick_at, 1) if last_tick_at else None
                ),
                "last_error": self.last_error,
                "nifty": latest.get("13"),
            }
        payload["impulse"] = {
            f"change_{seconds}s": self._change(history, now, seconds)
            for seconds in (5, 15, 30, 60)
        }
        return payload

    def health(self) -> dict[str, Any]:
        state = self.public_state()
        age = state["last_tick_age_seconds"]
        fresh = bool(state["connected"] and age is not None and age <= 20)
        return {
            "service": state["service"],
            "status": "LIVE" if fresh else "STARTING_OR_MARKET_CLOSED",
            "connected": state["connected"],
            "tick_count": state["tick_count"],
            "last_tick_age_seconds": age,
            "configured": not bool(state["last_error"].startswith("DHAN_CLIENT_ID")),
        }


STATE = LiveFeedState()
ALERTS = LiveAlertEngine(
    confirmations=int(os.getenv("TELEGRAM_ALERT_CONFIRMATIONS", "2") or 2),
    cooldown_seconds=int(os.getenv("TELEGRAM_ALERT_COOLDOWN_SECONDS", "180") or 180),
)
GATEWAY: DhanGateway | None = None
PREMIUM_STORE = RailwayAlertStore()
PREMIUM_MONITOR: PremiumAlertMonitor | None = None
DAY_RECORDER = DayRecorder(lambda: _gateway())


def _authorise(key: str, header_key: str) -> None:
    expected = os.getenv("LIVE_API_KEY", "").strip()
    if expected and key != expected and header_key != expected:
        raise HTTPException(status_code=401, detail="Invalid live API key")


def _gateway() -> DhanGateway:
    global GATEWAY
    if GATEWAY is None:
        client_id = os.getenv("DHAN_CLIENT_ID", "").strip()
        access_token = os.getenv("DHAN_ACCESS_TOKEN", "").strip()
        if not client_id or not access_token:
            raise HTTPException(status_code=503, detail="Railway Dhan credentials missing")
        GATEWAY = DhanGateway(client_id, access_token)
    return GATEWAY


def _gateway_result(function: Any) -> dict[str, Any]:
    try:
        _gateway().mark_foreground()
        return {"ok": True, "data": function()}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:300]) from exc


@asynccontextmanager
async def lifespan(_: FastAPI):
    global PREMIUM_MONITOR
    STATE.start()
    try:
        DAY_RECORDER.start()
    except Exception as exc:
        DAY_RECORDER.status = "START FAILED — " + type(exc).__name__
    try:
        PREMIUM_MONITOR = PremiumAlertMonitor(
            PREMIUM_STORE,
            _gateway().market_quote,
            ALERTS.notifier.send,
            interval_seconds=float(os.getenv("PREMIUM_ALERT_POLL_SECONDS", "5") or 5),
        )
        PREMIUM_MONITOR.start()
    except Exception:
        PREMIUM_MONITOR = None
    yield
    DAY_RECORDER.stop()
    if PREMIUM_MONITOR is not None:
        PREMIUM_MONITOR.stop()
    STATE.stop()


app = FastAPI(title="Nifty Seller Live Feed", version="1.0.0", lifespan=lifespan)


@app.post("/day-memory")
def day_memory(payload: dict[str, Any] = Body(default={}), x_live_key: str = Header(default="")):
    if not os.getenv("LIVE_API_KEY", "").strip():
        raise HTTPException(status_code=503, detail="LIVE_API_KEY required for history")
    _authorise("", x_live_key)
    if payload.get("event") and DAY_RECORDER.store:
        try:
            DAY_RECORDER.store.app_event(datetime.now(IST), payload["event"])
        except (ValueError, TypeError, KeyError):
            raise HTTPException(status_code=400, detail="Invalid history event")
    return {"ok": True, "data": DAY_RECORDER.report()}


@app.post("/day-memory-export")
def day_memory_export(x_live_key: str = Header(default="")):
    import base64
    if not os.getenv("LIVE_API_KEY", "").strip():
        raise HTTPException(status_code=503, detail="LIVE_API_KEY required for history")
    _authorise("", x_live_key)
    if not DAY_RECORDER.store:
        raise HTTPException(status_code=503, detail="Persistent recorder unavailable")
    try:
        content = DAY_RECORDER.store.export_bytes()
    except (ValueError, OSError):
        raise HTTPException(status_code=503, detail="Export unavailable; existing records preserved")
    return {"ok": True, "data": {"content_base64": base64.b64encode(content).decode("ascii"),
                                  "filename": "nifty-evidence.jsonl.gz"}}


@app.get("/day-memory-export-file")
def day_memory_export_file(background_tasks: BackgroundTasks, x_live_key: str = Header(default="")):
    """Stream a full evidence file; avoids JSON/base64 duplication on small RAM plans."""
    if not os.getenv("LIVE_API_KEY", "").strip():
        raise HTTPException(status_code=503, detail="LIVE_API_KEY required for history")
    _authorise("", x_live_key)
    if not DAY_RECORDER.store:
        raise HTTPException(status_code=503, detail="Persistent recorder unavailable")
    fd, raw = tempfile.mkstemp(prefix="nifty-evidence-", suffix=".jsonl.gz")
    os.close(fd)
    try:
        DAY_RECORDER.store.export_file(raw)
    except (ValueError, OSError) as exc:
        try:
            os.unlink(raw)
        except OSError:
            pass
        raise HTTPException(status_code=503, detail=f"Evidence export failed: {type(exc).__name__}") from exc
    background_tasks.add_task(os.unlink, raw)
    return FileResponse(raw, media_type="application/gzip", filename="nifty-evidence.jsonl.gz")


@app.post("/paper-monitor")
def paper_monitor(payload: dict[str, Any] = Body(default={}), x_live_key: str = Header(default="")):
    if not os.getenv("LIVE_API_KEY", "").strip():
        raise HTTPException(status_code=503, detail="LIVE_API_KEY required")
    _authorise("", x_live_key)
    if not DAY_RECORDER.paper_monitor:
        raise HTTPException(status_code=503, detail="Persistent paper monitor unavailable")
    try:
        entries = DAY_RECORDER.paper_monitor.register(payload.get("entries", []))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid paper records")
    return {"ok": True, "data": {"entries": entries}}


@app.post("/market-history")
def market_history(payload: dict[str, Any] = Body(default={}), x_live_key: str = Header(default="")):
    if not os.getenv("LIVE_API_KEY", "").strip():
        raise HTTPException(status_code=503, detail="LIVE_API_KEY required for history")
    _authorise("", x_live_key)
    from services.shared_history import read_history
    root = DAY_RECORDER.history_root
    data = read_history(root, datetime.now(IST), str(payload.get("expiry", ""))) if root else {"options": [], "top9": []}
    return {"ok": True, "data": data}


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "nifty-seller-live", "message": "Railway live server is running"}


@app.get("/health")
def health() -> dict[str, Any]:
    payload = STATE.health()
    payload["telegram"] = ALERTS.status()
    try:
        payload["dhan_gateway"] = _gateway().status()
    except HTTPException as exc:
        payload["dhan_gateway"] = {"configured": False, "error": exc.detail}
    payload["premium_alerts"] = (
        PREMIUM_MONITOR.status() if PREMIUM_MONITOR is not None else {"active": 0, "last_error": "monitor unavailable"}
    )
    # Linux ru_maxrss is KiB. This exposes a number only, never environment data.
    payload["memory"] = {
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1),
        "cache_bounded": True,
    }
    payload["day_recorder"] = {
        "status": DAY_RECORDER.status,
        "last_build_seconds": DAY_RECORDER.last_build_seconds,
    }
    return payload


@app.get("/live")
def live(
    key: str = Query(default=""),
    x_live_key: str = Header(default=""),
) -> dict[str, Any]:
    _authorise(key, x_live_key)
    payload = STATE.public_state()
    payload["telegram"] = ALERTS.status()
    return payload


@app.post("/telegram/test")
def telegram_test(
    key: str = Query(default=""),
    x_live_key: str = Header(default=""),
) -> dict[str, Any]:
    _authorise(key, x_live_key)
    if not ALERTS.configured:
        raise HTTPException(status_code=503, detail="Telegram alerts not configured")
    try:
        ALERTS.send_test()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:200]) from exc
    return {"ok": True, "telegram": ALERTS.status()}


@app.post("/dhan/market-quote")
def dhan_market_quote(
    payload: dict[str, Any] = Body(...),
    key: str = Query(default=""),
    x_live_key: str = Header(default=""),
) -> dict[str, Any]:
    _authorise(key, x_live_key)
    instruments = payload.get("instruments") or {}
    return _gateway_result(lambda: _gateway().market_quote(instruments))


@app.post("/dhan/intraday")
def dhan_intraday(
    payload: dict[str, Any] = Body(...),
    key: str = Query(default=""),
    x_live_key: str = Header(default=""),
) -> dict[str, Any]:
    _authorise(key, x_live_key)
    return _gateway_result(lambda: _gateway().intraday(payload))


@app.post("/dhan/expiry-list")
def dhan_expiry_list(
    payload: dict[str, Any] = Body(...),
    key: str = Query(default=""),
    x_live_key: str = Header(default=""),
) -> dict[str, Any]:
    _authorise(key, x_live_key)
    return _gateway_result(
        lambda: _gateway().expiry_list(
            int(payload.get("underlying_security_id", 13)),
            str(payload.get("segment", "IDX_I")),
        )
    )


@app.post("/dhan/option-chain")
def dhan_option_chain(
    payload: dict[str, Any] = Body(...),
    key: str = Query(default=""),
    x_live_key: str = Header(default=""),
) -> dict[str, Any]:
    _authorise(key, x_live_key)
    return _gateway_result(
        lambda: _gateway().option_chain(
            str(payload["expiry"]),
            int(payload.get("underlying_security_id", 13)),
            str(payload.get("segment", "IDX_I")),
        )
    )


@app.post("/alerts/premium")
def create_premium_alert(
    payload: dict[str, Any] = Body(...),
    key: str = Query(default=""),
    x_live_key: str = Header(default=""),
) -> dict[str, Any]:
    _authorise(key, x_live_key)
    required = ("security_id", "side", "position", "strike", "target_premium")
    missing = [name for name in required if payload.get(name) in (None, "")]
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing: {', '.join(missing)}")
    try:
        row = PREMIUM_STORE.add(
            {
                "security_id": int(payload["security_id"]),
                "side": str(payload["side"]).upper(),
                "position": str(payload["position"]).upper(),
                "strike": float(payload["strike"]),
                "expiry": str(payload.get("expiry", "")),
                "target_premium": float(payload["target_premium"]),
                "mode": str(payload.get("mode", "TOUCH")).upper(),
                "tolerance": max(0.05, float(payload.get("tolerance", 0.5))),
                "entry_no": max(1, min(3, int(payload.get("entry_no", 1)))),
            }
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "data": row}


@app.get("/alerts")
def list_alerts(
    key: str = Query(default=""),
    x_live_key: str = Header(default=""),
) -> dict[str, Any]:
    _authorise(key, x_live_key)
    return {"ok": True, "data": {"alerts": PREMIUM_STORE.list()[-50:]}}


@app.delete("/alerts/{alert_id}")
def cancel_alert(
    alert_id: str,
    key: str = Query(default=""),
    x_live_key: str = Header(default=""),
) -> dict[str, Any]:
    _authorise(key, x_live_key)
    return {"ok": True, "data": {"cancelled": PREMIUM_STORE.cancel(alert_id)}}


@app.post("/alerts/big-player")
def big_player_alert(
    payload: dict[str, Any] = Body(...),
    key: str = Query(default=""),
    x_live_key: str = Header(default=""),
) -> dict[str, Any]:
    _authorise(key, x_live_key)
    sent = ALERTS.observe_big_player(payload)
    return {"ok": True, "data": {"accepted": True, "sent": sent}}


@app.post("/alerts/pattern")
def pattern_alert(payload: dict[str, Any] = Body(...), key: str = Query(default=""),
                  x_live_key: str = Header(default="")) -> dict[str, Any]:
    _authorise(key, x_live_key)
    return {"ok": True, "data": {"sent": ALERTS.observe_pattern(payload)}}
