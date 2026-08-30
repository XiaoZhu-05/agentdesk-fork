"""检索器：vector / hybrid + 可选 Rerank，支持多查询融合。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from app.config import settings
from app.llm import embed_query
from app.rag.bm25 import BM25
from app.rag.indexer import INDEX_PATH
from app.rag.rerank import rerank
from app.rag.store import Chunk
from app.rag.store_factory import get_store

CANDIDATE_N = 20
RRF_K = 60


@dataclass
class Evidence:
    doc_id: str
    chunk_id: str
    text: str
    score: float


class Retriever:
    def __init__(self) -> None:
        self.store = get_store()
        self.store.load(INDEX_PATH)
        self.bm25 = BM25(self.store.chunks)

    @property
    def doc_ids(self) -> List[str]:
        seen = []
        for c in self.store.chunks:
            if c.doc_id not in seen:
                seen.append(c.doc_id)
        return seen

    def _vector(self, query: str, n: int, doc_ids=None) -> List[Chunk]:
        qv = embed_query(query)
        return [c for c, _ in self.store.search(qv, top_k=n, doc_ids=doc_ids)]

    def _bm25(self, query: str, n: int, doc_ids=None) -> List[Chunk]:
        return [c for c, _ in self.bm25.search(query, top_k=n, doc_ids=doc_ids)]

    @staticmethod
    def _rrf(rank_lists: List[List[Chunk]], kinds: List[str] | None = None,
             weights: dict | None = None) -> List[tuple]:
        """加权 RRF 融合：无 weights 时等价于等权 RRF（保持原行为）。"""
        scores = {}
        by_id = {}
        for idx, lst in enumerate(rank_lists):
            w = 1.0
            if weights and kinds and idx < len(kinds):
                w = float(weights.get(kinds[idx], 1.0))
            for rank, c in enumerate(lst):
                by_id[c.chunk_id] = c
                scores[c.chunk_id] = scores.get(c.chunk_id, 0.0) + w / (RRF_K + rank + 1)
        fused = sorted(scores.items(), key=lambda x: -x[1])
        return [(by_id[cid], s) for cid, s in fused]

    def retrieve(self, query: str, mode: str = "hybrid", use_rerank: bool = True,
                 top_k: int | None = None, filter_doc_ids=None,
                 weights: dict | None = None) -> List[Evidence]:
        return self.retrieve_multi([query], mode=mode, use_rerank=use_rerank,
                                   top_k=top_k, filter_doc_ids=filter_doc_ids,
                                   weights=weights)

    def retrieve_multi(self, queries: List[str], mode: str = "hybrid",
                       use_rerank: bool = True, top_k: int | None = None,
                       filter_doc_ids=None, weights: dict | None = None) -> List[Evidence]:
        k = top_k or settings.top_k
        fset = set(filter_doc_ids) if filter_doc_ids else None
        if fset:
            # 过滤集内每篇文档的 chunk 都要进上下文，top_k 至少覆盖全部
            needed = sum(1 for c in self.store.chunks if c.doc_id in fset)
            k = max(k, needed)
        rank_lists: List[List[Chunk]] = []
        rank_kinds: List[str] = []
        for q in queries:
            rank_lists.append(self._vector(q, CANDIDATE_N, doc_ids=fset))
            rank_kinds.append("vector")
            if mode == "hybrid":
                rank_lists.append(self._bm25(q, CANDIDATE_N, doc_ids=fset))
                rank_kinds.append("bm25")

        if mode == "vector" and len(rank_lists) == 1:
            fused = [(c, 1.0 / (RRF_K + i + 1)) for i, c in enumerate(rank_lists[0])]
        else:
            fused = self._rrf(rank_lists, kinds=rank_kinds, weights=weights)

        candidates = [c for c, _ in fused][:CANDIDATE_N]

        if use_rerank:
            reranked = rerank(queries[0], candidates, top_k=k)
            return [Evidence(c.doc_id, c.chunk_id, c.text, s) for c, s in reranked]
        return [Evidence(c.doc_id, c.chunk_id, c.text, s) for c, s in fused[:k]]
