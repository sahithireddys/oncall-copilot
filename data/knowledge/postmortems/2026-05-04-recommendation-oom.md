---
id: PM-2026-05-04
title: Recommendation OOM loop from an unbounded product cache
type: postmortem
date: 2026-05-04
services: [recommendation, frontend]
category: memory_leak
---
## Summary
Recommendation was OOMKilled every ~7 minutes for two hours. Each restart dropped in-flight requests, so frontend saw periodic
bursts of 5xx. Memory grew linearly from ~45% to 100% of the limit between restarts (sawtooth).

## Root cause
An in-process cache keyed by product-id list had no eviction. The `recommendationCacheFailure` flag reproduces this.

## Resolution
Restarted the deployment to stop the bleeding, then shipped an LRU bound of 10k entries.

## Lessons
- Sawtooth memory + `OOMKilled` last-termination reason + restarts is a memory leak, not a traffic spike.
- Restarting buys time; it is not the fix.
