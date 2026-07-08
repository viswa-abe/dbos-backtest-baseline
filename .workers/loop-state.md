# Loop state
- rails: { loops: 16, workloads: 250 }   # brief cap: <=16 episodes, <=1200 sim cases
- counters: { episodes: 2, producer: 1, executor: 1, workloads: 5, sim_cases: 210 }
- no-new-info: { streak: 0, K: 5 }
- in-flight unit: none
- re-entry: none
- re-plan triggers: none
- stop: "wrapped 2026-07-08 ~T+2.5h — primary objective met (flagship finding #541 official RED 10/10 + green baselines); official grid publication blocked server-side (harness:* 500s); budget remained (~210/1200). NOT coverage exhaustion."
- publish-pending: [durable-workflow-completion.baseline, durable-workflow-completion.crash-recover, queue-exactly-once.baseline, queue-exactly-once.two-runner]  # re-run .workers/publish.py when harness grid endpoint recovers (idempotent)
- last episode summary: >-
    Episode 2 (executor). Runtime probe found the sim VM is musl+gcompat, so the
    Postgres plan (pgserver/psycopg glibc wheels) was DOA — pivoted DBOS to its
    pure-Python SQLite backend, rewrote both workloads + harness, added a
    pure-Python psycopg shim to build.sh (import dbos eagerly imports psycopg,
    dead on SQLite). Committed 3a1a5fc, prepared (image==HEAD). Confirmed via a
    depth-1 cloud smoke that musl `import dbos` + SQLite work. Launched official
    batches: durability baseline d6 + crash-recover d10 (expect GREEN); queue
    deq-baseline d6 (GREEN) + deq-attack d12 (FINDING — #541 two-runner
    double-dequeue survives #564, reproduced RED locally 3-5 dups/run).
    Reconciled budget: 151 pre-pivot dead-end runs were unrecorded; true Σ=186.
    Next: collect batch verdicts, pick replay seed for the finding, set promise
    frontmatter status:done + result + replay, run publish.py, write SUMMARY.md.
