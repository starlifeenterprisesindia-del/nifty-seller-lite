# Nifty Seller Lite 2.31.1

## Stable View

- Main auto-refreshing detail sections now use session-backed open/closed controls.
- A 15-second full snapshot refresh no longer closes an open Big Player, Alerts, Compact Evidence, Strategy Audit, Decision Reason, or Advanced Options section.
- The 5-second Fast Live Monitor remains an isolated fragment and does not rerun the full page.
- This is presentation-only state; market calculations and the One-Brain decision are unchanged.

## Hybrid Lite live monitor

- Adds a lightweight 5-second live monitor for NIFTY and the current ATM CE/PE pair.
- Changes the default full One-Brain snapshot interval to 15 seconds.
- Keeps live quote display separate from the authoritative One-Brain decision, so fast price movement cannot create noisy BUY/SELL flips.
- Uses one batched quote request per fast-monitor cycle; it does not rebuild candles, option chain, news, reports, or the full strategy brain every 5 seconds.
- Pauses both automatic paths when the market session is not live.
- Falls back safely to the last complete snapshot if the lightweight quote request fails.
- Requires no separate server while the Streamlit browser tab remains open.

## Support Bundle and report update

- Adds a one-click credential-free Support Bundle ZIP.
- Bundle contains the Complete Diagnostic PDF, current and previous snapshot summaries, option-chain CSV, and completed spot/futures candle CSVs.
- Adds Price Shock, last-live activity reference, and role-reversal status to reports.
- Replaces report-facing Top-7 wording with Top-9.
- Adds explicit market-data health and Top-9 remaining-market context.
- Keeps Quick Report compact at two pages.
- Treats the NSE 15:15-15:30 Closing Auction Session as reference-only and blocks fresh entries.
- Freezes the last continuous-session activity during CAS while retaining CAS Price Shock as a separate warning.
- Removes duplicate `PRICE SHOCK PRICE SHOCK` wording from PDF reports.

The bundle intentionally excludes Dhan credentials, access tokens, Streamlit secrets, passwords, and order actions.
