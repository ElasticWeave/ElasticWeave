# Delivery workflow

Use this reference only when research must become a local memo, Linear artifact, Notion page, or
another external deliverable. Evidence planning lives in `source-strategy.md`.

## Default memo shape

1. Research question, horizon, and selected width
2. Answer and decision recommendation
3. Evidence that changes the decision
4. Claim coverage and contradictions
5. Risks, unknowns, and boundary conditions
6. Extrapolation, only when requested and explicitly labeled
7. Sources and method note

Include a compact ledger when it helps auditability:

| Claim | Type | Support | Quality | Link | Status |
|---|---|---|---|---|---|
| ... | Fact / Inference / Extrapolation | ... | primary / secondary | ... | supported / contradicted / gap |

Write in the user's working language unless asked otherwise. Apply `writeup-human-cleanup` after
the evidence and caveats are stable; never remove citations, uncertainty, or proof obligations for
style.

## Intake and scope

If the horizon, audience, destination, or authority to mutate an external system is unclear,
checkpoint the exact candidate human dependency and exhausted safe alternatives. A headless
workflow has no human node and its internal checkpoint never authorizes an ask or stop. Outside the
cold-start launching conversation, return the checkpoint to the inherited `terminalOwner`; only
that owner dispatches a fresh terminal reviewer; only its typed
`HUMAN_BLOCKED` result may authorize the exact human request through the controlling session or
configured UI. `REVISE` continues or redispatches work. Any
Linear/Notion/file or other durable/external mutation is executed by a bounded delivery worker; the
top only authorizes and inspects its receipt.

For Linear work, inspect the project, milestone, parent, relevant siblings, children, prior
comments, and attachments before treating the issue as standalone. Research unresolved leaves
before synthesizing a parent. Do not close a route master while required descendants remain open.

For Notion work, fetch the target page or database context before writing. Identify whether the
page is canonical or a derived view.

## Replay-safe publication

Create the local canonical artifact first and compute or record its version/hash. Derive a stable
delivery key from the research run, destination, and artifact version. Before every retry:

1. Read the destination and check whether that key or artifact version is already present.
2. Reuse or update the existing object instead of creating a duplicate.
3. After writing, read back the page, issue, comment, attachment, or link.
4. Record the remote object ID/URL and delivered artifact version in run state.

If a timeout makes success ambiguous, reconcile before retrying. Do not claim delivery merely
because an API call was sent.

## Linear delivery

1. Publish a durable canonical report as an attachment or document.
2. Attach the strongest external links directly to the issue when useful.
3. Add one compact summary comment pointing to the canonical artifact.
4. Read back the report/link/comment and record their IDs.
5. Close the issue only after the artifact and evidence are present, acceptance passes, required
   descendants are complete, and the user authorized closure.

If replacing a provisional parent synthesis, state that the new artifact supersedes it. A comment
alone is not a durable research report.

## Notion delivery

1. Create or update one canonical page rather than scattering the report across comments.
2. Preserve claim labels, links, dates, contradictions, and open gaps.
3. Reconcile by page ID and delivery key before retrying.
4. Use comments for review or follow-up, not as the primary artifact.

When both systems are requested, keep one canonical memo in the chosen durable destination and
link to it from the operational system. Do not maintain two silently divergent full copies.

## Close-out evidence

Before reporting success, confirm:

- the delivered artifact matches the accepted local artifact version;
- source links and claim labels survived publication;
- facts and extrapolations remain separate;
- unresolved contradictions and gaps remain visible;
- remote object IDs and read-back evidence are recorded;
- issue closure, if any, happened after artifact delivery and child completion.

If research is complete but requested delivery is unavailable, return a local artifact plus
`unresolved_delivery` with the reconciliation state and one next action. Do not redo the research.
