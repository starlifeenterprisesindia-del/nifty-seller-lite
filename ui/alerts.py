from __future__ import annotations

import io
import json
import math
import struct
import wave
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components

from analysis.alerts import (
    early_activity_alert_qualifies,
    heavy_activity_alert_qualifies,
    heavy_activity_signature,
    target_crossed,
)
from config import IST_TIMEZONE
from models import MarketSnapshot
from services.railway_live_client import delete_railway_alert, post_railway_json


def _bell_wav() -> bytes:
    sample_rate = 22050
    duration = 1.35
    frames = bytearray()
    for index in range(int(sample_rate * duration)):
        second = index / sample_rate
        envelope = max(0.0, 1.0 - second / duration)
        pulse = 1.0 if second < 0.36 or 0.52 < second < 0.88 else 0.0
        value = (
            math.sin(2 * math.pi * 880 * second)
            + 0.45 * math.sin(2 * math.pi * 1320 * second)
        )
        sample = int(11000 * envelope * pulse * value / 1.45)
        frames.extend(struct.pack("<h", sample))
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(frames))
    return output.getvalue()


def _play_alert(message: str) -> None:
    st.audio(_bell_wav(), format="audio/wav", autoplay=True)
    safe_message = json.dumps(str(message))
    components.html(
        f"""
        <script>
        const message = {safe_message};
        if ('speechSynthesis' in window) {{
          window.speechSynthesis.cancel();
          const voice = new SpeechSynthesisUtterance(message);
          voice.rate = 0.92;
          voice.pitch = 1.0;
          window.speechSynthesis.speak(voice);
        }}
        </script>
        """,
        height=0,
    )


def _activity_message(snapshot: MarketSnapshot) -> str:
    item = snapshot.big_player_activity
    if item is None:
        return "Heavy market activity alert"
    activity_type = str(item.activity_type)
    if activity_type == "SHORT COVERING":
        meaning = "Strong short covering. Fresh buying not confirmed."
    elif activity_type == "LONG UNWINDING":
        meaning = "Strong long unwinding. Fresh selling not confirmed."
    elif activity_type == "LONG BUILD-UP":
        meaning = "Fresh long build-up. New buyers are entering."
    elif activity_type == "SHORT BUILD-UP":
        meaning = "Fresh short build-up. New sellers are entering."
    else:
        meaning = f"Very strong {item.direction.lower()} detected."
    return f"Alert. {meaning} Score {item.score:.0f} out of 100."


def _early_activity_message(snapshot: MarketSnapshot) -> str:
    item = snapshot.big_player_activity
    if item is None:
        return "Early market activity warning."
    direction = "buying" if item.direction == "BUYING" else "selling"
    level = str(item.level_reaction or "").lower()
    setup = str(item.futures_setup or "").replace("-", " ").lower()
    return (
        f"Early warning. Strong {direction} may be starting. "
        f"Score {item.score:.0f}. Futures {setup}. {level}. "
        "Wait for the next confirmation."
    )


def _available_strikes(snapshot: MarketSnapshot, option_side: str) -> list[float]:
    frame = snapshot.option_chain
    if frame is None or frame.empty or not {"side", "strike"}.issubset(frame.columns):
        return []
    rows = frame[frame["side"].astype(str).str.upper() == option_side]
    values = sorted(
        {
            float(value)
            for value in rows["strike"].dropna().tolist()
            if float(value) > 0
        }
    )
    return values


def _option_premium(
    snapshot: MarketSnapshot, option_side: str, strike: float
) -> float | None:
    frame = snapshot.option_chain
    required = {"side", "strike", "last_price"}
    if frame is None or frame.empty or not required.issubset(frame.columns):
        return None
    rows = frame[
        (frame["side"].astype(str).str.upper() == option_side)
        & ((frame["strike"].astype(float) - float(strike)).abs() < 0.01)
    ]
    if rows.empty:
        return None
    try:
        value = float(rows.iloc[0]["last_price"])
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _option_security_id(snapshot: MarketSnapshot, option_side: str, strike: float) -> int | None:
    frame = snapshot.option_chain
    required = {"side", "strike", "security_id"}
    if frame is None or frame.empty or not required.issubset(frame.columns):
        return None
    rows = frame[
        (frame["side"].astype(str).str.upper() == option_side)
        & ((frame["strike"].astype(float) - float(strike)).abs() < 0.01)
    ]
    if rows.empty:
        return None
    try:
        return int(rows.iloc[0]["security_id"])
    except (TypeError, ValueError):
        return None


