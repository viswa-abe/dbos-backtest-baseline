---
key: durability
title: Durable Workflow Execution
description: "Workflows checkpoint to Postgres and resume exactly once across crash and restart."
order: 10
---
# Durability

DBOS's headline guarantee (README): "DBOS workflows make your program durable
by checkpointing its state in Postgres. If your program ever fails, when it
restarts all your workflows will automatically resume from the last completed
step."

Boundaries: this area covers the workflow/step execution core — durable
completion, step checkpointing, and recovery of pending workflows. Queue
scheduling semantics (rate/concurrency limits, dequeue exactly-once) live in
the `queues` area; idempotency of explicit workflow IDs and messaging live in
their own areas as they are promoted.

Provenance: README.md#L37-60 (Durable Workflows), dbos/_core.py step
memoization, dbos/_recovery.py recovery path.
