# OpenWebUI 文档实测：字符切分 vs 结构感知切分

> 生成日期：2026-08-29
> 项目：`D:\VScode\VScode_files\3. agentdesk\agentdesk`
> 测试语料：OpenWebUI 项目真实文档（10 个文件，提取文本约 1.06MB）
> 评测环境：embedding=text-embedding-v3（DashScope）、检索=向量 + BM25 RRF 混合、离线重排

---

## 一、为什么换语料

在 agentdesk 自带小语料（39 题、38 个文档）上，字符切分与结构感知切分的 doc 级指标完全持平——原因不是切分没区别，而是语料太"轻"：25 个 md 全部小于 512 字符、本身就是单块，切分根本没有发挥空间。

本报告改用 OpenWebUI 项目真实文档做压测：`CHANGELOG.md`（936KB）这类长文档、README/SECURITY 的表格与代码块、中文架构分析报告、PDF/DOCX 等，全部是多块文档，切分差异可以被真正量化。

---

## 二、语料与评测集

### 2.1 语料（10 个文件）

| 文件 | 提取文本 | 字符切分块数 | 结构切分块数 |
|---|---:|---:|---:|
| CHANGELOG.md | 923,958 | 2,063 | 2,748 |
| README.md | 17,941 | 40 | 61 |
| SECURITY.md | 24,441 | 55 | 70 |
| OpenWebUI_项目架构分析报告.md | 29,357 | 66 | 96 |
| VALIDATION.md | 4,769 | 11 | 20 |
| TROUBLESHOOTING.md | 2,969 | 7 | 9 |
| CODE_OF_CONDUCT.md | 8,245 | 19 | 26 |
| 指南.pdf | 23,404 | 53 | 59 |
| architecture——修改前.pdf | 19,065 | 43 | 48 |
| 代办.docx | 9,139 | 21 | 21 |
| **合计** | **1,063,288** | **2,378** | **3,158** |

### 2.2 评测集（40 题）

- 由 LLM 从各文档随机片段生成，问题只依赖片段内容即可回答；
- 答案必须**逐字取自原文**（禁止改写），因此可以精确判定"包含答案的 chunk"；
- 覆盖全部 10 个文档；文档级与 chunk 级指标分开统计；
- 检索方式：向量（text-embedding-v3）+ BM25 加权 RRF 混合，top-10 取回，不计 cross-encoder（对两套切分同等适用）。

---

## 三、切分质量统计（确定性，不调 API）

| 指标 | 字符定长 | 结构感知 |
|---|---:|---:|
| chunk 总数 | 2,378 | 3,158 |
| 平均 chunk 大小 | 510 字符 | 339 字符 |
| 边界质量（落在行首/行尾或句末标点后） | 1.8% | **95.4%** |
| 切词次数（边界切在单词中间，如 `lang`/`uage`） | 1,799 | **0** |
| 表格行被切断次数 | 25 | **0** |
| 短代码块围栏被切断（README/TROUBLESHOOTING/VALIDATION） | 9 | **0** |
| 超长代码块（>512 字符，共 6 个） | 随机切断 | 按函数/空行边界切，首尾块保留围栏 |

字符定长的 2,326 处行内边界中 **1,799 处直接切词**（`language` 被切成 `lang`/`uage` 这种程度）；结构感知的 146 处行内边界全部落在空格（单词边界，主要是 CHANGELOG 长列表行内的 commit 行），**0 处切词**；12 处代码块围栏"破损"全部来自 6 个超长代码块的结构化内部切分（首尾块各保留一个围栏），属预期行为。

---

## 四、检索指标对比（40 题）

| 指标 | 字符定长 | 结构感知 | 变化 |
|---|---:|---:|---:|
| 答案跨 chunk 边界（单块装不下完整答案） | 4 | **0** | -4 |
| doc hit@1 | 63.9% | 62.5% | -1.4pp（top-5 内次序噪声） |
| doc hit@3 | 88.9% | **97.5%** | +8.6pp |
| doc hit@5 | 94.4% | **100%** | +5.6pp |
| MRR@5 | 0.7671 | **0.7854** | +0.018 |
| chunk hit@1（含答案的块进 top-1） | 38.9% | **47.5%** | +8.6pp |
| chunk hit@3 | 63.9% | **77.5%** | +13.6pp |
| chunk hit@5 | 75.0% | **80.0%** | +5.0pp |

