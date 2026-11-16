# 政策解读系统 - JDBC 中间层配置说明

## 系统架构

```
┌─────────────────┐        ┌─────────────────┐        ┌─────────────────┐
│   前端页面       │  HTTP  │   Node.js API   │  HTTP  │  Java JDBC      │
│   merge.html    │ ──────►│   服务 (3000)   │ ──────►│  中间层 (8080)  │
│                 │        │                 │        │                 │
└─────────────────┘        └─────────────────┘        └────────┬────────┘
                                                                │
                                                                │ JDBC
                                                                ▼
                                                       ┌─────────────────┐
                                                       │   达梦数据库     │
                                                       │ 127.0.0.1  │
                                                       │    :5237/MAIN   │
                                                       └─────────────────┘
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `JDBCService.java` | Java JDBC 中间层服务 |
| `server.js` | Node.js API 网关 |
| `pom.xml` | Maven 构建配置 |
| `compile.bat` | 编译脚本 |
| `start.bat` | 启动脚本 |

## 达梦数据库配置

```
主机:   127.0.0.1
端口:   5237
用户:   SYSDBA
密码：运行时通过 KEJIJU_DB_PASSWORD 提供。
表空间: MAIN
模式:   SYSDBA (用户即模式，注意表名需加 SYSDBA. 前缀)
```

## 编译步骤

### 1. 安装达梦 JDBC 驱动

1. 访问达梦官网下载: https://www.dameng.com/download.html
2. 下载 `DmJdbcDriver18.jar`
3. 放入项目 `lib/` 目录

### 2. 编译 Java 服务

```bash
# 方式一: 使用编译脚本 (推荐)
compile.bat

# 方式二: 手动编译
mkdir target
javac -encoding UTF-8 -d target JDBCService.java
```

### 3. 启动服务

```bash
# 方式一: 使用启动脚本 (推荐)
start.bat

# 方式二: 手动启动
# 终端1: 启动 JDBC 服务
java -cp "target;lib/*" com.policy.jdbcservice.JDBCService

# 终端2: 启动 Node.js API
node server.js
```

### 4. 访问页面

```
前端页面: http://localhost:3000/pages/merge.html
API接口:  http://localhost:3000/api/policies
JDBC接口: http://localhost:8080/api/policies
```

## 表结构参考

### POLICY_ORIGINAL 表

| 字段 | 类型 | 说明 |
|------|------|------|
| ID | VARCHAR(36) | 主键 UUID |
| UUID | VARCHAR(36) | 唯一标识 |
| TITLE | VARCHAR(500) | 政策标题 |
| DOCUMENT_NO | VARCHAR(100) | 文号 |
| PUBLISH_DATE | VARCHAR(20) | 发布日期 |
| DEPARTMENT | VARCHAR(200) | 发布部门 |
| FILE_TYPE | VARCHAR(50) | 文件类型 |
| TYPE | VARCHAR(50) | 政策类型 |
| ATTACHMENT_TYPE | VARCHAR(50) | 附件类型 |
| STATUS | VARCHAR(20) | 标注状态 |
| CREATED_AT | VARCHAR(30) | 创建时间 |

## 常见问题

### Q: 编译失败 "找不到 dm.jdbc.driver.DmDriver"

**解决**: 确保 `lib/DmJdbcDriver18.jar` 存在

### Q: 连接数据库失败

**解决**:
1. 检查网络连通性: `ping 127.0.0.1`
2. 检查端口: `telnet 127.0.0.1 5237`
3. 确认数据库服务正在运行

### Q: Node.js 服务启动失败

**解决**:
1. 确保端口 3000 未被占用
2. 确保 JDBC 服务已启动 (端口 8080)

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/policies` | 获取政策列表 |
| GET | `/api/statistics` | 获取统计数据 |
| GET | `/api/filterStats` | 获取筛选统计 |
| POST | `/api/policies/update` | 更新政策分类 |
| GET | `/api/health` | 健康检查 |

## 注意事项

1. **Java 版本**: 需要 JDK 8 或更高版本
2. **达梦驱动**: 不同版本的达梦数据库可能需要对应版本的 JDBC 驱动
3. **防火墙**: 确保 3000 和 8080 端口已开放
