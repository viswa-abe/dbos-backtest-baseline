---
key: durable-workflow-completion
area: durability
title: Durable workflow completion & step exactly-once
claim: >-
  A workflow that crashes mid-execution resumes on restart and completes exactly
  once; a step whose checkpoint committed before the crash is not re-executed on
  recovery, and the final result is stable.
status: active
provenance: "README.md#L37-60; dbos/_core.py step memoization; dbos/_recovery.py"
invariant_prefix: durability
explorations:
  - key: durable-workflow-completion-baseline
    title: Durable completion baseline
    description: "No faults; workflow runs to completion. Proves the oracle observes exactly-once step execution."
    status: done
    result: green
    reason: "6/6 seeds GREEN on musl runtime; 3 invariants held (completes, step_one once, step_two once)"
    workload: .workers/workloads/wf_durability.py
    command: ".workers/pyrun .workers/workloads/wf_durability.py baseline"
    faults: []
    depth: 6
    replay: { run: "01KX1VB73ZW4HBAP9FB86DDQV9", exploration: "nd79q8dfbp1xecktpht78qr5tx8a443q" }
    freshness: new-current
    reported: null
    published: pending
  - key: durable-workflow-completion-crash-recover
    title: Crash mid-workflow then recover
    description: >-
      Crash (os._exit) after step_one's checkpoint commits and before step_two;
      a fresh recovery executor resumes. Oracle: step_one exactly once (not
      re-run), step_two exactly once, result stable, status SUCCESS.
    status: done
    result: green
    reason: "10/10 seeds GREEN on musl runtime; crash rc=7 w/ step_one once, recovery resumes, step_two once, result 15, status SUCCESS"
    workload: .workers/workloads/wf_durability.py
    command: ".workers/pyrun .workers/workloads/wf_durability.py crash-recover"
    faults: []
    depth: 10
    replay: { run: "01KX1VEDN6DZJ9HZQ94KW73K43", seed: "11428777993557200518", exploration: "nd7d8y3vgqjjgfa7mwpsn2ezdh8a42ga" }
    freshness: new-current
    reported: null
    published: pending
---
# Durable workflow completion & step exactly-once

## Adversarial model
The promise breaks if, after a crash between steps, recovery either (a) fails to
resume the workflow, (b) re-executes a step whose checkpoint had already
committed (double side effect), or (c) produces a different / missing result.

## Fault dimensions
- Process crash (`os._exit`) after step_one returns and its checkpoint commits,
  before step_two. Reachability: the workload owns the SUT lifecycle and crashes
  its own executor subprocess; recovery is a second executor process calling
  `DBOS._recover_pending_workflows(["local"])`.

## Oracle (effects ledger, independent of DBOS bookkeeping)
Each step appends its label to a plain append-only ledger file (atomic O_APPEND
writes), counted without touching DBOS bookkeeping. Invariants:
- `durability.step_one_exactly_once` — step_one count == 1 after recovery
  (checkpointed step not re-run).
- `durability.step_two_exactly_once` — step_two count == 1 (runs once on
  recovery; never ran before the crash).
- `durability.result_stable` — retrieved result == 15.
- `durability.status_success` — final workflow status SUCCESS.
Baseline additionally checks completion with no faults.

## Red-proof
`wf_durability.py selftest` plants a duplicate step_two effect; the oracle goes
RED (step_two count == 2). Verified locally before trusting any green.

## Workload plan
Single file `wf_durability.py`, case selector (`baseline` | `crash-recover` |
`selftest`); the file re-invokes itself as worker/recover subprocesses. It also
prints guest environment diagnostics (doubles as the first-contact probe).
