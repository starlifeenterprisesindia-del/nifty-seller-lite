# Nifty Seller Lite 2.70 — Pressure Integrity + Smart Alert Hardening

Read-only NIFTY options decision-support app with one operational path:

**Regime → Direction → Entry → Risk → Action**

The canonical Simple One-Brain remains intentionally small: **Trend/Regime 40% + Options Flow 25% + Participation 20% + Barrier/Entry 15%**. Missing evidence is **NO VOTE**, never an invented neutral vote. Future Brain and Market Intelligence are advisory/shadow layers; no UI panel creates a second operational decision engine.

## 2.70 highlights

- **Pressure Integrity Engine (shadow-only):** separates fast big-move risk from directional pressure quality. It labels pressure as UNVERIFIED / BUILDING / VERIFIED / REALIZED / ABSORPTION RISK / BUILD-UP FAILED / EXHAUSTING / FLIP WATCH / FLIP CONFIRMED.
- **No late-warning penalty from patterns:** W/M and strong-candle evidence is supportive only. Missing W/M/candle confirmation never blocks an early Move Radar warning.
- **Move Attack state:** combines already-cached pressure, price response and barrier attack into NORMAL / WATCH / BUILDING / ATTACK / BREAK-EXPANSION states without adding a broker call.
- **Real-vs-fake distinction:** pressure that already moved price meaningfully is remembered as REALIZED; if it later cools it becomes EXHAUSTING rather than incorrectly labelled fake.
- **Pressure flip control:** opposite pressure begins as FLIP WATCH and upgrades only after stronger independent confirmation.
- **Liquidity wording:** a zone already containing price is shown as CURRENT BATTLE ZONE; the directional extension becomes NEXT HUNT ZONE. Immediate reclaim/rejection is REVERSAL WATCH; follow-through is required before REVERSAL FAVORED.
- **One Telegram voice:** W/M, candle and Big Player evidence still calculate and record, but when Market Intelligence alerts are enabled they are merged as supportive context instead of creating repeated standalone messages.
- **WAIT presentation:** backend WAIT safety remains unchanged; the UI can show NO EDGE / WATCH / ARMED progression so a trigger-ready setup is visible without loosening One Brain.
- **Diagnostic hardening:** numeric core-block tables use consistent display types to avoid Streamlit/PyArrow mixed-type serialization warnings.
- **Validation pack:** adds pressure-integrity review fields and CSV output for fake/real pressure, price response, barriers, flips and realized move tracking.
- **No extra API calls:** all new calculations reuse the authoritative MarketSnapshot and already-cached evidence.

## Main screen philosophy

Show one operational answer first. Deep evidence stays behind expanders. Market Intelligence may warn early that **a move is developing**, while pressure quality separately indicates how trustworthy its direction currently is.

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
