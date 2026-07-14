"""Shared harness for DBOS workload cases.

Runtime reality: the wio bhyve simulation guest is FreeBSD. Preparation builds
Python and DBOS's pure-Python dependencies in a FreeBSD jail, then captures
`/usr/local` for the guest. DBOS runs against its first-class **SQLite**
system-DB backend here. The Postgres locking path (`FOR UPDATE ... SKIP LOCKED`)
is a no-op on SQLite (dbos/_sys_db.py:2078); mutual exclusion instead rests on
SQLite IMMEDIATE transactions (dbos/_sys_db_sqlite.py) — a recently-churned
surface (#541/#553/#559/#564) and a high-value target for exactly-once dequeue.

Design (single-command, self-contained; the guest runs ONE command per case):
  * A single shared SQLite FILE under /tmp is the system + application DB, so
    independent executor PROCESSES coordinate through it.
  * The controller drives the SUT lifecycle: spawns DBOS executor subprocesses,
    SIGKILLs them mid-workflow (os._exit) to simulate crashes, starts recovery
    executors. Process faults are the workload's own subprocess calls.
  * Oracles use an application-independent, append-only effects ledger (a plain
    file, atomic O_APPEND writes) so exactly-once / overlap violations are
    directly countable without touching DBOS bookkeeping.
  * Evidence channel is stdout: one `INVARIANT <id> <name> PASS|FAIL <summary>`
    line per oracle clause; exit code is the verdict (0 green, 1 red, 2 setup).

  WIO records the per-run exploration seed outside the guest. The environment
  available to the workload contains the snapshot's capture seed, which is
  useful provenance but must not be mistaken for the exploration replay key.
"""
import os
import sys
import time
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def report_capture_seed():
    seed_hex = os.environ.get("WORKERS_SEED_HEX", "unknown")
    print(f"CAPTURE_SEED {seed_hex}", flush=True)


# ---- database ------------------------------------------------------------

def _statedir():
    d = os.environ.get("WIO_STATE", "/tmp/wio_dbos")
    os.makedirs(d, exist_ok=True)
    return d


def start_db(name="wio"):
    """Return the SQLite system/app DB URL (a shared file so processes join)."""
    path = os.path.join(_statedir(), f"{name}.sqlite")
    return f"sqlite:///{path}"


def make_config(url, name="wio", executor_id=None):
    from dbos import DBOSConfig

    cfg: "DBOSConfig" = {
        "name": name,
        "system_database_url": url,
        "application_database_url": url,
        "run_admin_server": False,
        "log_level": os.environ.get("DBOS_LOG_LEVEL", "WARNING"),
    }
    # A distinct executor_id per PROCESS is how real DBOS clusters identify
    # runners (issue #541's own repro uses worker-<pid>). Without it every
    # process shares "local" and the per-executor concurrency accounting
    # collapses — so the attack must set it to be a faithful multi-runner test.
    if executor_id is not None:
        cfg["executor_id"] = executor_id
    return cfg


# ---- append-only effects ledger (oracle substrate) ----------------------

def _ledger():
    return os.path.join(_statedir(), "effects.tsv")


def record_effect(scope, label):
    """Atomically append one effect row. O_APPEND writes < PIPE_BUF are atomic
    across processes on Linux, so concurrent executors never interleave."""
    line = f"{scope}\t{label}\t{time.time():.6f}\n".encode()
    fd = os.open(_ledger(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def _read_ledger(scope):
    rows = []
    try:
        with open(_ledger()) as f:
            for ln in f:
                parts = ln.rstrip("\n").split("\t")
                if len(parts) == 3 and parts[0] == scope:
                    rows.append((parts[1], float(parts[2])))
    except FileNotFoundError:
        pass
    return rows


def effect_counts(scope):
    counts = {}
    for label, _ts in _read_ledger(scope):
        counts[label] = counts.get(label, 0) + 1
    return counts


def fetch_events(scope, kind):
    """Return sorted [(label, ts)] for effects whose label starts with kind+':'."""
    pref = kind + ":"
    ev = [(l, t) for (l, t) in _read_ledger(scope) if l.startswith(pref)]
    ev.sort(key=lambda x: x[1])
    return ev


def reset_ledger():
    try:
        os.remove(_ledger())
    except FileNotFoundError:
        pass


# ---- invariant reporting -------------------------------------------------

class Oracle:
    def __init__(self):
        self.failed = 0
        self.n = 0

    def check(self, inv_id, name, ok, summary):
        self.n += 1
        if not ok:
            self.failed += 1
        print(f"INVARIANT {inv_id} {name} {'PASS' if ok else 'FAIL'} {summary}", flush=True)

    def verdict_exit(self):
        if self.failed:
            print(f"VERDICT RED {self.failed}/{self.n} invariants failed", flush=True)
            sys.exit(1)
        print(f"VERDICT GREEN {self.n} invariants held", flush=True)
        sys.exit(0)
