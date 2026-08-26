# Nifty Seller Lite v2.35.0 — One Source + Cloud Alerts

## Main changes

- Railway is now the single Dhan REST gateway when `[live_server]` is configured.
- Streamlit no longer falls back to a second direct Dhan request during Railway gaps.
- Shared cache, request spacing and 429 cooldown protect Dhan API limits.
- Dhan client ID/access token are required only in Railway; Streamlit needs only the Railway URL and `LIVE_API_KEY`.
- Manual CE/PE premium alerts can be armed on Railway for TOUCH/ABOVE/BELOW with a near-price tolerance and entry number 1–3.
- Railway polls active premium alerts and sends Telegram messages even if the Streamlit/browser tab is closed.
- Big Player evidence posts to the Telegram engine: early 65+/1 confirmation and confirmed 70+/2 confirmations. Conflicting options/Top-9 produces an activity alert with TRADE WAIT, not advice.
- New 5m/15m/30m/1h One-Brain outlook table is display-only and does not feed scores back into the final decision.
- Premium calculator now shows a liquidity-aware best entry and up to three staged entries. Entry 2/3 are conditional; no blind averaging.

## Safety

- No broker order methods were added.
- Alerts and premium plans are informational only.
- Final trade action remains the single canonical One-Brain decision.
