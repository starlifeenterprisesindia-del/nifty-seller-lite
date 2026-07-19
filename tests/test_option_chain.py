from analysis.option_chain import option_chain_to_frame, select_atm_window


def sample_response():
    return {
        "status": "success",
        "data": {
            "last_price": 24120.0,
            "oc": {
                "24100.000000": {
                    "ce": {
                        "last_price": 100,
                        "oi": 1200,
                        "previous_oi": 1000,
                        "volume": 500,
                        "previous_volume": 100,
                        "previous_close_price": 90,
                        "security_id": 1,
                    },
                    "pe": {
                        "last_price": 80,
                        "oi": 1600,
                        "previous_oi": 1300,
                        "volume": 700,
                        "previous_volume": 200,
                        "previous_close_price": 85,
                        "security_id": 2,
                    },
                },
                "24150.000000": {
                    "ce": {
                        "last_price": 70,
                        "oi": 1000,
                        "previous_oi": 900,
                        "volume": 400,
                        "previous_volume": 100,
                        "previous_close_price": 75,
                        "security_id": 3,
                    },
                    "pe": {
                        "last_price": 105,
                        "oi": 1100,
                        "previous_oi": 1000,
                        "volume": 450,
                        "previous_volume": 100,
                        "previous_close_price": 100,
                        "security_id": 4,
                    },
                },
            },
        },
    }


def test_option_chain_flattening():
    spot, frame = option_chain_to_frame(sample_response())
    assert spot == 24120.0
    assert len(frame) == 4
    ce = frame[(frame["strike"] == 24100) & (frame["side"] == "CE")].iloc[0]
    assert ce["day_oi_change"] == 200
    assert ce["day_price_change"] == 10


def test_atm_window_marks_atm():
    spot, frame = option_chain_to_frame(sample_response())
    window = select_atm_window(frame, spot, 1)
    assert window["is_atm"].any()
