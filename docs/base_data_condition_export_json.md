# base-data-condition 申报条件 JSON 导出说明

## 目标

`/index.html#base-data-condition` 页面对应的是申报条件字段信息管理，底层数据来自 `SYSDBA.CATEGORY_FIELD_INFO`。

本页现在支持将全部申报条件导出为 JSON，供后续“政策内容 / 申报要求 -> 条件细化抽取”和政策匹配使用。

## 导出入口

在 `pages/base-data.html?type=application_condition` 页面点击“导出”即可下载 JSON 文件。

## 导出结构

导出文件采用以下结构：

```json
{
  "schemaVersion": "v1",
  "source": {
    "page": "/index.html#base-data-condition",
    "moduleType": "application_condition",
    "endpoint": "/api/categoryFieldInfo",
    "table": "SYSDBA.CATEGORY_FIELD_INFO",
    "exportedAt": "2026-07-22T00:00:00.000Z"
  },
  "summary": {
    "total": 0,
    "level1Count": 0,
    "level2Count": 0
  },
  "data": [
    {
      "rowNo": 1,
      "id": "uuid",
      "level1Category": "工商基础信息",
      "level2Category": "基础信息",
      "level3Category": "机构性质",
      "coreField": "企业应为在中国境内注册的居民企业...",
      "status": "启用",
      "createdBy": "",
      "updatedBy": "",
      "createTime": "",
      "updateTime": "",
      "levelPath": ["工商基础信息", "基础信息", "机构性质"],
      "sourcePage": "/index.html#base-data-condition"
    }
  ]
}
```

## 字段说明

| 字段 | 说明 |
|---|---|
| `schemaVersion` | 导出结构版本，便于后续升级兼容 |
| `source` | 数据来源信息，便于追溯 |
| `summary` | 导出汇总信息，便于快速检查数据规模 |
| `data[]` | 申报条件明细列表 |
| `level1Category` | 一级分类 |
| `level2Category` | 二级分类 |
| `level3Category` | 三级分类 |
| `coreField` | 核心字段原文，后续抽取细化条件时优先使用 |
| `levelPath` | 三级路径数组，方便规则匹配和分层归类 |

## 后续使用建议

1. 先按 `level1Category / level2Category / level3Category` 建立条件目录。
2. 再用 `coreField` 作为细化规则的主文本来源。
3. 如果政策文本里出现同义表达、近义表达或上下位表达，可以在后续抽取层做关键词扩展和规则归一。
4. 如需进一步做政策匹配评分，建议在该 JSON 基础上继续补充：
   - 条件类型：硬性 / 软性
   - 条件属性：定量 / 定性 / 布尔
   - 条件权重：用于评分

