# Nifty Seller Lite v2.34.2 — Telegram Live Alerts

- Railway can send deduplicated FAST MOVE and MAJOR MOVE Telegram alerts.
- Two consecutive confirmations are required by default.
- Same-direction/state alerts have a three-minute cooldown.
- Alerts run only during weekday cash-market hours and are early warnings only.
- Telegram credentials remain Railway environment variables; no secret is stored in GitHub.
- Protected `POST /telegram/test` endpoint verifies setup without exposing bot secrets.
