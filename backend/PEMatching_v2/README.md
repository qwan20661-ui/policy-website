# PEMatching_v2

基于工商数据的科技政策标签匹配链路：企业工商数据 → 4+2 标签体系 → 两轮匹配 → 四通道分流 → 评分排序。

与 `backend/PEMatching/` 零代码耦合：不 import 其模块、不改其文件、不读写其 config 与 output。独立端口（v2 `8020`，老链路 `8010`）。

## 快速开始

```powershell
# 1. 配置
cp backend/PEMatching_v2/.env.example backend/PEMatching_v2/.env
# 编辑 .env 填入 V2_API_KEY

# 2. 依赖（与老链路一致，通常已装）
pip install -r backend/PEMatching_v2/requirements.txt

# 3. 跑测试
python -m pytest backend/PEMatching_v2/tests -q
```

## 文档

见 [docs/README.md](docs/README.md)。总体方案在 [docs/开发计划.md](docs/开发计划.md)，四期开发流程各有一份文档。

## 三条硬约束

1. **`unknown` 与"已核验的真实 0"必须区分**。`ipClass1Count=0` 是确认没有，计 0 分；`=null` 是不知道，该计分项从分母剔除。不造假、不用均值补造。
2. **C 标签只能是 docx 的 C1-C11 及子类编号**，不引入政策库 `industryField` 词表作为匹配轴。
3. **模型不算分、不做单位换算、不决定最终资格**。资格模型只逐条件选择企业事实和证据路径；服务端回读真实值后比较、计分并生成门禁结论。`mode=deterministic` 是一等公民路径。

## 当前进度

一期至四期已完成，链路对外可用。

申报条件匹配重构已完成 T0～T9 的代码与离线验证：新链路会预过滤明确不适用政策、每条政策一次模型判断、服务端复算条件分，并提供实时、批量、缓存和审计能力。当前默认 `PEMATCHING_V2_ENGINE=condition_llm`，`/api/v2/tagmatch/*` 与 `/api/v2/condition-match/*` 均使用新链路；故障时可显式切换为 `legacy` 回滚。

- 一期：确定性底座（画像 + 34 条规则 + 评分器），见 [一期验收报告](docs/一期验收报告.md)
- 二期：政策打标（110 条政策全量打标 + 标签检索），见 [二期验收报告](docs/二期验收报告.md)
- 三期：Agent 与接口（tool-calling 编排 + HTTP 服务 + 批跑 CLI），见 [三期验收报告](docs/三期验收报告.md)
- 四期：全量校准与调优（85/60 阈值、确定性次级排序、规模门槛、复核回写与政策缺口报告），见 [四期开发流程](docs/四期_校准与调优开发流程.md)

四期校准解决的是推荐量、区分度和可复现性。当前已增加独立的逐政策资格规则层：推荐分数只负责候选排序，只有原文条件完整结构化且企业证据全部通过才可标记为严格符合；缺字段为 `unknown`，原文冲突、已不适用或硬条件失败的政策不会进入推荐列表。首批规则覆盖见 `config/policy_eligibility_rules.json`，其余政策会保守标为待核验，不把分布校准误称为语义精度校准。

## 常用命令

```powershell
# 启动服务（端口 8020）
python backend/PEMatching_v2/api_server.py

# 单企业匹配
curl "http://127.0.0.1:8020/api/v2/tagmatch/enterprise?mode=deterministic&topN=5" `
  -X POST -H "Content-Type: application/json" -d "@企业.json"

