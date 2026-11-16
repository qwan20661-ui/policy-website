# 基础数据字典表 - 设计报告

> 设计日期：2026-07-01
> 数据库：达梦数据库 (DM8)
> 主机：127.0.0.1:5237
> Schema：SYSDBA

---

## 一、设计背景

### 1.1 问题来源

"基础数据维护"模块（原使用前端 Mock 数据）需要升级为真实数据库数据。

在检查现有数据库表结构时，发现以下表**不是**我们需要的字典表：

| 现有表名 | 实际用途 | 原因 |
|----------|----------|------|
| POLICY_KEYWORD | 政策关键词**结果表** | 有 POLICY_ID 字段，是某条政策的关键词标注结果 |
| SUPPORT_METHOD | 扶持方式**结果表** | 有 POLICY_ID 字段，是某条政策的扶持方式标注结果 |
| APPLICATION_CONDITION | 申报条件**结果表** | 有 POLICY_ID 字段，是某条政策的申报条件标注结果 |
| POLICY_METADATA | 政策元数据**结果表** | 有 POLICY_ID 字段，是某条政策的元数据 |
| POLICY_TYPE | 政策**分类标注表** | 用于政策归类标注，不是基础字典表 |

### 1.2 设计目标

新建两张基础数据**字典表**，用于支撑：

| 模块 | 页面 | 数据类型 |
|------|------|----------|
| 政策用途管理 | base-data.html?type=policy_usage | policy_usage |
| 政策关键词管理 | base-data.html?type=policy_keyword | policy_keyword |
| 适用对象管理 | base-data.html?type=applicable_object | applicable_object |
| 扶持类型管理 | base-data.html?type=support_type | support_type |
| 适用范围管理 | base-data.html?type=applicable_scope | applicable_scope |
| 申报条件管理 | apply-condition.html | 独立表 |

---

## 二、表结构设计

### 2.1 BASE_DATA_DICT - 基础数据字典表

**用途：** 支撑 5 个基础数据维护页面（政策用途、关键词、适用对象、扶持类型、适用范围）

```sql
CREATE TABLE SYSDBA.BASE_DATA_DICT (
    ID          VARCHAR2(36)   NOT NULL,  -- 主键 UUID
    TYPE        VARCHAR2(50)   NOT NULL,  -- 数据类型：policy_usage, policy_keyword 等
    LEVEL1      VARCHAR2(100)  DEFAULT '', -- 一级分类
    LEVEL2      VARCHAR2(100)  DEFAULT '', -- 二级分类
    NAME        VARCHAR2(200)  NOT NULL,  -- 名称
    CODE        VARCHAR2(100)  DEFAULT '', -- 编码
    DESCRIPTION VARCHAR2(1000) DEFAULT '', -- 范围定义说明
    STATUS      VARCHAR2(20)   DEFAULT '启用', -- 状态：启用/禁用
    CREATE_TIME TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,
    UPDATE_TIME TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ID)
);
```

**索引：**
- `IDX_BASE_DATA_DICT_TYPE` - 按 TYPE 查询
- `IDX_BASE_DATA_DICT_LEVEL1` - 按 LEVEL1 查询
- `IDX_BASE_DATA_DICT_LEVEL2` - 按 LEVEL2 查询
- `IDX_BASE_DATA_DICT_STATUS` - 按 STATUS 查询
- `IDX_BASE_DATA_DICT_CODE` - 按 CODE 查询

**TYPE 取值：**
| TYPE 值 | 对应页面 |
|---------|----------|
| policy_usage | 政策用途管理 |
| policy_keyword | 政策关键词管理 |
| applicable_object | 适用对象管理 |
| support_type | 扶持类型管理 |
| applicable_scope | 适用范围管理 |

### 2.2 APPLICATION_CONDITION_DICT - 申报条件字典表

**用途：** 支撑申报条件管理页面（支持三级分类结构）

