# Source strategy

Use this reference to plan evidence, choose tools, and normalize branch outputs. Delivery rules live
in `delivery-workflow.md`.

## Contents

- Source quality ladder
- Failure-first discovery
- Tool routing
- Evidence record
- Planning breadth
- Contradictions and gaps
- Coverage test

## Source quality ladder

Prefer sources in this order, adjusted for the claim:

1. Official specifications, product documentation, public datasets, and regulatory records
2. Standards bodies and primary technical documentation
3. Original papers, proceedings pages, preprints, and author repositories
4. Official company disclosures, engineering blogs, and release material
5. Reputable secondary reporting and technical analysis
6. Teardowns, analyst estimates, forums, and aggregations

Use lower-ranked sources when primary evidence does not exist, when triangulating an uncertain
claim, or when searching for counterevidence. Label their limits. Popularity, citation count, and
search rank select what to inspect; they do not establish truth.

## Failure-first discovery

Before a broad survey, map the current idea or decision to its ranked critical assumptions. Search
the most uncertain blocker with the greatest downstream invalidation first, even when another
question is easier to answer. Cost selects among valid ways to test that assumption; it does not
select a more certain assumption.

Use targeted discovery terms and source classes for:

- failed replications, null results, negative results, retractions, and known non-working regimes;
- limitations, counterexamples, impossibility results, lower bounds, and adverse boundary cases;
- benchmark contamination, leakage, invalid proxies, measurement error, and weak baselines;
- scaling cliffs, sample/compute complexity, integration failures, and operational constraints;
- safety, compliance, licensing, authority, or resource conditions that can block deployment;
- alternate mechanisms that predict the same observed result.

Bind each result to an assumption ID, scope, falsifier, and decision impact. A search result that is
negative only outside the user's required regime narrows a boundary; it does not refute the idea.
Do not pad the attack ledger with easy background facts while a load-bearing uncertainty remains
untested.

## Tool routing

- Use web search for current facts, products, standards, prices, roadmaps, public repositories, and
  source discovery. Open the supporting page; do not cite the search result.
- Use PDF extraction or page inspection when a paper, standard, report, table, footnote, or exact
  statement is the canonical evidence.
- Use arXiv, OpenReview, proceedings, and author repositories for primary research. Prefer the
  paper itself over summaries.
- Use official documentation tooling for SDK, API, library, and framework behavior.
- Read Linear or Notion first when internal decisions, prior work, or issue structure constrain the
  question; mark internal interpretation separately from external evidence.
- Generate an image only when a diagram materially clarifies the synthesis. When inserting an
  arXiv figure into Notion, inspect the paper HTML for the figure filename and use
  `https://arxiv.org/html/{paper-index-including-version}/{picture-index}.png`.

Always browse live for facts likely to drift. Record the retrieval date for versions, prices,
roadmaps, stars, citation counts, and current market data.

## Evidence record

Each branch emits structured records rather than prose summaries:

```json
{
  "claimId": "C-01",
  "statement": "...",
  "claimType": "Fact",
  "sourceUrl": "https://...",
  "sourceClass": "official-doc",
  "publishedOrUpdatedAt": "YYYY-MM-DD or unknown",
  "retrievedAt": "YYYY-MM-DD",
  "supportPointer": "section, page, table, or paragraph",
  "supportSummary": "...",
  "quality": "primary",
  "uncertainty": "...",
  "contradictionKey": null
}
```

Allowed claim types:

- `Fact`: directly supported by the cited source.
- `Inference`: derived from named facts; state the reasoning chain.
- `Extrapolation`: future-oriented projection; state the trend, horizon, and falsifier.

Discovery snippets are leads, not evidence records. Keep short support summaries and pointers in
shared state; store full extracts as artifacts.

## Planning breadth

Use source amounts as defaults for search planning, not acceptance thresholds:

| Width | Typical unique strong sources | Required mix |
|---|---:|---|
| `quick` | 3–6 | enough primary material to establish terminology and the current baseline |
| `standard` | 10–18 | primary sources across the main claim dimensions plus relevant contrary evidence |
| `broad` | 20–36 | frontier/opportunity sources, limitations, failed or contrary findings, and adjacent mechanisms |

Some narrow questions need fewer sources; some contested questions need more. Stop only when the
claim matrix shows adequate coverage and quality, not when a number is reached.

These breadth targets apply after the falsification gate permits continuation. The bounded attack
search may use fewer sources because its acceptance test is coverage of the ranked kill set, not a
balanced literature survey.

For a decision-driving claim, seek at least one direct primary source when one exists. Triangulate
high-impact, controversial, or conflict-prone claims with independent evidence proportional to the
risk. If strong evidence does not exist, keep the uncertainty in the conclusion.

## Contradictions and gaps

When sources conflict:

1. Keep both records and assign a shared contradiction key.
2. Check scope, date, population, measurement, definitions, incentives, and source quality.
3. State which record carries more weight and why.
4. Route decision-relevant unresolved conflict to targeted refill, reframing, wider research, or
   `unresolved_with_gaps`.

Do not average incompatible numbers or choose the more convenient source. A missing primary source,
stale evidence, unsupported claim dimension, or unresolved proof obligation is a named gap.

## Coverage test

The evidence set is ready for synthesis only when:

- every material claim dimension maps to adequate evidence or a visible gap;
- drift-prone claims were checked at the required date;
- source quality matches claim impact;
- material counterevidence and boundary cases were sought;
- contradictions are resolved or carried into the decision;
- facts, inferences, and extrapolations remain distinguishable.
