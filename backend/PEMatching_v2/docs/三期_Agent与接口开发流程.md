# 三期开发流程：Agent 与接口

> 目标：把一期的确定性能力包装成工具交给 LLM 编排，产出可调用的 HTTP 接口与批跑 CLI。本期结束时链路对外可用。
>
> 依据 [开发计划.md](开发计划.md) 第 7、8 节。前置：[一期](一期_确定性底座开发流程.md)、[二期](二期_政策打标开发流程.md) 已验收。

## 0. 前置条件

- 一期二期 DoD 全部达成，`config/policy_tags.json` 有真实数据
- LLM 网关支持 tool-calling：已实测 `minimax2.7` 可用，`qwen3-8b`/`qwen3.6-35b` 传 `tools` 报 400（vLLM 未开 `--enable-auto-tool-choice`）
- 端口 `8020` 未被占用（老链路占 `8010`）

## 1. 任务顺序

```
T1 llm_client.py             tool-calling 客户端
 └─ T2 agent.py 工具注册层    把一期函数包装成 tool schema
     └─ T3 agent.py 循环控制  多轮往返 + 轮次上限 + 降级
         ├─ T4 agent_system_prompt.md
         └─ T5 api_server.py  三个 endpoint
             ├─ T6 run_tagmatch_batch.py
             └─ T7 docs/接口文档.md
                 └─ T8 验收
```

---

## 2. T1：llm_client.py

### 2.1 函数签名

```python
def chat(messages: List[Dict], tools: List[Dict] | None = None,
         tool_choice: str = "auto", response_format: Dict | None = None,
         timeout: int | None = None, event_log=None) -> Dict
    """单次 LLM 调用。返回 choices[0].message 原始 dict（含 content 与 tool_calls）。"""
def chat_with_retry(..., max_retries: int = 2) -> Dict
    """带指数退避的重试。"""
```

### 2.2 实现要点

| 要点 | 说明 |
|---|---|
| 标准库 `urllib.request` | 与老链路一致，不引入 `requests` 之外的依赖（`requests` 虽在 requirements 里但老链路 LLM 调用用的是 urllib） |
| `tools` 为 None 时不传该字段 | 传 `tools: null` 部分网关会报错 |
| `tool_calls` 的 `arguments` 是 JSON **字符串** | 必须 `json.loads`，且要处理模型输出非法 JSON 的情况（返回 `None` 让上层降级） |
| 重试只针对网络错误与 5xx | 4xx 是请求本身有问题，重试无意义，直接抛 |
| 退避 | `V2_LLM_BACKOFF`（默认 2 秒）× 2^n |
| 每次调用写日志 | 请求 messages 长度、tool_calls 名称与参数、响应 token 数、耗时，写入 `event_log` |

### 2.3 测试 `tests/test_llm_client.py`

- mock 正常响应（含 tool_calls），断言解析结果
- mock `arguments` 是非法 JSON，断言返回 None 而非抛异常
- mock 500 → 重试 → 成功，断言重试次数与最终结果
- mock 400，断言立即抛出不重试
- `tools=None` 时断言请求体里无 `tools` 字段

---

## 3. T2：工具注册层

### 3.1 五个工具（开发计划 7.2）

每个工具需要三样东西：JSON Schema（给模型看）、执行函数（包装一期代码）、结果序列化（控制回填给模型的体积）。

```python
TOOLS = [
    {"name": "build_enterprise_profile", "schema": {...}, "handler": _handle_build_profile},
    {"name": "run_channel_rules",        "schema": {...}, "handler": _handle_run_rules},
    {"name": "search_policies",          "schema": {...}, "handler": _handle_search},
    {"name": "score_policy",             "schema": {...}, "handler": _handle_score},
    {"name": "get_policy_detail",        "schema": {...}, "handler": _handle_detail},
]
```

### 3.2 结果体积控制（关键）

模型上下文有限，工具返回值必须裁剪。这是 tool-calling 方案能立住的前提（开发计划 7.1）。

| 工具 | 完整结果 | 回填给模型的 |
|---|---|---|
| `build_enterprise_profile` | 含 `fieldAvailability` 全量 | 标签 + metrics + `dataCompleteness` 汇总；`fieldAvailability` 只回 `unknown` 的字段名列表 |
| `run_channel_rules` | 34 条规则完整明细 | 命中规则的 `ruleId`+`signals`、跳过规则的 `ruleId`+`skipReason`；未命中的只回 ID 列表 |
| `search_policies` | 政策摘要列表 | 已在二期裁剪（`summary` 前 200 字），此处再限 `limit` 条 |
| `score_policy` | 完整 `scoreExplanation` | 总分 + 档位 + 各项名称与得分；`basis` 保留（模型写理由要用），`excludedItems` 只回名称 |
| `get_policy_detail` | 政策全量 | 申报条件 + 材料 + 支持方式，**去掉** `relatedPolicies` 与 `documents` |

