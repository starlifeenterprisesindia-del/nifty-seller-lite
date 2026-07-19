from config import CONFIG


def test_top7_have_unique_direct_security_ids():
    assert len(CONFIG.top7) == 7
    ids = [item.security_id for item in CONFIG.top7]
    assert len(ids) == len(set(ids))
    assert all(item.exchange_segment == "NSE_EQ" for item in CONFIG.top7)


def test_vix_has_direct_index_reference():
    assert CONFIG.india_vix.exchange_segment == "IDX_I"
    assert CONFIG.india_vix.security_id
