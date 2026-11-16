import fs from 'node:fs';

const baseUrl = 'http://127.0.0.1:33044';
const mdFile = fs.readdirSync('.').find((name) => name.startsWith('8.4') && name.endsWith('.md'));
if (!mdFile) throw new Error('8.4*.md not found');

function trim(value) {
  return String(value ?? '').trim();
}

function parseMd(file) {
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/);
  const docs = [];
  let mainNo = 0;
  let mainTitle = '';
  const mainRe = /^###\s+(\d+)\.\s+(.+?)\s*$/;
  const docRe = /^\d+\.\s+\[(指导政策|实施细则|申报通知|结果公告)\]\s+(.+?)(?:（发布时间：([0-9]{4}-[0-9]{2}-[0-9]{2})）)?\s*$/;
  for (let i = 0; i < lines.length; i += 1) {
    const mainMatch = lines[i].match(mainRe);
    if (mainMatch) {
      mainNo = Number(mainMatch[1]);
      mainTitle = trim(mainMatch[2]);
      continue;
    }
    const docMatch = lines[i].match(docRe);
    if (docMatch && mainNo) {
      docs.push({
        lineNo: i + 1,
        mainNo,
        mainTitle,
        type: trim(docMatch[1]),
        title: trim(docMatch[2]),
      });
    }
  }
  return docs;
}

