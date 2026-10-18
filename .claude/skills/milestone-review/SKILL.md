---
name: milestone-review
description: "Perform independent terminal review before milestone completion or stopping for human help; judge the diff, criteria, and fresh implementation evidence."
---
> **Model tier:** HIGH — independent terminal completion and first-principles judgment.


# milestone-review — mandatory milestone terminal judgment

The native reviewer launch follows
`../default-mode/references/native-cli-execution.md`: use a 3600-second timeout and at most four
transient retries with 5m/10m/30m/1h backoff. Do not retry authorization, quota, invalid-model,
malformed-output, evidence, or policy failures.

Every milestone reaches an independent reviewer before the top session treats it as complete or
stops for human help. Mandatory self-evolution must already be finalized and sealed, and the checks
must be rerun on that sealed state. A recorded `review_policy: none` does not skip this terminal
review. Workflow internal review/proofs remain useful execution evidence, but they do not let the
coordinator grade its own stopping decision.

The reviewer must be fresh, read-only, and launched with a freshly resolved semantic Max review role
whose upstream provider family differs from every actual implementation/check writer execution it
covers. Strong direction-qualified candidates are Fable 5.1 (`claude-fable-5-1`) for an
OpenAI-family writer and GPT-6 Astra (`gpt-6-astra`) for an Anthropic-family writer. Model
strength never overrides provider independence; route selection remains bound to the
configured exact terminal pairing. A planned role, coordinator model, CLI name, or relay label is
not actual-writer identity evidence. It inspects the
actual diff/current files, pinned goal, milestone and every child
issue's acceptance criteria, sealed self-evolution checkpoint, reports, and fresh post-seal
implementation/check evidence. It does not fix the work. It asks, per deliverable, whether the
approach is sound, progress is real/general rather than surface or hardcoded, assumptions are
verified, and the milestone genuinely advances the goal. It also judges project-memory residue and
any separately reviewed generalized self-improvement change rather than treating them as post-review
cleanup.

## Run inside `complete-milestone`

First complete every self-evolution mutation and run `pm.py self-improve close <id> ...`, producing
`.pm/self_evolve/<id>.sealed`. Then delegate the milestone acceptance checks and write fresh evidence to
`.pm/evidence/<milestone-id>.md` (or pass repeatable `--evidence` paths). Evidence must identify the
check/procedure, result, relevant output, and limitations and must post-date the reviewed criteria
and implementation state, including the seal.

Resolve the policy-selected reviewer through
`.claude/skills/update-platform-config/scripts/config-sync/resolve_launch_spec.py` before PREPARE.
Keep its repo-local `workflow-native-launch-receipt/v1` and pass one receipt for every distinct
builder role. The receipt is a policy-bound launch specification, not proof that a live process was
authenticated; the fresh native reviewer must actually use the bound identity.

Only a fresh canonical resolution authorizes a launch. Every receipt is re-resolved against the
installed CLIs' live catalogs at PREPARE, FINALIZE, and artifact verification, and all receipts must
agree on one concrete reviewer `catalogModelId`. The persisted `catalogResolution` block in
`config/workflows/models.json` is audit metadata: its schema is validated structurally, but its
`status`, `source`, `verifiedRoles`, `verifiedBackends`, and `evidenceReceipt` grant no authority. A
newly installed `bootstrap-unverified` snapshot reviews normally when fresh resolution succeeds, and
a forged `verified` snapshot cannot rescue a refused launch. The named Fable 5.1 and Astra
candidates receive no availability assumption: the selected exact model, upstream family, backend,
and Max effort must appear in the fresh live catalog. If the candidate is unavailable or
unverifiable, report the route unresolved and never silently substitute another model, version,
alias, provider, backend, or effort.

```bash
# PREPARE — reads evidence; never executes an acceptance shell command.
python3 .claude/skills/milestone-review/scripts/milestone_review.py <id> \
  --base <task-start-ref> \
  --builder-role <implementation-role> \
  --reviewer-role <policy-selected-verification-role> \
  --reviewer-launch-receipt .pm/receipts/launch/<reviewer>.json

# FINALIZE — repeat the exact base/role/evidence arguments. FINALIZE launches
# the fresh read-only reviewer itself and captures its stdout.
python3 .claude/skills/milestone-review/scripts/milestone_review.py <id> \
  --base <task-start-ref> \
  --builder-role <implementation-role> \
  --reviewer-role <policy-selected-verification-role> \
  --reviewer-launch-receipt .pm/receipts/launch/<reviewer>.json \
  --finalize
```

