# 验证报告：独立 Rerank 与 RAG 上下文注入改造

## 1. 问题背景

Open WebUI v0.10.2 的两个与企业级 RAG 问答强相关的问题：

1. **Rerank 依赖 Hybrid Search**：关闭混合检索时，Rerank 完全不执行；
   为使用 Rerank 被迫开启 Hybrid Search，会引入 Python 内存 BM25 的性能瓶颈。
2. **RAG 注入破坏消息历史与 Prompt Caching**：RAG 模板直接改写最后一条用户消息；
   工具调用多跳时反复追加相同 Source，导致历史消息被修改、云 LLM 前缀缓存无法命中。

## 2. 两项修改

1. **独立 Rerank**：新增配置 `rag.rerank_enabled`（默认 `False`，保持原行为）。
   关闭 Hybrid、开启该开关且已加载 Rerank 模型时，纯向量检索结果仍执行 Rerank
   （先取 `max(k, k_reranker)` 候选，重排后截断回 `k`）。
2. **追加式上下文注入**：新增配置 `rag.append_rag_context`（默认 `True`）。
   开启时 RAG 上下文作为独立 user 消息追加，不再改写历史消息；
   同一轮工具调用按 `document_id + chunk_id`（缺 chunk_id 时用内容哈希）去重；
   关闭该开关即恢复旧行为。

## 3. 评测数据集说明

- 位置：`eval/datasets/retrieval_questions.jsonl`
- 目标数量：20~30 条；当前仓库没有可用的真实企业文档，因此仅提供
  **5 条标记为 `SAMPLE_REQUIRES_REVIEW` 的示例**，使用前必须人工确认并替换。
- 字段：`id`、`question`、`expected_document_ids`、`expected_document_titles`、
  `expected_keywords`、`tags`、`notes`
- 人工确认清单：`eval/datasets/HUMAN_REVIEW_CHECKLIST.md`
- 数据集校验失败时，评测脚本会**停止运行**并提示问题。

## 4. 运行命令

### 4.1 单元测试（离线）

```powershell
cd backend
$env:WEBUI_SECRET_KEY = (Get-Content .webui_secret_key -Raw).Trim()
..\.venv\Scripts\python.exe -m pytest tests -q
cd ..
..\..\.venv\Scripts\python.exe -m pytest tests -q   # 仓库根 tests/
```

### 4.2 上下文注入验证（离线，无需 LLM）

```powershell
..\.venv\Scripts\python.exe eval/scripts/run_context_injection_eval.py `
  --output eval/results/injection_after.json --mode both
```

### 4.3 检索评测（需要运行中的 Open WebUI + 知识库 + 模型）

```powershell
..\.venv\Scripts\python.exe eval/scripts/run_retrieval_eval.py `
  --dataset eval/datasets/retrieval_questions.jsonl `
  --knowledge-base-id <KB_ID> `
  --output eval/results/retrieval_after.json `
  --mode api --base-url http://localhost:8080 --api-key <KEY> `
  --candidate-k 10 --final-top-n 5
```

### 4.4 RAG 答案审查（原文片段 + 模型答案 + 引用原文）

```powershell
..\.venv\Scripts\python.exe eval/scripts/run_rag_answer_eval.py `
  --dataset eval/datasets/retrieval_questions.jsonl `
  --knowledge-base-id <KB_ID> `
  --output-dir eval/results `
  --mode api --base-url http://localhost:8080 --api-key <KEY> `
  --candidate-k 10 --final-top-n 5 `
  --model-api-base-url <CHAT_API_BASE_URL> --model-api-key <KEY> --model <MODEL_ID>
```

输出 `eval/results/rag_answer_review.json`（结构化）与 `rag_answer_review.md`
（人工复核表）：每题给出问题、标准答案要点、检索到的原文片段（得分与
rerank 前/后命中状态）、模型答案、引用原文、`与答案是否一致：/` 与
`人工复核备注`。想要逐片段观测 rerank 前/后候选，改用 `--mode inprocess`；
API 模式无法观测 rerank 前候选，相关字段记为 `NOT_RUN`。

### 4.5 前后结果对比

