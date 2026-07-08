"""Shared harness for DBOS workload cases.

Design (single-command, self-contained; the wio guest runs ONE command per
simulation case):

  * An embedded PostgreSQL is started once per run under /tmp (mutable; the
    repo tree is read-only in the guest). pgserver vendors the PG 16 binaries.
  * The controller (the workload's main) drives the SUT lifecycle itself:
    it spawns DBOS "executor" subprocesses, SIGKILLs them mid-workflow to
    simulate crashes, and starts recovery executors. Process-level faults are
    the workload's own subprocess calls (per the executor playbook).
  * Oracles are checked against an application-DB "effects" ledger that is
    independent of DBOS's own bookkeeping: every observable side effect inserts
    a row, so exactly-once / at-least-once violations are directly countable.
  * Evidence channel is stdout. Each oracle clause prints exactly one
    `INVARIANT <id> <name> PASS|FAIL <summary>` line. Exit code is the verdict
    (0 green, 1 red finding, 2 setup/harness error -> not a product finding).

  Seed: no seed env var reaches the guest, so we derive one from os.urandom
  (deterministic per run inside the sim), print it first as the replay key.
"""
import os
import sys
import time
import subprocess

# Import the DBOS under test from the repo tree (this checkout is the SUT).
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

_PG_SINGLETON = {}


def derive_seed():
    s = int.from_bytes(os.urandom(8), "big")
    print(f"SEED {s}", flush=True)
    return s


def _pgdata():
    d = os.environ.get("WIO_PGDATA", "/tmp/dbos_pg")
    os.makedirs(d, exist_ok=True)
    return d


def start_pg():
    """Start (or reuse) the embedded postgres, return its base URI."""
    if "uri" in _PG_SINGLETON:
        return _PG_SINGLETON["uri"]
    import pgserver

    srv = pgserver.get_server(_pgdata())
    uri = srv.get_uri()
    _PG_SINGLETON["srv"] = srv
    _PG_SINGLETON["uri"] = uri
    return uri


def make_config(uri, name="wio"):
    from dbos import DBOSConfig

    cfg: "DBOSConfig" = {
        "name": name,
        "system_database_url": uri,
        "application_database_url": uri,
        "run_admin_server": False,
        "log_level": os.environ.get("DBOS_LOG_LEVEL", "WARNING"),
    }
    return cfg


# ---- application-DB effects ledger (oracle substrate) --------------------

def ensure_effects_table(uri):
    import psycopg

    with psycopg.connect(_libpq(uri), autocommit=True) as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS wio_effects(
                   id BIGSERIAL PRIMARY KEY,
                   scope TEXT NOT NULL,
                   label TEXT NOT NULL,
                   ts DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from clock_timestamp())
               )"""
        )


def record_effect(uri, scope, label):
    import psycopg

    with psycopg.connect(_libpq(uri), autocommit=True) as c:
        c.execute(
            "INSERT INTO wio_effects(scope, label) VALUES (%s, %s)", (scope, label)
        )


def effect_counts(uri, scope):
    import psycopg

    with psycopg.connect(_libpq(uri), autocommit=True) as c:
        rows = c.execute(
            "SELECT label, COUNT(*) FROM wio_effects WHERE scope=%s GROUP BY label",
            (scope,),
        ).fetchall()
    return {label: n for (label, n) in rows}


def _libpq(uri):
    """pgserver hands back a SQLAlchemy-style URI; psycopg wants libpq form.
    Both accept the postgresql://.../db?host=/sock shape, so pass through."""
    return uri


# ---- invariant reporting -------------------------------------------------

class Oracle:
    def __init__(self):
        self.failed = 0
        self.n = 0

    def check(self, inv_id, name, ok, summary):
        self.n += 1
        verdict = "PASS" if ok else "FAIL"
        if not ok:
            self.failed += 1
        print(f"INVARIANT {inv_id} {name} {verdict} {summary}", flush=True)

    def verdict_exit(self):
        if self.failed:
            print(f"VERDICT RED {self.failed}/{self.n} invariants failed", flush=True)
            sys.exit(1)
        print(f"VERDICT GREEN {self.n} invariants held", flush=True)
        sys.exit(0)


# ---- subprocess orchestration -------------------------------------------

def spawn_worker(script_path, mode, env_extra, wait=False):
    env = dict(os.environ)
    env["WIO_WORKER_MODE"] = mode
    env.update({k: str(v) for k, v in env_extra.items()})
    p = subprocess.Popen([sys.executable, script_path], env=env)
    if wait:
        p.wait()
    return p