def render_market_alerts(
    snapshot: MarketSnapshot,
    *,
    live_server_url: str = "",
    live_server_api_key: str = "",
) -> None:
    activity = snapshot.big_player_activity
    sound_enabled = bool(st.session_state.get("market_alert_sound_enabled", False))

    st.subheader("🔔 Heavy Activity + Manual Price Alerts")
    st.caption(
        "Heavy alert poore NIFTY market ki activity hai. Manual alert me CE/PE, BUY/SELL, strike aur option premium tum khud bharoge; "
        "yeh alert-only hai, order place ya One-Brain decision change nahi karta."
    )

    sound_col, test_col = st.columns(2)
    with sound_col:
        sound_enabled = st.toggle(
            "Alert Sound ON",
            value=sound_enabled,
            key="market_alert_sound_enabled",
        )
    with test_col:
        test_sound = st.button(
            "🔊 Enable / Test Ring",
            width="stretch",
            disabled=not sound_enabled,
            key="test_market_alert_sound",
        )
    if test_sound:
        _play_alert("Nifty Seller Lite alert sound is ready.")
        st.success("Sound ready—browser me awaaz sunai deni chahiye.")

    heavy_col, manual_col = st.columns(2)
    with heavy_col:
        st.markdown("#### 🐘 Automatic NIFTY Market Heavy Alert")
        st.caption("Yeh CE/PE trade nahi—poore NIFTY market ki heavy buying/selling hai.")
        qualifies = heavy_activity_alert_qualifies(activity)
        if activity is None:
            st.info("Big Player activity unavailable")
        else:
            st.metric(
                "Current activity",
                f"{activity.direction} {activity.score:.0f}/100",
                f"Confirmed {activity.confirmation_count}/{activity.confirmation_total}",
            )
            if qualifies:
                st.warning(
                    f"HEAVY ALERT READY · {activity.state} {activity.activity_type}"
                )
            elif early_activity_alert_qualifies(activity):
                st.warning(
                    f"EARLY WARNING READY · {activity.direction} {activity.score:.0f}/100 · "
                    f"{activity.confirmation_count}/{activity.confirmation_total}"
                )
            else:
                st.caption("ARMED · Early 65+ (1 confirmation), Heavy 70+ (2 confirmations) ka wait")

    with manual_col:
        st.markdown("#### 🎯 Manual CE/PE Premium Alert")
        option_side = st.selectbox(
            "Option type",
            ("CE", "PE"),
            key="manual_option_alert_side_input",
        )
        position = st.selectbox(
            "Position",
            ("BUY", "SELL"),
            key="manual_option_alert_position_input",
        )
        strikes = _available_strikes(snapshot, option_side)
        strike = st.selectbox(
            "Strike",
            strikes,
            format_func=lambda value: f"{value:,.0f} {option_side}",
            key="manual_option_alert_strike_input",
            disabled=not strikes,
        ) if strikes else None
        current_premium = (
            _option_premium(snapshot, option_side, float(strike))
            if strike is not None
            else None
        )
        if current_premium is not None:
            st.metric("Current option premium", f"₹{current_premium:,.2f}")
        else:
            st.caption("Selected CE/PE premium abhi unavailable")
        default_target = float(round(current_premium, 2)) if current_premium is not None else 0.0
        target = st.number_input(
            "Target option premium ₹",
            min_value=0.0,
            value=default_target,
            step=0.50,
            key="manual_option_alert_target_input",
        )
        m1, m2, m3 = st.columns(3)
        trigger_mode = m1.selectbox(
            "Trigger",
            ("TOUCH", "ABOVE", "BELOW"),
            help="TOUCH target ke aas-paas; ABOVE/BELOW exact crossing.",
            key="manual_option_alert_mode_input",
        )
        tolerance = m2.number_input(
            "Near ₹",
            min_value=0.05,
            value=0.50,
            step=0.05,
            key="manual_option_alert_tolerance_input",
        )
        entry_no = m3.selectbox("Entry no.", (1, 2, 3), key="manual_option_alert_entry_no")
        arm_col, cancel_col = st.columns(2)
        arm_clicked = arm_col.button(
            "ARM ALERT",
            type="primary",
            width="stretch",
            disabled=current_premium is None or target <= 0 or strike is None,
            key="arm_manual_option_alert",
        )
        cancel_clicked = cancel_col.button(
            "CANCEL",
            width="stretch",
            disabled=not st.session_state.get("manual_option_alert_active", False),
            key="cancel_manual_option_alert",
        )
        if arm_clicked and current_premium is not None and strike is not None:
            security_id = _option_security_id(snapshot, option_side, float(strike))
            cloud_armed = False
            if live_server_url and live_server_api_key and security_id is not None:
                try:
                    result = post_railway_json(
                        live_server_url,
                        live_server_api_key,
                        "/alerts/premium",
                        {
                            "security_id": security_id,
                            "side": option_side,
                            "position": position,
                            "strike": float(strike),
                            "expiry": str(snapshot.expiry or ""),
                            "target_premium": float(target),
                            "mode": trigger_mode,
                            "tolerance": float(tolerance),
                            "entry_no": int(entry_no),
                        },
                    )
                    st.session_state.manual_option_cloud_alert_id = result.get("id")
                    cloud_armed = True
                except Exception as exc:
                    st.error(f"Railway Telegram alert arm nahi hua: {exc}")
            st.session_state.manual_option_alert_active = True
            st.session_state.manual_option_alert_side = option_side
            st.session_state.manual_option_alert_position = position
            st.session_state.manual_option_alert_strike = float(strike)
            st.session_state.manual_option_alert_target = float(target)
            st.session_state.manual_option_alert_armed_premium = float(current_premium)
            st.session_state.manual_option_alert_expiry = str(snapshot.expiry or "")
            st.session_state.manual_option_alert_armed_at = datetime.now(
                ZoneInfo(IST_TIMEZONE)
            ).isoformat()
            st.success(
                f"{option_side} {position} alert ARMED · Strike {float(strike):,.0f} · "
                f"Target ₹{target:,.2f} · Current ₹{current_premium:,.2f} · "
                f"{'Railway + Telegram 24×7' if cloud_armed else 'Browser only'}"
            )
        if cancel_clicked:
            cloud_id = str(st.session_state.get("manual_option_cloud_alert_id", ""))
            if cloud_id and live_server_url and live_server_api_key:
                try:
                    delete_railway_alert(live_server_url, live_server_api_key, cloud_id)
                except Exception as exc:
                    st.warning(f"Railway cancel pending: {exc}")
            st.session_state.pop("manual_option_cloud_alert_id", None)
            st.session_state.manual_option_alert_active = False
            st.info("Manual CE/PE premium alert cancelled")

        if st.session_state.get("manual_option_alert_active", False):
            active_side = str(st.session_state.get("manual_option_alert_side", "CE"))
            active_position = str(st.session_state.get("manual_option_alert_position", "BUY"))
            active_strike = float(st.session_state.get("manual_option_alert_strike", 0.0))
            active_target = float(st.session_state.get("manual_option_alert_target", 0.0))
            live_premium = _option_premium(snapshot, active_side, active_strike)
            current_text = f"₹{live_premium:,.2f}" if live_premium is not None else "Unavailable"
            st.info(
                f"ACTIVE · {active_strike:,.0f} {active_side} {active_position} · "
                f"Target ₹{active_target:,.2f} · Current {current_text}"
            )
        last_manual = st.session_state.get("last_manual_option_alert")
        if isinstance(last_manual, dict):
            st.caption(
                f"Last: {float(last_manual.get('strike', 0)):,.0f} {last_manual.get('side')} "
                f"{last_manual.get('position')} · Target ₹{float(last_manual.get('target', 0)):,.2f} "
                f"reached at ₹{float(last_manual.get('premium', 0)):,.2f} · {last_manual.get('time')}"
            )

    # Two-stage latches prevent a ring on every 30-second snapshot. An early
    # heads-up can still escalate to a separate confirmed-heavy ring.
    qualifies = heavy_activity_alert_qualifies(activity)
    early_qualifies = early_activity_alert_qualifies(activity) and not qualifies
    active_signature = heavy_activity_signature(activity) if qualifies else ""
    early_signature = heavy_activity_signature(activity) if early_qualifies else ""
    if activity is not None and live_server_url and live_server_api_key:
        post_signature = (
            f"{snapshot.snapshot_id}:{activity.direction}:{activity.score:.0f}:"
            f"{activity.confirmation_count}:{activity.activity_type}"
        )
        if st.session_state.get("last_big_player_cloud_post") != post_signature:
            try:
                option_confirmation = str(getattr(activity, "option_confirmation", ""))
                top_confirmation = str(getattr(activity, "top7_confirmation", ""))
                expected = "BULL" if activity.direction == "BUYING" else "BEAR"
                conflict = any(
                    value and expected not in value.upper() and "MIX" not in value.upper()
                    for value in (option_confirmation, top_confirmation)
                )
                post_railway_json(
                    live_server_url,
                    live_server_api_key,
                    "/alerts/big-player",
                    {
                        "score": activity.score,
                        "direction": activity.direction,
                        "activity_type": activity.activity_type,
                        "confirmation_count": activity.confirmation_count,
                        "futures_setup": activity.futures_setup,
                        "conflict": conflict,
                    },
                )
                st.session_state.last_big_player_cloud_post = post_signature
            except Exception as exc:
                st.caption(f"Telegram Big Player sync pending: {exc}")
    if not early_qualifies:
        st.session_state.pop("early_alert_latched_signature", None)
    elif (
        sound_enabled
        and not test_sound
        and st.session_state.get("early_alert_latched_signature") != early_signature
    ):
        st.session_state.early_alert_latched_signature = early_signature
        message = _early_activity_message(snapshot)
        st.toast(message, icon="⚠️")
        _play_alert(message)
    if not qualifies:
        st.session_state.pop("heavy_alert_latched_signature", None)
    elif (
        sound_enabled
        and not test_sound
        and st.session_state.get("heavy_alert_latched_signature") != active_signature
    ):
        st.session_state.heavy_alert_latched_signature = active_signature
        message = _activity_message(snapshot)
        st.toast(message, icon="🚨")
        _play_alert(message)

    # Manual option-premium alert is one-shot. Arm-click itself never triggers it.
    if (
        not arm_clicked
        and st.session_state.get("manual_option_alert_active", False)
    ):
        active_side = str(st.session_state.get("manual_option_alert_side", "CE"))
        active_position = str(st.session_state.get("manual_option_alert_position", "BUY"))
        active_strike = float(st.session_state.get("manual_option_alert_strike", 0.0))
        active_target = float(st.session_state.get("manual_option_alert_target", 0.0))
        armed_premium = float(
            st.session_state.get("manual_option_alert_armed_premium", 0.0)
        )
        live_premium = _option_premium(snapshot, active_side, active_strike)
        if live_premium is not None and active_target > 0 and target_crossed(
            armed_spot=armed_premium,
            current_spot=live_premium,
            target=active_target,
        ):
            now = datetime.now(ZoneInfo(IST_TIMEZONE))
            message = (
                f"{active_strike:,.0f} {active_side} {active_position} premium reached. "
                f"Current premium {live_premium:,.2f} rupees. Target {active_target:,.2f} rupees."
            )
            st.session_state.manual_option_alert_active = False
            st.session_state.last_manual_option_alert = {
                "side": active_side,
                "position": active_position,
                "strike": active_strike,
                "target": active_target,
                "premium": float(live_premium),
                "time": now.strftime("%H:%M:%S"),
            }
            st.toast(message, icon="🔔")
            if sound_enabled:
                _play_alert(message)
            else:
                st.warning(message + " Sound OFF tha.")

    st.caption(
        "Laptop/mobile browser tab open ho to alert best kaam karta hai. Phone screen lock/background me "
        "browser sound guaranteed nahi. CE/PE premium fresh check ke liye Auto Snapshot ON rakho."
    )
