"""Promise: durable-workflow-completion.

Claim: a workflow that crashes mid-execution resumes on restart and completes
exactly once; a step whose checkpoint committed before the crash is not
re-executed on recovery (exactly-once for a checkpointed step); the final
result is stable.

Cases (argv[1]):
  baseline      no crash; workflow runs to completion. Proves the oracle
                observes the invariants at all.
  crash-recover crash (os._exit, SIGKILL-equivalent) after step_one's checkpoint
                commits and before step_two; a recovery executor resumes and
                completes. Oracle: step_one exactly once, step_two exactly once,
                result stable, status SUCCESS.
  selftest      run baseline then plant a duplicate step effect; the oracle MUST
                go red (proves the red path).

This file is also the guest probe: it prints environment diagnostics first, so
one run reveals exactly where (if anywhere) the guest breaks.

It re-invokes itself as worker subprocesses (WIO_WORKER_MODE) so process-level
crash/recovery is the workload's own subprocess lifecycle.
"""
import os
import sys
import time
import platform
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import dbos_harness as H  # noqa: E402

WFID = "durability-wf"


def build_dbos():
    uri = H.start_pg()
    from dbos import DBOS
    cfg = H.make_config(uri, name="durability")
    DBOS(config=cfg)

    @DBOS.step()
    def step_one():
        H.record_effect(uri, WFID, "step_one")
        return 10

    @DBOS.step()
    def step_two():
        H.record_effect(uri, WFID, "step_two")
        return 5

    @DBOS.workflow()
    def wf():
        a = step_one()
        if os.environ.get("WIO_CRASH_BEFORE_STEP_TWO") == "1":
            # step_one's checkpoint has committed by now (DBOS records a step's
            # output before returning control to the workflow body).
            print("WORKER crashing before step_two", flush=True)
            os._exit(7)
        b = step_two()
        return a + b

    return DBOS, wf, uri


# ---- worker subprocess entrypoints --------------------------------------

def worker_run(crash):
    from dbos import DBOS, SetWorkflowID
    DBOS_, wf, uri = build_dbos()
    if crash:
        os.environ["WIO_CRASH_BEFORE_STEP_TWO"] = "1"
    DBOS.launch()
    with SetWorkflowID(WFID):
        try:
            r = wf()
            print(f"WORKER wf returned {r}", flush=True)
        except SystemExit:
            raise
    DBOS.destroy()


def worker_recover():
    from dbos import DBOS
    DBOS_, wf, uri = build_dbos()
    DBOS.launch()
    handles = DBOS._recover_pending_workflows(["local"])
    print(f"RECOVER recovered {len(handles)} handles", flush=True)
    deadline = time.time() + 30
    while time.time() < deadline:
        st = DBOS.get_workflow_status(WFID)
        if st and str(getattr(st, "status", "")).endswith("SUCCESS"):
            break
        time.sleep(0.2)
    st = DBOS.get_workflow_status(WFID)
    status = str(getattr(st, "status", None))
    print(f"RECOVER final status {status}", flush=True)
    res = None
    try:
        res = DBOS.retrieve_workflow(WFID).get_result()
        print(f"RECOVER result {res}", flush=True)
    except Exception as e:
        print(f"RECOVER result-error {e!r}", flush=True)
    sf = os.environ.get("WIO_STATUS_FILE")
    if sf:
        with open(sf, "w") as f:
            f.write(f"{status}\n{res}\n")
    DBOS.destroy()


# ---- controller ---------------------------------------------------------

def diagnostics():
    print("=== GUEST DIAGNOSTICS ===", flush=True)
    print(f"uname {platform.platform()}", flush=True)
    print(f"machine {platform.machine()}", flush=True)
    print(f"python {sys.version.split()[0]} exe {sys.executable}", flush=True)
    print(f"cwd {os.getcwd()}", flush=True)
    tmp_ok = os.access("/tmp", os.W_OK)
    print(f"/tmp writable {tmp_ok}", flush=True)
    try:
        import pgserver  # noqa
        print("pgserver import OK", flush=True)
    except Exception as e:
        print(f"pgserver import FAIL {e!r}", flush=True)
    try:
        import dbos  # noqa
        print(f"dbos import OK {dbos.__file__}", flush=True)
    except Exception as e:
        print(f"dbos import FAIL {e!r}", flush=True)
    print("=== END DIAGNOSTICS ===", flush=True)


