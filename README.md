# AgentDesk · Agentic RAG + 分层记忆 + 评测闭环

> 一个可复现的**企业知识问答 Agent 原型**：把 _记忆 → 查询规划 → 检索 → 工具调用 → 带证据生成 → 反思重试 → 质量评测_ 串成一条可观测、可量化、可回归的闭环，并配一套开箱即用的 Streamlit 控制台（上传文档、管理知识库、切换模型、查看执行链）。

<p>
<img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white">
<img alt="LangGraph" src="https://img.shields.io/badge/Orchestration-LangGraph-7c5cff">
<img alt="FastAPI" src="https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white">
<img alt="Streamlit" src="https://img.shields.io/badge/Console-Streamlit-FF4B4B?logo=streamlit&logoColor=white">
<img alt="Qdrant" src="https://img.shields.io/badge/Vector-Qdrant-dc244c">
<img alt="License" src="https://img.shields.io/badge/License-MIT-green">
<img alt="No API key needed" src="https://img.shields.io/badge/run-offline%20fallback-fbbf24">
</p>

**无需任何 API key 也能端到端运行**（自动走离线 fallback：哈希向量 + 拼接式回答）；Qdrant / Redis / 大模型 key 任一不可用都会自动回退内存/离线实现，本地零外部依赖即可演示完整链路。

---

## ✨ 核心特性

- **Agentic 编排（LangGraph）**：`memory_retrieve → planner → retrieval → tool → writer → critic(不达标且未超限则重试) → memory_write → summarize`，每个节点可观测；LangGraph 不可用时退化为等价顺序执行。
- **查询规划（planner）**：三段式 `plan_query()` —— ① 编号范围展开（如 `AC-100~AC-119` 自动枚举并过滤 doc_id，无需 LLM）；② 意图检测（对比/枚举类问题提高召回 `top_k`）；③ LLM 改写（多查询 + 自适应 `top_k`）；并按查询类型自动调整 vector/BM25 融合权重（含代码/ID → BM25 偏重，指标数字 → 均衡，语义 → 向量偏重）。
- **混合检索 + 重排**：多查询改写 → 向量 + BM25 → RRF 融合 → Rerank（默认离线 `overlap 主键 + IDF 同分裁决`，零依赖不倒退；设置 `RERANK_MODEL` 可一键切换 cross-encoder）。
- **结构感知切分（structure splitter）**：表格/代码块整体保留、中文按句子切分；配 OpenWebUI 40 问实测对比，chunk hit@1 0.389 → 0.475、答案跨文档串库 4 → 0（详见「评测」）。
- **多格式知识库上传与管理**：支持 14 种格式（txt/md、pdf、docx、xlsx、csv、pptx、html、json、rtf、xml、xls、odt、epub）；上传即持久化并重建索引；文档清单 + 密码删除 + 内置示例文档保护；embedding 模型变化自动重建索引。
- **分层记忆（Memory Layer）**：短期工作记忆（对话 buffer + 滚动摘要）、长期记忆（偏好/事实抽取 → 向量化 → Qdrant 按 `user_id` namespace 隔离 → 检索注入）、记忆演化（写入去重 / 冲突覆盖留审计 / TTL+LRU 淘汰）。
- **可信回答与反思循环**：faithfulness 评估（有 key 用 LLM-as-judge，无 key 回退启发式）；critic 把工具结果（kb_stats/calculator）也纳入证据，统计类答案不再被误判重试；writer 出口净化无效/幻觉引用。
- **MCP 风格工具层**：`list_tools / call_tool` 契约 + 工具名/参数 schema 校验 + 输出截断；calculator 用 AST 白名单阻断注入；可经 stdio JSON-RPC 对接独立 MCP server。
- **控制台访问控制与配额**：`DEMO_PASSWORD` 会员登录 / 访客模式；Redis 记录会话、每日、访客上传/查询配额（无 Redis 自动放行）。
- **评测闭环**：检索 `hit@k / MRR`（37 问）、记忆 `memory hit@k`（10 条）、faithfulness（8 条）、OpenWebUI 结构切分对比（40 问）。
- **全程可观测**：执行 trace 实时可视化（Streamlit 时间线），并落盘 `eval/reports/traces.jsonl` 供事后复盘。

---

## 🧭 架构

```
                         ┌──────────── LangGraph 编排 ────────────┐
 /chat (query,           │ memory_retrieve → planner → retrieval  │
  user_id, session_id)   │      → tool → writer → critic           │
         │               │            │ (不达标且未超限则重试)      │
         ▼               │            └──→ memory_write → summarize│
   FastAPI / Streamlit   └──────┬──────────────────┬───────────────┘
         │                      │                  │
   实时 trace 可视化         Qdrant(知识库+记忆)   Redis(缓存+短期记忆+配额)
                               └─ 不可用→内存        └─ 不可用→内存
```

