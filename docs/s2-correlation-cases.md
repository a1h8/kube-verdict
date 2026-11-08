# S2 — cases where the cause is several steps from the symptom

> Spec, written 2026-11-06, before any S2 case, fixture or run. Status: draft. It details set S2
> of `docs/protocol-002-retrieval-vs-models.md` and proposes two amendments to it (§ Amendments
> proposed); nothing here applies until they are approved and dated.

## Why

On the 19 native cases (S1) the structured diagnosis passes 19/19, but its rules were written on
them. An external pass over 76 sampled answers adds one observation: the models alone find the
symptom and miss the mechanism where the cause takes several objects to reach (NetworkPolicy,
PVC and node, etcd behind a probe). S2 asks whether that holds on cases the rules were not
written on, where the correlation is longer.

**What S2 can show:** whether each arm follows a chain of several objects to its cause, and keeps
quiet when nothing is wrong. **What it cannot show:** how any arm does on incidents we did not
write. We write S2, knowing the product, so it stays a set of our own making. That is set S3
(ITBench), the primary one.

## What makes a case

A case is in S2 only if it has all of the following, declared in its `expect.json` before any run:

1. **A chain of at least two hops** from the symptom to the cause, each hop a stated relation between
   two objects, at least one of them with no edge in the graph today (for example, a pod's DNS timeout
   → the NetworkPolicy selecting it → the namespace label that policy matches).
   **Every hop has its evidence in the fixtures,** listed in `tests/s2/evidence.yaml`: the file that shows the hop, a
   text it contains (or, for an absence, a text it must not contain), and what joins it to the next hop (a name, a
   label, a port, a timestamp). `tests/unit/test_s2_cases.py` checks every one against the fixtures. A text found in a
   file proves the evidence is there, not that it is unambiguous: the joins are still for a reader to check. An analyst
   reading only the fixtures can then walk the chain from the symptom to the cause without guessing (added 2026-11-07,
   before any S2 run).
2. **At least two decoys:** drifts or warnings that are real and visible but do not explain the symptom.
   Each has a written reason why it is not the cause.
3. **Tens of objects across at least two namespaces,** most of them healthy, so the cause is not the
   only thing in the evidence.
4. **A time order** where it matters: the change that caused the symptom is dated before it, and a
   decoy may be dated after.
5. **A source outside the product:** a runbook, a Kubernetes documentation page or a public postmortem
   that describes the mechanism, cited in the case. The case is never derived from a rule of ours.

## Families

The seven of protocol 002, each with at least two cases with a fault and one negative case (the same
objects, no fault, decoys kept):

| Family | Example chain, to be specified per family |
|---|---|
| ResourceQuota | a dependency slows, retries scale an HPA, the namespace quota fills, new pods stay Pending |
| LimitRange | a namespace default limit injected into a pod that declares none, OOMKilled under load |
| NetworkPolicy | a new namespace label stops matching an allow policy, a pod's calls time out |
| HPA | the metrics adapter's APIService is unavailable, the HPA cannot read its metric, no scale-up |
| Dependency in another namespace | a Service in another namespace loses its endpoints, a client's readiness fails |
| Mutating webhook | a webhook injects a sidecar with a bad setting, pods of several releases fail to start |
| Shared ConfigMap | one ConfigMap read by several Deployments changes, only those reloading it break |

The example chains are starting points, not decisions: each family's spec fixes its own, from its source.

## How a case is written

- **Order, per family:** its spec (chains, decoys, negative case, sources), shown and approved; then its
  fixtures; then its `expect.json`. No family's fixtures before its spec.
- **Formats:** those of `tests/integration/cases/` and `case_loader.py`, unchanged.
- **The author is declared biased.** Whoever writes the cases knows the product's rules, and that knowledge
  cannot be removed. What limits it: the chain comes from the external source, the decoys are chosen
  before reading what the rules match, and no case is edited after any arm has run on it.
- **Frozen before the first run:** the hash of every S2 case is written in the amendment that sizes S2.
  A case changed afterwards is a new case, measured on a new block.
