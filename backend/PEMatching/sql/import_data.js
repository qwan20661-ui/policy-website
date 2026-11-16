/**
 * 将 科小数据样例.json 转换为 SQL INSERT 语句
 * uid 采用 UUID v4 格式
 *
 * 注意：2023 年数据存在结构性错位，无法可靠修复，因此只导入 2024/2025 年数据
 */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const jsonPath = path.resolve(__dirname, '../企业测试/科小数据样例.json');
const data = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));

// 只导入这些年份的数据（2023 数据结构错位，跳过）
const IMPORT_YEARS = ['2024', '2025'];

function uuid() {
    return crypto.randomUUID();
}

// 收集所有企业的基础信息，为每个企业预分配一个 UUID
const enterpriseMap = new Map(); // credit_code -> { uid, ... }
for (const year of IMPORT_YEARS) {
    const yearData = data[year] || {};
    for (const ent of Object.values(yearData)) {
        const code = ent['统一社会信用代码'];
        if (!enterpriseMap.has(code)) {
            enterpriseMap.set(code, {
                uid: uuid(),
                企业名称: ent['企业名称'],
                统一社会信用代码: code,
                注册日期: ent['注册日期'] ? String(ent['注册日期']).replace(/\.0$/, '') : null,
                注册地: ent['注册地'] || null,
                地址: ent['地址'] || null,
                一级行业: ent['一级行业'] || null,
                二级行业: ent['二级行业'] || null,
            });
        }
    }
}

function escape(val) {
    if (val === null || val === undefined) return 'NULL';
    if (typeof val === 'number') return val;
    return "'" + String(val).replace(/'/g, "\\'") + "'";
}

const sql = [];
sql.push('-- ==========================================');
sql.push('-- 自动生成的科小企业数据导入脚本');
sql.push('-- 导入年份: ' + IMPORT_YEARS.join(', '));
sql.push('-- uid 格式: UUID v4');
sql.push('-- ==========================================');
sql.push('SET NAMES utf8mb4;');
sql.push('SET FOREIGN_KEY_CHECKS = 0;');
sql.push('');
sql.push('DELETE FROM `enterprise_yearly`;');
sql.push('DELETE FROM `enterprise`;');
sql.push('');

// 插入企业
sql.push(`-- ========== 企业主表 (${enterpriseMap.size}家) ==========`);
for (const ent of enterpriseMap.values()) {
    sql.push(`INSERT INTO \`enterprise\` (\`uid\`, \`企业名称\`, \`统一社会信用代码\`, \`注册日期\`, \`注册地\`, \`地址\`, \`一级行业\`, \`二级行业\`) VALUES (${escape(ent.uid)}, ${escape(ent['企业名称'])}, ${escape(ent['统一社会信用代码'])}, ${escape(ent['注册日期'])}, ${escape(ent['注册地'])}, ${escape(ent['地址'])}, ${escape(ent['一级行业'])}, ${escape(ent['二级行业'])});`);
}
sql.push('');

// 插入年度数据
sql.push('-- ========== 企业年度数据 ==========');
let totalRows = 0;
for (const year of IMPORT_YEARS) {
    const yearData = data[year] || {};
    const count = Object.keys(yearData).length;
    sql.push(`-- ${year}年数据 (${count}家)`);

    for (const ent of Object.values(yearData)) {
        const code = ent['统一社会信用代码'];
        const entUid = enterpriseMap.get(code).uid;
        const yearlyUid = uuid();

        const cols = [
            '`uid`', '`enterprise_uid`', '`年份`',
            '`指标1_注册地`', '`指标2_企业规模`', '`指标3_产品及服务范围`', '`指标4_企业信用`',
            '`指标5_高新技术企业`', '`指标6_研发机构`', '`指标7_科技奖励`', '`指标8_制定标准`',
            '`是否享受加计扣除`', '`享受加计扣除金额_元`', '`总得分`',
            '`科技人员占比`', '`研发费用占销售收入比例`', '`研发费用占成本费用比例`',
            '`I类知识产权数量`', '`II类知识产权数量`',
            '`资产总额`', '`净资产`', '`成本费用总额`', '`销售收入`', '`主营业务收入`',
            '`研发费用总额`', '`利润总额`', '`纳税总额`',
            '`职工总数`', '`在职职工人数`', '`兼职职工人数`', '`临时职工人数`',
            '`科技人员总数`', '`科技人员在职人数`', '`科技人员兼职人数`', '`科技人员临时人数`',
            '`本年新增人数`', '`吸纳高校毕业生人数`',
            '`博士人数`', '`硕士人数`', '`本科人数`', '`大专及以下人数`',
            '`高级职称人数`', '`中级职称人数`', '`初级职称人数`', '`高级技工人数`'
        ];

        const vals = [
            escape(yearlyUid), escape(entUid), ent['填报年份'] || year,
            escape(ent['指标1_注册地']), escape(ent['指标2_企业规模']),
            escape(ent['指标3_产品及服务范围']), escape(ent['指标4_企业信用']),
            escape(ent['指标5_高新技术企业']), escape(ent['指标6_研发机构']),
            escape(ent['指标7_科技奖励']), escape(ent['指标8_制定标准']),
            escape(ent['是否享受加计扣除']), escape(ent['享受加计扣除金额(元)']),
            escape(ent['总得分']),
            escape(ent['科技人员占企业职工总数比例']),
            escape(ent['研发费用占销售收入总额比例']),
            escape(ent['研发费用占成本费用支出总额比例']),
            escape(ent['I类知识产权数量']), escape(ent['II类知识产权数量']),
            escape(ent['资产总额(万元)']), escape(ent['净资产(万元)']),
            escape(ent['成本费用总额(万元)']), escape(ent['销售收入(万元)']),
            escape(ent['主营业务收入(万元)']), escape(ent['研发费用总额(万元)']),
            escape(ent['利润总额(万元)']), escape(ent['纳税总额(万元)']),
            escape(ent['职工总数']), escape(ent['在职职工人数']),
            escape(ent['兼职职工人数']), escape(ent['临时职工人数']),
            escape(ent['科技人员总数']), escape(ent['科技人员在职人数']),
            escape(ent['科技人员兼职人数']), escape(ent['科技人员临时人数']),
            escape(ent['本年新增人数']), escape(ent['吸纳高校毕业生人数']),
            escape(ent['博士人数']), escape(ent['硕士人数']),
            escape(ent['本科人数']), escape(ent['大专及以下人数']),
            escape(ent['高级职称人数']), escape(ent['中级职称人数']),
            escape(ent['初级职称人数']), escape(ent['高级技工人数'])
        ];

        sql.push(`INSERT INTO \`enterprise_yearly\` (${cols.join(', ')}) VALUES (${vals.join(', ')});`);
        totalRows++;
    }
    sql.push('');
}

sql.push('SET FOREIGN_KEY_CHECKS = 1;');
sql.push(`-- 共导入 ${enterpriseMap.size} 家企业，${totalRows} 条年度记录`);

console.log(sql.join('\n'));
