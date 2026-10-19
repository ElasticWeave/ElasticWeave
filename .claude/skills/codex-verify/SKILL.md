---
name: codex-verify
description: "Run independent issue terminal review from the diff, criteria, and fresh worker evidence; return COMPLETE, HUMAN_BLOCKED, or REVISE."
---
> **Model tier:** HIGH — completion judgment by a verified cross-provider review role.


# codex-verify — independent issue terminal review

Read `../default-mode/references/native-cli-execution.md` before launching the terminal reviewer.
The reviewer CLI receives a 3600-second timeout. Retry only transient API/service/transport or
unexpected-process failures using the bounded 5m/10m/30m/1h schedule; auth, quota, invalid request
or model, malformed output, evidence, and policy failures stop and remain unresolved.

Use this skill when an issue needs an independent terminal judgment before the coordinator accepts
its result. The reviewer reads the actual diff/current files, the goal and issue criteria, and
fresh evidence produced by the implementation/check agent. The reviewer is read-only and cannot
fix the work. Its semantic role must resolve to an active Max verification identity on an upstream
provider family different from every actual writer execution it covers. Strong direction-qualified
routes are Fable 5.1 (`claude-fable-5-1`) for an OpenAI-family writer and GPT-6 Astra
(`gpt-6-astra`) for an Anthropic-family writer. Route selection remains bound to the
configured exact terminal pairing. Model strength never overrides provider independence, and
a planned role, coordinator model, CLI name, or relay label is not actual-writer identity evidence.

This is an evidence/review contract, not a shell gate. `verify.py` never executes `.pm/accept.sh`,
an arbitrary `--cmd`, or any other acceptance command. It has no dependency on `.pm/immutable` or
`.pm/verify_base`.

## Evidence contract

Before PREPARE, delegate the stated acceptance checks to the implementation/check agent and save
its fresh result to `.pm/evidence/<issue-id>.md` (or pass repeatable `--evidence <path>` arguments).
The evidence should identify what was checked, commands or procedure, exit/result, relevant output,
and any known limitations. It must post-date the criteria and reviewed implementation state.
Write/update `.pm/reports/<issue-id>.md` before PREPARE as well: reports are reviewed context and
part of the commitment. Never write the completion report after FINALIZE.

The coordinator supplies the task-start base and semantic roles. The repository-local
`config/workflows/models.json` must be `workflow-model-roles/v3` and pass the canonical structural
validator, including the audited static family-binding map.

Launch authorization comes only from a fresh canonical resolution. Before PREPARE, resolve the
reviewer launch specification with `resolve_launch_spec.py`, which queries the installed native CLIs
for their live catalogs, and retain its repo-local `workflow-native-launch-receipt/v1`. Pass one
receipt per distinct builder role. Every receipt is re-resolved against a fresh catalog at PREPARE,
FINALIZE, and artifact verification, and all receipts must agree on one concrete reviewer
`catalogModelId`; disagreement fails closed. The named Fable 5.1 and Astra routes receive no
special availability assumption: the selected exact model, upstream family, backend, and Max effort
must appear in that fresh live catalog. If the selected candidate is unavailable or unverifiable,
report the route unresolved and never silently substitute another model, version, alias, provider,
backend, or effort.

The persisted `catalogResolution` block is audit metadata, never authority. Its schema is checked
structurally, but its `status`, `source`, `verifiedRoles`, `verifiedBackends`, and `evidenceReceipt`
grant nothing. A freshly installed repository whose snapshot still says `bootstrap-unverified`
therefore reviews normally as long as fresh resolution succeeds, and a forged `verified` snapshot
cannot rescue a launch that fresh resolution refuses. Missing, weak, same-upstream, or
policy-mismatched identities fail closed. A local-only repository without a discoverable upstream
must pass `--base` explicitly.