- 分层架构图：[docs/分层架构图.mermaid](docs/分层架构图.mermaid)
- 调用流程图（含反思重试循环）：[docs/调用流程图.mermaid](docs/调用流程图.mermaid)
- 数据流与可观测：[docs/数据流与可观测.mermaid](docs/数据流与可观测.mermaid)
- 记忆层设计：[docs/记忆层设计文档.md](docs/记忆层设计文档.md) 与 [docs/记忆数据流.mermaid](docs/记忆数据流.mermaid)
- 关键变更记录：`docs/变更记录_*.md`（rerank 升级 / 前端模型选择与示例 / 知识库统计修复 / 运行健壮性与热重载）
- Faithfulness 计算流程可视化：[docs/可信回答-faithfulness-计算流程.html](docs/可信回答-faithfulness-计算流程.html) · 记忆层演示：[docs/记忆层演示.html](docs/记忆层演示.html)

---

## 🚀 快速开始

```bash
pip install -r requirements.txt

# （可选）配置真实模型；不配置则用离线 fallback
cp .env.example .env          # 填 OPENAI_API_KEY（或硅基流动/百炼等兼容厂商）
python check_api.py           # 可选：自检真实大模型链路（embedding + chat）

python -m scripts.build_index # 建索引（Streamlit 首次启动也会自动建）
uvicorn app.api.main:app --reload
```

- **可视化控制台（推荐演示）**：`streamlit run streamlit_app.py` —— 登录（`DEMO_PASSWORD`）或访客模式 → 侧边栏上传文档 / 管理知识库 / 切换记忆身份 → 主区选择 Chat 模型、点示例或直接提问 → 查看答案 + faithfulness 仪表 + 引用/证据 + 工具调用 + 对话历史/记忆面板 + 演化审计 + 执行链时间线。
- Web 聊天页：<http://localhost:8000/> · 交互式 API 文档：<http://localhost:8000/docs>
- 一键全栈：`docker compose up --build`（api + qdrant + redis，均带回退）。

```bash
# 跨轮记忆演示（同一 user + session）
curl -s localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"query":"我是法务，只看2024年的合规条款","user_id":"alice","session_id":"s1"}'
curl -s localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"query":"这份合同我该重点看什么？","user_id":"alice","session_id":"s1"}'   # 第二轮会召回上面的记忆
```

---

## 🔐 控制台访问与配额

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `DEMO_PASSWORD` | 空 | 会员口令；不配置则只能走访客模式 |
| `DELETE_PASSWORD` | 复用 `DEMO_PASSWORD` | 文档删除密码（sha256 哈希） |
| `DEMO_SESSION_QUOTA` | 30 | 单会话查询上限 |
| `DEMO_DAILY_QUOTA` | 100 | 全站每日查询上限 |
| `DEMO_GUEST_UPLOADS` | 3 | 访客上传文档数上限 |
| `DEMO_GUEST_QUERIES` | 10 | 访客查询次数上限 |

> 配额依赖 `REDIS_URL`；未配置 Redis 时自动放行。部署到 Streamlit Cloud 可改用 `.streamlit/secrets.toml`（见 `.streamlit/secrets.toml.example`）。

---

## 📄 知识库：多格式上传与管理

- **14 种格式解析**：txt/md、pdf、docx、xlsx、csv、pptx、html、json、rtf、xml、xls、odt、epub，统一抽取为文本后走同一套切分/建索引链路（`app/rag/extractors.py`）。
- **上传即持久化**：文档保存到 `data/docs/`，清单记录到 `data/uploads_meta.json`，随后自动重建索引；重复上传、索引构建均带结果提示。
- **文档管理**：侧边栏展示文档清单（类型/大小/来源），上传文档可输密码删除；内置示例文档（`sample_*` / `plan_*`）受保护不可删。
- **索引签名**：`data/index/index_meta.json` 记录 embedding 模型与文件清单；更换模型/新增文件后 `ensure_index()` 自动触发重建，避免旧向量维度错配。
- **切分方式**：`RAG_TEXT_SPLITTER=structure`（结构感知，默认）或 `character`（旧版字符定长）。

---

## 🧠 分层记忆

| 层 | 做什么 | 存储 | 文件 |
|---|---|---|---|
| 短期工作记忆 | 对话 buffer + 滚动 summary 压缩（保留近 K 轮 + running_summary），控制长对话上下文膨胀 | Redis / 内存 | `app/memory/short_term.py` |
| 长期记忆 | 抽取用户偏好/事实 → 向量化 → Qdrant 按 `user_id` 隔离 → 检索注入 | Qdrant / 内存 | `app/memory/long_term.py` |
| 记忆演化 | 写入去重（相似度阈值）、冲突更新（新值覆盖、旧值留 `version/superseded_by` 审计）、过期淘汰（event TTL + 容量 LRU） | — | `app/memory/evolution.py` |

入口/出口以 `memory_retrieve / memory_write / summarize` 三节点无侵入接入既有编排；阈值与开关见 `.env.example` 的 `MEM_*`。

---

## ⚖️ 评测

