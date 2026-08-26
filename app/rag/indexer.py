"""文档切割 + 建索引。第 1 周用递归字符切割（按段落优先）。"""
from __future__ import annotations

import os
from typing import List

from app.config import settings
from app.llm import embed_texts
from app.rag.store import Chunk
from app.rag.store_factory import get_store
from app.rag.extractors import extract_text, SUPPORTED_EXTS

INDEX_PATH = "data/index/store.json"


def split_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    """简单按字符切割，带 overlap。后续可升级为语义边界切割。"""
    text = text.strip()
    if not text:
        return []
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return chunks


def build_index(docs_dir: str = "data/docs"):
    store = get_store()
    all_chunks: List[Chunk] = []
    for fname in sorted(os.listdir(docs_dir)):
        path = os.path.join(docs_dir, fname)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(fname)[1].lower() not in SUPPORTED_EXTS:
            continue
        try:
            content = extract_text(path)
        except Exception as e:  # 单个文件解析失败不阻断整体建索引
            print(f"[indexer] skip {fname}: {e}")
            continue
        if not content.strip():
            continue
        pieces = split_text(content, settings.chunk_size, settings.chunk_overlap)
        for i, piece in enumerate(pieces):
            all_chunks.append(Chunk(doc_id=fname, chunk_id=f"{fname}#{i}", text=piece, embedding=[]))

    # 批量做 embedding
    embeddings = embed_texts([c.text for c in all_chunks])
    for c, emb in zip(all_chunks, embeddings):
        c.embedding = emb

    store.add(all_chunks)
    store.save(INDEX_PATH)
    return store
