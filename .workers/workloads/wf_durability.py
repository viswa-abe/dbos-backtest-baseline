"""Promise: durable-workflow-completion (backend-agnostic; runs on SQLite here).

Claim: a workflow that crashes mid-execution resumes on restart and completes
exactly once; a step whose checkpoint committed before the crash is not
re-executed on recovery; the final result is stable.

Cases (argv[1]):
  baseline      no crash; workflow runs to completion. Proves the oracle.
  crash-recover crash (os._exit) after step_one's checkpoint commits and before
                step_two; a fresh recovery executor resumes and completes.
  selftest      run baseline then plant a duplicate step effect; oracle MUST RED.

Also the guest probe: prints environment diagnostics first. Re-invokes itself as
worker/recover subprocesses (WIO_WORKER_MODE) so crash/recovery is the
workload's own process lifecycle.
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
    url = H.start_db("durability")
    from dbos import DBOS
    DBOS(config=H.make_config(url, name="durability"))

    @DBOS.step()
    def step_one():
        H.record_effect(WFID, "step_one")
        return 10

    @DBOS.step()
    def step_two():
        H.record_effect(WFID, "step_two")
        return 5

    @DBOS.workflow()
    def wf():
        a = step_one()
        if os.environ.get("WIO_CRASH_BEFORE_STEP_TWO") == "1":
            print("WORKER crashing before step_two", flush=True)
            os._exit(7)
        b = step_two()
        return a + b

    return DBOS, wf, url


def worker_run(crash):
    from dbos import DBOS, SetWorkflowID
    if crash:
        os.environ["WIO_CRASH_BEFORE_STEP_TWO"] = "1"
    _dbos, wf, _url = build_dbos()
    DBOS.launch()
    with SetWorkflowID(WFID):
        r = wf()
        print(f"WORKER wf returned {r}", flush=True)
    DBOS.destroy()


def worker_recover():
    from dbos import DBOS
    _dbos, wf, _url = build_dbos()
    DBOS.launch()
    handles = DBOS._recover_pending_workflows(["local"])
    print(f"RECOVER recovered {len(handles)} handles", flush=True)
    deadline = time.time() + 40
    status = "None"
    while time.time() < deadline:
        st = DBOS.get_workflow_status(WFID)
        status = str(getattr(st, "status", None))
        if status.endswith("SUCCESS"):
            break
        time.sleep(0.2)
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


def diagnostics():
    print("=== GUEST DIAGNOSTICS ===", flush=True)
    print(f"uname {platform.platform()}", flush=True)
    print(f"machine {platform.machine()}", flush=True)
    print(f"python {sys.version.split()[0]} exe {sys.executable}", flush=True)
    print(f"cwd {os.getcwd()}  /tmp-writable {os.access('/tmp', os.W_OK)}", flush=True)
    try:
        import sqlite3
        print(f"sqlite3 OK {sqlite3.sqlite_version}", flush=True)
    except Exception as e:
        print(f"sqlite3 FAIL {e!r}", flush=True)
    try:
        import dbos
        print(f"dbos import OK {dbos.__file__}", flush=True)
    except Exception as e:
        print(f"dbos import FAIL {e!r}", flush=True)
    print("=== END DIAGNOSTICS ===", flush=True)


def run_worker(mode, extra=None):
    env = dict(os.environ)
    env["WIO_WORKER_MODE"] = mode
    if extra:
        env.update(extra)
    p = subprocess.Popen([sys.executable, os.path.abspath(__file__)], env=env)
    p.wait()
    return p.returncode


def _read_status(sf):
    try:
        with open(sf) as f:
            lines = f.read().splitlines()
        status = lines[0] if lines else ""
        res = lines[1] if len(lines) > 1 else None
        try:
            res = int(res)
        except (TypeError, ValueError):
            pass
        return status.endswith("SUCCESS"), res
    except Exception as e:
        print(f"status-read-error {e!r}", flush=True)
        return False, None


def controller(case):
    H.report_capture_seed()
    diagnostics()
    H.reset_ledger()
    # Start from a pristine system DB: WFID is fixed, so a completed run left in
    # the DB would be returned memoized (no crash, no step effects). Each case
    # must begin with the workflow genuinely absent.
    try:
        os.remove(os.path.join(H._statedir(), "durability.sqlite"))
    except FileNotFoundError:
        pass
    url = H.start_db("durability")
    print(f"DB: {url}", flush=True)
    o = H.Oracle()

    if case == "baseline":
        rc = run_worker("run")
        counts = H.effect_counts(WFID)
        o.check("durability.completes", "workflow-completes", rc == 0, f"worker rc={rc}")
        o.check("durability.step_one_once", "step-one-exactly-once",
                counts.get("step_one") == 1, f"step_one count={counts.get('step_one')}")
        o.check("durability.step_two_once", "step-two-exactly-once",
                counts.get("step_two") == 1, f"step_two count={counts.get('step_two')}")

    elif case == "crash-recover":
        rc1 = run_worker("crash")
        c1 = H.effect_counts(WFID)
        o.check("durability.crashed_midway", "crash-before-step-two",
                rc1 == 7 and c1.get("step_two") is None, f"rc={rc1} counts={c1}")
        sf = os.path.join(H._statedir(), "status")
        rc2 = run_worker("recover", {"WIO_STATUS_FILE": sf})
        print(f"recover rc={rc2}", flush=True)
        counts = H.effect_counts(WFID)
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
        run_worker("run")
        H.record_effect(WFID, "step_two")  # plant a duplicate
        counts = H.effect_counts(WFID)
        o.check("durability.step_two_once", "step-two-exactly-once-SELFTEST",
                counts.get("step_two") == 1, f"step_two count={counts.get('step_two')} (planted dup)")
    else:
        print(f"unknown case {case}", flush=True)
        sys.exit(2)

    o.verdict_exit()


if __name__ == "__main__":
    mode = os.environ.get("WIO_WORKER_MODE")
    if mode == "run":
        worker_run(crash=False)
    elif mode == "crash":
        worker_run(crash=True)
    elif mode == "recover":
        worker_recover()
    else:
        controller(sys.argv[1] if len(sys.argv) > 1 else "baseline")
