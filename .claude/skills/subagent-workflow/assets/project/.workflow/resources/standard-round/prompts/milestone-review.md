Run an independent first-principles milestone review. Stay read-only and evaluate the work itself,
not only its execution.

TASK
{{task}}

FOCUSED CHECK ARGV (JSON; executed without a shell)
{{acceptanceArgv}}

BUILDER REPORT
{{report}}

MECHANICAL EVIDENCE PATH
{{evidencePath}}

MECHANICAL EVIDENCE DIGEST
{{evidenceDigest}}

Reconstruct the task, constraints, and criteria from first principles. Treat the focused check as
evidence rather than terminal authority. Determine whether
each substantive change advances that goal, whether a convention was mistaken for a constraint,
and whether unnecessary machinery creates correctness or maintenance risk. A simpler alternative
is a REVISE finding only when the current construction has a concrete defect, unsupported burden,
or scope violation. Do not reject sound work for not being theoretically minimal.

Return exactly one JSON object as the final non-empty line with keys `verdict` and `summary`.
`verdict` must be `APPROVE` or `REVISE`. Do not emit a state patch and do not modify files.
