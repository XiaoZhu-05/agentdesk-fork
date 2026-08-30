"""openwebui 文档语料：字符切分 vs 结构感知切分的检索对比评测。

用法：python -B eval/openwebui_对比/run_compare.py
依赖：eval/openwebui_对比/dataset.jsonl（由 generate_dataset.py 生成）
输出：store_character.json / store_structure.json、results_character.json / results_structure.json
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

os.environ.setdefault("RERANK_MODEL", "")  # 对比切分方式用离线重排即可，避免 CPU cross-encoder 拖慢

from app.llm import embed_texts
from app.rag.bm25 import BM25
from app.rag.extractors import extract_text, SUPPORTED_EXTS
from app.rag.indexer import StructureAwareTextSplitter, _split_character
from app.rag.store import Chunk, VectorStore

BASE = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(BASE, "docs")
DATASET = os.path.join(BASE, "dataset.jsonl")
CHUNK_SIZE, OVERLAP = 512, 64
RRF_K = 60
CANDIDATE_N = 20
TOP_K = 10


def load_docs() -> dict[str, str]:
    docs = {}
    for f in sorted(os.listdir(DOCS_DIR)):
        path = os.path.join(DOCS_DIR, f)
        if not os.path.isfile(path) or os.path.splitext(f)[1].lower() not in SUPPORTED_EXTS:
            continue
        try:
            text = extract_text(path)
        except Exception:  # noqa: BLE001
            continue
        if text.strip():
            docs[f] = text
    return docs


def build_store(docs: dict[str, str], splitter) -> VectorStore:
    chunks: list[Chunk] = []
    for doc_id, text in docs.items():
        pieces = splitter(text, CHUNK_SIZE, OVERLAP)
        for i, piece in enumerate(pieces):
            chunks.append(Chunk(doc_id=doc_id, chunk_id=f"{doc_id}#{i}", text=piece, embedding=[]))
    embs = embed_texts([c.text for c in chunks])
    for c, e in zip(chunks, embs):
        c.embedding = e
    store = VectorStore()
    store.add(chunks)
    return store


def rrf(rank_lists: list[list[tuple[Chunk, float]]]) -> list[tuple[Chunk, float]]:
    scores: dict[str, float] = {}
    by_id: dict[str, Chunk] = {}
    for lst in rank_lists:
        for rank, (c, _) in enumerate(lst):
            by_id[c.chunk_id] = c
            scores[c.chunk_id] = scores.get(c.chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    return [(by_id[cid], s) for cid, s in sorted(scores.items(), key=lambda x: -x[1])]


def locate_gold_chunk(store: VectorStore, doc_id: str, answer: str) -> str | None:
    """返回包含 answer 起始位置的 chunk_id（answer 必须逐字存在于该 doc 文本）。"""
    for c in store.chunks:
        if c.doc_id == doc_id and answer in c.text:
            return c.chunk_id
    return None


def evaluate(store: VectorStore, rows: list[dict], tag: str) -> dict:
    bm25 = BM25(store.chunks)
    out = {"doc_hit1": 0, "doc_hit3": 0, "doc_hit5": 0, "mrr5": 0.0,
           "chunk_hit1": 0, "chunk_hit3": 0, "chunk_hit5": 0, "answer_cross": 0, "n_valid": 0}
    for row in rows:
        q = row["question"]
        gold_doc = row["doc_id"]
        answer = row["answer"]
        gold_chunk = locate_gold_chunk(store, gold_doc, answer)
        if gold_chunk is None:
            out["answer_cross"] += 1  # 答案跨 chunk 边界（单块装不下完整答案）
            continue
        out["n_valid"] += 1
        qv = embed_texts([q])[0]
        vec = store.search(qv, top_k=CANDIDATE_N)
        lex = bm25.search(q, top_k=CANDIDATE_N)
        fused = rrf([vec, lex])[:TOP_K]
        docs_ranked = []
        seen = set()
        for c, _ in fused:
            if c.doc_id not in seen:
                seen.add(c.doc_id)
                docs_ranked.append(c.doc_id)
        chunk_ids = [c.chunk_id for c, _ in fused]
        # doc 级
        if gold_doc in docs_ranked[:1]:
            out["doc_hit1"] += 1
        if gold_doc in docs_ranked[:3]:
            out["doc_hit3"] += 1
        if gold_doc in docs_ranked[:5]:
            out["doc_hit5"] += 1
            out["mrr5"] += 1.0 / (docs_ranked.index(gold_doc) + 1)
        # chunk 级
        if gold_chunk in chunk_ids[:1]:
            out["chunk_hit1"] += 1
        if gold_chunk in chunk_ids[:3]:
            out["chunk_hit3"] += 1
        if gold_chunk in chunk_ids[:5]:
            out["chunk_hit5"] += 1
    n = out["n_valid"]
    for k in ("doc_hit1", "doc_hit3", "doc_hit5", "chunk_hit1", "chunk_hit3", "chunk_hit5"):
        out[k] = round(out[k] / max(1, n), 4)
    out["mrr5"] = round(out["mrr5"] / max(1, n), 4)
    return out


def main() -> None:
    docs = load_docs()
    print(f"docs: {len(docs)}")
    rows = []
    with open(DATASET, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    print(f"questions: {len(rows)}")

    char_store = build_store(docs, lambda t, cs, ov: _split_character(t, cs, ov))
    char_store.save(os.path.join(BASE, "store_character.json"))
    struct_store = build_store(docs, lambda t, cs, ov: StructureAwareTextSplitter(cs, ov).split_text(t))
    struct_store.save(os.path.join(BASE, "store_structure.json"))
    print(f"chunks: character={len(char_store)} structure={len(struct_store)}")

    res_c = evaluate(char_store, rows, "character")
    res_s = evaluate(struct_store, rows, "structure")
    # 公平口径：只统计两套索引都找到金标块的问题
    common = 0
    for r in rows:
        gc = locate_gold_chunk(char_store, r["doc_id"], r["answer"])
        gs = locate_gold_chunk(struct_store, r["doc_id"], r["answer"])
        if gc is not None and gs is not None:
            common += 1
    print(f"common valid questions (both indexes): {common}")
    for tag, res in (("character", res_c), ("structure", res_s)):
        res["n_questions"] = len(rows)
        res["n_common_valid"] = common
        with open(os.path.join(BASE, f"results_{tag}.json"), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
    print()
    print(f"{'指标':<20}{'字符切分':>12}{'结构感知':>12}")
    for k in ("n_questions", "n_common_valid", "answer_cross", "doc_hit1", "doc_hit3",
              "doc_hit5", "mrr5", "chunk_hit1", "chunk_hit3", "chunk_hit5"):
        print(f"{k:<16}{res_c[k]:>12}{res_s[k]:>12}")


if __name__ == "__main__":
    main()
