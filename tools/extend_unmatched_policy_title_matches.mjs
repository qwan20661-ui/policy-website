import fs from 'node:fs';

const baseUrl = 'http://127.0.0.1:33044';
const summaryPath = 'tmp/policy_duplicate_match_summary.json';
if (!fs.existsSync(summaryPath)) throw new Error(`${summaryPath} not found`);

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
  for (let page = 2; page <= pages; page += 1) {
    const json = await fetchJson(`${baseUrl}/api/classification/policies?page=${page}&size=${size}`);
    all.push(...(json.data || []));
    console.log(`page ${page}/${pages}`);
  }
  return { total, all };
}

function policyIdOf(row) {
  return String(row.policyId || row.id || '').trim();
}

function buildPolicyIndex(policies) {
  return policies.map((policy) => {
    const titles = [
      trim(policy.title),
      trim(policy.policyOriginalTitle),
      trim(policy.fileName).replace(/\.md$/i, ''),
    ].filter(Boolean);
    const uniqueTitles = [...new Set(titles)];
    return {
      policy,
      policyId: policyIdOf(policy),
      titles: uniqueTitles,
      comparableTitles: uniqueTitles.map(comparable).filter(Boolean),
    };
  });
}

function scoreCandidate(queryNorm, candidateNorm) {
  if (!queryNorm || !candidateNorm) return 0;
  if (candidateNorm === queryNorm) return 10000;
  if (candidateNorm.startsWith(queryNorm)) return 9000 - Math.abs(candidateNorm.length - queryNorm.length);
  if (candidateNorm.includes(queryNorm)) return 8000 - Math.abs(candidateNorm.length - queryNorm.length);
  if (queryNorm.includes(candidateNorm)) return 6500 - Math.abs(candidateNorm.length - queryNorm.length);
  return 0;
}

function matchOne(title, index) {
  const queryNorm = comparable(title);
  const rawCandidates = [];
  if (queryNorm.length < 6) return { queryNorm, candidates: [], policyIds: [] };

  for (const item of index) {
    let best = 0;
    for (const cTitle of item.comparableTitles) {
      const score = scoreCandidate(queryNorm, cTitle);
      if (score > best) best = score;
    }
    if (best > 0) rawCandidates.push({ item, score: best });
  }

  rawCandidates.sort((a, b) => b.score - a.score || Number(b.item.policyId) - Number(a.item.policyId));
  const bestScore = rawCandidates[0]?.score || 0;
  const candidates = rawCandidates
    .filter((c) => c.score === bestScore || c.score >= 8000)
    .map((c) => ({
      policyId: c.item.policyId,
      score: c.score,
      title: c.item.policy.title || '',
      originalTitle: c.item.policy.policyOriginalTitle || '',
      publishDate: c.item.policy.publishDate || '',
      filePath: c.item.policy.filePath || '',
    }));
  const policyIds = [...new Set(candidates.map((c) => c.policyId).filter(Boolean))];
  return { queryNorm, candidates, policyIds };
}

const existing = JSON.parse(fs.readFileSync(summaryPath, 'utf8'));
const unmatchedTitles = existing.unmatchedTitles || [];
const { total, all } = await fetchAllPolicies();
const index = buildPolicyIndex(all);

const resolved = [];
const unresolved = [];
const ambiguous = [];

for (const group of unmatchedTitles) {
  const result = matchOne(group.title, index);
  const item = {
    title: group.title,
    mdCount: group.mdCount,
    rows: group.rows,
    queryNorm: result.queryNorm,
    policyIds: result.policyIds,
    candidates: result.candidates,
  };
  if (result.policyIds.length === 1) resolved.push(item);
  else if (result.policyIds.length === 0) unresolved.push(item);
  else ambiguous.push(item);
}

const summary = {
  dbTotalFetched: all.length,
  dbTotalReported: total,
  originalUnmatchedTitleGroups: unmatchedTitles.length,
  originalUnmatchedRows: unmatchedTitles.reduce((sum, g) => sum + g.mdCount, 0),
  extendUniqueMatchedTitleGroups: resolved.length,
  extendUniqueMatchedRows: resolved.reduce((sum, g) => sum + g.mdCount, 0),
  extendAmbiguousTitleGroups: ambiguous.length,
  extendAmbiguousRows: ambiguous.reduce((sum, g) => sum + g.mdCount, 0),
  stillUnmatchedTitleGroups: unresolved.length,
  stillUnmatchedRows: unresolved.reduce((sum, g) => sum + g.mdCount, 0),
};

const report = { summary, resolved, ambiguous, unresolved };
fs.writeFileSync('tmp/policy_extended_unmatched_match.json', JSON.stringify(report, null, 2), 'utf8');

const lines = [];
lines.push('# 167 条未命中政策标题延伸匹配结果', '');
for (const [k, v] of Object.entries(summary)) lines.push(`- ${k}: ${v}`);
lines.push('');
lines.push('## 可唯一确认');
lines.push('');
resolved.forEach((g, indexNo) => {
  const c = g.candidates[0];
  lines.push(`${indexNo + 1}. ${g.title}`);
  for (const r of g.rows) lines.push(`   - md line ${r.lineNo} [${r.type}] #${r.mainNo} ${r.mainTitle}`);
  lines.push(`   - DB POLICY_ID=${c.policyId} PUBLISH_DATE=${c.publishDate || '-'} SCORE=${c.score}`);
  lines.push(`     TITLE=${c.title}`);
  if (c.originalTitle && c.originalTitle !== c.title) lines.push(`     ORIGINAL_TITLE=${c.originalTitle}`);
  lines.push(`     FILE_PATH=${c.filePath || '-'}`, '');
});
lines.push('## 多候选待确认');
lines.push('');
ambiguous.forEach((g, indexNo) => {
  lines.push(`${indexNo + 1}. ${g.title}`);
  for (const r of g.rows) lines.push(`   - md line ${r.lineNo} [${r.type}] #${r.mainNo} ${r.mainTitle}`);
  for (const c of g.candidates.slice(0, 10)) {
    lines.push(`   - DB POLICY_ID=${c.policyId} PUBLISH_DATE=${c.publishDate || '-'} SCORE=${c.score}`);
    lines.push(`     TITLE=${c.title}`);
    lines.push(`     FILE_PATH=${c.filePath || '-'}`);
  }
  lines.push('');
});
lines.push('## 仍未找到');
lines.push('');
unresolved.forEach((g, indexNo) => {
  lines.push(`${indexNo + 1}. ${g.title}`);
  for (const r of g.rows) lines.push(`   - md line ${r.lineNo} [${r.type}] #${r.mainNo} ${r.mainTitle}`);
  lines.push('');
});
fs.writeFileSync('tmp/policy_extended_unmatched_match.md', `${lines.join('\n')}\n`, 'utf8');

console.log(JSON.stringify(summary, null, 2));
