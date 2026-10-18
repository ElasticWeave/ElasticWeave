# Community Evidence: Exploration, Local Optima, and Workflow Costs

Research date: 2026-07-30. This document uses only papers, official proceedings pages, author
repositories, or official documentation as technical evidence. Effect sizes reported in papers are
results under the original research settings and must not be treated directly as benefits to this
project.

## Conclusion

**Fact:** There is no unified "idea board" standard among public systems, but several lines of work
have developed complementary mechanisms:

- AIDE uses a solution tree to preserve candidates, branches, and incremental improvements;
- ADAS uses an archive to preserve agent designs that have already been discovered and evaluated,
  and requires new designs to be novel;
- PACEvolve explicitly separates macro ideas from micro hypotheses, using a persistent idea pool,
  conceptual deduplication, a cap on active threads, retention of failures, and momentum-based
  backtracking;
- AgentRevive uses `Active / Standby / Terminated` to prevent agents with temporary failures from
  being removed prematurely;
- Agent K uses positive and negative experience stores, active task selection, and failure cooldowns
  to schedule subsequent experiments within a finite token budget.

**Inference:** Among currently available public materials, PACEvolve is the design closest to the
"idea board" discussed in the meeting, but this does not establish that the two share an origin. An
implementation suited to this project should combine the mechanisms above into a project-local,
auditable board rather than introduce a product's hidden runtime.

**Proposal:** Workflow optimization should use two layers of state: an idea represents a direction
for a mechanism, while a trial represents one falsifiable experiment. `UNDEREXPLORED/EXPLORING/STANDBY`
must be nonterminal states; successful exit must check both quality and exploration coverage; cost
optimization should use total cost per accepted result.

## 1. Early Stopping and Underexploration

### MLE-bench and AIDE

