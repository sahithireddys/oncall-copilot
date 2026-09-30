---
id: RB-probes
title: Readiness probe failures
type: runbook
services: ["*"]
category: probe_failure
---
Failing readiness probes remove pods from Service endpoints; callers get 503 / "no healthy upstream".
1. `kubectl -n otel-demo get endpoints <svc>` - fewer addresses than replicas?
2. `kubectl -n otel-demo describe pod <pod>` - look for `Unhealthy` events.
3. Check the probe path and thresholds, and the failedReadinessProbe flag.
