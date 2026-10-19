# Kickoff Prompt

Use this as the only per-project prompt. Omit fields that do not apply; do not repeat operating
rules already owned by skills.

```text
Start exactly the one entrypoint selected by STARTUP MODE below. `default-mode` and `fast-mode`
initialize or reconcile this repository and then coordinate it with `pm-dispatch`; `naive-mode`
runs only the isolated one-shot baseline and does not initialize the repository or PM state.

1. TASK + GOAL: <one-line task and measurable success criteria>
2. ACCEPTANCE CRITERIA / EVIDENCE: <executable checks and/or concrete reviewer-judged conditions>; GRADED EVAL: <no | cases, metric, baseline, threshold>
3. PROJECT ROOT: <current repository | explicit path>; REMOTE POLICY: <keep existing | local only | explicit remote>
4. MODELS: <fresh automatic role resolution | explicit provider preferences>; reviewer/adviser must use the upstream provider opposite the actual writer; the active exact pairing is Fable 5.1/Opus for OpenAI-family writers and GPT-6 Astra (`gpt-6-astra`) for Anthropic-family writers; unavailable selections remain unresolved without substitution
5. CONSTRAINTS: <source of hard constraints, fixed resources, disallowed techniques>
6. UI: <linear team + handle | interactive | file>
7. DEBUG MODE: <off | on>
8. STARTUP MODE: <default | fast | naive>; use exactly one mode entrypoint
9. ITERATION MODE: <autonomous | human_guided>; HUMAN CHECK-IN: <5-1440 minutes and pause|autonomous_review fallback | not applicable>
10. RESOURCES / COMPUTE: <available compute, submission method, quotas, credential location without secrets | none; local only>
```

For managed startup, `default-mode` and `fast-mode` verify every selected reviewer/adviser against
the authenticated live catalog and actual writer identity rather than trusting ids in this prompt;
they preserve the repository's existing remote and user-owned
instruction bytes and merge-manage only the marked session-contract block in root
`AGENTS.md`/`CLAUDE.md`. `fast-mode` selects the human-guided short cycle. `naive-mode` runs an
isolated one-shot `codex exec` without project skills or configuration and never materializes the
managed block.
