# PatchTST — what it contributes today, and what to do

## Two paths

1. **In-process** — `signal_analysis_node` (`workflow/nodes.py`) runs `SignalAnalyzer`
   (`signals/analyzer.py`) on every investigation and writes `signal.*` annotations on
   graph entities, which reach the context.
2. **Pushed** — the standalone PatchTST / temporal-evidence-engine pipeline POSTs a
   `SignalAlert` to `/api/v1/webhook/signal`. It was verified live once (2026-09-26,
   `docs/patchtst-readiness.md`): the push lands and its labels scope the session.

## What path 1 does in every offline test (read from the code, 2026-10-12)

- The harness enables Prometheus but stubs `PrometheusMetricSource._range_query` to
  return nothing. `SignalAnalyzer` then **falls back to synthetic series per pod**
  (`signals/analyzer.py`, `_synthetic_pod_segments`).
- A synthetic series is generated **from the pod's own current state**: for example,
  `restart_count` becomes a fabricated history with a spike. PatchTST then "detects" an
  anomaly in a series built to contain one, and the `signal.*` annotation enters the
  prompt as if it were independent evidence. It is the restart count, said twice.
- Its seed is `hash(pod.uid) & 0xFFFF`. Python randomizes `str` hashes per process
  (`PYTHONHASHSEED`), so **the synthetic series, and possibly the anomaly flags, can
  differ between runs**. *To verify with a test before relying on it.*
- Cost: it trains one small model per watched entity, 20–45 s per h016 run and about
  4 min for the h016 module.

So in tests PatchTST adds circular evidence, a possible source of non-determinism, and
most of the runtime. No case has a PatchTST anomaly as its cause or its earliest signal.

## What to do

1. **Make the offline path honest.** In the harness, signals come from fixtures or not
   at all: a case may ship `signals/` (metric series, like `prometheus/` ships alerts).
   Without it, the node records `mode: "none"` and adds no annotation. Synthetic mode
   stays available for the legacy UI and demos, flagged in its annotation text
   (`synthetic`), never shown as evidence in a graded run.
2. **Check the seed issue.** Run the same case under two `PYTHONHASHSEED` values and
   compare the `signal.*` annotations. If they differ, seed from a stable digest (e.g.
   `zlib.crc32(uid)`).
3. **One case where PatchTST matters** (tier B, signal: metrics). A slow memory leak:
   nothing in events or alerts yet, pods still Ready. The only evidence is a real series
   (fixture) whose trend PatchTST flags on the medium horizon. Ground truth: the leak and
   the deployment. Decoy: the healthy CPU series. This is the case the "early signal"
   claim needs.
4. **One case through the push path**: a `SignalAlert` fixture → `signal_mapper` →
   session → verdict, offline, so the webhook contract is tested on every run, not only
   live.
5. **Pending on the PatchTST side** (from `docs/patchtst-readiness.md`): one atomic live
   proof (a run that detects a real anomaly *and* writes a fresh KB row), and the two
   Flink fixes left uncommitted on `feat/flink-local-live` in that repo.

## Gate

- [ ] Offline runs use fixture signals or none; synthetic never graded as evidence
- [ ] Seed determinism checked (and fixed if needed)
- [ ] Memory-leak case where PatchTST is the only evidence, in the veracity report
- [ ] Push-path case (`SignalAlert` fixture → verdict) offline
- [ ] PatchTST atomic live proof + Flink fixes committed (PatchTST repo)

## Next step

The determinism check (item 2): one test, cheap, and it says whether every pre-LLM score
recorded so far is reproducible.
