# extractdata standalone

这个目录是 `metadata/extractdata.py` 的独立运行版。现在支持两件事同时完成：

1. 边抽取边输出 JSON。
2. 每条抽取完成后立即写入达梦数据库的多张业务表。

## 目录结构

- `metadata/extractdata.py`：命令行主入口。
- `metadata/api_server.py`：HTTP 接口入口。
- `metadata/db_importer.py`：单条 JSON 到数据库导入封装。
- `metadata/prompts/`：结构化抽取提示词。
- `metadata/Attribute/`：关键词后处理依赖的属性文件。
- `metadata/json2/merged_output.json`：行业分类辅助匹配数据。
- `metadata/input/`：默认输入目录。
- `metadata/output/`：默认输出目录。
- `scripts/stage_policy_data11.py`：把单条 JSON 映射成多张表 TSV。
- `scripts/DmPolicyData11Importer.java`：把 TSV 写入达梦数据库。
- `demo/lib/jdbc/DmJdbcDriver8.jar`：达梦 JDBC 驱动。

## 写入的数据库表

导入器会按顺序写入这些表：

`POLICY`、`POLICY_METADATA`、`POLICY_KEYWORD`、`APPLICATION_CONDITION`、`SUPPORT_METHOD`、`APPLICATION_MATERIAL`、`APPLICATION_ATTACHMENT`、`CONDITION_CLUSTER`、`MAIN_TECHNOLOGY`、`MAIN_PRODUCT`、`CORE_TEAM`、`INNOVATION_INVESTMENT`、`INNOVATION_OUTPUT`、`INNOVATION_DEVELOPMENT`、`INNOVATION_IMPACT`。

导入逻辑：

- 每条政策抽取成功后立即生成单条 JSON。
- 同时刷新 `metadata/output/all_policy_data.json` 汇总文件。
- 再生成该条数据的临时 TSV，目录为 `METADATA_DB_IMPORT_STAGE_DIR`。
- 最后调用 Java/JDBC 导入达梦库。
- 默认不重复插入已有 `POLICY`，会按 `FILE_NAME + POLICY_TITLE` 判断并跳过。

## 环境准备

```powershell
cd D:\Kejiju\kejiju\extractdata_standalone
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

入库需要本机能执行：

```powershell
java -version
javac -version
```

## 配置

配置文件是 `metadata/.env`。常用项：

```env
METADATA_INPUT_DIRS=metadata/input
METADATA_OUTPUT_DIR=metadata/output
METADATA_PROMPT_FILE=metadata/prompts/policy_extract.md

METADATA_DB_HOST=127.0.0.1
METADATA_DB_PORT=5237
METADATA_DB_USER=SYSDBA
METADATA_DB_PASSWORD=***

METADATA_DB_IMPORT_ENABLED=true
METADATA_DB_IMPORT_DRY_RUN=false
METADATA_DB_IMPORT_STAGE_DIR=metadata/output/_db_stage
METADATA_DB_IMPORT_FORCE_INSERT_POLICIES=false
```

`METADATA_DB_IMPORT_DRY_RUN=true` 会执行完整导入链路，但最后回滚事务，适合联调。

`METADATA_DB_IMPORT_FORCE_INSERT_POLICIES=true` 会强制新增 `POLICY`，一般不要开启，除非明确要保留重复版本。

## 命令行运行

默认：抽取并入库。

```powershell
.\run_extractdata.ps1
```

只输出 JSON，不入库：

```powershell
.\run_extractdata.ps1 -NoDbImport
```

测试入库链路但回滚：

```powershell
.\run_extractdata.ps1 -DbDryRun
```

单文件模式：

```powershell
.\run_extractdata.ps1 -File metadata\input\your_file.md
```

## HTTP 接口

启动接口：

```powershell
python metadata\api_server.py
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

批量抽取并按配置入库：

```powershell
$body = @{
  input_dirs = @('metadata/input')
  output_dir = 'metadata/output'
  batch_size = 1
  max_workers = 1
  import_to_db = $true
  db_dry_run = $false
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/extract `
  -Method Post `
  -ContentType 'application/json; charset=utf-8' `
  -Body $body
```

请求字段：

- `env_file`：可选，配置文件路径。
- `file`：可选，单文件路径。
- `input_dirs`：可选，批量输入目录数组。
- `output_dir`：可选，输出目录。
- `batch_size`：可选，批量参数。
- `max_workers`：可选，并发参数。
- `import_to_db`：可选，是否入库；不传则使用 `.env`。
- `db_dry_run`：可选，是否导入后回滚；不传则使用 `.env`。

## 本次验证结果

已在 `metadata/input` 的 1 条示例数据上测试：

- 命令行抽取成功，模型接口返回 200。
- JSON 输出成功：
  - `metadata/output/input/2021年 厦门市科学仪器设备资源共享管理办法（政策已到期）.json`
  - `metadata/output/all_policy_data.json`
- 数据库导入链路成功，返回 `insertedPolicies=0, skippedPolicies=1`，表示该政策主表记录已存在，本次按去重规则跳过。
- 接口 `/health` 返回 `{"status":"ok"}`。
- 接口 `/extract` 单条调用成功，返回 `status=success` 和输出 JSON 路径。
