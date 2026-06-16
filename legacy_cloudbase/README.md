# Legacy CloudBase Archive

This folder keeps the old CloudBase / CloudRun deployment assets out of the active project root.

Current production-like deployment uses the CPU DevCloud container flow documented in:

```text
docs/devcloud-container-deployment.md
```

## Contents

- `config/`
  - `Dockerfile`
  - `.dockerignore`
  - `.cloudbaseignore`
  - `cloudbaserc.json`
- `scripts/`
  - `deploy_all.sh`
  - `deploy_cloudrun.py`
  - `com.feedback_hub.daily_sync.plist`
  - `feedback_hub/auto_daily.sh`
  - `feedback_hub/bootstrap_sync.py`
  - `feedback_hub/sync_to_cloud.py`
- `runtime/`
  - `entrypoint.sh`
- `docs/`
  - CloudBase deployment and auto-sync notes.
- `skills/`
  - Local CloudBase skill/plugin material kept for reference.

## Notes

- These files are retained for reference only.
- Do not use them for the current DevCloud container deployment.
- For code or frontend updates, run `APP_PORT=8000 ./deploy_devcloud.sh` from the project root.
- For data-only updates, sync `feedback_hub/data/feedback.db` to the remote container as described in `docs/devcloud-container-deployment.md`.
