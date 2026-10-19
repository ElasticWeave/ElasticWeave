---
name: update-platform-config
description: "Propagate canonical skill, agent, gate, model, hook, or workflow changes to compatible platform surfaces; edit one owner and render the rest."
---
> **Model tier:** LOW - propagation is mechanical after the design decision is made.
>
> **Memory abstraction:** one canonical skill-owned template or project artifact; symlink when
> formats match, render when they differ, and never treat generated user configuration as source.

# update-platform-config - skill-owned sources, project-local state

The distributable repository owns skills and inert templates. `default-mode` materializes selected
project-local state without overwriting existing files. This skill synchronizes that state; it
manages only the marked general-agent policy block in root instruction files, never replaces a
repository's own instructions or installs a model CLI.

| Artifact | Package source | Project source after default-mode startup | Derived surfaces |
|---|---|---|---|
| Project session policy | `default-mode/references/project-session-contract.md` | its marked block in root instructions | root `AGENTS.md` and `CLAUDE.md`; text outside the markers remains repository-owned; an existing managed `fast` block is preserved |
| Skills | `.claude/skills/` | same installed skill tree | native discovery and compatible skill symlinks |
| Platform hooks/rules | `assets/project/` plus owning gate skills | selected project hook/rule files | Claude settings/hooks, Codex rules, Kimi hook config |
| Model roles and global reasoning policy | `default-mode/assets/project/config/workflows/models.json` as bootstrap only | `config/workflows/models.json` | native role launches and adapter route fields |
| Default agents | `write-subagent/assets/project/.omp/agents/` | `.omp/agents/*.md` | compatible Claude/Codex formats; incompatible providers omitted |
| Workflow intent | owning workflow skill assets | `.workflow/designs/*.workflow.json` | adapter mappings created after capability validation |
| Built-in controller | `subagent-workflow/scripts/` | executed from the installed skill | no copied root `tools/` tree |
| Trusted native Humanize scheduler/root/slot | `write-humanize-flow/assets/project/.humanize/` | `.humanize/run_scheduler.py`, `.humanize/flows/scheduler/__init__.py`, `.humanize/flows/default.py`, controlled `.humanize/flows/slot.py` link, and versioned slot files | repo-default TUI launcher plus explicitly validated native flows; never an implicit default process |
| Trusted research references | `deep-research-workflow/assets/project/.humanize/flows/` | `.humanize/flows/deep_research.py`, `.humanize/flows/auto_research.py`, shared helper | native reference implementations; domain research contract remains canonical |
| Neutral Humanize mappings | `humanize-workflow` generator | `.humanize/mappings/*.json` and generated flow | narrow capability-checked adapter output, not the native authoring lane |

## Model and reasoning contract

`config/workflows/models.json` is per-project runtime state, not package configuration. Resolve every
model id from live native catalogs and current vendor sources. The required semantic roles and one
global `reasoningPolicy` describe intelligence class, relative routing rank, effort envelope,
escalation trigger, and maximum role. Provider effort labels are local controls; never assume that
two families' `max` or `medium` values are equivalent. Use the cheapest role expected to pass the
leaf's acceptance check and escalate only the failing judgment leaf. Selected review must resolve to
a different provider from every writer it covers.

## Native catalog execution boundary

Fresh native catalog probes derive `HOME` and `PATH` from the running OS account, discard inherited
endpoint, code-loading, alternate config-root, and credential-root selectors, then restore only the
canonical `CODEX_HOME`, `XDG_CONFIG_HOME`, and `XDG_DATA_HOME` paths for that account. Other
backend-specific config overrides remain absent so they fall back to those canonical roots. The
bound executable's directory walk rejects foreign-owned and non-sticky world-writable parent
components. A group-writable component is admitted only when it is owned by the effective uid and
the OS account databases prove that group is private to that account: all passwd entries for its
primary gid and all explicit group members identify that same account. Missing or contradictory
account data fails closed; a trusted sticky world-writable parent remains accepted for the standard
temporary-directory case.

Native Claude catalog discovery uses the fixed audited `claude -p --input-format stream-json
--output-format stream-json --verbose` argv with exactly one fixed stream-JSON `control_request`
for `list_models`; it is a zero-conversation control query, not model inference. Its subprocess
environment removes caller Claude/Anthropic credential, account, endpoint, proxy/TLS, config, and
runtime-injection selectors, then restores only the canonical OS-account configuration root needed
for the authenticated native CLI. Never record credentials.

Cursor catalog launch does not execute the shell wrapper or its unbound `realpath`/`readlink`
helpers. It binds the logical wrapper, the sibling `node` runtime, `index.js`, and a sorted
manifest of every stable no-follow regular file in the installed version directory, then invokes
the bound absolute `node` plus absolute `index.js` with the fixed `--use-system-ca` flag and a
scrubbed environment. That environment removes caller Cursor credential, account, endpoint,
config-root, and runtime-injection selectors, then restores only the non-secret
`CURSOR_INVOKED_AS=cursor-agent` wrapper marker so the bound bundle sees the same invoked-as
identity as the installed wrapper. Canonical OS-account Cursor configuration remains the
credential store. Never record credentials.
The only excluded subtree is the exact `.running` runtime-state directory. Unexpected symlinks,
special files, missing required members, unsafe ownership/modes, or read-time identity drift fail
closed. After every real catalog subprocess exits, including nonzero exits, the executable and
full closure are re-resolved, re-hashed, and compared exactly, so a persistent target or chunk
swap fails. The full install digest is folded into `executionClosureSha256` so a chunk change
moves the catalog receipt even though the named closure still lists only the wrapper, node, and
`index.js`. A same-UID attacker who replaces bound files while the command runs and restores the
original bytes before that post-exit rehash is not detected; that swap-and-restore window is an
OS trust limit, not a closed control.

