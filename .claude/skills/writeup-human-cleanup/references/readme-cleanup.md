# README Cleanup Pattern

## Goal

A README should help a reader decide whether to use the project, then help them succeed with the first run.

## First screen

A strong README opening has:

1. One-line purpose.
2. One concrete constraint or proof point.
3. Install or quick start link.
4. A small example.
5. One honest limitation or scope boundary when relevant.

## Default README structure

```markdown
# Project Name

One sentence: what it does, for whom, and where it runs.

Why it exists. Keep this to one or two short paragraphs.

## Quick start

Commands.

## Example

Small working example.

## Common tasks

The tasks users actually do.

## How it works

Only after usage.

## Limits

Compatibility, non-goals, failure modes.

## Development

Build, test, release.

## License
```

## Common removals

Cut these unless the README is large enough to need them:

- table of contents;
- “features” list that repeats the intro;
- architecture diagram before usage;
- “project overview” that says the same thing as the first paragraph;
- “comprehensive solution” wording;
- badges that do not help trust;
- AI collaboration notes unless required;
- screenshots that do not explain a task.

## Common replacements

- “Built for modern teams” → name the user and task.
- “Seamless integration” → show the integration code.
- “Robust error handling” → name the error path.
- “Scalable architecture” → name the scale limit or mechanism.
- “Easy to use” → show the smallest working command.
- “Production-ready” → name tests, deployments, or caveats.

## README voice

Use short paragraphs. Prefer plain verbs:

- reads;
- writes;
- validates;
- retries;
- rejects;
- exports;
- watches;
- uploads;
- signs;
- caches;
- logs.

Avoid inflated verbs:

- empowers;
- enables seamless;
- revolutionizes;
- leverages;
- unlocks;
- supercharges.
