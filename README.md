# Nifty Seller Lite — Milestone 1

Fresh, read-only Streamlit foundation for one authoritative DhanHQ market snapshot.

## What this version does

- Fetches NIFTY live quote in one grouped quote request.
- Fetches Dhan 1-minute and native 15-minute candles.
- Builds 3-minute candles locally from 1-minute data, anchored at 09:15 IST.
- Fetches the nearest NIFTY expiry and one option-chain snapshot.
- Shows ATM ±5 strikes with LTP, OI, previous OI, day OI change and volume.
- Resolves configurable Top-7 heavyweight symbols using Dhan instrument master.
- Attempts to resolve India VIX and the nearest NIFTY future.
- Produces one `MarketSnapshot` object used by the complete screen.
- Contains no trading advice and no order APIs.

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

Upload the contents of this folder to a NEW GitHub repository. Do not merge this code into the old heavy app.
