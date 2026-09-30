---
id: RB-postgres
title: Postgres slow queries and lock contention
type: runbook
services: [postgresql, accounting, product-catalog]
category: datastore_degradation
---
1. `kubectl -n otel-demo exec deploy/postgresql -- psql -U root -d otel -c "SELECT pid, now()-query_start AS age, state, query FROM pg_stat_activity WHERE state<>'idle' ORDER BY age DESC LIMIT 5"`
2. Blocking locks: `SELECT * FROM pg_locks WHERE NOT granted`.
3. Cancel the offender: `SELECT pg_cancel_backend(<pid>)`.
4. Consumers lagging behind a slow DB recover by themselves once latency drops; don't scale them first.
