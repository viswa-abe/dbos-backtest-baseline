# Backlog
- active: 9
- areas: { durability: 3, recovery: 2, idempotency: 2, queues: 1, messaging: 1 }
- top-score: 512
- threshold: 20

## Active (sorted by score, descending)

| score | candidate | area | L·I·O·N·R/C | provenance | source | notes |
|-------|-----------|------|-------------|------------|--------|-------|
| 512 | sqlite-two-runner-dequeue-exactly-once — N OS processes dequeue one SQLite-backed queue; total executions == enqueued | queues | 4·4·4·4·4/2 | issue #541; dbos/_sys_db.py:2078 (FOR UPDATE/SKIP LOCKED no-op on SQLite); recent churn #564 #553 #559 | scout-issues | skip 2026-07-09: brief pins Postgres as the runtime; SQLite is off-directive. Highest raw score but parked unless Postgres surface exhausts. |
| 384 | write-stream-from-step-nonidempotent — retried/concurrent step stream writes duplicate rows or collide on offset PK | durability | 4·3·4·4·4/2 | dbos/_sys_db.py:2246-2280 (read max(offset)+insert, no OAOO, no lock); vs write_stream_from_workflow L2296 | scout-source | no operation-result recording on the step path; step retries are first-class |
| 384 | update-workflow-outcome-clobbers-cancelled — final SUCCESS write overwrites a CANCELLED/terminal status (no status guard) | durability | 3·4·4·4·4/2 | dbos/_sys_db.py:625-637 (unconditional UPDATE, no WHERE status guard) | scout-source | cancel a wf between its last step and outcome write; running wf clobbers CANCELLED |
| 320 | recovery-redequeue-double-exec — recovery re-enqueues a workflow still running on a live executor; both advance it | recovery | 4·5·4·4·3/3 | dbos/_recovery.py:19-23 clear_queue_assignment; force_execute bypass dbos/_sys_db.py:472,607-612 | scout-source | operation_outputs dedups the record but not the side effect; needs crash/partition orchestration |
| 256 | post-commit-step-crash-retry — crash after step-result txn commits on a retrying step + concurrent recovery; exactly-once side effect | idempotency | 4·4·4·4·3/3 | dbos/_sys_db.py:1317-1323 DEBUG_TRIGGER_STEP_COMMIT; @db_retry re-runs txn | scout-tests | test_commit_hiccup is single-thread, no retries, asserts only return value |
| 240 | poison-dispatch-strands-siblings (#546) — one workflow raising on dispatch must not strand the rest PENDING | recovery | 2·3·4·2·5/1 | issue #546; guard dbos/_queue.py:154-166 (per-id try/except) | scout-issues | likely GREEN (regression guard present); cheap; run as guard |
| 216 | concurrent-setworkflowid-exactly-once — K processes start same explicit workflow_id; body runs once | idempotency | 3·4·4·3·3/2 | dbos/_sys_db.py:557-612 owner_xid; issue #544 | scout-source | existing singleexec tests are in-process only |
| 144 | scheduled-exactly-once-multi-executor — cron fires exactly once per tick across N executors sharing sys DB | idempotency | 3·4·3·4·3/3 | dbos/_scheduler.py; test_scheduler_oaoo single-process only | scout-tests | multi-replica scheduling untested |
| 108 | recv/get_event-durable-timeout — a wf that waited t of T then crashed resumes with remaining deadline, not fresh T | messaging | 3·3·3·4·3/3 | dbos/_sys_db.py:1568-1571,1905-1912 (OAOO sleep deadline) | scout-docs | README "wait through restarts"; no restart-mid-wait test |

## Archive (no loop agent reads this)

- queues: 1 promoted (rate-limiter over-admission → queue-limits-global; concurrency cap folded in as conc-attack).
- durability: 1 promoted (durable-workflow-completion baseline + crash-recover).
