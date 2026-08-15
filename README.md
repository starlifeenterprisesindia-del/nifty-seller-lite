# Nifty Seller Lite 2.24

Directional CE/PE candidates now appear in three compact protected profiles:
LOW RISK, BALANCED and HIGH RISK. Every BUY or SELL candidate uses a same-expiry,
equal-quantity hedge; no naked directional candidate is produced. The One-Brain
action remains authoritative and BALANCED is the default candidate profile.

The compact evidence view now shows each module's current One-Brain contribution,
last-snapshot contribution and delta. VIX displays its value/change/regime/movement.
News is reduced to a compact direction/severity/impact indicator; 3–24 hour headlines
may remain as context but always carry zero live decision weight.

Nearest Levels includes 3-minute W/M structure and a completed 5-minute Special
Candle with 3-minute/15-minute confirmation, possible effect and signal confidence.
Optional Auto Snapshot can run for 5/15/30 minutes at 30-second or 1-minute intervals.

Big Player Activity is a separate responsive evidence screen fed by the same
authoritative One-Brain snapshot. It combines time-normalized NIFTY-futures volume,
futures price/OI, ATM option flow, Top-7 participation and barrier reaction. A 2/2 distinct-minute
same-session persistence gate is required before its bounded decision adjustment;
the app never claims to identify a particular institution.

Big Player direction now uses two distinct completed-minute observations instead of
counting repeated 30-second refreshes. A minimum four-point move filter suppresses
small BUY/SELL flips, and Top-7 is treated as supporting context rather than a required
market-direction vote. The screen uses simple Hinglish states for starting, confirmed,
fading and small/noisy moves.

The alert expander provides a two-stage bell/voice notification: an early directional
heads-up at 65+ with 1/3 persistence and supporting volume/flow evidence, followed by
a confirmed heavy NIFTY-market alert at 75+ and 2/3. It also provides one manually armed CE/PE strike-premium
target with a BUY/SELL label. Browser
audio requires a one-time Enable/Test interaction and is most reliable while the tab
is open; alerts never place an order or alter the One-Brain decision.

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
