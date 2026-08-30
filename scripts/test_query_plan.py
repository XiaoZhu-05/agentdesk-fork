"""三段式查询规划离线测试（不调 LLM）。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.rag.query_plan as qp


def main() -> None:
    docs = sorted(
        d for d in os.listdir("data/docs")
        if os.path.isfile(os.path.join("data/docs", d))
    )
    print("known docs:", len(docs))

    cases = [
        ("AC-100到AC-110的套餐哪个更便宜", 11),
        ("AC100 到 AC110 套餐价格对比", 11),
        ("AC-100~AC-105 有什么套餐", 6),
        ("AC-110到AC-100（倒序）", 11),
        ("AC-100至AC-102", 3),
        ("介绍一下 AC-100 套餐", None),
        ("AC-100和AC-110哪个更便宜", None),
    ]
    for q, expect in cases:
        r = qp.expand_range(q, docs)
        n = len(r) if r else None
        status = "OK" if n == expect else "FAIL"
        print(f"[{status}] {q} -> {n} (expect {expect})")
        if r and status == "FAIL":
            print("   ", r)

    print("intent compare:", qp.detect_intent("哪个更便宜"))
    print("intent enum:", qp.detect_intent("列出所有套餐"))
    print("intent none:", qp.detect_intent("什么是向量数据库"))

    # 屏蔽 LLM，验证三段式分支
    qp.rewrite = lambda q, n=3: [q, q + "（对比）"]
    qp.rewrite_with_top_k = lambda q, n=3, max_top_k=20: ([q], 7)
    p1 = qp.plan_query("AC-100到AC-110的套餐哪个更便宜", known_doc_ids=docs)
    print("plan range:", p1.source, p1.top_k, len(p1.filter_doc_ids or []))
    p2 = qp.plan_query("AC-100和AC-110哪个更便宜", known_doc_ids=docs)
    print("plan intent:", p2.source, p2.top_k, p2.filter_doc_ids)
    p3 = qp.plan_query("什么是向量数据库", known_doc_ids=docs)
    print("plan llm:", p3.source, p3.top_k, p3.filter_doc_ids)


if __name__ == "__main__":
    main()
