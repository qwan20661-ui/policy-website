# Markdown 政策结构化导入说明

本目录用于把 `fusiondata` 下的 Markdown 政策文件批量导入达梦数据库。

## 目录格式

导入工具默认扫描仓库根目录下的 `fusiondata`，推荐每个政策一个文件夹：

```text
fusiondata/
  2026年行业龙头企业年会、峰会补助项目/
    2026年行业龙头企业年会、峰会补助项目.md
    attachments/
      附件1：2026年行业年会、峰会项目备案表.xls
      附件2：项目资金申请表.xls
      系统操作手册.doc
```

Markdown 中的附件链接应使用相对路径：

```md
- [附件1：2026年行业年会、峰会项目备案表.xls](attachments/附件1：2026年行业年会、峰会项目备案表.xls)
```

## 运行方式

第一次使用前，先建表。

如果主表和附件表都还没有创建，执行：

```sql
policy_md_structured_schema.sql
```

如果主表 `SYSDBA.POLICY_MD_STRUCTURED` 已存在，只需要补附件表，执行：

```sql
structured_attachment_schema.sql
```

批量导入默认目录：

```bat
policy_md_structured_import\run_import_policy_md_structured.bat
```

指定导入目录：

```bat
policy_md_structured_import\run_import_policy_md_structured.bat fusiondata
```

脚本会自动编译并运行：

```bat
javac -encoding UTF-8 -cp "lib\jdbc\DmJdbcDriver8.jar" -d target "policy_md_structured_import\ImportPolicyMdStructured.java"
java -cp "target;lib\jdbc\DmJdbcDriver8.jar" ImportPolicyMdStructured "fusiondata"
```

默认数据库连接：

```text
POLICY_MD_DB_URL=jdbc:dm://127.0.0.1:5236
POLICY_MD_DB_USER=SYSDBA
POLICY_MD_DB_PASSWORD=[已移除凭据]
```

需要换库时，在运行前设置环境变量即可：

```bat
set POLICY_MD_DB_URL=jdbc:dm://host:5237
set POLICY_MD_DB_USER=SYSDBA
set POLICY_MD_DB_PASSWORD=your_password
policy_md_structured_import\run_import_policy_md_structured.bat fusiondata
```

## 去重规则

主表导入前会检查：

```sql
SOURCE_MD_PATH = 当前 Markdown 相对路径
OR CONTENT_HASH = 当前 Markdown 文件 SHA-256
```

已存在且 `DEL_FLAG='0'` 时跳过主表插入。

附件表导入前会检查同一个 `STRUCTURED_ID` 下：

```sql
FILE_PATH = 附件工作区相对路径
OR MARKDOWN_RELATIVE_PATH = Markdown 链接相对路径
OR CONTENT_HASH = 附件文件 SHA-256
```

已存在且 `DEL_FLAG='0'` 时跳过附件插入。

## 主表字段

表名：`SYSDBA.POLICY_MD_STRUCTURED`

