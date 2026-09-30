---
id: PM-2026-03-12
title: Cart checkout failures from Redis connection-pool exhaustion
type: postmortem
date: 2026-03-12
services: [cart, valkey-cart, frontend]
category: resource_exhaustion
---
## Summary
For 47 minutes, 18% of add-to-cart and checkout requests failed with `RedisTimeoutException ... pool busy=8/8`.
The cart service's Redis client pool had been reduced to 8 connections in a refactor (PR #2231). Valkey itself was healthy
(CPU 9%, p99 2ms). Frontend alerted on 5xx ratio.

## Detection
FrontendHighErrorRate fired first. The on-call initially suspected Valkey because of a pod restart earlier that day during a
node drain. That restart was unrelated and cost ~20 minutes.

## Root cause
Connection pool exhaustion in cart. Requests queued waiting for a free connection and hit the 5s timeout.

## Resolution
Raised the pool size back to 64 and restarted cart. Error rate returned to baseline within 2 minutes.

## Lessons
- Check the *client* side of a datastore before blaming the datastore: if the server's latency is flat but the client
  times out, the pool is the bottleneck.
- Alert on pool wait time (`redis_pool_wait_ms`).
