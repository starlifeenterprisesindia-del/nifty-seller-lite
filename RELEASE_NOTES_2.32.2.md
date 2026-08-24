# Nifty Seller Lite 2.32.2 — Data Safety

- Closing-auction and post-market NIFTY rows no longer influence intraday EMA,
  MACD, RSI, swing structure, or support/resistance calculations.
- After-hours heavyweight quotes remain visible as reference data but now carry
  zero decision confidence and no directional vote.
- Option Greeks remain DhanHQ-native. Invalid zero or incorrectly signed values
  are treated as unavailable instead of being used for strike selection.
- Added regression coverage for all three safety rules.
