# Live validation 001 — block B and the chart's release access

> Written 2026-10-25, before any action on the cluster. It lists every step of the live run of
> this PR, in order, with what each must show before the next (`docs/fix-plan-001.md`, H80).
> Nothing below may be changed once the first measured run has started, except by a dated
> amendment at the end of this file. Every action that changes the cluster needs an explicit go,
> given for that action.

## What it measures

The deterministic diagnosis against models that read the same cases
(`docs/veracity-replay.md` § Comparison and its amendments; decision rules there, unchanged).
Protocol 001's C2 and C3 are not run (protocol 001, amendment 2026-10-25). It also confirms H45
on the cluster: the chart lets kube-verdict read a Helm release, and only where it is told to.

## Block B

On the head of this PR before it is merged; every record names its commit (H79). The block
counts only when complete. It may run over several sessions, resumed with `--resume` on the
same commit; no code change between sessions — a change restarts the block on the new commit.

| Configuration | Solver | Runs per case | Cut runs |
|---|---|---|---|
| rules | `rules` | 1 | — |
| structured | `structured` | 1 | 8 (offline) |
| LLM / sLLM | `llm:mistral`, `llm:qwen2.5:3b-instruct` | 3 | 8 each |
| raw-LLM / raw-sLLM | `raw:mistral`, `raw:qwen2.5:3b-instruct` | 3 | 8 each |
| ctx-LLM / ctx-sLLM | `context:mistral`, `context:qwen2.5:3b-instruct` | 3 | 8 each |

19 native cases. Model runs: 6 configurations × 19 × 3 = 342, plus 48 cut runs.
**Cost, estimated:** about 3 min per call on the CPU-only Ollama (mistral, `docs/veracity-replay.md`
§ Practical constraints): more than one night. The first case's wall time is recorded before
the rest is started, and the estimate is replaced by it.

