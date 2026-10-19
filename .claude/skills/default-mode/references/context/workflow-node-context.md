# Supervisor and workflow-node context

ElasticWeave has two different reasoning surfaces:

1. The top session is the supervisor. It owns the user goal, decomposition, workflow topology,
   role and model assignment, review capacity, resource limits, and escalation. It may read the
   mode bootstrap plus the supervisor coordination, workflow-design, routing, governance, and
   review references selected by the context manifest.
2. A workflow node is a specialist inside an already-admitted organization. It receives only its
   node contract, declared state inputs, write scope, evidence requirements, and the exact
   `skills.required` / `skills.onDemand` entries in the workflow design. It does not inherit the
   supervisor's skills, model-routing heuristics, PM board, or other sibling-node context.

The supervisor therefore applies management knowledge before dispatch: classify the node's task,
choose a qualified role and route, set the smallest useful context, reserve review and integration,
and state the escalation trigger. The node then applies domain knowledge to its bounded assignment.
Workflow construction and model routing are supervisor responsibilities; they are not default node
skills. A node that needs to design another workflow is a supervisor/delegated designer and must be
given that authority explicitly.

Self-evolution, memory promotion, and terminal review nodes are explicit supervisor-function
exceptions: their workflow contract may assign governance or review skills because they are
delegated control-plane work. They must be labeled as such in the node role/brief and remain
separate from ordinary task, research, or implementation nodes.

## Dispatch context

Every node brief must contain:

- one responsibility and measurable acceptance condition;
- the exact state fields and evidence paths it may read;
- its allowed files/tools and write scope;
- one semantic role, model route, effort, and escalation trigger resolved by the supervisor;
- `skills.required` and `skills.onDemand`, with no overlap;
- the instruction to load only those skills and not enumerate or bulk-load the project skill root.

Required skills are materialized into the first node prompt. On-demand skills expose only their
exact path until the node's trigger applies. A node may request missing expertise through its
declared escalation route, but it may not silently widen its context or change its role.

## Context boundary

The top session may use organizational heuristics to build or revise a workflow. The workflow
runtime may use deterministic scheduling, state validation, evidence capture, and terminal review.
Neither runtime scheduling nor a node's domain skill grants supervisor authority. This keeps the
large management context at the layer that needs it and makes node results comparable, reviewable,
and cheaper to run.

When an admitted workflow is dispatched by `pm-dispatch`, prefer its `copy_on_write` workspace
mode. The dispatcher creates a temporary filtered checkout, keeps only the controller runtime and
the skills declared by workflow nodes, and removes root `AGENTS.md`/`CLAUDE.md` before the CLI
starts. The filtered checkout is a workflow-level allowlist for filesystem visibility; it may be the
union of skills declared by the admitted nodes so concurrent nodes can use one stable checkout.
The per-node prompt contract remains authoritative: a node may load only its own declared paths,
must not enumerate `.claude/skills` to discover extra expertise, and must escalate when its
contract is insufficient. The resulting `cow-result.json` is evidence for the outer reviewer; it is
not an automatic merge. A shared workspace is reserved for explicitly read-only or intentionally
shared operations.