```bash
# 1. PREPARE: collect criteria + diff + fresh evidence and write the brief.
python3 .claude/skills/codex-verify/scripts/verify.py <id> \
  --base <task-start-ref> \
  --builder-role <implementation-role> \
  --reviewer-role <policy-selected-verification-role> \
  --reviewer-launch-receipt .pm/receipts/launch/<reviewer>.json

# 2. FINALIZE with exactly the same base/role/evidence arguments. FINALIZE launches
#    the fresh read-only reviewer itself and captures its stdout.
python3 .claude/skills/codex-verify/scripts/verify.py <id> \
  --base <task-start-ref> \
  --builder-role <implementation-role> \
  --reviewer-role <policy-selected-verification-role> \
  --reviewer-launch-receipt .pm/receipts/launch/<reviewer>.json \
  --finalize
```

PREPARE computes `EVIDENCE-COMMITMENT: sha256:<64hex>` over the base/HEAD identity, current
implementation bytes, diffs, criteria, reports, evidence, raw model-role document digest, freshly
resolved reviewer catalog model id, and full builder/reviewer identities. The shipped exact
`reviewPairings` are Astra writer to Opus reviewer and Opus writer to Astra reviewer. Fable 5.1
remains a scoped advice alternative; other
writers use only a freshly resolved opposite-provider `providerReviewerDefaults` entry. The launch-receipt path and raw-byte digest are also
bound. FINALIZE recomputes everything, so role-document, receipt, or identity drift invalidates the
reply.
Re-run the checks and reviewer instead of overriding the mismatch.

Criteria, report/context, evidence, model-role, and launch-receipt files are bound to their declared
repository-relative logical paths through directory-anchored, no-follow regular-file reads.
Symlinked parent components fail closed instead of being resolved to another path.

## Audited native reviewer launch

FINALIZE does not accept caller-supplied reply bytes or an `--output` path, and the CLI exposes no
executable, model, backend, argv, model-policy, or catalog-runner flag. It selects the backend,
model, and effort from the canonical policy and the fresh resolution, resolves an allowlisted
installed native CLI independently of caller `PATH`, binds its execution closure, launches one fixed
read-only argv, and captures stdout directly.

Production has audited launch profiles for three backends; every other canonical reviewer backend
fails closed.

- `claude` — `claude --effort <policy effort> --model <fresh catalogModelId> -p --output-format text
  --safe-mode --permission-mode dontAsk --tools Read,Glob,Grep`
- `codex` — `-c model_reasoning_effort="<policy effort>" exec --model <fresh catalogModelId>
  --sandbox read-only --disable multi_agent --ephemeral --ignore-rules --color never
  --cd <canonical repo> -`
- `cursor` — `cursor-agent --print --output-format text --single-turn --mode plan
  --model <fresh catalogModelId> --workspace <canonical repo> --trust --disable-project-configs
  --disable-auto-update --disable-indexing --disable-codebase-ref`

Claude expresses reasoning effort as a flag rather than inside the concrete catalog id, so the policy
effort and the freshly verified id are both passed; the tool set is the three read-only tools.

The review prompt travels only on stdin; it never appears in `argv` or the environment. Before launch
the built vector is checked against a forbidden-option set, so a future profile edit cannot quietly
reintroduce write authority (`--force`, `--yolo`, `-f`, `-w`, `--approve-mcps`, `--auto-review`,
`--dangerously-skip-permissions`), endpoint or credential redirection (`--endpoint`, `--api-key`,
`--header`, `-H`, `-e`, `--bedrock`), caller code or instruction loading (`--plugin-dir`,
`--mcp-config`, `--settings`, `--agents`, `--system-prompt`, `--append-system-prompt`,
`--permission-prompt-tool`), filesystem widening (`--add-dir`, `--worktree`, `--worktree-base`), or
caller-seeded history (`--resume`, `--continue`, `--fork-session`, `--session-id`).

The child environment is built from an empty mapping rather than inherited. No caller variable,
including an unknown provider-specific credential selector, reaches the reviewer. `HOME` comes from
the OS `passwd` entry for the current uid, `PATH` is the fixed system helper list
`/usr/local/bin:/usr/bin:/bin`, and locale, timezone, terminal, and no-color values are fixed. Claude
receives only `CLAUDE_CONFIG_DIR` from `<passwd-home>/.claude`; Codex receives only `CODEX_HOME` from
`<passwd-home>/.codex`; and the direct Cursor runtime receives only the fixed wrapper identity marker
`CURSOR_INVOKED_AS=cursor-agent`. The concrete model travels in the receipt-bound argv. Ambient
`OPENAI_*`, `CODEX_*`, `ANTHROPIC_*`, `CLAUDE_*`, `CURSOR_*`, cloud, endpoint, proxy/TLS, loader, and
config variables therefore cannot substitute a caller account or route.

