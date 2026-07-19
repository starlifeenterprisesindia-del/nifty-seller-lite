from datetime import datetime
from zoneinfo import ZoneInfo

from analysis.heavyweights import calculate_heavyweight_bundle
from analysis.market_risk import calculate_vix_context
from config import CONFIG


IST = ZoneInfo("Asia/Kolkata")


def test_official_top7_weights_and_broad_bullish_state():
    quotes = []
    for item in CONFIG.top7:
        quotes.append(
            {
                "symbol": item.symbol,
                "display_name": item.name,
                "last_price": 101.0,
                "ohlc": {"close": 100.0},
            }
        )
    result = calculate_heavyweight_bundle(
        quotes, datetime(2026, 7, 20, 10, 0, tzinfo=IST)
    )
    assert CONFIG.top7_weight_date == "2026-06-30"
    assert round(sum(item.weight_pct for item in CONFIG.top7), 2) == 45.20
    assert result.covered_weight_pct == 45.20
    assert result.advancing == 7
    assert result.state == "BROAD BULLISH"
    assert result.status == "READY"


def test_vix_context_marks_high_gap_risk():
    result = calculate_vix_context(
        {"last_price": 19.0, "ohlc": {"close": 17.5}},
        datetime(2026, 7, 20, 10, 0, tzinfo=IST),
    )
    assert result.regime == "HIGH"
    assert result.movement == "RISING FAST"
    assert result.seller_environment == "HIGH PREMIUM / HIGH GAP RISK"
