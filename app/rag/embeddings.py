"""Embedding backends.

Primary: Sentence Transformers multilingual model (supports Thai).
Fallback: TF-IDF over character n-grams (works offline; Thai has no word spaces,
so character n-grams are a reasonable lexical representation).

The backend actually in use is reported by GET /health so it is never hidden.
"""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)


class Embedder:
    name: str = "base"

    def fit(self, texts: list[str]) -> None:  # only needed by fitted backends
        pass

    def encode(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError


class SentenceTransformerEmbedder(Embedder):
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # heavy import, lazy

        self.model = SentenceTransformer(model_name)
        self.name = f"sentence-transformers:{model_name}"

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = self.model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return np.asarray(vecs, dtype="float32")


class TfidfEmbedder(Embedder):
    def __init__(self):
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)
        self.name = "tfidf-char-ngram"
        self._fitted = False

    def fit(self, texts: list[str]) -> None:
        self.vectorizer.fit(texts)
        self._fitted = True

    def encode(self, texts: list[str]) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("TfidfEmbedder.fit() must be called before encode()")
        vecs = self.vectorizer.transform(texts).toarray().astype("float32")
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms


def make_embedder(backend: str, model_name: str) -> Embedder:
    backend = backend.lower()
    if backend in {"sentence-transformers", "st"}:
        return SentenceTransformerEmbedder(model_name)
    if backend == "tfidf":
        return TfidfEmbedder()
    # auto: try the multilingual model, fall back to TF-IDF if it cannot load
    try:
        return SentenceTransformerEmbedder(model_name)
    except Exception as exc:  # ImportError, network/model download errors
        log.warning("Sentence Transformers unavailable (%s); falling back to TF-IDF", exc)
        return TfidfEmbedder()