Commands (from the worktree at the PR head, `OLLAMA_URL` pointing at the cluster's Ollama):

```bash
python tools/veracity_replay.py --solver rules
python tools/veracity_replay.py --solver structured
python tools/veracity_replay.py --solver structured --cut auto
for m in mistral qwen2.5:3b-instruct; do
  for s in llm raw context; do
    python tools/veracity_replay.py --solver "$s:$m" --runs 3 --resume
    python tools/veracity_replay.py --solver "$s:$m" --cut auto --resume
  done
done
```

## Before the first measured run, in this order

Each step is checked and its result written in the run log (§ Records) before the next.

1. **Images pinned and imported.** Ollama
   `docker.io/ollama/ollama@sha256:2b8eecdb85aa6ea67c381c8ecffec04b2658496cf2821a8f88b0485bdff198e8`,
   curl `docker.io/curlimages/curl@sha256:58adaa4e8dca9c988bae2aba4ab3434a0bb2da16bbe3f92dec39ec7785166777`,
   loaded with `k3d image import`; the Ollama Deployment runs the pinned digest. *(go)*
2. **Ollama alone.** The hourly kube-verdict CronJob is suspended (`docs/live-k3s.md`, one Ollama
   for everything); no other replay or capture runs. *(go; resumed after the block, with a go)*
3. **Models and their digests.** `mistral` and `qwen2.5:3b-instruct` pulled; their digests read
   from Ollama and written in the log. Every record carries `model_digest`; a run whose digest
   differs from the log's is invalid.
4. **Every prompt measured with the model's tokenizer.** Each `raw:` and `context:` prompt of the
   19 cases (and of their cut runs) sent once to Ollama with `num_predict: 1` and a `num_ctx`
   above any prompt (32 768); its `prompt_eval_count` is the prompt's size in that model's tokens.
   A prompt whose size + `num_predict` + reserve exceeds `OLLAMA_NUM_CTX_MAX` (8 192) is listed
   before the block: its runs are FAIL `did_not_fit`, the ceiling is not moved. The script:
   `tools/token_measure.py --model <m>` (tested offline with a scripted HTTP answer). It builds
   the prompts with the replay's own solvers — one model call per run, each with its system
   prompt — and also lists `would_truncate`: a prompt the client sends, sized from its
   character estimate, whose measured tokens and answer exceed the `num_ctx` it sends. A
   measurement that reaches 32 768 tokens is invalid, never "fits".
5. **The positive control.** `context:` runs h001 first: if it does not pass 2 of 3, it is re-run
   once; failing again, the configuration is recorded invalid (`docs/design-choices-evidence.md`
   § 2) and no decision is drawn from it.

## H45 on the cluster

The cluster runs kube-verdict as a CronJob (`k8s/kubeverdict.yaml`), not from the chart: the
replay does not exercise H45. After block B, or before it with Ollama idle:

1. Build the image at the PR head, `k3d image import` it; `helm install` the chart in its own
   namespace with `rbac.helmReleaseNamespaces={observability}` (Prometheus is a Helm release
   there) and `config` pointing at the cluster's Ollama. *(go)*
2. One investigation on `observability`. **Pass:** `ingestion_stats.applied.releases` ≥ 1 and
   `fallback: false`; `helm.fallback: false`.
3. **Negative control:** the same with `rbac.helmReleaseNamespaces` empty (`helm upgrade`).
   **Pass:** `applied.fallback: true` and `helm.fallback: true`, the applied state reported as
   not read — never `releases: 0` with `fallback: false`. *(go)*
4. `helm uninstall`; nothing of the chart left in the cluster. *(go)*

Either control failing reopens H45; the block's results stand apart from it.

## Stop rules (fixed now)

- Ollama OOM-killed or restarted: the runs in flight are invalid; resume with `--resume` once it
  is back, nothing else changed.
- A run past `REPLAY_RUN_TIMEOUT`: killed by the harness and recorded, as the harness does.
- A write to a real store: the run is invalid (`docs/design-choices-evidence.md` § 2) and the
  block stops until the cause is found; a code fix restarts the block on the new commit.
- A threshold, case, grading keyword or prompt is never changed during the block.

## Records

- The run records, under `tests/veracity/replay-baselines/<run name>/`, committed after the
  block, with no personal path in them (checked before the commit).
- A run log in this file's § Results: the commit, the image and model digests, the token table,
  the first case's wall time, each session's start and end, every invalid run and why, the two
  H45 controls.
- The tables of `tools/compare_solvers.py` and the decisions of `docs/veracity-replay.md`
  § Comparison, written as they come out.

## After

Each with its own go: the records committed; the branch pushed; the README updated with the
numbers as they came out (a claim of `docs/design-choices-evidence.md` § 1 only once its rule
has decided); the PR merged.

## Results

Not run.

## Amendments

- **2026-10-30 — C2 is added to block B; block B is the pilot of protocol 002.** Written before
  any measured run (step 1 of § Before the first measured run done on 2026-10-29: images pinned
  and imported, nothing measured). Protocol 001's amendment 2026-10-25 deferred C2 because it
  measures a layer on the model, not the diagnosis. That question is now asked on its own:
  does a model gain from the vector search, against the deterministic diagnosis and against the
  search alone (`docs/protocol-002-retrieval-vs-models.md`). C2 is run here as its pilot, under
  protocol 001's rules, unchanged.
  - **Added configuration:** `context:mistral` and `context:qwen2.5:3b-instruct` with
    `--store faiss` (C2), 3 runs per case, 19 cases: 114 model runs. No cut runs: the
    collector-cut control asks whether a configuration is more confident without a collector,
    and C2 differs from C1 only by the search, which reads the same collectors.
  - **Commands**, after the `context:` runs of each model:
    `python tools/veracity_replay.py --solver "context:$m" --store faiss --runs 3 --resume`.
  - **Step 4 covers C2's prompts.** `tools/token_measure.py` builds the `raw:` and `context:`
    prompts without the search; it must also build C2's (CPI-26). Until it does, block B does
    not start.
  - **What C2 makes measurable:** C2 against C1 per model (protocol 001's decision rules), and
    the anchoring test (`docs/veracity-replay.md` § Amendment 2026-10-24), which needed C2.
  - **Pilot, not a decision on scale.** On 19 in-sample cases with small stores (R1: 4 to 30
    vectors), C2 decides only protocol 001's question on these cases. Its discordant pairs
    size protocol 002; they do not answer it.
  - **Cost:** 342 + 114 = 456 model runs and 48 cut runs; the estimate is replaced by the
    first case's wall time, as before.
