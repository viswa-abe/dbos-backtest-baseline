# Runs — queue-exactly-once (SQLite, musl runtime)

## queue-exactly-once-baseline — GREEN
- Batch (explorationId): `nd7bw7wrpj5bgk2tvmr3e08hbs8a5c02`, depth 4 → 4/4 succeeded.
- Representative run: `01KX1VZYXMTP8PHE79735R1C6X`.
- Command: `QL_NTASKS=40 QL_DRAIN_TIMEOUT=70 .workers/pyrun .workers/workloads/queue_limits.py deq-baseline`
- Evidence: `distinct=40 total_executions=40 num_duplicated=0` →
  `qexactly.no_task_lost PASS`, `qexactly.exactly_once PASS`, `VERDICT GREEN`.
- Proves the oracle counts executions faithfully and that a single runner
  dequeues exactly-once (and that the `listen_queues` split isolates enqueue
  from consume — an earlier version without it was flaky, distinct=39/40).

## queue-exactly-once-two-runner — FINDING (RED, 10/10 seeds)
- Batch (explorationId): `nd78x31xeea1gcfnr8zb04nba18a4h0e`, depth 10 → **10/10 failed (RED)**.
- Command: `QL_NTASKS=120 QL_NWORKERS=4 QL_DRAIN_TIMEOUT=250 .workers/pyrun .workers/workloads/queue_limits.py deq-attack`
- Replay run: `01KX1Y4NVX0J7JEQB3KS8NGVS7`, SEED `1587603904490932047`:
  ```
  distinct=102 total_executions=146 num_duplicated=39
  INVARIANT qexactly.no_task_lost all-enqueued-tasks-ran FAIL distinct=102/120
  INVARIANT qexactly.exactly_once each-task-executed-exactly-once FAIL duplicated=39 total=146 vs distinct=102
  VERDICT RED 2/2 invariants failed
  ```
- Every one of the 10 seeds was RED. Across seeds: 28–42 tasks each executed
  2–4× (total executions 132–146 vs 120 enqueued) and 14–32 tasks lost
  (distinct 88–106 / 120). Per-seed summary:
  | run | seed | distinct/120 | total exec | dup tasks |
  |---|---|---|---|---|
  | 01KX1Y4NVX0J7JEQB3KS8NGVS7 | 1587603904490932047 | 102 | 146 | 39 |
  | 01KX1Y4NVX6M290FT0EYAFHMF9 | 9607556203142026758 | 100 | 144 | 32 |
  | 01KX1Y4NVXVZE1DVKNXY3GV1DP | 4496193608092806981 | 95 | 146 | 42 |
  | 01KX1Y4NVXZYC0GP8N5XX7G25X | 16713038949516834997 | 88 | 132 | 36 |
  | 01KX1Y4NVXQ9GAD7TYNW55C9BJ | 9693237476511360976 | 106 | 137 | 28 |
  | 01KX1Y4NVX568BF89GFF59V4GT | 3446865165214696223 | 94 | 143 | 33 |
  | 01KX1Y4NVXJRX7EMJZPK425FBT | 7440783644227845192 | 102 | 145 | 33 |
  | 01KX1Y4NVXYCB402PMTE8P6SV7 | 15533741015623880112 | 95 | 140 | 39 |
  | 01KX1Y4NVXDQ374K9KN2BP7C1S | 10847192148715601748 | 94 | 138 | 37 |
  | 01KX1Y4NVXF6AT00F9BKBYBEY2 | 6237945081129925532 | 95 | 134 | 32 |

### Interpretation
Issue **#541** (SQLite two-runner double-dequeue) was closed by **#564**
(commit 0beb275, in this checkout's HEAD), whose fix is a single line —
`dbapi_conn.isolation_level = "IMMEDIATE"` in dbos/_sys_db_sqlite.py:43. That fix
is **incomplete**. The dequeue (dbos/_sys_db.py:1978 `with engine.begin()`,
:2058-2094) runs `SELECT status==ENQUEUED` *then* `UPDATE ->PENDING`, and the
`.with_for_update(skip_locked=...)` row lock is a no-op on SQLite. pysqlite's
legacy `isolation_level="IMMEDIATE"` issues `BEGIN IMMEDIATE` (which takes the
write lock) lazily — only before the first DML statement, not before the leading
SELECT. So concurrent runners' dequeue SELECTs execute outside the write lock,
both observe the same ENQUEUED row, and both transition+execute it. Result:
tasks run multiple times (exactly-once violated) and some are stranded
(no_task_lost violated). The single-runner baseline is GREEN, isolating the
defect to the concurrent-dequeue path. Oracle proven to bite via local
`deq-selftest` (planted dup → RED).

### Suggested direction (not applied — target is a customer repo)
Hold the write lock across the dequeue SELECT (e.g. issue an explicit
`BEGIN IMMEDIATE` at transaction start for the SQLite dequeue, per the SQLAlchemy
pysqlite recipe: `isolation_level=None` + a `begin` event emitting BEGIN
IMMEDIATE), so the SELECT and the ->PENDING UPDATE are one serialized critical
section.
