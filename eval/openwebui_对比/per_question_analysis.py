"""逐题对比：字符 vs 结构感知切分下，金标文档/金标 chunk 的检索排名。

用法：python -B eval/openwebui_对比/per_question_analysis.py
输出：eval/openwebui_对比/per_question_compare.csv
"""
from __future__ import annotations

import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

os.environ.setdefault("RERANK_MODEL", "")

from app.llm import embed_texts
from app.rag.bm25 import BM25
from app.rag.store import VectorStore

BASE = os.path.dirname(os.path.abspath(__file__))
DATASET = os.path.join(BASE, "dataset.jsonl")
RRF_K = 60
CANDIDATE_N = 20


def load_store(path: str) -> VectorStore:
    s = VectorStore()
    s.load(path)
    return s


def rrf(rank_lists):
    scores, by_id = {}, {}
    for lst in rank_lists:
        for rank, (c, _) in enumerate(lst):
            by_id[c.chunk_id] = c
            scores[c.chunk_id] = scores.get(c.chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    return [(by_id[cid], s) for cid, s in sorted(scores.items(), key=lambda x: -x[1])]


def rank_of(store: VectorStore, bm25: BM25, q: str, doc_id: str, chunk_id: str | None):
    qv = embed_texts([q])[0]
    fused = rrf([store.search(qv, top_k=CANDIDATE_N), bm25.search(q, top_k=CANDIDATE_N)])
    doc_rank = chunk_rank = 0
    seen_docs = []
    for i, (c, _) in enumerate(fused, 1):
        if c.doc_id not in seen_docs:
            seen_docs.append(c.doc_id)
            if c.doc_id == doc_id and doc_rank == 0:
                doc_rank = len(seen_docs)
        if chunk_id and c.chunk_id == chunk_id and chunk_rank == 0:
            chunk_rank = i
    return doc_rank, chunk_rank


def main() -> None:
    rows = [json.loads(l) for l in open(DATASET, encoding="utf-8") if l.strip()]
    char_store = load_store(os.path.join(BASE, "store_character.json"))
    struct_store = load_store(os.path.join(BASE, "store_structure.json"))
    bm_c, bm_s = BM25(char_store.chunks), BM25(struct_store.chunks)
    out = []
    for r in rows:
        q, doc, ans = r["question"], r["doc_id"], r["answer"]
        gc = next((c.chunk_id for c in char_store.chunks if c.doc_id == doc and ans in c.text), None)
        gs = next((c.chunk_id for c in struct_store.chunks if c.doc_id == doc and ans in c.text), None)
        dc, cc = rank_of(char_store, bm_c, q, doc, gc) if gc else (0, 0)
        ds, cs = rank_of(struct_store, bm_s, q, doc, gs) if gs else (0, 0)
        out.append({
            "question": q, "doc_id": doc,
            "char_doc_rank": dc, "struct_doc_rank": ds,
            "char_chunk_rank": cc, "struct_chunk_rank": cs,
            "char_cross": 1 if gc is None else 0,
            "struct_cross": 1 if gs is None else 0,
        })
    path = os.path.join(BASE, "per_question_compare.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    imp = [o for o in out if o["struct_chunk_rank"] and (not o["char_chunk_rank"] or o["struct_chunk_rank"] < o["char_chunk_rank"])]
    reg = [o for o in out if o["char_chunk_rank"] and (not o["struct_chunk_rank"] or o["struct_chunk_rank"] > o["char_chunk_rank"])]
    print(f"total={len(out)} chunk_improved={len(imp)} chunk_regressed={len(reg)}")
    print(f"char_cross={sum(o['char_cross'] for o in out)} struct_cross={sum(o['struct_cross'] for o in out)}")
    for o in imp[:5]:
        print(f"  + {o['doc_id']} | {o['question'][:36]} | char_chunk#{o['char_chunk_rank']} -> struct_chunk#{o['struct_chunk_rank']}")
    for o in reg[:5]:
        print(f"  - {o['doc_id']} | {o['question'][:36]} | char_chunk#{o['char_chunk_rank']} -> struct_chunk#{o['struct_chunk_rank']}")
    print("->", path)


if __name__ == "__main__":
    main()