**完整结果仍需保留**：写入 `logs/` 与最终响应的 `data`，只是不回填给模型。这样审计信息不丢，上下文也不炸。

### 3.3 状态传递

`build_enterprise_profile` 产出的 profile 需被后续工具复用。**不让模型在参数里传递整个 profile**（体积大且易被模型篡改），改为：

- Agent 内部维护 `session_state = {"profile": ..., "ruleResult": ...}`
- 工具 schema 里 `run_channel_rules` / `score_policy` **不接受 profile 参数**，handler 从 `session_state` 取
- 若模型在 profile 未构建时就调 `score_policy`，handler 返回错误提示"请先调用 build_enterprise_profile"，让模型自我纠正

这条设计同时防住了"模型伪造 profile 参数来操纵分数"。

### 3.4 测试 `tests/test_agent_tools.py`

- 五个工具的 schema 都是合法 JSON Schema（`type`/`properties`/`required` 齐全）
- 每个 handler 在正常输入下返回预期结构
- `score_policy` 在 `session_state` 无 profile 时返回错误提示而非抛异常
- 结果裁剪生效：断言回填结果不含被裁剪字段（如 `get_policy_detail` 结果无 `relatedPolicies`）

---

## 4. T3：循环控制

### 4.1 主循环

```python
def run_agent(record: Dict, keyword: str = "", enable_stage2: bool = False,
              top_n: int = 20, event_log=None) -> Dict
```

流程：

```
1. 初始化 session_state、messages（system + user）
2. loop（最多 V2_AGENT_MAX_TURNS 轮，默认 12）：
     a. chat_with_retry(messages, tools=TOOLS)
     b. 有 tool_calls → 逐个执行 handler → 结果 append 为 role=tool 消息 → continue
     c. 无 tool_calls 且有 content → 尝试解析最终输出 → 成功则 break
     d. 无 tool_calls 且无 content → 计入 idle_count
3. 收尾：组装响应，标记 turns / truncated / degraded
```

### 4.2 降级触发条件（开发计划 7.3）

四种情况切 `deterministic`：

| 触发 | 判定 | 标记 |
|---|---|---|
| 模型连续 2 轮不调工具 | `idle_count >= 2` | `degraded: true, reason: "model_idle"` |
| 最终输出不可解析 | 连续 2 次解析失败 | `degraded: true, reason: "unparseable_output"` |
| 超轮次 | `turns >= MAX_TURNS` 且未产出最终输出 | `truncated: true` + 走 deterministic 补齐 |
| LLM 调用彻底失败 | `chat_with_retry` 抛异常 | `degraded: true, reason: "llm_unavailable"` |

降级后**仍尝试让模型写解释**（单轮调用，不带 tools）；若连这一步也失败，`reason` 字段用模板文案填充，接口仍返回 200 与完整分数。

### 4.3 deterministic 模式

```python
def run_deterministic(record, keyword="", enable_stage2=False, top_n=20, event_log=None) -> Dict
```

固定顺序：`build_profile` → `evaluate_rules` → `search_policies`（用 `aggregatedSignals`）→ 逐条 `score_policy` → 按总分排序取 `top_n`。

**这是一等公民路径**（开发计划决策 3 + 13 节风险表），不只是降级兜底：
- `mode=deterministic` 可由调用方直接指定
- 是回归测试的主要手段（不依赖模型，结果可复现）
- 是 T8 验收里"agent 与 deterministic 重合度 ≥90%"的基准侧

### 4.4 审计日志

每轮写入 `logs/<requestId>.log`：轮次序号、模型 `tool_calls`（名称+参数）、handler 执行耗时、返回结果摘要、token 用量。格式与老链路 `_RequestEventLog` 类似但 v2 自己实现（不跨目录 import）。

### 4.5 测试 `tests/test_agent.py`

用录制的 tool_calls 序列回放，不依赖真实模型。必须覆盖：

1. **正常多轮**：mock 5 轮（profile → rules → search → score×3 → 最终输出），断言 `turns=5`、`degraded=False`、推荐列表非空
2. **模型不调工具**：mock 连续 2 轮返回空 content 无 tool_calls，断言切 deterministic 且 `reason="model_idle"`
3. **返回不可解析**：mock 最终输出是非法 JSON，断言 `reason="unparseable_output"`
4. **超轮次**：mock 12 轮都在调工具从不收尾，断言 `truncated=True` 且仍返回完整分数
5. **LLM 不可用**：mock `chat_with_retry` 抛异常，断言 `degraded=True` 且接口不失败
6. **模型伪造参数**：mock 模型在未调 profile 时直接调 `score_policy`，断言 handler 返回纠正提示
7. **deterministic 独立跑通**：不 mock 任何 LLM，断言能出完整结果

