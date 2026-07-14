"""Promise: queue-exactly-once (SQLite backend, multi-runner).

Claim: a durable queue processes each enqueued workflow EXACTLY ONCE across
multiple independent runner PROCESSES that share one SQLite system DB — no
task executed twice, none lost.

This is the exact scenario of dbos-inc issue #541 ("Two runners dequeueing from
one queue in SQLite sometimes both execute the same enqueued workflow"),
reported on v2.7.0a4 and CLOSED by #564 "SQLite Isolation Level"
(commit 0beb275, present in this checkout's HEAD). #564's fix is a single line
in dbos/_sys_db_sqlite.py:43 — `dbapi_conn.isolation_level = "IMMEDIATE"`.

Attack thesis: that fix is INCOMPLETE. Python's sqlite3 legacy isolation mode
issues `BEGIN IMMEDIATE` (which takes the write lock) lazily, only before the
first DML statement. The dequeue in dbos/_sys_db.py:2058-2094 is
SELECT(ENQUEUED)-then-UPDATE(->PENDING); the `.with_for_update(skip_locked=...)`
row lock is a no-op on SQLite. So the dequeue SELECT runs BEFORE any
`BEGIN IMMEDIATE`, outside the write lock. Two runners can therefore both read
the same ENQUEUED row, then serialize only on the UPDATE — and both proceed to
execute the workflow. => double execution survives the #564 fix.

Config mirrors the issue's own repro exactly: a `bar` queue with
worker_concurrency=1, each runner a distinct process with a distinct
executor_id (worker-<pid>), N workflows enqueued, then K runners race to drain.

Cases (argv[1]):
  deq-baseline  1 runner drains the queue; each task runs exactly once. Oracle proof.
  deq-attack    K runners race one queue (issue #541 config); each task must run
                exactly once and none lost. Target of #541/#564.
  deq-selftest  1 runner, then a duplicate execution is planted; oracle MUST RED.

Oracle: each workflow body appends its workflow id to the append-only ledger.
Every enqueued id must appear exactly once (no duplicates) and all N must appear.
"""
import os
import sys
import time
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import dbos_harness as H  # noqa: E402

NTASKS = int(os.environ.get("QL_NTASKS", "120"))
NWORKERS = int(os.environ.get("QL_NWORKERS", "5"))
WORKER_CONC = int(os.environ.get("QL_WORKER_CONC", "1"))
DRAIN_TIMEOUT = int(os.environ.get("QL_DRAIN_TIMEOUT", "90"))
QUEUE = "bar"
SCOPE = "q"


def build(executor_id):
    from dbos import DBOS, Queue
    DBOS(config=H.make_config(H.start_db("queue"), name="queue", executor_id=executor_id))
    q = Queue(QUEUE, worker_concurrency=WORKER_CONC)

    @DBOS.workflow()
    def task(i: int):
        H.record_effect(SCOPE, DBOS.workflow_id)
        return i

    return DBOS, q, task


def enqueuer():
    from dbos import DBOS
    _dbos, q, task = build("enqueuer")
    # Listen to NO queue: this process only enqueues, it must not consume.
    # Without this, DBOS listens to every registered queue by default
    # (dbos/_queue.py:199-203) and the enqueuer races its own destroy(),
    # stealing/erroring tasks. Matches issue #541's foo/bar listen_queues split.
    DBOS.listen_queues([])
    DBOS.launch()
    for i in range(NTASKS):
        q.enqueue(task, i)
    print(f"ENQUEUED {NTASKS}", flush=True)
    DBOS.destroy()


def runner():
    from dbos import DBOS
    _dbos, q, task = build(f"worker-{os.getpid()}")
    DBOS.listen_queues([q])  # this process consumes the bar queue
    DBOS.launch()
    # Drain: stop once the queue has been observed empty twice in a row, or at
    # the hard deadline. list_queued_workflows returns ENQUEUED+PENDING rows.
    deadline = time.time() + DRAIN_TIMEOUT
    empty_streak = 0
    while time.time() < deadline:
        try:
            remaining = len(DBOS.list_queued_workflows(queue_name=QUEUE))
        except Exception:
            remaining = -1
        empty_streak = empty_streak + 1 if remaining == 0 else 0
        if empty_streak >= 3:
            break
        time.sleep(0.5)
    time.sleep(1.0)
    DBOS.destroy()


def spawn_runners(n):
    procs = []
    for _ in range(n):
        env = dict(os.environ)
        env["WIO_WORKER_MODE"] = "runner"
        procs.append(subprocess.Popen([sys.executable, os.path.abspath(__file__)], env=env))
    for p in procs:
        p.wait()


def controller(case):
    H.report_capture_seed()
    H.reset_ledger()
    # fresh DB file so runs don't inherit prior enqueues
    try:
        os.remove(os.path.join(H._statedir(), "queue.sqlite"))
    except FileNotFoundError:
        pass
    print(f"DB: {H.start_db('queue')}", flush=True)
    o = H.Oracle()

    # enqueue in its own process (warms schema before runners race)
    subprocess.run([sys.executable, os.path.abspath(__file__)],
                   env={**os.environ, "WIO_WORKER_MODE": "enqueuer"}, check=True)

    if case == "deq-selftest":
        spawn_runners(1)
        # plant a duplicate execution of whatever ran first
        counts0 = H.effect_counts(SCOPE)
        if counts0:
            H.record_effect(SCOPE, next(iter(counts0)))
    else:
        n = 1 if case.endswith("baseline") else NWORKERS
        print(f"spawning {n} runner(s) for {case} (worker_concurrency={WORKER_CONC})", flush=True)
        spawn_runners(n)

    counts = H.effect_counts(SCOPE)
    distinct = len(counts)
    total = sum(counts.values())
    dups = {k: v for k, v in counts.items() if v > 1}
    print(f"distinct={distinct} total_executions={total} num_duplicated={len(dups)}", flush=True)
    if dups:
        sample = dict(list(dups.items())[:8])
        print(f"DUPLICATED sample={sample}", flush=True)

    o.check("qexactly.no_task_lost", "all-enqueued-tasks-ran",
            distinct == NTASKS, f"distinct={distinct}/{NTASKS}")
    o.check("qexactly.exactly_once", "each-task-executed-exactly-once",
            len(dups) == 0, f"duplicated={len(dups)} total={total} vs distinct={distinct}")
    o.verdict_exit()


if __name__ == "__main__":
    mode = os.environ.get("WIO_WORKER_MODE")
    if mode == "enqueuer":
        enqueuer()
    elif mode == "runner":
        runner()
    else:
        controller(sys.argv[1] if len(sys.argv) > 1 else "deq-baseline")
