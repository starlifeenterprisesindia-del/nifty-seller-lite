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
    heavy_activity_alert_qualifies,
    heavy_activity_signature,
    target_crossed,
)
from config import IST_TIMEZONE
from models import MarketSnapshot


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


def _current_spot(snapshot: MarketSnapshot) -> float | None:
    try:
        value = float(snapshot.nifty_quote.get("last_price"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def render_market_alerts(snapshot: MarketSnapshot) -> None:
    spot = _current_spot(snapshot)
    activity = snapshot.big_player_activity
    sound_enabled = bool(st.session_state.get("market_alert_sound_enabled", False))

    st.subheader("🔔 Heavy Activity + Manual Price Alerts")
    st.caption(
        "Heavy alert automatic hai. Manual alert me BUY/SELL label aur NIFTY target tum khud bharoge; "
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
        st.markdown("#### 🐘 Automatic Heavy Alert")
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
            else:
                st.caption("ARMED · 75+ score aur 2/3 confirmation ka wait")

    with manual_col:
        st.markdown("#### 🎯 Manual NIFTY Price Alert")
        side = st.selectbox(
            "Alert label",
            ("BUY", "SELL"),
            key="manual_price_alert_side_input",
        )
        default_target = float(round(spot / 50) * 50) if spot is not None else 0.0
        target = st.number_input(
            "Target NIFTY price",
            min_value=0.0,
            value=default_target,
            step=1.0,
            key="manual_price_alert_target_input",
        )
        arm_col, cancel_col = st.columns(2)
        arm_clicked = arm_col.button(
            "ARM ALERT",
            type="primary",
            width="stretch",
            disabled=spot is None or target <= 0,
            key="arm_manual_price_alert",
        )
        cancel_clicked = cancel_col.button(
            "CANCEL",
            width="stretch",
            disabled=not st.session_state.get("manual_price_alert_active", False),
            key="cancel_manual_price_alert",
        )
        if arm_clicked and spot is not None:
            st.session_state.manual_price_alert_active = True
            st.session_state.manual_price_alert_side = side
            st.session_state.manual_price_alert_target = float(target)
            st.session_state.manual_price_alert_armed_spot = float(spot)
            st.session_state.manual_price_alert_armed_at = datetime.now(
                ZoneInfo(IST_TIMEZONE)
            ).isoformat()
            st.success(
                f"{side} alert ARMED · Target {target:,.2f} · Current {spot:,.2f}"
            )
        if cancel_clicked:
            st.session_state.manual_price_alert_active = False
            st.info("Manual price alert cancelled")

        if st.session_state.get("manual_price_alert_active", False):
            active_side = str(st.session_state.get("manual_price_alert_side", "BUY"))
            active_target = float(st.session_state.get("manual_price_alert_target", 0.0))
            current_text = f"{spot:,.2f}" if spot is not None else "—"
            st.info(
                f"ACTIVE · {active_side} target {active_target:,.2f} · Current {current_text}"
            )
        last_manual = st.session_state.get("last_manual_price_alert")
        if isinstance(last_manual, dict):
            st.caption(
                f"Last: {last_manual.get('side')} {float(last_manual.get('target', 0)):,.2f} "
                f"reached at NIFTY {float(last_manual.get('spot', 0)):,.2f} · {last_manual.get('time')}"
            )

    # Heavy alert is latched by event type and rearms only after activity falls
    # below the threshold. This prevents a ring on every 30-second snapshot.
    qualifies = heavy_activity_alert_qualifies(activity)
    active_signature = heavy_activity_signature(activity) if qualifies else ""
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

    # Manual alert is one-shot. Arm-click itself never triggers it immediately.
    if (
        not arm_clicked
        and st.session_state.get("manual_price_alert_active", False)
        and spot is not None
    ):
        armed_spot = float(st.session_state.get("manual_price_alert_armed_spot", spot))
        active_target = float(st.session_state.get("manual_price_alert_target", 0.0))
        if active_target > 0 and target_crossed(
            armed_spot=armed_spot,
            current_spot=spot,
            target=active_target,
        ):
            active_side = str(st.session_state.get("manual_price_alert_side", "BUY"))
            now = datetime.now(ZoneInfo(IST_TIMEZONE))
            message = (
                f"{active_side} price reached. Nifty {spot:,.2f}. "
                f"Target {active_target:,.2f}."
            )
            st.session_state.manual_price_alert_active = False
            st.session_state.last_manual_price_alert = {
                "side": active_side,
                "target": active_target,
                "spot": float(spot),
                "time": now.strftime("%H:%M:%S"),
            }
            st.toast(message, icon="🔔")
            if sound_enabled:
                _play_alert(message)
            else:
                st.warning(message + " Sound OFF tha.")

    st.caption(
        "Laptop/mobile browser tab open ho to alert best kaam karta hai. Phone screen lock/background me "
        "browser sound guaranteed nahi. Fresh check ke liye Auto Snapshot ON rakho."
    )
