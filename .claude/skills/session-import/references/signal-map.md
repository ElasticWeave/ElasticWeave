# signal-map — extracted signal → target artifact / kickoff field

Read this in **step 3** (distill). Each row: a durable signal to mine from the prior session, the source cue
the miner keys on, where it lands in our setup, and how much to trust it (drives the `⚠ CONFIRM` markers).
Distillation is JUDGMENT: dedup, resolve conflicts, keep only high-signal, carry the `[src: <session>:<line>]`
tag, redact secrets. Never copy raw transcript lines — synthesize.

| Extracted signal | Source cue (regex / heuristic) | Target artifact / kickoff field | Confidence |
|---|---|---|---|
| Task + one-line goal | first user prompt; "optimize/build/implement/fix…", "the goal is", "we're trying to" | kickoff **1**; `goal.draft.md` Ultimate Goal; plan.md PINNED GOAL | high |
| Milestones / success criteria | "milestone", "M1/M2", "success =", "reach/beat X (cycles/acc/latency)" | kickoff **1** ladder; `goal.draft.md` Acceptance + ladder | med |
| Acceptance criterion / verify method | test cmd (`pytest`, `python3 tests/…`), "done when", "passes when", harness name, "verified N" | kickoff **2**; `acceptance-criteria.draft.md` (criterion + expected observation + fresh check, explicit argv preferred); `goal.draft.md` Acceptance | high |
| Graded-eval signal | metric + threshold + baseline present ("baseline X, beat by Y", eval set) | kickoff **2**; `evaluation.draft.md` (source/metric/baseline/threshold + covering cases/per-case traces) | med |
| Evaluation fixture / reference case | "fixture", "golden", "reference output", fixed eval/test input, held-out case | `evaluation.draft.md`; location/cost → `resources.draft.md`; fixed/off-limits fixture → `constraint.draft.md` | med |
| Off-limits paths | "don't edit tests", "immutable", "frozen", "never modify <file>" | `constraint.draft.md` §2/§3 candidates; kickoff **5** (no separate gate file) | high |
| Target repo + intro method | git URLs, cwd, clone cmds, "as a submodule/reference" | kickoff **3** (+ submodule\|plain\|text-only guess) | high |
| Project remote | "push to", private repo URL, "origin =" | kickoff **4** (else `⚠ SUPPLY`; never fabricate) | low |
| Machine constraints | ssh/host limits, "no GPU", "CPU-only", "no sudo", disk quota (extract_constraints ch1/ch2) | constraint.md §1; kickoff **5** | med |
| Functional constraints | "N_CORES=1", "specialize on dims not data", "don't monkeypatch", KW-scan hits | constraint.md §2a; kickoff **5** | high |
| Deterministic constraints | protected paths + cheat patterns + required checks | constraint.md §3 candidates (→ constraints-to-hooks) | med |
| Resources / compute | ssh aliases, `srun`/`sbatch`, GPU-alloc cmds, quotas, "runs on <host>" | `resources.draft.md`; kickoff **8** | med |
| Machine facts (durable) | cost-model/ISA/env facts ("VLEN=8", "load 2/cycle", API rate limit) | `memory_seeds.md` → MachineFacts (`unverified`) | med |
| Key decisions | "we decided", "chose X because", "the approach is" | `memory_seeds.md` → MemoryHistory; docs/wiki seed | med |
| Rejected paths / failures | "didn't work", "reverted", "failed because", "dead end" | `memory_seeds.md` → anti-loop fences (plan.md) | high |
| Reference material | URLs, arXiv, "see <repo>", doc links | `reference_leads.md` quarantine → project `.memory` only; reusable guidance requires self-improve transfer admission, supported lemma, learned-reference validation, and independent review | high |
| Audience level | who the user is / expertise cues | kickoff note; `.pm/audience.md` | low |
| Current state | last substantive msgs, current metric value, git status | `handoff.md`; plan.md Current state | high |
| Debug preference | "human review each step", "debug mode on" | kickoff **7** (else `⚠ SUPPLY`) | low |
| Linear team / handle | team name, @handles mentioned | kickoff **6** (usually `⚠ SUPPLY`) | low |

**Confidence → behavior:** `high` = draft it directly (still human-confirmed if alignment-critical). `med` = draft +
`⚠ CONFIRM`. `low` = usually a `⚠ SUPPLY` placeholder; **never fabricate** a remote/team/handle/threshold.

**Alignment-critical rows (goal, acceptance, off-limits paths, fixtures, graded-eval baseline/threshold) are
ALWAYS `⚠ CONFIRM`** regardless of cue strength. They determine what a terminal reviewer accepts, so a wrong
one silently steers the whole run (step 5 confirms them).
