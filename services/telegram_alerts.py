from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, time as clock_time
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from services.live_monitor import LiveImpulse, calculate_live_impulse_from_changes


IST = ZoneInfo("Asia/Kolkata")


def _market_hours(now: datetime) -> bool:
    local = now.astimezone(IST)
    return local.weekday() < 5 and clock_time(9, 15) <= local.time() <= clock_time(15, 30)


class TelegramNotifier:
    def __init__(self) -> None:
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.enabled = os.getenv("TELEGRAM_ALERTS_ENABLED", "1").strip().lower() not in {
            "0",
            "false",
            "off",
            "no",
        }

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.bot_token and self.chat_id)

    def send(self, text: str) -> None:
        if not self.configured:
            raise RuntimeError("Telegram bot token or chat ID is missing")
        body = urlencode(
            {
                "chat_id": self.chat_id,
                "text": str(text)[:4000],
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = Request(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urlopen(request, timeout=5.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("description") or "Telegram send failed"))


class LiveAlertEngine:
    """Deduplicated live-speed alerts; never places or recommends an order."""

    def __init__(
        self,
        notifier: TelegramNotifier | None = None,
        *,
        confirmations: int = 2,
        cooldown_seconds: int = 180,
        sender: Callable[[str], None] | None = None,
        async_delivery: bool = True,
    ) -> None:
        self.notifier = notifier or TelegramNotifier()
        self.confirmations = max(2, int(confirmations))
        self.cooldown_seconds = max(30, int(cooldown_seconds))
        self._sender = sender or self.notifier.send
        self.async_delivery = bool(async_delivery)
        self._lock = threading.Lock()
        self._candidate = ""
        self._candidate_count = 0
        self._last_sent: dict[str, float] = {}
        self.last_alert_at: float | None = None
        self.last_alert = ""
        self.last_error = ""
        self.alert_count = 0
        self._big_player_candidate = ""
        self._big_player_count = 0

    @property
    def configured(self) -> bool:
        return self.notifier.configured

    @staticmethod
    def _message(impulse: LiveImpulse, ltp: float | None) -> str:
        icon = "🟢" if impulse.direction == "BULLISH" else "🔴"
        title = "MAJOR MOVE" if impulse.state == "MAJOR MOVE CONFIRMED" else "FAST MOVE"
        lines = [
            f"{icon} NIFTY {title} — {impulse.direction}",
            f"Speed score: {impulse.score:.0f}/100",
        ]
        if ltp is not None:
            lines.append(f"NIFTY: {ltp:,.2f}")
        if impulse.reasons:
            lines.append(" | ".join(impulse.reasons[:4]))
        lines.extend(
            (
                "Status: Early warning — One-Brain confirmation alag se check karein.",
                "Automatic order nahi lagaya gaya.",
            )
        )
        return "\n".join(lines)

    def observe(
        self,
        *,
        changes: dict[int, float | None],
        ltp: float | None,
        now_ts: float | None = None,
        enforce_market_hours: bool = True,
    ) -> bool:
        timestamp = float(now_ts if now_ts is not None else time.time())
        if enforce_market_hours and not _market_hours(datetime.fromtimestamp(timestamp, IST)):
            return False
        impulse = calculate_live_impulse_from_changes(changes)
        qualified = (
            impulse.direction in {"BULLISH", "BEARISH"}
            and (
                impulse.state == "MAJOR MOVE CONFIRMED"
                or (impulse.state == "FAST MOVE WATCH" and impulse.score >= 55)
            )
        )
        with self._lock:
            if not qualified:
                self._candidate = ""
                self._candidate_count = 0
                return False
            signature = f"{impulse.direction}:{impulse.state}"
            if signature == self._candidate:
                self._candidate_count += 1
            else:
                self._candidate = signature
                self._candidate_count = 1
            if self._candidate_count < self.confirmations:
                return False
            if timestamp - self._last_sent.get(signature, 0.0) < self.cooldown_seconds:
                return False
            self._last_sent[signature] = timestamp
            self._candidate_count = 0

        message = self._message(impulse, ltp)
        if self.async_delivery:
            threading.Thread(
                target=self._deliver,
                args=(message, signature, timestamp),
                daemon=True,
                name="telegram-alert-send",
            ).start()
            return True
        return self._deliver(message, signature, timestamp)

    def _deliver(self, message: str, signature: str, timestamp: float) -> bool:
        try:
            self._sender(message)
            with self._lock:
                self.last_alert_at = timestamp
                self.last_alert = signature
                self.last_error = ""
                self.alert_count += 1
            return True
        except Exception as exc:
            with self._lock:
                self.last_error = str(exc)[:300]
            return False

    def send_test(self) -> None:
        self._sender(
            "✅ Nifty Seller Lite Telegram alert connected.\n"
            "Railway server se test message successful hai.\n"
            "Automatic order kabhi place nahi hoga."
        )

    def observe_pattern(self, payload: dict[str, Any], now_ts: float | None = None) -> bool:
        timestamp = time.time() if now_ts is None else now_ts
        if not _market_hours(datetime.fromtimestamp(timestamp, IST)):
            return False
        try:
            stamp = datetime.fromisoformat(str(payload["captured_at"]))
            if not 0 <= timestamp - stamp.timestamp() <= 120:
                return False
        except (KeyError, ValueError, TypeError):
            return False
        ids = payload.get("pattern_ids")
        if payload.get("direction") not in {"BULLISH", "BEARISH"} or not isinstance(ids, list) or not 1 <= len(ids) <= 2:
            return False
        signatures = ["pattern:" + stamp.date().isoformat() + ":" + str(x)[:180] for x in ids]
        with self._lock:
            if all(x in self._last_sent for x in signatures):
                return False
            # Delivery is synchronous under this lock to prevent duplicate requests.
            self._sender(str(payload.get("message", "Strong aligned pattern confirmation"))[:1800])
            for signature in signatures:
                self._last_sent[signature] = timestamp
            self.alert_count += 1
            self.last_alert = signatures[0]
            self.last_alert_at = timestamp
        return True

    def observe_big_player(self, payload: dict[str, Any], *, now_ts: float | None = None) -> bool:
        """Send bounded Big Player evidence; it never converts conflict into advice."""
        timestamp = float(now_ts if now_ts is not None else time.time())
        score = float(payload.get("score", 0.0) or 0.0)
        confirmations = int(payload.get("confirmation_count", 0) or 0)
        direction = str(payload.get("direction", "MIXED")).upper()
        activity_type = str(payload.get("activity_type", "ACTIVITY")).upper()
        conflict = bool(payload.get("conflict", False))
        stage = "CONFIRMED" if score >= 70 and confirmations >= 2 else "EARLY" if score >= 65 and confirmations >= 1 else ""
        if not stage or direction not in {"BUYING", "SELLING"}:
            return False
        signature = f"BIG:{stage}:{direction}:{activity_type}"
        with self._lock:
            if signature == self._big_player_candidate:
                self._big_player_count += 1
            else:
                self._big_player_candidate = signature
                self._big_player_count = 1
            required = 1 if stage == "CONFIRMED" else 2
            if self._big_player_count < required:
                return False
            if timestamp - self._last_sent.get(signature, 0.0) < self.cooldown_seconds:
                return False
            self._last_sent[signature] = timestamp
            self._big_player_count = 0
        icon = "🟢" if direction == "BUYING" else "🔴"
        lines = [
            f"{icon} BIG PLAYER {stage} — {direction}",
            f"Score {score:.0f}/100 · {confirmations}/2 · {activity_type}",
        ]
        if payload.get("futures_setup"):
            lines.append(f"Futures: {payload['futures_setup']}")
        if conflict:
            lines.append("⚠️ Options/Top-9 conflict — TRADE WAIT; activity alert only.")
        else:
            lines.append("Direction supporting evidence; One-Brain entry confirmation alag hai.")
        lines.append("Automatic order nahi lagaya gaya.")
        message = "\n".join(lines)
        if self.async_delivery:
            threading.Thread(
                target=self._deliver,
                args=(message, signature, timestamp),
                daemon=True,
                name="telegram-big-player-send",
            ).start()
            return True
        return self._deliver(message, signature, timestamp)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "configured": self.configured,
                "alert_count": self.alert_count,
                "last_alert": self.last_alert or None,
                "last_alert_age_seconds": (
                    round(time.time() - self.last_alert_at, 1)
                    if self.last_alert_at is not None
                    else None
                ),
                "last_error": self.last_error,
                "confirmations_required": self.confirmations,
                "cooldown_seconds": self.cooldown_seconds,
            }