---

## 5. T4：agent_system_prompt.md

### 5.1 必须写进 prompt 的内容

| 内容 | 为什么 |
|---|---|
| 禁止自行计算分数 | 模型算术不可靠且不可复现（开发计划 7.1） |
| 禁止自行判断规则命中 | 规则判定是确定性逻辑，模型判会与引擎结果冲突 |
| 禁止推荐 `search_policies` 未返回的政策 | 防幻觉政策 |
| **字段缺失是常态**，遇 `unknown` 不推测填补 | 本项目字段缺失面很大（`enterpriseCredit` 100% 空、`industryLevel2` 仅 30.8%），模型倾向于"合理推测"，必须明令禁止 |
| 建议调用顺序，但允许重复检索 | 首轮检索结果不理想时应换标签组合再试，这是 Agent 相对 deterministic 的主要增量价值 |
| 最终输出契约 | 推荐排序、分数与档位、理由、命中规则、补数建议、跳过项说明 |

### 5.2 最终输出格式

要求模型输出 JSON（不用 tool-calling 收尾，直接 content 出 JSON）：

```json
{
  "recommendations": [
    {"fusionPolicyId": "6534", "rank": 1,
     "reason": "该企业已获国家级高新技术企业资质，本政策为重新认定环节…"}
  ],
  "supplementSuggestions": [
    {"field": "techContractAmount", "note": "补充技术合同登记额可解锁技术交易奖励类政策"}
  ],
  "summary": "…一段话总结…"
}
```

**模型只出 `reason` / `note` / `summary` 这些文案字段**，分数、档位、命中规则全部由 Agent 从工具结果里取，不信模型转述。这是防越权的结构性保障。

### 5.3 测试 `tests/test_prompt_contract.py`（扩展二期的同名文件）

- prompt 含"禁止"相关约束字样
- prompt 含 `unknown` 处理说明
- prompt 里的输出 JSON 示例本身可被 `json.loads` 解析

---

## 6. T5：api_server.py

