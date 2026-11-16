import fs from 'node:fs';
import path from 'node:path';

const baseUrl = 'http://127.0.0.1:33044';
const mdFile = fs.readdirSync('.').find((name) => name.startsWith('8.4') && name.endsWith('.md'));
if (!mdFile) throw new Error('8.4*.md not found');

function trim(value) {
  return String(value ?? '').trim();
}

function comparable(value) {
  return trim(value)
    .replace(/（发布时间：未匹配）/g, '')
    .replace(/发布时间：未匹配/g, '')
    .replace(/附件\d*[:：]/g, '')
    .replace(/\.(pdf|docx?|xlsx?|md)$/gi, '')
    .replace(/[“”"']/g, '')
    .replace(/[_\s\u3000]/g, '')
    .replace(/[《》〈〉（）()【】\[\]{}、，,。．.：:；;！!？?·\-—－]/g, '')
    .toLowerCase();
}

function parseMd(file) {
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/);
  const groups = [];
  const docs = [];
  let current = null;
  const mainRe = /^###\s+(\d+)\.\s+(.+?)\s*$/;
  const docRe = /^\d+\.\s+\[(指导政策|实施细则|申报通知|结果公告)\]\s+(.+?)(?:（发布时间：([0-9]{4}-[0-9]{2}-[0-9]{2})）)?\s*$/;
  lines.forEach((line, index) => {
    const mainMatch = line.match(mainRe);
    if (mainMatch) {
      current = {
        mainNo: Number(mainMatch[1]),
        mainTitle: trim(mainMatch[2]),
        lineNo: index + 1,
      };
      groups.push(current);
      return;
    }
    const docMatch = line.match(docRe);
    if (docMatch && current) {
      docs.push({
        mdRowNo: docs.length + 1,
        lineNo: index + 1,
        mainNo: current.mainNo,
        mainTitle: current.mainTitle,
        type: trim(docMatch[1]),
        title: trim(docMatch[2]),
      });
    }
  });
  return { groups, docs };
}

async function fetchJson(url) {
  let lastError;
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${url}`);
      const json = await res.json();
      if (!json.success) throw new Error(`api failed: ${url}`);
      return json;
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 900 * attempt));
    }
  }
  throw lastError;
}

async function fetchAllPolicies() {
  const size = 500;
  const first = await fetchJson(`${baseUrl}/api/classification/policies?page=1&size=${size}`);
  const total = Number(first.pagination?.total || 0);
  const pages = Math.ceil(total / size);
  const all = [...(first.data || [])];
  console.log(`page 1/${pages}`);
  for (let pageNo = 2; pageNo <= pages; pageNo += 1) {
    const json = await fetchJson(`${baseUrl}/api/classification/policies?page=${pageNo}&size=${size}`);
    all.push(...(json.data || []));
    console.log(`page ${pageNo}/${pages}`);
  }
  return { total, all };
}

function policyIdOf(row) {
  return String(row.policyId || row.id || '').trim();
}

function push(map, key, value) {
  if (!key) return;
  if (!map.has(key)) map.set(key, []);
  map.get(key).push(value);
}

function makeDbIndexes(policies) {
  const exact = new Map();
  const search = [];
  for (const policy of policies) {
    const titles = [
      trim(policy.title),
      trim(policy.policyOriginalTitle),
      trim(policy.fileName).replace(/\.md$/i, ''),
    ].filter(Boolean);
    const uniqueTitles = [...new Set(titles)];
    for (const title of uniqueTitles) push(exact, title, policy);
    search.push({
      policy,
      policyId: policyIdOf(policy),
      titles: uniqueTitles,
      comparableTitles: uniqueTitles.map(comparable).filter(Boolean),
    });
  }
  return { exact, search };
}

function scoreCandidate(queryNorm, candidateNorm) {
  if (!queryNorm || !candidateNorm) return 0;
  if (candidateNorm === queryNorm) return 10000;
  if (candidateNorm.startsWith(queryNorm)) return 9000 - Math.abs(candidateNorm.length - queryNorm.length);
  if (candidateNorm.includes(queryNorm)) return 8000 - Math.abs(candidateNorm.length - queryNorm.length);
  if (queryNorm.includes(candidateNorm)) return 6500 - Math.abs(candidateNorm.length - queryNorm.length);
  return 0;
}

function distinctPolicyIds(hits) {
  return [...new Set(hits.map(policyIdOf).filter(Boolean))];
}

function matchDoc(doc, indexes) {
  const exactHits = indexes.exact.get(doc.title) || [];
  const exactIds = distinctPolicyIds(exactHits);
  if (exactIds.length === 1) {
    return { status: 'exact_unique', policy: exactHits.find((h) => policyIdOf(h) === exactIds[0]), candidates: exactHits };
  }
  if (exactIds.length > 1) {
    return { status: 'exact_ambiguous', policy: null, candidates: exactHits };
  }

  const queryNorm = comparable(doc.title);
  const scored = [];
  if (queryNorm.length >= 6) {
    for (const item of indexes.search) {
      let best = 0;
      for (const title of item.comparableTitles) best = Math.max(best, scoreCandidate(queryNorm, title));
      if (best > 0) scored.push({ item, score: best });
    }
  }
  scored.sort((a, b) => b.score - a.score || Number(b.item.policyId) - Number(a.item.policyId));
  const bestScore = scored[0]?.score || 0;
  const candidates = scored
    .filter((c) => c.score === bestScore || c.score >= 8000)
    .map((c) => ({ ...c.item.policy, _score: c.score }));
  const ids = distinctPolicyIds(candidates);
  if (ids.length === 1) return { status: 'extended_unique', policy: candidates.find((h) => policyIdOf(h) === ids[0]), candidates };
  if (ids.length > 1) return { status: 'extended_ambiguous', policy: null, candidates };
  return { status: 'unmatched', policy: null, candidates: [] };
}

function importRow(doc, match) {
  const p = match.policy;
  return {
    mdRowNo: doc.mdRowNo,
    mdLineNo: doc.lineNo,
    mainNo: doc.mainNo,
    mainTitle: doc.mainTitle,
    policyAttribute: doc.type,
    mdTitle: doc.title,
    matchStatus: match.status,
    policyId: policyIdOf(p),
    dbTitle: p.title || '',
    dbOriginalTitle: p.policyOriginalTitle || '',
    publishDate: p.publishDate || '',
    filePath: p.filePath || '',
    fileType: p.fileType || '',
    currentPolicyAttribute: p.policyAttribute || '',
    reviewStatus: p.reviewStatus || '',
    attachmentCount: p.attachmentCount ?? 0,
  };
}

const { groups, docs } = parseMd(mdFile);
const { total, all } = await fetchAllPolicies();
const indexes = makeDbIndexes(all);

const readyRows = [];
const ambiguousRows = [];
const unmatchedRows = [];
for (const doc of docs) {
  const match = matchDoc(doc, indexes);
  if (match.policy) readyRows.push(importRow(doc, match));
  else if (match.status.includes('ambiguous')) {
    ambiguousRows.push({
      ...doc,
      matchStatus: match.status,
      candidates: match.candidates.map((p) => ({
        policyId: policyIdOf(p),
        title: p.title || '',
        originalTitle: p.policyOriginalTitle || '',
        publishDate: p.publishDate || '',
        filePath: p.filePath || '',
        score: p._score || '',
      })),
    });
  } else {
    unmatchedRows.push({ ...doc, matchStatus: match.status });
  }
}

const seenMainPolicy = new Set();
const duplicateReadyRows = [];
const dedupedReadyRows = [];
for (const row of readyRows) {
  const key = `${row.mainTitle}|${row.policyId}`;
  if (seenMainPolicy.has(key)) duplicateReadyRows.push(row);
  else {
    seenMainPolicy.add(key);
    dedupedReadyRows.push(row);
  }
}

const policiesMissingCritical = readyRows.filter((row) => !row.policyId || !row.dbTitle);
const mainPolicies = [...new Map(groups.map((g) => [g.mainTitle, g])).values()];
const duplicateMainTitles = groups.length - mainPolicies.length;

const summary = {
  sourceMd: mdFile,
  dbTotalFetched: all.length,
  dbTotalReported: total,
  mdMainPolicyRows: groups.length,
  mdUniqueMainPolicyTitles: mainPolicies.length,
  duplicateMainTitleRowsBeyondFirst: duplicateMainTitles,
  mdDocumentRows: docs.length,
  readyRows: readyRows.length,
  readyExactUniqueRows: readyRows.filter((r) => r.matchStatus === 'exact_unique').length,
  readyExtendedUniqueRows: readyRows.filter((r) => r.matchStatus === 'extended_unique').length,
  ambiguousRows: ambiguousRows.length,
  unmatchedRows: unmatchedRows.length,
  duplicateReadyRowsWithinSameMainPolicy: duplicateReadyRows.length,
  dedupedReadyRowsToInsertDocuments: dedupedReadyRows.length,
  policiesMissingCritical: policiesMissingCritical.length,
};

fs.mkdirSync('tmp', { recursive: true });
fs.writeFileSync('tmp/policy_import_plan_ready_rows.json', JSON.stringify({
  summary,
  mainPolicies,
  readyRows,
  dedupedReadyRows,
  duplicateReadyRows,
  ambiguousRows,
  unmatchedRows,
}, null, 2), 'utf8');

const lines = [];
lines.push('# 8.4 政策归类入库前准备清单', '');
for (const [key, value] of Object.entries(summary)) lines.push(`- ${key}: ${value}`);
lines.push('', '## 入库效果确认', '');
lines.push('- 每个主政策会新增/复用一条 `SYSDBA.X1_FUSION_POLICY`，保持 `STATUS=ACTIVE`、`DEL_FLAG=0`、`MARK_REVIEW_STATUS=0`、`STRUCTURE_REVIEW_STATUS=0`。');
lines.push('- 每条可确认关联政策会新增一条 `SYSDBA.X1_POLICY_DOCUMENT`，`SOURCE_POLICY_ID` 指向已存在的 `POLICY.ID`，并复制标题、发布日期、发文部门、文件路径等基础信息。');
lines.push('- 每条关联政策会 upsert `SYSDBA.POLICY_METADATA.POLICY_ATTRIBUTE` 为 md 里的 `[指导政策/实施细则/申报通知/结果公告]`，审核状态保持 `未通过`。');
lines.push('- 不执行审核通过接口，所以前端会和上次 3 条一样显示为待审核/标注中，而不是已审核。');
lines.push('', '## 同一主政策下重复挂载风险', '');
if (duplicateReadyRows.length) {
  lines.push(`可确认 ${readyRows.length} 行里有 ${duplicateReadyRows.length} 行是同一主政策下重复命中同一个 POLICY_ID。为了避免前端重复显示，建议实际插入 X1_POLICY_DOCUMENT 时跳过这些重复行。`, '');
  duplicateReadyRows.forEach((row, index) => {
    lines.push(`${index + 1}. skip md line ${row.mdLineNo} #${row.mainNo} ${row.mainTitle}`);
    lines.push(`   - POLICY_ID=${row.policyId} [${row.policyAttribute}] ${row.mdTitle}`);
  });
} else {
  lines.push('无。');
}
lines.push('', '## 仍不能自动入库', '');
lines.push(`- 多候选待确认: ${ambiguousRows.length} 行`);
lines.push(`- 未找到: ${unmatchedRows.length} 行`);
fs.writeFileSync('tmp/policy_import_plan_ready_rows.md', `${lines.join('\n')}\n`, 'utf8');

console.log(JSON.stringify(summary, null, 2));
