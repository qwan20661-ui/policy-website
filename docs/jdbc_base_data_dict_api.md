# JDBC 基础数据字典接口文档

> 服务端口：8080
> 数据库：达梦数据库 127.0.0.1:5237
> Schema：SYSDBA
> 更新日期：2026-07-01

---

## 一、接口概览

| 接口 | 方法 | 说明 | 表 |
|------|------|------|-----|
| `/api/baseDataDict` | GET | 获取基础数据列表 | BASE_DATA_DICT |
| `/api/baseDataDict/create` | POST | 创建基础数据 | BASE_DATA_DICT |
| `/api/baseDataDict/update` | POST | 更新基础数据 | BASE_DATA_DICT |
| `/api/baseDataDict/delete` | POST | 删除基础数据 | BASE_DATA_DICT |
| `/api/applicationConditionDict` | GET | 获取申报条件列表 | APPLICATION_CONDITION_DICT |
| `/api/applicationConditionDict/create` | POST | 创建申报条件 | APPLICATION_CONDITION_DICT |
| `/api/applicationConditionDict/update` | POST | 更新申报条件 | APPLICATION_CONDITION_DICT |
| `/api/applicationConditionDict/delete` | POST | 删除申报条件 | APPLICATION_CONDITION_DICT |

---

## 二、统一返回格式

```json
{
  "success": true,
  "data": [],
  "message": "",
  "pagination": {
    "page": 1,
    "size": 10,
    "total": 40,
    "pages": 4
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| success | boolean | 操作是否成功 |
| data | array | 返回的数据列表 |
| message | string | 提示信息 |
| pagination | object | 分页信息 |

---

## 三、基础数据字典接口 (BASE_DATA_DICT)

### 3.1 获取基础数据列表

**接口：** `GET /api/baseDataDict`

**参数：**

| 参数名 | 类型 | 必填 | 说明 | 示例 |
|--------|------|------|------|------|
| type | string | 否 | 数据类型 | `policy_usage` |
| level1 | string | 否 | 一级分类 | `项目支持` |
| level2 | string | 否 | 二级分类 | `科技项目` |
| keyword | string | 否 | 关键词搜索 | `高新技术` |
| page | int | 否 | 页码，默认1 | `1` |
| size | int | 否 | 每页条数，默认10 | `10` |

**type 可选值：**
| type | 说明 |
|------|------|
| `policy_usage` | 政策用途 |
| `policy_keyword` | 政策关键词 |
| `applicable_object` | 适用对象 |
| `support_type` | 扶持类型 |
| `applicable_scope` | 适用范围 |

**示例请求：**
```bash
# 获取政策用途数据（第一页，每页10条）
curl "http://localhost:8080/api/baseDataDict?type=policy_usage&page=1&size=10"

# 搜索关键词
curl "http://localhost:8080/api/baseDataDict?type=policy_usage&keyword=高新技术&page=1&size=10"

# 按一级分类筛选
curl "http://localhost:8080/api/baseDataDict?type=policy_usage&level1=项目支持&page=1&size=10"
```

**响应示例：**
```json
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
      "description": "针对高新技术企业的认定管理工作",
      "status": "启用",
      "createTime": "2026-07-01 10:00:00",
      "updateTime": "2026-07-01 10:00:00"
    }
  ],
  "message": "",
  "pagination": {
    "page": 1,
    "size": 10,
    "total": 40,
    "pages": 4
  }
}
```

---

### 3.2 创建基础数据

**接口：** `POST /api/baseDataDict/create`

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| type | string | ✅ | 数据类型 |
| name | string | ✅ | 名称 |
| level1 | string | 否 | 一级分类 |
| level2 | string | 否 | 二级分类 |
| code | string | 否 | 编码 |
| description | string | 否 | 范围定义说明 |
| status | string | 否 | 状态，默认"启用" |

**示例请求：**
```bash
curl -X POST "http://localhost:8080/api/baseDataDict/create" \
  -H "Content-Type: application/json" \
  -d '{"type":"policy_usage","level1":"项目支持","level2":"科技项目","name":"新技术企业认定","code":"PU099","description":"测试数据","status":"启用"}'
```

**响应示例：**
```json
{
  "success": true,
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000"
  },
  "message": "创建成功",
  "pagination": {}
}
```

---

### 3.3 更新基础数据

**接口：** `POST /api/baseDataDict/update`

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | ✅ | 记录ID |
| type | string | 否 | 数据类型 |
| name | string | 否 | 名称 |
| level1 | string | 否 | 一级分类 |
| level2 | string | 否 | 二级分类 |
| code | string | 否 | 编码 |
| description | string | 否 | 范围定义说明 |
| status | string | 否 | 状态 |

**示例请求：**
```bash
curl -X POST "http://localhost:8080/api/baseDataDict/update" \
  -H "Content-Type: application/json" \
  -d '{"id":"bd-001-001","name":"高新技术企业认定（修订版）","status":"启用"}'
