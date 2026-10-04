# EchoRAG: A Lightweight Retrieval Memory for Offline Assistants

Authors: Mina Chen, Taro Sato
Year: 2026
Venue: Proceedings of the Fictional Workshop on Reproducible Agents
Keywords: retrieval augmented generation, memory, offline evaluation

# Abstract

We present EchoRAG, a small retrieval-and-memory module for task-oriented
assistants.  EchoRAG stores previous observations as timestamped notes and
retrieves the top *k* notes for a new query.  On the fictional MemoBench
benchmark, the method improves exact-answer accuracy over a no-memory baseline
while using a bounded context window.  This document is an intentionally
fictional, self-contained sample paper for Paper2Lab's offline demonstration;
it does not claim results about a real system.

# Introduction

Long-running agents often lose useful facts when their context window is
small.  A reproducible memory component should expose its retrieval rule,
dataset split, and evaluation metric.  Our central claim is that a deterministic
top-*k* memory can improve answer accuracy without changing the base model.

The paper makes three testable claims:

1. EchoRAG improves exact-match accuracy over the no-memory baseline.
2. Increasing the memory budget from 2 to 4 notes improves recall on queries
   whose answer was seen earlier.
3. The gain is retained when the query order is replayed with a fixed seed.

# Related Work

Prior memory systems use vector databases or learned controllers.  We focus on
an auditable rule-based component so that an experiment can be run offline.

# Method

Each interaction is converted into a note `(key, value, timestamp)`.  Given a
query, the retriever scores notes by token overlap, breaks ties by recency, and
returns the top *k*.  The answer module copies the value from the highest
scoring note.  The only independent variable in the main experiment is the
memory budget *k*; the answer module, tokenizer, and query order are controls.

Algorithm 1 — deterministic retrieval:

1. tokenize the query and every stored key;
2. compute overlap score `|query ∩ key|`;
3. sort by `(score, timestamp)` descending;
4. return the first `k` notes.

# Dataset

MemoBench-S is a fictional dataset of 120 short key-value interactions.  It is
split into 60 training, 30 validation, and 30 test queries.  Each test query
has one answer in the preceding interaction history.  The sample distribution
is balanced across the three topics `city`, `food`, and `schedule`.

# Experiment

We compare EchoRAG with `NoMemory`, which receives only the current query.
Experiments use the deterministic MockModel, seed 7, memory budgets `k ∈ {0,
2, 4}` and three repeated runs.  We report exact-match accuracy, answer recall,
and mean lookup time.  The expected direction is that `k=4` is at least as
accurate as `k=2`, while lookup time remains below 5 ms per query in the mock
environment.

# Metrics

Exact-match accuracy is the fraction of predictions equal to the reference
answer.  Answer recall is the fraction of test answers found in retrieved
notes.  We also record mean lookup time in milliseconds.  No metric is treated
as evidence beyond this fictional sample.

# Results

The paper-reported result for the main comparison is:

| Method | Accuracy | Recall | Lookup time (ms) |
|---|---:|---:|---:|
| NoMemory | 0.600 | 0.000 | 0.10 |
| EchoRAG (k=2) | 0.800 | 0.800 | 0.80 |
| EchoRAG (k=4) | 0.814 | 0.900 | 1.20 |

These numbers are fixed demonstration values, not measurements from a real
publication.

# Ablation

Removing the recency tie-breaker lowers accuracy from 0.814 to 0.810 in the
fictional ablation.  Setting `k=0` is equivalent to the NoMemory baseline.

# Limitations

The dataset, model, and results are synthetic.  The overlap scorer does not
represent semantic retrieval, and the benchmark is too small to support claims
about real-world agents.  A real reproduction must replace every TODO with
documented code and a legally obtained dataset.

# Conclusion

EchoRAG is a compact example of a claim that can be translated into a local
experiment blueprint.  Paper2Lab should preserve the distinction between the
reported values above and locally generated values, and should mark missing
implementation details as TODO rather than inventing them.

# Appendix

Default configuration: `seed: 7`, `memory_budget: 4`, `repeats: 3`,
`metric: exact_match`.  The sample experiment intentionally uses a deterministic
MockExperiment so that a clean installation can reproduce the same result.
