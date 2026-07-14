---
key: queue-exactly-once
area: queues
title: Queue dequeue is exactly-once across concurrent runners (SQLite)
claim: >-
  A durable queue processes each enqueued workflow exactly once across multiple
  independent runner processes sharing one SQLite system database — no task is
  executed twice and none is lost.
status: active
provenance: "issue-snapshot #541 (closed) two-runner double-dequeue on SQLite; fixed by #564 commit 0beb275 (in HEAD) via dbos/_sys_db_sqlite.py:43 isolation_level=IMMEDIATE; dequeue SELECT-then-UPDATE at dbos/_sys_db.py:2058-2094; .with_for_update(skip_locked) no-op on SQLite"
invariant_prefix: qexactly
explorations:
  - key: queue-exactly-once-baseline
    title: Single-runner dequeue baseline
    description: "One runner drains the queue; every enqueued task runs exactly once. Proves the oracle counts executions faithfully."
    status: done
    result: green
    reason: "4/4 seeds GREEN on musl; single runner drains 40 tasks exactly-once (distinct=40/40, no dups). Proves the oracle + that the listen_queues fix isolates enqueue from consume."
    workload: .workers/workloads/queue_limits.py
    command: "/usr/bin/env QL_NTASKS=40 QL_DRAIN_TIMEOUT=70 .workers/pyrun .workers/workloads/queue_limits.py deq-baseline"
    faults: []
    depth: 4
    replay: { run: "01KX1VZYXMTP8PHE79735R1C6X", exploration: "nd7bw7wrpj5bgk2tvmr3e08hbs8a5c02" }
    freshness: new-current
    reported: null
    published: pending
  - key: queue-exactly-once-two-runner
    title: Multi-runner double-dequeue (issue #541 config)
    description: >-
      K=4 independent runner processes, each a distinct executor_id (worker-<pid>),
      race one `bar` queue with worker_concurrency=1 over N=120 enqueued workflows —
      the exact configuration of dbos-inc issue #541. The dequeue is
      SELECT(status=ENQUEUED)-then-UPDATE(->PENDING) and the `for_update
      skip_locked` row lock is a no-op on SQLite; #564's fix only sets pysqlite
      `isolation_level=IMMEDIATE`, which takes the write lock lazily (before the
      first DML, not before the SELECT). So two runners' dequeue SELECTs can both
      observe the same ENQUEUED row and both execute it. Oracle: every task runs
      exactly once (no duplicates) and none is lost.
    status: done
    result: finding
    reason: "FINDING: 10/10 seeds RED on musl/SQLite. Every seed showed 28-42 tasks executed 2-4x (total execs 132-146 vs 120 enqueued) AND 14-32 tasks lost (distinct 88-106/120). #541's two-runner double-dequeue survives #564's isolation fix."
    workload: .workers/workloads/queue_limits.py
    command: "/usr/bin/env QL_NTASKS=120 QL_NWORKERS=4 QL_DRAIN_TIMEOUT=250 .workers/pyrun .workers/workloads/queue_limits.py deq-attack"
    faults: [concurrent-runners]
    depth: 10
    replay: { run: "01KX1Y4NVX0J7JEQB3KS8NGVS7", seed: "1587603904490932047", exploration: "nd78x31xeea1gcfnr8zb04nba18a4h0e" }
    freshness: new-current
    reported: null
    published: pending
---
# Queue dequeue is exactly-once across concurrent runners (SQLite)

## Adversarial model
DBOS durable queues advertise exactly-once dequeue: an enqueued workflow is
executed by exactly one runner. On the SQLite system-DB backend that guarantee
rests entirely on transaction isolation, because the Postgres row lock the
dequeue uses — `.with_for_update(skip_locked=queue.concurrency is None)` at
dbos/_sys_db.py:2058-2078 — is a **no-op on SQLite** (SQLite has no row locks).

The dequeue transaction (`with self.engine.begin()`, dbos/_sys_db.py:1978) runs
SELECT `status == ENQUEUED` and *then* UPDATE the chosen rows to PENDING. Issue
**#541** ("Two runners dequeueing from one queue in SQLite sometimes both
execute the same enqueued workflow") reported this on v2.7.0a4. It was CLOSED by
**#564** "SQLite Isolation Level" (commit 0beb275, present in this checkout's
HEAD), whose entire fix is one line — `dbapi_conn.isolation_level = "IMMEDIATE"`
in dbos/_sys_db_sqlite.py:43.

**Thesis (the attack):** that fix is incomplete. Python's `sqlite3` legacy
isolation mode issues `BEGIN IMMEDIATE` — which acquires the write lock — lazily,
only before the first *DML* statement. The dequeue's leading statement is a
SELECT, which runs before any `BEGIN IMMEDIATE`, i.e. outside the write lock. Two
runners can therefore both execute the SELECT, both see the same ENQUEUED row,
then serialize only on the UPDATE — and both proceed to run the workflow.

## Fault dimensions
- K independent runner OS processes, each a distinct `executor_id`
  (`worker-<pid>`, exactly as issue #541's own repro), on a `worker_concurrency=1`
  queue. No crash needed — the defect is in the dequeue transaction interleaving.
  Schedule diversity comes from per-seed entropy (depth): each seed is a fresh
  enqueue+race with a different interleaving.

## Oracle (append-only effects ledger, independent of DBOS bookkeeping)
Each workflow body appends its workflow id to a plain append-only file (atomic
O_APPEND writes, cross-process safe). After the queue drains:
- `qexactly.no_task_lost` — distinct ids observed == N (nothing stranded).
- `qexactly.exactly_once` — no id appears more than once (nothing double-run).

## Red-proof
`queue_limits.py deq-selftest` runs one runner then plants a single duplicate
execution; `qexactly.exactly_once` goes RED (verified locally). Confirms the
oracle bites before any green or red is trusted.

## Finding (official RED, 10/10 seeds on the fixed checkout)
Batch `nd78x31xeea1gcfnr8zb04nba18a4h0e` (depth 10, K=4 runners,
worker_concurrency=1, N=120) went **RED on all 10 seeds** against HEAD *with #564
present*. Replay run `01KX1Y4NVX0J7JEQB3KS8NGVS7` (seed 1587603904490932047):
`distinct=102 total_executions=146 num_duplicated=39`. Across the 10 seeds, 28–42
tasks each ran 2–4× (total executions 132–146 vs 120 enqueued) and 14–32 tasks
were lost (distinct 88–106/120). i.e. **#564 did not fully fix #541** — the SQLite
two-runner double-dequeue survives, because the IMMEDIATE isolation is not held
across the dequeue SELECT. The single-runner baseline is GREEN, isolating the
defect to the concurrent-dequeue path. (Also reproduced locally, 1–5 dups/run;
the slower musl VM widens the SELECT race window, so cloud reds are far larger.)

## Workload plan
Single file `queue_limits.py`, case selector (`deq-baseline` | `deq-attack` |
`deq-selftest`). The controller enqueues in its own process (warming schema),
then spawns K runner processes that drain the queue (polling
`list_queued_workflows` until empty) and record each execution to the ledger.
Contention tunables via QL_* env baked into the command.
