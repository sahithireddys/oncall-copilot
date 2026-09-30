---
id: PM-2026-08-02
title: Currency crash loop after a release missing its data file
type: postmortem
date: 2026-08-02
services: [currency, frontend, checkout]
category: crash_loop
---
## Summary
Currency 2.0.1 shipped without `currency_conversion.json`. Pods exited on start-up (`std::runtime_error`), entered
CrashLoopBackOff and frontend errors reached 60% because almost every page converts prices.

## Root cause
Crash loop introduced by a bad release (packaging error).

## Resolution
`kubectl rollout undo deployment/currency`.

## Lessons
- `waiting: CrashLoopBackOff` + near-zero request rate on the callee + `Unavailable` in callers = crash loop.
- Add a startup smoke test to the image build.
