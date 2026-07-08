"""Promise: queue-rate-and-concurrency-limits-are-global.

Claims (README: "rate limit how often queued tasks are executed",
"control how many workflows run at once"):
  * rate limiter: across the whole cluster, no more than `limit` workflows
    START per `period` window.
  * global concurrency: across the whole cluster, no more than `concurrency`
    workflows are executing at once.

Attack surface (scouts, source): dbos/_sys_db.py:2057
  `skip_locks = queue.concurrency is None` -> a limiter-only queue dequeues with
  SKIP LOCKED and reads the trailing-window start count under a REPEATABLE READ
  snapshot, per transaction. Multiple independent executor PROCESSES each see
  the same pre-window count and each admit up to `limit`, so the cluster can
  start up to K*limit per period. The concurrency path uses `nowait` FOR UPDATE
  but the PENDING count is still snapshot-isolated (:2011-2053) and only warns
  on breach (:2043-2053).

Cases (argv[1]):
  rl-baseline   1 executor on a limiter queue; oracle observes <= limit/period.
  rl-attack     K executors on one limiter queue; look for a window > limit.
  rl-selftest   plant an over-admission (extra starts in one window) -> RED.
  conc-baseline 1 executor on a concurrency=C queue; overlap <= C.
  conc-attack   K executors on one concurrency=C queue; look for overlap > C.

Each task records a `start`/`end` effect pair with clock timestamps; oracles
are computed from those timestamps, independent of DBOS bookkeeping.
"""
import os
import sys
import time
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import dbos_harness as H  # noqa: E402
import psycopg  # noqa: E402

# tunables (kept modest to bound cost; clear separation so boundary fuzz can't
# masquerade as a finding)
LIMIT = int(os.environ.get("QL_LIMIT", "2"))
PERIOD = float(os.environ.get("QL_PERIOD", "3.0"))
CONC = int(os.environ.get("QL_CONC", "2"))
NTASKS = int(os.environ.get("QL_NTASKS", "18"))
NWORKERS = int(os.environ.get("QL_NWORKERS", "8"))
RUN_SECONDS = int(os.environ.get("QL_RUN_SECONDS", "40"))
QUEUE = "wio_limited_q"
SCOPE = "ql"


def define(uri, case):
    from dbos import DBOS, Queue
    cfg = H.make_config(uri, name="qlimits")
    DBOS(config=cfg)
    if case.startswith("rl"):
        q = Queue(QUEUE, limiter={"limit": LIMIT, "period": PERIOD})
    else:
        q = Queue(QUEUE, concurrency=CONC)

    hold = float(os.environ.get("QL_HOLD", "1.0"))

    @DBOS.workflow()
    def task(i: int):
        # record start then a short hold then end, so concurrency overlap is
        # observable; timestamps come from the DB clock.
        H.record_effect(uri, SCOPE, f"start:{i}")
        time.sleep(hold)
        H.record_effect(uri, SCOPE, f"end:{i}")
        return i

    return DBOS, q, task, uri


def worker(case, do_enqueue):
    from dbos import DBOS
    DBOS_, q, task, uri = define(uri=H.start_pg(), case=case)
    DBOS.launch()
    if do_enqueue:
        for i in range(NTASKS):
            q.enqueue(task, i)
        print(f"ENQUEUED {NTASKS} tasks", flush=True)
    # run long enough to drain, then exit
    time.sleep(RUN_SECONDS)
    DBOS.destroy()


# ---- oracle helpers ------------------------------------------------------

def fetch_events(uri, kind):
    """Return sorted list of ts for start/end effects of the given kind."""
    with psycopg.connect(uri, autocommit=True) as c:
        rows = c.execute(
            "SELECT label, ts FROM wio_effects WHERE scope=%s AND label LIKE %s ORDER BY ts",
            (SCOPE, kind + ":%"),
        ).fetchall()
    return [(lbl, float(ts)) for (lbl, ts) in rows]


def max_starts_in_window(starts, period):
    ts = sorted(t for _, t in starts)
    best = 0
    j = 0
    for i in range(len(ts)):
        # count starts in (ts[i]-period, ts[i]]
        lo = ts[i] - period
        cnt = sum(1 for t in ts if lo < t <= ts[i])
        best = max(best, cnt)
    return best


def max_concurrent(starts, ends):
    # interval overlap: pair start:i with end:i
    smap = {lbl.split(":", 1)[1]: t for lbl, t in starts}
    emap = {lbl.split(":", 1)[1]: t for lbl, t in ends}
    events = []
    for i, st in smap.items():
        en = emap.get(i, st + 1.0)
        events.append((st, +1))
        events.append((en, -1))
    events.sort(key=lambda x: (x[0], -x[1]))
    cur = best = 0
    for _, d in events:
        cur += d
        best = max(best, cur)
    return best