# 新申报条件匹配链路（默认返回全部推荐，传 topN 后才截断）
curl "http://127.0.0.1:8020/api/v2/condition-match/enterprise?topN=5" `
  -X POST -H "Content-Type: application/json" -d "@企业.json"
python backend/PEMatching_v2/run_condition_match_batch.py --file "企业.json" --workers 2

# 批跑（deterministic 100 家约 2 秒）
python backend/PEMatching_v2/run_tagmatch_batch.py --mode deterministic --limit 100
# 每个批次目录会自动生成：summary.xlsx、推荐链路证据.xlsx、推荐链路证据_简易版.xlsx

# 第五期资格门禁：逐政策将全部条件交给模型抽取企业事实，服务端回读、换算和打分
# Excel 额外包含“资格条件明细”和“资格评分公式”工作表
python backend/PEMatching_v2/run_tagmatch_batch.py `
  --file "backend/PEMatching/企业测试目录/2252_测试企业名称1.json" `
  --mode deterministic --top-n 20 --workers 1 --today 2026-08-05 `
  --with-eligibility --eligibility-llm

# 双模式一致性比对（验收核心指标）
python backend/PEMatching_v2/script/compare_modes.py --limit 20

# 政策打标（增量，指纹未变则跳过）
# 仅供当前旧 tagmatch 链路灰度和回滚；新条件匹配链路不需要执行。
python backend/PEMatching_v2/run_policy_tagging.py

# 生成全量校准数据并评估参数
python backend/PEMatching_v2/script/build_calibration_dataset.py --today 2026-08-04
python backend/PEMatching_v2/script/evaluate_calibration.py --today 2026-08-04

# 测量业务文本覆盖率
python backend/PEMatching_v2/script/measure_text_coverage.py --today 2026-08-04

# 从全量 deterministic 批次生成政策缺口报告
python backend/PEMatching_v2/run_tagmatch_batch.py --mode deterministic --today 2026-08-04
python backend/PEMatching_v2/script/build_policy_gap_report.py --run-dir <批次目录>

# 将人工复核差异写入独立 overrides（先 dry-run）
python backend/PEMatching_v2/run_apply_review.py --file <复核后的Excel> --dry-run
```

## 独立政策库（`V2_POLICY_LIBRARY_DIR`）

需要跑**第二套**政策数据（不与已审核主政策混在一起）时，用环境变量让快照/标签/复核覆盖全部指向独立目录：

```powershell
$env:V2_POLICY_LIBRARY_DIR = "E:\workspace\kejiju-frontend\backend\PEMatching_v2\政策\SYSDBA_POLICY_MD_STRUCTURED"
```

设置后 `policy_snapshot.json`/`policy_tags.json`/`policy_tags_overrides.json` 改从该目录的
`snapshot.json`/`tags.json`/`tags_overrides.json` 读取，主库（`config/` 下，77 条已审核）完全不受影响。
打标、匹配、API 服务都会自动跟随该目录。

示例：把 `政策/SYSDBA_POLICY_MD_STRUCTURED.xlsx`（xmsme.cn 全量申报政策库，988 条）转换为可匹配独立库：

```powershell
# 1. 转换（确定性，不调 LLM）
python backend/PEMatching_v2/script/convert_sysdba_policy_excel.py
#   产出 政策/SYSDBA_POLICY_MD_STRUCTURED/snapshot.json + conversion_report.md

# 2. 打标（生成 tags.json；不强制“审核通过即可申报”，让个人类政策判为不可申报）
$env:V2_POLICY_LIBRARY_DIR = "E:\workspace\kejiju-frontend\backend\PEMatching_v2\政策\SYSDBA_POLICY_MD_STRUCTURED"
$env:PEMATCHING_V2_FORCE_APPLICABLE = "false"
python backend/PEMatching_v2/run_policy_tagging.py --limit 5   # 先小样验证，再全量（988 条 LLM 调用）

# 3. 匹配（政策库指向独立库，推荐来自独立库而非主库）
python backend/PEMatching_v2/run_tagmatch_batch.py --file <企业.json> --limit 1
```

⚠️ 带 `V2_POLICY_LIBRARY_DIR` 时**不要运行 `run_sync_policies.py`**——它会把 API 拉取的主政策
覆盖进独立库快照。独立库数据来源是转换脚本的确定性转换，不是 API。


接口细节见 [接口文档](docs/接口文档.md)，**特别是第 5 节「字段缺失对结果的影响」**——不读这节容易误读分数。
