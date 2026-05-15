# ── Stage 1: build dependencies ───────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps needed to compile faiss-cpu, sentence-transformers, and streamlit wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libopenblas-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: runtime image ─────────────────────────────────────────────────────
FROM python:3.11-slim

LABEL org.opencontainers.image.source="https://github.com/your-org/kubewhisperer"
LABEL org.opencontainers.image.description="KubeWhisperer — local air-gapped Kubernetes RCA"
LABEL org.opencontainers.image.licenses="MIT"

# libopenblas is required at runtime by faiss-cpu
RUN apt-get update && apt-get install -y --no-install-recommends \
        libopenblas0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy application source
COPY config.py         ./
COPY main.py           ./
COPY ontology/         ./ontology/
COPY ingestion/        ./ingestion/
COPY dedup/            ./dedup/
COPY vectorstore/      ./vectorstore/
COPY rca/              ./rca/
COPY llm/              ./llm/
COPY ui/               ./ui/

# Pre-download the embedding model so the image is self-contained
# Remove this RUN if you want to mount a model cache volume instead.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Sentence-transformers cache
ENV TRANSFORMERS_CACHE=/app/.cache/huggingface
ENV HF_HOME=/app/.cache/huggingface

# Defaults — override via ConfigMap / env in k8s manifests
ENV OLLAMA_URL=http://ollama:11434
ENV OLLAMA_MODEL=mistral
ENV VECTOR_STORE_PATH=/data/index.faiss
ENV LOG_LEVEL=INFO

VOLUME ["/data", "/root/.kube"]

EXPOSE 8501

CMD ["streamlit", "run", "ui/app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
