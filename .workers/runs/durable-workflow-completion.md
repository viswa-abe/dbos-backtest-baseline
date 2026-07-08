# Runs — durable-workflow-completion (SQLite, musl runtime)

Official cloud runs executed the prepared image at pushed HEAD. Evidence channel
is stdout `INVARIANT ...` lines; exit code is the verdict.

## durable-workflow-completion-baseline — GREEN
- Batch (explorationId): `nd79q8dfbp1xecktpht78qr5tx8a443q`, depth 6 → 6/6 succeeded, 0 failed.
- Representative run: `01KX1VB73ZW4HBAP9FB86DDQV9`.
- Command: `.workers/pyrun .workers/workloads/wf_durability.py baseline`
- Invariants (all PASS): `durability.completes`, `durability.step_one_once`,
  `durability.step_two_once`.
- Guest confirmed: `python 3.12.13`, `sqlite3 3.45.3`, `dbos import OK`
  (`/workspace/dbos/__init__.py`), SQLite DB under `/tmp`.

## durable-workflow-completion-crash-recover — GREEN
- Batch (explorationId): `nd7d8y3vgqjjgfa7mwpsn2ezdh8a42ga`, depth 10 → 10/10 succeeded, 0 failed.
- Representative run: `01KX1VEDN6DZJ9HZQ94KW73K43`, SEED `11428777993557200518`.
- Command: `.workers/pyrun .workers/workloads/wf_durability.py crash-recover`
- Evidence:
  ```
  INVARIANT durability.crashed_midway crash-before-step-two PASS rc=7 counts={'step_one': 1}
  RECOVER recovered 1 handles
  RECOVER final status SUCCESS
  RECOVER result 15
  INVARIANT durability.step_one_exactly_once step-one-not-reexecuted PASS step_one count=1
  INVARIANT durability.step_two_exactly_once step-two-runs-once-on-recovery PASS step_two count=1
  INVARIANT durability.result_stable result-correct-after-recovery PASS result=15
  INVARIANT durability.status_success status-success-after-recovery PASS final status SUCCESS
  VERDICT GREEN 5 invariants held
  ```
- Interpretation: DBOS's SQLite crash/recovery honors step exactly-once across a
  hard process crash (`os._exit(7)`) between checkpointed steps — the committed
  step_one is not re-executed on recovery; step_two runs exactly once; the result
  is stable. Oracle proven to bite via local `selftest` (planted dup → RED).
