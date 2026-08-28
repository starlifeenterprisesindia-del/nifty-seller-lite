from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, fields, replace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from analysis.barrier_map import calculate_barrier_map
from analysis.big_player import calculate_big_player_activity
from analysis.candles import (
    aggregate_candles,
    candles_from_dhan,
    exclude_session_from_time,
    mark_completed_candles,
)
from analysis.core_market import calculate_core_market_evidence
from analysis.decision import calculate_final_decision
from analysis.execution_guard import calculate_execution_guard
from analysis.heavyweights import calculate_heavyweight_bundle
from analysis.indicators import calculate_indicator_bundle
from analysis.levels import calculate_levels
from analysis.market_session import classify_market_session, feed_use_state
from analysis.market_context import calculate_market_context
from analysis.market_risk import calculate_vix_context
from analysis.option_chain import option_chain_to_frame, select_atm_window
from analysis.option_intelligence import calculate_option_intelligence
from analysis.patterns import calculate_pattern_evidence
from analysis.price_action import calculate_price_action_bundle
from analysis.position_guardian import calculate_position_guardian
from analysis.pre_touch_barriers import calculate_pre_touch_barriers
from analysis.trade_plan import calculate_trade_plan
from analysis.volume import calculate_volume_bundle
from analysis.history_features import oi_history, futures_vwap, institutional_trends
from config import CONFIG, IST_TIMEZONE
from models import BigPlayerActivity, DisciplineState, FeedStatus, MarketSnapshot, NewsContext, RiskProfile
from services.dhan_client import DhanClient
from services.discipline_store import DisciplineStore
from services.errors import SnapshotBuildError
from services.context_store import MarketContextStore
from services.instrument_master import InstrumentMaster, ResolvedInstrument
from services.option_state_store import OptionStateStore
from services.activity_state_store import ActivityStateStore
from services.news_service import MarketNewsService
from services.recent_quotes import record_quotes
from services.shared_history import bounded


IST = ZoneInfo(IST_TIMEZONE)


