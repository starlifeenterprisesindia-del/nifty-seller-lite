DEPLOY PACKAGE — v2.76.0_EDGE_CONTEXT_INTEGRATION

Complete-source deployment:
1. Keep analysis/, services/, ui/, tests/, app.py, config.py, models.py, live_server.py, requirements*.txt and railway.toml together.
2. Keep nsl_runtime_v2760.zip beside app.py as fallback. Do NOT extract the runtime ZIP inside the repository.
3. If upgrading an existing v2.75.2 repo, remove the old nsl_runtime_v2752.zip after the new package is in place so the repository stays clean.
4. data/ may be empty. Runtime state is created automatically as needed and is git-ignored.
5. Direct source folders take priority over the runtime fallback ZIP.

Expected app version: 2.76.0_EDGE_CONTEXT_INTEGRATION
Expected fallback runtime: nsl_runtime_v2760.zip

No credential, token or generated runtime data is included.
