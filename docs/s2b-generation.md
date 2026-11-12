# S2b — generating the scenarios with Grok

> Spec, written 2026-11-08, before any call to the xAI API and before any Grok scenario exists. It applies
> `docs/s2b-validation-cases.md` § *Who writes the cases* to a second external author. Nothing here changes after the
> pilot's results are read, except by a dated amendment below.

## Why a second author

Gemini wrote rounds 1 and 2 (`tests/s2b/scenarios/provenance.md`): 15 of 33 scenarios were usable. Grok (xAI) is a
fourth provider. It has written nothing in this project and graded nothing. A second author also lets the results be
split by author, which is a control on the author's own style.

## The pipeline

**Stage A, the scenario.**
1. Grok receives `tests/s2b/scenarios/brief-v2.md` and a request naming the families and the number of scenarios.
2. Its answer is kept as given.
3. `tools/s2b_check.py` runs the automated checks (§ Checks).
4. A scenario that passes them is read by a person, who may reject it only on a Kubernetes fact, stated with its
   source. The reviewer neither rewrites it nor edits its ground truth.

**Stage B, the fixtures.** For each accepted scenario, the same author writes the case's files:
- the `kube/` objects;
- the events, logs and alerts;
- the Helm history;
- the evidence of each hop.

Our tools only verify:
- the objects parse as Kubernetes objects;
- every hop's evidence is in the files (`tests/s2/evidence.yaml`'s check);
- the graph built by the product's collectors holds them (`tests/s2/live_graph.py`).

A case that fails a check goes back to its author, with the check's output. Stage B has its own spec, written
before its first request.

## Checks (stage A)

`tools/s2b_check.py <file>` prints one line per scenario, PASS or FAIL, with the reasons. The checks are:

1. The file is valid YAML, every id is unique, and the required fields are present.
2. `partial_if_only` never contains `cause_object`, nor an object `passes_if` names.
3. A scenario with no incident has `passes_if` exactly "states that there is no incident and names no fault", an
   empty chain, and a mechanism that names no cause ("caused by", "due to", "because of" followed by an object).
4. A scenario with a cause has at least 2 hops that are neither "object → its own Namespace" nor
   "Deployment → its ReplicaSet".
5. Every kind named in `cause_object`, the chain and the decoys is read by the product (brief v2, *What the tool can
   see*).
6. Every time (`T0…`) in the chain and the decoys appears in the timeline.
7. There are at least 2 decoys.
8. `objects` holds at least 25 entries across at least 2 namespaces.
9. No two scenarios of the file share a chain signature (cause kind and the kinds along the chain).

The checks are tested on Gemini's round 2 (`tests/unit/test_s2b_check.py`), whose defects are known from its review,
and each check has a mutant that must fail.

## The pilot, fixed before any result

- **One batch of 11 scenarios**, one per family, the 8 families with a cause first, then 3 with no incident.
  Precisely: 8 with a cause (quota, netpol, dependency, limitrange, hpa, webhook, configmap, pdb) and 3 with no
  incident (ingress, storage, pullsecret).
- **Yield = scenarios accepted at stage A ÷ scenarios received.**
- **Rule:**
  - a yield of at least 70 % lets Grok write the rest in batches of 33;
  - between 50 % and 70 %, a second pilot batch;
  - **below 50 % after two batches, an alert is raised**, and the choice of author is decided again by the user.
- The yield of each later batch is recorded too, with the same alert.

## Provenance

Each batch is a directory `tests/s2b/scenarios/grok-<date>-<batch>/` holding:

- `request.json`: what was sent (the model, the messages, the temperature, the token cap), with the sha256 of the
  brief and of the full request;
- `models.json`: the API's model list at the time of the call, from which the exact model id is taken, never assumed;
- `response.json`: the raw HTTP response body, as given;
- `scenarios.yaml`: the answer's text, as given, with nothing removed or reformatted;
- `provenance.json`: the date, the model id, the hashes, the token counts the API reports, and the duration.

## Cost cap

One request per batch, with a fixed `max_tokens` (32 000 for 11 scenarios, 64 000 for 33). There is no automatic
retry. A failed call is recorded and reported, and a new attempt is a new batch with its own go. The token counts
of each batch are recorded, so its cost can be read.

## The key

Read from the environment variable `XAI_API_KEY`, or else from the file `~/.config/xai/api_key`. The script refuses
the file unless its mode is 600. The key is never printed, logged, written to a record or committed. The user sets
it themselves, outside any assistant session.

## Independence and declared bias

- Grok writes scenarios and fixtures. It never grades an answer on any case.
- The reviewer of stage A is also the author of the correlation engine. This bias is declared with every result. The
  review is limited to the automated checks and to rejections stated with a Kubernetes fact; no scenario is
  rewritten.
- The brief does not contain the engine's spec, its code, S2's cases or S2's results.
- Every call needs the user's go. The first is the pilot.
