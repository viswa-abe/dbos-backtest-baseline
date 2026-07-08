---
key: queue-limits-global
area: queues
title: Queue rate & concurrency limits are global
claim: >-
  A durable queue's rate limit ("no more than `limit` starts per `period`") and
  concurrency cap ("no more than `concurrency` running at once") hold across the
  whole cluster, not merely per executor process.
status: active
provenance: "README durable-queues; dbos/_sys_db.py:2057 skip_locks = queue.concurrency is None; limiter count L1984-2005; concurrency warn-only L2043-2053"
invariant_prefix: qlimits
explorations:
  - key: queue-limits-global-rl-baseline
    title: Rate limit baseline (single executor)
    description: "One executor on a limiter queue; oracle observes <= limit starts per period. Proves the oracle."
    status: ready
    result: null
    reason: null
    workload: .workers/workloads/queue_limits.py
    command: "QL_HOLD=0.05 .workers/venv/bin/python3 .workers/workloads/queue_limits.py rl-baseline"
    faults: []
    depth: 6
    replay: null
    freshness: new-current
    reported: null
    published: null
  - key: queue-limits-global-rl-attack
    title: Rate limit under concurrent executors
    description: >-
      8 independent executor processes poll one limiter-only queue
      (limit=2/period=3s). The limiter count is read per-transaction under a
      REPEATABLE READ snapshot and the dequeue uses SKIP LOCKED (skip_locks =
      queue.concurrency is None), so executors do not serialize — each admits up
      to `limit`. Oracle: no `period` window has more than `limit` starts.
    status: ready
    result: null
    reason: null
    workload: .workers/workloads/queue_limits.py
    command: "QL_HOLD=0.05 .workers/venv/bin/python3 .workers/workloads/queue_limits.py rl-attack"
    faults: []
    depth: 12
    replay: null
    freshness: new-current
    reported: null
    published: null
  - key: queue-limits-global-conc-attack
    title: Concurrency cap under concurrent executors
    description: >-
      8 executor processes poll one queue with concurrency=2; each task holds a
      slot ~1s. Oracle: at no instant do more than `concurrency` tasks overlap.
    status: ready
    result: null
    reason: null
    workload: .workers/workloads/queue_limits.py
    command: ".workers/venv/bin/python3 .workers/workloads/queue_limits.py conc-attack"
    faults: []
    depth: 10
    replay: null
    freshness: new-current
    reported: null
    published: null
---
# Queue rate & concurrency limits are global

## Adversarial model
DBOS advertises rate/concurrency controls as properties of the queue (README),
implying cluster-wide enforcement. The dequeue path enforces them per executor
transaction: the trailing-window start count (limiter) and PENDING count
(concurrency) are read under REPEATABLE READ, and for a limiter-only queue
`skip_locks = queue.concurrency is None` (dbos/_sys_db.py:2057) makes dequeue use
SKIP LOCKED, so concurrent executors grab disjoint rows and each independently
admits up to the limit. The concurrency path locks head rows with `nowait` but
still computes capacity from a snapshot-isolated count and only *warns* on breach
(L2043-2053).

## Fault dimensions
- Multiple concurrent executor processes (8) polling one queue. No crash needed;
  the race is in the dequeue transaction interleaving. Schedule diversity comes
  from the sim's per-seed entropy (depth) — different interleavings per seed.

## Oracle (timestamp ledger, independent of DBOS bookkeeping)
Each task records `start:<i>` and `end:<i>` effects with DB clock timestamps.
- `qlimits.rate_le_limit` — max starts in any trailing `period` window <= limit.
- `qlimits.conc_le_limit` — max overlapping [start,end] intervals <= concurrency.
- `qlimits.exercised` — enough starts observed to evaluate the window (attack).
- `qlimits.progress` — all tasks ran (baseline).

## Red-proof
`queue_limits.py rl-selftest` plants extra starts in one window; oracle goes RED.
Verified locally. The attack itself reproduced RED locally: 8 executors admitted
4 starts in a 3s window with limit=2.

## Workload plan
Single file `queue_limits.py`, case selector; worker-0 enqueues then all workers
consume as independent OS processes. Contention tunables via QL_* env
(defaults limit=2, period=3, 8 workers, 18 tasks) baked into the command.
