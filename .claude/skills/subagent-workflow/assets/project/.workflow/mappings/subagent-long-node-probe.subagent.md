# subagent-long-node-probe → built-in subagent scheduler

- Canonical design: `.workflow/designs/subagent-long-node-probe.workflow.json`
- Canonical design SHA-256: `cff4d858460ea86b61ac379a95a4c2fef9bca58fd8dfa5b36d498809fe9fa19d`
- Mapping: `.subagent/flows/subagent-long-node-probe.json`
- Mapping SHA-256: `8413d4f975ff9ea0dfc38d2061830d6af51d11ed8213861cd2194475a8306036`
- Adapter: `subagent-scheduler`
- Scheduler policy: deterministic singleton frontier, zero scheduler calls.
- Terminal review policy: `independent`; controller success is candidate-ready.

| Neutral requirement | Mapping owner |
|---|---|
| Explicit long-node lifetime | The command node declares `timeoutSeconds=620`; the design declares `maxWallTimeSeconds=660`. |
| Deterministic execution | The command handler requests the 605-second probe and writes only `probeStatus`. |
| Budgets | The mapping permits no scheduler call and the workflow invokes no model node. |
| Durability | The controller owns checkpoints, command receipts, event timing, and result bytes. |
| Terminal | `probeStatus=PASS` reaches controller success; malformed or failed output closes unresolved. The top session then dispatches a fresh external reviewer over the criterion and run evidence before stopping. |

The historical 2026-08-01 evidence preserved with the scheduler smoke evidence pack predates the
migration from `reviewPolicy=none` to `reviewPolicy=independent`. It proves the greater-than-600
second controller behavior and explicit deadlines; it does not claim that an external terminal
review ran. Current design/mapping digest consistency and the independent-review policy are covered
by active tests.
