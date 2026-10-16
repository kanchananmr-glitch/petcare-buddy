"""FAISS vector store and retriever for the Knowledge Base chunks."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import faiss
import numpy as np

from app.rag.embeddings import Embedder
from app.rag.loader import Chunk, kb_fingerprint

log = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    chunk: Chunk
    score: float


class VectorStore:
    """Inner-product FAISS index over L2-normalized vectors (= cosine similarity)."""

    def __init__(self, embedder: Embedder, index_dir: Path | None = None):
        self.embedder = embedder
        self.index_dir = Path(index_dir) if index_dir else None
        self.index: faiss.Index | None = None
        self.chunks: list[Chunk] = []
        self.fingerprint: str | None = None
        self.loaded_from_disk = False

    # ------------------------------------------------------------ build / persist
    def build(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        texts = [c.text for c in chunks]
        self.embedder.fit(texts)  # no-op for sentence-transformers
        self.fingerprint = kb_fingerprint(chunks, self.embedder.name)

        if self._try_load():
            self.loaded_from_disk = True
            return
        vectors = self.embedder.encode(texts)
        self.index = faiss.IndexFlatIP(vectors.shape[1])
        self.index.add(vectors)
        self._save()

    def _paths(self):
        assert self.index_dir is not None
        return self.index_dir / "faiss.index", self.index_dir / "meta.json"

    def _try_load(self) -> bool:
        if not self.index_dir:
            return False
        index_path, meta_path = self._paths()
        if not (index_path.exists() and meta_path.exists()):
            return False
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("fingerprint") != self.fingerprint:
                return False
            self.index = faiss.read_index(str(index_path))
            return self.index.ntotal == len(self.chunks)
        except Exception as exc:  # corrupt cache -> rebuild
            log.warning("Could not load FAISS index (%s); rebuilding", exc)
            return False

    def _save(self) -> None:
        if not self.index_dir:
            return
        self.index_dir.mkdir(parents=True, exist_ok=True)
        index_path, meta_path = self._paths()
        faiss.write_index(self.index, str(index_path))
        meta = {
            "fingerprint": self.fingerprint,
            "embedder": self.embedder.name,
            "dimension": self.index.d,
            "count": self.index.ntotal,
            "chunk_ids": [c.id for c in self.chunks],
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # ------------------------------------------------------------ search
    def search(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        if self.index is None:
            raise RuntimeError("Vector store has not been built")
        qv = self.embedder.encode([query])
        k = min(k, self.index.ntotal)
        scores, idxs = self.index.search(qv, k)
        return [
            RetrievedChunk(self.chunks[i], float(s)) for s, i in zip(scores[0], idxs[0]) if i >= 0
        ]

    def get(self, chunk_id: str) -> Chunk | None:
        return next((c for c in self.chunks if c.id == chunk_id), None)


class Retriever:
    """Retrieves top-k chunks and optionally pins chunks that must always be present."""

    def __init__(self, store: VectorStore, top_k: int = 5):
        self.store = store
        self.top_k = top_k

    def retrieve(self, query: str, k: int | None = None, pinned: list[str] | None = None) -> list[RetrievedChunk]:
        results = self.store.search(query, k or self.top_k)
        seen = {r.chunk.id for r in results}
        for cid in pinned or []:
            chunk = self.store.get(cid)
            if chunk and cid not in seen:
                results.append(RetrievedChunk(chunk, 0.0))
                seen.add(cid)
        return results
