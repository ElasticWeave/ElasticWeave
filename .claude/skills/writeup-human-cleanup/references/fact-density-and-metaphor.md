# Fact Density and Metaphor Audit

Use this reference for substantial prose, research reports, and visual explainers. The objective is
high decision-relevant information per reading effort, not minimum length.

## Contents

1. Count direct fact units
2. Normalize length without gaming it
3. Audit metaphor cost
4. Protect readability under compression
5. Audit visuals by recoverable facts
6. Reviewer receipt

## 1. Count direct fact units

A **direct fact unit** (DFU) is one source-supported proposition that a reader can check
independently. It names enough of the subject, relationship, condition, value, or outcome to stand
without a metaphorical translation.

Count as one DFU:

- a dated event with an actor and action;
- a component and its stated specification;
- a measured comparison with condition, direction, and value;
- a supported mechanism linking cause and effect;
- a limitation, uncertainty, or negative result that changes interpretation.

Do not count:

- an adjective such as “important,” “robust,” or “transformative”;
- the same claim repeated in a summary, heading, and conclusion;
- a number with no subject, unit, baseline, or relationship;
- a citation that does not state what the source supports;
- an unsupported inference;
- terminology whose meaning the intended reader cannot recover locally.

A sentence may carry several DFUs. Split it when the facts use different evidence or qualifiers, or
when compression makes the relationships hard to recover.

## 2. Normalize length without gaming it

Use the metric only to compare a passage with its rewrite in the same language and register.

- Chinese, Japanese, or Korean prose: `10 × DFU / content characters` (DFUs per 10 content
  characters).
- Space-delimited prose: `DFU / 50 content words`.
- Exclude whitespace, punctuation, citation syntax, code, equations, and table borders from the
  denominator. Do not exclude necessary caveats or definitions.

Report the raw values as well as the ratio: `7 DFUs / 142 content characters = 0.49 per 10`.
Counting is a reviewer aid, not an acceptance gate. A higher ratio fails if the rewrite loses a
caveat, changes a claim, or becomes slower to understand.

Example:

> Apple released the iPod in 2001 with a Toshiba 1.8-inch hard drive, 5 GB capacity, a Click Wheel,
> and FireWire.

This short sentence carries five recoverable facts. “The iPod was a visionary milestone that
transformed how people experienced music” is longer but contributes no supported specification or
measured effect. Do not count “visionary,” “milestone,” or “transformed” as facts without evidence.

For a long document, sample the first screen and two load-bearing sections rather than claiming
false precision for the entire artifact. Compare like with like: prose with prose, table with table,
and one visual panel with its replacement.

## 3. Audit metaphor cost

For each candidate metaphor, ask:

1. Is it an established domain term for this audience?
2. Is it defined once and used consistently?
3. Is it more precise or materially shorter than literal wording?
4. Does it preserve the claim's actor, measurement, condition, and uncertainty?

Keep it only when all applicable answers are yes. Remove decorative drama and changing metaphor
families. Common costly patterns include results that “survive,” evidence that “fights back,” costs
that “eat” benefits, conclusions that “land,” and systems that “unlock” outcomes.

Examples:

- “The lemma survived both attacks.” → “Neither falsification test rejected the lemma.”
- “The evidence pushed the instruction back.” → “The measured result contradicted the
  instruction.”
- “The synchronization tax ate the gain.” → “Added synchronization cost exceeded the measured
  gain.”

Keep literal technical terms such as `pipeline`, `bottleneck`, or `tree` when they are conventional
and unambiguous in the domain. Do not replace a precise technical term merely because it began as a
metaphor.

## 4. Protect readability under compression

Reject a denser rewrite when it introduces any of these:

- stacked nouns or undefined acronyms;
- several numbers whose baselines or units are unclear;
- a qualifier that can attach to more than one claim;
- tiny labels or a table too wide to scan;
- removal of uncertainty, negative evidence, or source boundaries;
- claims that require the reader to search another section for their subject.

Use short literal clauses, local definitions, tables for repeated fields, and appendices for raw
detail. Density comes from deleting interpretation overhead and repetition, not deleting evidence.

## 5. Audit visuals by recoverable facts

A visual earns space when it makes relationships easier to recover than prose or a compact table.
For each panel, record:

- the question it answers;
- the direct takeaway;
- comparison or baseline;
- labels and units;
- experimental or observational conditions;
- uncertainty or evidence status;
- a text equivalent for accessibility.

Count a visual fact only when the reader can recover the relationship from the visual or adjacent
caption without first reading a narrative explanation. A title such as “Case 4” identifies a panel
but communicates no result; prefer “Per-group signaling lost 1.10–1.70% under bs-4096.” Remove
decorative shapes, repeated legends, and panels whose only function is to restate prose. Do not
increase “visual density” by shrinking text or placing every raw number in one figure.

## 6. Reviewer receipt

For substantial rewrites, return this compact audit with the edited artifact:

```text
reader/job:
sampled passages or panels:
before: DFUs / normalized length; metaphor count
after:  DFUs / normalized length; metaphor count
metaphors retained and why:
facts, caveats, citations, or proof status lost: none | list
readability failures: none | list
visual findings: none | list
```

The count should be reproducible from the named passages. Do not report an unsampled document-wide
score.
