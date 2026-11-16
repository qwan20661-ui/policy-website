import fs from 'node:fs';
import path from 'node:path';

const baseUrl = 'http://127.0.0.1:33044';
const mdFile = fs.readdirSync('.').find((name) => name.startsWith('8.4') && name.endsWith('.md'));
if (!mdFile) throw new Error('未找到 8.4*.md 文件');

function normalizeTitle(value) {
  return String(value ?? '').trim();
}

function parseMd(file) {
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/);
  const docs = [];
  let mainNo = null;
  let mainTitle = '';
  const mainRe = /^###\s+(\d+)\.\s+(.+?)\s*$/;
  const docRe = /^\d+\.\s+\[(指导政策|实施细则|申报通知|结果公告)\]\s+(.+?)(?:（发布时间：([0-9]{4}-[0-9]{2}-[0-9]{2})）)?\s*$/;
  lines.forEach((line, index) => {
    let match = line.match(mainRe);
    if (match) {
      mainNo = Number(match[1]);
      mainTitle = normalizeTitle(match[2]);
      return;
    }
    match = line.match(docRe);
    if (match && mainNo) {
      docs.push({
        lineNo: index + 1,
        mainNo,
        mainTitle,
        type: normalizeTitle(match[1]),
        title: normalizeTitle(match[2]),
      });
    }
  });
  return docs;
}

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${url}`);
  const json = await res.json();
  if (!json.success) throw new Error(`接口返回失败: ${url}`);
  return json;
}

async function fetchAllPolicies() {
  const pageSize = 2000;
  const first = await fetchJson(`${baseUrl}/api/classification/policies?page=1&size=${pageSize}`);
  const total = Number(first.pagination?.total || first.data?.length || 0);
  const pages = Math.ceil(total / pageSize);
  const all = [...(first.data || [])];
  console.log(`fetched page 1 / ${pages}, total ${total}`);
  for (let pageNo = 2; pageNo <= pages; pageNo += 1) {
    const json = await fetchJson(`${baseUrl}/api/classification/policies?page=${pageNo}&size=${pageSize}`);
    all.push(...(json.data || []));
    console.log(`fetched page ${pageNo} / ${pages}`);
  }
  return { total, all };
}

function pushMapList(map, key, value) {
  if (!key) return;
  if (!map.has(key)) map.set(key, []);
  map.get(key).push(value);
}

function groupDocsByTitle(docs) {
  const map = new Map();
  for (const doc of docs) pushMapList(map, doc.title, doc);
  return map;
}

function groupDbByTitle(policies) {
  const map = new Map();
  for (const policy of policies) {
    const title = normalizeTitle(policy.title);
    const originalTitle = normalizeTitle(policy.policyOriginalTitle);
    pushMapList(map, title, policy);
    if (originalTitle && originalTitle !== title) pushMapList(map, originalTitle, policy);
  }
  return map;
}

function policyIdOf(hit) {
  return String(hit.policyId || hit.id || '').trim();
}

function distinctPolicyIds(hits) {
  return [...new Set(hits.map(policyIdOf).filter(Boolean))];
}

function sumRows(groups) {
  return groups.reduce((sum, group) => sum + group.rows.length, 0);
}

function appendRows(lines, rows) {
  for (const row of rows) {
    lines.push(`   - md line ${row.lineNo} [${row.type}] #${row.mainNo} ${row.mainTitle}`);
  }
}

function appendHits(lines, hits) {
  if (!hits?.length) {
    lines.push('   - DB: 未命中');
    return;
  }
  const seen = new Set();
  for (const hit of hits) {
    const pid = policyIdOf(hit);
    const key = `${pid}|${hit.publishDate || ''}|${hit.filePath || ''}`;
    if (seen.has(key)) continue;
    seen.add(key);
    lines.push(`   - DB POLICY_ID=${pid} PUBLISH_DATE=${hit.publishDate || '-'} FILE_TYPE=${hit.fileType || '-'}`);
    lines.push(`     FILE_PATH=${hit.filePath || '-'}`);
    if (hit.policyOriginalTitle && hit.policyOriginalTitle !== hit.title) {
      lines.push(`     ORIGINAL_TITLE=${hit.policyOriginalTitle}`);
    }
  }
}

function appendSection(lines, title, groups) {
  lines.push(title, '');
  if (!groups.length) {
    lines.push('无。', '');
    return;
  }
  groups.forEach((group, index) => {
    lines.push(`${index + 1}. ${group.title}`);
    appendRows(lines, group.rows);
    appendHits(lines, group.hits);
    lines.push('');
  });
}

