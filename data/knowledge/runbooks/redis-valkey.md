---
id: RB-valkey
title: Cart / Valkey errors and timeouts
type: runbook
services: [cart, valkey-cart]
category: resource_exhaustion
---
Decide whether the problem is the client pool or the server.

- Server: `kubectl -n otel-demo exec deploy/valkey-cart -- valkey-cli --latency` and `SLOWLOG GET 10`.
  High latency or `BUSY` => datastore degradation.
- Client: server latency flat but cart logs `pool busy=N/N` or "timeout awaiting connection" => pool exhaustion.
  Raise the pool size and restart cart.
