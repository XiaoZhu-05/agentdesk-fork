"""从 openwebui 文档抽取片段，用 LLM 生成「问题 + 原文逐字答案」评测集。

用法：python -B eval/openwebui_对比/generate_dataset.py
输出：eval/openwebui_对比/dataset.jsonl
"""
from __future__ import annotations

import json
import os
import random
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app.llm import chat
from app.rag.extractors import extract_text, SUPPORTED_EXTS

BASE = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(BASE, "docs")
OUT = os.path.join(BASE, "dataset.jsonl")

PER_DOC = {
    "CHANGELOG.md": 12,
    "README.md": 4,
    "SECURITY.md": 4,
    "OpenWebUI_项目架构分析报告.md": 4,
    "VALIDATION.md": 3,
    "TROUBLESHOOTING.md": 3,
    "CODE_OF_CONDUCT.md": 2,
    "指南.pdf": 3,
    "architecture——修改前.pdf": 3,
    "代办.docx": 2,
}
CTX_LEN = 1500

SYSTEM = (
    "你是 RAG 评测数据生成器。根据给定的文档片段，生成 1 个问题："
    "1) 问题必须只能靠该片段回答，独立完整，不能出现“根据上文/该文档/它”等指代；"
    "2) answer 必须是片段中逐字存在的一段连续文本（原样复制，禁止改写、禁止翻译、禁止省略中间内容）；"
    "3) answer 控制在 10~300 字；"
    "4) 只输出一个 JSON 对象，不要输出其他文字：{\"question\": \"...\", \"answer\": \"...\"}"
)


def sample_context(text: str, rng: random.Random) -> str:
    if len(text) <= CTX_LEN + 200:
        return text
    idx = rng.randrange(len(text))
    start = text.rfind("\n", 0, idx) + 1
    return text[start : start + CTX_LEN]


def parse_json(raw: str) -> dict:
    raw = raw.strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no json object")
    return json.loads(m.group(0))


def gen_one(doc_id: str, context: str) -> dict | None:
    for _ in range(2):
        raw = chat(SYSTEM, f"文档片段：\n```\n{context}\n```")
        try:
            obj = parse_json(raw)
            q = str(obj.get("question", "")).strip()
            a = str(obj.get("answer", "")).strip()
            if q and a and a in context:
                return {"question": q, "answer": a, "doc_id": doc_id}
        except Exception:  # noqa: BLE001
            continue
    return None


def main() -> None:
    rng = random.Random(20260829)
    rows = []
    for fname, n_q in PER_DOC.items():
        path = os.path.join(DOCS_DIR, fname)
        if not os.path.isfile(path):
            print(f"skip missing: {fname}")
            continue
        try:
            text = extract_text(path)
        except Exception as e:  # noqa: BLE001
            print(f"extract fail {fname}: {e}")
            continue
        if not text.strip():
            print(f"empty text: {fname}")
            continue
        got = 0
        for _ in range(n_q * 4):
            if got >= n_q:
                break
            ctx = sample_context(text, rng)
            row = gen_one(fname, ctx)
            if row:
                rows.append(row)
                got += 1
                print(f"[{got}/{n_q}] {fname}")
        print(f"{fname}: generated {got}/{n_q}")
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"done: {len(rows)} questions -> {OUT}")


if __name__ == "__main__":
    main()
