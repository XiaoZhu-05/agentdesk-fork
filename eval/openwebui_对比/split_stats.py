"""openwebui 文档语料：字符切分 vs 结构感知切分的确定性质量统计（不调 API）。

用法：python -B eval/openwebui_对比/split_stats.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.rag.extractors import extract_text, SUPPORTED_EXTS
from app.rag.indexer import StructureAwareTextSplitter, _split_character

BASE = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(BASE, "docs")
CHUNK_SIZE, OVERLAP = 512, 64
SENT_PUNCT = set("。；！？，.!?;:")


def boundary_stats(text: str, chunks: list) -> dict:
    """统计 chunk 边界质量：落在行边界 / 句末标点后 / 行中间（含表格行切断）。

    chunk 带 overlap（相邻块共享尾部），用后缀/前缀匹配还原每个 chunk 在原文中的真实偏移。
    """
    ref = text.strip()
    if not ref:
        return {}
    offsets = []
    pos = 0
    for i, c in enumerate(chunks):
        offsets.append(pos)
        if i < len(chunks) - 1:
            nxt = chunks[i + 1]
            # 下一块从「上一块结尾往前最多 64 字符（overlap）」的位置开始；
            # 用 find 直接定位，避免重复文本导致后缀/前缀匹配误判。
            p = ref.find(nxt, max(0, pos + len(c) - 64))
            pos = p if p >= 0 else pos + len(c)
    st = {"line_break": 0, "sentence": 0, "mid_line": 0, "mid_word": 0, "table_cut": 0, "fence_broken": 0}
    for i, c in enumerate(chunks[:-1]):
        off = offsets[i] + len(c)
        if off <= 0 or off >= len(ref):
            continue
        prev = ref[off - 1] if off > 0 else ""
        nxt = ref[off] if off < len(ref) else ""
        if prev == "\n" or nxt == "\n":
            st["line_break"] += 1
        elif prev in SENT_PUNCT and (nxt in " \n" or nxt == ""):
            st["sentence"] += 1
        else:
            st["mid_line"] += 1
            if prev == " " or nxt == " ":
                st["mid_word"] += 1  # 行内但落在单词边界（空格处），不算切词
            line_start = ref.rfind("\n", 0, off) + 1
            if ref[line_start:off].strip().startswith("|"):
                st["table_cut"] += 1
    st["fence_broken"] = sum(1 for c in chunks if c.count("```") % 2 == 1)
    return st


def main() -> None:
    docs = sorted(
        f for f in os.listdir(DOCS_DIR)
        if os.path.isfile(os.path.join(DOCS_DIR, f))
        and os.path.splitext(f)[1].lower() in SUPPORTED_EXTS
    )
    agg = {
        "character": {"chunks": 0, "len_sum": 0, "max": 0, "line_break": 0, "sentence": 0,
                      "mid_line": 0, "mid_word": 0, "table_cut": 0, "fence_broken": 0},
        "structure": {"chunks": 0, "len_sum": 0, "max": 0, "line_break": 0, "sentence": 0,
                      "mid_line": 0, "mid_word": 0, "table_cut": 0, "fence_broken": 0},
    }
    print(f"{'文件':<32}{'文本':>9}{'字符块数':>8}{'结构块数':>8}{'字符均长':>8}{'结构均长':>8}")
    for f in docs:
        path = os.path.join(DOCS_DIR, f)
        try:
            text = extract_text(path)
        except Exception as e:  # noqa: BLE001
            print(f"{f:<32} 解析失败: {e}")
            continue
        if not text.strip():
            print(f"{f:<32} 空文本")
            continue
        char_chunks = _split_character(text, CHUNK_SIZE, OVERLAP)
        struct_chunks = StructureAwareTextSplitter(CHUNK_SIZE, OVERLAP).split_text(text)
        cs = boundary_stats(text, char_chunks)
        ss = boundary_stats(text, struct_chunks)
        for key, chunks, st in (("character", char_chunks, cs), ("structure", struct_chunks, ss)):
            a = agg[key]
            a["chunks"] += len(chunks)
            a["len_sum"] += sum(len(c) for c in chunks)
            a["max"] = max(a["max"], max((len(c) for c in chunks), default=0))
            for k in ("line_break", "sentence", "mid_line", "mid_word", "table_cut", "fence_broken"):
                a[k] += st.get(k, 0)
        print(
            f"{f[:31]:<32}{len(text):>9}{len(char_chunks):>8}{len(struct_chunks):>8}"
            f"{sum(len(c) for c in char_chunks) // max(1, len(char_chunks)):>8}"
            f"{sum(len(c) for c in struct_chunks) // max(1, len(struct_chunks)):>8}"
        )
    print()
    for key, a in agg.items():
        n = a["chunks"]
        b = a["line_break"] + a["sentence"] + a["mid_line"]
        good = a["line_break"] + a["sentence"]
        print(
            f"[{key}] chunks={n} avg={a['len_sum'] // max(1, n)} max={a['max']} "
            f"boundary_good={good}/{b} ({100.0 * good / max(1, b):.1f}%) "
            f"mid_line={a['mid_line']} (其中切词={a['mid_line'] - a['mid_word']}) "
            f"table_cut={a['table_cut']} fence_broken={a['fence_broken']}"
        )


if __name__ == "__main__":
    main()
