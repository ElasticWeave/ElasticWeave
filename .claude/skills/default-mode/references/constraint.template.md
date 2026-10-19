# constraint.md — operating constraints (read on-demand)

> The constraints every agent in this system must respect. **Read this on-demand** — before planning a change, before a commit, before touching an unfamiliar machine. It has two halves:
> - **Prose constraints** (§1–§2): judgment-based, read by agents (machine facts, subtle functional limits, change-scope/readability rules).
> - **Deterministic block** (§3): a machine-readable JSON fence compiled into **hooks** by the `constraints-to-hooks` skill, so the hard rules are enforced mechanically (not left to model judgment).
>
> **Provenance / how this file is built (cold start):**
> 1. **Current-project evidence** — this repository's sessions, files, tools, and observed machine
>    state may supply project-memory or constraint evidence. Verify the claim against current bytes
>    or a current observation and keep its source.
> 2. **Cross-project transcripts are unverified leads only** — do not copy them into this file,
>    project memory, or a reusable reference. A possible transfer must first pass `self-improve`'s
>    cross-task reference admission (separate source/reference contexts, de-identification,
>    applicability boundary, falsifier, and owner review), then be revalidated in this project.
>    Only the locally reproduced and reviewed result may become a project constraint or reference.
> 3. **Project history & conventions** — analyze commit/PR history (how changes/PRs/maintenance are
>    actually done), code cleanliness, and in-repo rules (`CONTRIBUTING.md`, `CODEOWNERS`,
>    `.github/`, lint configs).
>
> Bounded workers **update** this file only after a durable constraint is verified for this project
> (then re-run `constraints-to-hooks` so deterministic ones become hooks). Keep entries terse and
> sourced; the top session only authorizes and inspects this mutation.

---

## 1. Machine-level constraints
Per machine the agent can reach (including via SSH). One block per machine; add as machines are discovered.

### machine: `local` (the box the agent runs on)
- **Access:** local shell.
- **Limits:** {{e.g. no GPU; CPU-only; disk quota; no sudo}}.
- **DO NOT:** {{e.g. don't start long-running daemons; don't bind ports <1024; don't run the full build on this box}}.

<!-- Template for an SSH-reachable machine — copy per host:
### machine: `<hostname/alias>`
- **Access:** ssh <user>@<host> ({{key/agent}}); reachable from {{where}}.
- **Purpose / situation:** {{what it's for, who else uses it}}.
- **Limits:** {{ram/gpu/quota/shared-tenant cautions}}.
- **DO NOT:** {{don't start service X; don't reboot; don't write outside /scratch; don't run on the login node}}.
-->

---

## 2. Project-level constraints

### 2a. Functional (substantive) constraints
Subtle, logic-level limits — things that are easy to break without noticing.
- {{e.g. "the frozen simulator is the source of truth; never monkeypatch it"}}
- {{e.g. "specialize on the fixed problem dimensions, never on runtime data values"}}
- {{e.g. "API X is rate-limited to N/s; batch calls"}}

### 2b. Form & review constraints (readability / change-scope)
From a code-review standpoint: what may change, what must not, how changes should look.
- **Change scope — may edit:** {{the optimization target / the allowed surface}}.
- **Change scope — OFF-LIMITS (never edit to pass a check):** {{tests/**, the spec, the scoring harness, the gate scripts}}.
- **Style / review:** {{match surrounding code; keep diffs minimal & focused; one concern per commit; follow CONTRIBUTING.md}}.
- **PR conventions:** {{branch naming, PR template, required reviewers/labels, commit-message format}}.

---

## 3. Deterministic constraints (compiled to hooks)
The `constraints-to-hooks` skill reads the single JSON fence below (marked
`constraints:deterministic`) and `.claude/skills/constraints-to-hooks/scripts/constraint_gate.py` enforces it as a
**pre-commit hook** (Claude Code + Codex). Edit this block, then re-run the skill to recompile.

<!-- constraints:deterministic -->
```json
{
  "protected_paths": [],
  "forbidden_diff_patterns": [],
  "pre_commit_checks": [],
  "notes": "FILL THESE DURING DEFAULT-MODE STARTUP for THIS project — they START EMPTY on purpose; never carry over another project's rules. Keep the lists MINIMAL: only known, mechanically decidable facts whose violation would corrupt the goal or measurement. Default-mode startup cannot pre-seal every reward-hacking mechanism; the standard workflow's independent byte-evidence reviewer handles evolving behavioral tactics. protected_paths = ground-truth acceptance/spec/reference/scoring surfaces that require review. forbidden_diff_patterns = only exact low-false-positive invalid constructs already known for this repo (for example an explicit check-disable switch), never a speculative catalog of hardcoding or test-detection guesses. pre_commit_checks = the project's own deterministic verification commands, each encoded as a JSON argv array (program name followed by literal arguments); raw shell strings are invalid. A flag is evidence for the configured native cross-provider reviewer, not an absolute security claim."
}
```
