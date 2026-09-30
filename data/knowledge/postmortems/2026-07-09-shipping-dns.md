---
id: PM-2026-07-09
title: Shipping failed after a quote address change
type: postmortem
date: 2026-07-09
services: [shipping, quote, checkout]
category: config_change
---
## Summary
A Helm values change renamed the quote service address. Shipping could not resolve `quote-svc` and 90% of checkouts failed
at the shipping step with `dns error`.

## Root cause
Configuration change on shipping (QUOTE_ADDR). Quote itself was healthy and idle.

## Resolution
Reverted the env var and redeployed shipping.

## Lessons
- `no such host` / `dns error` in a caller plus an idle callee is a caller-side config problem.
- Diff env vars between the last two ReplicaSets: `kubectl rollout history deployment/shipping --revision=N`.
