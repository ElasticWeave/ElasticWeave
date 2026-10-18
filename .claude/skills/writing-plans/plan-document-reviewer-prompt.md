# Plan Document Reviewer Prompt Template

Use this template to dispatch one bounded read-only native reviewer whose provider differs from the
plan writer. Resolve the current provider route before dispatch; do not hardcode Codex or Claude,
rely on a deleted gate/hook, or invoke a legacy harness.

**Purpose:** Verify the plan is complete, matches the spec, and has proper task decomposition.

**Dispatch after:** The complete plan is written.

```
Reviewer (configured different-provider native reviewer):
  description: "Review plan document"
  prompt: |
    You are a plan document reviewer. Verify this plan is complete and ready for implementation.

    **Plan to review:** [PLAN_FILE_PATH]
    **Spec for reference:** [SPEC_FILE_PATH]

    ## What to Check

    | Category | What to Look For |
    |----------|------------------|
    | Completeness | TODOs, placeholders, incomplete tasks, missing steps |
    | Spec Alignment | Plan covers spec requirements, no major scope creep |
    | Task Decomposition | Tasks have clear boundaries, steps are actionable |
    | Buildability | Could an engineer follow this plan without getting stuck? |

    ## Calibration

    **Only flag issues that would cause real problems during implementation.**
    An implementer building the wrong thing or getting stuck is an issue.
    Minor wording, stylistic preferences, and "nice to have" suggestions are not.

    Approve unless there are serious gaps — missing requirements from the spec,
    contradictory steps, placeholder content, or tasks so vague they can't be acted on.

    ## Output Format

    ## Plan Review

    **Status:** APPROVE | REVISE

    **Issues (if any):**
    - [Task X, Step Y]: [specific issue] - [why it matters for implementation]

    **Recommendations (advisory, do not block approval):**
    - [suggestions for improvement]
```

**Reviewer returns:** `APPROVE | REVISE`, Issues (if any), Recommendations. This is a nonterminal
planning review, not the assignment's terminal completion verdict. If asked to review completion,
use the terminal reviewer contract instead: `COMPLETE | HUMAN_BLOCKED | REVISE` over fresh evidence.
