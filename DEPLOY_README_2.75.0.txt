DEPLOY PACKAGE — v2.75.0_LIVE_READABILITY_VALIDATION_HARDENING

Upload/replace the files from the deploy ZIP as one version. Do not mix individual v2.75 files with an older runtime bundle.

Required top-level deployment files:
- app.py
- live_server.py
- config.py
- models.py
- requirements.txt
- railway.toml
- nsl_runtime_v275.zip
- README.md
- RELEASE_NOTES_2.75.0_LIVE_READABILITY_VALIDATION_HARDENING.txt
- BUILD_VALIDATION_2.75.0.txt

The deploy package intentionally keeps analysis/services/ui inside the runtime ZIP so the GitHub upload remains compact. Runtime state, logs, caches, evidence files and secrets are not packaged.

First live-session checks after deploy:
1. App version shows 2.75.0_LIVE_READABILITY_VALIDATION_HARDENING.
2. Journal restores historical decisions after restart rather than showing a false zero.
3. Top-9 shows proper LIVE/WARMING/REFERENCE/NO-VOTE state.
4. Telegram Market Intelligence alerts are materially quieter (STRONG ONLY).
5. Same MI setup does not create a new paper sample merely because cooldown elapsed.
6. One Brain research samples can be created from qualifying candidate_action while live final action remains WAIT.
7. Snapshot timing no longer stalls on stale Instrument Master refresh.
8. Regime text, pattern/evidence highlights and barrier attack visuals display correctly on laptop/phone.
9. Calculator accepts large lot what-if values while keeping the live risk warning/guard.
10. Master Live Test Pack generates successfully after sufficient session evidence exists.

Do not tune weights/thresholds during the first live verification session.
