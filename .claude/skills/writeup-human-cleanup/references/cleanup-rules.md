# Cleanup Rules

## The rewrite contract

A cleanup pass must preserve the factual contract of the original.

Keep:

- technical names;
- command syntax;
- API names;
- version numbers;
- measurements;
- citations;
- links;
- caveats;
- license and attribution.

Change:

- order;
- emphasis;
- redundancy;
- vague language;
- over-explaining;
- generic claims;
- AI-shaped rhythm.

Flag instead of rewriting when a claim is unsupported.

## The four-pass method

### Pass 1: Reader task

Write one sentence that names the reader and their job.

Examples:

- Developer deciding whether to install this library.
- VP deciding whether the report supports a budget change.
- Reviewer checking whether a PR changed behavior.

### Pass 2: Load-bearing content

Mark each paragraph as one of:

- keep;
- merge;
- move to appendix;
- cut;
- needs source.

A paragraph is load-bearing when removing it would change the reader's decision or make the next step harder.

For a substantial draft, mark the direct fact units in the first screen and two load-bearing
sections. Use `fact-density-and-metaphor.md`; do not count repeated claims, unsupported adjectives,
or raw identifiers as facts.

### Pass 3: Human rewrite

Rewrite around facts, not around transitions.

Prefer literal relationships over decorative metaphors. Keep a conventional technical metaphor
only when it is defined, consistent, and cheaper for the intended reader than a literal paraphrase.

Before:

> This comprehensive tool empowers teams to seamlessly navigate data workflows.

After:

> The CLI validates the CSV, writes rejected rows to `errors.csv`, and uploads the clean file to S3.

### Pass 4: Fidelity audit

Check every strong claim against the source. If the source does not prove it, lower the claim.

- “best” → “includes”
- “guarantees” → “checks”
- “real-time” → name the actual interval
- “secure” → name the mechanism
- “fast” → give the benchmark or remove it

## Compression targets

For AI-looking drafts, aim for:

- 20–50% shorter for READMEs with repeated sections;
- 10–30% shorter for reports with useful evidence;
- no compression target for legal, safety, or scientific methods sections.

Do not cut examples, commands, or caveats just to hit a target.
Do not compress text into jargon piles just to raise a fact-per-character score. A longer sentence
is better when it makes the subject, comparison, unit, condition, or uncertainty recoverable.

## Human details that help

Use concrete details that prove the author has touched the thing:

- a real command;
- a known failure mode;
- a limitation;
- a small production anecdote;
- a non-goal;
- a compatibility edge case;
- a performance number with context.

Human writing often earns trust by admitting a boundary.

## What not to do

Do not:

- add slang for no reason;
- add jokes to serious reports;
- introduce first person if the document should be institutional;
- replace precise domain terms with plain but wrong terms;
- add typos or grammar errors;
- remove required disclosures;
- remove citations because they feel “academic.”
