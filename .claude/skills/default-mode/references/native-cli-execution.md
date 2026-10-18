# Native Agent CLI Execution

This contract applies to a real agent turn launched through `claude`, `codex`,
`cursor-agent`, `kimi`, or a Humanize-backed model turn. It does not apply to
short deterministic probes, Git reads, cleanup, or process-drain operations.

## Timeout

Give every native agent CLI turn a `3600` second timeout. Do not wrap a Fable,
Opus, Codex, or Cursor turn in a shorter ad-hoc timeout such as `180` seconds.
The built-in native runner's `stallSeconds` is the no-output/heartbeat bound;
the workflow node deadline must also leave at least 3600 seconds for a native
agent turn. A stream that is still producing valid heartbeats may run longer
only when the owning workflow explicitly declares a larger outer budget.

## Observation windows and independent branches

An observation window is only the coordinator's wait/poll interval. It never shortens a native
turn's 3600-second deadline, changes a workflow's declared outer budget, or converts silence into a
terminal result. On observation timeout, retain the exact handle as pending, emit
`observation_timeout`, and poll again. Classify a stall only through the declared heartbeat/no-output
rule; then use the recovery policy below. Do not start a duplicate attempt while the original handle
is live or its side effects are unreconciled.

If the declared process deadline expires, stop and drain that attempt, reconcile its side effects,
and route the classified failure through the existing recovery policy. Deadline expiry is never a
successful result or permission to end the enclosing task.

A permanent failure settles only that worker's attempt as unresolved. Record its classification and
route its dependent work according to the task graph, but do not cancel, pause, or final-return from
unrelated running or ready workers. Their evidence, reviews, and required fan-in remain pending. A
parent may stop the whole goal only through explicit user cancellation/replacement, or after the
ordinary reviewed human-block/completion conditions are met.

## Recovery

Use one initial attempt and at most four retries. The waits after failed
attempts are exactly:

`300s -> 600s -> 1800s -> 3600s`

Session-capable harnesses resume the same session. A provider without a session
profile must still declare the timeout and backoff policy and starts a fresh
turn for each retry; it must not be silently left unbounded.

Retry only a transient API/service/transport failure, rate-limit or 5xx
response, heartbeat stall, or an otherwise unexpected non-zero native process
exit. Preserve the existing session when the harness supports resume, and
reset any pending dispatch authorization before retrying.

Stop that attempt immediately for authentication or permission failure, quota exhaustion or
insufficient credits, invalid request or unsupported model, policy refusal,
malformed structured output, evidence/receipt failure, explicit cancellation,
or a repeated failure after the fourth retry. A provider may explicitly classify
a bounded usage-limit window as transient; only that audited adapter-specific
classification may retry it, never a generic quota message. Report the final
classification and the retry history; never loop indefinitely or silently
downgrade the model.

## Reasoning effort resolution

Prefer `max`, but treat it as a provider-local capability rather than a universal spelling. If the
fresh authenticated catalog for the exact selected model/backend has no `max`, select its highest
available declared effort before dispatch. Pin that actual effort in the project role or mapping,
mark an adaptive Cursor route with `effortPolicy: highest-available`, and require the canonical
resolver to prove no higher declared effort is present. The launch receipt records the actual
effort and concrete catalog model id.

This is not a retry fallback: do not launch with `max`, catch an error, and then lower effort. Do
not change model, backend, provider, account, or effort after the call begins. When a task's
acceptance contract explicitly requires literal `max`, absence of `max` remains unresolved.

## GPT-6 Astra consultation

`gpt-6-astra` is an optional, deliberately slow, high-quality review and advice route. Use it for
consequential planning, difficult result analysis, or a final consistency pass when the fresh
authenticated catalog admits the exact model. Request `xhigh` reasoning and keep the full 3600
second native-turn budget; extended deliberation is expected and is not a stall or failure by
itself. This is bounded non-terminal advice unless the task's review contract explicitly names
Astra. If Astra or `xhigh` is unavailable, report the route as unresolved and do not silently
substitute another model, backend, account, or effort.

## Simplify decision

At each task boundary, the top session decides whether a Simplify -> Verify
pass is warranted. Task type alone is not a trigger: ordinary code, algorithm,
workflow, configuration, multi-file, or externally visible work may record
`not_required`. Use `required` only for observed redundancy or unnecessary
abstraction, speculative fallback/recovery, mock or synthetic scaffolding,
repeated failed revisions, a high-impact/irreversible/security-sensitive
change, weakly measurable consequential logic, or an explicit user or
acceptance requirement. Reassess after candidate evidence and before
presentation. Focused tests, code review, alignment review, and terminal review
remain independent requirements. In the active agent
instruction brief (`AGENTS.md`/`CLAUDE.md` managed block or bounded worker brief), record
exactly these fields, and mirror them in the PM task record:

`SIMPLIFY_VERIFY_DECISION: required|not_required`

`SIMPLIFY_VERIFY_REASON: <one concrete sentence>`

For example:

`Simplify -> Verify: required -- observed speculative fallback branches obscure acceptance behavior.`

When required, run the bounded loop from
`review-and-simplify-verify.md`; do not manufacture mock or smoke evidence.
