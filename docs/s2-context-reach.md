# S2 — does the cause object reach what the arms are given? (offline, no model)

> Written 2026-11-08, before the measurement below was run. The rule, the prediction and the metric are fixed here;
> nothing in them changes after the run. Results are appended under § Results, below the line, with the commit.

## Why

S2's first run (`docs/s2-results.md`) was made with two defects of the harness and the product that hid evidence:

1. **Discovery was empty.** `tests/s2/live_graph.py` answered API discovery with nothing, while production adds every
   kind it has no typed collector for (ResourceQuota, LimitRange, HPA, Job, CronJob, CRDs) as a generic entity
   (`K8sCollector.collect`, step 2). Fixed 2026-11-08: discovery is the product's own `APIServerDiscovery`, served
   from the case's objects (`discovery_for_case`, tested in `tests/unit/test_s2_live_graph.py`, with the empty
   discovery as a control that fails).
2. **A discovered entity broke code that reads `entity.kind.value`.** Fixed 2026-11-08 (`DynamicKind`,
   `tests/unit/test_dynamic_kind.py`, mutant killed).

And S2 ran with the investigation's vector search off (`store=stub`, configuration C1), the mechanism meant to bring
in what the graph does not link.

This measure asks only where the evidence was, not what any arm concluded. It needs no model.

## The metric

For each of S2's 14 cases with a cause: **is the cause object in the analyze prompt?**

- The prompt is the one the context arms (L₀, S₀) receive. It is built by `solve_context`'s own path
  (`RCAAnalyzer` over the case's graph), with a client that answers nothing.
- The object counts as present when one line of the prompt contains both its kind and its name, case-insensitive. For
  a Namespace, it is the line `Namespace/<name>` or `namespace <name>`.
- Also reported, without a rule: whether the cause object is in the graph (what D reads).

The 5 cases whose cause kind is collected only by discovery are reported apart: a1 (APIService), a2 (HPA), l1 and l2
(LimitRange), and q2 (CronJob).

## The configurations

| Name | Discovery | Vector search |
|---|---|---|
| **B0** | empty, as S2's first run | off (stub) |
| **B1** | served | off (stub), C1 |
| **B2** | served | the investigation's hybrid search (BM25 + dense + RRF over the graph), C2 |

## The prediction, written before the run

- B1 against B0: the cause object reaches the graph in a2, l1, l2 and q2. It reaches the prompt in **at most 2 more
  cases**, because a generic entity is linked only to its Namespace, and competes for 10 related slots ranked by
  TF-IDF against the question.
- a1 stays absent in every configuration. APIService is cluster-scoped, and discovery skips cluster-scoped kinds under
  a namespace filter.
- B2 against B1: **at most 2 more cases.** The store holds the graph only, without the documents or the anchors the
  thesis counts on (`docs/protocol-001-retrieval.md` § R1, *What R1 does not measure*).

## The rule

- **B1 against B0.** Three or more more cases with the cause in the prompt means the harness defect alone explains
  part of S2's result, and S2 is re-run before its numbers are cited again. Fewer than three means the relations
  missing from the graph, not the harness, are the main reason (M1, `docs/correlation-engine.md` § 1).
- **B2 against B1.** Three or more more cases means the investigation's hybrid search brings in what the graph does
  not link. One or two is inconclusive. None means no gain on this set.
- **What this measure cannot do.** It cannot refute the thesis that hybrid search over heterogeneous sources (charts,
  release notes, controller logs, enterprise documents) brings in the missing hop. That is R2's question. This store
  holds none of them.

## How it is run

`python tools/s2_context_reach.py`, offline, inside the replay's isolated stores. Records go to
`tests/s2/context-reach/`, one JSON per configuration and case, holding the prompt, its hash, the presence of the
cause object and the commit.

---

## Results

Run 2026-11-08 at 17:49Z, on commit `74a53e0` with the working tree's fixes (discovery served, `DynamicKind`, and a
third fix found while running: `find_unhealthy` raised AttributeError on a ResourceQuota added by discovery, since its
kind string equals `ResourceKind.RESOURCE_QUOTA`; it now keeps typed quotas only, `tests/unit/test_dynamic_kind.py`,
mutant killed). The rule above was written at 17:47Z and not changed. Records: `tests/s2/context-reach/`.

**Control.** B0's 14 prompts are identical to the prompts S2's first run sent to mistral
(`s2-context-mistral-…/run_1.json`), except the analysis timestamp line: B0 reproduces that run.

