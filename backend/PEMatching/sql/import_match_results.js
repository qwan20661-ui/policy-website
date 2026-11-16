/**
 * 将 llm_matching_results.json 导入数据库
 * 通过 enterprise_uid 关联到 enterprise 表
 */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const jsonPath = path.resolve(__dirname, '../llm_matching_results.json');
const data = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));

function uuid() {
    return crypto.randomUUID();
}

function escape(val) {
    if (val === null || val === undefined) return 'NULL';
    if (typeof val === 'number') return val;
    if (typeof val === 'boolean') return val ? 1 : 0;
    return "'" + String(val).replace(/\\/g, '\\\\').replace(/'/g, "\\'") + "'";
}

const sql = [];
sql.push('-- ==========================================');
sql.push('-- 自动生成的匹配结果导入脚本');
sql.push('-- ==========================================');
sql.push('SET NAMES utf8mb4;');
sql.push('SET FOREIGN_KEY_CHECKS = 0;');
sql.push('');
sql.push('DELETE FROM `match_risk`;');
sql.push('DELETE FROM `match_analysis`;');
sql.push('DELETE FROM `match_condition`;');
sql.push('DELETE FROM `match_result`;');
sql.push('');

let resultCount = 0, condCount = 0, analysisCount = 0, riskCount = 0;
let skippedEnts = [];

for (const ent of data) {
    const entName = ent['企业名称'];
    const entYear = ent['年份'];

    // 查找对应的 enterprise_uid（按企业名称匹配，enterprise表不含年份）
    const entUidLookup = `(SELECT \`uid\` FROM \`enterprise\` WHERE \`企业名称\` = '${entName.replace(/'/g, "\\'")}' LIMIT 1)`;

    for (const pol of ent['政策匹配结果'] || []) {
        if (pol['错误']) continue; // 跳过错误结果

        const resultUid = uuid();
        resultCount++;
        const conditions = pol['条件匹配详情'] || [];
        const fullMatch = conditions.filter(c => c['匹配度'] === '完全匹配').length;
        const matchRate = conditions.length > 0 ? fullMatch / conditions.length : 0;

        // === 1. 匹配结果主表 ===
        sql.push(`INSERT INTO \`match_result\` (\`uid\`, \`enterprise_uid\`, \`企业名称\`, \`政策标题\`, \`匹配率\`, \`是否推荐\`, \`是否匹配\`, \`总体建议\`) VALUES (
  ${escape(resultUid)},
  ${entUidLookup},
  ${escape(entName)},
  ${escape(pol['政策标题'])},
  ${matchRate.toFixed(4)},
  ${matchRate >= 0.90 ? 0 : 1},
  ${escape(pol['一票否决'] ? 0 : 1)},
  ${escape(pol['总体建议'])}
);`);

        // === 2. 条件匹配详情 ===
        for (const cond of conditions) {
            const condUid = uuid();
            condCount++;

            // 量化指标提取
            const quant = cond['量化指标'];
            // 如果有量化指标但企业数值未单独给出，从量化指标中取
            let entValue = cond['企业数值'];
            if ((entValue === null || entValue === undefined) && quant && quant['要求值'] !== undefined) {
                // 不自动填充，保持原样
            }

            sql.push(`INSERT INTO \`match_condition\` (\`uid\`, \`result_uid\`, \`企业名称\`, \`政策标题\`, \`条件名称\`, \`政策要求\`, \`企业现状\`, \`企业数值\`, \`匹配度\`, \`差距分析\`, \`风险等级\`) VALUES (
  ${escape(condUid)},
  ${escape(resultUid)},
  ${escape(entName)},
  ${escape(pol['政策标题'])},
  ${escape(cond['条件名称'])},
  ${escape(cond['政策要求'])},
  ${escape(cond['企业现状'])},
  ${escape(entValue)},
  ${escape(cond['匹配度'])},
  ${escape(cond['差距分析'])},
  ${escape(cond['风险等级'])}
);`);
        }

        // === 3. 优势/劣势分析 ===
        for (let i = 0; i < (pol['优势分析'] || []).length; i++) {
            const uid = uuid();
            analysisCount++;
            sql.push(`INSERT INTO \`match_analysis\` (\`uid\`, \`result_uid\`, \`企业名称\`, \`政策标题\`, \`类型\`, \`内容\`, \`排序\`) VALUES (${escape(uid)}, ${escape(resultUid)}, ${escape(entName)}, ${escape(pol['政策标题'])}, '优势', ${escape(pol['优势分析'][i])}, ${i});`);
        }
        for (let i = 0; i < (pol['劣势分析'] || []).length; i++) {
            const uid = uuid();
            analysisCount++;
            sql.push(`INSERT INTO \`match_analysis\` (\`uid\`, \`result_uid\`, \`企业名称\`, \`政策标题\`, \`类型\`, \`内容\`, \`排序\`) VALUES (${escape(uid)}, ${escape(resultUid)}, ${escape(entName)}, ${escape(pol['政策标题'])}, '劣势', ${escape(pol['劣势分析'][i])}, ${i});`);
        }

        // === 4. 风险提示 ===
        for (let i = 0; i < (pol['风险提示'] || []).length; i++) {
            const uid = uuid();
            riskCount++;
            sql.push(`INSERT INTO \`match_risk\` (\`uid\`, \`result_uid\`, \`企业名称\`, \`政策标题\`, \`内容\`, \`排序\`) VALUES (${escape(uid)}, ${escape(resultUid)}, ${escape(entName)}, ${escape(pol['政策标题'])}, ${escape(pol['风险提示'][i])}, ${i});`);
        }
    }
}

sql.push('');
sql.push('SET FOREIGN_KEY_CHECKS = 1;');
sql.push(`-- 导入完成: ${resultCount} 条匹配结果, ${condCount} 条条件详情, ${analysisCount} 条优劣势, ${riskCount} 条风险提示`);

console.log(sql.join('\n'));