For Codex, the OS-account provider/profile file at `<passwd-home>/.codex/config.toml` is resolved
through no-follow regular-file checks and bound by absolute path plus sha256; a missing or unsafe
config fails closed. Its bytes are never printed or persisted, and auth/credential secret files are
not read into the artifact. A Codex artifact always carries an absolute configuration path: the
`<none>` sentinel is rejected unconditionally, with any digest. Claude and Cursor bind no
provider/profile file, because both reach the account credential store through the pinned roots
above; their artifacts carry `<none>` and the fixed empty digest.

Because `cursor-agent` installs as a shell wrapper over a bundled runtime, the binding is a closure,
not a single file. The verifier consumes the public `CatalogEvidence.executable`
`ResolvedExecutable` produced by the fresh canonical resolver, requires its command record to match
the receipt, and launches its exact `invoked_argv` prefix. Cursor therefore runs as the bound
`node --use-system-ca index.js` while the wrapper, runtime, bundle, and opaque full install-tree
manifest remain covered by `REVIEWER-EXECUTABLE-CLOSURE-SHA256`. The verifier resolves that same
public closure again after the process exits; it never calls catalog-private wrapper parsers or
degrades to a bare-path invocation.

PREPARE writes `.pm/review-briefs/`, and FINALIZE writes the terminal artifact, through one
repo-anchored no-follow primitive. It rejects symlinked parents or leaves, multi-link or special
destinations, and destination drift; writes a mode-`0600` same-directory temporary file; fsyncs it;
and atomically replaces an existing single-link regular output before fsyncing the directory. Normal
reruns remain supported without ever opening or truncating the prior inode. Artifact verification
reopens the stored artifact through the same anchored single-link regular-file policy. FINALIZE also
creates an exclusive random `.txt` under `.pm/reviewer-replies/` through anchored no-follow
descriptors. The artifact records `REVIEWER-EXECUTABLE` and its digest,
`REVIEWER-EXECUTABLE-CLOSURE-SHA256`, `REVIEWER-CONFIG-PATH`,
`REVIEWER-CONFIG-SHA256`, `REVIEWER-MODEL`, `REVIEWER-ARGV-SHA256`, and
`REVIEWER-OUTPUT-SHA256`. Artifact verification recomputes these from the current policy,
installation, OS-account config, and files, and rejects any drift or tampering.

This proves which local executable closure, model id, argument vector, and stdout bytes the verifier
observed. It does not cryptographically attest the remote service, account, or credential owner
behind that native CLI; the OS account, installed CLI, native authentication store, and vendor TLS
remain trusted. A caller with authority to replace those same-account resources can cross this
boundary.

## Trusted git

The commitment is only as trustworthy as the `git` that produced the diffs behind it, so `git` is
resolved the same way the native reviewers are: by scanning the fixed system directories
`/usr/local/bin`, `/opt/homebrew/bin`, `/usr/bin`, `/bin`, `/snap/bin` for a regular executable file,
never through caller `PATH`. A caller who puts a `git` of their own earlier on `PATH` changes
nothing; if no system `git` exists, the review fails rather than falling back. The resolved path and
its sha256 are bound on first use and rechecked on every later call, so replacing the binary
mid-review fails closed.

