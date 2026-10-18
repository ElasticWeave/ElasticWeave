# Native CLI dispatch batches

Load this reference only when a bounded dispatch owner needs to launch native CLI workers or
separate admitted workflows. Platform-native subagent tools can instead provide the owner handles;
they do not need this wrapper. This is an agent-facing execution interface, not user onboarding.

The top supplies one authorized batch with schema `managed-dispatch-plan/v1`, a `request_id`, its
inherited `skill-invocation-envelope/v1` envelope, and `nodes`. A child batch keeps its outer
`terminalOwner`; it does not create another completion lifecycle. The envelope carries the existing
iteration snapshot and resource/authority receipts. Use the conservative literal `autonomous` only
for a legacy assignment without a PM snapshot. The tool does not create a PM board or probe unused
model routes.

Each node contains exactly:

| Field | Meaning |
| --- | --- |
| `id` | Unique bounded owner identifier. |
| `owner` | `bounded_worker` or `admitted_workflow`; top-owned work is refused. |
| `action_class` | `inspect`, `disposable_verify`, `mutation`, or `external_side_effect`. |
| `workspace` | Absolute existing workspace; separate worktrees for concurrent workflow controllers. |
| `workspace_mode` | Optional for admitted workflows: `copy_on_write` creates a disposable filtered copy and retains its change manifest for outer review. |
| `reads`, `writes` | Relative path scopes, including directory ownership and real shared inputs. |
| `resources` | Names of exclusive resources, such as a GPU, hardware setup, or Git index. |
| `depends_on` | Required predecessors; an empty list identifies an initially ready leaf. |
| `brief` | Task, authority, acceptance, required skill paths/workflow, and exact result expectations. |
| `envelope` | Narrowed child envelope, with unique invocation ID and the same outer terminal owner. |
| `launch` | One of the two typed routes below. |

A native route supplies the canonical resolver request, never a model or command override:

```json
{"kind":"native","request":{"taskClass":"analysis","reasoningPosture":"balanced","semanticRole":"task","reviewMode":"none","writerRole":null}}
```

A workflow route supplies `{"kind":"workflow","mapping":".subagent/flows/example.json"}`.
Its already-admitted mapping and input remain authoritative. The existing controller validates the
design, fresh model routes, evidence and review policy. Its declared call cap must fit the child
allocation, and parent wall time and cancellation also bound execution. A read-only parent cannot
authorize writer nodes or arbitrary command handlers.

For a workflow that should not inherit the top session's project context, set
`workspace_mode: "copy_on_write"`. The dispatcher uses reflinks when available, removes root
`AGENTS.md`/`CLAUDE.md` and unrelated `.claude/skills/*`, and retains only the controller runtime
and skills declared by workflow nodes. The child runs from that copy, so automatic Codex/Claude
project discovery cannot see supervisor-only skills. The dispatcher writes `cow-result.json` beside
the owner evidence with changed paths, hashes, and text diffs. It never applies those changes to the
source workspace automatically. After all worker processes are drained, the outer owner may accept
or discard the candidate and remove the disposable copies with `managed_dispatch.py cleanup <run-dir>
--accepted`. Cleanup is also allowed for a stopped or routed-failure dispatch; it is rejected while
any worker is live.

Budgets use numeric `model_calls` and `wall_time_seconds`; each native turn retains at least 3600
seconds. Allocate enough parent time for sequential stages and native recovery. Optional budgets
for `experiments`, `compute`, and `external_spend` may only narrow the parent allocations. A worker
can never obtain experiment/external authority merely by supplying an envelope. Bind actual user
authority and resource receipts in the brief.

Without `max_concurrency`, the runtime launches every currently independent eligible owner.
Conflicting paths/resources serialize automatically and their reason is recorded. A capacity limit
that withholds independent ready work needs `serialization_reason` with `kind: resource_limit` or
`kind: user_instruction` and a concrete `detail`. Reserve all mandatory review and integration
capacity when choosing the cap. An observed or credible estimated expert review/integration
bottleneck qualifies as `resource_limit`: set `max_concurrency` and include the observation/estimate
and revisit condition in `detail`, not just "coordination cost". The runtime enforces the chosen cap;
the owner judges and revisits this estimate. Read-only access to the same immutable source is not a
conflict. If B needs A's information, declare that dependency instead of labeling it a resource cap.
These limits never waive write-scope, dependency, evidence, or review checks. A dependency chain
needs no artificial reason and a single leaf gets no invented peer or reviewer. Preserve required
review even for one execution owner. Reviews and fan-in can be explicit dependency nodes when
already selected by the assignment's policy.

Use task-qualified owners within the resolved model policy, minimizing briefing, calls, review,
rework, and integration burden. The deterministic scheduler may stay cheap; ambiguous semantic
integration requires a qualified owner. Read
`../../default-mode/references/heterogeneous-team-management.md` when choosing briefs or capacity.

Choose a fresh durable run directory outside every worker's declared writes and every workflow
proof workspace, then run:

```bash
python3 .claude/skills/pm-dispatch/scripts/managed_dispatch.py run <plan.json> --run-dir <new-run-dir>
```

The top monitors that dispatch owner's process handle and can inspect `status <run-dir>`.
`snapshot.json` and `events.jsonl` expose owner states, actual PIDs, selected batches, conflicts,
results and pending nodes. Per-owner directories retain launch receipts, native traces and raw
output. Use the existing PM task-event append lock to link these observations when PM exists;
these files are process accounting, not a second task board or completion proof.

`progress_update`, `checkpoint`, and `observation_timeout` never remove live handles. Failed owners
block only their dependents; unrelated owners drain normally. An execution error returns unresolved
work for the outer owner to repair/reassign, with no automatic mutation fallback. Explicit
cancellation propagates to current native/workflow calls and waits for cleanup. Abrupt process
death cannot guarantee cleanup or resumability: reconcile the recorded handles and side effects
before starting another batch; do not blindly replay them.

A successful process exit with `candidate_ready=true` is a **batch result**. Every response has
`terminal=false`. An independent-review workflow can return a successful execution candidate while
its canonical workflow terminal remains `unresolved`; its original review requirement is preserved.
The top still routes Default result gates, Fast feedback dependencies, canonical review/proofs,
PM closure, and any other outstanding request work. This tool never manufactures `COMPLETE` or
`HUMAN_BLOCKED`, authenticates a human request, or intercepts arbitrary tools outside the batch.
