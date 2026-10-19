# Write-up Human Cleanup

A skill for cleaning AI-looking READMEs, reports, and technical write-ups into concise, source-faithful prose.

This is not a “make it sound casual” filter. It is a reader-first editing pass:

- keep the facts;
- cut the padding;
- put the answer near the top;
- replace generic claims with proof;
- maximize directly readable, source-supported facts per reading effort;
- remove decorative metaphors when literal wording is cheaper to understand;
- preserve code, numbers, links, citations, and caveats.

## Install

Copy this folder into your project's **git-tracked** skills directory:

```bash
mkdir -p .claude/skills
cp -r writeup-human-cleanup .claude/skills/
```

(For a one-off personal, non-repo use you *can* copy it to `~/.claude/skills/` instead — but that is
per-machine and lost on clone, so framework projects must keep skills under the repo's `.claude/skills/`.)

## Use

Ask for it naturally:

```text
Use writeup-human-cleanup on this README. Keep the API details and code blocks intact.
```

```text
Clean this report. Make it answer-first and less AI-sounding. Preserve citations.
```

```text
Rewrite this launch note so a busy founder would keep reading.
```

## What it changes

The skill targets patterns I found in GitHub changes that explicitly rewrite READMEs to be “less AI,” “no AI slop,” “plain human voice,” or “less AI-generated.” It also incorporates ideas from community anti-slop skills and tools such as `stop-slop`, `deslop`, and `ai-slop-cleaner`.

The distilled moves are simple:

- Delete generic openings and table-of-contents bloat.
- Put concrete proof in the first screen.
- Replace “robust / seamless / comprehensive” with actual behavior.
- Use active voice and name the actor.
- Break repeated three-part rhythms.
- Remove em-dash padding and performative one-liners.
- Audit representative passages for direct fact yield and metaphor-decoding cost.
- Make each important visual state its finding, comparison, units, conditions, and uncertainty.
- Keep caveats, non-goals, and limitations.
- Never rewrite code, commands, metrics, or citations unless asked.

## Files

```text
writeup-human-cleanup/
├── SKILL.md
├── README.md
├── INDEX.md
├── references/
│   ├── distilled-from-git-changes.md
│   ├── cleanup-rules.md
│   ├── readme-cleanup.md
│   ├── report-cleanup.md
│   ├── anti-slop-patterns.md
│   ├── fact-density-and-metaphor.md
│   ├── before-after-examples.md
│   └── scorecard.md
├── samples/
│   ├── ai-readme-before.md
│   ├── human-readme-after.md
│   ├── ai-report-before.md
│   └── human-report-after.md
├── test-prompts.json
└── validate_pack.py
```

## Validate

```bash
python3 validate_pack.py
```
