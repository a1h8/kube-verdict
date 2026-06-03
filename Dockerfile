# ── Stage 1: build dependencies ───────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps needed to compile faiss-cpu, sentence-transformers, and streamlit wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libopenblas-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# CPU-only torch: make the PyTorch CPU index primary (PyPI as fallback for everything
# else) so the +cpu wheel wins and no CUDA/nvidia wheels are pulled. ~6GB → ~1.5GB.
RUN pip install --no-cache-dir --prefix=/install \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://pypi.org/simple \
        -r requirements.txt

# ── Stage 2: runtime image ─────────────────────────────────────────────────────
FROM python:3.11-slim

LABEL org.opencontainers.image.source="https://github.com/a1h8/kube-verdict"
LABEL org.opencontainers.image.description="KubeVerdict — evidence-first Kubernetes incident decision engine"
LABEL org.opencontainers.image.licenses="Apache-2.0"

# libopenblas is required at runtime by faiss-cpu
RUN apt-get update && apt-get install -y --no-install-recommends \
        libopenblas0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy application source — every package imported on the streamlit/API runtime path.
# (cases/ is test-fixture data and dashboard/ is a separate JS build — neither is needed here.)
COPY config.py         ./
COPY main.py           ./
COPY mcp_server.py     ./
COPY api/              ./api/
COPY decision/         ./decision/
COPY dedup/            ./dedup/
COPY ingestion/        ./ingestion/
COPY knowledge/        ./knowledge/
COPY llm/              ./llm/
COPY ontology/         ./ontology/
COPY persistence/      ./persistence/
COPY rca/              ./rca/
COPY reasoning/        ./reasoning/
COPY remediation/      ./remediation/
COPY signals/          ./signals/
COPY ui/               ./ui/
COPY vectorstore/      ./vectorstore/
COPY workflow/         ./workflow/

# Sentence-transformers / HF cache — set BEFORE the download so the weights
# land in this dir (the previous order downloaded to the default ~/.cache).
ENV TRANSFORMERS_CACHE=/app/.cache/huggingface
ENV HF_HOME=/app/.cache/huggingface
ENV HF_HUB_DOWNLOAD_TIMEOUT=60

# Pre-download the embedding model so the image is self-contained (air-gapped).
# Retried with backoff because huggingface.co rate-limits CI egress IPs — a
# single attempt fails the GHCR build. The trailing attempt (no `|| true`) keeps
# a genuinely persistent failure loud instead of shipping a model-less image.
# Remove this block if you want to mount a model cache volume instead.
RUN for i in 1 2 3 4 5 6; do \
      python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')" && break; \
      echo "HF model fetch attempt $i failed — backing off"; sleep $((i * 20)); \
    done; \
    python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Defaults — override via ConfigMap / env in k8s manifests
ENV OLLAMA_URL=http://ollama:11434
ENV OLLAMA_MODEL=mistral
ENV VECTOR_STORE_PATH=/data/index.faiss
ENV LOG_LEVEL=INFO

VOLUME ["/data", "/root/.kube"]

# API-first: the default process is the FastAPI service (the IDP capability surface).
EXPOSE 8000

CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]

# Alternative surfaces (override CMD):
#   Streamlit UI:  streamlit run ui/app.py --server.port=8501 --server.address=0.0.0.0 --server.headless=true
#   MCP server:    python mcp_server.py
