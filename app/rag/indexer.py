"""文档切分 + 建索引。

切分策略为结构感知（参考 open-webui 的 StructureAwareTextSplitter）：
- 表格块整行保留，按行分组到 chunk_size，不切断表格行；
- 代码块整体保留，超过 chunk_size 才在 class/function 边界切；
- 普通文本按段落 / 句子（中文标点）递归切分，避免中文断句；
- 可用 RAG_TEXT_SPLITTER=character 回退到旧的字符定长切分做 A/B。
"""
from __future__ import annotations

import os
from typing import List

from app.config import settings
from app.llm import embed_texts
from app.rag.store import Chunk
from app.rag.store_factory import get_store
from app.rag.extractors import extract_text, SUPPORTED_EXTS

INDEX_PATH = "data/index/store.json"

_FENCE = "```"

_TEXT_SEPARATORS = ["\n\n", "\n", "。", "；", "！", "？", "，", " ", ""]

_CODE_SEPARATORS = [
    "\nclass ",
    "\ndef ",
    "\n    def ",
    "\nasync def ",
    "\n    async def ",
    "\nfunction ",
    "\n    function ",
    "\n@",
    "\n\n",
    "\n",
    " ",
    "",
]


def _split_character(text: str, chunk_size: int, overlap: int) -> List[str]:
    """旧版字符定长切分，带 overlap（RAG_TEXT_SPLITTER=character 时启用）。"""
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


def _split_with_sep(text: str, sep: str) -> List[str]:
    """按分隔符切分，并把分隔符保留在每段末尾（keep_separator='end'）。"""
    if not sep:
        return list(text)
    raw = text.split(sep)
    out = [p + sep for p in raw[:-1]]
    if raw[-1]:
        out.append(raw[-1])
    return [p for p in out if p]


def _merge_splits(splits: List[str], chunk_size: int, chunk_overlap: int) -> List[str]:
    """把切好的小段合并成 <= chunk_size 的块，尾部重叠 chunk_overlap 字符。"""
    docs: List[str] = []
    current: List[str] = []
    total = 0
    for d in splits:
        ln = len(d)
        if current and total + ln > chunk_size:
            doc = "".join(current)
            if doc.strip():
                docs.append(doc)
            while current and total > chunk_overlap:
                total -= len(current[0])
                current = current[1:]
        current.append(d)
        total += ln
    if current:
        doc = "".join(current)
        if doc.strip():
            docs.append(doc)
    return docs


def _split_recursive(
    text: str, separators: List[str], chunk_size: int, chunk_overlap: int
) -> List[str]:
    """递归字符切分：取第一个能命中文本的分隔符切分，超长片段用更细的分隔符再切。"""
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    sep = separators[-1]
    next_seps: List[str] = []
    for i, s in enumerate(separators):
        if s == "":
            sep = ""
            break
        if s in text:
            sep = s
            next_seps = separators[i + 1 :]
            break

    splits = _split_with_sep(text, sep)
    final: List[str] = []
    good: List[str] = []
    for s in splits:
        if len(s) < chunk_size:
            good.append(s)
        else:
            if good:
                final.extend(_merge_splits(good, chunk_size, chunk_overlap))
                good = []
            if next_seps:
                final.extend(_split_recursive(s, next_seps, chunk_size, chunk_overlap))
            else:
                final.append(s)
    if good:
        final.extend(_merge_splits(good, chunk_size, chunk_overlap))
    return final


def _split_table_block(text: str, chunk_size: int, chunk_overlap: int) -> List[str]:
    """长表格按行分组：表头 + 分隔行跟随第一个 chunk，其余按行整组切分。"""
    rows = text.splitlines()
    if not rows:
        return []
    header = rows[:2]  # 表头行 + 分隔行
    header_len = sum(len(r) + 1 for r in header)
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for row in rows[2:]:
        add = len(row) + 1
        limit = chunk_size - header_len if not chunks else chunk_size
        if current and current_len + add > limit:
            body = "\n".join(current)
            chunks.append("\n".join(header) + "\n" + body if not chunks else body)
            current = [row]
            current_len = add
        else:
            current.append(row)
            current_len += add
    if current:
        body = "\n".join(current)
        chunks.append("\n".join(header) + "\n" + body if not chunks else body)
    return chunks


def _extract_blocks(text: str) -> List[tuple]:
    """把文档按行扫描拆成 ('table' | 'code' | 'text') 块。"""
    blocks: List[tuple] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith(_FENCE):
            content = [line]
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(_FENCE):
                content.append(lines[i])
                i += 1
            if i < len(lines):
                content.append(lines[i])
                i += 1
            blocks.append(("code", "\n".join(content)))
        elif stripped.startswith("|") and "|" in stripped[1:]:
            content = [line]
            i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                content.append(lines[i])
                i += 1
            blocks.append(("table", "\n".join(content)))
        else:
            content = [line]
            i += 1
            while (
                i < len(lines)
                and not lines[i].strip().startswith(_FENCE)
                and not lines[i].strip().startswith("|")
            ):
                content.append(lines[i])
                i += 1
            blocks.append(("text", "\n".join(content)))
    return blocks


class StructureAwareTextSplitter:
    """结构感知切分：保持表格/代码块完整，中文按句子边界切。"""

    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 64) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split_text(self, text: str) -> List[str]:
        chunks: List[str] = []
        for kind, content in _extract_blocks(text):
            if not content.strip():
                continue
            if kind == "table":
                if len(content) <= self.chunk_size:
                    chunks.append(content)
                else:
                    chunks.extend(
                        _split_table_block(content, self.chunk_size, self.chunk_overlap)
                    )
            elif kind == "code":
                if len(content) <= self.chunk_size:
                    chunks.append(content)
                else:
                    chunks.extend(
                        _split_recursive(
                            content, _CODE_SEPARATORS, self.chunk_size, self.chunk_overlap
                        )
                    )
            else:
                chunks.extend(
                    _split_recursive(
                        content, _TEXT_SEPARATORS, self.chunk_size, self.chunk_overlap
                    )
                )
        return chunks


def split_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    """结构感知切分；RAG_TEXT_SPLITTER=character 时回退旧字符切分。"""
    if getattr(settings, "text_splitter", "structure") == "character":
        return _split_character(text, chunk_size, overlap)
    return StructureAwareTextSplitter(
        chunk_size=chunk_size, chunk_overlap=overlap
    ).split_text(text)


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
