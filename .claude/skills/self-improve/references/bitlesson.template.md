# BitLesson — distilled lessons (push THIS up, not raw memory/)

The **upward-propagation artifact**: a −1 team-leader reports up its **distilled lessons**, not its raw
`memory/` event log. `memory/` stays local; only lessons bubble up. Keep entries precise + reusable.
*(Mechanism ported from humanize — paired with `bitlesson-selector` + the `## BitLesson Delta` gate.)*

Record a lesson **only when it is non-obvious and reusable across at least two independent task
contexts**. Several rounds, artifacts, or retries of one task are still one context; keep those
observations in project `.memory` and report no BitLesson delta. Distill, don't dump.

## Entry template (STRICT — use this exact field order for every entry)
```markdown
## Lesson: <unique-id>
Lesson ID: <BL-YYYYMMDD-short-name>
Scope: <component/subsystem/files>
Task Contexts: <at least two distinct task/run evidence pointers, comma-separated>
Independence: <why one project-specific cause does not explain both contexts>
Project Identifiers Removed: <yes>
Applicability: <inputs and runtime conditions where this lesson should transfer>
Problem Description: <specific failure mode with trigger conditions>
Root Cause: <direct technical cause>
Solution: <exact fix that resolved it>
Constraints: <limits, assumptions, non-goals>
Counterexamples / Falsifier: <known negative case or observation that would invalidate reuse>
Validation Evidence: <tests/commands/logs/PR>
Source Rounds: <round numbers where it appeared and was solved>
```

## Entries
<!-- add lessons below using the strict template. -->
