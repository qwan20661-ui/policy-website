# 修改 `structure.html` 页面 — 政策融合列表改版

## 背景

当前 `structure.html` 页面（"政策融合标注"）展示的是扁平化的关联文档行记录，每行是一个融合政策下的子文档。
用户要求改为以 **融合政策（X1_FUSION_POLICY）为单位** 的列表，每行展示一条融合政策，包含：
**融合后标题、摘要、发布部门、申报最高金额、标注状态**。

## 数据库关系链

```
X1_FUSION_POLICY ──SOURCE_POLICY_ID──→ POLICY (ABSTRACT)
                                      └─→ POLICY_ORIGINAL_V2 (ISSUE_DEPARTMENT)
                                      └─→ SUPPORT_METHOD (MAX_SUPPORT_AMOUNT)
                      ──→ X1_POLICY_DOCUMENT (子文档，详情展示)
                            └── X1_POLICY_BASE_INFO (ISSUING_DEPARTMENT)
```

## 修改方案

### 1. 后端 — JDBCService.java

**文件**: `e:\workspace\kejiju-frontend\JDBCService.java`

在 `FusionClassificationHandler` 的主查询 `listSql` 中，增加三个子查询字段：

```sql
(SELECT p.ABSTRACT FROM SYSDBA.POLICY p WHERE p.ID = f.SOURCE_POLICY_ID AND ROWNUM=1) AS ABSTRACT,
(SELECT o.ISSUE_DEPARTMENT FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID = CAST(f.SOURCE_POLICY_ID AS VARCHAR(50)) AND ROWNUM=1) AS ISSUING_DEPARTMENT,
(SELECT MAX(sm.MAX_SUPPORT_AMOUNT) FROM SYSDBA.SUPPORT_METHOD sm WHERE sm.POLICY_ID = f.SOURCE_POLICY_ID) AS MAX_SUPPORT_AMOUNT
```

同时更新 row 的 put 映射，添加：
```java
row.put("abstract", nullToEmpty(rs.getString("ABSTRACT")));
row.put("issuingDepartment", nullToEmpty(rs.getString("ISSUING_DEPARTMENT")));
row.put("maxSupportAmount", nullToEmpty(rs.getString("MAX_SUPPORT_AMOUNT")));
```

### 2. 前端 — structure.html

**文件**: `e:\workspace\kejiju-frontend\pages\structure.html`

#### 2.1 表格列重改

| 当前 | 改为 |
|------|------|
| 序号 | 序号 |
| 主政策原标题 | **融合后标题**（点击进入详情） |
| 关联政策文件名称 | **摘要**（截断显示，鼠标悬浮展示全文） |
| 政策附件名称 | **发布部门** |
| 状态 | **申报最高金额**（带单位） |
| 操作（编辑名称/详情） | **标注状态**（可点击切换） |
| | **操作**（仅保留"详情"按钮） |

- 数据源从 `flatRows`（每条文档一行）改为 `policies`（每条融合政策一行）
- 不再需要展平逻辑，移除 `flatRows` computed
- 表绑定 `:data="policies"` 而非 `:data="flatRows"`
- 统计数据中的「共 X 条关联记录」改为「共 X 条融合政策」
- 搜索从搜"主政策原标题或关联文件名称"改为搜"融合政策标题"

#### 2.2 详情抽屉修改

当前详情展示：主政策原标题、目录名称、关联文件数、标注状态 + 关联政策文件列表

改为：
- 头部：融合后标题 + 描述
- 概览区（4格）：
  - 融合后标题
  - 摘要（完整展示）
  - 发布部门
  - 申报最高金额
  - 标注状态
  - 更新日期
- 下方：关联政策文件列表（保持现有样式）

#### 2.3 移除不需要的功能

- 移除「编辑名称」按钮和相关逻辑（`openEditDoc`, `saveDocEdit`, edit dialog）
- 移除「查看原文」对话框（按用户需求，标注状态手动点击切换即可）
- 保留「状态点击切换」功能，但只作用于融合政策级别

#### 2.4 搜索框调整

搜索提示改为"搜索融合政策标题或关联文件名称"

## 无需修改的部分

- 后端 API 路由（保持不变，只是增加 3 个返回字段）
- 统计数据（融合政策总数、关联文件总数、已标注数量）
- 分页逻辑
- CSS 样式（基本沿用现有）
- 菜单和布局组件

## 步骤

1. 修改 JDBCService.java → 添加 3 个子查询字段 + row.put 映射
2. 修改 structure.html → 替换表格列定义和绑定数据
3. 修改 structure.html → 更新详情抽屉内容
4. 修改 structure.html → 移除不需要的功能和变量
