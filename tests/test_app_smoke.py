from streamlit.testing.v1 import AppTest


def test_app_starts_safely_without_credentials():
    app = AppTest.from_file("app.py").run(timeout=20)
    assert not app.exception
    assert app.title[0].value == "📈 Nifty Seller Lite"
    assert any("Dhan credentials missing" in item.value for item in app.error)
