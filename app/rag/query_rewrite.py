"""查询改写（multi-query）。

把用户问题扩展成多条检索 query，提升召回。
有 LLM 时让模型生成同义/拆解变体；离线时回退为原问题。
"""
from __future__ import annotations

import json
from typing import List

from app.config import settings
from app.llm import chat


def rewrite(query: str, n: int = 3) -> List[str]:
    if not settings.use_llm:
        return [query]
    system = (
        "你是检索查询改写器。把用户问题改写成 {n} 条语义等价或"
        "聚焦子意图的检索查询，便于向量/关键词检索。"
        "只输出 JSON 数组，例如 [\"q1\",\"q2\"]。".format(n=n)
    )
    try:
        raw = chat(system, query)
        start, end = raw.find("["), raw.rfind("]")
        variants = json.loads(raw[start : end + 1])
        variants = [v for v in variants if isinstance(v, str) and v.strip()]
        # 原问题始终保留在首位，去重
        out = [query] + [v for v in variants if v != query]
        return out[: n + 1]
    except Exception:
        return [query]


def rewrite_with_top_k(query: str, n: int = 3, max_top_k: int = 20) -> tuple[list[str], int]:
    """改写查询的同时，让 LLM 推荐检索条数 top_k（5 ~ max_top_k）。

    只在规则未命中（三段式第三段）时调用；复用同一次 LLM 调用，
    额外输出仅一个数字，token 增量可忽略。解析失败回退默认值。
    """
    default_k = int(getattr(settings, "top_k", 5))
    if not settings.use_llm:
        return [query], default_k
    system = (
        "你是检索查询改写器。把用户问题改写成 {n} 条语义等价或聚焦子意图的检索查询，"
        "便于向量/关键词检索。同时评估完成该问题至少需要的参考文档数量 top_k"
        "（整数，范围 {min_k}~{max_k}；普通单点问题给 {min_k}，"
        "涉及对比/列举/大范围才给更大的数）。"
        '只输出 JSON 对象，例如 {{"queries": ["q1","q2","q3"], "top_k": 7}}。'
    ).format(n=n, min_k=default_k, max_k=max_top_k)
    try:
        raw = chat(system, query)
        top_k = default_k
        variants = None
        if "{" in raw:
            start, end = raw.find("{"), raw.rfind("}")
            obj = json.loads(raw[start : end + 1])
            if isinstance(obj, dict):
                variants = obj.get("queries")
                k = obj.get("top_k")
                if isinstance(k, int) and default_k <= k <= max_top_k:
                    top_k = k
        if not isinstance(variants, list):
            s, e = raw.find("["), raw.rfind("]")
            variants = json.loads(raw[s : e + 1]) if s != -1 and e != -1 else [query]
        variants = [v for v in variants if isinstance(v, str) and v.strip()]
        out = [query] + [v for v in variants if v != query]
        return out[: n + 1], top_k
    except Exception:
        return [query], default_k