```sql
CREATE TABLE SYSDBA.APPLICATION_CONDITION_DICT (
    ID          VARCHAR2(36)   NOT NULL,  -- 主键 UUID
    CODE        VARCHAR2(100)  DEFAULT '', -- 编码
    APPLY_TYPE  VARCHAR2(100)  NOT NULL,  -- 申报类型：高新技术企业认定等
    LEVEL1      VARCHAR2(100)  DEFAULT '', -- 一级分类：工商基础信息、核心团队等
    LEVEL2      VARCHAR2(100)  DEFAULT '', -- 二级分类：基础信息、财务信息等
    LEVEL3      VARCHAR2(100)  DEFAULT '', -- 三级分类：机构性质、企业职工总数等
    NAME        VARCHAR2(200)  NOT NULL,  -- 申报条件名称
    KEYWORDS    VARCHAR2(500)  DEFAULT '', -- 关键词（逗号分隔）
    DESCRIPTION VARCHAR2(1000) DEFAULT '', -- 范围定义说明
    STATUS      VARCHAR2(20)   DEFAULT '启用', -- 状态：启用/禁用
    CREATE_TIME TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,
    UPDATE_TIME TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ID)
);
```

**索引：**
- `IDX_APP_COND_DICT_TYPE` - 按 APPLY_TYPE 查询
- `IDX_APP_COND_DICT_LEVEL1` - 按 LEVEL1 查询
- `IDX_APP_COND_DICT_LEVEL2` - 按 LEVEL2 查询
- `IDX_APP_COND_DICT_LEVEL3` - 按 LEVEL3 查询
- `IDX_APP_COND_DICT_STATUS` - 按 STATUS 查询
- `IDX_APP_COND_DICT_CODE` - 按 CODE 查询

---

## 三、与现有结果表的区别

### 3.1 对比表

| 特性 | BASE_DATA_DICT (新建) | POLICY_KEYWORD (已有) |
|------|----------------------|---------------------|
| 用途 | **字典表** - 基础数据选项 | **结果表** - 政策标注结果 |
| 关联 | 无 POLICY_ID | 有 POLICY_ID |
| 数据量 | 每类型 10-20 条 | 可能有上千条 |
| 维护 | 管理员维护选项 | 系统自动生成/标注 |

| 特性 | APPLICATION_CONDITION_DICT (新建) | APPLICATION_CONDITION (已有) |
|------|----------------------------------|------------------------------|
| 用途 | **字典表** - 申报条件标准字段 | **结果表** - 政策申报条件 |
| 关联 | 无 POLICY_ID | 有 POLICY_ID |
| 结构 | 申报类型 + 三级分类 | 单一结构 |
| 维护 | 管理员维护选项 | 系统自动生成/标注 |

### 3.2 设计原则

**字典表 vs 结果表：**

| 字典表 (本次新建) | 结果表 (已有) |
|------------------|---------------|
| 定义"可选值" | 记录"选择结果" |
| 数据量小，相对固定 | 数据量大，随时增加 |
| 管理员手动维护 | 系统自动生成/用户标注 |
| 可供多个政策复用 | 属于特定政策 |

---

## 四、后续接口对接

### 4.1 JDBCService.java 新增接口

```java
// 1. 获取基础数据字典列表
server.createContext("/api/baseData", new BaseDataDictHandler());

// 2. 获取申报条件字典列表
server.createContext("/api/applyConditions", new ApplyConditionDictHandler());
```

### 4.2 前端调用方式（base-data.html）

```javascript
// 修改前（Mock 数据）
const getRawData = () => {
  return mockData[currentModuleType.value] || [];
};

// 修改后（真实 API）
const API_BASE = 'http://localhost:3000';

const fetchBaseData = async () => {
  loading.value = true;
  try {
    const params = new URLSearchParams({
      type: currentModuleType.value,
      level1: search.level1,
      level2: search.level2,
      keyword: search.keyword,
      page: pagination.page,
      size: pagination.size
    });
    
    const res = await fetch(`${API_BASE}/api/baseData?${params}`);
    const result = await res.json();
    
    if (result.success) {
      tableData.value = result.data;
      pagination.total = result.pagination.total;
      updateStatistics(result.data);
    }
  } catch (err) {
    console.error('获取数据失败:', err);
  } finally {
    loading.value = false;
  }
};
```

