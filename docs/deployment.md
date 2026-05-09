# Deployment

## Option A — K3s (recommended, single-node, fully local)

The `scripts/init-k3s.sh` script handles everything end-to-end:

```bash
# 1. Clone the repo on the target machine
git clone https://github.com/your-org/kubewhisperer.git
cd kubewhisperer

# 2. Build the Docker image
docker build -t ghcr.io/your-org/kubewhisperer:latest .

# 3. Load the image into K3s (no registry needed)
docker save ghcr.io/your-org/kubewhisperer:latest | \
  sudo k3s ctr images import -

# 4. Run the init script (installs K3s, Ollama, Mistral, KubeWhisperer)
sudo bash scripts/init-k3s.sh --image ghcr.io/your-org/kubewhisperer:latest
```

After ~5 minutes (model download) the CronJob runs every hour automatically.

### Manual model pull (if the Job failed)

```bash
kubectl exec -n kubewhisperer deploy/ollama -- ollama pull mistral
```

### Trigger an ad-hoc analysis

```bash
kubectl create job --from=cronjob/kubewhisperer kw-adhoc -n kubewhisperer
kubectl logs -n kubewhisperer -l app=kubewhisperer -f
```

### Watch scheduled runs

```bash
kubectl get jobs -n kubewhisperer -w
kubectl logs -n kubewhisperer job/<job-name>
```

---

## Option B — Existing K8s cluster (any distribution)

### Prerequisites

- `kubectl` configured and pointing at the target cluster
- The cluster must be able to reach an Ollama endpoint (in-cluster or external)
- A `local-path` or equivalent storage class (for PVCs)

### Step 1 — Build and push the image

```bash
docker build -t your-registry/kubewhisperer:latest .
docker push your-registry/kubewhisperer:latest
```

### Step 2 — Create namespace and RBAC

```bash
kubectl create namespace kubewhisperer
kubectl apply -f k8s/rbac.yaml
```

### Step 3 — Deploy Ollama (skip if you have an external Ollama)

```bash
kubectl apply -f k8s/ollama.yaml
kubectl rollout status deployment/ollama -n kubewhisperer
# Pull Mistral (~4 GB — wait for the Job to complete)
kubectl wait job/ollama-pull-mistral -n kubewhisperer --for=condition=complete --timeout=600s
```

### Step 4 — Deploy KubeWhisperer

Edit `k8s/kubewhisperer.yaml` and replace `ghcr.io/your-org/kubewhisperer:latest` with your image.

```bash
kubectl apply -f k8s/kubewhisperer.yaml
```

---

## Option C — Local development (no cluster required for tests)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run all tests
pytest

# Run against your current kubeconfig context
python main.py --query "pods are crashlooping in production" --stream
```

---

## Storage layout

| Mount | Content |
|---|---|
| `/data/index.faiss` | FAISS index (rebuilt on each CronJob run) |
| `/data/index.meta.pkl` | FAISS metadata (parallel to index rows) |
| `/root/.ollama/models` | Mistral model weights (~4 GB, persisted in `ollama-models` PVC) |
| `/root/.kube/config` | Kubeconfig (not needed in-cluster — ServiceAccount is used) |

---

## RBAC scope

KubeWhisperer only needs **read** access. The ClusterRole in `k8s/rbac.yaml` grants:

- `get`, `list`, `watch` on all core resource types
- `get`, `list`, `watch` on `apps`, `batch`, `networking.k8s.io`, `autoscaling`
- Non-resource URL access to `/api`, `/apis`, `/version` (required for dynamic API discovery)
- No `create`, `update`, `patch`, `delete` permissions anywhere

---

## GPU support

Ollama can use NVIDIA GPUs. Uncomment the `nodeSelector` and `tolerations` blocks in
`k8s/ollama.yaml` and adjust the resource limits:

```yaml
resources:
  limits:
    nvidia.com/gpu: "1"
    memory: 8Gi
```

For K3s with NVIDIA, install the GPU operator first:
```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm install gpu-operator nvidia/gpu-operator -n gpu-operator --create-namespace
```

---

## Updating the model

To switch from `mistral` to another model (e.g. `llama3`):

1. Edit the `OLLAMA_MODEL` key in the `kubewhisperer-config` ConfigMap.
2. Pull the new model: `kubectl exec -n kubewhisperer deploy/ollama -- ollama pull llama3`
3. The next CronJob run will use the new model automatically.
