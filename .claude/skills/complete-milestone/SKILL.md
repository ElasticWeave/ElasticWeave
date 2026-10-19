---
name: complete-milestone
description: "Close a milestone through evidence sealing, independent terminal review, approved PR handling, and the authorized PM transition."
---
> **Model tier:** HIGH — terminal judgment routing plus milestone self-evolution.


# complete-milestone — review the final bytes before stopping

Dispatch this lifecycle to a bounded completion owner or admitted workflow. That owner executes the
checkpoint, self-evolution, checks, report, and review preparation. The top session only authorizes
the milestone/base/semantic roles, launch receipts, and optional exact PR scope, inspects returned
artifacts, and dispatches the terminal route. It never mutates the project, runs these commands,
performs self-evolution, merges, or writes the verdict itself. The independent reviewer only judges
and never repairs or completes the lifecycle. A PR-backed COMPLETE route uses a separate bounded
review-owned PR-merge worker; the final PM transition is likewise worker-executed after the top
inspects the merge receipt. The publication and PR-merge helpers are maintained by
`codex-verify`; no separate lifecycle skill is required.

The terminal review must be the last judgment over deliverable bytes. Finish and seal every
mandatory self-evolution mutation first; then rerun acceptance, write the final report, review, and
mechanically revalidate. For PR-backed work, the exact reviewed/approved merge is the sole external
transition allowed before `set milestone ... done`; it must preserve the reviewed branch HEAD and
worktree bytes. `set milestone ... done` remains the one intentional post-review project mutation.
It atomically updates the board and archives the lifecycle control token; it must not trigger or be
followed by project, memory, report, reviewer, or UI mutation. The only other
post-verdict external/UI route is a valid HUMAN_BLOCKED artifact's exact request plus its matching
atomic `awaiting_human` transition and receipts; that route is not completion.

## Inputs

- milestone id and task-start base;
- every implementation/check provider whose work or evidence is covered;
- a configured reviewer semantic role whose upstream provider family differs from every builder/check
  provider;
- milestone and child-issue criteria, run traces, reports, and workflow proof/evidence.
- when PR-backed, the exact repository, PR, approved HEAD, approval pointer, merge strategy,
  authorization sidecar, and durable merge-receipt path.

## Steps

1. **Open/resume the checkpoint.** The bounded owner runs `pm.py milestone-evolve <id>`. This
   idempotently creates `.pm/self_evolve/<id>.pending`. Resume an existing pending round. A sealed
   round may be reopened only after REVISE or resumed HUMAN_BLOCKED, with
   `milestone-evolve <id> --reopen --reason "<why new project mutation is required>"`; reopening
   supersedes the prior seal before any builder changes a project byte.
2. **Self-evolve and seal the checkpoint before terminal review.** Read raw workflow/subagent
   traces, node transcripts, evidence, reports, prior reviews/verdicts, and evaluation cases. Write
   project-specific residue to `.memory/`; do not promote a one-project workaround into cross-task
   guidance. Any generalized lemma, reference, skill, hook, or workflow change must satisfy the
   self-improve evidence contract and receive its separate independent review. Correct or
   supersede wrong entries explicitly. When every self-evolution mutation is final, run:

   ```bash
   python3 .claude/skills/pm-dispatch/scripts/pm.py self-improve close <id> \
     --note "traces_read: … | trace_findings: … | memory_delta: … | distilled: …"
   ```

   This replaces pending with `.pm/self_evolve/<id>.sealed`. Do not mutate deliverables, `.memory`,
   generalized artifacts, or criteria after sealing without explicitly reopening the round.
3. **Run fresh post-seal acceptance checks.** The implementation/check agent—not the top
   coordinator—runs the milestone's concrete procedures against the sealed final state and records
   command/procedure, result, relevant output, and limitations in `.pm/evidence/<id>.md` (or
   repeatable explicit `--evidence` paths). Evidence must post-date the sealed checkpoint and every
   reviewed criteria/work byte. Workflow receipts may supplement but never replace current checks.
4. **Write the final milestone report before review.** The bounded owner runs
   `pm.py report milestone <id> --summary "<final result + post-seal evidence + outcome>"`. Do any
   delegated human-UI report/status mirroring now, before terminal review. The commitment includes
   the local report; editing it afterward invalidates the artifact.
