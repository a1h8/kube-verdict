from __future__ import annotations
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=False)


def _list(key: str, default: str = "") -> list[str]:
    raw = os.getenv(key, default)
    return [v.strip() for v in raw.split(",") if v.strip()]


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


# ── Kubernetes ─────────────────────────────────────────────────────────────────
KUBECONFIG: str | None = os.getenv("KUBECONFIG") or None
KUBE_CONTEXT: str | None = os.getenv("KUBE_CONTEXT") or None
KUBE_NAMESPACES: list[str] = _list("KUBE_NAMESPACES")           # empty = all
KUBE_SKIP_KINDS: set[str] = set(_list("KUBE_SKIP_KINDS", "Event,Endpoints,EndpointSlice"))

# ── Helm ───────────────────────────────────────────────────────────────────────
HELMFILE_PATH: str | None = os.getenv("HELMFILE_PATH") or None
HELMFILE_ENVIRONMENT: str = os.getenv("HELMFILE_ENVIRONMENT", "default")
HELMFILE_USE_CLI: bool = os.getenv("HELMFILE_USE_CLI", "false").lower() == "true"

# ── GitOps ─────────────────────────────────────────────────────────────────────
GITOPS_ENABLED: bool = os.getenv("GITOPS_ENABLED", "false").lower() == "true"
GITOPS_REPO_URL: str | None = os.getenv("GITOPS_REPO_URL") or None
GITOPS_BRANCH: str = os.getenv("GITOPS_BRANCH", "main")
GITOPS_CHARTS_PATH: str = os.getenv("GITOPS_CHARTS_PATH", "charts")
GITHUB_TOKEN: str | None = os.getenv("GITHUB_TOKEN") or None

# ── OpenTelemetry (traces) ─────────────────────────────────────────────────────
OTEL_ENABLED: bool = os.getenv("OTEL_ENABLED", "false").lower() == "true"
OTEL_BACKEND_TYPE: str = os.getenv("OTEL_BACKEND_TYPE", "tempo")   # tempo | jaeger
OTEL_BACKEND_URL: str = os.getenv("OTEL_BACKEND_URL", "http://localhost:3100")
OTEL_TOKEN: str | None = os.getenv("OTEL_TOKEN") or None
OTEL_LOOKBACK_HOURS: int = _int("OTEL_LOOKBACK_HOURS", 1)
OTEL_TIMEOUT: int = _int("OTEL_TIMEOUT", 30)

# ── Loki (logs) ────────────────────────────────────────────────────────────────
LOKI_ENABLED: bool = os.getenv("LOKI_ENABLED", "false").lower() == "true"
LOKI_URL: str = os.getenv("LOKI_URL", "http://localhost:3100")
LOKI_TOKEN: str | None = os.getenv("LOKI_TOKEN") or None
LOKI_LOOKBACK_HOURS: int = _int("LOKI_LOOKBACK_HOURS", 1)
LOKI_TIMEOUT: int = _int("LOKI_TIMEOUT", 30)
LOKI_MAX_LOGS_PER_POD: int = _int("LOKI_MAX_LOGS_PER_POD", 20)

# ── Prometheus ──────────────────────────────────────────────────────���──────────
PROMETHEUS_ENABLED: bool = os.getenv("PROMETHEUS_ENABLED", "false").lower() == "true"
PROMETHEUS_URL: str = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
PROMETHEUS_TOKEN: str | None = os.getenv("PROMETHEUS_TOKEN") or None
PROMETHEUS_TIMEOUT: int = _int("PROMETHEUS_TIMEOUT", 30)

# ── Ollama / Mistral ────────────────────────────────────────────────────────────
OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "mistral")
OLLAMA_TIMEOUT: int = _int("OLLAMA_TIMEOUT", 120)

# ── Vector store ───────────────────────────────────────────────────────────────
VECTOR_STORE_TYPE: str = os.getenv("VECTOR_STORE_TYPE", "faiss")
VECTOR_STORE_PATH: Path = Path(os.getenv("VECTOR_STORE_PATH", "./data/index.faiss"))
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

# ── Deduplication ──────────────────────────────────────────────────────────────
BFS_MAX_DEPTH: int = _int("BFS_MAX_DEPTH", 3)
JACCARD_THRESHOLD: float = _float("JACCARD_THRESHOLD", 0.7)
TFIDF_TOP_K: int = _int("TFIDF_TOP_K", 20)
TFIDF_NGRAM_MAX: int = _int("TFIDF_NGRAM_MAX", 3)   # (1, N) — 3 = trigrams

# ── Runtime ────────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