The script refuses PREPARE, FINALIZE, and artifact verification while the checkpoint is pending or
unsealed. It never runs `.pm/accept.sh` or arbitrary bash. It has no `.pm/verify_base` or
`.pm/immutable` dependency. It preserves necessary base integrity by resolving the selected base,
requiring it to be an ancestor of HEAD, and binding base/HEAD, work bytes/diffs, criteria, evidence,
reports, the sealed checkpoint, the raw model-role document digest, the freshly resolved reviewer
catalog model id, and full resolved builder/reviewer identities plus the launch-receipt
path/raw-byte digest into `EVIDENCE-COMMITMENT: sha256:<64hex>`. FINALIZE recomputes the bundle;
drift requires fresh evidence and a new review. The shipped exact capability-policy pairings remain
Astra writer to Opus reviewer and Opus writer to Astra reviewer. Fable 5.1 remains a scoped advice
alternative after fresh live-catalog resolution; other upstreams use only a
freshly resolved opposite-provider `providerReviewerDefaults` entry. The launch receipt is bound structural
evidence, not live authentication of the launched process. Free provider labels never authorize
review.

Criteria, report/context, evidence, the sealed checkpoint, model-role, and launch-receipt files are
bound to their declared repository-relative logical paths through directory-anchored, no-follow
regular-file reads. Symlinked parent components fail closed instead of being resolved elsewhere.

## Audited native reviewer launch

FINALIZE does not accept caller-supplied reply bytes or an `--output` path, and the CLI exposes no
executable, model, backend, argv, model-policy, or catalog-runner flag. It selects the backend,
model, and effort from the canonical policy and the fresh resolution, resolves an allowlisted
installed native CLI independently of caller `PATH`, binds its execution closure, launches one fixed
read-only argv, and captures stdout directly. The launch profiles, environment rebuild, stdin-only
prompt, forbidden-option guard, and closure binding are exactly the ones documented under
`codex-verify`; both skills share one audited implementation.

