---
id: RB-network
title: Latency or timeouts between two services
type: runbook
services: ["*"]
category: network_latency
---
If the caller's client span is slow but the callee's server span is fast (or missing), the time is spent on the network.

1. Check for Chaos Mesh experiments: `kubectl get networkchaos -A`.
2. Check the node: `kubectl get pods -o wide` - are the slow calls all to pods on one node?
3. Check Envoy fault injection (image-provider uses `imageSlowLoad`).