```

**响应示例：**
```json
{
  "success": true,
  "data": [],
  "message": "更新成功",
  "pagination": {}
}
```

---

### 3.4 删除基础数据

**接口：** `POST /api/baseDataDict/delete`

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | ✅ | 记录ID |

**示例请求：**
```bash
curl -X POST "http://localhost:8080/api/baseDataDict/delete" \
  -H "Content-Type: application/json" \
  -d '{"id":"bd-001-001"}'
```

**响应示例：**
```json
{
  "success": true,
  "data": [],
  "message": "删除成功",
  "pagination": {}
}
```

---

## 四、申报条件字典接口 (APPLICATION_CONDITION_DICT)

### 4.1 获取申报条件列表

**接口：** `GET /api/applicationConditionDict`

**参数：**

| 参数名 | 类型 | 必填 | 说明 | 示例 |
|--------|------|------|------|------|
| applyType | string | 否 | 申报类型 | `高新技术企业认定` |
| level1 | string | 否 | 一级分类 | `工商基础信息` |
| level2 | string | 否 | 二级分类 | `基础信息` |
| level3 | string | 否 | 三级分类 | `机构性质` |
| keyword | string | 否 | 关键词搜索 | `研发投入` |
| page | int | 否 | 页码，默认1 | `1` |
| size | int | 否 | 每页条数，默认10 | `10` |

**示例请求：**
```bash
# 获取所有申报条件
curl "http://localhost:8080/api/applicationConditionDict?page=1&size=10"

# 按申报类型筛选
curl "http://localhost:8080/api/applicationConditionDict?applyType=高新技术企业认定&page=1&size=10"

# 按一级分类筛选
curl "http://localhost:8080/api/applicationConditionDict?level1=工商基础信息&page=1&size=10"

# 搜索关键词
curl "http://localhost:8080/api/applicationConditionDict?keyword=研发投入&page=1&size=10"
```

**响应示例：**
```json
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
      "description": "企业应为在中国境内（不包括港、澳、台地区）注册的居民企业，且申请认定时须注册成立一年以上",
      "status": "启用",
      "createTime": "2026-07-01 10:00:00",
      "updateTime": "2026-07-01 10:00:00"
    }
  ],
  "message": "",
  "pagination": {
    "page": 1,
    "size": 10,
    "total": 15,
    "pages": 2
  }
}
```

---

### 4.2 创建申报条件

**接口：** `POST /api/applicationConditionDict/create`

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| applyType | string | ✅ | 申报类型 |
| name | string | ✅ | 申报条件名称 |
| level1 | string | 否 | 一级分类 |
| level2 | string | 否 | 二级分类 |
| level3 | string | 否 | 三级分类 |
| code | string | 否 | 编码 |
| keywords | string | 否 | 关键词（逗号分隔） |
| description | string | 否 | 范围定义说明 |
| status | string | 否 | 状态，默认"启用" |

**示例请求：**
```bash
curl -X POST "http://localhost:8080/api/applicationConditionDict/create" \
  -H "Content-Type: application/json" \
  -d '{"applyType":"高新技术企业认定","level1":"创新投入","level2":"研发费用","level3":"年研发投入","name":"研发投入比例要求","code":"AC016","keywords":"研发投入,比例,销售收入","description":"企业研发费用占销售收入比例要求","status":"启用"}'
```

**响应示例：**
```json
{
  "success": true,
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440001"
  },
  "message": "创建成功",
  "pagination": {}
}
```

---

### 4.3 更新申报条件

**接口：** `POST /api/applicationConditionDict/update`

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | ✅ | 记录ID |
| applyType | string | 否 | 申报类型 |
| name | string | 否 | 申报条件名称 |
| level1 | string | 否 | 一级分类 |
| level2 | string | 否 | 二级分类 |
| level3 | string | 否 | 三级分类 |
| code | string | 否 | 编码 |
| keywords | string | 否 | 关键词 |
| description | string | 否 | 范围定义说明 |
| status | string | 否 | 状态 |

**示例请求：**
```bash
curl -X POST "http://localhost:8080/api/applicationConditionDict/update" \
  -H "Content-Type: application/json" \
  -d '{"id":"ac-001-001","name":"机构性质认定（修订版）","description":"更新后的描述"}'
```

**响应示例：**
```json
{
  "success": true,
  "data": [],
  "message": "更新成功",
  "pagination": {}
}
```

---

### 4.4 删除申报条件

**接口：** `POST /api/applicationConditionDict/delete`

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | ✅ | 记录ID |

**示例请求：**
```bash
curl -X POST "http://localhost:8080/api/applicationConditionDict/delete" \
  -H "Content-Type: application/json" \
  -d '{"id":"ac-001-001"}'