class SnapshotService:
    @classmethod
    def background_observer(cls, client, root):
        """Same analysis, separate runtime state; no app journal/cloud configuration."""
        return cls(client,
            option_state_store=OptionStateStore(root / "options.json"),
            activity_state_store=ActivityStateStore(root / "activity.json"),
            discipline_store=DisciplineStore(root / "background_signals.json"),
            context_store=MarketContextStore(root / "background_context.json"),
            recent_quotes_path=str(root / "top9.json"))

    def __init__(
        self,
        client: DhanClient,
        instrument_master: InstrumentMaster | None = None,
        option_state_store: OptionStateStore | None = None,
        context_store: MarketContextStore | None = None,
        discipline_store: DisciplineStore | None = None,
        news_service: MarketNewsService | None = None,
        activity_state_store: ActivityStateStore | None = None,
        recent_quotes_path: str = "data/recent_top9.json",
    ):
        self.client = client
        self.master = instrument_master or InstrumentMaster()
        self.option_state_store = option_state_store or OptionStateStore()
        self.context_store = context_store or MarketContextStore()
        self.discipline_store = discipline_store or DisciplineStore()
        self.activity_state_store = activity_state_store or ActivityStateStore()
        # Kept injectable so unit tests and offline analysis never need public internet.
        self.news_service = news_service
        self.recent_quotes_path = recent_quotes_path

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
    def _parse_quote_timestamp(raw: Any, now: datetime) -> pd.Timestamp | None:
        """Parse Dhan quote timestamps without DD/MM and MM/DD ambiguity.

        Dhan market-feed strings may arrive as ``DD/MM/YYYY HH:MM:SS``. Pandas'
        default month-first inference can silently turn 03/08 into 8 March, making a
        fresh quote appear roughly five months old. We therefore try explicit Dhan
        formats first, then ISO/epoch fallbacks.
        """
        if raw in (None, ""):
            return None
        current = pd.Timestamp(now)
        if current.tzinfo is None:
            current = current.tz_localize(IST_TIMEZONE)
        else:
            current = current.tz_convert(IST_TIMEZONE)
        try:
            if isinstance(raw, (int, float)):
                unit = "ms" if abs(float(raw)) > 10_000_000_000 else "s"
                return pd.to_datetime(raw, unit=unit, utc=True).tz_convert(IST_TIMEZONE)

            text = str(raw).strip()
            if not text:
                return None
            if text.replace(".", "", 1).isdigit():
                number = float(text)
                unit = "ms" if abs(number) > 10_000_000_000 else "s"
                return pd.to_datetime(number, unit=unit, utc=True).tz_convert(IST_TIMEZONE)

            # Time-only values are interpreted for today's IST trading date.
            for fmt in ("%H:%M:%S", "%H:%M:%S.%f"):
                try:
                    parsed_time = datetime.strptime(text, fmt).time()
                    return pd.Timestamp(datetime.combine(current.date(), parsed_time)).tz_localize(
                        IST_TIMEZONE
                    )
                except ValueError:
                    pass

            parsed: pd.Timestamp | None = None
            explicit_formats = (
                "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y %H:%M:%S.%f",
                "%d-%m-%Y %H:%M:%S",
                "%d-%m-%Y %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f",
            )
            for fmt in explicit_formats:
                try:
                    parsed = pd.Timestamp(datetime.strptime(text, fmt))
                    break
                except ValueError:
                    continue
            if parsed is None:
                # ISO year-first values must never be sent through day-first inference.
                if len(text) >= 10 and text[:4].isdigit() and text[4] == "-":
                    parsed = pd.Timestamp(pd.to_datetime(text, errors="raise"))
                else:
                    parsed = pd.Timestamp(
                        pd.to_datetime(text, dayfirst=True, errors="raise")
                    )
            if parsed.tzinfo is None:
                parsed = parsed.tz_localize(IST_TIMEZONE)
            else:
                parsed = parsed.tz_convert(IST_TIMEZONE)
            return parsed
        except Exception:
            return None

    @classmethod
    def _quote_age_seconds(
        cls,
        quote: dict[str, Any] | None,
        now: datetime,
    ) -> float | None:
        if not quote:
            return None
        raw = (
            quote.get("last_trade_time")
            or quote.get("last_traded_time")
            or quote.get("ltt")
        )
        parsed = cls._parse_quote_timestamp(raw, now)
        if parsed is None:
            return None
        current = pd.Timestamp(now)
        if current.tzinfo is None:
            current = current.tz_localize(IST_TIMEZONE)
        else:
            current = current.tz_convert(IST_TIMEZONE)
        delta = (current - parsed).total_seconds()
        # A quote materially in the future signals a timezone/date parse problem.
        if delta < -300:
            return None
        return max(0.0, delta)

    @staticmethod
    def _completed_only(frame: pd.DataFrame) -> pd.DataFrame:
        """Return only bars whose interval has fully closed.

        Dhan may include the currently forming row. Analysis modules already respect the
        ``is_complete`` marker, but the authoritative snapshot and raw audit tables must
        never expose a forming candle under a "completed candles" label.
        """

        if frame is None or frame.empty:
            return frame.copy() if frame is not None else pd.DataFrame()
        if "is_complete" not in frame.columns:
            return frame.iloc[0:0].copy().reset_index(drop=True)
        mask = frame["is_complete"].fillna(False).eq(True)
        return frame.loc[mask].copy().reset_index(drop=True)

    @staticmethod
    def _latest_candle_age_seconds(
        frame: pd.DataFrame,
        now: datetime,
        *,
        interval_minutes: int = 0,
    ) -> float | None:
        """Age from candle close, not from the candle's opening timestamp."""

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
            candle_close = latest + pd.Timedelta(
                minutes=int(max(0, interval_minutes))
            )
            return max(0.0, (current - candle_close).total_seconds())
        except Exception:
            return None

    @staticmethod
    def _positive_number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not pd.notna(number) or number <= 0:
            return None
        return number

    @staticmethod
    def _option_chain_integrity(
        frame: pd.DataFrame,
        *,
        option_spot: float | None,
        nifty_price: float,
    ) -> tuple[bool, str]:
        if frame.empty:
            return False, "Option-chain ATM window is empty"
        required = {"strike", "side", "last_price", "oi", "volume", "is_atm"}
        missing = sorted(required.difference(frame.columns))
        if missing:
            return False, f"Option-chain fields missing: {', '.join(missing)}"
        if len(frame) < CONFIG.option_min_integrity_rows:
            return False, f"Only {len(frame)} option rows returned"

        sides = set(frame["side"].astype(str).str.upper())
        if not {"CE", "PE"}.issubset(sides):
            return False, "Both CE and PE rows are required"
        atm_sides = set(
            frame.loc[frame["is_atm"].fillna(False), "side"].astype(str).str.upper()
        )
        if not {"CE", "PE"}.issubset(atm_sides):
            return False, "ATM CE/PE pair is incomplete"

        positive_prices = pd.to_numeric(frame["last_price"], errors="coerce").fillna(
            0.0
        )
        if int((positive_prices > 0).sum()) < min(4, len(frame)):
            return False, "Too few positive option premiums"

        if option_spot is None or option_spot <= 0:
            return False, "Option-chain underlying spot is invalid"
        tolerance = max(
            CONFIG.option_spot_max_divergence_points,
            nifty_price * CONFIG.option_spot_max_divergence_pct / 100.0,
        )
        divergence = abs(option_spot - nifty_price)
        if divergence > tolerance:
            return (
                False,
                f"Option-chain spot differs from NIFTY by {divergence:.1f} points",
            )
        return True, "CE/PE structure, ATM pair, premiums and spot alignment verified"

    def _resolve_market_references(
        self,
    ) -> tuple[ResolvedInstrument | None, ResolvedInstrument]:
        fallback_vix = ResolvedInstrument(
            symbol=CONFIG.india_vix.symbol,
            security_id=int(CONFIG.india_vix.security_id),
            exchange_segment=CONFIG.india_vix.exchange_segment,
            instrument=CONFIG.india_vix.instrument,
            display_name=CONFIG.india_vix.name,
        )
        try:
            raw_master = self.master.load()
            future = self.master.resolve_nearest_nifty_future(raw_master)
            resolver = getattr(self.master, "resolve_india_vix", None)
            vix = resolver(raw_master) if callable(resolver) else None
            return future, vix or fallback_vix
        except Exception:
            return None, fallback_vix

    def _fetch_candles(
        self,
        *,
        security_id: str | int,
        exchange_segment: str,
        instrument: str,
        from_date: datetime,
        current: datetime,
        include_oi: bool = False,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        raw_1m = self.client.intraday_candles(
            security_id=str(security_id),
            exchange_segment=exchange_segment,
            instrument=instrument,
            interval=1,
            from_date=from_date,
            to_date=current,
            include_oi=include_oi,
        )
        raw_15m = self.client.intraday_candles(
            security_id=str(security_id),
            exchange_segment=exchange_segment,
            instrument=instrument,
            interval=15,
            from_date=from_date,
            to_date=current,
            include_oi=include_oi,
        )
        marked_1m = mark_completed_candles(candles_from_dhan(raw_1m), 1, current)
        candles_1m = self._completed_only(marked_1m)
        marked_3m = mark_completed_candles(
            aggregate_candles(
                candles_1m.drop(columns=["is_complete"], errors="ignore"), 3
            ),
            3,
            current,
        )
        candles_3m = self._completed_only(marked_3m)
        marked_15m = mark_completed_candles(
            candles_from_dhan(raw_15m), 15, current
        )
        candles_15m = self._completed_only(marked_15m)
        return candles_1m, candles_3m, candles_15m

    def build(
        self,
        now: datetime | None = None,
        risk_profile: RiskProfile | None = None,
    ) -> MarketSnapshot:
        if now is None:
            current = datetime.now(IST)
        elif now.tzinfo:
            current = now.astimezone(IST)
        else:
            current = now.replace(tzinfo=IST)

        profile = risk_profile or RiskProfile(
            capital_rupees=CONFIG.risk_default_capital,
            risk_pct=CONFIG.risk_default_pct,
            lot_size=CONFIG.risk_default_lot_size,
            max_lots_cap=CONFIG.risk_default_max_lots,
            target_capture_pct=CONFIG.risk_default_target_capture_pct,
            stop_loss_pct=CONFIG.risk_default_stop_loss_pct,
            entry_start=CONFIG.risk_default_entry_start,
            entry_end=CONFIG.risk_default_entry_end,
            forced_exit=CONFIG.risk_default_forced_exit,
        )

        future_ref, vix_ref = self._resolve_market_references()
        # NIFTY and INDIA VIX share IDX_I. Incremental construction prevents
        # duplicate dictionary keys from overwriting an instrument.
        grouped: dict[str, list[int]] = {}
        grouped.setdefault(CONFIG.nifty.exchange_segment, []).append(
            int(CONFIG.nifty.security_id)
        )
        grouped.setdefault(vix_ref.exchange_segment, []).append(vix_ref.security_id)
        if future_ref:
            grouped.setdefault(future_ref.exchange_segment, []).append(
                future_ref.security_id
            )
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
        nifty_price = self._positive_number(nifty_quote.get("last_price"))
        if nifty_price is None:
            raise SnapshotBuildError("NIFTY quote has an invalid last price")
        vix_quote = self._extract_quote(
            quote_response,
            vix_ref.exchange_segment,
            vix_ref.security_id,
        )
        future_quote = (
            self._extract_quote(
                quote_response, future_ref.exchange_segment, future_ref.security_id
            )
            if future_ref
            else None
        )

        heavyweight_quotes: list[dict[str, Any]] = []
        for item in CONFIG.top7:
            quote = self._extract_quote(
                quote_response, item.exchange_segment, item.security_id
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
        candles_1m, candles_3m, candles_15m = self._fetch_candles(
            security_id=CONFIG.nifty.security_id,
            exchange_segment=CONFIG.nifty.exchange_segment,
            instrument=CONFIG.nifty.instrument,
            from_date=from_date,
            current=current,
        )
        # Closing-auction indicative/final-close rows remain broker reference data,
        # but must not manufacture EMA/MACD/RSI or swing evidence.
        candles_1m = exclude_session_from_time(candles_1m, CONFIG.cas_start)
        candles_3m = exclude_session_from_time(candles_3m, CONFIG.cas_start)
        candles_15m = exclude_session_from_time(candles_15m, CONFIG.cas_start)

        future_candles_1m = pd.DataFrame()
        future_candles_3m = pd.DataFrame()
        future_candles_15m = pd.DataFrame()
        future_candle_error: str | None = None
        if future_ref:
            try:
                (
                    future_candles_1m,
                    future_candles_3m,
                    future_candles_15m,
                ) = self._fetch_candles(
                    security_id=future_ref.security_id,
                    exchange_segment=future_ref.exchange_segment,
                    instrument=future_ref.instrument,
                    from_date=from_date,
                    current=current,
                    include_oi=True,
                )
            except Exception as exc:
                future_candle_error = str(exc)

        quote_age = self._quote_age_seconds(nifty_quote, current)
        latest_1m_age = self._latest_candle_age_seconds(
            candles_1m, current, interval_minutes=1
        )
        has_current_day_candle = (
            not candles_1m.empty
            and pd.Timestamp(candles_1m.iloc[-1]["timestamp"]).date() == current.date()
        )
        latest_spot_close = (
            self._positive_number(candles_1m.iloc[-1].get("close"))
            if not candles_1m.empty
            else None
        )
        quote_candle_divergence = (
            abs(float(nifty_price) - float(latest_spot_close))
            if latest_spot_close is not None
            else None
        )
        quote_candle_limit = max(
            CONFIG.quote_candle_max_divergence_points,
            float(nifty_price) * CONFIG.quote_candle_max_divergence_pct / 100.0,
        )
        quote_candle_aligned = (
            quote_candle_divergence is not None
            and quote_candle_divergence <= quote_candle_limit
        )
        market_session = classify_market_session(
            current,
            quote_age_seconds=quote_age,
            has_current_day_candle=has_current_day_candle,
            candle_age_seconds=latest_1m_age,
            quote_candle_aligned=quote_candle_aligned,
        )

        vix_age = self._quote_age_seconds(vix_quote, current)
        fresh_heavyweight_quotes = [
            quote
            for quote in heavyweight_quotes
            if (age := self._quote_age_seconds(quote, current)) is not None
            and age <= CONFIG.context_quote_max_age_seconds
        ]
        analysis_heavyweight_quotes = (
            fresh_heavyweight_quotes if market_session.is_live else heavyweight_quotes
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
            message=f"Received {len(heavyweight_quotes)}/{len(CONFIG.top7)} configured heavyweight quotes",
            source="Configured Dhan security IDs",
            use_state="READY"
            if len(heavyweight_quotes) == len(CONFIG.top7)
            else "CAUTION",
        )
        statuses["heavyweights"] = FeedStatus(
            name="heavyweights",
            ok=len(analysis_heavyweight_quotes) == len(CONFIG.top7),
            fetched_at=current,
            message=(
                f"Usable Top-9 quotes {len(analysis_heavyweight_quotes)}/{len(CONFIG.top7)}"
            ),
            source="Grouped Dhan market quote",
            use_state=(
                "LIVE"
                if market_session.is_live
                and len(analysis_heavyweight_quotes) == len(CONFIG.top7)
                else "CAUTION"
                if market_session.is_live
                else "REFERENCE"
            ),
        )
        statuses["vix"] = FeedStatus(
            name="vix",
            ok=self._positive_number((vix_quote or {}).get("last_price")) is not None,
            fetched_at=current,
            age_seconds=vix_age,
            message="India VIX grouped quote",
            source="DhanHQ",
            use_state=feed_use_state(
                available=self._positive_number((vix_quote or {}).get("last_price"))
                is not None,
                market_session=market_session,
                age_seconds=vix_age,
                max_live_age_seconds=CONFIG.context_quote_max_age_seconds,
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
                f"NIFTY 1m={len(candles_1m)}, derived 3m={len(candles_3m)}, "
                f"native 15m={len(candles_15m)}; quote/candle gap "
                f"{quote_candle_divergence:.1f} pts (limit {quote_candle_limit:.1f})"
                if quote_candle_divergence is not None
                else "NIFTY candle/quote alignment unavailable"
            ),
            use_state=feed_use_state(
                available=candle_available,
                market_session=market_session,
                age_seconds=latest_1m_age,
                max_live_age_seconds=CONFIG.candle_max_age_minutes * 60,
            ),
        )
        future_candle_available = (
            not future_candles_3m.empty and not future_candles_15m.empty
        )
        statuses["future_volume"] = FeedStatus(
            name="future_volume",
            ok=future_candle_available,
            fetched_at=current,
            age_seconds=self._latest_candle_age_seconds(
                future_candles_1m, current, interval_minutes=1
            ),
            message=(
                f"NIFTY future 1m={len(future_candles_1m)}, 3m={len(future_candles_3m)}, 15m={len(future_candles_15m)}"
                if future_candle_available
                else future_candle_error or "Nearest NIFTY future unavailable"
            ),
            source="DhanHQ NIFTY FUTIDX candles",
            use_state=(
                feed_use_state(
                    available=True,
                    market_session=market_session,
                    age_seconds=self._latest_candle_age_seconds(
                        future_candles_1m, current, interval_minutes=1
                    ),
                    max_live_age_seconds=CONFIG.candle_max_age_minutes * 60,
                )
                if future_candle_available
                else "UNAVAILABLE"
            ),
        )

        expiry: str | None = None
        option_frame = pd.DataFrame()
        validated_option_frame = pd.DataFrame()
        full_option_frame = pd.DataFrame()
        option_spot: float | None = None
        option_integrity_ok = False
        option_integrity_message = "Option chain unavailable"
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
            expiry = (
                min(active_expiries, key=lambda pair: pair[0])[1]
                if active_expiries
                else None
            )
            if expiry:
                response = self.client.option_chain(
                    expiry=expiry,
                    underlying_security_id=int(CONFIG.nifty.security_id),
                    segment=CONFIG.nifty.exchange_segment,
                )
                option_spot, full_chain = option_chain_to_frame(response)
                full_option_frame = full_chain.copy()
                spot = option_spot or nifty_price
                option_frame = select_atm_window(
                    full_chain, spot, CONFIG.option_strikes_each_side
                )
                option_integrity_ok, option_integrity_message = (
                    self._option_chain_integrity(
                        option_frame,
                        option_spot=option_spot,
                        nifty_price=nifty_price,
                    )
                )
                if option_integrity_ok:
                    validated_option_frame = option_frame.copy()
                statuses["option_chain"] = FeedStatus(
                    name="option_chain",
                    ok=option_integrity_ok,
                    fetched_at=current,
                    age_seconds=None,
                    message=(
                        f"Expiry {expiry}, {len(option_frame)} CE/PE rows; "
                        f"{option_integrity_message}. Request-time freshness only."
                    ),
                    use_state=(
                        "LIVE"
                        if option_integrity_ok and market_session.is_live
                        else "REFERENCE"
                        if option_integrity_ok
                        else "UNAVAILABLE"
                    ),
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
        price_action = calculate_price_action_bundle(candles_3m, candles_15m)
        current_price = nifty_price
        levels = calculate_levels(candles_3m, candles_15m, indicators, current_price)
        volume = calculate_volume_bundle(
            future_candles_3m,
            future_candles_15m,
            candles_3m,
            candles_15m,
        )
        marked_5m = mark_completed_candles(
            aggregate_candles(
                candles_1m.drop(columns=["is_complete"], errors="ignore"), 5
            ),
            5,
            current,
        )
        candles_5m = self._completed_only(marked_5m)
        patterns = calculate_pattern_evidence(
            candles_3m,
            levels,
            volume,
            candles_5m=candles_5m,
            candles_15m=candles_15m,
        )
        core_evidence = calculate_core_market_evidence(
            price_action,
            indicators,
            levels,
            volume,
            market_session,
            future_volume_live=statuses["future_volume"].use_state == "LIVE",
        )
        shared = {}
        history_message = "Local observations"
        if market_session.is_live and callable(getattr(self.client, "market_history", None)):
            try:
                shared = self.client.market_history(expiry or "")
                if not isinstance(shared, dict):
                    raise ValueError("Invalid history response")
                if not all(isinstance(shared.get(k, []), list) for k in ("top9", "options")):
                    raise ValueError("Invalid history observations")
                history_message = "Railway + local observations; timestamps checked"
            except Exception:
                shared = {}
                history_message = "Railway history unavailable; local observations only"
        top9_history = []
        if market_session.is_live and statuses["heavyweights"].use_state == "LIVE":
            try:
                top9_history = record_quotes(analysis_heavyweight_quotes, nifty_quote, current, path=self.recent_quotes_path)
            except OSError:
                top9_history = []  # no fabricated recent direction if storage unavailable
        top9_history = bounded(shared.get("top9", []) + top9_history, current, "at")
        statuses["analysis_history"] = FeedStatus(
            name="analysis_history", ok=bool(top9_history or shared.get("options")),
            fetched_at=current, age_seconds=None,
            message=f"{history_message}; Top-9 samples={len(top9_history)}; oldest={top9_history[0]['at'] if top9_history else 'missing'}; latest={top9_history[-1]['at'] if top9_history else 'missing'}",
            source="Timestamp-checked market observations", use_state="READY" if top9_history or shared.get("options") else "WARMING UP")
        heavyweights = calculate_heavyweight_bundle(
            analysis_heavyweight_quotes,
            current,
            nifty_quote=nifty_quote,
            reference_only=not market_session.is_live,
            history=top9_history,
        )
        vix_for_analysis = (
            vix_quote
            if not market_session.is_live or statuses["vix"].use_state == "LIVE"
            else None
        )
        vix_context = calculate_vix_context(vix_for_analysis, current)

        option_history: list[dict[str, Any]] = []
        option_state_snapshot: dict[str, Any] = {
            "captured_at": current.isoformat(),
            "expiry": expiry or "",
            "spot": float(current_price) if current_price is not None else None,
            "fingerprint": "",
            "rows": [],
        }
        option_state_error: str | None = None
        if expiry and not validated_option_frame.empty:
            try:
                option_history = self.option_state_store.load_session(
                    captured_at=current, expiry=expiry
                )
                option_state_snapshot = self.option_state_store.make_snapshot(
                    captured_at=current,
                    expiry=expiry,
                    spot=float(current_price) if current_price is not None else None,
                    frame=validated_option_frame,
                    vix=self._positive_number((vix_quote or {}).get("last_price")),
                )
            except Exception as exc:
                option_state_error = str(exc)
                option_history = []

        option_history = bounded(shared.get("options", []) + option_history, current, "captured_at", expiry)
        option_intelligence = calculate_option_intelligence(
            current_frame=validated_option_frame,
            spot=float(current_price) if current_price is not None else 0.0,
            expiry=expiry,
            captured_at=current,
            history=option_history,
            current_snapshot=option_state_snapshot,
            is_live=market_session.is_live,
        )
        state_appended = False
        if (
            market_session.is_live
            and expiry
            and statuses["option_chain"].use_state == "LIVE"
            and not validated_option_frame.empty
            and option_state_error is None
        ):
            try:
                _, state_appended = self.option_state_store.append(
                    option_state_snapshot
                )
                if state_appended:
                    option_intelligence = calculate_option_intelligence(
                        current_frame=validated_option_frame,
                        spot=float(current_price) if current_price is not None else 0.0,
                        expiry=expiry,
                        captured_at=current,
                        history=option_history,
                        current_snapshot=option_state_snapshot,
                        is_live=True,
                    )
            except Exception as exc:
                option_state_error = str(exc)

        statuses["option_state"] = FeedStatus(
            name="option_state",
            ok=(
                option_state_error is None
                and bool(expiry)
                and not validated_option_frame.empty
            ),
            fetched_at=current,
            message=(
                f"Same-day bounded history: {len(option_history)} prior snapshot(s); "
                f"current {'stored' if state_appended else 'not stored'}"
                if option_state_error is None
                and expiry
                and not validated_option_frame.empty
                else option_state_error or "Option state unavailable"
            ),
            source="Local atomic option-state file",
            use_state=(
                "READY"
                if market_session.is_live
                and option_state_error is None
                and not validated_option_frame.empty
                else "REFERENCE"
                if not market_session.is_live
                and option_state_error is None
                and not validated_option_frame.empty
                else "UNAVAILABLE"
            ),
        )

        context_error: str | None = None
        try:
            context_entries = self.context_store.load()
        except Exception as exc:
            context_entries = []
            context_error = str(exc)
        institutional_context, event_risk = calculate_market_context(
            context_entries, current.date()
        )
        statuses["market_context"] = FeedStatus(
            name="market_context",
            ok=context_error is None,
            fetched_at=current,
            message=(
                f"FII/DII observations={institutional_context.observations}; "
                f"event risk={event_risk.level}"
                if context_error is None
                else context_error
            ),
            source="Primary + mirror bounded market-context journal",
            use_state=(
                "READY"
                if context_error is None and institutional_context.status != "MISSING"
                else "OPTIONAL / MISSING"
                if context_error is None
                else "UNAVAILABLE"
            ),
        )

        if self.news_service is None:
            news_context = NewsContext(
                as_of=current,
                headlines=(),
                bias="NEUTRAL",
                risk_level="NONE",
                summary="Live news service is not enabled in this runtime.",
                newest_age_minutes=None,
                status="UNAVAILABLE",
                source="Disabled",
            )
            news_error = None
        else:
            try:
                news_context = self.news_service.fetch(current)
                news_error = None
            except Exception as exc:
                news_error = str(exc)
                news_context = NewsContext(
                    as_of=current,
                    headlines=(),
                    bias="NEUTRAL",
                    risk_level="NONE",
                    summary="Live news fetch failed safely; news weight is zero.",
                    newest_age_minutes=None,
                    status="UNAVAILABLE",
                    source="Public RSS",
                )
        statuses["news"] = FeedStatus(
            name="news",
            ok=news_context.status in {"READY", "NO RECENT NEWS"},
            fetched_at=current,
            age_seconds=(
                news_context.newest_age_minutes * 60.0
                if news_context.newest_age_minutes is not None
                else None
            ),
            message=(news_error or news_context.summary)[:500],
            source=news_context.source,
            use_state=(
                "LIVE"
                if news_context.status == "READY"
                else "READY / NO RECENT"
                if news_context.status == "NO RECENT NEWS"
                else "UNAVAILABLE"
            ),
        )

        pre_touch_barriers = calculate_pre_touch_barriers(
            levels=levels,
            options=option_intelligence,
            spot=float(current_price) if current_price is not None else 0.0,
        )
        barrier_map = calculate_barrier_map(
            spot=float(current_price) if current_price is not None else 0.0,
            captured_at=current,
            market_session=market_session,
            expiry=expiry,
            candles_1m=candles_1m,
            levels=levels,
            indicators=indicators,
            price_action=price_action,
            core=core_evidence,
            volume=volume,
            options=option_intelligence,
            heavyweights=heavyweights,
            vix=vix_context,
            option_history=option_history,
        )

        recent_vix = barrier_map.market_speed.vix_change_5m_pct
        if vix_context.status == "READY" and recent_vix is not None:
            vix_context = replace(vix_context, movement="RISING FAST" if recent_vix >= 3 else "RISING" if recent_vix > .5 else "FALLING" if recent_vix < -.5 else "STABLE")
        activity_history = self.activity_state_store.load(current)
        activity_candles = future_candles_1m
        if not activity_candles.empty and "is_complete" in activity_candles.columns:
            activity_candles = activity_candles[
                activity_candles["is_complete"].fillna(False).astype(bool)
            ]
        activity_observation_key = (
            str(activity_candles.iloc[-1]["timestamp"])
            if not activity_candles.empty
            else current.replace(second=0, microsecond=0).isoformat()
        )
        activity_spot = (
            float(activity_candles.iloc[-1]["close"])
            if not activity_candles.empty
            else float(current_price)
            if current_price is not None
            else None
        )
        big_player_activity = calculate_big_player_activity(
            as_of=current,
            market_session=market_session,
            volume=volume,
            future_candles_1m=future_candles_1m,
            options=option_intelligence,
            heavyweights=heavyweights,
            barrier_map=barrier_map,
            core=core_evidence,
            history=activity_history,
            observation_key=activity_observation_key,
            spot_candles_1m=candles_1m,
        )
        if market_session.is_live:
            self.activity_state_store.append(
                current,
                direction=big_player_activity.direction,
                score=big_player_activity.score,
                state=big_player_activity.state,
                observation_key=activity_observation_key,
                spot=activity_spot,
                activity_payload=asdict(big_player_activity),
            )
        elif activity_history:
            frozen_payload = activity_history[-1].get("activity_payload")
            if isinstance(frozen_payload, dict):
                allowed = {item.name for item in fields(BigPlayerActivity)}
                safe_payload = {key: value for key, value in frozen_payload.items() if key in allowed}
                try:
                    frozen = BigPlayerActivity(**safe_payload)
                    big_player_activity = replace(
                        frozen,
                        status="REFERENCE ONLY",
                        cautions=tuple(dict.fromkeys((*frozen.cautions, "Market band hai; last live activity freeze ki gayi")))[:3],
                        price_shock_state=big_player_activity.price_shock_state,
                        price_shock_points=big_player_activity.price_shock_points,
                        frozen_after_close=True,
                    )
                except (TypeError, ValueError):
                    pass

        discipline_error: str | None = None
        signal_appended = False
        try:
            discipline_state = self.discipline_store.load(current.date())
        except Exception as exc:
            discipline_error = str(exc)
            discipline_state = DisciplineState(
                session_date=current.date().isoformat(),
                trades_taken=0,
                day_locked=False,
                last_outcome="",
                last_action="",
                signal_history=(),
                status="UNAVAILABLE",
            )

        decision = calculate_final_decision(
            core=core_evidence,
            options=option_intelligence,
            heavyweights=heavyweights,
            vix=vix_context,
            levels=levels,
            institutional=institutional_context,
            event_risk=event_risk,
            news=news_context,
            market_session=market_session,
            quote_live=statuses["quotes"].use_state == "LIVE",
            candles_live=statuses["candles"].use_state == "LIVE",
            option_chain_live=statuses["option_chain"].use_state == "LIVE",
            price_action=price_action,
            volume=volume,
            patterns=patterns,
            big_player=big_player_activity,
            signal_history=discipline_state.signal_history,
            as_of=current,
            current_price=(float(current_price) if current_price is not None else None),
        )

        trade_plan = calculate_trade_plan(
            frame=validated_option_frame,
            spot=float(current_price) if current_price is not None else 0.0,
            expiry=expiry,
            levels=levels,
            options=option_intelligence,
            decision=decision,
            market_session=market_session,
            indicators=indicators,
            risk_profile=profile,
        )

        fresh_signal = (
            market_session.is_live
            and statuses["quotes"].use_state == "LIVE"
            and statuses["candles"].use_state == "LIVE"
            and statuses["option_chain"].use_state == "LIVE"
        )
        if discipline_error is None and fresh_signal:
            try:
                discipline_state, signal_appended = self.discipline_store.append_signal(
                    captured_at=current,
                    action=decision.final_action,
                    candidate_action=decision.instant_action,
                    execution_status=decision.execution_status,
                    # Legacy history keys mean bearish and bullish directional edge.
                    ce_score=max(decision.ce_sell.score, decision.pe_buy.score),
                    pe_score=max(decision.pe_sell.score, decision.ce_buy.score),
                    condor_score=decision.iron_condor.score,
                    wait_need=decision.wait_need.score,
                    signal_state=decision.signal_state,
                    market_direction=decision.market_direction,
                    fake_move_risk=decision.outlook.fake_move_risk,
                    spot=(float(current_price) if current_price is not None else None),
                )
            except Exception as exc:
                discipline_error = str(exc)

        statuses["discipline_state"] = FeedStatus(
            name="discipline_state",
            ok=discipline_error is None,
            fetched_at=current,
            message=(
                f"One-trade state: trades={discipline_state.trades_taken}; "
                f"signals={len(discipline_state.signal_history)}; "
                f"memory={decision.outlook.signal_memory}; "
                f"current {'stored' if signal_appended else 'not stored'}"
                if discipline_error is None
                else discipline_error
            ),
            source="Local atomic discipline-state and signal-memory file",
            use_state=(
                "READY"
                if discipline_error is None and market_session.is_live
                else "REFERENCE"
                if discipline_error is None
                else "UNAVAILABLE"
            ),
        )

        execution_guard = calculate_execution_guard(
            decision=decision,
            trade_plan=trade_plan,
            market_session=market_session,
            option_intelligence=option_intelligence,
            price_action=price_action,
            risk_profile=profile,
            discipline_state=discipline_state,
            big_player=big_player_activity,
            feed_status=statuses,
            as_of=current,
        )

        position_guardian = calculate_position_guardian(
            discipline_state=discipline_state,
            option_chain=full_option_frame,
            current_expiry=expiry,
            current_spot=float(current_price) if current_price is not None else None,
            market_session=market_session,
            option_chain_live=statuses["option_chain"].use_state == "LIVE",
            as_of=current,
            completed_spot_close=indicators.three_minute.close if indicators.three_minute.status == "READY" else None,
        )
        statuses["position_guardian"] = FeedStatus(
            name="position_guardian",
            ok=position_guardian.status not in {"DATA BLOCKED"},
            fetched_at=current,
            message=(
                f"{position_guardian.instruction}; legs={len(position_guardian.legs)}"
            ),
            source="Current full option-chain snapshot + local manual trade record",
            use_state=(
                "LIVE"
                if market_session.is_live
                and position_guardian.status not in {"DATA BLOCKED"}
                else "REFERENCE"
                if not market_session.is_live
                else "UNAVAILABLE"
            ),
        )

        fingerprint = {
            "created_at": current.replace(microsecond=0).isoformat(),
            "market_state": market_session.code,
            "nifty": nifty_quote.get("last_price"),
            "expiry": expiry,
            "last_1m": candles_1m.iloc[-1]["timestamp"].isoformat()
            if not candles_1m.empty
            else None,
            "last_future_1m": (
                future_candles_1m.iloc[-1]["timestamp"].isoformat()
                if not future_candles_1m.empty
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
            future_candles_1m=future_candles_1m,
            future_candles_3m=future_candles_3m,
            future_candles_15m=future_candles_15m,
            indicators=indicators,
            price_action=price_action,
            levels=levels,
            volume=volume,
            core_evidence=core_evidence,
            option_intelligence=option_intelligence,
            heavyweights=heavyweights,
            vix_context=vix_context,
            institutional_context=institutional_context,
            event_risk=event_risk,
            news_context=news_context,
            pre_touch_barriers=pre_touch_barriers,
            barrier_map=barrier_map,
            decision=decision,
            trade_plan=trade_plan,
            execution_guard=execution_guard,
            position_guardian=position_guardian,
            risk_profile=profile,
            discipline_state=discipline_state,
            expiry=expiry,
            option_chain=option_frame,
            feed_status=statuses,
            big_player_activity=big_player_activity,
            patterns=patterns,
            metadata={
                "version": CONFIG.version,
                "history_analytics": {
                    "oi": oi_history(option_history, option_state_snapshot, live=market_session.is_live and statuses["option_chain"].use_state == "LIVE"),
                    "vwap": futures_vwap(future_candles_1m, current),
                    "institutions": institutional_trends(context_entries, current.date()),
                    "mode": "OBSERVATION ONLY — existing OI votes unchanged; no automatic training",
                },
                "read_only": True,
                "live_trading_ready": market_session.is_live
                and statuses["quotes"].use_state == "LIVE"
                and statuses["candles"].use_state == "LIVE"
                and statuses["option_chain"].use_state == "LIVE",
                "top7_configured": list(CONFIG.top7_symbols),
                "vix_resolved": bool(vix_quote),
                "vix_security_id": vix_ref.security_id,
                "future_resolved": bool(future_quote),
                "future_security_id": future_ref.security_id if future_ref else None,
                "future_expiry": future_ref.expiry if future_ref else None,
                "future_volume_resolved": future_candle_available,
                "option_state_prior_snapshots": len(option_history),
                "option_state_current_stored": state_appended,
                "top7_weight_date": CONFIG.top7_weight_date,
                "strategy_scores_enabled": True,
                "decision_engine": "analysis.decision.calculate_final_decision",
                "pre_touch_engine": "analysis.pre_touch_barriers.calculate_pre_touch_barriers",
                "pre_touch_status": pre_touch_barriers.status,
                "barrier_map_engine": "analysis.barrier_map.calculate_barrier_map",
                "barrier_map_status": barrier_map.status,
                "big_player_engine": "analysis.big_player.calculate_big_player_activity",
                "big_player_status": big_player_activity.status,
                "news_status": news_context.status,
                "trade_plan_engine": "analysis.trade_plan.calculate_trade_plan",
                "trade_plan_status": trade_plan.status,
                "execution_guard_engine": "analysis.execution_guard.calculate_execution_guard",
                "execution_guard_status": execution_guard.status,
                "position_guardian_engine": "analysis.position_guardian.calculate_position_guardian",
                "position_guardian_status": position_guardian.status,
                "discipline_signal_appended": signal_appended,
                "discipline_state_status": discipline_state.status,
            },
        )