**Fact:** Agent evaluation records from [MLE-bench](https://arxiv.org/pdf/2410.07095) show that MLAB
and OpenHands often stopped within the first few minutes even when instructed to work for 24 hours;
AIDE continued requesting improvements until it reached either 24 hours or 500 solution-tree nodes.
The paper also reports that more independent attempts increased the medal rate, but marginal returns
slowed when runs were extended to 100 hours. Systems generally do not manage time and compute
resources well.

**Fact:** The [AIDE paper](https://arxiv.org/pdf/2502.13138) represents each candidate solution as a
tree node and each edge as an improvement. Its strategy first generates diverse initial solutions,
then repairs nodes with errors and continues improving promising nodes. The paper uses tree summaries
instead of repeatedly putting the full history into the prompt. The [official documentation](https://docs.weco.ai/)
also provides a tree view, branching from any node, natural-language injection of new ideas, fanout
comparisons, and experiment traceability.

**Inference:** "Continue revising the incumbent" and "continue exploring" are not the same thing. A
workflow must record uncovered directions and allocate budget to them; otherwise, extending the total
runtime merely gives the same path more opportunities.

### Soft State Rather Than One-Shot Elimination

**Fact:** [AgentRevive](https://aclanthology.org/2026.acl-long.373/) points out that aggressive pruning
can discard agents that still have value because of temporary states or intermittent errors. Its
state machine retains `Standby`, which permits later recovery, as distinct from irreversible
`Terminated`.

**Proposal:** A single failure, timeout, cheap-proxy failure, or trial reaching its cap may only leave
an idea in `EXPLORING` or move it to `STANDBY`. An idea may enter `EXHAUSTED` only after an explicit
falsifier or after satisfying a predeclared minimum budget for diverse evidence.

## 2. Huawei Agent K's Method

**Fact:** The [Agent K v1 PDF](https://arxiv.org/pdf/2411.03562v1), submitted on 2024-11-05, lists
author affiliations including Huawei Noah’s Ark Lab, UCL, and TU Darmstadt. The v1 title is *Agent K
v1.0: Large Language Models Orchestrating Structured Reasoning Achieve Kaggle Grandmaster Level*. It
is not a single fixed pipeline. Its primary mechanisms are:

1. Working/short-term memory supports nested reasoning and experimentation;
2. Successful and failed trajectories are retained separately as long-term `RAG+` and `RAG-`
   experience;
3. The next task is selected actively based on historical experience, balancing positive similarity,
   negative experience, difficulty, and cooldown after recent failures;
4. External optimization tools perform numerical work such as hyperparameter search and blending,
   instead of requiring the LLM to calculate it manually;
5. Different task-selection strategies are compared under a token budget. Figure 14 in the paper
   shows that active selection with memory outperforms heuristic, random, and no-memory methods within
   the same output-token range; this is an experimental result from the paper, not a universal
   guarantee for all tasks.

**Fact:** The 92.5% success rate and 6 gold / 3 silver / 7 bronze results reported in v1 were obtained
under the 2024 version's setup. The current [arXiv record](https://arxiv.org/abs/2411.03562) was updated
to v3 in 2025, with a changed title, task scale, and results. Citations must pin the version and must
not mix the v1 method description with v3 figures.

**Inference:** The most valuable parts of Agent K for the current problem are not its medal counts,
but three search strategies: failures also enter long-term memory; recent failures produce only a
cooldown, not permanent deletion; and task/idea selection considers similarity, difficulty, and
historical returns together.

## 3. Escaping Local Optima

### Hierarchical Idea Pool and Backtracking

**Fact:** [PACEvolve](https://arxiv.org/pdf/2601.10657) characterizes the problems of long-running
agentic search as context pollution, mode collapse/local minima, and weak collaboration. It uses:

- Hierarchical Context Management: separates macro conceptual ideas from micro experimental
  hypotheses; decouples generation from selection; persists an idea pool; deduplicates concepts;
  limits the number of hypotheses per idea and the number of active idea threads; retains summaries
  of pruned or failed directions;
- Momentum-based Backtracking: uses normalized relative progress and EWMA momentum; returns to an
  earlier state and clears recent local context when momentum falls below a threshold;
- Collaborative Evolution: multiple islands switch among independent search, backtracking, and
  compatible idea crossover.

**Proposal:** A plateau route must change the search neighborhood. Permitted actions include
returning to an ancestor node, activating a different cluster, performing compatible crossover, or
refreshing community evidence. Trying the same prompt again does not count as backtracking.

### Archive, Diversity, and Nonzero Exploration

**Fact:** The meta-agent in [ADAS](https://arxiv.org/pdf/2408.08435) starts from an archive of evaluated
designs, proposes a new agent, and checks it for novelty; the archive provides reusable experience for
subsequent discoveries.

**Fact:** [AFlow](https://arxiv.org/pdf/2410.10762) represents workflows as code and uses MCTS-based
search and tree experience. Its selection probability includes a uniform exploration component, so
low-scoring directions retain a nonzero chance of selection. AFlow also uses a top-k set remaining
unchanged for a long time as a stopping condition.

**Boundary:** A stable top-k set indicates only that the incumbent ranking is stable, not that idea
coverage is sufficient. When adapting an AFlow-style stopping rule to long-running optimization,
uncovered clusters and underexplored ideas must also be checked.

**Fact:** [MAP-Elites](https://arxiv.org/pdf/1504.04909) retains multiple local elites along
user-specified behavioral dimensions rather than retaining only a single global best.
[Hyperband](https://arxiv.org/pdf/1603.06560) uses different resource-allocation brackets to handle
early stopping, and [BOHB](https://arxiv.org/pdf/1807.01774) combines this resource allocation with
model-guided search.

**Inference:** An idea board can treat clusters or behavioral dimensions as diversity cells and
compare directions under different minimum budgets. It should not mechanically copy these
algorithms, because workflow trials usually do not satisfy standard hyperparameter-optimization
assumptions about cost, noise, and comparability.

## 4. Token and Workflow Efficiency

### Schedule First, Then Prune

**Fact:** [LLM-as-a-Scheduler](https://aclanthology.org/2026.acl-long.581/) uses a lightweight decision
to determine whether to invoke a heavier agentic workflow. The original paper reports 43% fewer
tokens and more than 36% lower latency in its evaluation, with an accuracy decrease of no more than
1.4 percentage points.

**Fact:** [SCALE](https://aclanthology.org/2026.findings-acl.254/) finds that a small number of
task-level workflows can cover multiple query classes, and selects the top-K through low-cost,
calibrated evaluation. The original paper reports up to an 83% token reduction and an average
performance decrease of 0.61%.

**Proposal:** Use a cheap deterministic prefilter plus a calibrated gate in the current framework.
Simple, low-risk tasks take the compact lane; uncertain tasks default to the heavy lane. Calibration
metrics must include the false-easy rate.

### Remove Deterministic Repetition from Traces

**Fact:** [AWO](https://arxiv.org/pdf/2601.22037) mines recurring tool-call sequences from successful
traces and compiles them into a deterministic meta-tool. The original paper reports up to 11.9% fewer
LLM calls and a 4.2-percentage-point increase in success rate.

**Proposal:** Script a sequence only after multiple traces show that it is stable, its inputs and
outputs can be defined, and regression tests pass. Do not prematurely hard-code steps that still
require judgment.

### Baseline-Anchored Slimming

**Fact:** [AgentSlimming](https://aclanthology.org/2026.acl-long.1387/) estimates the marginal importance
of agents, removing low-value agents or replacing them with cheaper models. The paper reports up to a
78.9% token reduction on its benchmarks.

**Fact:** [AgentDropout](https://aclanthology.org/2025.acl-long.1170/) dynamically prunes redundant agents
and communication. The paper reports average reductions of 21.6% in prompt tokens and 18.4% in
completion tokens.

**Proposal:** Use the same acceptance sample before and after deleting, merging, or downgrading a
node. Put a temporarily inactive node into `STANDBY` first, then observe changes in the input
distribution before removing it permanently.

### Separate Development Exploration from Deployment Reuse

**Fact:** During development, [DS-Agent](https://arxiv.org/pdf/2402.17453) uses a case-based reasoning
cycle of retrieve/reuse/revise/retain, then reuses successful cases during low-resource deployment.
The original paper reports a cost reduction of more than 90% in its setting.

**Fact:** [AutoKaggle](https://arxiv.org/pdf/2410.20424) uses a staged, multi-agent, debug/test workflow.
Its experiments show that unit tests significantly improve task completion rates, but additional
debugging attempts eventually plateau; complex errors do not disappear merely through more of the
same debugging.

**Proposal:** Put expensive exploration, community refresh, and workflow evolution in the development
lane; stable runs should reuse results from validated cases, scripts, and compact state. Increase
exploration and review again when scope/model/data drift appears.

## 5. Executable Design Rules

| Observed problem | Adopted state/routing | Prohibited shortcut |
|---|---|---|
| New direction fails in its first round | `EXPLORING` or `STANDBY`; record the evidence deficit | Move directly to `EXHAUSTED` |
| Repeated minor revisions to the incumbent make no progress | momentum plateau → backtrack / new cluster / crossover | Unlimited revision |
| The board grows and pollutes context | macro/micro hierarchy, cap on the active set, summary + artifact pointer | Inject the full history on every round |
| Easy tasks also run the full graph | calibrated cheap gate → compact/heavy route | Choose cheap based only on cost |
| A tool sequence recurs | Compile it into a script/meta-tool after trace validation | Freeze judgment steps prematurely |
| An agent/node is considered for deletion | Run an ablation on the same baseline; use standby first | Consider only a single cost reduction |
| The total budget expires while a promising idea remains | checkpoint + board + next action | Claim convergence |

## 6. Primary Sources

1. [Agent K v1.0, arXiv v1 PDF](https://arxiv.org/pdf/2411.03562v1)
2. [Agent K current arXiv record](https://arxiv.org/abs/2411.03562)
3. [MLE-bench](https://arxiv.org/pdf/2410.07095)
4. [AIDE paper](https://arxiv.org/pdf/2502.13138)
5. [AIDE repository](https://github.com/WecoAI/aideml)
6. [Weco/AIDE documentation](https://docs.weco.ai/)
7. [PACEvolve](https://arxiv.org/pdf/2601.10657)
8. [AgentRevive](https://aclanthology.org/2026.acl-long.373/)
9. [ADAS](https://arxiv.org/pdf/2408.08435)
10. [AFlow](https://arxiv.org/pdf/2410.10762)
11. [AFlow repository](https://github.com/FoundationAgents/AFlow)
12. [MAP-Elites](https://arxiv.org/pdf/1504.04909)
13. [Hyperband](https://arxiv.org/pdf/1603.06560)
14. [BOHB](https://arxiv.org/pdf/1807.01774)
15. [LLM-as-a-Scheduler](https://aclanthology.org/2026.acl-long.581/)
16. [SCALE](https://aclanthology.org/2026.findings-acl.254/)
17. [AWO](https://arxiv.org/pdf/2601.22037)
18. [AgentSlimming](https://aclanthology.org/2026.acl-long.1387/)
19. [AgentDropout](https://aclanthology.org/2025.acl-long.1170/)
20. [DS-Agent](https://arxiv.org/pdf/2402.17453)
21. [AutoKaggle](https://arxiv.org/pdf/2410.20424)
