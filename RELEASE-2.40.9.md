# 2.40.9 — Recording audit fixes

This cumulative update keeps the single canonical One-Brain unchanged and fixes
the audit/reporting gaps found from the 28-Aug-2026 evidence export.

- The cycle close table now uses the last genuinely-live 15:28–15:30 observation
  when Dhan stops marking data LIVE at the exact close. It never relabels the
  capture time; `Quality` distinguishes exact from last-live.
- Recording diagnostics distinguish background recorder progress from the last
  opened Streamlit-app AI sync.
- Greeks diagnostics show source-complete, usable and per-quality row counts.
- Last recorded Top-9 state is shown as history reference only when the market is
  closed; it never receives a live vote.
- Missing minutes remain missing and are never backfilled or treated as evidence.

Commit: `Update v2.40.9: fix close checkpoint and recording diagnostics`