```powershell
..\.venv\Scripts\python.exe eval/scripts/compare_results.py `
  --before eval/results/retrieval_before.json `
  --after eval/results/retrieval_after.json `
  --output-dir eval/results
```

## 5. 修改前后结果表

### 5.1 上下文注入（已实际运行，真实结果）

| 指标 | 旧实现 | 新实现 |
|---|---|---|
| 历史消息是否被修改 | 是 | 否 |
| 同轮 Source 注入次数 | 5 | 5 |
| 重复 Source 数 | 1 | 0 |
| RAG 上下文消息数量 | 1（重写用户消息） | 3（初始 1 + 工具增量 2） |
| 结构上符合前缀缓存条件 | 否 | 是 |
| Prompt Token | NOT_RUN（未调用 Provider） | NOT_RUN |
| Provider 缓存统计 | NOT_RUN | NOT_RUN |

> 运行环境：commit `c5a23de5c`、Python 3.12.10、Windows。
> 固定输入：6 条历史消息 + 4 个初始 Sources（含 1 个重复） + 3 次工具调用（含 1 次重复 Source）。
> 具体记录见 `eval/results/injection_before.json`、`injection_after.json` 与 `comparison.md`。

### 5.2 检索指标（待运行）

| 指标 | 纯向量基线 | 纯向量 + Rerank | 差异 |
|---|---|---|---|
| Hit@1 | NOT_RUN | NOT_RUN | NOT_RUN |
| Hit@3 | NOT_RUN | NOT_RUN | NOT_RUN |
| Hit@5 | NOT_RUN | NOT_RUN | NOT_RUN |
| MRR | NOT_RUN | NOT_RUN | NOT_RUN |
| 平均耗时 | NOT_RUN | NOT_RUN | NOT_RUN |
| P95 耗时 | NOT_RUN | NOT_RUN | NOT_RUN |
| Rerank 执行率 | NOT_RUN | NOT_RUN | NOT_RUN |

> 需要真实知识库、Embedding/Rerank 模型与运行中的服务；本仓库开发环境不具备，
> 未伪造任何检索数字。

## 6. 测试结果（已实际运行）

- `backend/tests/`：13 个单元测试，全部通过（含独立 Rerank 与注入行为）。
- `tests/`（仓库根）：22 个验证测试（7 个 Rerank + 9 个注入 + 6 个 RAG 答案审查），全部通过。
- 均使用 mock/fake 组件，不依赖真实云端 LLM。

## 7. 已知限制

- 检索评测未运行：缺少知识库、Embedding/Rerank 模型与运行中的 Open WebUI 服务；
- `/query/collection` API 不返回 chunk id，`returned_chunk_ids` 在 API 模式下为 `null`；
- API 模式默认不含诊断字段；调用时带 `include_debug=true`（评测脚本已默认开启）即可观测
  `rerank_executed`、候选池大小与 pre/post 候选（改进后），否则需用 `--mode inprocess` 的 hook 观测；
- 注入评测的 `prompt_tokens`/缓存字段为 `null`：本次未调用模型 Provider，
  实际缓存命中效果需由支持缓存统计的提供商进一步验证；
- `cache_eligible_by_structure` 只是结构判断，**不代表缓存命中率或成本降低**。

## 8. 结果文件位置

```
eval/results/              # 所有运行产物
  .gitkeep
  injection_after.json     # 注入验证结果（已生成）
  comparison.json          # 前后对比（已生成）
  comparison.md            # 对比报告（已生成）
  retrieval_after.json     # 检索评测（待运行）
  rag_answer_review.json   # RAG 答案审查（待运行）
  rag_answer_review.md     # 人工复核表（待运行）
```

## 9. 简历描述占位模板

> 以下为占位形式，**只有评测完成后才允许填入真实数字**：

- 构建包含 {QUESTION_COUNT} 个问题的检索评测集；
- 纯向量检索加入独立 Rerank 后，Hit@3 从 {BEFORE_HIT3} 变化为 {AFTER_HIT3}；
- 同一轮工具调用重复 Source 数从 {BEFORE_DUPLICATES} 变化为 {AFTER_DUPLICATES}；
- 新上下文注入方式保证历史消息 hash 保持不变；
- 实际缓存命中数据为 {CACHE_RESULT}。
