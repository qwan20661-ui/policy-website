# PEMatching 重构说明
pkill -f api_server.py
## 当前数据源

- 企业数据：默认调用政策推荐企业分页接口 `/cms/tb-base-business-info/web/page`
- 企业 JSON：仅用于离线回放或通过参数显式覆盖
- 企业字段说明：`backend/PEMatching/Enterprisedata/数据说明.md`
- 企业字段中文映射：`backend/PEMatching/config/policy_recommendation_field_map.json`（模型输入使用）
- 政策数据：`/index.html#structure` 对应的结构化接口
- 结构化接口文档：`docs/结构化接口文档.md`

## 新结构

- `service/enterprise_loader.py`
  - 负责分页读取政策推荐企业接口（或离线企业 JSON），完成单位换算并标准化为匹配侧统一企业画像。
- `service/policy_loader.py`
  - 负责通过结构化列表接口 + 详情接口拉取主政策，并提取结构化申报条件。
- `service/matching_service.py`
  - 负责编排企业与政策的匹配任务，只走结构化规则表判定链路。
- `service/rule_table_catalog_service.py`
  - 负责筛出已经生成 `RULE_TABLE_JSON` 的主政策列表，并提供规则表详情标准化能力。
- `api_server.py`
  - 提供轻量 HTTP 后端接口，便于页面或其他服务直接调用。

## 启动方式

### 1. 启动后端服务

```bash
python3 backend/PEMatching/api_server.py
```

> ⚠️ 注意：代码使用了 Python 3.6+ 的类型注解语法，**不支持 Python 2**。如果服务器上 `python` 指向 Python 2，请使用 `python3` 命令启动。可通过 `python3 --version` 确认版本 >= 3.6。

默认地址：

```text
http://127.0.0.1:8010
```

### 2. 直接跑企业匹配政策

脚本默认读取政策推荐企业接口，建议在 `.env` 中配置：

```env
PEMATCHING_ENTERPRISE_SOURCE=api
PEMATCHING_ENTERPRISE_API_BASE_URL=http://112.48.19.225:8500
PEMATCHING_ENTERPRISE_API_PATH=/cms/tb-base-business-info/web/page
PEMATCHING_ENTERPRISE_API_PAGE_SIZE=100
PEMATCHING_ENTERPRISE_API_MAX_PAGES=0
PEMATCHING_ENTERPRISE_API_TIMEOUT=15
```

接口返回的注册资本单位是万元，经营信息金额单位是元；适配层会将营业收入、主营业务收入、研发费用、净利润、净资产和技术合同成交额统一换算为万元后再进入规则匹配。

企业接口是企业事实边界：接口未提供的资产、成本费用比例、知识产权分类等字段保持为空和“缺失”状态，不使用 `enterprise_feature_defaults.json` 均值补造；接口明确返回的零值按真实值保留。

金额、年限等下限型条件存在真实值但未达标时，继续按成长性公式形成匹配分。企业接口完全缺失字段，或政策要求指定年度而接口只有其他年度数据时，不使用其他年度冒充政策年度，也不写入虚构企业值；该条件按召回优先口径默认企业具备，记 70 分并允许推荐。真实直接证据仍可得到更高分，缺失默认分不会高于有证据且满足条件的得分。结果通过 `默认假定条件项数` 和 `推荐状态说明` 提示业务人员后续核验；明确反证、真实上限超标或模型调用失败仍会阻止推荐。

模型事实使用稳定的 `semanticItemKey` 关联，原始 `itemId` 仅用于展示，因此同一政策存在重复 `itemId` 时不会覆盖事实。默认 `SEMANTIC_MATCH_BATCH_SIZE=1`，即每条申报条件独立调用一次模型；完成固定快照 A/B 审计后，才可将同一政策中最多 10 条条件组成微批。模型只回传证据路径，服务端从本次企业候选快照回填原始值；模型漏项会单项补偿，非法 JSON、重复键或超时会二分回退，额外请求受单政策预算限制。每个请求仍携带条件组原文、组操作符、条件上下文、分支说明和同组分支，确保互斥分支不能同时命中。政策限定年份时不能使用其他年度企业数据替代。`MAX_WORKERS` 是唯一模型请求并发闸门。

### 独立政策关联匹配

设置 `POLICY_ASSOCIATION_LLM_ENABLED=true` 后，系统会在全部申报条件匹配任务结束后，对同一批企业和政策执行第二阶段关联判断。第二阶段只读取现有企业对象和政策摘要，不改变企业输入、`final_score`、`是否推荐`、条件明细或默认排序；内部结果写入 `associationMatch`。公共企业接口返回 `score`、`level`、`isAssociated`、`status`、最多三条 `reasons`，以及不含企业/政策原始证据的 `calculation` 可复算说明。