```

**响应示例：**
```json
{
  "success": true,
  "data": [],
  "message": "删除成功",
  "pagination": {}
}
```

---

## 五、错误码说明

| HTTP 状态码 | success | message | 说明 |
|-------------|---------|---------|------|
| 200 | true | "操作成功" | 请求成功 |
| 400 | false | "缺少必填字段: xxx" | 参数缺失 |
| 404 | false | "xxx失败，记录不存在" | 记录不存在 |
| 405 | false | "只支持 POST 方法" | 请求方法错误 |
| 500 | false | SQL错误信息 | 服务器内部错误 |

---

## 六、Java 端编译运行

### 6.1 编译

```bash
cd D:/app/demo/demo

# 编译 Java 文件（需要包含达梦 JDBC 驱动）
# 假设达梦驱动在 lib/dm-jdbc.jar
javac -cp ".;lib/*" JDBCService.java

# 或不带类路径（Java 11+ 会自动加载）
javac JDBCService.java
```

### 6.2 运行

```bash
# 运行服务
java -cp ".;lib/*" com.policy.jdbcservice.JDBCService

# 或不带类路径
java JDBCService
```

### 6.3 停止服务

按 `Ctrl+C` 停止服务，或在代码中调用 `JDBCService.shutdown()`

---

## 七、测试示例

### 7.1 使用 curl 测试

```bash
# 1. 健康检查
curl http://localhost:8080/api/health

# 2. 获取政策用途数据
curl "http://localhost:8080/api/baseDataDict?type=policy_usage&page=1&size=5"

# 3. 获取申报条件数据
curl "http://localhost:8080/api/applicationConditionDict?applyType=高新技术企业认定&page=1&size=5"

# 4. 创建基础数据
curl -X POST "http://localhost:8080/api/baseDataDict/create" \
  -H "Content-Type: application/json" \
  -d '{"type":"policy_usage","level1":"测试","level2":"测试","name":"测试数据","code":"TEST001","status":"启用"}'

# 5. 更新数据
curl -X POST "http://localhost:8080/api/baseDataDict/update" \
  -H "Content-Type: application/json" \
  -d '{"id":"test-id","name":"更新后的名称"}'

# 6. 删除数据
curl -X POST "http://localhost:8080/api/baseDataDict/delete" \
  -H "Content-Type: application/json" \
  -d '{"id":"test-id"}'
```

### 7.2 使用 Postman 测试

1. 新建 Collection：`基础数据维护`
2. 添加请求：
   - **GET List**：GET `http://localhost:8080/api/baseDataDict?type=policy_usage&page=1&size=10`
   - **POST Create**：POST `http://localhost:8080/api/baseDataDict/create`
   - **POST Update**：POST `http://localhost:8080/api/baseDataDict/update`
   - **POST Delete**：POST `http://localhost:8080/api/baseDataDict/delete`
3. Body 类型选择 `raw` → `JSON`
4. 发送请求查看响应

---

## 八、注意事项

1. **SQL 注入防护**：所有接口使用 `PreparedStatement`，参数绑定安全
2. **分页**：默认每页10条，最大建议不超过100条
3. **ID 生成**：新增时自动生成 UUID
4. **状态默认值**：新增时 `STATUS` 默认值为"启用"
5. **时间戳**：新增和更新时自动维护 `CREATE_TIME` 和 `UPDATE_TIME`
6. **字符编码**：统一使用 UTF-8
7. **CORS**：已配置允许所有来源访问

---

## 九、与前端对接

### 9.1 前端调用示例

```javascript
const API_BASE = 'http://localhost:3000'; // Node.js 服务地址

// 获取基础数据列表
async function fetchBaseDataDict(type, page = 1, size = 10) {
  const res = await fetch(
    `${API_BASE}/api/baseDataDict?type=${type}&page=${page}&size=${size}`
  );
  return res.json();
}

// 创建基础数据
async function createBaseDataDict(data) {
  const res = await fetch(`${API_BASE}/api/baseDataDict/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  return res.json();
}
```

### 9.2 server.js 转发配置

需要在 `server.js` 中添加转发到 JDBCService (8080)：

```javascript
// 转发到 JDBC 服务
const JDBC_SERVICE = 'http://localhost:8080';

server.createContext('/api/baseDataDict', (req, res) => {
  proxyRequest(req, res, JDBC_SERVICE + '/api/baseDataDict');
});

server.createContext('/api/baseDataDict/create', (req, res) => {
  proxyRequest(req, res, JDBC_SERVICE + '/api/baseDataDict/create');
});

// ... 其他接口类似
```

---

*文档生成时间：2026-07-01*