| Case | Cause kind collected by discovery only | B0 graph/prompt | B1 graph/prompt | B2 graph/prompt |
|---|---|---|---|---|
| a1 | yes | no/no | no/no | no/no |
| a2 | yes | no/no | yes/yes | yes/yes |
| c1 | | yes/no | yes/no | yes/**yes** |
| c2 | | yes/yes | yes/yes | yes/yes |
| d1 | | yes/no | yes/no | yes/no |
| d2 | | yes/no | yes/no | yes/no |
| l1 | yes | no/no | yes/no | yes/**yes** |
| l2 | yes | no/no | yes/no | yes/**yes** |
| n1 | | yes/yes | yes/yes | yes/yes |
| n2 | | yes/yes | yes/yes | yes/yes |
| q1 | | yes/yes | yes/yes | yes/yes |
| q2 | yes | no/no | yes/no | yes/no |
| w1 | | yes/no | yes/no | yes/no |
| w2 | | no/yes | no/yes | no/yes |
| **Cause in prompt** | | **5/14** | **6/14** | **9/14** |
| Cause in graph | | 8/14 | 12/14 | 12/14 |

**Against the rule.**

- **B1 against B0: +1** (a2). Below 3, so the harness defect alone does not explain S2's result for the context arms:
  the main reason the cause misses the prompt is the relations missing from the graph. **In the graph, though,
  4 more cases** (a2, l1, l2, q2) now hold the cause. D reads the graph, so S2's D result was measured on a graph
  missing 4 cause objects, and must be re-run before it is cited again.
- **B2 against B1: +3** (c1, l1, l2). By the rule, the investigation's hybrid search brings in what the graph does not
  link. **The prediction (at most 2) was wrong.** On this set, retrieval over the graph alone already reaches 3 cause
  objects that no relation reaches. This is evidence for the hybrid-search thesis even without documents.
- **As predicted, a1 stays absent everywhere.** APIService is cluster-scoped, and discovery skips it under a namespace
  filter.

**Limits.**

- The metric is textual: a line with the kind and the name.
- w2 counts as present while its cause (a Secret) is in no graph. The prompt names it in an event or log line with the
  word "secret".
- d1, d2 and w1 have their cause in the graph and never in the prompt by this test. The prompt may name it on a line
  without its kind.
- 14 cases, one run, no model: this says where the evidence was, not what any arm would conclude.

## Development milestones (S2 as the development set, not a test)

Each change to the engine is measured with the same tool, `python tools/s2_context_reach.py <label>` (B1 and B2
only), its records under `tests/s2/context-reach/<label>/`. S2 is now a development set (`docs/correlation-engine.md`
§ 5): these numbers measure the implementation, not generalization.

| Label | Change | B1 cause in prompt | B2 cause in prompt |
|---|---|---|---|
| (baseline, above) | discovery served | 6/14 | 9/14 |
| `M1-limits` | LIMITS built (`ontology/relations.py`): LimitRange → Pod for an injected default (owner template declares none, with LimitRanger's record), → the refused object for a LimitRanger refusal | 6/14 | 9/14 |
| `M1-limits-eventwalk` | the context walk follows HAS_EVENT backwards: from a warning event (always a seed) to its object | **9/14** (+ l1, l2, d1) | 9/14 (d1 in B1, not in B2: its Service loses its slot to retrieval hits) |
| `M1-configures` | CONFIGURES built: ConfigMap → Pod through `envFrom` / `env.valueFrom` (mounted ConfigMaps keep `MOUNTS_CONFIGMAP`); followed backwards by the walk | **10/14** (+ c1) | 9/14 |

Read: LIMITS alone changed nothing in the prompt, because the seeds of l1 and l2 are only warning events, and the walk
could not leave an event. With both, the LimitRange reaches the prompt in l1 and l2. In B2, the 10 related slots are
now the limit.

## Rule for the related-context fusion, written 2026-11-08 before its code

**Change.** The related section of the prompt (`rca/context_builder.py`, section 7) keeps 10 texts, ranked today by
TF-IDF against the question over the union of the graph walk's objects and the retrieval hits. The candidate is a
Reciprocal Rank Fusion (`vectorstore/rrf.py`, k = `RRF_K`) of three ranked lists over the same deduplicated candidates:
- the graph walk's order: the seeds first, then each seed's breadth-first neighbours;
- the investigation's hybrid search order (BM25 + dense, already RRF-fused), empty with the stub store;
- TF-IDF against the question.

It is behind `CONTEXT_RELATED_RANKING` (`tfidf` | `rrf`), and the default stays `tfidf` unless the rule below keeps
the fusion.

**Metric.** Cause object in the prompt, as above, on S2 (the development set), B1 and B2, against the last milestone
(`M1-configures`: B1 10/14, B2 9/14).

**Prediction.** d1 comes back in B2; B2 ≥ B1.

**Rule.** The fusion becomes the default if B2 gains at least 1 case and neither B1 nor B2 loses one, and `make ci`
stays green. Otherwise it stays off, and the result is recorded here.

**Result (2026-11-08, 18:3xZ, label `M1-configures-rrf`).** B1 10/14, B2 9/14: no change. **The prediction was wrong
and the fusion is not kept**; its code is removed rather than left behind an unused setting. Why: d1's Service is among
the 21 B2 candidates and survives deduplication, but ranks past the 10 related slots in all three orders. The limit
is the number of slots, not the ranking. What follows from it is the engine's (M4): the objects a verified relation
reaches from the symptom get their own prompt section, instead of competing for related slots.
