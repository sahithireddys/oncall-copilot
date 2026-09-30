---
id: RB-kafka-lag
title: Kafka consumer lag growing
type: runbook
services: [kafka, accounting, fraud-detection]
category: queue_backlog
---
1. Is the consumer running? CrashLoopBackOff on the consumer => fix that first.
2. Is the consumer slow because of a dependency (Postgres latency)? Fix the dependency.
3. Is the consumer itself slow per message (kafkaQueueProblems flag, CPU)? Scale consumers up to the partition count.
`kubectl -n otel-demo exec deploy/kafka -- /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --all-groups`
