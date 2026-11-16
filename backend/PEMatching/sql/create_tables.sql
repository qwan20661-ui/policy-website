-- =============================================
-- 科小企业数据库建表脚本
-- 数据库: policy_metadata_v2
-- uid 采用 UUID v4 格式 (VARCHAR(36))
-- =============================================

-- 1. 企业主表（跨年不变的基础信息）
CREATE TABLE IF NOT EXISTS `enterprise` (
  `uid` VARCHAR(36) NOT NULL PRIMARY KEY COMMENT 'UUID主键',
  `企业名称` VARCHAR(200) NOT NULL COMMENT '企业名称',
  `统一社会信用代码` VARCHAR(50) NOT NULL COMMENT '统一社会信用代码',
  `注册日期` DATETIME DEFAULT NULL COMMENT '注册日期',
  `注册地` VARCHAR(100) DEFAULT NULL COMMENT '注册地（市/区）',
  `地址` VARCHAR(300) DEFAULT NULL COMMENT '详细地址',
  `一级行业` VARCHAR(100) DEFAULT NULL COMMENT '一级行业',
  `二级行业` VARCHAR(100) DEFAULT NULL COMMENT '二级行业',
  `创建时间` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `更新时间` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY `uk_credit_code` (`统一社会信用代码`),
  KEY `idx_name` (`企业名称`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='企业主表（基础信息）';


-- 2. 企业年度数据表（每年一条记录）
CREATE TABLE IF NOT EXISTS `enterprise_yearly` (
  `uid` VARCHAR(36) NOT NULL PRIMARY KEY COMMENT 'UUID主键',
  `enterprise_uid` VARCHAR(36) NOT NULL COMMENT '企业uid（关联enterprise表）',
  `年份` YEAR NOT NULL COMMENT '填报年份',

  -- === 评价指标得分（8项） ===
  `指标1_注册地` VARCHAR(20) DEFAULT NULL COMMENT '指标1: 注册地是否符合',
  `指标2_企业规模` VARCHAR(20) DEFAULT NULL COMMENT '指标2: 企业规模是否符合',
  `指标3_产品及服务范围` VARCHAR(20) DEFAULT NULL COMMENT '指标3: 产品及服务范围是否符合',
  `指标4_企业信用` VARCHAR(20) DEFAULT NULL COMMENT '指标4: 企业信用是否符合',
  `指标5_高新技术企业` VARCHAR(20) DEFAULT NULL COMMENT '指标5: 是否高新技术企业',
  `指标6_研发机构` VARCHAR(20) DEFAULT NULL COMMENT '指标6: 是否有研发机构',
  `指标7_科技奖励` VARCHAR(20) DEFAULT NULL COMMENT '指标7: 是否获得科技奖励',
  `指标8_制定标准` VARCHAR(20) DEFAULT NULL COMMENT '指标8: 是否参与制定标准',

  -- === 加计扣除与总得分 ===
  `是否享受加计扣除` VARCHAR(10) DEFAULT NULL COMMENT '是否享受研发费用加计扣除',
  `享受加计扣除金额_元` DECIMAL(18,2) DEFAULT NULL COMMENT '加计扣除金额（元）',
  `总得分` INT DEFAULT NULL COMMENT '科小评价总得分',

  -- === 比例指标 ===
  `科技人员占比` DECIMAL(8,4) DEFAULT NULL COMMENT '科技人员占职工总数比例',
  `研发费用占销售收入比例` DECIMAL(8,4) DEFAULT NULL COMMENT '研发费用占销售收入总额比例',
  `研发费用占成本费用比例` DECIMAL(8,4) DEFAULT NULL COMMENT '研发费用占成本费用支出比例',

  -- === 知识产权 ===
  `I类知识产权数量` INT DEFAULT 0 COMMENT 'I类知识产权数量',
  `II类知识产权数量` INT DEFAULT 0 COMMENT 'II类知识产权数量',

  -- === 财务数据（单位：万元） ===
  `资产总额` DECIMAL(18,2) DEFAULT NULL COMMENT '资产总额（万元）',
  `净资产` DECIMAL(18,2) DEFAULT NULL COMMENT '净资产（万元）',
  `成本费用总额` DECIMAL(18,2) DEFAULT NULL COMMENT '成本费用总额（万元）',
  `销售收入` DECIMAL(18,2) DEFAULT NULL COMMENT '销售收入（万元）',
  `主营业务收入` DECIMAL(18,2) DEFAULT NULL COMMENT '主营业务收入（万元）',
  `研发费用总额` DECIMAL(18,2) DEFAULT NULL COMMENT '研发费用总额（万元）',
  `利润总额` DECIMAL(18,2) DEFAULT NULL COMMENT '利润总额（万元，可为负）',
  `纳税总额` DECIMAL(18,2) DEFAULT NULL COMMENT '纳税总额（万元）',

  -- === 人员结构 ===
  `职工总数` INT DEFAULT 0 COMMENT '职工总数',
  `在职职工人数` INT DEFAULT 0 COMMENT '在职职工人数',
  `兼职职工人数` INT DEFAULT 0 COMMENT '兼职职工人数',
  `临时职工人数` INT DEFAULT 0 COMMENT '临时职工人数',
  `科技人员总数` INT DEFAULT 0 COMMENT '科技人员总数',
  `科技人员在职人数` INT DEFAULT 0 COMMENT '科技人员在职人数',
  `科技人员兼职人数` INT DEFAULT 0 COMMENT '科技人员兼职人数',
  `科技人员临时人数` INT DEFAULT 0 COMMENT '科技人员临时人数',
  `本年新增人数` INT DEFAULT 0 COMMENT '本年新增人数',
  `吸纳高校毕业生人数` INT DEFAULT 0 COMMENT '吸纳高校毕业生人数',

  -- === 学历结构 ===
  `博士人数` INT DEFAULT 0 COMMENT '博士人数',
  `硕士人数` INT DEFAULT 0 COMMENT '硕士人数',
  `本科人数` INT DEFAULT 0 COMMENT '本科人数',
  `大专及以下人数` INT DEFAULT 0 COMMENT '大专及以下人数',

  -- === 职称结构 ===
  `高级职称人数` INT DEFAULT 0 COMMENT '高级职称人数',
  `中级职称人数` INT DEFAULT 0 COMMENT '中级职称人数',
  `初级职称人数` INT DEFAULT 0 COMMENT '初级职称人数',
  `高级技工人数` INT DEFAULT 0 COMMENT '高级技工人数',

  `创建时间` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `更新时间` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  UNIQUE KEY `uk_ent_year` (`enterprise_uid`, `年份`),
  KEY `idx_year` (`年份`),
  CONSTRAINT `fk_yearly_enterprise` FOREIGN KEY (`enterprise_uid`) REFERENCES `enterprise`(`uid`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='企业年度数据（科小评价指标）';
