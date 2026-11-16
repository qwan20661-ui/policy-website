-- =============================================================
-- 基础数据字典表 - 初始化数据
-- 数据库: 达梦数据库 (DM8)
-- 主机: 127.0.0.1:5237
-- 用户: SYSDBA
-- 创建日期: 2026-07-01
-- =============================================================

-- =============================================================
-- 1. 政策用途数据 (policy_usage) - 8条
-- =============================================================
INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-001-001', 'policy_usage', '项目支持', '科技项目', '高新技术企业认定', 'PU001', '针对高新技术企业的认定管理工作', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-001-002', 'policy_usage', '项目支持', '科技项目', '科技型中小企业评价', 'PU002', '科技型中小企业入库评价工作', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-001-003', 'policy_usage', '项目支持', '工业项目', '智能制造示范项目', 'PU003', '智能制造试点示范项目申报', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-001-004', 'policy_usage', '资金扶持', '无偿资助', '研发费用补贴', 'PU004', '企业研发费用后补助资金申请', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-001-005', 'policy_usage', '资金扶持', '贷款贴息', '中小企业贷款贴息', 'PU005', '中小企业银行贷款贴息政策', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-001-006', 'policy_usage', '资金扶持', '奖励补助', '上规升级奖励', 'PU006', '鼓励企业上规升级的奖励政策', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-001-007', 'policy_usage', '人才支持', '人才引进', '高层次人才引进', 'PU007', '引进高层次创新创业人才计划', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-001-008', 'policy_usage', '平台支持', '孵化平台', '科技企业孵化器', 'PU008', '科技企业孵化器认定与管理', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- =============================================================
-- 2. 政策关键词数据 (policy_keyword) - 8条
-- =============================================================
INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-002-001', 'policy_keyword', '科技类', '技术创新', '技术创新', 'PK001', '涉及技术创新的政策关键词', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-002-002', 'policy_keyword', '科技类', '技术创新', '研发投入', 'PK002', '研发费用相关政策关键词', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-002-003', 'policy_keyword', '科技类', '知识产权', '知识产权', 'PK003', '专利、商标、著作权等知识产权相关', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-002-004', 'policy_keyword', '工业类', '智能制造', '智能制造', 'PK004', '智能制造相关政策关键词', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-002-005', 'policy_keyword', '工业类', '数字化转型', '数字化转型', 'PK005', '企业数字化转型相关政策', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-002-006', 'policy_keyword', '农业类', '乡村振兴', '乡村振兴', 'PK006', '乡村振兴战略相关政策', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-002-007', 'policy_keyword', '服务业类', '现代服务业', '现代服务业', 'PK007', '现代服务业发展相关政策', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-002-008', 'policy_keyword', '人才类', '高层次人才', '高层次人才', 'PK008', '高层次人才引进培养相关', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- =============================================================
-- 3. 适用对象数据 (applicable_object) - 8条
-- =============================================================
INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-003-001', 'applicable_object', '企业类型', '民营企业', '民营企业', 'AO001', '所有民营企业，包括有限责任公司、股份有限公司等', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-003-002', 'applicable_object', '企业类型', '国有企业', '国有企业', 'AO002', '国有及国有控股企业', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-003-003', 'applicable_object', '企业类型', '小微企业', '小微企业', 'AO003', '符合工信部小型微型企业标准的企业', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-003-004', 'applicable_object', '企业类型', '中型企业', '中型企业', 'AO004', '符合工信部中型企业标准的企业', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-003-005', 'applicable_object', '主体性质', '个体工商户', '个体工商户', 'AO005', '在市场监管局登记的个体工商户', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-003-006', 'applicable_object', '主体性质', '社会组织', '社会组织', 'AO006', '包括社会团体、民办非企业单位、基金会', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-003-007', 'applicable_object', '规模大小', '大型企业', '大型企业', 'AO007', '营业收入4亿元以上或从业人员1000人以上的企业', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-003-008', 'applicable_object', '规模大小', '微型企业', '微型企业', 'AO008', '符合微型企业划型标准的企业', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- =============================================================
-- 4. 扶持类型数据 (support_type) - 8条
-- =============================================================
INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-004-001', 'support_type', '资金支持', '直接资助', '项目资助', 'ST001', '政府直接给予资金资助的项目', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-004-002', 'support_type', '资金支持', '奖励补贴', '事后奖补', 'ST002', '根据企业实际投入或产出给予的奖励补贴', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-004-003', 'support_type', '资金支持', '贷款贴息', '贷款贴息', 'ST003', '对企业银行贷款利息给予补贴', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-004-004', 'support_type', '税收优惠', '减免税', '税收减免', 'ST004', '各类税收减免优惠政策', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-004-005', 'support_type', '税收优惠', '抵免税', '加计抵减', 'ST005', '研发费用加计扣除等抵税政策', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-004-006', 'support_type', '融资支持', '贷款担保', '融资担保', 'ST006', '政府性融资担保机构提供担保', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-004-007', 'support_type', '融资支持', '风险投资', '股权投资', 'ST007', '政府引导基金股权投资', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-004-008', 'support_type', '人才支持', '人才引进补贴', '引才补贴', 'ST008', '人才引进安家补贴和生活补贴', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- =============================================================
-- 5. 适用范围数据 (applicable_scope) - 8条
-- =============================================================
INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-005-001', 'applicable_scope', '全国级', '中央部委', '工信部项目', 'AS001', '工业和信息化部发布的全国性项目', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-005-002', 'applicable_scope', '全国级', '中央部委', '科技部项目', 'AS002', '科学技术部发布的全国性项目', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-005-003', 'applicable_scope', '全国级', '全国性', '中小企业发展', 'AS003', '面向全国中小企业的普惠性政策', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-005-004', 'applicable_scope', '省级', '省级政府', '省级重点项目', 'AS004', '省级人民政府发布的重点项目', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-005-005', 'applicable_scope', '省级', '省级部门', '省工信厅项目', 'AS005', '省工业和信息化厅发布的项目', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-005-006', 'applicable_scope', '市级', '市级政府', '市级专项', 'AS006', '市级人民政府发布的专项政策', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-005-007', 'applicable_scope', '市级', '市级部门', '市发改委项目', 'AS007', '市发展和改革委员会发布的项目', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

INSERT INTO SYSDBA.BASE_DATA_DICT (ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATE_TIME, UPDATE_TIME)
VALUES ('bd-005-008', 'applicable_scope', '区县级', '区政府', '区级专项', 'AS008', '区级人民政府发布的专项政策', '启用', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

-- =============================================================
-- 数据插入完成
-- =============================================================