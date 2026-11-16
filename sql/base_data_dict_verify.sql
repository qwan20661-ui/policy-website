-- =============================================================
-- 基础数据字典表 - 验证脚本
-- 数据库: 达梦数据库 (DM8)
-- 主机: 127.0.0.1:5237
-- 用户: SYSDBA
-- 创建日期: 2026-07-01
-- =============================================================

-- =============================================================
-- 1. 验证表是否创建成功
-- =============================================================

-- 查看所有表，确认新建的表存在
SELECT 'BASE_DATA_DICT' AS TABLE_NAME, COUNT(*) AS RECORD_COUNT 
FROM SYSDBA.BASE_DATA_DICT
UNION ALL
SELECT 'APPLICATION_CONDITION_DICT', COUNT(*) 
FROM SYSDBA.APPLICATION_CONDITION_DICT;

-- =============================================================
-- 2. BASE_DATA_DICT 表统计
-- =============================================================

-- 按 TYPE 统计数量
SELECT 
    TYPE AS 数据类型,
    COUNT(*) AS 记录数,
    SUM(CASE WHEN STATUS = '启用' THEN 1 ELSE 0 END) AS 启用数量,
    SUM(CASE WHEN STATUS = '禁用' THEN 1 ELSE 0 END) AS 禁用数量
FROM SYSDBA.BASE_DATA_DICT
GROUP BY TYPE
ORDER BY TYPE;

-- =============================================================
-- 3. APPLICATION_CONDITION_DICT 表统计
-- =============================================================

-- 按申报类型统计
SELECT 
    APPLY_TYPE AS 申报类型,
    COUNT(*) AS 记录数,
    SUM(CASE WHEN STATUS = '启用' THEN 1 ELSE 0 END) AS 启用数量,
    SUM(CASE WHEN STATUS = '禁用' THEN 1 ELSE 0 END) AS 禁用数量
FROM SYSDBA.APPLICATION_CONDITION_DICT
GROUP BY APPLY_TYPE
ORDER BY APPLY_TYPE;

-- =============================================================
-- 4. BASE_DATA_DICT 各类型前10条数据
-- =============================================================

-- 政策用途 (policy_usage)
SELECT ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, STATUS
FROM SYSDBA.BASE_DATA_DICT
WHERE TYPE = 'policy_usage'
ORDER BY CODE;

-- 政策关键词 (policy_keyword)
SELECT ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, STATUS
FROM SYSDBA.BASE_DATA_DICT
WHERE TYPE = 'policy_keyword'
ORDER BY CODE;

-- 适用对象 (applicable_object)
SELECT ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, STATUS
FROM SYSDBA.BASE_DATA_DICT
WHERE TYPE = 'applicable_object'
ORDER BY CODE;

-- 扶持类型 (support_type)
SELECT ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, STATUS
FROM SYSDBA.BASE_DATA_DICT
WHERE TYPE = 'support_type'
ORDER BY CODE;

-- 适用范围 (applicable_scope)
SELECT ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, STATUS
FROM SYSDBA.BASE_DATA_DICT
WHERE TYPE = 'applicable_scope'
ORDER BY CODE;

-- =============================================================
-- 5. APPLICATION_CONDITION_DICT 前15条数据
-- =============================================================

SELECT 
    ID, 
    CODE, 
    APPLY_TYPE, 
    LEVEL1, 
    LEVEL2, 
    LEVEL3, 
    NAME, 
    STATUS,
    CREATE_TIME,
    UPDATE_TIME
FROM SYSDBA.APPLICATION_CONDITION_DICT
ORDER BY CODE;

-- =============================================================
-- 6. 验证数据完整性
-- =============================================================

-- 检查是否有重复的 CODE
SELECT CODE, COUNT(*) AS CNT
FROM SYSDBA.BASE_DATA_DICT
GROUP BY CODE
HAVING COUNT(*) > 1;

SELECT CODE, COUNT(*) AS CNT
FROM SYSDBA.APPLICATION_CONDITION_DICT
GROUP BY CODE
HAVING COUNT(*) > 1;

-- 检查必填字段是否为空
SELECT 'BASE_DATA_DICT - NAME为空' AS CHECK_ITEM, COUNT(*) AS EMPTY_COUNT
FROM SYSDBA.BASE_DATA_DICT
WHERE NAME IS NULL OR NAME = '';

SELECT 'APPLICATION_CONDITION_DICT - NAME为空' AS CHECK_ITEM, COUNT(*) AS EMPTY_COUNT
FROM SYSDBA.APPLICATION_CONDITION_DICT
WHERE NAME IS NULL OR NAME = '';

SELECT 'APPLICATION_CONDITION_DICT - APPLY_TYPE为空' AS CHECK_ITEM, COUNT(*) AS EMPTY_COUNT
FROM SYSDBA.APPLICATION_CONDITION_DICT
WHERE APPLY_TYPE IS NULL OR APPLY_TYPE = '';

-- =============================================================
-- 验证完成
-- =============================================================