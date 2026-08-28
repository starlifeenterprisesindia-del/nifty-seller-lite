# 2.40.6 — Local Greeks guard

- Main screen: single concise Greeks health line, diagnostics in collapsed panel.
- Invalid opposite-leg Greeks no longer contaminate a valid contract via pair checks. Pair checks grouped by expiry when provided.
- Preserves quotes/OI, source values, decision weights and thresholds. No global Greeks gate added.
- Invalid contracts remain excluded from Greek-based selection/projection. IV-warning contracts remain conditional; automatic retest premium remains disabled. Existing selected-contract calculator warnings retained.
- This fixes scope and presentation, not the provider's pricing model. If no safe eligible contract exists, final action can still be WAIT.
- Includes prior Top-9 short notes, strike/fit reference display and recording diagnostics.