Each invocation uses that absolute path with an environment built from scratch: the system helper
`PATH`, the `passwd` `HOME`, fixed locale/timezone, and a fixed set of `GIT_*` values that pin system
and global config to `/dev/null` and disable terminal prompts. Nothing is inherited, so caller
`GIT_DIR`, `GIT_CONFIG_*` redirection, `GIT_EXTERNAL_DIFF`, `GIT_ASKPASS`, and `GIT_SSH_COMMAND`
never reach it. Every call uses `--no-replace-objects` plus `GIT_NO_REPLACE_OBJECTS=1`, pins the
canonical worktree explicitly, and reapplies the executable binding after the command. Command-line
config fixes file-mode, symlink, case, fsmonitor, hook, rename, external-diff, protocol, and
untracked-cache behavior; diff calls additionally pin literal pathspecs, submodule handling, the
algorithm, and textconv/external-diff refusal. Untracked enumeration deliberately ignores
`.git/info/exclude` and other uncommitted exclude metadata. Assume-unchanged or skip-worktree index
flags fail closed because they can hide real bytes. Tracked worktree inventory also compares actual
file bytes to HEAD, so a matching index stat cache cannot omit a modified path. The worktree patch
itself is then built from a private temporary index: HEAD is read into that index, current
worktree bytes are hashed with `--no-filters` into an isolated object store, and
`git diff --cached HEAD` emits the exact patch, including regular-file content, symlink targets,
mode, deletion/type changes, and binary form. The live index is never written. Consequently private
replacement, config, exclude, or index metadata cannot make a different tree appear under the
committed base/HEAD ids. One visible side-effect is that ignored untracked files appear in review
bundles unless they are an explicit verifier control path.

## Binding terminal contract

The reviewer and `.pm/verdicts/<id>.md` use exactly one terminal state:

- `TERMINAL: COMPLETE` — every criterion is met and fresh evidence supports the actual work.
- `TERMINAL: HUMAN_BLOCKED` — only a human can supply an authorization, decision, credential, or
  external action. It also requires exactly one
  `HUMAN-REQUEST-KIND: authorization|decision|credential|external-action` and a concrete,
  non-empty `HUMAN-REQUEST: ...`.
- `TERMINAL: REVISE` — continue delegated implementation/check work.

Test failure, missing evidence, budget, ordinary technical difficulty, debugging, and unfinished
implementation are always `REVISE`, never `HUMAN_BLOCKED`. COMPLETE exits 0, REVISE exits 2, and
HUMAN_BLOCKED exits 3. A reviewer that is uninstalled, malformed, weak, same-upstream, incorrectly
paired, or absent from the fresh catalog is reported unresolved and fails closed to REVISE. It is
never silently replaced.

Immediately before a board transition, revalidate the stored artifact:

```bash
python3 .claude/skills/codex-verify/scripts/verify.py <id> --verify-artifact
```

The artifact records its base, goal/evidence paths, model-role path/digests, launch-receipt
path/digests, full identities, allow-empty flag, HEAD, native reviewer executable closure, bound
native configuration path/digest, model id, argv digest, dedicated reviewer-output path, and
reviewer-output digest. No other arguments are needed. Any code, criteria, report, evidence, policy,
role-identity, resolved model, executable closure, bound config, argv, or reviewer-output drift
exits 2.

Those recorded fields are drift and audit inputs, not authority. Both the artifact and the recorded
reply live in the repository, so anyone who can edit the tree can write a set of bytes that agrees
with itself, and no digest over caller-written bytes can tell that apart from a real review. A
stored `COMPLETE` or `HUMAN_BLOCKED` is therefore not believed on the strength of its own bytes.
Verification first parses the bound stored output and requires it to match the artifact terminal and
structured request. It then rebuilds the bundle, re-resolves the reviewer against a fresh catalog,
launches it again on the same brief, and requires the fresh stdout to return the exact stored
terminal. For `HUMAN_BLOCKED`, the fresh `HUMAN-REQUEST-KIND` and `HUMAN-REQUEST` must also match
byte-for-byte. Success prints the path of the separate fresh reply. A disagreement, malformed reply,
failed launch, or non-zero exit fails closed; verification never falls back to stored bytes.

This costs one reviewer launch for each authoritative `COMPLETE` or `HUMAN_BLOCKED` verification,
so run it once at the transition or human-stop boundary rather than in a loop. `REVISE` remains
drift-checked without a fresh launch because it authorizes no completion, stop, state transition, or
human action.

Tests exercise this path through a private in-process test-executable seam that runs a local Python
script instead of a model. It is a keyword-only parameter on the Python entry points; the CLI has no
flag, and no environment variable switches it on.

