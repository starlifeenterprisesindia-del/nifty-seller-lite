# Nifty Seller Lite 2.21

Directional CE/PE candidates now appear in three compact protected profiles:
LOW RISK, BALANCED and HIGH RISK. Every BUY or SELL candidate uses a same-expiry,
equal-quantity hedge; no naked directional candidate is produced. The One-Brain
action remains authoritative and BALANCED is the default candidate profile.

Compact, read-only NIFTY options decision-support app using one canonical strategy brain.

## Main screen

- One-Brain final action and market direction
- Nearest support/resistance and full R1/R2/S1/S2 map
- Spot-to-Premium calculator with automatic barrier targets, ETA range, reach chance and total P&L
- Expiry-aware structural SL, conservative premium SL, T1/T2 RR and time-exit plan
- Optional manual upper/lower targets
- Compact 5-15 minute outlook and all-five strategy audit
- Collapsed market and options evidence
- Quick and Full Audit PDF downloads

## One-Brain boundary

`analysis/decision.py::calculate_final_decision` is the only strategy selector. UI, PDFs, barrier map, protected strike planner and premium calculator consume the same `MarketSnapshot`; they do not create another BUY/SELL/WAIT decision.

The automatic R1/R2/S1/S2 premium table reuses the existing `calculate_spot_premium_range` pricing engine. ETA/chance is contextual evidence from existing speed, VIX expected move and barrier pressure.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

Add Dhan credentials to Streamlit Secrets:

```toml
[dhan]
client_id = "..."
access_token = "..."
```

## Verify

```bash
pip install -r requirements-dev.txt
pytest -q
```

Runtime state, credentials, caches, generated PDFs and ZIPs are excluded through `.gitignore`.
