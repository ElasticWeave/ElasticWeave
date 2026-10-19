# Learned cross-task reference template

Use this only for self-evolve-generated reusable documentation. Name the destination
`.claude/skills/<skill>/references/learned-<slug>.md`. Static upstream documentation and templates
do not use this wrapper.

```markdown
---
kind: cross-task-reference
source: self-evolve
status: supported
skill: example-skill
derived_from: [supported-lemma-id]
transfer:
  task_contexts: ["task://context-a", "task://context-b"]
  artifacts: ["artifact://context-a-observation", "artifact://context-b-observation"]
  independence: "Why the contexts do not share one project-specific explanation."
  project_identifiers_removed: true
  applicability: "Inputs and runtime conditions where this guidance should transfer."
  counterexamples: "Known negative or boundary case."
  falsifier: "Observation that would invalidate the guidance."
---

# Reusable guidance

State the supported, de-identified guidance. Link the lemma instead of copying its full reasoning.
```

Every `derived_from` id must resolve to an existing `supported` lemma, and that lemma must link this
reference path in `derived_changes`. Tentative synthesis stays in project `.memory`; do not create
`learned-*.md` as a staging document.