# ---- controller ----------------------------------------------------------

def spawn_workers(case):
    script = os.path.abspath(__file__)
    procs = []
    for k in range(NWORKERS):
        env = dict(os.environ)
        env["WIO_WORKER_MODE"] = "worker"
        env["WIO_CASE"] = case
        env["WIO_ENQUEUE"] = "1" if k == 0 else "0"
        procs.append(subprocess.Popen([sys.executable, script], env=env))
    for p in procs:
        p.wait()


def warm_schema(uri, case):
    """Run DBOS migrations once (serially) before spawning concurrent workers,
    so their simultaneous first-launch migration runs are all no-ops. Otherwise
    concurrent CREATE EXTENSION/TABLE IF NOT EXISTS race at the Postgres level."""
    DBOS_, q, task, _ = define(uri, case)
    from dbos import DBOS
    DBOS.launch()
    DBOS.destroy()
    print("schema warmed", flush=True)


def controller(case):
    H.derive_seed()
    uri = H.start_pg()
    print(f"PG up: {uri}", flush=True)
    H.ensure_effects_table(uri)
    warm_schema(uri, case)
    o = H.Oracle()

    if case == "rl-selftest":
        # single worker (correct behavior), then plant extra starts in one window
        os.environ["WIO_ONE"] = "1"
        _run_single_worker(case)
        starts = fetch_events(uri, "start")
        base_t = starts[0][1] if starts else time.time()
        with psycopg.connect(uri, autocommit=True) as c:
            for x in range(LIMIT + 2):
                c.execute(
                    "INSERT INTO wio_effects(scope,label,ts) VALUES (%s,%s,%s)",
                    (SCOPE, f"start:plant{x}", base_t + 0.1 * x),
                )
        mx = max_starts_in_window(fetch_events(uri, "start"), PERIOD)
        o.check("qlimits.rate_le_limit", "rate-le-limit-SELFTEST",
                mx <= LIMIT, f"max starts in {PERIOD}s window={mx} limit={LIMIT} (planted)")
        o.verdict_exit()

    n_workers = 1 if case.endswith("baseline") else NWORKERS
    print(f"spawning {n_workers} workers for {case}", flush=True)
    if n_workers == 1:
        _run_single_worker(case)
    else:
        spawn_workers(case)

    starts = fetch_events(uri, "start")
    ends = fetch_events(uri, "end")
    n_started = len(starts)
    print(f"observed {n_started} starts, {len(ends)} ends", flush=True)

    attack = not case.endswith("baseline")
    if case.startswith("rl"):
        mx = max_starts_in_window(starts, PERIOD)
        if attack:
            o.check("qlimits.exercised", "race-exercised", n_started >= LIMIT + 1,
                    f"started={n_started} (need >= {LIMIT+1} to evaluate window)")
        else:
            o.check("qlimits.progress", "all-tasks-ran", n_started == NTASKS,
                    f"started={n_started}/{NTASKS}")
        o.check("qlimits.rate_le_limit", "rate-le-limit",
                mx <= LIMIT, f"max starts in {PERIOD}s window={mx} limit={LIMIT}")
    else:
        mc = max_concurrent(starts, ends)
        if attack:
            o.check("qlimits.exercised", "race-exercised", n_started >= CONC + 1,
                    f"started={n_started} (need >= {CONC+1} to evaluate overlap)")
        else:
            o.check("qlimits.progress", "all-tasks-ran", n_started == NTASKS,
                    f"started={n_started}/{NTASKS}")
        o.check("qlimits.conc_le_limit", "concurrency-le-limit",
                mc <= CONC, f"max concurrent={mc} limit={CONC}")
    o.verdict_exit()


def _run_single_worker(case):
    script = os.path.abspath(__file__)
    env = dict(os.environ)
    env["WIO_WORKER_MODE"] = "worker"
    env["WIO_CASE"] = case
    env["WIO_ENQUEUE"] = "1"
    subprocess.Popen([sys.executable, script], env=env).wait()


if __name__ == "__main__":
    if os.environ.get("WIO_WORKER_MODE") == "worker":
        worker(os.environ["WIO_CASE"], os.environ.get("WIO_ENQUEUE") == "1")
    else:
        controller(sys.argv[1] if len(sys.argv) > 1 else "rl-baseline")
