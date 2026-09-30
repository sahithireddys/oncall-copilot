---
id: RB-flagd
title: Check and revert feature flags (flagd)
type: runbook
services: ["*"]
category: config_change
---
The demo's failure flags live in the `flagd-config` ConfigMap: paymentFailure, paymentUnreachable, adFailure, adHighCpu,
adManualGc, cartFailure, productCatalogFailure, recommendationCacheFailure, kafkaQueueProblems, imageSlowLoad,
emailMemoryLeak, failedReadinessProbe.

1. `kubectl -n otel-demo get cm flagd-config -o jsonpath='{.data.demo\.flagd\.json}' | jq '.flags | map_values(.defaultVariant)'`
2. Set the flag's defaultVariant back to "off" (or use the flagd UI at /feature).
