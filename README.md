# Nifty Seller Lite 2.48 — Simple One-Brain

Read-only NIFTY options decision-support app built around one simple operational path:

**Regime → Direction → Entry → Risk → Action**

The app keeps the existing rich market evidence, protected strike planner, barrier map, Fast Monitor, journal and reports, but removes duplicate hard vetoes from the live decision path. The Simple One-Brain uses only four core blocks: Trend/Regime 40%, blended Options Flow 25%, Participation 20%, and Barrier/Entry 15%. Scores normalize over available evidence.

Confirmed 15m breakout/breakdown is treated as a directional regime; RSI extremes are chase-risk only; Future Brain is advisory only; Big Player is confirmation inside Participation; VIX/FII-DII/news/Greeks/patterns retain their appropriate risk, context or strike-quality roles without becoming separate direction gates.

The Decision Journal records WAIT/READY/ENTRY observations and later observed +5m/+15m/+30m outcomes, while the Paper Trade Journal remains restricted to protected gate-passed simulations. The 5-second Fast Monitor can trigger a priority full snapshot on a major move but never places or decides a trade itself.

## Main screen

- One-Brain final action and market direction
- Nearest support/resistance and full R1/R2/S1/S2 map
- Spot-to-Premium calculator with automatic barrier targets, ETA range, reach chance and total P&L
- Expiry-aware structural SL, conservative premium SL, T1/T2 RR and time-exit plan
- Optional manual upper/lower targets
- Compact 5-15 minute outlook and all-five strategy audit
- Collapsed market and options evidence
- Quick PDF, Complete Diagnostic PDF and one-click credential-free Support Bundle ZIP

## One-Brain boundary

`analysis/simple_brain.py` is the live operational entry authority after the canonical snapshot evidence is built. `analysis/decision.py` remains the legacy/core evidence and protected-plan source for compatibility and diagnostics. UI/PDF/premium tools never fetch data or independently approve a trade.

The automatic R1/R2/S1/S2 premium table reuses the existing pricing engine. ETA/chance is contextual evidence only.

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
