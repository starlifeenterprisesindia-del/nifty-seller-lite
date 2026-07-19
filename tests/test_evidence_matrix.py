from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from analysis.evidence_matrix import build_compact_evidence_matrix
from models import FeedStatus


IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 7, 20, 11, 0, tzinfo=IST)


def _indicator(ema: str, macd: str, rsi: str):
    return SimpleNamespace(
        status="READY", ema_state=ema, macd_state=macd, rsi_state=rsi
    )


def test_compact_matrix_has_six_rows_and_normalized_directional_scores():
    snapshot = SimpleNamespace(
        price_action=SimpleNamespace(
            three_minute=SimpleNamespace(
                bullish_score=82,
                bearish_score=12,
                range_score=20,
                status="READY",
                structure="BULLISH HH/HL",
            ),
            fifteen_minute=SimpleNamespace(
                bullish_score=44,
                bearish_score=44,
                range_score=52,
                status="READY",
                structure="MIXED / TRANSITION",
            ),
            confidence=70.4,
        ),
        option_intelligence=SimpleNamespace(
            bullish_score=70,
            bearish_score=10,
            range_score=20,
            confidence=75,
            market_bias="BULLISH",
            persistence="BULLISH PERSISTENT ×2",
            windows=(
                SimpleNamespace(status="READY"),
                SimpleNamespace(status="READY"),
                SimpleNamespace(status="WARMING UP"),
            ),
            pcr=SimpleNamespace(state="BULLISH SUPPORT"),
        ),
        indicators=SimpleNamespace(
            three_minute=_indicator("BULLISH ALIGNED", "BULLISH", "BULLISH STRENGTH"),
            fifteen_minute=_indicator(
                "BULLISH ALIGNED", "BULLISH", "BULLISH / OVEREXTENDED CAUTION"
            ),
        ),
        levels=SimpleNamespace(
            status="READY",
            current_position="NEAR RESISTANCE",
            immediate_support=None,
            immediate_resistance=SimpleNamespace(status="ACTIVE"),
            upside_room=7.0,
            downside_room=30.0,
        ),
        volume=SimpleNamespace(overall_view="BULLISH PARTICIPATION", confidence=80.0),
        heavyweights=SimpleNamespace(
            state="MIXED / FLAT", status="READY", confidence=92.0
        ),
        institutional_context=SimpleNamespace(
            state="MISSING", status="MISSING", confidence=0.0
        ),
        vix_context=SimpleNamespace(seller_environment="VIX DATA UNAVAILABLE"),
        market_session=SimpleNamespace(
            label="MARKET CLOSED — LAST AVAILABLE DATA", is_live=False
        ),
        event_risk=SimpleNamespace(level="NONE"),
        feed_status={
            "quotes": FeedStatus("quotes", True, NOW, use_state="REFERENCE"),
            "candles": FeedStatus("candles", True, NOW, use_state="REFERENCE"),
            "option_chain": FeedStatus(
                "option_chain", True, NOW, use_state="REFERENCE"
            ),
        },
    )

    rows = build_compact_evidence_matrix(snapshot)
    assert [row["Module"] for row in rows] == [
        "Price Action",
        "OI & Options Flow",
        "EMA / MACD / RSI",
        "Levels & Volume",
        "Top-7 & FII/DII",
        "VIX / Data / Event Risk",
    ]
    assert len(rows) == 6
    for row in rows[:5]:
        total = row["Bullish %"] + row["Bearish %"] + row["Neutral %"]
        assert round(total, 1) == 100.0
    assert rows[-1]["Bullish %"] is None
    assert "VIX DATA UNAVAILABLE" in rows[-1]["Result"]


def test_directional_rounding_always_totals_exactly_100():
    from analysis.evidence_matrix import _normalise

    samples = [
        (1, 1, 1),
        (2, 3, 7),
        (0.1, 0.2, 0.3),
        (99, 0.2, 0.8),
        (0, 5, 2),
    ]
    for values in samples:
        result = _normalise(*values)
        assert round(sum(result), 1) == 100.0
