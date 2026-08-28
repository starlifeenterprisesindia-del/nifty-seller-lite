# 2.40.5 — Compact evidence notes

- Adds Short note column; Top-9 uses observed recent 15-minute per-stock moves, not evidence percentages or daily breadth.
- Up/down threshold matches existing recent-state logic (+/-0.03%); flat and missing counts are explicit.
- Optional brief pattern/candle/activity notes; other rows remain uncluttered.
- Mobile table scrolls horizontally. No decision weights, thresholds, or trading gates changed.
- Cumulative package includes 2.40.4 reference strike/fit display and recording diagnostics. Provider IV warnings remain visible; this release does not claim to repair or calibrate source Greeks.
