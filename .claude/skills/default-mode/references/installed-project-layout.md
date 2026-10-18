# Installed Project Layout

The skill bundle is package source. `default-mode` creates project state only when selected and never
overwrites an unowned existing path; the marked root instruction block is the documented
merge-managed replacement exception.

| Project path | Owner | Purpose |
|---|---|---|
| `AGENTS.md`, `CLAUDE.md` marked block | `default-mode`, `fast-mode`, `update-platform-config` | Merge-managed top-session delegation, selected startup-mode contract, and universal terminal-review contract; surrounding bytes remain user-owned; `naive-mode` is prompt-only and never writes this block |
| `.pm/` | delegated PM workers, completion skills | Goal, explicit criteria, reports, terminal artifacts, review-policy state, and per-issue `.pm/tasks/<issue-id>/{task.json,events.jsonl,experiments/}` workspaces; the top session inspects it but never mutates it directly, and there is no global acceptance shell gate |
| `.memory/` | `memory` | Project-local cross-platform memory and inbox |
| `config/workflows/models.json` | `default-mode`, `fast-mode`, `update-platform-config` | Fresh project-specific native model roles and global reasoning policy |
| `.workflow/designs/` | `build-workflow` | Project-specific neutral workflow intent |
| `.workflow/harnesses.json` | `run-workflow` | Optional project override of the bundled adapter registry |
| `.subagent/flows/` | `subagent-workflow` | Generated built-in scheduler mappings |
| `.humanize/flows/default.py`, `.humanize/flows/slot.py`, `.humanize/flows/versions/slot-<sha256>.py` | `write-humanize-flow` | Trusted native Humanize root; slot is a controlled content-addressed symlink and versions are published by the validator; inert until explicitly selected |
| `.humanize/run_scheduler.py`, `.humanize/flows/scheduler/__init__.py`, `.humanize/scheduler/README.md` | `write-humanize-flow` | Repo-default launcher and TUI-selectable outer Codex directory flow; exact false or true-plus-approved-slot dispatch returns to a fresh Codex turn after each child |
| `.humanize/flows/deep_research.py`, `.humanize/flows/auto_research.py`, `_research_reference.py` | `deep-research-workflow` | Trusted native Humanize reference implementations; the domain skill contract remains canonical intent |
| `.humanize/mappings/` | `humanize-workflow` | Generated mappings for the narrow neutral-workflow adapter, separate from trusted native flows |
| `.humanize/artifacts/`, `workflow-output/humanize/` | running Humanize flow | Runtime terminal artifacts and traces; never package source or completion authority by themselves |
| `.omp/agents/` | `write-subagent` | Generated canonical project agents |
| `.claude/agents/`, `.codex/agents/` | `update-platform-config` | Derived compatible native agent surfaces |
| `.claude/hooks/`, `.codex/rules`, platform hook config | `update-platform-config`, gate skills | Selected platform enforcement surfaces |
| `resources.md`, `constraint.md` | `default-mode`, `constraints-to-hooks` | Project resources and constraints |

`--minimum-bootstrap` merges only the marked `AGENTS.md`/`CLAUDE.md` contract and defers every row
below it in the table. A later full materialization copies the trusted Humanize source files without
importing them, installing Humanize, or starting a flow. Humanize has no project-level default
auto-start behavior: validate and launch `default.py` explicitly through `write-humanize-flow` when
that native lane is selected. Its default launcher reads the project-root
`config/workflows/models.json` only and rejects `--roles`; callers select semantic executor and
reviewer roles, not an alternate role-policy file.

The managed root instruction block records one startup operating mode. A fresh default bootstrap
uses `--mode default`; `/fast-mode` re-materializes it with `--mode fast`, while `/naive-mode` does
not materialize project state at all (`materialize.py --mode naive` is rejected). Switching mode
replaces only the marked block and preserves
all surrounding user-owned bytes.

For a direct TUI outer loop, run `python3 .humanize/run_scheduler.py` from the project root. For a
PM-managed issue, add `--pm-issue .pm/issues/<milestone>/<issue>.md`; this seals the issue's
iteration snapshot so later project config cannot change the run. A human-guided continuation also
adds `--human-feedback`, which must bind the exact pending observation checkpoint before another
Scheduler turn. The launcher opens the
real `hmz` TUI with repository-versioned defaults for scheduler, executor, reviewer, planner,
preflight, collector, counterevidence, reducer, knowledge, applicability, and experimenter in that
order. Each invocation reapplies those defaults, but `/agents` remains available. Plain
`hmz -f local/scheduler` uses Humanize's saved choices. The short defaults omit
provider and permission, selecting the ambient account and pinned Humanize's `bypass` permission.
A saved or ambient choice never authorizes review/advice selection. Resolve the actual writer first,
then verify the exact opposite-provider reviewer/adviser against a fresh authenticated live catalog;
Fable 5.1 (`claude-fable-5-1`) and GPT-6 Astra (`gpt-6-astra`) are strong conditional candidates,
not ambient defaults or replacements for the configured exact terminal pairing. An unavailable
selection remains unresolved without silent substitution.
A Slot selects an allowed
child-role subset while retaining those configured Agent objects. The scheduler role is rejected
unless its Humanize backend is native `codex`. Its config defaults to 50 logical rounds and one
initial attempt plus four retry rounds per scheduler turn, waiting 5 minutes, 10 minutes, 30 minutes,
then 1 hour. Rate limits, bounded provider-reported usage-limit windows, transport errors, timeouts,
and temporary service failures retry; insufficient credits/billing, authentication, permission,
model, sandbox, and a stopped app-server fail immediately. Every failure restores dispatch to
`pending` before retrying or raising. Each native agent CLI turn receives a 3600-second timeout;
shorter bounds are reserved
for deterministic probes and cleanup. Runtime
`.humanize/scheduler/dispatch.txt` and `history.jsonl` are ignored project state, not package files
or completion evidence. PM-bound history records include a scope made from the sealed issue path
and digest; the scheduler reads only the current issue scope, while
`.pm/tasks/<issue-id>/events.jsonl` remains the durable per-issue evidence chain.
