-- =============================================================
-- 申报条件字典表 - 初始化数据
-- 数据库: 达梦数据库 (DM8)
-- 主机: 127.0.0.1:5237
-- 用户: SYSDBA
-- 创建日期: 2026-07-01
-- =============================================================

-- =============================================================
-- 申报条件数据 - 15条（覆盖用户要求的8条+扩展）
-- =============================================================

-- 1. 工商基础信息 - 机构性质
INSERT INTO SYSDBA.APPLICATION_CONDITION_DICT (ID, CODE, APPLY_TYPE, LEVEL1, LEVEL2, LEVEL3, NAME, KEYWORDS, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('ac-001-001', 'AC001', '高新技术企业认定', '工商基础信息', '基础信息', '机构性质', '机构性质认定', '机构性质,企业类型,所有制形式', '企业应为在中国境内（不包括港、澳、台地区）注册的居民企业，且申请认定时须注册成立一年以上', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- 2. 核心团队 - 企业职工总数
INSERT INTO SYSDBA.APPLICATION_CONDITION_DICT (ID, CODE, APPLY_TYPE, LEVEL1, LEVEL2, LEVEL3, NAME, KEYWORDS, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('ac-001-002', 'AC002', '高新技术企业认定', '核心团队', '基础信息', '企业职工总数', '企业职工总数要求', '职工总数,从业人员,在职员工', '企业从事研发和相关技术创新活动的科技人员占企业当年职工总数的比例不低于10%', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- 3. 核心团队 - 科技人员占比
INSERT INTO SYSDBA.APPLICATION_CONDITION_DICT (ID, CODE, APPLY_TYPE, LEVEL1, LEVEL2, LEVEL3, NAME, KEYWORDS, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('ac-001-003', 'AC003', '高新技术企业认定', '核心团队', '基础信息', '科技人员占比', '科技人员占比要求', '科技人员,研发人员,占比要求', '企业从事研发和相关技术创新活动的科技人员占企业当年职工总数的比例不低于10%', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- 4. 创新投入 - 年研发投入
INSERT INTO SYSDBA.APPLICATION_CONDITION_DICT (ID, CODE, APPLY_TYPE, LEVEL1, LEVEL2, LEVEL3, NAME, KEYWORDS, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('ac-001-004', 'AC004', '研发费用补助', '创新投入', '研发费用', '年研发投入', '年研发投入金额', '研发投入,研发费用,年度投入', '企业近三个会计年度（实际经营期不满三年的按实际经营时间计算）的研究开发费用总额占同期销售收入总额的比例符合要求', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- 5. 创新发展 - 营收规模
INSERT INTO SYSDBA.APPLICATION_CONDITION_DICT (ID, CODE, APPLY_TYPE, LEVEL1, LEVEL2, LEVEL3, NAME, KEYWORDS, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('ac-001-005', 'AC005', '科技计划项目', '创新发展', '企业发展规模', '营收规模', '营业收入规模要求', '营业收入,销售额,营收规模', '企业上年度营业收入达到一定规模，其中：一般项目要求营收≥500万元，重点项目要求营收≥2000万元', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- 6. 创新发展 - 资产规模
INSERT INTO SYSDBA.APPLICATION_CONDITION_DICT (ID, CODE, APPLY_TYPE, LEVEL1, LEVEL2, LEVEL3, NAME, KEYWORDS, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('ac-001-006', 'AC006', '科技计划项目', '创新发展', '企业发展规模', '资产规模', '资产规模要求', '资产规模,总资产,净资产', '企业资产规模需满足相应要求，一般项目要求净资产≥200万元，重点项目要求净资产≥1000万元', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- 7. 创新产出 - 专利
INSERT INTO SYSDBA.APPLICATION_CONDITION_DICT (ID, CODE, APPLY_TYPE, LEVEL1, LEVEL2, LEVEL3, NAME, KEYWORDS, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('ac-001-007', 'AC007', '高新技术企业认定', '创新产出', '知识产权', '专利', '自主知识产权要求', '专利,知识产权,自主知识产权', '企业通过自主研发、受让、受赠、并购等方式，获得对其主要产品（服务）在技术上发挥核心支持作用的知识产权所有权', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- 8. 创新影响 - 重复申报限制
INSERT INTO SYSDBA.APPLICATION_CONDITION_DICT (ID, CODE, APPLY_TYPE, LEVEL1, LEVEL2, LEVEL3, NAME, KEYWORDS, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('ac-001-008', 'AC008', '科技型中小企业', '创新影响', '信用合规', '重复申报限制', '信用记录及重复申报限制', '信用记录,重复申报,违规记录', '企业入库前一个年度内未发生重大安全、重大质量事故和严重环境违法、科研严重失信行为', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- 9. 专精特新 - 企业职工总数
INSERT INTO SYSDBA.APPLICATION_CONDITION_DICT (ID, CODE, APPLY_TYPE, LEVEL1, LEVEL2, LEVEL3, NAME, KEYWORDS, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('ac-001-009', 'AC009', '专精特新企业', '核心团队', '基础信息', '企业职工总数', '专精特新企业职工规模', '职工总数,研发人员,专业人才', '企业从事特定细分市场时间达到2年以上，上年度营业收入总额不少于1000万元，其中主营业务收入占比≥70%', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- 10. 研发费用 - 归集要求
INSERT INTO SYSDBA.APPLICATION_CONDITION_DICT (ID, CODE, APPLY_TYPE, LEVEL1, LEVEL2, LEVEL3, NAME, KEYWORDS, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('ac-001-010', 'AC010', '研发费用补助', '创新投入', '研发费用', '年研发投入', '研发费用归集要求', '研发费用,归集,账务处理', '企业应按照国家财务会计制度要求，对研发费用进行单独列支、单独核算', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- 11. 科技计划项目 - 专利数量
INSERT INTO SYSDBA.APPLICATION_CONDITION_DICT (ID, CODE, APPLY_TYPE, LEVEL1, LEVEL2, LEVEL3, NAME, KEYWORDS, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('ac-001-011', 'AC011', '科技计划项目', '创新产出', '知识产权', '专利', '专利数量要求', '专利,发明专利,实用新型', '企业应拥有与项目相关的有效专利、软件著作权、集成电路布图设计专有权等知识产权', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- 12. 高新技术企业 - 高新收入占比
INSERT INTO SYSDBA.APPLICATION_CONDITION_DICT (ID, CODE, APPLY_TYPE, LEVEL1, LEVEL2, LEVEL3, NAME, KEYWORDS, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('ac-001-012', 'AC012', '高新技术企业认定', '创新发展', '企业发展规模', '营收规模', '高新技术产品收入占比', '高新收入,产品收入,技术服务', '近一年高新技术产品（服务）收入占企业同期总收入的比例不低于60%', '禁用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- 13. 科技型中小企业 - 科技人员认定
INSERT INTO SYSDBA.APPLICATION_CONDITION_DICT (ID, CODE, APPLY_TYPE, LEVEL1, LEVEL2, LEVEL3, NAME, KEYWORDS, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('ac-001-013', 'AC013', '科技型中小企业', '核心团队', '基础信息', '科技人员占比', '科技人员认定标准', '科技人员,认定标准,社保缴纳', '企业科技人员是指直接从事研发和相关技术创新活动，以及专门从事上述活动的管理和提供直接技术服务的', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- 14. 专精特新 - 专业化程度
INSERT INTO SYSDBA.APPLICATION_CONDITION_DICT (ID, CODE, APPLY_TYPE, LEVEL1, LEVEL2, LEVEL3, NAME, KEYWORDS, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('ac-001-014', 'AC014', '专精特新企业', '创新发展', '企业发展规模', '营收规模', '专业化程度指标', '专业化,主营业务,市场专注', '企业应主营业务收入占营业收入比重≥70%，在细分市场区内排名在全国前列', '禁用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- 15. 研发费用 - 专利转化
INSERT INTO SYSDBA.APPLICATION_CONDITION_DICT (ID, CODE, APPLY_TYPE, LEVEL1, LEVEL2, LEVEL3, NAME, KEYWORDS, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('ac-001-015', 'AC015', '研发费用补助', '创新产出', '知识产权', '专利', '专利转化要求', '专利转化,成果转化,技术应用', '企业研发项目须有相关知识产权支撑，且知识产权需与企业主要产品（服务）有核心关联', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- =============================================================
-- 数据插入完成
-- =============================================================