def run_worker(script, mode, extra=None):
    env = dict(os.environ)
    env["WIO_WORKER_MODE"] = mode
    if extra:
        env.update(extra)
    p = subprocess.Popen([sys.executable, script], env=env)
    p.wait()
    return p.returncode


def controller(case):
    H.derive_seed()
    diagnostics()
    uri = H.start_pg()
    print(f"PG up: {uri}", flush=True)
    H.ensure_effects_table(uri)
    script = os.path.abspath(__file__)
    o = H.Oracle()

    if case == "baseline":
        rc = run_worker(script, "run")
        print(f"worker rc={rc}", flush=True)
        counts = H.effect_counts(uri, WFID)
        o.check("durability.completes", "workflow-completes", rc == 0, f"worker rc={rc}")
        o.check("durability.step_one_once", "step-one-exactly-once",
                counts.get("step_one") == 1, f"step_one count={counts.get('step_one')}")
        o.check("durability.step_two_once", "step-two-exactly-once",
                counts.get("step_two") == 1, f"step_two count={counts.get('step_two')}")

    elif case == "crash-recover":
        rc1 = run_worker(script, "crash")
        print(f"crash worker rc={rc1} (expect 7)", flush=True)
        c_after_crash = H.effect_counts(uri, WFID)
        o.check("durability.crashed_midway", "crash-before-step-two",
                rc1 == 7 and c_after_crash.get("step_two") is None,
                f"rc={rc1} counts={c_after_crash}")
        sf = "/tmp/wio_durability_status"
        rc2 = run_worker(script, "recover", {"WIO_STATUS_FILE": sf})
        print(f"recover worker rc={rc2}", flush=True)
        counts = H.effect_counts(uri, WFID)
        st_ok, res = _read_status(sf)
        o.check("durability.step_one_exactly_once", "step-one-not-reexecuted",
                counts.get("step_one") == 1, f"step_one count={counts.get('step_one')}")
        o.check("durability.step_two_exactly_once", "step-two-runs-once-on-recovery",
                counts.get("step_two") == 1, f"step_two count={counts.get('step_two')}")
        o.check("durability.result_stable", "result-correct-after-recovery",
                res == 15, f"result={res}")
        o.check("durability.status_success", "status-success-after-recovery",
                st_ok, "final status SUCCESS")

    elif case == "selftest":
        run_worker(script, "run")
        # plant a duplicate side effect: oracle MUST catch step_two > 1
        H.record_effect(uri, WFID, "step_two")
        counts = H.effect_counts(uri, WFID)
        o.check("durability.step_two_once", "step-two-exactly-once-SELFTEST",
                counts.get("step_two") == 1, f"step_two count={counts.get('step_two')} (planted dup)")
    else:
        print(f"unknown case {case}", flush=True)
        sys.exit(2)

    o.verdict_exit()


def _read_status(sf):
    try:
        with open(sf) as f:
            lines = f.read().splitlines()
        status = lines[0] if lines else ""
        res = lines[1] if len(lines) > 1 else None
        ok = status.endswith("SUCCESS")
        try:
            res = int(res)
        except (TypeError, ValueError):
            pass
        return ok, res
    except Exception as e:
        print(f"status-read-error {e!r}", flush=True)
        return False, None


if __name__ == "__main__":
    mode = os.environ.get("WIO_WORKER_MODE")
    if mode == "run":
        worker_run(crash=False)
    elif mode == "crash":
        worker_run(crash=True)
    elif mode == "recover":
        worker_recover()
    else:
        case = sys.argv[1] if len(sys.argv) > 1 else "baseline"
        controller(case)
