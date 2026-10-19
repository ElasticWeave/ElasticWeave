# Reference design: fan-out/fan-in research

Use when independent branches must gather different evidence and a deterministic reducer can merge
their outputs. Do not use parallel branches merely to ask several agents the same broad question.
For idea- or decision-bearing research, do not begin with the broad fan-out: first lock the user's
core problem and test the most uncertain assumptions that could invalidate the most downstream
work.

## Shape

```text
lock_core_problem → derive_and_rank_critical_assumptions
  → [failure_scan, boundary_scan, measurement_resource_scan]
  → reduce_attack_evidence → plan_minimum_valid_checks
  → sequential_preflight_gate
       ├─CONTINUE→ [repo_scan, paper_scan, counterevidence_scan]
       │              → validate_outputs → reduce → coverage_gate
       │                                            ├─PASS→ deliver
       │                                            └─GAP→ one bounded refill
       ├─REFACTOR→ one bounded candidate revision → rerun affected checks
       ├─HUMAN_EXCLUSIVE_REQUEST→ checkpoint
       ├─STOP_IDEA_REFUTED→ negative result
       └─UNRESOLVED→ checkpoint with next valid check
```

The preflight searches may fan out by independent source class, but the checks themselves are
sequential. The reducer must return to the gate after every outcome before lower-priority work can
start.

## Uncertainty-first preflight

Keep the user outcome, success criteria, authority, and hard constraints separate from the mutable
idea, method, technical target, and proxy. Rank assumptions by:

1. remove assumptions already resolved by valid evidence;
2. keep every unresolved blocker and load-bearing assumption in the critical set;
3. within comparable downstream impact, current uncertainty, then dependency centrality;
4. discriminating validity of the proposed check;
5. cost and latency.

Cost selects the smallest valid check for the same assumption. It cannot move easy background work
ahead of a runnable high-uncertainty blocker. A tunable method receives only a predeclared minimum
fair tuning envelope before refutation; full tuning belongs after the gate.

## Branch contracts

Give each attack branch a non-overlapping assumption/source class and this structured result:

```json
{
  "assumption_id": "A-001",
  "falsifier": "...",
  "scope": "...",
  "evidence": [{"text": "...", "source_url": "...", "source_type": "primary", "date": "..."}],
  "decision_impact": "refute | narrow | support_context | unresolved",
  "gaps": ["..."],
  "searched_at": "..."
}
```

After preflight `CONTINUE`, give each full-research branch a non-overlapping source class and this
result:

```json
{
  "claims": [{"text": "...", "source_url": "...", "source_type": "primary", "date": "..."}],
  "gaps": ["..."],
  "searched_at": "..."
}
```

- `repo_scan`: official repositories, release notes, live popularity and maintenance signals.
- `paper_scan`: primary papers and official proceedings pages.
- `counterevidence_scan`: failed replications, limitations, contrary mechanisms, and boundary cases.
- `failure_scan`: direct null/negative evidence, impossibility results, and failed replications tied
  to one critical assumption.
- `boundary_scan`: scaling cliffs, counterexamples, non-working regimes, and stronger baselines.
- `measurement_resource_scan`: invalid proxies, leakage, data/compute complexity, authority, and
  integration blockers.
- Branches write isolated artifacts; none edits the final report.

## Reducer

The reducer is deterministic where possible:

1. Reject missing URLs, dates, or source types.
2. Deduplicate by canonical URL and normalized claim key.
3. Preserve conflicts as separate records rather than averaging them away.
4. Compute coverage by research question, not raw source count.
5. Hand only the validated evidence table to the synthesis node.

For the attack fan-in, also reject records without an assumption ID, falsifier, scope, and decision
impact. Preserve validated attack evidence in the later full ledger instead of repeating the search.

## Bounded refill

The coverage gate may request one focused refill with named missing questions. It must not restart
all branches. A remaining gap exits as `unresolved_with_gaps` and is stated in the delivery.

Do not route an ordinary evidence gap to a human. `HUMAN_EXCLUSIVE_REQUEST` is legal only for an
authorization, decision, credential, or external action that no agent/workflow can supply. The
terminal reviewer records that kind and a concrete request before the top session may emit
`HUMAN_BLOCKED`; missing sources, weak coverage, failed checks, and exhausted search budgets remain
`REVISE` or unresolved.

## Cost controls

- Search snippets are discovery aids; open only shortlisted primary sources.
- Keep extracted facts and pointers, not full pages, in shared state.
- Stop a branch once its assigned questions are covered; do not use one global token quota as a
  reason for all branches to over-search.
- Do not count easy completed branches as progress when the highest-priority blocker remains
  untested, invalid, or resource-blocked.
