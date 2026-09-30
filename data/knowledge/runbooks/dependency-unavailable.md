---
id: RB-unavailable
title: A downstream service is unreachable
type: runbook
services: ["*"]
category: dependency_unavailable
---
Signs: callers log `connection refused` / `no such host`; the callee's request rate drops to ~0.
1. `kubectl -n otel-demo get deploy <svc>` - are there 0 ready replicas? Scaled to zero?
2. `kubectl -n otel-demo get endpoints <svc>`.
3. If the name doesn't resolve, the *caller's* config is wrong (see RB-flagd / env diffs).
Fix: `kubectl -n otel-demo scale deployment/<svc> --replicas=1`.
