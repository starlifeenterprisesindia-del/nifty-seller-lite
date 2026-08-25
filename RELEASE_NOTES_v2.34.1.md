# Nifty Seller Lite v2.34.1 — Railway Live Integration

- Main Streamlit app now reads the protected Railway `/live` feed through the
  `X-Live-Key` header, so the key is not exposed in the request URL.
- The existing five-second live-speed formula remains the only impulse formula.
  Railway and local fallback observations both use it.
- Railway WebSocket is preferred. The previous Dhan quote monitor remains a safe
  fallback if the Railway secrets are missing or temporarily unavailable.
- Live impulse is an early-warning lane only. It does not create a second decision
  brain, place orders, or bypass One-Brain confirmation/risk gates.

## Streamlit secrets

```toml
[live_server]
url = "https://nifty-seller-lite-production.up.railway.app"
api_key = "YOUR_PRIVATE_LIVE_API_KEY"
```

Do not commit the real API key to GitHub.
