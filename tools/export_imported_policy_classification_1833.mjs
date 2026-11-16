import fs from 'node:fs';
import path from 'node:path';

const plan = JSON.parse(fs.readFileSync('tmp/policy_import_allocation_by_main_title.json', 'utf8'));
const verify = fs.existsSync('tmp/policy_import_1850_verify.json')
  ? JSON.parse(fs.readFileSync('tmp/policy_import_1850_verify.json', 'utf8'))
  : null;

const rows = plan.allocRows || [];

function clean(value) {
  return String(value ?? '').trim();
}

function attrOrder(attr) {
  return { '指导政策': 1, '实施细则': 2, '申报通知': 3, '结果公告': 4 }[attr] || 99;
}

const uniqueMap = new Map();
for (const row of rows) {
  const key = `${clean(row.mainTitle)}\u0001${clean(row.assignedPolicyId)}`;
  const attr = clean(row.type);
  const mdTitle = clean(row.title);
  if (!uniqueMap.has(key)) {
    uniqueMap.set(key, {
      mainNo: Number(row.mainNo || 0),
      mainTitle: clean(row.mainTitle),
      policyId: clean(row.assignedPolicyId),
      type: attr,
      title: clean(row.assignedTitle || row.title),
      mdTitle,
      publishDate: clean(row.publishDate),
      filePath: clean(row.filePath),
      lines: row.lineNo ? [row.lineNo] : [],
      attrs: new Set(attr ? [attr] : []),
      mdTitles: new Set(mdTitle ? [mdTitle] : []),
    });
  } else {
    const item = uniqueMap.get(key);
    if (row.lineNo) item.lines.push(row.lineNo);
    if (attr) item.attrs.add(attr);
    if (mdTitle) item.mdTitles.add(mdTitle);
    if (attr) item.type = attr;
  }
}

const uniqueRows = [...uniqueMap.values()];
const groups = new Map();
for (const row of uniqueRows) {
  if (!groups.has(row.mainTitle)) {
    groups.set(row.mainTitle, { mainNo: row.mainNo, mainTitle: row.mainTitle, docs: [] });
  }
  groups.get(row.mainTitle).docs.push(row);
}

const groupList = [...groups.values()].sort(
  (a, b) => a.mainNo - b.mainNo || a.mainTitle.localeCompare(b.mainTitle, 'zh-Hans-CN'),
);

for (const group of groupList) {
  group.docs.sort(
    (a, b) =>
      attrOrder(a.type) - attrOrder(b.type) ||
      (a.publishDate || '').localeCompare(b.publishDate || '') ||
      Number(a.policyId) - Number(b.policyId),
  );
}

let duplicateTitleGroups = 0;
let duplicateTitleRows = 0;
for (const group of groupList) {
  const byTitle = new Map();
  for (const doc of group.docs) {
    const title = doc.title || doc.mdTitle;
    if (!byTitle.has(title)) byTitle.set(title, []);
    byTitle.get(title).push(doc);
  }
  for (const sameTitleDocs of byTitle.values()) {
    if (sameTitleDocs.length > 1) {
      duplicateTitleGroups += 1;
      duplicateTitleRows += sameTitleDocs.length;
      for (const doc of sameTitleDocs) doc.sameTitleDuplicate = true;
    }
  }
}

const totalsByType = {};
for (const row of uniqueRows) totalsByType[row.type] = (totalsByType[row.type] || 0) + 1;

function distributionText(docs) {
  const counts = {};
  for (const doc of docs) counts[doc.type] = (counts[doc.type] || 0) + 1;
  return Object.entries(counts)
    .sort((a, b) => attrOrder(a[0]) - attrOrder(b[0]))
    .map(([type, count]) => `${type}:${count}`)
    .join(' / ');
}

const totalDistribution = Object.entries(totalsByType)
  .sort((a, b) => attrOrder(a[0]) - attrOrder(b[0]))
  .map(([type, count]) => `${type}:${count}`)
  .join(' / ');

const output = [];
output.push('# 已归类入库政策清单（1833条）', '');
output.push('## 总体情况', '');
output.push('- 来源文件: 8.4含有实施细则或申报通知的分类.md');
output.push(`- 本次导入计划行数: ${rows.length}`);
output.push(`- 已入库唯一关联政策数: ${uniqueRows.length}`);
output.push(`- 已入库主政策数: ${groupList.length}`);
output.push(`- 政策属性分布: ${totalDistribution}`);
output.push(`- 同一主政策下同标题但不同 POLICY_ID 的标题组数: ${duplicateTitleGroups}`);
output.push(`- 同一主政策下同标题但不同 POLICY_ID 的关联政策数: ${duplicateTitleRows}`);
if (verify?.stats) {
  output.push(
    `- 校验结果: foundFusion=${verify.stats.foundFusion}, foundDocument=${verify.stats.foundDocument}, missingFusion=${verify.stats.missingFusion}, missingDocument=${verify.stats.missingDocument}`,
  );
}
output.push('', '## 主政策明细', '');

let index = 1;
for (const group of groupList) {
  output.push(`### ${index}. ${group.mainTitle}`, '');
  output.push(`- 原报告主政策编号: ${group.mainNo}`);
  output.push(`- 关联政策数: ${group.docs.length}`);
  output.push(`- 政策属性分布: ${distributionText(group.docs) || '-'}`);
  output.push('', '#### 关联政策', '');

  group.docs.forEach((doc, docIndex) => {
    const publishDate = doc.publishDate ? `，发布日期：${doc.publishDate}` : '';
    const duplicateNote = doc.sameTitleDuplicate ? '，同名文件以 POLICY_ID 区分' : '';
    const attrConflict = doc.attrs.size > 1 ? `，md属性冲突：${[...doc.attrs].join('/')}` : '';
    const titleNote = doc.mdTitles.size > 1 ? `，md标题数：${doc.mdTitles.size}` : '';
    output.push(
      `${docIndex + 1}. [${doc.type || '-'}] ${doc.title || doc.mdTitle}（POLICY_ID：${doc.policyId}${publishDate}${duplicateNote}${attrConflict}${titleNote}）`,
    );
  });
  output.push('');
  index += 1;
}

fs.mkdirSync('tmp', { recursive: true });
const outPath = path.resolve('tmp/imported_policy_classification_1833.md');
fs.writeFileSync(outPath, `${output.join('\n')}\n`, 'utf8');

console.log(JSON.stringify({
  outPath,
  mainPolicies: groupList.length,
  documents: uniqueRows.length,
  duplicateTitleGroups,
  duplicateTitleRows,
  totalDistribution,
}, null, 2));