function analyze(docs, policies) {
  const docsByTitle = groupDocsByTitle(docs);
  const dbByTitle = groupDbByTitle(policies);
  const mdDuplicateGroups = [];
  const unmatchedGroups = [];
  const uniqueHitGroups = [];
  const multiPolicyIdGroups = [];
  const exactDuplicateGroups = [];
  const mdDuplicateFull = [];
  let totalDbRows = 0;

  for (const [title, rows] of docsByTitle.entries()) {
    const hits = dbByTitle.get(title) || [];
    const policyIds = distinctPolicyIds(hits);
    totalDbRows += hits.length;
    const group = { title, rows, hits, policyIds };
    if (rows.length > 1) {
      mdDuplicateGroups.push(group);
      mdDuplicateFull.push(group);
    }
    if (policyIds.length === 0) unmatchedGroups.push(group);
    else if (policyIds.length === 1) uniqueHitGroups.push(group);
    else multiPolicyIdGroups.push(group);

    if (policyIds.length === 1 && rows.length > 1) {
      const pairCounts = new Map();
      for (const row of rows) {
        const key = `${row.mainTitle}|${policyIds[0]}`;
        pairCounts.set(key, (pairCounts.get(key) || 0) + 1);
      }
      if ([...pairCounts.values()].some((count) => count > 1)) {
        exactDuplicateGroups.push(group);
      }
    }
  }
  return {
    docsByTitle,
    mdDuplicateGroups,
    unmatchedGroups,
    uniqueHitGroups,
    multiPolicyIdGroups,
    exactDuplicateGroups,
    mdDuplicateFull,
    totalDbRows,
  };
}

function writeReport(mdPath, docs, dbTotal, policies, result) {
  const lines = [];
  const mdDuplicateRows = result.mdDuplicateGroups.reduce((sum, g) => sum + g.rows.length, 0);
  lines.push('# 8.4 md 政策标题匹配只读预检', '');
  lines.push(`- 源文件: \`${path.resolve(mdPath)}\``);
  lines.push(`- 远程只读接口: \`${baseUrl}/api/classification/policies\``);
  lines.push(`- 原政策库拉取数量: ${policies.length} / ${dbTotal}`);
  lines.push(`- md 关联政策行数: ${docs.length}`);
  lines.push(`- md 去重后标题数: ${result.docsByTitle.size}`);
  lines.push(`- md 内部重复标题组数: ${result.mdDuplicateGroups.length}`);
  lines.push(`- md 内部重复标题涉及行数: ${mdDuplicateRows}`);
  lines.push(`- 数据库未命中标题组数: ${result.unmatchedGroups.length}`);
  lines.push(`- 数据库未命中 md 行数: ${sumRows(result.unmatchedGroups)}`);
  lines.push(`- 数据库唯一 POLICY_ID 命中标题组数: ${result.uniqueHitGroups.length}`);
  lines.push(`- 数据库唯一 POLICY_ID 命中 md 行数: ${sumRows(result.uniqueHitGroups)}`);
  lines.push(`- 数据库多 POLICY_ID 命中标题组数: ${result.multiPolicyIdGroups.length}`);
  lines.push(`- 数据库多 POLICY_ID 命中 md 行数: ${sumRows(result.multiPolicyIdGroups)}`);
  lines.push(`- 数据库返回匹配行数: ${result.totalDbRows}`);
  lines.push(`- 同一主政策下同一 POLICY_ID 完全重复组数: ${result.exactDuplicateGroups.length}`);
  lines.push(`- 同一主政策下同一 POLICY_ID 完全重复涉及 md 行数: ${sumRows(result.exactDuplicateGroups)}`);
  lines.push('');
  appendSection(lines, '## 同一主政策下同一 POLICY_ID 完全重复', result.exactDuplicateGroups);
  appendSection(lines, '## 数据库多 POLICY_ID 命中标题', result.multiPolicyIdGroups);
  appendSection(lines, '## 数据库未命中标题', result.unmatchedGroups);
  appendSection(lines, '## md 内部重复标题完整清单', result.mdDuplicateFull);
  fs.mkdirSync('tmp', { recursive: true });
  const out = path.resolve('tmp/policy_title_match_precheck.md');
  fs.writeFileSync(out, `${lines.join('\n')}\n`, 'utf8');
  console.log(`report: ${out}`);
}

const docs = parseMd(mdFile);
const { total, all } = await fetchAllPolicies();
const result = analyze(docs, all);
writeReport(mdFile, docs, total, all, result);
