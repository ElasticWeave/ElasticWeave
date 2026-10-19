# Before / After Examples

## README opening

Before:

```markdown
# DataFlow Pro

DataFlow Pro is a comprehensive, robust, and scalable data processing solution designed to empower modern teams to seamlessly navigate complex workflows. In today's fast-paced data landscape, organizations need tools that are not just powerful but also flexible.
```

After:

```markdown
# DataFlow Pro

DataFlow Pro validates CSV files, writes rejected rows to `errors.csv`, and uploads the clean rows to S3.

It is for teams that receive messy vendor files and need a repeatable import step before data reaches the warehouse.
```

Why it works:

- concrete verbs;
- named output files;
- named user;
- no generic market setup.

## README feature list

Before:

```markdown
## Features

- **Seamless Integration** — Easily connects with your existing tools.
- **Robust Processing** — Handles errors gracefully.
- **Scalable Architecture** — Built for growth.
```

After:

```markdown
## What it does

- Reads CSV files from a local folder or S3.
- Rejects rows with missing IDs, invalid dates, or duplicate keys.
- Writes an import report with row counts and errors.
```

## Report summary

Before:

```markdown
# Q2 Customer Health Report

This report provides a comprehensive analysis of customer health across multiple dimensions. It explores churn, activation, expansion, and engagement trends to surface actionable insights for stakeholders.
```

After:

```markdown
# Q2 churn risk is concentrated in accounts that missed onboarding

Churn did not rise across the base. It rose in accounts that skipped the second onboarding session. Prioritize onboarding recovery before changing pricing or packaging.
```

## Technical explanation

Before:

```markdown
The system is designed with a modular architecture that enables flexibility and maintainability while ensuring robust performance across use cases.
```

After:

```markdown
The service has three modules: parser, validator, and uploader. The parser has no network access. The uploader retries failed S3 requests and records each attempt in `upload_log`.
```

## Research result with metaphor overhead

Before:

```markdown
The lemma survived both attacks, but the synchronization tax ate the expected win. This negative
result is not an apology; it is a first-class conclusion.
```

After:

```markdown
Neither falsification test rejected the lemma. Added synchronization cost exceeded the measured
gain, so the experiment records a negative result.
```

Why it works:

- preserves three direct facts: two tests did not reject the lemma, synchronization cost exceeded
  the gain, and the result is negative;
- removes three metaphor mappings: “survived,” “tax ate,” and “not an apology”;
- keeps the epistemic status at “not rejected” rather than upgrading it to “proved.”

## Dense does not mean cramped

Bad compression:

```markdown
Cross-campaign resolved A/A-bounded rung inversion invalidates pooled ratios.
```

Better:

```markdown
The same comparison changed direction across two campaigns, even though each result cleared its
own A/A floor. Do not pool or rank ratios across campaigns.
```

The second version is longer but easier to verify: it names the comparison, condition, evidence
status, and consequence.
