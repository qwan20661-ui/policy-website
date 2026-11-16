# 脚本目录使用说明

本目录只存放 PEMatching 相关的一次性数据处理、导入导出、接口采集、调试和审计脚本。脚本默认从仓库根目录执行，命令中的路径均以仓库根目录为基准。

政策申报条件规则生成属于主流程前置脚本，不放在本目录，入口位于
`backend/PEMatching/run_generate_rule_tables.py`，使用说明见
`backend/PEMatching/README.md` 的“批量生成 structure 页全部主政策的申报条件规则”章节。

## 工具清单

| 脚本 | 用途 | 默认是否写入数据 |
|---|---|---|
| `run_rule_table_catalog.py` | 查看已生成规则表的政策列表和详情 | 否 |
| `benchmark_enterprise_match.py` | 对比两份企业匹配归档的结果差异 | 仅按参数写报告 |
| `export_matching_result_excel.py` | 将匹配 JSON 离线导出为 Excel | 写入 Excel 文件 |
| `run_fusion_policy_6548_coverage_test.py` | 运行成长性政策覆盖测试 | 写入测试结果 |
| `run_fusion_policy_semantic_match.py` | 运行指定融合政策的语义匹配测试 | 写入测试结果 |
| `_generate_model_request_example.py` | 生成模型请求示例数据 | 写入 `docs` 示例文件 |
| `check_data.py` | 查看科小测试数据的年度和企业数量 | 否 |
| `fetch_tech_sme_enterprises.py` | 获取科技型中小企业并生成测试文件 | 是 |

正式服务入口、企业主匹配入口、企业测试目录批跑器和自动化测试仍保留在
`backend/PEMatching/` 根目录，不在本目录重复维护。

## 查看已生成规则表

```powershell
# 查看前 20 条已生成规则表的政策
python .\backend\PEMatching\script\run_rule_table_catalog.py --list --limit 20

# 查看指定主政策的规则表详情
python .\backend\PEMatching\script\run_rule_table_catalog.py --detail 123456789
```

## 结果审计和导出

对比优化前后的企业匹配归档：

```powershell
python .\backend\PEMatching\script\benchmark_enterprise_match.py `
  --baseline <优化前归档.json> `
  --candidate <优化后归档.json> `
  --output <差异报告.json>
```

将 CLI 结果或接口归档导出为 Excel：

```powershell
python .\backend\PEMatching\script\export_matching_result_excel.py `
  --input <匹配结果.json> `
  --output <匹配结果.xlsx>
```

## 专项测试和示例

成长性评分覆盖测试：

```powershell
python .\backend\PEMatching\script\run_fusion_policy_6548_coverage_test.py
```

指定融合政策运行语义匹配：

```powershell
python .\backend\PEMatching\script\run_fusion_policy_semantic_match.py `
  --policy-id 6548 `
  --enterprise-file <企业数据.json> `
  --output-file <匹配结果.json>
```

生成指定政策条件的模型请求示例：

```powershell
python .\backend\PEMatching\script\_generate_model_request_example.py
```

查看科小测试数据概况：

```powershell
python .\backend\PEMatching\script\check_data.py
```

## 环境准备

使用 Python 3.10 或更高版本，并安装 PEMatching 所需依赖：

```powershell
Set-Location E:\workspace\kejiju-frontend
python -m pip install -r .\backend\PEMatching\requirements.txt
```

## 获取科技型中小企业

脚本：`backend/PEMatching/script/fetch_tech_sme_enterprises.py`

该脚本请求企业分页接口：

```text
http://112.48.19.225:8500/cms/tb-base-business-info/web/page
```

执行命令：

```powershell
Set-Location E:\workspace\kejiju-frontend
python .\backend\PEMatching\script\fetch_tech_sme_enterprises.py
```

脚本会先请求第 1 页，根据接口返回的 `totalCount` 计算总页数，然后使用 `pageSize=100` 遍历全部页面。企业的 `honorQualificationList` 中任意一条记录满足 `isTechSme == "是"` 时，该企业会被保留。

## 输出文件

为保持与现有企业测试批跑器的目录约定一致，输出仍保存在：

```text
backend/PEMatching/企业测试目录/
```

其中：

- `tech_sme_enterprises.json`：全部匹配企业的汇总文件，`totalCount` 为匹配数量。
- `0001_企业名称.json`：每家企业一个独立 JSON 文件，文件名前四位数字为序号。

如果需要使用这些企业继续调用本地 PEMatching 企业匹配接口，可以执行：

```powershell
python .\backend\PEMatching\run_enterprise_test_directory.py --limit 3
```

确认小批量结果无误后，再执行完整批跑：

```powershell
python .\backend\PEMatching\run_enterprise_test_directory.py
```

## 注意事项

- 当前脚本会遍历全部分页，不是只处理某一个页码。
- 脚本使用接口返回值的精确判断 `isTechSme == "是"`；如果上游返回的是接口文档中的编码值 `"1"`，不会被该脚本纳入。
- 脚本会覆盖同名的汇总文件和企业 JSON 文件；执行前请确认已有输出是否需要保留。
- 接口不可达或单页请求失败时，脚本会打印错误并跳过失败页面；运行结束后应检查控制台输出和汇总数量。