- **No rule follows the results.** No rule of the structured diagnosis is added or changed for an S2
  family until S2 has run. A rule written after that is measured on S3 or on new cases, never on S2.

## How a case is graded

Block B showed that grading by keywords errs both ways: too strict on a model's cause in other words, too
lenient on a case with no incident (an invented "paused deployment" passed, because the rule only asks
for a LOW label and no forbidden word). For S2:

- **Primary: the cause object and its mechanism.** `expect.json` names the cause as `kind/namespace/name`
  and states the mechanism in one sentence (`kind/name` for a cluster-scoped object such as a Namespace). An answer passes when it names that object and that mechanism.
- **Graded blind by two graders,** not told which arm wrote each answer, with the instructions written
  before any run: two models from different providers, or one person and one model. Their agreement is
  reported; a disagreement goes to a third reading, and the case is reported as disputed.
- **The keyword grader is kept as a secondary measure,** so S2 can be compared with S1, and the two
  are never pooled.
- **A negative case passes only on an explicit "no incident"** with no fault claimed. A LOW label is no
  longer enough. A test fails on the answer that passed in block B ("The deployment of the ledger is
  paused…").

## Before any S2 run

0. **The test loader at parity with the live collector** (decided 2026-11-07). Writing the first families showed that
   `tests/integration/cases/case_loader.py` builds a graph the product would not: it reads no Service, so no
   `EXPOSES` edge exists in the replay while `K8sCollector._collect_services` creates one from each Service's selector;
   it counts ConfigMaps as covered without building any, so an applied ConfigMap is reported absent; and it does not
   carry a Deployment's image where the applied-vs-live comparison reads it; and it reads no MutatingWebhookConfiguration, which the live policy collector adds
   to the graph (found 2026-11-07). Measured as it is, S2 would understate the
   product. *Decided 2026-11-07 (option B):* S2's graph is built by the product's own collectors, `K8sCollector` and
   the policy collector, served the case's fixtures by `tests/integration/fake_kube_api.py`, then completed with the
   loader's other steps (alerts, logs, traces, the Helm release, drift). Parity is then by construction, not by copying
   each behaviour into the loader. S1 keeps its loader, unchanged, so its results and block B's stand as they are; S1
   and S2 are built differently, which is stated wherever both appear. Moving S1 to the same path later is its own
   dated change, measured before and after.
   *Done 2026-11-07:* `tests/s2/live_graph.py`, checked by `tests/unit/test_s2_live_graph.py` against the loader graph
   of the same cases: Services and their `EXPOSES` edges, ConfigMaps (none reported absent), the applied-vs-live image
   drift, and the webhook as a node are all there. Two things the API always returns and the fixtures lacked were added
   before any run: a Namespace's phase (filled when served) and a ReplicaSet's selector (in the builders).
1. The two amendments below, approved and dated.
2. The fix to the negative-case grading, with its test and its control.
3. The volume, computed from block B's discordant pairs, as protocol 002 requires, written before any run. *Done 2026-11-08:* protocol 002, amendment A3. S2 is exploratory.
4. Every family's spec, cases and `expect.json`, then the hashes frozen. *Done 2026-11-08:* 21 cases, hashed in `tests/s2/frozen.json` (`tests/s2/frozen.py`), checked by
   `tests/unit/test_s2_frozen.py`. From here a changed case is a new case.

## Amendments proposed to protocol 002

- **A1 — grading of S2:** primary on the cause object and its mechanism, by two blind graders; the keyword
  grader secondary. Protocol 002 names the keyword grader as primary on S1 and S2, unchanged for S1.
- **A2 — negative cases:** an explicit "no incident" with no fault claimed, on every set. It changes how
  S1's negative cases would be graded in protocol 002's runs, not block B's results.

## Not in this spec

- S3 (ITBench) and its adapter, specified on their own.
- Any change to the structured diagnosis.
- Which models are run: protocol 002's arms are unchanged.
