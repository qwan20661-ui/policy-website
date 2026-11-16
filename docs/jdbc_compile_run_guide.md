# JDBCService.java 编译运行说明

> 更新日期：2026-07-01

---

## 一、文件位置

```
D:\app\demo\demo\
├── JDBCService.java          # Java 源文件（已更新）
├── docs/
│   └── jdbc_base_data_dict_api.md    # API 接口文档
└── sql/
    ├── base_data_dict_schema.sql     # 建表脚本
    ├── base_data_dict_init.sql       # 初始化数据
    └── base_data_dict_verify.sql     # 验证脚本
```

---

## 二、编译

### 2.1 检查环境

```bash
# 检查 Java 版本（需要 JDK 8 或更高）
java -version

# 检查 javac 是否可用
javac -version
```

### 2.2 编译命令

```bash
cd D:\app\demo\demo

# 编译（当前目录包含达梦 JDBC 驱动）
javac JDBCService.java

# 或指定类路径（如果达梦驱动在其他位置）
javac -cp ".;lib/dm-jdbc.jar" JDBCService.java
```

### 2.3 编译成功标志

```
# 执行完无错误输出，生成了 class 文件
D:\app\demo\demo\
├── JDBCService.class
├── JDBCService$1.class
├── JDBCService$BaseDataDictHandler.class
├── JDBCService$BaseDataDictCreateHandler.class
├── JDBCService$BaseDataDictUpdateHandler.class
├── JDBCService$BaseDataDictDeleteHandler.class
├── JDBCService$ApplicationConditionDictHandler.class
├── JDBCService$ApplicationConditionDictCreateHandler.class
├── JDBCService$ApplicationConditionDictUpdateHandler.class
├── JDBCService$ApplicationConditionDictDeleteHandler.class
└── ... (其他 class 文件)
```

---

## 三、运行

### 3.1 启动服务

```bash
# 启动（需要包含达梦驱动）
java JDBCService

# 或指定类路径
java -cp ".;lib/dm-jdbc.jar" JDBCService
```

### 3.2 启动成功输出

```
------------------------------------------------------------
🚀 JDBC 中间层服务已启动
------------------------------------------------------------
📡 服务地址: http://localhost:8080
📊 数据库: 127.0.0.1:5237 模式: SYSDBA
------------------------------------------------------------
📋 可用接口:
   [政策归类标注]
   GET  /api/policies        - 获取政策列表
   GET  /api/statistics      - 获取统计数据
   GET  /api/filterStats     - 获取筛选统计
   POST /api/policies/update - 更新政策分类
   GET  /api/health          - 健康检查
------------------------------------------------------------
   [基础数据字典 - BASE_DATA_DICT]
   GET  /api/baseDataDict            - 获取基础数据列表
   POST /api/baseDataDict/create     - 创建基础数据
   POST /api/baseDataDict/update     - 更新基础数据
   POST /api/baseDataDict/delete     - 删除基础数据
------------------------------------------------------------
   [申报条件字典 - APPLICATION_CONDITION_DICT]
   GET  /api/applicationConditionDict            - 获取申报条件列表
   POST /api/applicationConditionDict/create     - 创建申报条件
   POST /api/applicationConditionDict/update     - 更新申报条件
   POST /api/applicationConditionDict/delete     - 删除申报条件
------------------------------------------------------------
```

### 3.3 停止服务

```
按 Ctrl+C 停止服务
```

---

## 四、测试

### 4.1 健康检查

```bash
curl http://localhost:8080/api/health
```

响应：
```json
{
  "status": "ok",
  "service": "JDBC Middleware",
  "database": "DMDB",
  "schema": "SYSDBA",
  "timestamp": 1751376000000
}
```

### 4.2 获取基础数据列表

```bash
# 获取所有类型的政策用途数据
curl "http://localhost:8080/api/baseDataDict?type=policy_usage&page=1&size=10"

# 获取所有申报条件数据
curl "http://localhost:8080/api/applicationConditionDict?page=1&size=10"
```

### 4.3 创建数据

```bash
curl -X POST "http://localhost:8080/api/baseDataDict/create" \
  -H "Content-Type: application/json" \
  -d '{"type":"policy_usage","level1":"项目支持","level2":"科技项目","name":"测试数据","code":"TEST001","status":"启用"}'
```

### 4.4 更新数据

```bash
curl -X POST "http://localhost:8080/api/baseDataDict/update" \
  -H "Content-Type: application/json" \
  -d '{"id":"test-id","name":"更新后的名称"}'
```

### 4.5 删除数据

```bash
curl -X POST "http://localhost:8080/api/baseDataDict/delete" \
  -H "Content-Type: application/json" \
  -d '{"id":"test-id"}'
```

---

## 五、常见问题

### 5.1 找不到达梦 JDBC 驱动

```
Error: Could not find or load main class DmDriver
```

**解决**：确保达梦 JDBC 驱动 (dm-jdbc.jar) 在 classpath 中

```bash
# 将驱动放到 lib 目录
mkdir lib
# 复制 dm-jdbc.jar 到 lib 目录

# 然后重新编译和运行
javac -cp ".;lib/*" JDBCService.java
java -cp ".;lib/*" JDBCService
```

### 5.2 数据库连接失败

```
[SQL Error] Communication link failure
```

**解决**：
1. 检查数据库服务器是否运行：`ping 127.0.0.1`
2. 检查端口是否可访问：`telnet 127.0.0.1 5237`
3. 检查用户名密码是否正确

### 5.3 端口被占用

```
java.net.BindException: Address already in use: JVM_Bind
```

**解决**：
1. 查找占用端口的进程：`netstat -ano | findstr :8080`
2. 关闭占用进程或修改 SERVICE_PORT

---

## 六、完整执行流程

```bash
# 1. 进入项目目录
cd D:\app\demo\demo

# 2. 编译 Java 文件
javac JDBCService.java

# 3. 运行服务
java JDBCService

# 4. 新开终端测试接口
# 健康检查
curl http://localhost:8080/api/health

# 获取基础数据
curl "http://localhost:8080/api/baseDataDict?type=policy_usage&page=1&size=5"

# 获取申报条件
curl "http://localhost:8080/api/applicationConditionDict?page=1&size=5"

# 5. 按 Ctrl+C 停止服务
```

---

## 七、注意事项

1. **启动顺序**：先启动 JDBCService (8080)，再启动 Node.js server (3000)
2. **数据库连接**：确保达梦数据库可访问
3. **驱动依赖**：需要达梦 JDBC 驱动 (dm-jdbc.jar)
4. **防火墙**：确保 8080 端口未被防火墙阻止
5. **并发限制**：默认 10 线程池

---

*文档生成时间：2026-07-01*