Review the self-evolution edits made in this round. Stay read-only and treat the model report as an
untrusted claim.

SELF-EVOLUTION NOTES
{{evolveNotes}}

LEMMA REPORT
{{lemmaReport}}

DETERMINISTIC LEMMA VALIDATION
{{lemmaValidation}}

EVOLUTION RETRY ALREADY USED
{{evolutionRetryUsed}}

Read the actual diff and cited run artifacts. Require deterministic validation with `ok: true`.
Check that evidence comes from this round; lemma predictions precede interventions; probes
distinguish plausible alternatives; stochastic claims use multiple runs with noise handling;
scope and falsifiers prevent unsupported generalization; and refuted or superseded evidence is
retained. `NONE` must not change durable behavior. `REPAIR` must reproduce a direct defect and cite
discriminating failing and passing probes. Only a supported lemma may introduce generalized new
behavior. Reject weakened gates, review rules, protected paths, acceptance, metrics, or evidence.

APPROVE is valid when no durable change was warranted and the run left only allowed memory residue.
REVISE with one consolidated list of exact fixes otherwise.

Return exactly one JSON object as the final non-empty line with keys `verdict` and `summary`.
`verdict` must be `APPROVE` or `REVISE`. Do not emit a state patch and do not modify files.
