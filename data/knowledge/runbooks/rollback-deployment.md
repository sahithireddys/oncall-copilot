---
id: RB-rollback
title: Roll back a bad deployment
type: runbook
services: ["*"]
category: bad_deploy
---
Use when errors or latency start within ~20 minutes of a rollout of the failing service.

1. Confirm the timing: `kubectl -n otel-demo rollout history deployment/<svc>`.
2. Compare images: `kubectl -n otel-demo get rs -l app.kubernetes.io/name=<svc> -o wide`.
3. Roll back: `kubectl -n otel-demo rollout undo deployment/<svc>`.
4. Watch: `kubectl -n otel-demo rollout status deployment/<svc>` and the error-rate panel.
Risk: low. Rollbacks are safe unless the release included a schema migration.
