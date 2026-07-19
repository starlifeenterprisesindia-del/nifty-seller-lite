from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from analysis.candles import aggregate_candles, candles_from_dhan, mark_completed_candles
from analysis.indicators import calculate_indicator_bundle
from analysis.market_session import classify_market_session, feed_use_state
from analysis.option_chain import option_chain_to_frame, select_atm_window
from config import CONFIG, IST_TIMEZONE
from models import FeedStatus, MarketSnapshot
from services.dhan_client import DhanClient
from services.errors import SnapshotBuildError
from services.instrument_master import InstrumentMaster, ResolvedInstrument


IST = ZoneInfo(IST_TIMEZONE)


class SnapshotService:
    def __init__(
        self,
        client: DhanClient,
        instrument_master: InstrumentMaster | None = None,
    ):
        self.client = client
        self.master = instrument_master or InstrumentMaster()

    @staticmethod
    def _extract_quote(
        response: dict[str, Any],
        segment: str,
        security_id: int | str,
    ) -> dict[str, Any] | None:
        data = response.get("data") or {}
        segment_data = data.get(segment) or {}
        return segment_data.get(str(security_id)) or segment_data.get(int(security_id))

    @staticmethod
    def _quote_age_seconds(
        quote: dict[str, Any] | None,
        now: datetime,
    ) -> float | None:
        if not quote:
            return None
        raw = quote.get("last_trade_time")
        if not raw:
            return None
        try:
            if isinstance(raw, (int, float)):
                unit = "ms" if float(raw) > 10_000_000_000 else "s"
                parsed = pd.to_datetime(raw, unit=unit, utc=True).tz_convert(IST_TIMEZONE)
            else:
                parsed = pd.to_datetime(raw)
            if parsed.tzinfo is None:
                parsed = parsed.tz_localize(IST_TIMEZONE)
            else:
                parsed = parsed.tz_convert(IST_TIMEZONE)
            return max(0.0, (pd.Timestamp(now) - parsed).total_seconds())
        except Exception:
            return None

    def _resolve_future(self) -> ResolvedInstrument | None:
        try:
            raw_master = self.master.load()
            return self.master.resolve_nearest_nifty_future(raw_master)
        except Exception:
            return None

    @staticmethod
    def _latest_candle_age_seconds(frame: pd.DataFrame, now: datetime) -> float | None:
        if frame.empty:
            return None
        try:
            latest = pd.Timestamp(frame.iloc[-1]["timestamp"])
            current = pd.Timestamp(now)
            if latest.tzinfo is None:
                latest = latest.tz_localize(IST_TIMEZONE)
            else:
                latest = latest.tz_convert(IST_TIMEZONE)
            if current.tzinfo is None:
                current = current.tz_localize(IST_TIMEZONE)
            else:
                current = current.tz_convert(IST_TIMEZONE)
            return max(0.0, (current - latest).total_seconds())
        except Exception:
            return None

    def build(self, now: datetime | None = None) -> MarketSnapshot:
        if now is None:
            current = datetime.now(IST)
        elif now.tzinfo:
            current = now.astimezone(IST)
        else:
            current = now.replace(tzinfo=IST)

        future_ref = self._resolve_future()
        grouped: dict[str, list[int]] = {
            CONFIG.nifty.exchange_segment: [int(CONFIG.nifty.security_id)],
            CONFIG.india_vix.exchange_segment: [int(CONFIG.india_vix.security_id)],
        }
        if future_ref:
            grouped.setdefault(future_ref.exchange_segment, []).append(future_ref.security_id)
        for item in CONFIG.top7:
            grouped.setdefault(item.exchange_segment, []).append(int(item.security_id))

        quote_response = self.client.market_quote(grouped)
        nifty_quote = self._extract_quote(
            quote_response,
            CONFIG.nifty.exchange_segment,
            CONFIG.nifty.security_id,
        )
        if not nifty_quote:
            raise SnapshotBuildError("NIFTY quote missing from DhanHQ response")

        vix_quote = self._extract_quote(
            quote_response,
            CONFIG.india_vix.exchange_segment,
            CONFIG.india_vix.security_id,
        )
        future_quote = (
            self._extract_quote(
                quote_response,
                future_ref.exchange_segment,
                future_ref.security_id,
            )
            if future_ref
            else None
        )

        heavyweight_quotes: list[dict[str, Any]] = []
        for item in CONFIG.top7:
            quote = self._extract_quote(
                quote_response,
                item.exchange_segment,
                item.security_id,
            )
            if quote:
                heavyweight_quotes.append(
                    {
                        "symbol": item.symbol,
                        "display_name": item.name,
                        "security_id": int(item.security_id),
                        "exchange_segment": item.exchange_segment,
                        **quote,
                    }
                )

        from_date = current - timedelta(days=CONFIG.candle_lookback_days)
        candles_1m_raw = self.client.intraday_candles(
            security_id=CONFIG.nifty.security_id,
            exchange_segment=CONFIG.nifty.exchange_segment,
            instrument=CONFIG.nifty.instrument,
            interval=1,
            from_date=from_date,
            to_date=current,
        )
        candles_15m_raw = self.client.intraday_candles(
            security_id=CONFIG.nifty.security_id,
            exchange_segment=CONFIG.nifty.exchange_segment,
            instrument=CONFIG.nifty.instrument,
            interval=15,
            from_date=from_date,
            to_date=current,
        )
        candles_1m = mark_completed_candles(candles_from_dhan(candles_1m_raw), 1, current)
        candles_3m = mark_completed_candles(
            aggregate_candles(candles_1m.drop(columns=["is_complete"], errors="ignore"), 3),
            3,
            current,
        )
        candles_15m = mark_completed_candles(candles_from_dhan(candles_15m_raw), 15, current)

        quote_age = self._quote_age_seconds(nifty_quote, current)
        latest_1m_age = self._latest_candle_age_seconds(candles_1m, current)
        has_current_day_candle = (
            not candles_1m.empty
            and pd.Timestamp(candles_1m.iloc[-1]["timestamp"]).date() == current.date()
        )
        market_session = classify_market_session(
            current,
            quote_age_seconds=quote_age,
            has_current_day_candle=has_current_day_candle,
        )

        statuses: dict[str, FeedStatus] = {}
        statuses["quotes"] = FeedStatus(
            name="quotes",
            ok=True,
            fetched_at=current,
            age_seconds=quote_age,
            message="One grouped market-quote request",
            use_state=feed_use_state(
                available=True,
                market_session=market_session,
                age_seconds=quote_age,
                max_live_age_seconds=CONFIG.quote_max_age_seconds,
            ),
        )
        statuses["instruments"] = FeedStatus(
            name="instruments",
            ok=len(heavyweight_quotes) == len(CONFIG.top7),
            fetched_at=current,
            message=(
                f"Received {len(heavyweight_quotes)}/{len(CONFIG.top7)} configured "
                "heavyweight quotes"
            ),
            source="Configured Dhan security IDs",
            use_state=(
                "READY"
                if len(heavyweight_quotes) == len(CONFIG.top7)
                else "CAUTION"
            ),
        )
        candle_available = (
            len(candles_1m) >= CONFIG.minimum_one_minute_candles
            and not candles_15m.empty
        )
        statuses["candles"] = FeedStatus(
            name="candles",
            ok=candle_available,
            fetched_at=current,
            age_seconds=latest_1m_age,
            message=(
                f"1m={len(candles_1m)}, derived 3m={len(candles_3m)}, "
                f"native 15m={len(candles_15m)}"
            ),
            use_state=feed_use_state(
                available=candle_available,
                market_session=market_session,
                age_seconds=latest_1m_age,
                max_live_age_seconds=CONFIG.candle_max_age_minutes * 60,
            ),
        )

        expiry: str | None = None
        option_frame = pd.DataFrame()
        try:
            expiries = self.client.expiry_list(
                int(CONFIG.nifty.security_id),
                CONFIG.nifty.exchange_segment,
            )
            active_expiries: list[tuple[object, str]] = []
            for item in expiries:
                try:
                    parsed_expiry = pd.Timestamp(item).date()
                except Exception:
                    continue
                if parsed_expiry >= current.date():
                    active_expiries.append((parsed_expiry, item))
            expiry = min(active_expiries, key=lambda pair: pair[0])[1] if active_expiries else None
            if expiry:
                response = self.client.option_chain(
                    expiry=expiry,
                    underlying_security_id=int(CONFIG.nifty.security_id),
                    segment=CONFIG.nifty.exchange_segment,
                )
                option_spot, full_chain = option_chain_to_frame(response)
                spot = option_spot or float(nifty_quote.get("last_price"))
                option_frame = select_atm_window(
                    full_chain,
                    spot,
                    CONFIG.option_strikes_each_side,
                )
                statuses["option_chain"] = FeedStatus(
                    name="option_chain",
                    ok=not option_frame.empty,
                    fetched_at=current,
                    age_seconds=0.0,
                    message=(
                        f"Expiry {expiry}, {len(option_frame)} CE/PE rows in ATM window; "
                        "response age is not market-tick age"
                    ),
                    use_state=("LIVE" if market_session.is_live else "REFERENCE"),
                )
            else:
                statuses["option_chain"] = FeedStatus(
                    name="option_chain",
                    ok=False,
                    fetched_at=current,
                    message="No active NIFTY expiry returned",
                    use_state="UNAVAILABLE",
                )
        except Exception as exc:
            statuses["option_chain"] = FeedStatus(
                name="option_chain",
                ok=False,
                fetched_at=current,
                message=str(exc),
                use_state="UNAVAILABLE",
            )

        indicators = calculate_indicator_bundle(candles_3m, candles_15m)
        fingerprint = {
            "created_at": current.replace(microsecond=0).isoformat(),
            "market_state": market_session.code,
            "nifty": nifty_quote.get("last_price"),
            "expiry": expiry,
            "last_1m": (
                candles_1m.iloc[-1]["timestamp"].isoformat()
                if not candles_1m.empty
                else None
            ),
            "option_rows": len(option_frame),
            "heavyweights": [
                (item.get("symbol"), item.get("last_price"))
                for item in heavyweight_quotes
            ],
        }
        snapshot_id = hashlib.sha256(
            json.dumps(fingerprint, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]

        return MarketSnapshot(
            snapshot_id=f"SNAP-{snapshot_id}",
            created_at=current,
            market_session=market_session,
            nifty_quote=nifty_quote,
            vix_quote=vix_quote,
            nifty_future_quote=future_quote,
            heavyweight_quotes=heavyweight_quotes,
            candles_1m=candles_1m,
            candles_3m=candles_3m,
            candles_15m=candles_15m,
            indicators=indicators,
            expiry=expiry,
            option_chain=option_frame,
            feed_status=statuses,
            metadata={
                "version": CONFIG.version,
                "read_only": True,
                "live_trading_ready": market_session.is_live
                and all(
                    item.use_state in {"LIVE", "READY"}
                    for item in statuses.values()
                ),
                "top7_configured": list(CONFIG.top7_symbols),
                "vix_resolved": bool(vix_quote),
                "future_resolved": bool(future_quote),
            },
        )
