# WIO Workload Map — dbos-transact-py

Factual evidence index. Not a queue: no owner/claim/priority/next-action columns.

## Target

| Field | Value |
|---|---|
| Target repo | dbos-transact-py (this checkout, pinned) |
| Pinned HEAD | 2d125b5 (frozen; issue snapshot <= 2026-02-01) |
| wio project | kn747zp6tbh6my7cyqb37zrzsh8a4knv (DBOS Backtest Baseline) |
| Branch | main |
| DB backend | Postgres (embedded via pgserver, PG 16) |

## Runtime / guest reality notes

- The workload provisions its own Postgres in-guest: `.workers/build.sh` creates
  a venv, pip-installs the DBOS runtime deps + `pgserver` (vendors PG 16), and
  installs a no-op `uuid-ossp` shim (DBOS's migration does
  `CREATE EXTENSION "uuid-ossp"` but never calls uuid_generate_*; it uses the
  built-in `gen_random_uuid()` — dbos/_migration.py:87,141). DBOS itself is
  imported from the repo tree via sys.path (the SUT under test), not pip.
- Postgres data + sockets live under `/tmp` (guest `/workspace` is read-only at
  runtime). Command runs the venv python: `.workers/venv/bin/python3 ...`.
- Concurrent first-launch migrations race on `CREATE EXTENSION` at the Postgres
  level; controllers pre-warm the schema once before spawning worker processes.
- No seed env var reaches the guest — workloads derive a seed from os.urandom
  and print `SEED <n>` first (the replay key). Evidence channel is stdout
  `INVARIANT <id> <name> PASS|FAIL <summary>` lines; exit code is the verdict.
- Validated locally (host, free): embedded PG + DBOS crash/recovery exactly-once;
  rate-limiter over-admission reproduced with 8 concurrent executors.

## Areas

| Area | Key | Promises |
|---|---|---|
| Durable Workflow Execution | durability | durable-workflow-completion |
| Durable Queues | queues | queue-limits-global |

## Promoted findings

| Finding | Promise | Exploration | Run ids | Evidence |
|---|---|---|---|---|
| _(pending official runs)_ | | | | |
