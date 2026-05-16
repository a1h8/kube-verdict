# Configuration

All settings are read from `.env` (or environment variables). Copy `.env.example` to `.env` and edit.

```bash
cp .env.example .env
```

## Kubernetes

| Variable | Default | Description |
|---|---|---|
| `KUBECONFIG` | `~/.kube/config` | Path to kubeconfig file. In-cluster runs use the pod's ServiceAccount automatically. |
| `KUBE_CONTEXT` | _(current context)_ | Kubeconfig context to use. Leave empty to use the active context. |
| `KUBE_NAMESPACES` | _(all)_ | Comma-separated namespaces to collect. Empty = all namespaces. |
| `KUBE_SKIP_KINDS` | `Event,Endpoints,EndpointSlice` | Resource kinds to skip during dynamic discovery. |

## Helm / Helmfile

| Variable | Default | Description |
|---|---|---|
| `HELMFILE_PATH` | _(none)_ | Path to `helmfile.yaml` or a `helmfile.d/` directory. Leave empty to skip Helmfile ingestion. |
| `HELMFILE_ENVIRONMENT` | `default` | Helmfile environment to resolve environment-specific values for. |
| `HELMFILE_USE_CLI` | `false` | Set to `true` to run `helmfile build` for full Go-template rendering. Requires `helmfile` binary. |

## Ollama / Mistral

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API base URL. Set to `http://ollama:11434` in K8s. |
| `OLLAMA_MODEL` | `mistral` | Model to use. Any Ollama-compatible model works (e.g. `llama3`, `mixtral`). |
| `OLLAMA_TIMEOUT` | `120` | HTTP timeout in seconds for LLM calls. Increase for large contexts. |

## Vector store

| Variable | Default | Description |
|---|---|---|
| `VECTOR_STORE_PATH` | `./data/index.faiss` | Path to persist the FAISS index. Mount a PVC here in K8s. |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformers model name. Must match the model used to build the index. |

## Deduplication

| Variable | Default | Description |
|---|---|---|
| `BFS_MAX_DEPTH` | `3` | Maximum BFS hops from seed entities when expanding incident context. |
| `JACCARD_THRESHOLD` | `0.7` | Token overlap ratio above which a chunk is considered a duplicate and discarded. |
| `TFIDF_TOP_K` | `20` | Maximum number of context chunks passed to the LLM after ranking. |
| `TFIDF_NGRAM_MAX` | `3` | Upper bound of TF-IDF n-gram range. `3` = trigrams (recommended). |

## Hybrid retrieval (BM25 + FAISS → RRF)

| Variable | Default | Description |
|---|---|---|
| `RRF_K` | `60` | RRF damping constant from the original paper. Higher = more uniform fusion. |
| `RRF_FETCH_MULTIPLIER` | `3` | Over-fetch factor per source before RRF fusion (fetch `top_k × N`, fuse to `top_k`). |

## Document source weights

Applied as a score multiplier in `FAISSStore.hybrid_search()`. Override any source with `SOURCE_WEIGHT_<SOURCE_UPPER>=<float>` in `.env`.

| Variable | Default | Source | Rationale |
|---|---|---|---|
| `SOURCE_WEIGHT_CLUSTER` | `1.0` | Live K8s entities | Baseline |
| `SOURCE_WEIGHT_OFFICIAL` | `1.0` | K8s/Helm upstream docs | Baseline |
| `SOURCE_WEIGHT_EXAMPLE` | `1.2` | Past resolved incidents | Proven resolutions slightly favoured |
| `SOURCE_WEIGHT_ANCHOR` | `1.6` | Manifest drift violations | Strong diagnostic signal — declared ≠ observed |
| `SOURCE_WEIGHT_ENTERPRISE` | `1.5` | Internal runbooks/SOPs | Organisation-specific knowledge |
| `SOURCE_WEIGHT_RUNBOOK` | `1.8` | Operational procedures | Highest trust: explicit remediation steps |

## Persistence

| Variable | Default | Description |
|---|---|---|
| `KUBEWHISPERER_DB` | `kubewhisperer.db` | Path to the SQLite database. Stores session metadata, LangGraph checkpoints, and raw entity texts for FAISS reconstruction. Swap for a Postgres URL when scaling beyond a single pod. |

## Runtime

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Python logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |

## CLI flags (override .env)

```
python main.py --help

  --namespace, -n     Namespace to analyse (repeatable)
  --kubeconfig        Path to kubeconfig
  --context           Kubeconfig context
  --query, -q         Incident description (required)
  --helmfile          Path to helmfile.yaml or helmfile.d/
  --helm-environment  Helmfile environment
  --load-index        Skip collection, load existing FAISS index
  --stream            Stream Mistral output token by token
```

## In-cluster (K8s ConfigMap)

All variables can be set in the `kubewhisperer-config` ConfigMap (see `k8s/kubewhisperer.yaml`).
The pod mounts the ConfigMap via `envFrom`, so no secrets are baked into the image.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: kubewhisperer-config
  namespace: kubewhisperer
data:
  OLLAMA_URL: "http://ollama:11434"
  TFIDF_TOP_K: "25"
  LOG_LEVEL: "DEBUG"
```
