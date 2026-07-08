# Loop state
- rails: { loops: 16, workloads: 250 }   # brief cap: <=16 episodes, <=1200 sim cases
- counters: { episodes: 1, producer: 1, executor: 0, workloads: 0, sim_cases: 0 }
- no-new-info: { streak: 0, K: 5 }
- in-flight unit: none
- re-entry: none
- re-plan triggers: none
- publish-pending: []
- last episode summary: >-
    Episode 1 (producer/init+backfill). Scaffolded .workers/, ran 4-scout
    cartographer fan-out (docs/tests/issues/source). Promoted 2 promises:
    durable-workflow-completion (durability) and queue-limits-global (queues),
    each with ready explorations. Backlog seeded with 9 scored candidates
    (top: sqlite-#541 parked off-directive, write-stream-from-step,
    update-workflow-outcome-clobbers-cancelled, recovery-redequeue-double-exec).
    Toolkit + rate-limiter finding validated locally (no cloud cases spent).
    Next: commit+push+prepare, then executor episodes on the ready explorations.
