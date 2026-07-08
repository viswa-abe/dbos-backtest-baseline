---
key: queues
title: Durable Queues
description: "Durable queues run tasks with global rate limits, concurrency caps, and exactly-once dequeue."
order: 20
---
# Durable Queues

DBOS durable queues (README): "You can use queues to ensure that functions run
in a controlled manner — for example, to rate-limit calls to an external API or
to limit concurrency." The advertised controls are cluster-wide: a rate limiter
("no more than `limit` per `period`") and a concurrency cap ("no more than
`concurrency` workflows running at once"), plus exactly-once dequeue under
concurrent executors and crashes.

Boundaries: covers queue scheduling invariants — rate/concurrency-limit
enforcement, dequeue exactly-once, priority ordering, deduplication, DLQ.
Recovery of a queued workflow's *body* durability rolls up to `durability`.

Provenance: README queues section; dbos/_queue.py; dbos/_sys_db.py
start_queued_workflows (L1968-2160), limiter count (L1984-2005),
`skip_locks = queue.concurrency is None` (L2057).