async function fetchJson(url) {
  let lastError;
  for (let attempt = 1; attempt <= 4; attempt += 1) {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${url}`);
      const json = await res.json();
      if (!json.success) throw new Error(`api failed: ${url}`);
      return json;
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 800 * attempt));
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

function push(map, key, value) {
  if (!key) return;
  if (!map.has(key)) map.set(key, []);
  map.get(key).push(value);
}

function idOf(row) {
  return String(row.policyId || row.id || '').trim();
}

const docs = parseMd(mdFile);
const { total, all } = await fetchAllPolicies();

const docsByTitle = new Map();
for (const doc of docs) push(docsByTitle, doc.title, doc);

const dbByTitle = new Map();
for (const policy of all) {
  const title = trim(policy.title);
  const originalTitle = trim(policy.policyOriginalTitle);
  push(dbByTitle, title, policy);
  if (originalTitle && originalTitle !== title) push(dbByTitle, originalTitle, policy);
}

const duplicateMdGroups = [...docsByTitle.entries()]
  .filter(([, rows]) => rows.length > 1)
  .map(([title, rows]) => {
    const hits = dbByTitle.get(title) || [];
    const distinctIds = [...new Set(hits.map(idOf).filter(Boolean))];
    const distinctFiles = [...new Set(hits.map((h) => `${idOf(h)}|${h.publishDate || ''}|${h.filePath || ''}`).filter(Boolean))];
    const mainDocPairs = new Map();
    for (const row of rows) {
      for (const id of distinctIds.length ? distinctIds : ['']) {
        const key = `${row.mainTitle}|${id}`;
        mainDocPairs.set(key, (mainDocPairs.get(key) || 0) + 1);
      }
    }
    const hasExactDupWithinSameMain = distinctIds.length === 1 && [...mainDocPairs.values()].some((count) => count > 1);
    return {
      title,
      mdCount: rows.length,
      mdMainCount: new Set(rows.map((r) => r.mainTitle)).size,
      dbRowCount: hits.length,
      dbPolicyIdCount: distinctIds.length,
      dbFileCount: distinctFiles.length,
      quantityEqualByPolicyId: rows.length === distinctIds.length,
      quantityEqualByDbRows: rows.length === hits.length,
      hasDbDuplicate: distinctIds.length > 1 || hits.length > 1,
      hasExactDupWithinSameMain,
      rows: rows.map((r) => ({ lineNo: r.lineNo, mainNo: r.mainNo, mainTitle: r.mainTitle, type: r.type })),
      hits: hits.map((h) => ({ policyId: idOf(h), publishDate: h.publishDate || '', filePath: h.filePath || '' })),
    };
  });

const allGroups = [...docsByTitle.entries()].map(([title, rows]) => {
  const hits = dbByTitle.get(title) || [];
  const ids = [...new Set(hits.map(idOf).filter(Boolean))];
  return { title, rows, hits, ids };
});

const matchedRows = allGroups.reduce((sum, g) => sum + (g.ids.length ? g.rows.length : 0), 0);
const unmatchedRows = allGroups.reduce((sum, g) => sum + (!g.ids.length ? g.rows.length : 0), 0);
const uniqueHitRows = allGroups.reduce((sum, g) => sum + (g.ids.length === 1 ? g.rows.length : 0), 0);
const multiHitRows = allGroups.reduce((sum, g) => sum + (g.ids.length > 1 ? g.rows.length : 0), 0);

const summary = {
  mdFile,
  dbTotalFetched: all.length,
  dbTotalReported: total,
  mdRows: docs.length,
  mdUniqueTitles: docsByTitle.size,
  mdDuplicateTitleGroups: duplicateMdGroups.length,
  mdDuplicateRows: duplicateMdGroups.reduce((sum, g) => sum + g.mdCount, 0),
  duplicateMdAlsoDbDuplicateGroups: duplicateMdGroups.filter((g) => g.hasDbDuplicate).length,
  duplicateMdAlsoDbDuplicateRows: duplicateMdGroups.filter((g) => g.hasDbDuplicate).reduce((sum, g) => sum + g.mdCount, 0),
  duplicateMdDbPolicyIdCountEqualsMdCountGroups: duplicateMdGroups.filter((g) => g.quantityEqualByPolicyId).length,
  duplicateMdDbPolicyIdCountEqualsMdCountRows: duplicateMdGroups.filter((g) => g.quantityEqualByPolicyId).reduce((sum, g) => sum + g.mdCount, 0),
  duplicateMdDbRowCountEqualsMdCountGroups: duplicateMdGroups.filter((g) => g.quantityEqualByDbRows).length,
  duplicateMdDbRowCountEqualsMdCountRows: duplicateMdGroups.filter((g) => g.quantityEqualByDbRows).reduce((sum, g) => sum + g.mdCount, 0),
  exactDuplicateGroups: duplicateMdGroups.filter((g) => g.hasExactDupWithinSameMain).length,
  exactDuplicateRows: duplicateMdGroups.filter((g) => g.hasExactDupWithinSameMain).reduce((sum, g) => sum + g.mdCount, 0),
  matchedTitleGroups: allGroups.filter((g) => g.ids.length > 0).length,
  unmatchedTitleGroups: allGroups.filter((g) => g.ids.length === 0).length,
  uniqueHitTitleGroups: allGroups.filter((g) => g.ids.length === 1).length,
  multiHitTitleGroups: allGroups.filter((g) => g.ids.length > 1).length,
  matchedRows,
  unmatchedRows,
  uniqueHitRows,
  multiHitRows,
};

fs.mkdirSync('tmp', { recursive: true });
fs.writeFileSync('tmp/policy_duplicate_match_summary.json', JSON.stringify({
  summary,
  duplicateGroups: duplicateMdGroups,
  unmatchedTitles: allGroups.filter((g) => g.ids.length === 0).map((g) => ({
    title: g.title,
    mdCount: g.rows.length,
    rows: g.rows.map((r) => ({ lineNo: r.lineNo, mainNo: r.mainNo, mainTitle: r.mainTitle, type: r.type })),
  })),
  multiHitTitles: allGroups.filter((g) => g.ids.length > 1).map((g) => ({
    title: g.title,
    mdCount: g.rows.length,
    policyIds: g.ids,
    hits: g.hits.map((h) => ({ policyId: idOf(h), publishDate: h.publishDate || '', filePath: h.filePath || '' })),
  })),
}, null, 2), 'utf8');

console.log(JSON.stringify(summary, null, 2));