Native Claude Code agent generation emits only roles whose canonical `backend` is `claude` and
`provider` is `anthropic`. Cursor-hosted Claude-family roles remain available through Cursor and
subagent surfaces and are never collapsed onto native Claude aliases.

## Procedure

1. Identify the canonical owner. Edit a skill template for distributable behavior; edit project
   state only for this repository's roles, constraints, or workflow design. Never hand-edit a
   generated native target and expect it to persist.
2. When gate semantics change, update the skill-owned TypeScript and Python implementations in the
   same change, materialize them into a temporary project, and run both behavioral suites. Keep
   path authorization, authoritative proof protection, and legal workflow command grammar
   equivalent. `.pm/accept.sh`, `.pm/immutable`, and `.pm/verify_base` are not generated or
   protected runtime dependencies. Keep `.pm/reviews/`, `.pm/verdicts/`, `.pm/proofs/`, and
   `.pm/workflow-reviews/` protected and covered by the post-bash proof audit on both ports;
   `.pm/evidence/` and `.pm/review-briefs/` remain writable inputs. The Python port's pre/post
   proof hand-off is state carried between two hook invocations, not disposable scratch data: keep
   it in one fixed, private, account-scoped runtime location (never the shared system temp
   directory or a location derived from caller-controlled payload fields), written by exclusive
   randomized creation plus atomic rename, and read back only after strict regular-file/
   ownership/mode/no-symlink checks on the open file descriptor.
3. When roles, skills, agents, or platform templates change, run:
   ```bash
   python3 .claude/skills/update-platform-config/scripts/config-sync/sync_all.py
   ```
   The script reads the current target project root, loads and validates the exact canonical model
   and agent source bytes once, and asks every enabled renderer for a read-only plan before the
   first mutation. It rejects duplicate targets and any target, source, model, parent, ownership,
   or marker conflict during aggregate preflight. One shared transaction then holds no-follow
   directory descriptors, stages every regular file and symlink in its target directory, and
   revalidates guards and preimages around publication. Existing entries are moved to
   same-directory recovery names and desired/restored entries are installed only with Linux
   `renameat2(RENAME_NOREPLACE)`; a concurrent name wins and is preserved instead of being
   overwritten. Recovery entries remain until every action and final validation succeeds, and
   published files and directories are `fsync`ed. A caught failure rolls back in reverse order to
   exact prior bytes, modes, and link targets and move-validates transaction-created directories
   before removing them. A concurrent target prevents restoration, retains the recovery entry, and
   is reported distinctly as an incomplete rollback. Direct Codex and Claude entry points also bind
   the exact model-policy bytes used for rendering before committing one-surface plans.

   This is process-lifetime rollback, not crash atomicity. Abrupt process death, power loss, or a
   filesystem failure can interrupt the transaction because there is no durable recovery journal;
   do not claim recovery across that boundary. This is rollback, not isolation: a reader can observe
   the brief interval between a recoverable move-aside and no-replace publication. Held descriptors,
   random private names, and repeated identity checks detect ordinary concurrent replacements, but
   they are not a security boundary against an uncooperative same-UID process that deliberately
   targets a private name between the final identity check and cleanup syscall. Within the stated
   boundary, the instruction merge preserves bytes outside its markers and preserves the already
   selected managed `default` or `fast` startup mode. If the two root instruction targets select
   different modes, preflight fails before any write; a project with no selected mode uses the
   canonical `default` section. The merge fails closed on symlinks or malformed/duplicate markers.
   Do not duplicate the policy text in this skill. The sync must not overwrite unrelated existing
   instructions, write secrets, or install a harness. User-level Codex/Kimi writes require the
   explicit `GENERAL_AGENT_ALLOW_USER_CONFIG_WRITE=1` opt-in. That switch authorizes an attempt, not
   ownership: Codex may replace or remove only exact names in its last valid sync manifest, and
   must fail before any mutation when a skill, agent, rule, manifest, or parent path collides with
   an unowned object. Kimi preserves all bytes outside one balanced managed block. Both clients
   reject symlink/special-file destinations and publish complete regular files through recoverable,
   no-overwrite renames; a caught interrupted update restores prior user bytes, and the Codex
   manifest is published only after every planned target update succeeds. Ordinary sync also retires the two removed
   OMH-era pre-hooks and the superseded single-file `.humanize/flows/scheduler.py` only when their
   bytes exactly match the pinned package-owned versions. Removing that exact old Scheduler lets
   the audited `scheduler/__init__.py` directory flow become the sole local discovery entry after
   an upgrade. Sync preflights the complete retirement set with no-follow metadata before derived
   writes, rejects modified files, links, special files, or linked parent paths, and never removes
   parent directories.
4. Validate neutral workflow designs before mapping. Regenerate built-in mappings and their mapping
   reports from the design and owning skill assets; a mapping never becomes canonical intent.
5. Treat materialized `.humanize/flows/*.py` as project code. Ordinary config sync does not import,
   execute, or silently replace it. Use `write-humanize-flow` for trusted native source and
   `humanize-workflow` for derived neutral mappings; installing either skill asset does not start
   Humanize.
6. Run focused validators, inspect the generated diff, and use the issue's selected review policy.

## Boundaries

- Existing user configuration is bootstrap input, never package state.
- The project session policy has one canonical owner under `default-mode`; this skill owns only its
  platform merge mechanism.
- A missing, malformed, stale, unavailable, or same-provider review route fails closed.
- Codex lacks a general in-harness hook system; exact rules plus controller audits and independent
  diff review cover the selected project state. Do not claim stronger enforcement.
- Kimi post-tool hooks are observational; authoritative proof commitments and controller
  finalization remain the structural controls.