| 字段名 | 类型 | 说明 |
|---|---|---|
| `ID` | `VARCHAR2(36)` | 主键 UUID |
| `SOURCE_MD_PATH` | `VARCHAR2(1000)` | Markdown 文件在工作区内的相对路径 |
| `SOURCE_FILE_NAME` | `VARCHAR2(500)` | Markdown 文件名 |
| `CONTENT_HASH` | `VARCHAR2(64)` | Markdown 文件 SHA-256 |
| `POLICY_TITLE` | `VARCHAR2(500)` | 政策标题 |
| `POLICY_LEVEL` | `VARCHAR2(100)` | 政策层级 |
| `PUBLISH_REGION` | `VARCHAR2(100)` | 发布地区 |
| `POLICY_BATCH` | `VARCHAR2(100)` | 政策批次 |
| `ISSUE_DEPARTMENT` | `VARCHAR2(500)` | 主管部门 |
| `APPLICANT_OBJECT` | `VARCHAR2(200)` | 申请对象；Markdown 没有该字段时为空 |
| `SUBSIDY_OBJECT` | `VARCHAR2(200)` | 补贴对象；Markdown 没有该字段时为空 |
| `INDUSTRY_CATEGORY` | `VARCHAR2(1000)` | 行业类别 |
| `ENTERPRISE_SCALE` | `VARCHAR2(100)` | 企业规模 |
| `DECLARE_TYPE` | `VARCHAR2(100)` | 申报类型 |
| `SUPPORT_METHOD` | `VARCHAR2(100)` | 支持方式 |
| `FILE_BASIS` | `VARCHAR2(1000)` | 文件依据 |
| `DECLARE_START_TIME` | `TIMESTAMP` | 申报开始时间 |
| `DECLARE_END_TIME` | `TIMESTAMP` | 申报结束时间 |
| `ORIGINAL_URL` | `VARCHAR2(2000)` | 原文链接 |
| `APPLICATION_CONDITION` | `CLOB` | 申报条件，按分点保留换行 |
| `SUPPORT_CONTENT` | `CLOB` | 扶持内容 |
| `APPLICATION_PROCESS` | `CLOB` | 申报流程，按分点保留换行 |
| `APPLICATION_MATERIALS` | `CLOB` | 申报材料，按分点保留换行 |
| `CONTACT_INFO` | `CLOB` | 咨询电话和联系方式 |
| `ATTACHMENTS_JSON` | `CLOB` | Markdown 附件链接 JSON 快照 |
| `DEL_FLAG` | `CHAR(1)` | 删除标记：`0` 正常，`1` 删除 |
| `CREATED_BY` | `VARCHAR2(100)` | 创建操作人 |
| `UPDATED_BY` | `VARCHAR2(100)` | 最后更新操作人 |
| `CREATED_AT` | `TIMESTAMP` | 创建时间 |
| `UPDATED_AT` | `TIMESTAMP` | 更新时间 |

## 附件表字段

表名：`SYSDBA.STRUCTURED_ATTACHMENT`

| 字段名 | 类型 | 说明 |
|---|---|---|
| `ID` | `VARCHAR2(36)` | 主键 UUID |
| `STRUCTURED_ID` | `VARCHAR2(36)` | 关联 `POLICY_MD_STRUCTURED.ID` |
| `SOURCE_MD_PATH` | `VARCHAR2(1000)` | 来源 Markdown 文件相对路径 |
| `ATTACH_NAME` | `VARCHAR2(500)` | 附件实际文件名 |
| `ATTACH_SHOW_NAME` | `VARCHAR2(500)` | Markdown 中展示的附件名称 |
| `MARKDOWN_RELATIVE_PATH` | `VARCHAR2(1000)` | Markdown 链接里的附件相对路径，例如 `attachments/xxx.xls` |
| `FILE_PATH` | `VARCHAR2(1000)` | 附件文件在工作区内的相对路径 |
| `FILE_SUFFIX` | `VARCHAR2(30)` | 附件后缀 |
| `FILE_SIZE` | `BIGINT` | 附件文件字节数 |
| `CONTENT_HASH` | `VARCHAR2(64)` | 附件文件 SHA-256 |
| `DEL_FLAG` | `CHAR(1)` | 删除标记：`0` 正常，`1` 删除 |
| `CREATED_BY` | `VARCHAR2(100)` | 创建操作人 |
| `UPDATED_BY` | `VARCHAR2(100)` | 最后更新操作人 |
| `CREATED_AT` | `TIMESTAMP` | 创建时间 |
| `UPDATED_AT` | `TIMESTAMP` | 更新时间 |

## 常用核对 SQL

查看最近导入的 Markdown：

```sql
SELECT ID, SOURCE_MD_PATH, POLICY_TITLE, CREATED_AT
FROM SYSDBA.POLICY_MD_STRUCTURED
WHERE NVL(DEL_FLAG, '0') = '0'
ORDER BY CREATED_AT DESC;
```

查看某条 Markdown 的附件：

```sql
SELECT
  p.ID,
  p.POLICY_TITLE,
  a.ATTACH_NAME,
  a.MARKDOWN_RELATIVE_PATH,
  a.FILE_PATH,
  a.FILE_SIZE
FROM SYSDBA.POLICY_MD_STRUCTURED p
JOIN SYSDBA.STRUCTURED_ATTACHMENT a
  ON a.STRUCTURED_ID = p.ID
WHERE p.SOURCE_MD_PATH = 'fusiondata/2026年行业龙头企业年会、峰会补助项目/2026年行业龙头企业年会、峰会补助项目.md'
  AND NVL(p.DEL_FLAG, '0') = '0'
  AND NVL(a.DEL_FLAG, '0') = '0'
ORDER BY a.ATTACH_NAME;
```
