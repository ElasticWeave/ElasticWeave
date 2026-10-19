# plan.md — pinned goal + live dispatch plan (authorized by the top coordinator)

> The ranked batch of work the top session is dispatching. A bounded planner/PM worker writes or
> re-ranks it against fresh state and `.memory/`; the top session authorizes the exact change,
> inspects evidence, and routes each implementation to a native subagent or admitted workflow. It
> never writes this plan or implements deliverables itself. Keep this current-batch only; durable
> project history lives in `.memory/`.

## PINNED GOAL (the drift-gate's reference — change only by deliberate re-pin)
- **Task type:** {{optimization | feature/0→1}}.
- **Goal (one sentence, requirement-aligned with user/upstream):** {{...}}.
- **Success criterion:** {{the metric + how computed | the acceptance spec / checkable conditions}}.
- **Constraints (violation = void, not a win):** {{e.g. single-core only; immutable files; disallowed techniques}}.
- **Source / alignment:** {{user | Linear issue # | ticket — link; what was clarified}}.

## Current state
- **Status vs criterion:** current = {{VALUE}} (baseline {{BASELINE}}); floor/target = {{FLOOR}} / {{TARGET}}  ·  or: {{N of M acceptance conditions met}}.
- **Binding constraint / next gap:** {{the wall right now / the next unmet requirement}}.
- **Last change:** {{what landed + its measured delta}}.

## Ranked next moves (current batch)
| # | Delegated move | Lane / owner | Predicted effect | Risk | Status |
|---|------|--------------------------------|------------------|------|--------|
| 1 | {{...}} | {{native-subagent or workflow + bounded owner}} | {{predicted metric delta}} | {{...}} | todo |
| 2 | {{...}} | {{...}} | {{...}} | {{...}} | todo |
| 3 | {{...}} | {{...}} | {{...}} | {{...}} | todo |

## Anti-loop fences (do NOT re-try without saying what's different)
- {{dead lever}} — failed because {{reason}} (see memory/).

## Recommended next move
{{the single move to dispatch now + its lane, bounded brief, and falsifiable predicted effect}}
