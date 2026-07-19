from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from analysis.candles import (
    aggregate_candles,
    candles_from_dhan,
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
from analysis.price_action import calculate_price_action_bundle
from analysis.trade_plan import calculate_trade_plan
from analysis.volume import calculate_volume_bundle
from config import CONFIG, IST_TIMEZONE
from models import DisciplineState, FeedStatus, MarketSnapshot, RiskProfile
from services.dhan_client import DhanClient
from services.discipline_store import DisciplineStore
from services.errors import SnapshotBuildError
from services.context_store import MarketContextStore
from services.instrument_master import InstrumentMaster, ResolvedInstrument
from services.option_state_store import OptionStateStore


IST = ZoneInfo(IST_TIMEZONE)


class SnapshotService:
    def __init__(
        self,
        client: DhanClient,
        instrument_master: InstrumentMaster | None = None,
        option_state_store: OptionStateStore | None = None,
        context_store: MarketContextStore | None = None,
        discipline_store: DisciplineStore | None = None,
    ):
        self.client = client
        self.master = instrument_master or InstrumentMaster()
        self.option_state_store = option_state_store or OptionStateStore()
        self.context_store = context_store or MarketContextStore()
        self.discipline_store = discipline_store or DisciplineStore()

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
                parsed = pd.to_datetime(raw, unit=unit, utc=True).tz_convert(
                    IST_TIMEZONE
                )
            else:
                parsed = pd.to_datetime(raw)
            if parsed.tzinfo is None:
                parsed = parsed.tz_localize(IST_TIMEZONE)
            else:
                parsed = parsed.tz_convert(IST_TIMEZONE)
            return max(0.0, (pd.Timestamp(now) - parsed).total_seconds())
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
        candles_1m = mark_completed_candles(candles_from_dhan(raw_1m), 1, current)
        candles_3m = mark_completed_candles(
            aggregate_candles(
                candles_1m.drop(columns=["is_complete"], errors="ignore"), 3
            ),
            3,
            current,
        )
        candles_15m = mark_completed_candles(candles_from_dhan(raw_15m), 15, current)
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
            message=f"Received {len(heavyweight_quotes)}/{len(CONFIG.top7)} configured heavyweight quotes",
            source="Configured Dhan security IDs",
            use_state="READY"
            if len(heavyweight_quotes) == len(CONFIG.top7)
            else "CAUTION",
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
            message=f"NIFTY 1m={len(candles_1m)}, derived 3m={len(candles_3m)}, native 15m={len(candles_15m)}",
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
            age_seconds=self._latest_candle_age_seconds(future_candles_1m, current),
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
                        future_candles_1m, current
                    ),
                    max_live_age_seconds=CONFIG.candle_max_age_minutes * 60,
                )
                if future_candle_available
                else "UNAVAILABLE"
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
                spot = option_spot or float(nifty_quote.get("last_price"))
                option_frame = select_atm_window(
                    full_chain, spot, CONFIG.option_strikes_each_side
                )
                statuses["option_chain"] = FeedStatus(
                    name="option_chain",
                    ok=not option_frame.empty,
                    fetched_at=current,
                    age_seconds=0.0,
                    message=f"Expiry {expiry}, {len(option_frame)} CE/PE rows in ATM window; response age is not market-tick age",
                    use_state="LIVE" if market_session.is_live else "REFERENCE",
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
        current_price = nifty_quote.get("last_price")
        levels = calculate_levels(candles_3m, candles_15m, indicators, current_price)
        volume = calculate_volume_bundle(
            future_candles_3m,
            future_candles_15m,
            candles_3m,
            candles_15m,
        )
        core_evidence = calculate_core_market_evidence(
            price_action,
            indicators,
            levels,
            volume,
            market_session,
        )
        heavyweights = calculate_heavyweight_bundle(heavyweight_quotes, current)
        vix_context = calculate_vix_context(vix_quote, current)

        option_history: list[dict[str, Any]] = []
        option_state_snapshot: dict[str, Any] = {
            "captured_at": current.isoformat(),
            "expiry": expiry or "",
            "spot": float(current_price) if current_price is not None else None,
            "fingerprint": "",
            "rows": [],
        }
        option_state_error: str | None = None
        if expiry and not option_frame.empty:
            try:
                option_history = self.option_state_store.load_session(
                    captured_at=current, expiry=expiry
                )
                option_state_snapshot = self.option_state_store.make_snapshot(
                    captured_at=current,
                    expiry=expiry,
                    spot=float(current_price) if current_price is not None else None,
                    frame=option_frame,
                )
            except Exception as exc:
                option_state_error = str(exc)
                option_history = []

        option_intelligence = calculate_option_intelligence(
            current_frame=option_frame,
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
            and not option_frame.empty
            and option_state_error is None
        ):
            try:
                _, state_appended = self.option_state_store.append(
                    option_state_snapshot
                )
                if state_appended:
                    option_intelligence = calculate_option_intelligence(
                        current_frame=option_frame,
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
            ok=option_state_error is None and bool(expiry) and not option_frame.empty,
            fetched_at=current,
            message=(
                f"Same-day bounded history: {len(option_history)} prior snapshot(s); "
                f"current {'stored' if state_appended else 'not stored'}"
                if option_state_error is None and expiry and not option_frame.empty
                else option_state_error or "Option state unavailable"
            ),
            source="Local atomic option-state file",
            use_state=(
                "READY"
                if market_session.is_live and option_state_error is None
                else "REFERENCE"
                if not market_session.is_live and option_state_error is None
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
            source="Local bounded market-context journal",
            use_state=(
                "READY"
                if context_error is None and institutional_context.status != "MISSING"
                else "OPTIONAL / MISSING"
                if context_error is None
                else "UNAVAILABLE"
            ),
        )

        decision = calculate_final_decision(
            core=core_evidence,
            options=option_intelligence,
            heavyweights=heavyweights,
            vix=vix_context,
            levels=levels,
            institutional=institutional_context,
            event_risk=event_risk,
            market_session=market_session,
            quote_live=statuses["quotes"].use_state == "LIVE",
            candles_live=statuses["candles"].use_state == "LIVE",
            option_chain_live=statuses["option_chain"].use_state == "LIVE",
        )

        trade_plan = calculate_trade_plan(
            frame=option_frame,
            spot=float(current_price) if current_price is not None else 0.0,
            expiry=expiry,
            levels=levels,
            options=option_intelligence,
            decision=decision,
            market_session=market_session,
        )

        discipline_error: str | None = None
        signal_appended = False
        try:
            discipline_state = self.discipline_store.load(current.date())
            fresh_signal = (
                market_session.is_live
                and statuses["quotes"].use_state == "LIVE"
                and statuses["candles"].use_state == "LIVE"
                and statuses["option_chain"].use_state == "LIVE"
            )
            if fresh_signal:
                discipline_state, signal_appended = self.discipline_store.append_signal(
                    captured_at=current,
                    action=decision.final_action,
                    execution_status=decision.execution_status,
                )
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

        statuses["discipline_state"] = FeedStatus(
            name="discipline_state",
            ok=discipline_error is None,
            fetched_at=current,
            message=(
                f"One-trade state: trades={discipline_state.trades_taken}; "
                f"signals={len(discipline_state.signal_history)}; "
                f"current {'stored' if signal_appended else 'not stored'}"
                if discipline_error is None
                else discipline_error
            ),
            source="Local atomic discipline-state file",
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
            price_action=price_action,
            risk_profile=profile,
            discipline_state=discipline_state,
            feed_status=statuses,
            as_of=current,
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
            decision=decision,
            trade_plan=trade_plan,
            execution_guard=execution_guard,
            risk_profile=profile,
            discipline_state=discipline_state,
            expiry=expiry,
            option_chain=option_frame,
            feed_status=statuses,
            metadata={
                "version": CONFIG.version,
                "read_only": True,
                "live_trading_ready": market_session.is_live
                and statuses["quotes"].use_state == "LIVE"
                and statuses["candles"].use_state == "LIVE",
                "top7_configured": list(CONFIG.top7_symbols),
                "vix_resolved": bool(vix_quote),
                "vix_security_id": vix_ref.security_id,
                "future_resolved": bool(future_quote),
                "future_volume_resolved": future_candle_available,
                "option_state_prior_snapshots": len(option_history),
                "option_state_current_stored": state_appended,
                "top7_weight_date": CONFIG.top7_weight_date,
                "strategy_scores_enabled": True,
                "decision_engine": "analysis.decision.calculate_final_decision",
                "trade_plan_engine": "analysis.trade_plan.calculate_trade_plan",
                "trade_plan_status": trade_plan.status,
                "execution_guard_engine": "analysis.execution_guard.calculate_execution_guard",
                "execution_guard_status": execution_guard.status,
                "discipline_signal_appended": signal_appended,
                "discipline_state_status": discipline_state.status,
            },
        )
