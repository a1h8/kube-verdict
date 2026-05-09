from __future__ import annotations
import logging

import numpy as np
from sentence_transformers import SentenceTransformer

import config as cfg

log = logging.getLogger(__name__)


class Embedder:
    """
    Thin wrapper around a sentence-transformers model.
    Produces L2-normalized vectors suitable for cosine search via FAISS IndexFlatIP.
    """

    def __init__(self, model_name: str | None = None) -> None:
        name = model_name or cfg.EMBEDDING_MODEL
        log.info("Loading embedding model: %s", name)
        self._model = SentenceTransformer(name)
        self.dim: int = self._model.get_sentence_embedding_dimension()
        log.info("Embedding dimension: %d", self.dim)

    def embed(self, texts: list[str]) -> np.ndarray:
        """
        Returns a float32 array of shape (len(texts), dim), L2-normalized.
        """
        if not texts:
            return np.empty((0, self.dim), dtype=np.float32)
        vectors = self._model.encode(
            texts,
            convert_to_numpy=True,
            show_progress_bar=False,
            normalize_embeddings=True,   # L2-norm → cosine via inner product
        )
        return vectors.astype(np.float32)

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]
