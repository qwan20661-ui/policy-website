-- =============================================
-- 政策匹配结果数据库表
-- 数据库: policy_metadata_v2
-- uid 采用 UUID v4 格式 (VARCHAR(36))
-- =============================================

-- 1. 匹配结果主表（企业×政策）
CREATE TABLE IF NOT EXISTS `match_result` (
  `uid` VARCHAR(36) NOT NULL PRIMARY KEY COMMENT 'UUID主键',
  `enterprise_uid` VARCHAR(36) NOT NULL COMMENT '企业uid',
  `企业名称` VARCHAR(200) DEFAULT NULL COMMENT '企业名称',
  `政策标题` VARCHAR(300) NOT NULL COMMENT '政策名称',
  `匹配率` DECIMAL(5,4) DEFAULT NULL COMMENT '完全匹配条件占比',
  `是否推荐` TINYINT(1) DEFAULT NULL COMMENT '是否推荐(0=推荐,1=不推荐; 匹配率>=90%为0)',
  `是否匹配` TINYINT(1) DEFAULT NULL COMMENT '是否匹配(0否/1是)',
  `总体建议` TEXT DEFAULT NULL COMMENT '综合建议',
  `创建时间` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `更新时间` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY `idx_enterprise` (`enterprise_uid`),
  KEY `idx_policy` (`政策标题`(100)),
  CONSTRAINT `fk_result_enterprise` FOREIGN KEY (`enterprise_uid`) REFERENCES `enterprise`(`uid`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='企业-政策匹配结果';


-- 2. 条件匹配详情表
CREATE TABLE IF NOT EXISTS `match_condition` (
  `uid` VARCHAR(36) NOT NULL PRIMARY KEY COMMENT 'UUID主键',
  `result_uid` VARCHAR(36) NOT NULL COMMENT '匹配结果uid（关联match_result）',
  `企业名称` VARCHAR(200) DEFAULT NULL COMMENT '企业名称',
  `政策标题` VARCHAR(300) DEFAULT NULL COMMENT '政策标题',
  `条件名称` VARCHAR(200) DEFAULT NULL COMMENT '条件简称',
  `政策原文` TEXT DEFAULT NULL COMMENT '政策文档原文摘录',
  `政策要求` TEXT DEFAULT NULL COMMENT '政策要求（精炼概括）',
  `企业现状` TEXT DEFAULT NULL COMMENT '企业当前情况',
  `企业数值` DECIMAL(18,4) DEFAULT NULL COMMENT '企业实际数值（可量化时）',
  `匹配度` VARCHAR(20) DEFAULT NULL COMMENT '完全匹配/部分匹配/不匹配/待确认',
  `差距分析` TEXT DEFAULT NULL COMMENT '与要求的差距分析',
  `风险等级` VARCHAR(10) DEFAULT NULL COMMENT '高/中/低',
  `创建时间` DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY `idx_result` (`result_uid`),
  KEY `idx_match` (`匹配度`),
  CONSTRAINT `fk_condition_result` FOREIGN KEY (`result_uid`) REFERENCES `match_result`(`uid`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='条件匹配详情';


-- 3. 优势/劣势分析表
CREATE TABLE IF NOT EXISTS `match_analysis` (
  `uid` VARCHAR(36) NOT NULL PRIMARY KEY COMMENT 'UUID主键',
  `result_uid` VARCHAR(36) NOT NULL COMMENT '匹配结果uid（关联match_result）',
  `企业名称` VARCHAR(200) DEFAULT NULL COMMENT '企业名称',
  `政策标题` VARCHAR(300) DEFAULT NULL COMMENT '政策标题',
  `类型` VARCHAR(10) NOT NULL COMMENT '优势/劣势',
  `内容` TEXT NOT NULL COMMENT '分析内容',
  `排序` INT DEFAULT 0 COMMENT '显示顺序',
  `创建时间` DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY `idx_result` (`result_uid`),
  KEY `idx_type` (`类型`),
  CONSTRAINT `fk_analysis_result` FOREIGN KEY (`result_uid`) REFERENCES `match_result`(`uid`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='优势劣势分析';


-- 4. 风险提示表
CREATE TABLE IF NOT EXISTS `match_risk` (
  `uid` VARCHAR(36) NOT NULL PRIMARY KEY COMMENT 'UUID主键',
  `result_uid` VARCHAR(36) NOT NULL COMMENT '匹配结果uid（关联match_result）',
  `企业名称` VARCHAR(200) DEFAULT NULL COMMENT '企业名称',
  `政策标题` VARCHAR(300) DEFAULT NULL COMMENT '政策标题',
  `内容` TEXT NOT NULL COMMENT '风险提示内容',
  `排序` INT DEFAULT 0 COMMENT '显示顺序',
  `创建时间` DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY `idx_result` (`result_uid`),
  CONSTRAINT `fk_risk_result` FOREIGN KEY (`result_uid`) REFERENCES `match_result`(`uid`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='风险提示';
