# Nifty Seller Lite — Milestone 2

Fresh, read-only Streamlit foundation built around one authoritative DhanHQ snapshot.

## What this version does

- Classifies the session as live, pre-market, closed, weekend, or stale/holiday-like.
- Never labels last-available weekend/after-hours data as live.
- Fetches NIFTY, India VIX and seven configured heavyweight quotes in one grouped request.
- Fetches Dhan 1-minute and native 15-minute candles.
- Builds 3-minute candles locally from 1-minute data, anchored at 09:15 IST.
- Calculates EMA20, EMA50, MACD (12,26,9) and RSI14 on completed 3m and 15m candles.
- Fetches nearest NIFTY expiry and an ATM ±5-strike option-chain window.
- Shows raw OI, day OI change, volume, IV and bid/ask as reference data.
- Uses one `MarketSnapshot` object for the entire screen.
- Contains no trading advice, final scorer or order-placement code.

## Secrets

```toml
[dhan]
client_id = "YOUR_DHAN_CLIENT_ID"
access_token = "YOUR_24_HOUR_ACCESS_TOKEN"
```

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
ruff check .
```

Upload the contents of this folder to the existing `nifty-seller-lite` repository and replace matching files. Do not merge it into the old heavy app.
