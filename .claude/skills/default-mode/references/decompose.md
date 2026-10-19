# Decompose (default mode) — draft internally, then align with the user

The top coordinator does not hand-author implementation; it **decomposes the goal into the `.pm/`
board** and delegates the planning draft. Decompose is a discussion, not a workflow by default.
The first pass can be rough.

Use `heterogeneous-team-management.md` when selecting owners and briefs. Experts receive outcomes,
interfaces, constraints, and method autonomy; support members receive explicit inputs, outputs,
checks, and an exception route. Unknown capability is not weakness: use a small real assignment or
early sample when needed. Reserve every required review and qualified semantic integration owner;
optimize total delivery burden rather than helper count or call price. User authority is unchanged.

1. **DRAFT.** The `planner` agent drafts **≤10 milestones** (ordered, with `depends_on`) plus the
   first milestone's depth-0 issues. Every milestone and issue includes concrete acceptance
   criteria. Before lane selection, the planner writes a dependency record for every dispatch cycle:
   `TASK_GRAPH`, `INDEPENDENT_LEAVES`, `READ_SCOPES`, `WRITE_OWNERSHIP`, `SHARED_RESOURCES`,
   `EVIDENCE_DIRECTORY`, and `FANIN_CONDITION`. Dispatch eligible independent leaves concurrently
   within available review/integration capacity; each must have no unsatisfied data dependency or
   declared write/resource conflict. Do not create
   filler when only one leaf is ready. Each shared writable path has exactly one owner; use separate
   task evidence directories and the PM append
   lock rather than serializing independent work by habit. Two independent leaves may be separate
   concurrent native subagents even when they do not justify a workflow. A workflow remains for
   genuine graph semantics. If eligible leaves are deliberately serialized, the plan records a
   concrete `SERIALIZATION_REASON` naming the blocker: for example, B needs A's returned dataset,
   both need exclusive `gpu:0`, A still owns a path B must edit, or the batch is bounded by
   an observed reviewer/integrator bottleneck or credible capacity estimate. For a capacity cap, record
   `kind: resource_limit` with concrete detail naming the bottleneck, evidence/estimate, and revisit
   condition. Information dependencies belong in the graph; shared read-only input is no conflict.
   Coordinator convenience, context size alone, or vague coordination fear is insufficient.
   Each leaf records
   `ACTION_CLASS=inspect|disposable_verify|mutation|external_side_effect` and
   `OWNER=top|bounded_worker|admitted_workflow` before lane selection. Mutation and external side
   effects require `bounded_worker` or `admitted_workflow`; a failed worker never transfers its
   implementation to the top. Every issue also includes
   `dispatchLane` (`direct`, `native-subagent`, or `workflow`), `laneReason`,
   estimated/maximum model calls, `reviewPolicy` (`independent`, or workflow-only `machine-gated`),
   and promotion triggers. The top session may use `direct` only for read-only
   inspection or disposable verification that creates no durable state; anything that changes a
   deliverable, `.pm` state, UI state, repository state, or an external system goes to a native
   subagent or admitted subagent/humanize2 workflow. Ground
   the draft in repository evidence + recalled project memory.
   A `direct` issue also records `directScope` as `read-only-inspection` or
   `disposable-verification`, and budgets at least one model call for its terminal reviewer.
2. **REVIEW (cross-model — internal, BEFORE the user).** The planner returns its actual writer
   semantic role plus the concrete model and upstream-provider identity from its launch receipt.
   Resolve one direction-qualified reviewer via `resolve_reviewer_assignment` and a fresh live
   catalog. Under the shipped exact pairing, an OpenAI-family Astra writer uses
   `cross-review-openai-writer` with the Anthropic Opus `advisor`, while an Anthropic-family Opus
   writer uses `cross-review-anthropic-writer` with the OpenAI Astra `codex_advisor`. Fable 5.1
   (`claude-fable-5-1`) is active scoped advice; GPT-6 Astra
   (`gpt-6-astra`) is the active OpenAI route for Anthropic-family writers. Neither silently replaces the terminal
   pairing. Never infer direction from the top session, a planned role alone, or a generic
   reviewer name. If the
   exact opposite-provider reviewer is unavailable or unverifiable, report the review unresolved
   and do not silently substitute. Revise once. Use a second pass only for unresolved
   `PLAN-BLOCKING` findings; move implementation-testable questions into issue acceptance tests.
3. **ALIGN (user).** Surface the milestone/issue list and align on **how to decompose**. Under `debug` mode
   this is a human-review gate; if the goal was ambiguous at startup, align more actively.
   The fan-in owner is gated by `FANIN_CONDITION`; it must not begin until every required leaf has
   returned a routed result. A failed leaf blocks only dependents that require it and does not cancel
   unrelated leaves.
4. **MATERIALIZE.** Dispatch a bounded PM worker to write the board with
   **`python3 .claude/skills/pm-dispatch/scripts/pm.py`** (`milestone add` × N; `issue` × the depth-0 issues of
   milestone 1). Pass `--acceptance` on every `milestone add` and `issue` command. The **immutable
   Goal-Tracker** (`.pm/goal.md`) was already pinned in default mode — **VALIDATE it here, never overwrite it**.
   If a configured external UI mirror is enabled, delegate its synchronization to a bounded admin worker;
   the local `.pm` board remains canonical.
5. **PROCEED.** Hand to **`pm-dispatch`**; start milestone 1.

No global `.pm/accept.sh` is created. Focused checks are evidence for the independent terminal
reviewer. Before the current session stops, that reviewer must produce `TERMINAL: COMPLETE`,
structured `TERMINAL: HUMAN_BLOCKED`, or `TERMINAL: REVISE`; REVISE returns to dispatch.

**Later milestones:** re-decompose the same way when you reach each one — the top coordinator
authorizes that milestone's depth-0 issues when it starts the milestone, and a bounded PM worker
creates them, rather than creating all issues up front.