The issue file is an exact criteria input, including its status. There is deliberately no
status-only exception: a PR-backed issue must run the review-owned PR merge worker after
successful artifact verification and before `pm.py set issue <id> done`. If status or any other
criteria byte changes first, obtain fresh evidence and a fresh review; never weaken or canonicalize
the commitment.

The implementation agent never reviews or edits its own verdict. The coordinator inspects the
artifact and routes REVISE back to an implementation subagent; HUMAN_BLOCKED becomes one precise
request to the human; only COMPLETE permits issue completion.

## Review-owned publication and PR lifecycle

This skill owns the bounded publication and pull-request lifecycle helpers. These are mechanical post-review operations,
not additional verdicts, and they never turn an unreviewed change into a success.

### Management-artifact publication

Use `scripts/publish_guard.py` only when the coordinator has authorized an exact allowlisted set of
management files (`.pm/` non-verdict inputs, `.memory/`, `templates/`, `plan.md`, `bitlesson.md`,
`constraint.md`, or `resources.md`). It supports `commit-only`, `push`, and `open-pr`; it refuses
source code, skills, agents, workflows, gates, model-role configuration, pinned proofs, and every
other path. The worker must bind the exact repository, branch, remote, base, message, and receipt,
verify the staged bytes before commit, verify the resulting commit, and revalidate immediately
before each external push or PR action. The top session authorizes and inspects; a bounded worker
performs the side effect.

```bash
python3 .claude/skills/codex-verify/scripts/publish_guard.py prepare \
  --repo <repository> --action <commit-only|push|open-pr> --head <branch> \
  --remote <remote> --base <base-branch> --remote-url <exact-url> \
  --message "<exact commit message>" --file <exact-managed-file> \
  --receipt /tmp/publish-receipt.json
```

Run `verify-stage` before the exact commit and `verify-commit` after it. Never broaden the file
list, reuse a receipt after any input/HEAD change, or use this path for code/control-plane changes;
those require a normal reviewed issue or milestone.

### Exact approved-PR merge

For a PR-backed item with a fresh `TERMINAL: COMPLETE`, use the review-owned helpers under
`scripts/review_lifecycle/`. `pr_preflight.py` revalidates the reviewed pre-transition board
state and terminal artifact. Then a bounded worker runs `pr_merge.py`, which checks the exact
approval review, approved HEAD/base, green CI, canonical repository, and authorized strategy;
merging uses a match-head guard and writes an exclusive durable
`.pm/receipts/merge/` receipt. Remote GitHub queries use the canonical
`--repo github.com/<OWNER/REPO>` target from a temporary working directory outside the reviewed checkout. After merging, it runs the equivalent of
`git -C <reviewed-checkout> fetch --prune`, reads the merged PR back, and rerun the exact local preflight. It must not touch the reviewed checkout's branch, index, or worktree. All local
repository decisions use a fixed verified system Git with a rebuilt environment; caller `GIT_*`,
hooks, aliases, replacement objects, and alternate worktree/index settings are excluded. Only
after the top inspects the receipt may a separate PM worker perform the final corresponding `set ... done` transition. If the item is already `done`, the pre-transition artifact is stale and
must be reviewed again; there is no status-only exception. A failed, uncertain, stale,
same-provider, or head-changed operation leaves the item pre-`done`.

```bash
python3 .claude/skills/codex-verify/scripts/review_lifecycle/pr_preflight.py \
  --kind <issue|milestone> --id <id> --approved-head <approved-sha>

python3 .claude/skills/codex-verify/scripts/review_lifecycle/pr_merge.py \
  --checkout <reviewed-checkout> --repository <OWNER/REPO> --pr <number> \
  --approval-review-id <github-review-id> --kind <issue|milestone> --id <id> \
  --approved-head <approved-sha> --strategy <squash|merge|rebase> \
  --authorization-receipt .pm/receipts/merge/<kind>-<id>-authorization.json \
  --receipt .pm/receipts/merge/<kind>-<id>.json
```

The stable receipt schemas `publish-management-artifacts/v1`, `resolve-pr-authorization/v1`, and
`resolve-pr-merge-receipt/v1` are protocol identifiers for existing records; they do not denote
separate skills. The merge worker never reviews, repairs, marks done, or performs unrelated cleanup.
