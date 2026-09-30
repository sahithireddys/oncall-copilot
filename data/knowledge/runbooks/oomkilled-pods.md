---
id: RB-oom
title: Pods restarting with OOMKilled
type: runbook
services: ["*"]
category: memory_leak
---
Signs: `lastState.terminated.reason=OOMKilled`, sawtooth memory, restarts increasing.

1. `kubectl -n otel-demo describe pod <pod>` and check Last State.
2. Is memory growth linear between restarts (leak) or a step after a deploy (new baseline)?
3. Mitigate: `kubectl -n otel-demo rollout restart deployment/<svc>` or raise the limit temporarily.
4. Capture a heap profile before the next OOM (pprof / jmap / tracemalloc).
Risk: restart is low risk; raising limits may affect node packing.
