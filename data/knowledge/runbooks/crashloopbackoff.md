---
id: RB-crashloop
title: CrashLoopBackOff triage
type: runbook
services: ["*"]
category: crash_loop
---
1. `kubectl -n otel-demo logs <pod> --previous` shows why the last start failed.
2. Missing env var / config file / bad command -> check the most recent config change or rollout.
3. If a rollout caused it: `kubectl -n otel-demo rollout undo deployment/<svc>`.
4. If an env var changed: restore it with `kubectl set env`.
