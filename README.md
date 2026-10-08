# Nifty Seller Lite 2.71 — Observability + Replay + State Hardening

Read-only NIFTY options decision-support app with one operational path:

**Regime → Direction → Entry → Risk → Action**

The canonical Simple One-Brain remains unchanged: **Trend/Regime 40% + Options Flow 25% + Participation 20% + Barrier/Entry 15%**. Missing evidence is **NO VOTE**. Future Brain and Market Intelligence remain advisory/shadow layers and never become a second order/entry authority.

## 2.71 highlights

- **Zero-weight Snapshot Integrity diagnostic:** reads the already-built feed statuses and reports core-feed coverage, request/source-age information and timestamped age skew. It never changes One Brain or adds a broker request.
- **Latency Budget visibility:** existing stage timings are surfaced as top bottlenecks, P50/P95 pipeline timing and refresh-budget health so optimization can target measured bottlenecks instead of guessing.
- **Post-market calibration summary:** Market Intelligence Test Pack now includes `calibration_summary.json` and `latency_summary.json`. These are descriptive post-hoc diagnostics only; no threshold auto-tuning and no accuracy/win-rate claim.
- **Replay expanded with Market Intelligence:** the existing on-demand Railway SQLite replay can now show recorded Move Radar, pressure quality, Move Attack, alignment, hunt direction and snapshot sync state alongside One Brain/barriers/options.
- **Runtime state hardening:** Big Player/activity JSON state now uses the same process-safe lock + atomic replace pattern already used by other bounded state stores. Persistent Day Memory remains SQLite.
- **No UI bloat:** live main screen only adds a small Data Sync status. Deep replay/calibration/performance details remain collapsed/on-demand.
- **No extra Dhan/API calls:** all live diagnostics reuse the current MarketSnapshot. Calibration/replay run only on saved data after explicit user action.

## 2.70 foundation retained

- Pressure Integrity Engine: UNVERIFIED / BUILDING / VERIFIED / REALIZED / ABSORPTION RISK / BUILD-UP FAILED / EXHAUSTING / FLIP WATCH / FLIP CONFIRMED.
- Move Attack state and fake-vs-real pressure handling.
- W/M and strong candles are supportive only; never mandatory gates for early Move Radar warning.
- Current Battle Zone vs Next Hunt Zone liquidity wording.
- Smart Alert controller with one primary Market Intelligence Telegram voice.
- WAIT presentation stages without loosening the actual One Brain gate.

## Safety/performance rules

- no new broker/API request for diagnostics
- no future data in live calculations
- no automatic threshold tuning
- replay/calibration is on-demand and post-market
- no duplicate evidence weighting
- protected One Brain decision/risk files remain unchanged in 2.71

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Verify

```bash
pip install -r requirements-dev.txt
PYTHONPATH=. pytest -q
```

Runtime state, credentials, caches and generated reports are excluded through `.gitignore`.