```bash
python -m eval.run_eval            # 检索：vector / hybrid / hybrid+rerank 的 hit@k 与 MRR（37 问）
python -m eval.run_memory_eval 5   # 记忆：memory hit@1/3/5（10 条）→ eval/reports/memory_latest.json
python -m eval.run_faithfulness 8  # 生成侧：平均 faithfulness 与通过率（8 问）
```

**Rerank 内部对比**（离线索引，37 问）：

| 配置 | hit@k | MRR |
|---|---|---|
| vector（纯向量） | 0.919 | 0.800 |
| hybrid（向量+BM25，无 rerank） | 1.000 | 0.941 |
| hybrid + rerank | 1.000 | **1.000** |

**OpenWebUI 实测：结构感知切分 vs 旧字符定长**（40 问，见 `eval/openwebui_对比/`）：

| 指标 | character（旧） | structure（新） |
|---|---|---|
| chunk hit@1 | 0.389 | **0.475** |
| chunk hit@3 | 0.639 | **0.775** |
| chunk hit@5 | 0.750 | **0.800** |
| doc hit@5 | 0.944 | **1.000** |
| MRR@5 | 0.767 | **0.785** |
| 答案跨文档串库 | 4 | **0** |

对比工具链：`generate_dataset.py`（构造 40 问）、`run_compare.py`（双切分跑分）、`per_question_analysis.py`（逐题 chunk 定位分析）、`split_stats.py`（边界统计）；完整结论见 [eval/openwebui文档实测对比.md](eval/openwebui文档实测对比.md)。

> 当前示例语料较小，离线 embedding 已接近天花板，hit@k 提升幅度有限；评测框架已就绪，换真实 embedding + 更大语料后，混合检索 / Rerank / 结构切分的提升会更明显。

---

## 📊 可观测

- 每个节点统一往 `trace` 追加结构化记录（改写 / 检索命中 / 工具调用 / faithfulness 分数 / 记忆读写），前端时间线实时渲染。
- 每轮执行链落盘 `eval/reports/traces.jsonl`（一行一条 JSON，便于 grep/回放）；`TRACE_LOG=0` 可关。

---

## 📁 目录

```
agentdesk/
├── app/
│   ├── config.py            # 配置（.env / 环境变量，全部带默认值）
│   ├── llm.py               # Embedding/Chat 封装（离线 fallback + 缓存 + 超时）
│   ├── api/main.py          # FastAPI: / · /chat · /health
│   ├── web/index.html       # 内置极简聊天页
│   ├── graph/               # LangGraph: state / nodes / build_graph / judge / trace_log
│   ├── rag/                 # extractors(14格式) / indexer / query_plan / query_rewrite
│   │                        # retriever / bm25 / rerank / store / qdrant_store / cache
│   ├── memory/              # 分层记忆: schema / store / short_term / long_term / evolution
│   └── tools/               # MCP 风格工具层 + 内置工具 + stdio MCP server/client
├── eval/                    # run_eval(hit@k/MRR) · run_memory_eval · run_faithfulness
│   └── openwebui_对比/      # 结构切分 vs 字符切分：40 问评测工具链 + 结果
├── scripts/                 # build_index / gen_corpus / demo / mcp_demo / test_query_plan
├── docs/                    # 架构图 / 记忆层设计 / 数据流 mermaid / 变更记录
├── data/                    # docs 示例文档 + index 索引 + uploads_meta.json
├── streamlit_app.py         # 可视化控制台（上传/管理/问答/记忆/配额）
├── check_api.py             # 快速自检真实模型链路
├── docker-compose.yml · Dockerfile · requirements.txt
└── push_to_github.bat
```

---

## 🗺️ 里程碑

- [x] 朴素 RAG + FastAPI + LangGraph 编排
- [x] 查询改写 + 混合检索（向量+BM25, RRF）+ Rerank + eval(hit@k/MRR)
- [x] tool 节点 + Critic 反思节点 + faithfulness 重试循环
- [x] MCP 风格工具层 + JSON-RPC over stdio MCP server + 安全计算器
- [x] Qdrant 向量库 + Redis 缓存 + docker-compose（均带回退）
- [x] LLM-judge faithfulness + 生成侧评测
- [x] 分层记忆（短期/长期/演化）+ memory hit@k + 引用净化 + trace 落盘
- [x] 多格式文档上传（14 种）+ 知识库管理（清单/密码删除/内置保护）+ 索引自动重建
- [x] 结构感知切分 + OpenWebUI 40 问实测对比
- [x] 查询规划（范围展开/意图检测/LLM 改写 + 融合权重）
- [x] Rerank 升级（overlap 主键 + IDF 同分裁决 + 可插拔 cross-encoder）
- [x] 控制台：会员/访客 + Redis 配额 + 运行时切换 Chat 模型 + 示例即点即问
- [ ] cross-encoder 重排正式上线与评测集回归
- [ ] 更难评测集（改写/反向/无逐字 token）与更多 MCP 工具

---

## License

[MIT](LICENSE)