### 6.1 三个 endpoint

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/health` | 回显端口、模型名、`policy_tags.json` 版本与条数、`policy_snapshot.json` 拉取日期、配置项 |
| POST | `/api/v2/tagmatch/enterprise` | 单企业匹配，请求体为企业接口响应对象 |
| POST | `/api/v2/tagmatch/batch` | 批量，请求体为企业对象数组，内部复用单企业逻辑 |
| GET | `/api/v2/policy-tags` | 查看已打标政策与标签分布，排查用 |

### 6.2 实现要点

- `ThreadingHTTPServer` + `BaseHTTPRequestHandler`，路由用 `if parsed.path == ...` 链（与老链路一致）
- 统一响应 `{success, data, meta, message}` + CORS 头
- 异常映射：`ValueError`/`FileNotFoundError` → 400，其他 → 500
- 每请求生成 `requestId`（`uuid4().hex[:12]`），贯穿日志与响应 `meta`
- 查询参数：`keyword` / `enableStage2` / `topN` / `mode`，解析失败给 400 并说明合法取值
- **响应必须含 `meta.dataCompleteness`**（开发计划 8.1）：让调用方看出结果建立在多少信息量上

### 6.3 batch 接口的并发与限制

- 上限 `V2_BATCH_MAX_SIZE`（默认 50），超出给 400
- 内部线程池 `V2_MAX_WORKERS`
- 单个企业失败不影响其余，失败项在响应里带 `error` 字段
- `mode=agent` 的 batch 耗时会很长（单企业 30-60 秒 × N / 并发数），文档里要写明并建议大批量用 CLI

### 6.4 测试 `tests/test_api_server.py`

- `/health` 返回 200 且含必要字段
- 单企业接口：正常请求返回 200 与完整结构
- 请求体非法 JSON → 400
- 请求体缺 `enterpriseName` → 400 并说明
- `mode=invalid` → 400 并列出合法值
- `topN` 非数字 → 400
- batch 超上限 → 400
- batch 部分失败 → 200 且失败项带 `error`
- 断言响应含 `meta.requestId` / `meta.dataCompleteness`

测试用 mock 掉 `run_agent`，只验接口层（Agent 逻辑由 T3 测试覆盖）。

---

## 7. T6：run_tagmatch_batch.py

```powershell
python backend/PEMatching_v2/run_tagmatch_batch.py --dir "backend/PEMatching/企业测试目录" --limit 100
python backend/PEMatching_v2/run_tagmatch_batch.py --file path/to/one.json --mode agent
python backend/PEMatching_v2/run_tagmatch_batch.py --mode deterministic --resume <runId>
```

- 断点续跑：`output/tagmatch/<runId>/_progress.json` 记已完成文件名，`--resume` 跳过
- 每企业一个 JSON：`output/tagmatch/<runId>/<序号>_<企业名>.json`
- 汇总 Excel：`output/tagmatch/<runId>/summary.xlsx`，列出企业名、`dataCompleteness`、推荐条数、Top1 政策与分数、档位分布、命中规则数、跳过规则数、`degraded`/`truncated` 标记
- `--mode deterministic` 不调模型，用于快速回归

**测试** `tests/test_run_tagmatch_batch.py`：断点续跑生效、单文件失败不中断、Excel 生成成功。

---

## 8. T7：接口文档

`docs/接口文档.md`，面向调用方。内容：

- 端口与基础路径
- 每个 endpoint 的请求/响应完整示例（用真实数据跑出来的，不手写）
- 查询参数说明与默认值
- **字段缺失对结果的影响说明**——调用方需理解 `unknown` 语义、`effectiveWeight`、`dataCompleteness`，否则会误读分数
- `mode=agent` 与 `mode=deterministic` 的区别与选用建议
- 错误码表
- 耗时预期（agent 30-60 秒，deterministic < 2 秒）

---

## 9. T8：验收

### 9.1 自动化

```powershell
python -m pytest backend/PEMatching_v2/tests -q
python -m pytest backend/PEMatching/test_rule_engine_growth_scoring.py backend/PEMatching/test_semantic_rule_scoring.py -q   # 老链路未受影响
```

### 9.2 双模式一致性（核心验收项）

取 20 家企业，同时跑 `mode=agent` 与 `mode=deterministic`，比对：

| 指标 | 要求 | 不达标说明什么 |
|---|---|---|
| 推荐政策**集合**重合度 | ≥90% | Agent 只应影响排序与解释。集合差异大说明模型越权——可能在编造政策或擅自过滤，需收紧 prompt |
| 同一政策的**分数** | 100% 相同 | 分数由确定性函数算，两模式必须完全一致。不一致说明 Agent 路径下 profile 或 signals 被污染 |
| 档位分布 | 一致 | 同上 |

比对脚本：`script/compare_modes.py`，输出差异明细到 `output/mode_comparison.xlsx`。

### 9.3 稳定性

- 100 家企业 `mode=agent` 批跑无中断，记录 P50/P95/P99 耗时
- 统计 `degraded` / `truncated` 触发率。若 `degraded` > 10%，说明 prompt 或模型配合有问题，需排查而非放行
- 降级路径可被主动触发验证（临时把 `V2_API_URL` 指向不可达地址，断言接口仍返回 200 与完整分数）

### 9.4 完成定义（DoD）

- [ ] `tests/` 全绿，覆盖 2.3/3.4/4.5/5.3/6.4 列出的每个用例
- [ ] 双模式重合度 ≥90%，分数 100% 一致
- [ ] 100 家批跑无中断，耗时数据记录在案
- [ ] `degraded` 触发率 ≤10%
- [ ] 降级路径实测可用（断网测试）
- [ ] `docs/接口文档.md` 完成，示例为真实跑出的数据
- [ ] 对 `backend/PEMatching/` 改动为 0

---

## 10. 本期不做

- 阈值与权重校准 → 四期
- 关键词库扩充 → 四期
- 落库 → 四期
- 前端对接 → 不在本链路范围

## 11. 风险

| 风险 | 应对 |
|---|---|
| 模型越权：擅自算分或编造政策 | 结构性防护：分数只从工具结果取，模型只出文案字段（5.2）；`session_state` 不让模型传 profile（3.3）；双模式一致性验收（9.2）能量化检出 |
| Agent 耗时不可接受 | 提供 `mode=deterministic`（< 2 秒）；batch 大量场景引导用 CLI |
| minimax2.7 单点依赖 | deterministic 是完整路径而非兜底；降级链路实测验证（9.3） |
| 上下文膨胀导致截断 | 工具结果裁剪（3.2）；`get_policy_detail` 按需调用而非预加载全部 |
| 模型反复调同一工具刷轮次 | `MAX_TURNS` 硬上限；同一工具同参数连续调用 3 次时 handler 返回提示"已调用过，结果同上" |
