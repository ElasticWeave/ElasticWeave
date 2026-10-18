Perform the bounded post-round self-evolution step using the loaded `self-improve` and `memory`
contracts. Work from first-hand evidence rather than builder narration.

TASK CONTEXT
{{task}}

LATEST REVIEW FEEDBACK
{{reviewFeedback}}

LAST REVIEW NODE
{{lastReviewNode}}

CURRENT EVOLUTION INDEX
{{evolveCount}}

EVOLUTION RETRY ALREADY USED
{{evolutionRetryUsed}}

Inspect the current run's built-in subagent evidence under `workflow-output/subagent/standard-round/`,
including event and native-call journals, response receipts, mechanical evidence, and the actual
workspace diff. Also inspect relevant project reports, evaluation traces, source-layer memory, and
existing opt-in lemmas. Do not use another harness's runtime state, and do not infer prior
activations from provider prose.

Choose exactly one outcome allowed by `self-improve`: `NONE`, `REPAIR`, or one lemma status. Most
rounds should choose `NONE`. Do not change generalized behavior without the evidence level required
by the loaded skill. Deduplicate before writing and create or update at most one reusable artifact.

Set `/evolveNotes` to one short statement of what changed and why, or literal `NONE`. Set
`/lemmaReport` to a JSON string that satisfies the chosen `self-improve` outcome contract. A
minimal `NONE` report is:

{"outcome":"NONE","behaviorChanged":false,"artifacts":[],"reason":"why no durable behavior change is warranted"}

The controller appends the exact WorkflowActivationOutput state-patch contract; follow it exactly.
