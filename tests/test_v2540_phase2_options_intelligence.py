from types import SimpleNamespace

import pandas as pd

from analysis.advanced_options_display import (
    build_phase2_payload,
    iv_context,
    liquidity_board,
    straddle_context,
    unusual_activity,
)


def _frame():
    rows = []
    for strike in (22600, 22650, 22700):
        for side in ("CE", "PE"):
            rows.append({
                "strike": strike,
                "side": side,
                "last_price": 100 + abs(strike-22650)/4 + (5 if side == "PE" else 0),
                "top_bid_price": 99.5,
                "top_ask_price": 100.5,
                "oi": 100000 + (22700-strike)*100,
                "volume": 50000 + (strike-22600)*50,
                "implied_volatility": 14.0 + (0.8 if side == "PE" else 0.0),
                "theta": -6.0,
            })
    return pd.DataFrame(rows)


def test_liquidity_board_is_bounded_and_graded():
    rows = liquidity_board(_frame(), 22655, max_rows=4)
    assert len(rows) == 4
    assert all(row["grade"] in {"A+", "A", "B", "C", "D"} for row in rows)
    assert all(0 <= row["score"] <= 100 for row in rows)


def test_straddle_uses_nearest_atm_pair():
    result = straddle_context(_frame(), 22655)
    assert result["status"] == "READY"
    assert result["atm"] == 22650
    assert result["combined_premium"] > 0
    assert result["upper_proxy"] > result["lower_proxy"]


def test_true_ivr_needs_history_and_then_calculates():
    warm = iv_context(_frame(), 22655, historical_atm_iv=[12, 13, 14])
    assert warm["iv_rank"] is None
    assert "NEED >=20" in warm["ivr_status"]
    ready = iv_context(_frame(), 22655, historical_atm_iv=[10 + i * 0.4 for i in range(20)])
    assert ready["iv_rank"] is not None
    assert ready["iv_percentile"] is not None


def test_unusual_activity_uses_existing_flow_only():
    flow = [
        {"strike": 22650, "side": "CE", "integrity_status": "READY", "oi_delta": 50000,
         "volume_delta": 100000, "price_delta": -12, "flow_strength": 2.4,
         "classification": "SHORT BUILDUP", "directional_bias": "BEARISH"},
        {"strike": 22700, "side": "PE", "integrity_status": "READY", "oi_delta": 1000,
         "volume_delta": 5000, "price_delta": 1, "flow_strength": 0.2,
         "classification": "NOISE / FLAT", "directional_bias": "NEUTRAL"},
    ]
    rows = unusual_activity(flow, 22655)
    assert rows[0]["strike"] == 22650
    assert rows[0]["score"] >= rows[-1]["score"]


def test_phase2_payload_does_not_require_any_client():
    snap = SimpleNamespace(
        nifty_quote={"last_price": 22655},
        option_chain=_frame(),
        option_intelligence=SimpleNamespace(flow_rows=()),
    )
    payload = build_phase2_payload(snap)
    assert set(payload) == {"liquidity", "unusual_activity", "straddle", "iv", "note"}
    assert "zero broker/API calls" in payload["note"]