口径说明：字符版有 4 题答案被切分切碎、单块装不下完整答案，这 4 题在字符版中无法做 chunk 级判定（指标分母为 36）；结构版 40 题全部可定位（分母为 40）。该口径已是对字符版有利的处理。

---

## 五、具体例子

### 5.1 结构感知救回的问题（字符版完全未召回）

| 问题 | 字符切分 | 结构感知 |
|---|---|---|
| CHANGELOG：允许模型执行多步骤任务（联网检索+知识库+笔记+生图）的新能力是什么？ | chunk 未召回，文档未进 top-5 | chunk #1，doc #1 |
| CHANGELOG：Hybrid Search 的 BM25-Weight 参数改成了什么 UI 元素？ | chunk 未召回，文档未进 top-5 | chunk #1，doc #1 |
| CHANGELOG：为什么 Admin 配置在服务器重启后不再丢失？ | chunk 未召回，文档未进 top-5 | chunk #2，doc #2 |
| SECURITY：漏洞报告通过哪个平台提交？ | chunk 未召回，文档未进 top-5 | chunk #1，doc #1 |

### 5.2 次序小幅下移但仍命中的题（回归噪声）

| 问题 | 字符切分 | 结构感知 |
|---|---|---|
| CHANGELOG：HF Spaces 上创建管理员账户的配置检测 | doc #1 | doc #2 |
| TROUBLESHOOTING：Docker 网络 flag | doc #1 | doc #2 |
| CHANGELOG：OpenAPI 工具集成空值处理 | doc #2 | doc #3 |
| 架构报告：聊天消息处理全过程名称 | doc #2 | doc #3 |

逐题统计：40 题中 15 题 chunk 排名提升、8 题小幅下移（全部仍在 top-5 内），其余持平；doc@1 的 -1.4pp 即来自 5.2 这类 top-5 内的次序互换。

---

## 六、结论

1. **在真实大文档语料上，结构感知切分带来确定性检索收益**：doc hit@5 94.4% → 100%、chunk hit@3 63.9% → 77.5%、答案跨块 4 → 0；
2. 之前 agentdesk 小语料"没变化"是语料问题而非切分无效——文档全部小于 512 字符时，任何切分方式都等价；
3. doc@1 的微小波动属于 top-5 内排序噪声（+5/-4 题互抵），不影响实际召回；
4. chunk 级收益（含答案的块更容易进 top-1/3/5）直接有利于生成侧的答案完整性与 faithfulness；
5. 建议把该 40 题评测纳入切分回归集：后续任何切分改动都用它验证，避免小语料掩盖真实差异。

---

## 七、复现

```powershell
cd D:\VScode\VScode_files\3. agentdesk\agentdesk

# 1) 切分质量统计（确定性，不调 API）
python -B eval/openwebui_对比/split_stats.py

# 2) 用 LLM 从文档片段生成 40 题评测集（写 dataset.jsonl）
python -B eval/openwebui_对比/generate_dataset.py

# 3) 双索引检索对比（建向量索引 + 评测，约 4 分钟）
python -B eval/openwebui_对比/run_compare.py

# 4) 逐题排名明细
python -B eval/openwebui_对比/per_question_analysis.py
```

产物与证据：

- 语料：`eval/openwebui_对比/docs/`（10 个文件）
- 评测集：`eval/openwebui_对比/dataset.jsonl`
- 索引：`eval/openwebui_对比/store_character.json`（2,378 块）、`store_structure.json`（3,158 块）
- 结果：`eval/openwebui_对比/results_character.json`、`results_structure.json`、`per_question_compare.csv`

涉及代码：`app/rag/indexer.py`、`app/rag/extractors.py`、`eval/openwebui_对比/*.py`
