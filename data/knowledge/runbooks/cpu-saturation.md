---
id: RB-cpu
title: CPU saturation and throttling
type: runbook
services: ["*"]
category: cpu_saturation
---
1. `kubectl -n otel-demo top pod -l app.kubernetes.io/name=<svc>` - at the limit?
2. Was the CPU limit changed recently? `kubectl -n otel-demo get deploy <svc> -o jsonpath='{.spec.template.spec.containers[0].resources}'`
3. Mitigate: scale out (`kubectl scale`) or raise the limit (`kubectl set resources`).
