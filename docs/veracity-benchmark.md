# Veracity benchmark — is the live root cause actually right?

`tools/b13_capture.py` freezes two things per live capture, and until now only
one of them was ever checked against ground truth:

- `tests/golden/real_00N.json` — the deterministic verdict triple
  (`verdict`/`risk`/`rollback_available`). Provenance evidence, not asserted
  against an expected value (a live LLM + Monte-Carlo run is non-deterministic
  — see `docs/evidence/prometheus-live.md`'s "How to read this").
- The LLM's `root_cause` free text — previously appended to
  `docs/evidence/prometheus-live.md` as prose and **never checked against
  anything**. The first two live h014/h015 captures were both wrong
  (h014 said "memory issue" for an expired mTLS certificate; h015 said
  "incompatible container image" twice, for etcd compaction latency) and
  nothing short of re-reading the markdown by hand would have caught that.

## What this adds

`tests/veracity/real_00N.json`, written by `write_veracity()` alongside
`write_golden()`, one file per capture, same index:

```json
{
  "scenario_id": "h014_cert_expiry",
  "captured_at": "2026-09-26T10:37:41.402469+00:00",
  "root_cause_contains": ["certificate", "expired", "x509", "tls", "renew", "issuer"],
  "matched_keywords": [],
  "actual_root_cause": "The root cause appears to be a memory issue with the billing-gateway pods. ...",
  "veracity": "FAIL"
}
```

- **`root_cause_contains` is ground truth, not a guess** — for h001–h015 we
  authored the injected fault ourselves (see `demo/manifests/`), so the same
  keyword list already declared in each case's
  `tests/integration/cases/hNNN_*/expect.json` applies unchanged to a live
  capture of that same scenario. `SCENARIOS[...]["root_cause_contains"]` in
  `tools/b13_capture.py` is copied verbatim from there.
- **Grading rule matches the existing one** — `grade_veracity()` is the same
  "≥50% of keywords, case-insensitive substring, min 1" check
  `test_root_cause_keywords` (`tests/integration/use_cases/test_native_helm_dialogue.py`)
  already uses against the offline fixtures. A live capture and a fixture run
  are graded the same way.
- **PASS/FAIL, not a similarity score** — deliberately not a vector-similarity
  check. Veracity needs a binary, auditable answer ("did it name the real
  cause"), not "how close is this text to some other text."

## What this does *not* touch

`knowledge/example_store.py` / `data/examples/*.json` (FAISS `example:` UIDs)
is a **separate, pre-existing store** of *verified-correct* resolved
incidents, fed back to the LLM as few-shot RAG context
(`workflow/nodes.py::save_example`). A veracity `FAIL` must never end up there
— retrieving your own past wrong answer as a "resolved incident" would teach
the LLM to repeat it. The two stores stay disjoint: `tests/veracity/` is a
read-only benchmark corpus, `data/examples/` is a write path gated on human
approval of a correct fix. Nothing in `write_veracity()` writes to
`data/examples/`, and nothing in `save_example()` reads `tests/veracity/`.

## How to read the current corpus

| Scenario | real_00N | Veracity | Actual root cause said |
|---|---|---|---|
| h014_cert_expiry | real_003 | **FAIL** | "memory issue" — the true cause (expired x509 cert) never appears |
| h015_etcd_compaction | real_004 | **FAIL** (×2 attempts) | "incompatible container image" — the true cause (etcd compaction latency) never appears |

Both were retroactively backfilled from `docs/evidence/prometheus-live.md`
(captured before `write_veracity()` existed — see the `note` field in those
two files).

## Why this matters for the phase-2 "specialized small LLM" question

[[project_gcp_sovereign_cloud_goal]] records that a fine-tuned/specialized LLM
is a deliberately deferred phase-2 idea. This benchmark is the prerequisite
for ever answering "did switching models help": without a ground-truth,
re-playable PASS/FAIL corpus, there is no way to compare a generic Ollama
model against a future specialized one on anything but vibes. Every new live
capture (and eventually every fixture case) should grow this corpus.

## Next

- Extend `root_cause_contains` to the Phase 1 scenarios (h001–h004) in
  `tools/b13_capture.py`'s `SCENARIOS` registry, copied from their own
  `expect.json`, so every live capture — not just h014/h015 — gets graded.
- Once the corpus has enough entries, track a veracity pass-rate over time
  (per model, per scenario) the same way `docs/roadmap.md` tracks feature
  completion.
