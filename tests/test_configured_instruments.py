from config import CONFIG


def test_top9_have_unique_direct_security_ids():
    assert len(CONFIG.top9) == 9
    ids = [item.security_id for item in CONFIG.top9]
    assert len(ids) == len(set(ids))
    assert all(item.exchange_segment == "NSE_EQ" for item in CONFIG.top9)


def test_vix_has_direct_index_reference():
    assert CONFIG.india_vix.exchange_segment == "IDX_I"
    assert CONFIG.india_vix.security_id


def test_top9_weights_are_unique_and_positive():
    weights = [item.weight_pct for item in CONFIG.top9]
    assert all(weight > 0 for weight in weights)
    assert round(sum(weights), 2) == 50.21
    assert CONFIG.top9_symbols == (
        "HDFCBANK",
        "ICICIBANK",
        "RELIANCE",
        "BHARTIARTL",
        "LT",
        "SBIN",
        "INFY",
        "AXISBANK",
        "KOTAKBANK",
    )
    assert CONFIG.top9[-1].security_id == "1922"
