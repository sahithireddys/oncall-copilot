---
id: PM-2026-06-17
title: Accounting consumer lag from a long-running Postgres migration
type: postmortem
date: 2026-06-17
services: [postgresql, accounting, product-catalog, kafka]
category: datastore_degradation
---
## Summary
A manual `ALTER TABLE` on `orders` held a lock for 40 minutes. Accounting's inserts slowed from 30ms to ~1s, and its Kafka
consumer lag grew past 5,000 messages. Product-catalog reads were also slower. The alert fired on consumer lag.

## Root cause
Datastore degradation in postgresql (lock contention). Accounting was healthy but blocked on the database.

## Resolution
Cancelled the migration via `pg_cancel_backend`, re-ran it with `LOCK_TIMEOUT` during low traffic.

## Lessons
- When a consumer lags, check its dependencies' latency before scaling the consumer.
- `SELECT * FROM pg_stat_activity WHERE state <> 'idle' ORDER BY query_start` finds the blocker.
