# Nifty Seller Lite 2.50 — Lean AI Tracker

Read-only NIFTY options decision-support app with one operational path:

**Regime → Direction → Entry → Risk → Action**

The canonical Simple One-Brain remains intentionally small: **Trend/Regime 40% + Options Flow 25% + Participation 20% + Barrier/Entry 15%**. Missing evidence is **NO VOTE**, never an invented neutral vote. Future Brain is advisory only and no UI panel creates a second decision engine.

## 2.50 highlights

- **AI Move Check**: freezes a canonical UP/DOWN thesis and checks price every 3 minutes from Railway's existing live cache. It tracks ON TRACK / WEAKENING / STALLED / INVALIDATED / TARGET MET, MFE/MAE and 5m/15m/30m outcomes. It does not recalculate indicators, option flow, Top-9, news or One-Brain. Each 3-minute result is also written as a tiny Railway `AI TRACKER` history event for later audit.
- **Locked barrier tracking**: the tracker keeps the original relevant support/resistance so the goalpost cannot silently move. A completed 3m close from the normal snapshot can confirm the break.
- **Journal window 09:30–15:00 IST**: no new decision rows outside the clean learning window; already-open rows may still receive later outcome backfills.
- **Auto Snapshot adds 1 hour**.
- **Combined Strong Candle / W-M / Big Player alerts**: one Telegram lane with deduplication; the old separate Big Player alert path is removed.
- **Calculator persistence**: one outer panel only, so Streamlit reruns no longer collapse an unnecessary inner expander; manual entry inputs retain session-state keys.
- **Top-9 missing data = NO VOTE**: stale/flat session-change fallback cannot create fake 100% neutral Participation.
- **Cleaner presentation**: Compact Evidence is diagnostic, entry DATA WAIT is shown as data incomplete, Brain Fit is separated from Strike/Pair Quality, near-ATM OI walls are clearly distinguished from full-chain Global Max OI.
- **Lean performance**: instrument reference resolution is cached in-process; the AI tracker makes only one lightweight `/live` read every 3 minutes and adds no Dhan/option-chain calls.

## Main screen philosophy

Show one operational answer first. Deep evidence stays behind expanders. The app favors fewer calculations, correct calculations, and observable prediction outcomes over adding more indicators.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Verify

```bash
pip install -r requirements-dev.txt
pytest -q
```

Runtime state, credentials, caches, generated reports and local data are excluded through `.gitignore`.