关联阶段与申报条件阶段严格串行，两个阶段分别使用同一个 `MAX_WORKERS` 上限。未启用或缺少模型配置时仍返回稳定的 `associationMatch.status=disabled`，不会调用模型。关联模型可通过 `POLICY_ASSOCIATION_API_URL`、`POLICY_ASSOCIATION_API_KEY`、`POLICY_ASSOCIATION_MODEL_NAME`、`POLICY_ASSOCIATION_LLM_TIMEOUT`、`POLICY_ASSOCIATION_LLM_RETRIES` 和 `POLICY_ASSOCIATION_LLM_RETRY_BACKOFF` 独立配置；未单独设置时复用现有模型配置。

语义模型发生读取超时或连接失败时默认重试 2 次（共请求 3 次）；仍失败则结果标记为 `匹配失败待重试`，且 `是否推荐=null`。可通过 `SEMANTIC_MATCH_LLM_RETRIES` 和 `SEMANTIC_MATCH_LLM_RETRY_BACKOFF` 调整重试次数和递增退避秒数。

固定企业和政策快照完成两组运行后，可使用 `python script/benchmark_enterprise_match.py --baseline <优化前runId归档.json> --candidate <优化后runId归档.json> --output <差异报告.json>` 对照最终推荐及逐 `semanticItemKey` 的状态、证据路径、数值、单位和分支。无差异时退出码为 `0`，存在差异时为 `2`，便于接入发布前检查。

当前测试服务存在 `pageNum` 不推进的兼容性问题。适配层仍优先按文档分页；仅在检测到重复页时，才按 `totalCount` 发起一次受控全量请求（默认最多 10000 条），避免静默漏数。

配置好后，直接试跑全部主政策：

```bash
python3 backend/PEMatching/run_enterprise_policy_match.py --all-policies
```

如果只想先看数据是否加载成功，不执行实际匹配：

```bash
python3 backend/PEMatching/run_enterprise_policy_match.py --validate-only --all-policies
```

如果接口返回多家企业，可以指定企业名：

```bash
python3 backend/PEMatching/run_enterprise_policy_match.py --enterprise-name "A公司" --all-policies
```

离线排查时仍可显式切回企业 JSON：

```bash
python3 backend/PEMatching/run_enterprise_policy_match.py --enterprise-source json --enterprise-json backend/PEMatching/Enterprisedata/test.json --all-policies
```

如果只想先小范围试跑，再用关键词和数量收窄：

```bash
python3 backend/PEMatching/run_enterprise_policy_match.py --policy-keyword 新型研发机构 --policy-limit 2
```

### 2.1 批量运行“企业测试目录”并支持断点续跑

`企业测试目录`中的四位序号企业 JSON 可使用目录批跑器逐文件请求 `POST /api/pematching/match/enterprise`。每家企业收到 HTTP 响应后都会立即原样保存响应 JSON，同时原子更新运行状态；进程中断、机器重启或单家企业失败后，均可使用同一运行目录继续，已完成且输入文件未变化的企业会自动跳过。

```bash
# 先小批量验证 3 家企业，命令结束后记下“运行目录”
python backend/PEMatching/run_enterprise_test_directory.py --limit 3

# 完整批跑，默认请求本机 8010 的企业匹配 API
python backend/PEMatching/run_enterprise_test_directory.py

# 从上次运行目录断点续跑；失败或中断的企业会重试
python backend/PEMatching/run_enterprise_test_directory.py \
  --resume \
  --run-dir backend/PEMatching/output/企业测试目录批跑/20260729_120000
```

运行目录中包含：`响应结果/*_响应.json`、`失败记录/*_失败.json`、`运行状态.json` 和 `批跑日志.log`。`运行状态.json`记录源文件 SHA-256、尝试次数、HTTP 状态、接口 `runId`、输出路径和失败原因；因此即使源文件同名但内容发生变化，续跑也会重新处理它。接口自身还会按 `runId` 在服务端归档完整匹配明细。

如需每家企业匹配指定主政策，直接传递企业匹配 API 的查询参数：

```bash
python backend/PEMatching/run_enterprise_test_directory.py \
  --policy-id 6617 \
  --policy-limit 1
```

### 3. 查看已生成规则表的主政策

列出已经生成规则表 JSON 的主政策：

```bash
python3 backend/PEMatching/script/run_rule_table_catalog.py --list --limit 20
```

查看指定主政策的规则表详情：

```bash
python3 backend/PEMatching/script/run_rule_table_catalog.py --detail 123456789
```

### 4. 批量生成 structure 页全部主政策的申报条件规则

该脚本会先按 `/index.html#structure` 的主政策列表口径拉取全部政策，再对每条
政策调用页面同一个 `POST /api/rule-table/generate` 接口。生成成功后默认调用
详情保存接口写回 `RULE_TABLE_JSON`，因此在结构页重新打开详情即可看到规则表。

本机的结构化详情接口和页面网关是两个端口：默认从 `.env` 的
`STRUCTURE_API_BASE_URL`（通常为 `http://127.0.0.1:8080`）读取/保存详情，规则
生成请求默认发给 Node 网关 `http://127.0.0.1:3000`。后者可通过
`RULE_TABLE_API_BASE_URL` 或 `--rule-table-api-base-url` 覆盖，不要把规则生成请求
直接发到 8080。

```bash
python3 backend/PEMatching/run_generate_rule_tables.py
```

