"""查询规划：三段式自适应检索。

1. 规则范围展开：识别 `X到Y` / `X~Y` / `X-Y` 等区间写法（字母+数字序列），
   枚举出完整 doc_id 集合作为检索过滤条件，零 LLM 消耗；
2. 意图放大 top_k：对比/枚举类问题（哪个、对比、列出、所有……）固定放大检索条数；
3. LLM 兜底：前两步未命中时，复用改写调用让 LLM 推荐 top_k（5~上限，默认 20），
   只多输出一个数字，几乎不增加 token。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from app.config import settings
from app.rag.query_rewrite import rewrite, rewrite_with_top_k


_CODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*[-_]?\d+")
_RANGE_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9]*[-_]?\d+)\s*(?:到|至|~|～|—|--|－|\.\.)\s*"
    r"([A-Za-z][A-Za-z0-9]*[-_]?\d+)"
)
_MAX_ENUM = 100  # 防御：单次区间最多枚举 100 个

_COMPARE_WORDS = (
    "哪个", "对比", "比较", "区别", "差异", "更便宜", "更贵", "性价比",
    "谁更", "怎么选", "如何选", "选哪个", "该选", "哪款", "哪一", "推荐哪",
)
_ENUM_WORDS = (
    "列出", "所有", "全部", "有哪些", "哪些", "分别", "汇总", "清单",
    "一览", "枚举", "逐个", "依次",
)

# —— 检索融合路由：按题型给 vector / BM25 分配 RRF 权重（零 LLM）——
_FUSION_DEFAULT = {"vector": 0.5, "bm25": 0.5}
_FUSION_BM25 = {"vector": 0.3, "bm25": 0.7}
_FUSION_MID = {"vector": 0.4, "bm25": 0.6}
_FUSION_VECTOR = {"vector": 0.7, "bm25": 0.3}


@dataclass
class QueryPlan:
    queries: List[str]
    filter_doc_ids: Optional[List[str]] = None
    top_k: Optional[int] = None
    source: str = "default"
    weights: Optional[dict] = None


def _norm_code(code: str) -> str:
    """归一化型号编码：AC-100 / AC100 / ac_100 -> AC100。"""
    return re.sub(r"[-_]", "", code).upper()


def _build_code_map(doc_ids: Optional[List[str]]) -> dict:
    """doc_id -> 其中出现的型号编码 -> 完整 doc_id。"""
    mapping: dict = {}
    for did in doc_ids or []:
        for code in _CODE_RE.findall(did):
            mapping.setdefault(_norm_code(code), did)
    return mapping


def _enumerate_codes(a: str, b: str) -> List[str]:
    """把区间两端展开成完整编码列表；前缀不一致或区间过大返回空。"""
    ma = re.fullmatch(r"([A-Za-z]+)([-_]?)(\d+)", a)
    mb = re.fullmatch(r"([A-Za-z]+)([-_]?)(\d+)", b)
    if not ma or not mb or ma.group(1).upper() != mb.group(1).upper():
        return []
    lo, hi = sorted((int(ma.group(3)), int(mb.group(3))))
    if hi - lo > _MAX_ENUM:
        return []
    width = max(len(ma.group(3)), len(mb.group(3)))
    sep = ma.group(2) or mb.group(2) or ""
    return [f"{ma.group(1)}{sep}{str(i).zfill(width)}" for i in range(lo, hi + 1)]


def expand_range(query: str, known_doc_ids: Optional[List[str]] = None) -> Optional[List[str]]:
    """规则范围展开：命中区间写法且能在知识库中完整映射时，返回 doc_id 列表。"""
    try:
        m = _RANGE_RE.search(query)
        if not m:
            return None
        code_map = _build_code_map(known_doc_ids)
        docs: List[str] = []
        for code in _enumerate_codes(m.group(1), m.group(2)):
            did = code_map.get(_norm_code(code))
            if did and did not in docs:
                docs.append(did)
        return docs if len(docs) >= 2 else None
    except Exception:
        return None


def detect_intent(query: str) -> Optional[str]:
    """意图检测：对比/枚举类问题返回对应标签，否则 None。"""
    for w in _COMPARE_WORDS:
        if w in query:
            return "compare"
    for w in _ENUM_WORDS:
        if w in query:
            return "enumerate"
    return None


def fusion_strategy(query: str) -> dict:
    """按题型路由检索融合权重：型号/编号类 BM25 主导，价格/容量/区间类 BM25 偏重，
    枚举/比较类均衡，其余口语化/语义类向量主导。全部规则判定，零 LLM 消耗。"""
    if _CODE_RE.search(query):  # 型号/编号：AC-100、P1001…
        return dict(_FUSION_BM25)
    if re.search(r"(\d+\s*(元|万|GB|TB|QPS|%|G|T)|\d{4}[-/]\d{1,2}|到|至|~|～)", query):
        return dict(_FUSION_MID)
    if detect_intent(query):
        return dict(_FUSION_DEFAULT)
    return dict(_FUSION_VECTOR)


def plan_query(query: str, known_doc_ids: Optional[List[str]] = None,
               n: int = 3) -> QueryPlan:
    """三段式规划：范围展开 > 意图放大 > LLM 兜底。"""
    default_k = int(getattr(settings, "top_k", 5))
    compare_k = max(default_k, int(getattr(settings, "compare_top_k", 12)))
    max_k = int(getattr(settings, "max_expand_top_k", 20))
    weights = fusion_strategy(query)

    # 第一段：规则范围展开（零 LLM；原问题即最优检索 query，不再改写）
    doc_ids = expand_range(query, known_doc_ids)
    if doc_ids:
        return QueryPlan(
            queries=[query],
            filter_doc_ids=doc_ids,
            top_k=max(default_k, len(doc_ids)),
            source="range",
            weights=weights,
        )

    # 第二段：对比/枚举意图 -> 固定放大 top_k
    intent = detect_intent(query)
    if intent:
        return QueryPlan(
            queries=rewrite(query, n=n),
            top_k=compare_k,
            source=f"intent:{intent}",
            weights=weights,
        )

    # 第三段：LLM 兜底（复用改写调用，只多输出一个 top_k 数字）
    queries, top_k = rewrite_with_top_k(query, n=n, max_top_k=max_k)
    return QueryPlan(queries=queries, top_k=top_k, source="llm", weights=weights)
