# Nifty Seller Lite 2.30.2

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