Production supports `claude`, `codex`, and `cursor` reviewer backends. Claude's fixed vector is
`--effort <policy effort> --model <fresh catalogModelId> -p --output-format text --safe-mode
--permission-mode dontAsk --tools Read,Glob,Grep`. Cursor's is `--print --output-format text
--single-turn --mode plan --model <fresh catalogModelId> --workspace <canonical repo> --trust
--disable-project-configs --disable-auto-update --disable-indexing --disable-codebase-ref`. Both take
the prompt on stdin only and reach their account credentials through the pinned OS-account roots
(`CLAUDE_CONFIG_DIR` under the `passwd` home for Claude), so neither binds a provider/profile file.
Codex keeps project rules ignored while using the OS-account provider/profile file bound under
`codex-verify`, always by absolute path — the `<none>` sentinel is rejected for Codex whatever digest
accompanies it — and config bytes and auth secret files are not persisted. Every other canonical
reviewer backend fails closed. `git` itself is bound the same way: resolved from fixed system
directories rather than caller `PATH`, run with an absolute path, a rebuilt environment, and options
that disable hooks, external diff/textconv drivers, and `ext::` transports.

PREPARE briefs and FINALIZE terminal artifacts use the shared repo-anchored no-follow atomic writer.
Symlinked parents/leaves, hardlinks, special files, and concurrent destination drift fail closed;
existing single-link regular outputs are replaced atomically with a mode-`0600` fsynced file so
normal reruns remain supported without truncating an outside inode. Artifact verification reopens
the stored terminal through the same anchored single-link regular-file policy. FINALIZE also creates
an exclusive random `.txt` under `.pm/reviewer-replies/` through anchored no-follow descriptors. The
artifact records `REVIEWER-EXECUTABLE` and its digest,
`REVIEWER-EXECUTABLE-CLOSURE-SHA256`, `REVIEWER-CONFIG-PATH`,
`REVIEWER-CONFIG-SHA256`, `REVIEWER-MODEL`, `REVIEWER-ARGV-SHA256`, and
`REVIEWER-OUTPUT-SHA256`; artifact verification recomputes these and rejects drift.

This proves which local executable closure, model id, argument vector, and stdout bytes the verifier
observed. It does not cryptographically attest the remote service, account, or credential owner
behind that native CLI; the OS account, installed CLI, native authentication store, and vendor TLS
remain trusted. A caller with authority to replace those same-account resources can cross this
boundary.

## Terminal states

`.pm/reviews/<id>.md` contains exactly one authoritative terminal:

- `TERMINAL: COMPLETE` — every milestone/issue criterion is met, the approach makes sense, and
  fresh evidence supports the actual work.
- `TERMINAL: HUMAN_BLOCKED` — only a human authorization, decision, credential, or external action
  remains; the artifact also carries one legal `HUMAN-REQUEST-KIND` and one exact `HUMAN-REQUEST`.
- `TERMINAL: REVISE` — implementation/check work remains and must be delegated again.

Tests failing, evidence missing/stale, reviewer identity unresolvable/weak/mismatched or unavailable,
budget pressure, debugging, and ordinary technical difficulty are REVISE. An unresolved reviewer
selection is reported as such and never silently replaced. COMPLETE exits 0, REVISE exits 2,
HUMAN_BLOCKED exits 3. The coordinator inspects evidence, dispatches the next round, or forwards the
exact human request.

Immediately before the milestone transition, revalidate the artifact:

```bash
python3 .claude/skills/milestone-review/scripts/milestone_review.py <id> --verify-artifact
```

No other arguments are needed: the artifact carries its base, goal/evidence paths, model-role and
launch-receipt path/digests, full identities, allow-empty flag, HEAD, native reviewer executable
closure, bound native configuration path/digest, model id, argv digest, dedicated reviewer-output
path, and reviewer-output digest. Any implementation, self-evolution, criteria, report, evidence,
policy, identity, resolved model, executable closure, bound config, argv, or reviewer-output drift
exits 2 and requires reopen/reseal or fresh evidence/review.

Passing those checks is necessary but not sufficient for a stored `COMPLETE` or `HUMAN_BLOCKED`.
The artifact and recorded reply are both repository files, so a caller who can edit the tree can
make them agree; digests over caller-written bytes cannot distinguish that from a real review. As
under `codex-verify`, verification first requires the bound output to match the artifact terminal
and request, then rebuilds the bundle, re-resolves the reviewer against a fresh catalog, and launches
it again. Fresh stdout must return the exact same authoritative terminal under the recomputed
commitment and reviewer identity. For `HUMAN_BLOCKED`, its request kind and exact request must also
match. Disagreement, an unparseable reply, a failed launch, or a non-zero exit all fail closed. This
spends one reviewer launch, so run it once at the transition or human-stop boundary. Only `REVISE`
remains drift-checked without a fresh launch because it authorizes no state change or stop.

Only `--verify-artifact` recognizes an exact `.sealed` → `.done` archive rename as the same logical
checkpoint, solely so an interrupted `set milestone ... done` can resume while the milestone
criteria remain in their reviewed pre-transition state. PREPARE and FINALIZE still require the
physical `.sealed` file, and byte drift fails closed.

The milestone and child-issue files remain exact criteria inputs, including status. There is no
status-only exception. For a PR-backed milestone, run the review-owned PR merge helper after
successful verification and before `pm.py set milestone <id> done`; the merge must preserve the
reviewed branch HEAD and current worktree bytes. If a status or any other criteria byte changes
first, a fresh review is required rather than commitment canonicalization. The helper is
documented and maintained by `$codex-verify` under `scripts/review_lifecycle/`; it is a lifecycle
mechanic, not a second reviewer.

When the milestone change itself is limited to project-management artifacts, the same
`$codex-verify` skill also owns the guarded `scripts/publish_guard.py` commit/push/open-PR path.
Use its exact allowlist and receipt protocol; never publish code, skills, agents, workflows, model
policy, or authoritative proofs through that management-only path.

Because reports are committed review inputs, create/update the milestone report before PREPARE,
never after FINALIZE. After successful verification, the completion worker may perform only the
exact approved PR merge when applicable, followed by the atomic PM status transition that archives
`.pm/self_evolve/<id>.sealed` as `.done`; no deliverable, memory, report, reviewer, or UI mutation
may follow.
