---
id: PM-2026-01-20
title: Payment 1.7.3 release failed card charges with a TypeError
type: postmortem
date: 2026-01-20
services: [payment, checkout]
category: bad_deploy
---
## Summary
A payment release changed the shape of the `amount` object. 35% of charges failed with
`TypeError: Cannot read properties of undefined (reading 'units')`. Checkout's error-rate alert fired 9 minutes after rollout.

## Root cause
Bad deploy. The new code assumed `amount.units` was always set; older clients sent `amount.nanos` only.

## Resolution
`kubectl rollout undo deployment/payment`. Errors stopped within one minute.

## Lessons
- When checkout errors and traces end in payment, check payment's rollout history first.
- Canary payment releases at 5% with automatic rollback on error-rate SLO burn.