先全量检查“每条政策是否可以生成可用于匹配的申报条件规则”，但不写入数据库：

```bash
python3 backend/PEMatching/run_generate_rule_tables.py --check-only
```

常用的断点续跑方式，只处理当前还没有规则表的主政策：

```bash
python3 backend/PEMatching/run_generate_rule_tables.py --skip-existing --sleep-seconds 1
```

非默认部署可显式传入两个地址：

```bash
python3 backend/PEMatching/run_generate_rule_tables.py \
  --structure-api-base-url http://127.0.0.1:8080 \
  --rule-table-api-base-url http://127.0.0.1:3000
```

每次运行会在 `backend/PEMatching/logs/` 产生同名时间戳的 `.log`、`.json` 和
`.csv`：CSV 的“申报条件规则是否可用”列给出逐政策结论；JSON 保留每个条件项
状态、接口错误和写回状态，便于排查。默认筛选 `reviewStatus=1`，与 structure
页面主政策列表口径一致；需要扩大范围时可明确传入 `--review-status all`。

规则表生成会同步等待大模型返回，Node 网关内部最长等待 600 秒。批处理默认等待
660 秒，不能沿用短于模型生成耗时的 60 秒超时；如当前模型响应更慢，可显式提高：

```bash
python3 backend/PEMatching/run_generate_rule_tables.py --timeout 900
```

## 后端接口

### 健康检查

```http
GET /health
```

### 查看企业列表

```http
GET /api/pematching/enterprises
```

默认从企业接口读取；需要检查离线数据时可请求 `GET /api/pematching/enterprises?enterpriseSource=json`。

### 预览结构化政策列表

```http
GET /api/pematching/policies?limit=10&keyword=新型研发机构
```

### 查看已生成规则表的主政策列表

```http
GET /api/pematching/rule-tables?limit=20&keyword=科技型
```

### 查看指定主政策的规则表详情

```http
GET /api/pematching/rule-tables/detail?fusionPolicyId=123456789
```

### 执行匹配

```http
POST /api/pematching/match
Content-Type: application/json
```

请求体示例：

```json
{
  "enterpriseNames": ["厦门市巨龙软件工程有限公司"],
  "enterpriseSource": "api",
  "policyKeyword": "新型研发机构",
  "policyLimit": 3,
  "includeDocumentContent": false,
  "persistOutput": true
}
```

### 只匹配已经生成规则表的主政策

```http
POST /api/pematching/match/rule-tables
Content-Type: application/json
```

请求体示例：

```json
{
  "enterpriseSource": "api",
  "enterpriseNames": ["A公司"],
  "policyKeyword": "科技型中小企业",
  "policyLimit": 5,
  "persistOutput": false
}
```

### 单企业政策匹配接口

调用方从政策推荐企业分页接口的 `data.list` 中取出一条企业原始记录，直接作为请求体：

```http
POST /api/pematching/match/enterprise
Content-Type: application/json
```

```json
{
  "id": "123",
  "enterpriseName": "A公司",
  "creditCode": "91350200XXXXXXXXXX",
  "registerAddress": "厦门市思明区软件园",
  "operationInfoList": [
    {
      "year": "2025",
      "operatingIncome": 40000000,
      "rdExpense": 3000000,
      "rdPersonnelRatio": "0.1481"
    }
  ],
  "mainTechnologyList": [],
  "mainProductList": [],
  "honorQualificationList": []
}
```

默认匹配全部已经生成 `RULE_TABLE_JSON` 的主政策。可通过查询参数缩小范围，例如：

```http
POST /api/pematching/match/enterprise?policyId=6617&policyLimit=1
```

`creditCode` 可省略，`enterpriseName` 是唯一必填字段。响应中的 `data.policies` 是面向企业画像和推荐列表的轻量政策数组；完整跑数 JSON 会归档到服务端 `output/enterprise_match_details/`，通过 `meta.runId` 排查。完整契约见 `docs/企业政策匹配接口文档.md`。

## 说明

- 新链路默认优先使用结构化详情里的 `RULE_TABLE_JSON` 做逐项规则判定：每个申报条件单独调用模型提取事实，再由本地规则引擎汇总评分。
- 若当前主政策还没有 `RULE_TABLE_JSON`，系统会退回本地规则表文件，不再调用旧的整政策 LLM 匹配脚本。
- 结构化详情里的 `conditions`、`cluster`、`materials`、`supportMethods` 仍作为规则表生成和辅助展示的数据来源。
- `POST /api/pematching/match/rule-tables` 会只选择已经存在 `RULE_TABLE_JSON` 的主政策，企业数据默认来自政策推荐企业接口。
- 请求体显式传入 `enterpriseJsonPath` 时会自动切换为 JSON 数据源，兼容已有离线调用。
- 企业接口未提供的资产、纳税等字段不会被写成企业真实值；评分时按召回优先口径默认具备、记 70 分并允许推荐，同时明确标记默认假定项供后续核验。
- 如需切回旧的本地政策 JSON，可把 `.env` 中的 `POLICY_SOURCE` 改成 `local_json`。
