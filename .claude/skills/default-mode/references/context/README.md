# Mode-specific context

`manifest.json` is the skill-owned context routing contract for the three startup modes. It stays
under `default-mode/references/context/`, outside native `*/SKILL.md` discovery, so a skills-only
installation carries the same contract without requiring project materialization.

The intersection of `default`, `fast`, and `naive` project skills is empty. Naive mode is isolated by
contract and receives only `naive-mode`; shared startup semantics are carried by the invocation
contract and the selected mode entrypoint.

A mode loads only its `bootstrap` entry; `core` is empty for all three modes. PM coordination and
planning remain lazy. Packs are routing indexes, not bulk-load bundles: consult a pack when its
`when` condition applies, then load only the exact skills required by the bounded task. Never inline
every skill in a pack. A worker prompt names exact required skills and on-demand skills with the
existing `<!-- workflow-skills/v1 ... -->` contract; the native runner materializes required skills
and leaves on-demand skills as explicit paths. This manifest does not change model routing,
authority, workflow admission, or terminal review.

Keep skill directories at the top level. A nested `SKILL.md` layout would break native discovery,
Codex/Kimi symlink synchronization, marketplace scanning, and bundle validation.