5. **Run the mandatory independent terminal review.** Resolve the fresh reviewer launch receipt from
   the canonical model policy, then launch a read-only reviewer whose upstream provider family
   differs from every builder/check provider. Pass one receipt for every distinct builder role. An
   issue verdict, workflow-internal review, or legacy `review_policy: none` cannot skip this stopping
   review:

   ```bash
   # PREPARE — reads evidence and writes the review brief; it never runs acceptance commands.
   python3 .claude/skills/milestone-review/scripts/milestone_review.py <id> \
     --base <task-start-ref> \
     --evidence .pm/evidence/<id>.md \
     --builder-role <implementation-role> \
     --reviewer-role <policy-selected-verification-role> \
     --reviewer-launch-receipt \
       .pm/receipts/launch/<implementation-role>-<reviewer-role>.json

   # FINALIZE — repeat the exact input set; the verifier launches the reviewer and captures stdout.
   python3 .claude/skills/milestone-review/scripts/milestone_review.py <id> \
     --base <task-start-ref> \
     --evidence .pm/evidence/<id>.md \
     --builder-role <implementation-role> \
     --reviewer-role <policy-selected-verification-role> \
     --reviewer-launch-receipt \
       .pm/receipts/launch/<implementation-role>-<reviewer-role>.json \
     --finalize
   ```

   Repeat the `--builder-role`/`--reviewer-launch-receipt` pair for every distinct builder role and
   use the identical complete argument set for PREPARE and FINALIZE. Do not provide a
   caller-selected output path or caller-authored reviewer reply bytes: verifier-owned FINALIZE
   creates the exclusive reviewer output receipt and terminal artifact. The script requires the
   seal, binds the checkpoint plus final project bytes, reports, criteria, resolved role identities,
   fresh catalog evidence, and post-seal evidence, and never executes an acceptance script.
6. **Route the exact terminal.** The bounded owner returns the artifact; the top inspects and routes:

   - `REVISE` (exit 2): do not stop or mark done. Delegate
     `milestone-evolve <id> --reopen --reason "<review finding>"` before any new project mutation,
     then implement, reseal, rerun every final check/report, and obtain a new review.
   - `HUMAN_BLOCKED` (exit 3): only a concrete human authorization, decision, credential, or external
     action with canonical `HUMAN-REQUEST-KIND` and `HUMAN-REQUEST` is valid. Authorize one bounded
     UI-routing worker to forward exactly that request and, in the same atomic routing unit, set the
     sealed milestone to `awaiting_human` and return transition/send read-back receipts. It must not
     append any completion, report, status, close, or ordinary UI mirror update. Tests, budgets,
     debugging, ordinary technical difficulty, and unfinished work are REVISE. After human input,
     reopen before any project mutation and repeat seal-to-review.
   - `COMPLETE` (exit 0): continue to the final transition. Only COMPLETE permits done.
7. **Revalidate, resolve any PR, and mark done as the final mutation.** Immediately revalidate:

   ```bash
   python3 .claude/skills/milestone-review/scripts/milestone_review.py <id> --verify-artifact
   ```

   Drift returns to step 1 or 3 as appropriate. If the milestone is PR-backed, the top now
   authorizes one exact bounded review-owned PR-merge worker while the milestone is still in its reviewed
   pre-`done` state. That worker revalidates the same artifact, requires artifact `HEAD-SHA`, approved
   SHA, and PR `headRefOid` to match, requires green CI, merges with the match-head guard, and returns
   a read-back receipt. A failure or uncertain merge leaves the milestone pre-`done`; inspect and
   recover the same PR rather than changing status. The merge must not change the reviewed branch
   HEAD or worktree bytes.

   The PR worker receives both the exact authorization sidecar and its exclusive durable merge-receipt
   path:

   ```bash
   python3 .claude/skills/codex-verify/scripts/review_lifecycle/pr_merge.py \
     --checkout <reviewed-checkout> \
     --repository <OWNER/REPO> \
     --pr <number> \
     --approval-review-id <github-review-id> \
     --kind milestone \
     --id <id> \
     --approved-head <approved-sha> \
     --strategy <squash|merge|rebase> \
     --authorization-receipt .pm/receipts/merge/milestone-<id>-authorization.json \
     --receipt .pm/receipts/merge/milestone-<id>.json
   ```

   PM consumes only that worker-produced receipt after its verifier-owned, read-only
   `verify_merged_github` read-back of the exact PR, approval, HEAD/base, and merge commit. The
   verifier never invokes `pr merge`.

   After the top inspects a successful merge receipt—or immediately after verification when no PR
   exists—it authorizes a bounded PM worker to run:

   ```bash
   python3 .claude/skills/pm-dispatch/scripts/pm.py set milestone <id> done
   ```

   `set done` revalidates the artifact again, then performs only the atomic board status change and
   `.pm/self_evolve/<id>.sealed` → `.done` control-token archival. Stop and return the report,
   terminal artifact, merge receipt when applicable, and self-evolution outcome to the top. Do not
   mutate or mirror anything afterward; every UI side effect must have preceded final review.

## Boundaries

- Never review before mandatory self-evolution is sealed, or use pre-seal evidence.
- Never use `review_policy: none`, an issue verdict, or an internal workflow review to skip terminal review.
- Never treat HUMAN_BLOCKED as completion or use it for agent-performable work.
- Never stop on REVISE; reopen, delegate, reseal, and re-review.
- Never self-review, repair inside the reviewer session, or use a same-provider reviewer.
- After COMPLETE, allow only the exact approved PR merge when applicable and the matching atomic
  `set done`; never add a report, UI/mirror write, or other mutation. After REVISE, do not add a
  completion/report/status/UI mutation. After HUMAN_BLOCKED, allow only its exact reviewer-authorized
  request plus the same atomic
  `awaiting_human` transition/read-back receipt; no other post-verdict mutation is authorized.
- Never let the top session execute this lifecycle; it authorizes, inspects, and routes only.
