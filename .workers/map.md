# WIO Workload Map — dbos-transact-py

Factual evidence index. Not a queue: no owner/claim/priority/next-action columns.

## Target

| Field | Value |
|---|---|
| Target repo | dbos-transact-py (this checkout, pinned) |
| Pinned HEAD | 2d125b5 (frozen; issue snapshot <= 2026-02-01) |
| wio project | kn747zp6tbh6my7cyqb37zrzsh8a4knv (DBOS Backtest Baseline) |
| Branch | main |
| DB backend | **SQLite** (stdlib sqlite3, pure-Python path) — see pivot note |

## Runtime / guest reality notes

- **Build vs runtime env split (probed):** BUILD = Ubuntu Noble, glibc, apt,
  python3.12. RUNTIME sim VM = **musl + gcompat**, where glibc C-extension wheels
  fail to load (`mallinfo` symbol not found). So the initial Postgres plan
  (`pgserver`/`psycopg[binary]`, all glibc wheels) was DOA at runtime.
- **Pivot to DBOS SQLite backend.** DBOS's first-class SQLite system-DB backend
  is pure-Python (stdlib `sqlite3` compiled into the python binary + pure-Python
  SQLAlchemy) and runs on musl. `.workers/build.sh` now installs only pure deps
  (no psycopg/pgserver) and writes a tiny pure-Python `psycopg` **shim**, because
  `import dbos` eagerly `import psycopg` (dbos/_utils.py:6, _queue.py:6) but only
  for PG error-classification that never runs on SQLite. DBOS is imported from
  the repo tree via sys.path (the SUT), not pip.
- SQLite DB files + effects ledger live under `/tmp` (guest `/workspace` is
  read-only at runtime). Command runs `.workers/pyrun` (venv python launcher).
- Independent executor PROCESSES coordinate through one shared SQLite file;
  crash/recovery is the workload's own subprocess lifecycle.
- No seed env var reaches the guest — workloads derive a seed from os.urandom
  and print `SEED <n>` first (the replay key). Evidence channel is stdout
  `INVARIANT <id> <name> PASS|FAIL <summary>` lines; exit code is the verdict.
- Validated locally (host, free): DBOS SQLite crash/recovery exactly-once
  (durability GREEN); **queue two-runner double-dequeue reproduced RED** (see
  finding below). A separate Postgres rate-limiter over-admission was reproduced
  locally too but is NOT officially runnable (musl blocks the PG path).

## Areas

| Area | Key | Promises |
|---|---|---|
| Durable Workflow Execution | durability | durable-workflow-completion |
| Durable Queues | queues | queue-exactly-once |

## Promoted findings

| Finding | Promise | Exploration | Run ids | Evidence |
|---|---|---|---|---|
| SQLite two-runner double-dequeue (#541 not fully fixed by #564) | queue-exactly-once | queue-exactly-once-two-runner | _(pending official run)_ | local RED: 120 distinct tasks, 123–125 executions, 3–5 double-run; `qexactly.exactly_once` FAIL |
