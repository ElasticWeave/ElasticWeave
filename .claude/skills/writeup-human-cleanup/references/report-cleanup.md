# Report Cleanup Pattern

## Goal

A report should change or confirm a decision.

It is not a tour of everything the model found.

## Opening pattern

Use this shape:

```markdown
# Title that states the conclusion

One-paragraph answer. Include the decision, the evidence, and the caveat.

## Recommendation

What to do next.

## Evidence

The few facts that move the decision.

## Risks and caveats

What could make the conclusion wrong.

## Detail

Analysis by topic.

## Appendix

Method, raw data, long tables, prompts, source list.
```

## Report cleanup rules

- Lead with the answer, not the research journey.
- Replace “this report explores” with the conclusion.
- Separate evidence from interpretation.
- Move background down unless the reader needs it first.
- Put numbers near the claim they support.
- Measure direct fact yield on the first screen and two load-bearing sections; treat it as a
  diagnostic, not a license to delete caveats.
- Replace decorative metaphors with the measured relationship. Keep established domain terms when
  they reduce reading effort.
- Keep caveats visible.
- Use bullets for decisions and numbers, not for long explanations.
- Do not hide weak evidence under confident prose.

## Executive summary

Bad:

> This report provides a comprehensive overview of the current landscape and explores key considerations for future strategy.

Better:

> Enterprise renewals are stable, but expansion revenue depends on two accounts. Fund the onboarding team first; a pricing change can wait until Q4.

## Evidence language

Use:

- “The data shows…” only when the data directly shows it.
- “This suggests…” when inference is involved.
- “We do not know…” when evidence is missing.
- “The decision depends on…” when caveats matter.

Avoid:

- “clearly”;
- “undeniably”;
- “it is important to note”;
- “as mentioned earlier”;
- “the broader implications are significant.”

## Tables

Use tables for numbers, labels, decisions, or short phrases. Do not put long prose in tables.

For charts and diagrams, use a finding-led title and make the comparison, units, conditions,
uncertainty, and text equivalent recoverable without a narrative detour. A large table of raw
values is not a high-density visual when the reader cannot locate the conclusion.
