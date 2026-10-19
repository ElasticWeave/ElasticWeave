Adversarially review the implementation in this workspace. Stay read-only.

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

Read the diff and touched files yourself. Check that the task criteria are actually met, verification
claims correspond to real commands, the implementation is correct for all valid inputs under the
fixed specification, protected review/proof controls were not weakened or avoided, and changes remain in scope.

REVISE only for a concrete, nameable defect tied to a file or behavior. Do not manufacture a
finding merely to appear strict. PASS only when you would accept responsibility for the change as
implemented.

Return exactly one JSON object as the final non-empty line with keys `verdict` and `summary`.
`verdict` must be `PASS` or `REVISE`. Do not emit a state patch and do not modify files.