### 4.3 API 接口设计

#### /api/baseData

```
GET /api/baseData?type=policy_usage&level1=xxx&level2=xxx&keyword=xxx&page=1&size=10

Response:
{
  "success": true,
  "data": [
    {
      "id": "bd-001-001",
      "type": "policy_usage",
      "level1": "项目支持",
      "level2": "科技项目",
      "name": "高新技术企业认定",
      "code": "PU001",
      "description": "...",
      "status": "启用"
    }
  ],
  "pagination": {
    "page": 1,
    "size": 10,
    "total": 40,
    "pages": 4
  }
}
```

#### /api/applyConditions

```
GET /api/applyConditions?applyType=高新技术企业认定&keyword=xxx&page=1&size=10

Response:
{
  "success": true,
  "data": [
    {
      "id": "ac-001-001",
      "code": "AC001",
      "applyType": "高新技术企业认定",
      "level1": "工商基础信息",
      "level2": "基础信息",
      "level3": "机构性质",
      "name": "机构性质认定",
      "keywords": "机构性质,企业类型,所有制形式",
      "description": "...",
      "status": "启用"
    }
  ],
  "pagination": {...}
}
```

---

## 五、数据统计

### 5.1 BASE_DATA_DICT 初始化数据

| TYPE | 数量 | 说明 |
|------|------|------|
| policy_usage | 8条 | 政策用途 |
| policy_keyword | 8条 | 政策关键词 |
| applicable_object | 8条 | 适用对象 |
| support_type | 8条 | 扶持类型 |
| applicable_scope | 8条 | 适用范围 |
| **合计** | **40条** | |

### 5.2 APPLICATION_CONDITION_DICT 初始化数据

| 申报类型 | 数量 | 说明 |
|----------|------|------|
| 高新技术企业认定 | 5条 | 高企相关申报条件 |
| 科技计划项目 | 2条 | 科技计划相关 |
| 研发费用补助 | 3条 | 研发费用相关 |
| 科技型中小企业 | 2条 | 科小相关 |
| 专精特新企业 | 2条 | 专精特新相关 |
| **合计** | **15条** | |

---

## 六、SQL 执行顺序

```bash
# 1. 先创建表结构
sqlpu base_data_dict_schema.sql

# 2. 再插入初始化数据
sqlpu base_data_dict_init.sql
sqlpu application_condition_dict_init.sql

# 3. 最后验证
sqlpu base_data_dict_verify.sql
```

或使用 JDBCService.java 的 SQL 执行接口。

---

## 七、注意事项

### 7.1 兼容性

- 使用 VARCHAR2 而非 VARCHAR（达梦兼容）
- 使用 TIMESTAMP 而非 DATETIME（达梦兼容）
- 避免使用 COMMENT ON 语法

### 7.2 数据安全

- 本次 SQL 只创建/删除新建的两张表
- 不删除任何现有表（POLICY_KEYWORD、SUPPORT_METHOD 等）
- ID 使用固定字符串，便于追踪

### 7.3 后续扩展

- 可根据需要增加更多字段
- 可添加唯一索引（UNIQUE INDEX）防止重复
- 可添加触发器自动更新 UPDATE_TIME

---

## 八、文件清单

| 文件 | 说明 |
|------|------|
| `sql/base_data_dict_schema.sql` | 建表脚本 |
| `sql/base_data_dict_init.sql` | BASE_DATA_DICT 初始化数据（40条） |
| `sql/application_condition_dict_init.sql` | APPLICATION_CONDITION_DICT 初始化数据（15条） |
| `sql/base_data_dict_verify.sql` | 验证脚本 |
| `docs/base_data_dict_design_report.md` | 本设计文档 |

---

*报告生成时间：2026-07-01*