/**
 * 政策解读系统 - 后端服务
 *
 * 架构说明：
 * - 本服务作为 API 网关
 * - 实际数据库操作通过调用 Java JDBC 中间层服务完成
 * - Java 服务地址: http://localhost:8080
 *
 */

const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const zlib = require('zlib');
const { spawn } = require('child_process');
const ENABLE_DYNAMIC_TRACE_CACHE_SAVE = false;
const TRACE_IMPORT_PRIORITY_CUTOFF = '2026-07-26 00:00:00';

// ============================================
// 全局异常保护 + 增强日志记录
// ============================================

const LOG_FILE = path.resolve(__dirname, 'server.log');
const MAX_LOG_SIZE = 5 * 1024 * 1024; // 5MB 轮转

function writeLog(...args) {
  const timestamp = new Date().toISOString().replace('T', ' ').slice(0, 23);
  const line = `[${timestamp}] ${args.join(' ')}\n`;
  console.log(line.trimEnd());

  // 同步写入日志文件，防止进程退出时丢失
  try {
    const fd = fs.openSync(LOG_FILE, 'a');
    const stat = fs.fstatSync(fd);
    if (stat.size > MAX_LOG_SIZE) {
      fs.ftruncateSync(fd, 0); // 超大小后清空轮转
    }
    fs.writeSync(fd, line);
    fs.closeSync(fd);
  } catch (e) {
    // 写日志本身失败不阻塞
  }
}

function writeError(...args) {
  const timestamp = new Date().toISOString().replace('T', ' ').slice(0, 23);
  const line = `[${timestamp}] ❌ ${args.join(' ')}\n`;
  console.error(line.trimEnd());
  try {
    const fd = fs.openSync(LOG_FILE, 'a');
    fs.writeSync(fd, line);
    fs.closeSync(fd);
  } catch (e) {}
}

writeLog('='.repeat(55));
writeLog('服务启动 — PID:', process.pid, 'Node:', process.version, '平台:', process.platform);
writeLog('工作目录:', __dirname);
writeLog('参数:', process.argv.slice(2).join(' ') || '(无)');

process.on('uncaughtException', (err) => {
  writeError('[未捕获异常]', err.message);
  writeError(err.stack || '(无堆栈)');
  // 不退出进程，记录后继续运行
});

process.on('unhandledRejection', (reason) => {
  writeError('[未处理拒绝]', String(reason));
  if (reason?.stack) writeError(reason.stack);
  // 不退出进程，记录后继续运行
});

process.on('exit', (code) => {
  writeLog('[进程退出] exit code:', code);
});

process.on('warning', (warning) => {
  writeLog('[警告]', warning.name, ':', warning.message);
});

// 重启计数器（记录服务被自动重启的次数）
let restartCount = 0;

function loadLocalEnvFile(filePath) {
  try {
    if (!fs.existsSync(filePath)) return;
    const lines = fs.readFileSync(filePath, 'utf-8').split(/\r?\n/);
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const index = trimmed.indexOf('=');
      if (index <= 0) continue;
      const key = trimmed.slice(0, index).trim();
      let value = trimmed.slice(index + 1).trim();
      if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
        value = value.slice(1, -1);
      }
      if (!process.env[key]) process.env[key] = value;
    }
  } catch (err) {
    writeError('[Env Load Error]', err.message);
  }
}

loadLocalEnvFile(path.resolve(__dirname, '.env'));
loadLocalEnvFile(path.resolve(__dirname, '.env.local'));

// ============================================
// 配置
// ============================================

// Java JDBC 中间层服务地址
const JDBC_SERVICE_URL = process.env.KEJIJU_JDBC_URL || "http://localhost:8080";

// Python 政策融合 LLM 服务地址
const FUSION_LLM_SERVICE_URL = process.env.FUSION_LLM_URL || 'http://localhost:8003';

// Python PEMatching 匹配服务地址
const PEMATCHING_SERVICE_URL = process.env.PEMATCHING_URL || 'http://localhost:8010';

// 政策推荐企业分页服务地址
const ENTERPRISE_SERVICE_URL = (process.env.ENTERPRISE_SERVICE_URL || "").trim();

// 本服务端口（供前端调用）
const PORT = Number(process.env.KEJIJU_NODE_PORT || 3000);

const IMPORTANTPLUS_DIR = path.resolve(path.join(__dirname, 'importantplus'));
const XMSME_OUTPUT_ROOT = path.resolve(process.env.CRAWLER_OUTPUT_ROOT || path.join(__dirname, 'docs1', '慧企云'));
const ROUND3_CRAWLER_SCRIPT = path.resolve(path.join(__dirname, 'extractdata', 'crawl_round3_policies.py'));
const POLICY_REMOTE_SYNC_SCRIPT = path.resolve(path.join(__dirname, 'extractdata', 'sync_policy_to_server.py'));
const CRAWLER_REMOTE_ENABLED = process.env.CRAWLER_REMOTE_ENABLED === "1";
const CRAWLER_REMOTE_REQUIRED = process.env.CRAWLER_REMOTE_REQUIRED !== '0';
const CRAWLER_REMOTE_HOST = (process.env.CRAWLER_REMOTE_HOST || "").trim();
const CRAWLER_REMOTE_PORT = Number(process.env.CRAWLER_REMOTE_PORT || 22);
const CRAWLER_REMOTE_USER = (process.env.CRAWLER_REMOTE_USER || "").trim();
const CRAWLER_REMOTE_ROOT = (process.env.CRAWLER_REMOTE_ROOT || "").trim();
const CRAWLER_RELATIVE_ROOT = process.env.CRAWLER_RELATIVE_ROOT || 'docs1';
const CRAWLER_REMOTE_FOLDER = (process.env.CRAWLER_REMOTE_FOLDER || "").trim();
const XMSME_AUTH_TOKEN = process.env.XMSME_AUTH_TOKEN || '';
const XMSME_COOKIE = process.env.XMSME_COOKIE || '';
const XMSME_RESOLVE_IP = (process.env.XMSME_RESOLVE_IP || "").trim();
const XMSME_FETCH_TIMEOUT_MS = positiveInteger(process.env.XMSME_FETCH_TIMEOUT_MS, 45000);
const XMSME_ATTACHMENT_TIMEOUT_MS = positiveInteger(process.env.XMSME_ATTACHMENT_TIMEOUT_MS, 60000);
const CRAWLER_SCRIPT_TIMEOUT_MS = positiveInteger(process.env.CRAWLER_SCRIPT_TIMEOUT_MS, 120000);
const CRAWLER_REMOTE_SYNC_TIMEOUT_MS = positiveInteger(process.env.CRAWLER_REMOTE_SYNC_TIMEOUT_MS, 45000);
let xmsmeIncrementalEnabled = process.env.XMSME_INCREMENTAL_ENABLED === "1";
let xmsmeIncrementalHour = Number(process.env.XMSME_INCREMENTAL_HOUR || 1);
let xmsmeIncrementalMinute = Number(process.env.XMSME_INCREMENTAL_MINUTE || 0);
const XMSME_INCREMENTAL_PAGE_SIZE = Number(process.env.XMSME_INCREMENTAL_PAGE_SIZE || 100);
const POLICYSQL_DIR = path.resolve(process.env.POLICYSQL_ROOT || path.join(__dirname, '..', 'policysql'));
const ATTACHMENT_ROOTS = [path.resolve(__dirname), POLICYSQL_DIR];
const AUTH_COOKIE = 'policy_session';
const SESSIONS_FILE = path.resolve(__dirname, '.sessions.json');
const sessions = new Map();
const crawlerPreviews = new Map();

function positiveInteger(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? Math.floor(number) : fallback;
}

// ============================================
// Sessions 持久化 — 看门狗重启后不丢失登录状态
// ============================================

function saveSessionsToDisk() {
  try {
    const obj = {};
    for (const [token, data] of sessions) {
      if (data.expiresAt > Date.now()) {
        obj[token] = data; // 只保存未过期的
      }
    }
    fs.writeFileSync(SESSIONS_FILE, JSON.stringify(obj, null, 2), 'utf-8');
  } catch (err) {
    // 写 session 文件失败不阻塞
  }
}

function loadSessionsFromDisk() {
  try {
    if (fs.existsSync(SESSIONS_FILE)) {
      const raw = fs.readFileSync(SESSIONS_FILE, 'utf-8');
      const obj = JSON.parse(raw);
      const now = Date.now();
      let loaded = 0;
      for (const [token, data] of Object.entries(obj)) {
        if (data.expiresAt > now) {
          sessions.set(token, data);
          loaded++;
        }
      }
      writeLog(`[Sessions] 从磁盘恢复了 ${loaded} 个会话`);
    }
  } catch (err) {
    writeLog('[Sessions] 读取持久化文件失败:', err.message);
  }
}

// 延迟写盘：每次会话变更后 3 秒内合并写入
let sessionSaveTimer = null;
function scheduleSaveSessions() {
  if (sessionSaveTimer) clearTimeout(sessionSaveTimer);
  sessionSaveTimer = setTimeout(() => {
    saveSessionsToDisk();
    sessionSaveTimer = null;
  }, 3000);
}

// 登录时写盘、退出时写盘、定时写盘（每 5 分钟）
setInterval(() => {
  if (sessions.size > 0) saveSessionsToDisk();
}, 5 * 60 * 1000);

// 进程退出时立即写盘
process.on('exit', () => {
  if (sessions.size > 0) saveSessionsToDisk();
});

// 启动时从磁盘加载
loadSessionsFromDisk();

function parseCookies(req) {
  return Object.fromEntries((req.headers.cookie || '').split(';').map(item => {
    const index = item.indexOf('=');
    if (index < 0) return ['', ''];
    return [item.slice(0, index).trim(), decodeURIComponent(item.slice(index + 1).trim())];
  }).filter(([key]) => key));
}

function getSession(req) {
  const token = parseCookies(req)[AUTH_COOKIE];
  const session = token ? sessions.get(token) : null;
  if (!session) return null;
  if (session.expiresAt <= Date.now()) {
    sessions.delete(token);
    return null;
  }
  return { token, ...session };
}

function resolveSessionOperator(session) {
  const user = session?.user || {};
  const operatorId = String(user.id || user.userId || user.username || '').trim();
  const operatorName = String(user.realName || user.username || user.name || '系统').trim() || '系统';
  return { operatorId, operatorName };
}

function withAuditContext(req, body = {}, defaults = {}) {
  const payload = body && typeof body === 'object' && !Array.isArray(body) ? { ...body } : {};
  const session = getSession(req);
  const { operatorId, operatorName } = resolveSessionOperator(session);
  const traceId = String(payload.traceId || defaults.traceId || crypto.randomUUID()).replace(/-/g, '');
  const merged = {
    ...payload,
    ...defaults,
    traceId,
    operatorId: String(payload.operatorId || defaults.operatorId || operatorId || '').trim(),
    operatorName: String(payload.operatorName || payload.operator || defaults.operatorName || operatorName || '系统').trim() || '系统'
  };
  if (!merged.operator) {
    merged.operator = merged.operatorName;
  }
  return merged;
}

function resolveAllowedAttachmentPath(filePath) {
  if (!filePath) return null;
  const candidates = path.isAbsolute(filePath)
    ? [path.resolve(filePath)]
    : ATTACHMENT_ROOTS.map(root => path.resolve(root, filePath));
  return candidates.find(candidate =>
    ATTACHMENT_ROOTS.some(root => candidate === root || candidate.startsWith(root + path.sep)) &&
    fs.existsSync(candidate) && fs.statSync(candidate).isFile()
  ) || null;
}

function ensureDownloadFileName(filePath, rawName) {
  const fallbackName = path.basename(filePath || '') || 'attachment';
  const preferredName = String(rawName || '').trim() || fallbackName;
  const currentExt = path.extname(preferredName);
  const fallbackExt = path.extname(fallbackName);
  if (!currentExt && fallbackExt) {
    return `${preferredName}${fallbackExt}`;
  }
  return preferredName;
}

async function resolveAttachmentBinaryTarget(query) {
  const directPath = String(query.filePath || '').trim();
  if (directPath) {
    const file = resolveAllowedAttachmentPath(directPath);
    if (!file) return null;
    return {
      file,
      fileName: ensureDownloadFileName(file, query.fileName)
    };
  }

  const attachmentId = String(query.attachmentId || query.id || '').trim();
  if (!attachmentId) {
    throw new Error('缺少附件标识');
  }

  const result = await fetchFromJDBC(`/api/attachment/meta?attachmentId=${encodeURIComponent(attachmentId)}`);
  if (!result?.success || !result?.data?.filePath) {
    throw new Error(result?.message || '未找到对应附件');
  }

  const file = resolveAllowedAttachmentPath(result.data.filePath);
  if (!file) return null;

  return {
    file,
    fileName: ensureDownloadFileName(
      file,
      result.data.attachmentName || result.data.attachmentShowName || query.fileName
    )
  };
}

function setSessionCookie(res, token, maxAge) {
  const parts = [`${AUTH_COOKIE}=${encodeURIComponent(token)}`, 'Path=/', 'HttpOnly', 'SameSite=Lax'];
  if (maxAge) parts.push(`Max-Age=${maxAge}`);
  res.setHeader('Set-Cookie', parts.join('; '));
}

function normalizePolicyName(value) {
  return String(value || '')
    .normalize('NFKC')
    .replace(/\.(md|txt)$/i, '')
    .replace(/[（(]政策已到期[）)]/g, '')
    .replace(/^\s*\d{4}\s*年?\s*/, '')
    .replace(/[^\p{L}\p{N}]/gu, '')
    .toLowerCase();
}

function longestCommonSubstringLength(a, b) {
  if (!a || !b) return 0;
  const previous = new Array(b.length + 1).fill(0);
  let longest = 0;
  for (let i = 1; i <= a.length; i++) {
    for (let j = b.length; j >= 1; j--) {
      previous[j] = a[i - 1] === b[j - 1] ? previous[j - 1] + 1 : 0;
      if (previous[j] > longest) longest = previous[j];
    }
  }
  return longest;
}

function findPolicyDirectory(title) {
  if (!fs.existsSync(IMPORTANTPLUS_DIR)) return null;
  const target = normalizePolicyName(title);
  if (!target) return null;

  let best = null;
  for (const entry of fs.readdirSync(IMPORTANTPLUS_DIR, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const normalized = normalizePolicyName(entry.name);
    let score = 0;
    if (normalized === target) score = 100000 + normalized.length;
    else if (normalized.includes(target) || target.includes(normalized)) score = 10000 + Math.min(normalized.length, target.length);
    else {
      const commonLength = longestCommonSubstringLength(normalized, target);
      if (commonLength >= 8) score = commonLength;
    }
    if (!best || score > best.score) best = { name: entry.name, score };
  }
  return best && best.score > 0 ? path.join(IMPORTANTPLUS_DIR, best.name) : null;
}

function findPolicyMainFile(policyDir, title) {
  const files = fs.readdirSync(policyDir, { withFileTypes: true })
    .filter(entry => entry.isFile() && /\.(md|txt)$/i.test(entry.name));
  if (files.length === 0) return null;

  const target = normalizePolicyName(title);
  files.sort((a, b) => {
    const aName = normalizePolicyName(a.name);
    const bName = normalizePolicyName(b.name);
    const score = name => name === target ? 100000 : (name.includes(target) || target.includes(name) ? Math.min(name.length, target.length) : 0);
    return score(bName) - score(aName);
  });
  return path.join(policyDir, files[0].name);
}

function parsePolicyMarkdown(content) {
  const readField = label => {
    const match = content.match(new RegExp(`^-[ \\t]*(?:\\*\\*)?${label}(?:\\*\\*)?[：:][ \\t]*(.*)$`, 'm'));
    return match ? match[1].trim() : '';
  };
  return {
    publishDate: readField('发布日期'),
    documentNo: readField('政策文号'),
    department: readField('发文部门')
  };
}

function enrichPolicyFromLocalMarkdown(policy) {
  if (!policy || (policy.publishDate && policy.documentNo && policy.department)) return policy;
  try {
    const policyDir = findPolicyDirectory(policy.title);
    const filePath = policyDir ? findPolicyMainFile(policyDir, policy.title) : null;
    if (!filePath) return policy;
    const metadata = parsePolicyMarkdown(fs.readFileSync(filePath, 'utf-8'));
    return {
      ...policy,
      publishDate: policy.publishDate || metadata.publishDate,
      documentNo: policy.documentNo || metadata.documentNo,
      department: policy.department || metadata.department
    };
  } catch (err) {
    console.warn('[Local Metadata] 读取失败:', policy.title, err.message);
    return policy;
  }
}

// ============================================
// 工具函数：转发请求到 Java JDBC 服务
// ============================================

function safeCrawlerFileName(name, fallback = 'policy') {
  const cleaned = String(name || fallback)
    .replace(/[<>:"/\\|?*\x00-\x1f]/g, '_')
    .replace(/\s+/g, ' ')
    .replace(/[. ]+$/g, '')
    .trim();
  return (cleaned || fallback).slice(0, 120);
}

function decodeHtmlEntities(text) {
  return String(text || '')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/&#(\d+);/g, (_, code) => String.fromCharCode(Number(code)));
}

function stripHtml(html) {
  return decodeHtmlEntities(String(html || '')
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/<style[\s\S]*?<\/style>/gi, '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<\/div>/gi, '\n')
    .replace(/<\/li>/gi, '\n')
    .replace(/<li[^>]*>/gi, '- ')
    .replace(/<[^>]+>/g, '')
  )
    .replace(/\r/g, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function htmlSectionToMarkdown(title, html) {
  const content = stripHtml(html);
  if (!content) return '';
  return `## ${title}\n\n${content}\n`;
}

function firstText(...values) {
  for (const value of values) {
    if (Array.isArray(value)) {
      const joined = value.map(item => typeof item === 'string' ? item : (item?.name || item?.deptName || '')).filter(Boolean).join('、');
      if (joined) return joined;
    } else if (value !== undefined && value !== null && String(value).trim()) {
      return String(value).trim();
    }
  }
  return '';
}

async function fetchWithTimeout(url, options, timeoutMs, label) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (err) {
    if (err?.name === 'AbortError') {
      throw new Error(`${label}超时（${Math.round(timeoutMs / 1000)}秒）`);
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

function firstDate(...values) {
  const value = firstText(...values);
  const match = value.match(/\d{4}[-/.]\d{1,2}[-/.]\d{1,2}/);
  return match ? match[0].replace(/[/.]/g, '-') : value;
}

function extractXmsmePolicyId(rawUrl) {
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    throw new Error('政策 URL 格式不正确');
  }

  const queryId = url.searchParams.get('id') || url.searchParams.get('policyId') || url.searchParams.get('policy_id');
  if (queryId) return queryId;

  const pathParts = url.pathname.split('/').filter(Boolean);
  const approvedIdx = pathParts.findIndex(part => part === 'approved');
  if (approvedIdx >= 0 && pathParts[approvedIdx + 1]) return pathParts[approvedIdx + 1];

  for (let i = pathParts.length - 1; i >= 0; i--) {
    if (/^[A-Za-z0-9_-]{10,}$/.test(pathParts[i])) return pathParts[i];
  }

  throw new Error('无法从 URL 中识别慧企云政策 ID');
}

async function fetchXmsmeJson(policyId) {
  const apiUrl = `https://www.xmsme.cn/api/gxdp/policies/approved/${encodeURIComponent(policyId)}`;
  const response = await fetchWithTimeout(apiUrl, {
    headers: {
      'Accept': 'application/json,text/plain,*/*',
      'User-Agent': 'Mozilla/5.0 PolicyCrawler/1.0'
    }
  }, XMSME_FETCH_TIMEOUT_MS, '慧企云政策详情接口请求');
  if (!response.ok) throw new Error(`慧企云接口请求失败：HTTP ${response.status}`);
  const json = await response.json();
  const data = json?.data || json;
  if (!data || !firstText(data.name, data.title)) throw new Error('慧企云接口未返回政策详情');
  return data;
}

function fileNameFromDisposition(disposition, fallback) {
  if (!disposition) return fallback;
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match) {
    try { return decodeURIComponent(utf8Match[1]); } catch {}
  }
  const quotedMatch = disposition.match(/filename="?([^";]+)"?/i);
  if (quotedMatch) {
    try { return decodeURIComponent(quotedMatch[1]); } catch { return quotedMatch[1]; }
  }
  return fallback;
}

function suffixOf(name) {
  const ext = path.extname(name || '').replace('.', '').trim();
  return ext || 'bin';
}

function crawlerPublicPath(filePath) {
  return String(filePath || '').replace(/\\/g, '/');
}

async function downloadXmsmeAttachment(attachment, attachmentDir) {
  const attachmentId = firstText(attachment.id, attachment.resourceId, attachment.fileId);
  if (!attachmentId) throw new Error(`附件缺少资源 ID：${attachment.name || '未命名附件'}`);
  const downloadUrl = `https://www.xmsme.cn/api/gxdp/common/download/resource/${encodeURIComponent(attachmentId)}`;
  const response = await fetchWithTimeout(downloadUrl, {
    headers: {
      'Accept': '*/*',
      'User-Agent': 'Mozilla/5.0 PolicyCrawler/1.0',
      'Referer': 'https://www.xmsme.cn/policy/market'
    }
  }, XMSME_ATTACHMENT_TIMEOUT_MS, '慧企云附件下载');
  if (!response.ok) throw new Error(`附件下载失败：${attachment.name || attachmentId} HTTP ${response.status}`);
  const buffer = Buffer.from(await response.arrayBuffer());
  const rawName = fileNameFromDisposition(response.headers.get('content-disposition'), attachment.name || `${attachmentId}.bin`);
  const fileName = safeCrawlerFileName(rawName, `${attachmentId}.bin`);
  const filePath = path.join(attachmentDir, fileName);
  await fs.promises.writeFile(filePath, buffer);
  return {
    id: attachmentId,
    name: fileName,
    showName: rawName || fileName,
    suffix: suffixOf(fileName),
    size: buffer.length,
    filePath: crawlerPublicPath(filePath),
    status: 'downloaded'
  };
}

function buildXmsmeMarkdown(policy, info) {
  const lines = [
    `# ${info.title}`,
    '',
    '## 基本信息',
    '',
    `- 来源URL：${info.sourceUrl}`,
    `- 发文部门：${info.department || '-'}`,
    `- 适用范围：${info.applicableScope || '-'}`,
    `- 发布日期：${info.publishDate || '-'}`,
    `- 文号：${info.policyCode || '-'}`,
    ''
  ];

  const sections = [
    ['申报条件', policy.declarationCondition],
    ['支持内容', policy.supportContent],
    ['申报材料', policy.declarationMaterial],
    ['申报流程', policy.declarationProcess],
    ['咨询电话', policy.consultationTel],
    ['其他说明', policy.content || policy.detail || policy.remark]
  ];
  for (const [title, html] of sections) {
    const section = htmlSectionToMarkdown(title, html);
    if (section) lines.push(section);
  }

  const documents = Array.isArray(policy.documents) ? policy.documents : [];
  if (documents.length) {
    lines.push('## 政策文件', '');
    documents.forEach(item => lines.push(`- ${firstText(item.name, item.title, item.fileName, item.id)}`));
    lines.push('');
  }

  if (info.attachments.length) {
    lines.push('## 附件', '');
    info.attachments.forEach(item => lines.push(`- [${item.showName || item.name}](attachments/${item.name})`));
    lines.push('');
  }

  return lines.join('\n').replace(/\n{3,}/g, '\n\n').trim() + '\n';
}

async function buildXmsmePreview(rawUrl) {
  const sourcePolicyId = extractXmsmePolicyId(rawUrl);
  const sourceUrl = rawUrl.trim();
  const policy = await fetchXmsmeJson(sourcePolicyId);
  const title = firstText(policy.name, policy.title, `慧企云政策_${sourcePolicyId}`);
  const safeTitle = safeCrawlerFileName(title, sourcePolicyId);
  const policyDir = path.join(XMSME_OUTPUT_ROOT, safeTitle);
  const attachmentDir = path.join(policyDir, 'attachments');
  await fs.promises.mkdir(attachmentDir, { recursive: true });

  const department = firstText(policy.depts, policy.deptName, policy.department, policy.issueDepartment);
  const applicableScope = firstText(policy.level, policy.applyScope, policy.scope, policy.applicableScope);
  const publishDate = firstDate(policy.approvedTime, policy.publishTime, policy.startTime, policy.createdTime);
  const policyCode = firstText(policy.policyCode, policy.documentNo, policy.code);
  const policyAttribute = firstText(policy.policyAttribute, policy.typeName, '申报通知');
  const attachments = [];

  for (const item of Array.isArray(policy.attachments) ? policy.attachments : []) {
    try {
      attachments.push(await downloadXmsmeAttachment(item, attachmentDir));
    } catch (err) {
      attachments.push({
        id: firstText(item.id, item.resourceId, item.fileId),
        name: safeCrawlerFileName(item.name || '未下载附件'),
        showName: item.name || '未下载附件',
        suffix: suffixOf(item.name || ''),
        size: 0,
        filePath: '',
        status: 'failed',
        error: err.message
      });
    }
  }

  const info = { title, sourcePolicyId, sourceUrl, department, applicableScope, publishDate, policyCode, policyAttribute, attachments };
  const markdown = buildXmsmeMarkdown(policy, info);
  const markdownPath = path.join(policyDir, `${safeTitle}.md`);
  await fs.promises.writeFile(markdownPath, markdown, 'utf-8');

  const previewId = crypto.randomUUID();
  const preview = {
    previewId,
    title,
    safeTitle,
    sourcePolicyId,
    sourceUrl,
    policyCode,
    publishDate,
    department,
    applicableScope,
    policyAttribute,
    markdown,
    markdownPath: crawlerPublicPath(markdownPath),
    policyDir: crawlerPublicPath(policyDir),
    attachmentDir: crawlerPublicPath(attachmentDir),
    attachments
  };
  crawlerPreviews.set(previewId, preview);
  return preview;
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    let c = i;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
    table[i] = c >>> 0;
  }
  return table;
})();

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function dosDateTime(date = new Date()) {
  const year = Math.max(1980, date.getFullYear());
  const dosTime = (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2);
  const dosDate = ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
  return { dosTime, dosDate };
}

function collectZipFiles(rootDir, baseDir = rootDir) {
  const entries = [];
  for (const item of fs.readdirSync(rootDir, { withFileTypes: true })) {
    const fullPath = path.join(rootDir, item.name);
    if (item.isDirectory()) {
      entries.push(...collectZipFiles(fullPath, baseDir));
    } else if (item.isFile()) {
      entries.push({
        fullPath,
        relativePath: path.relative(baseDir, fullPath).split(path.sep).join('/')
      });
    }
  }
  return entries;
}

function createZipBuffer(rootDir, rootName) {
  const localParts = [];
  const centralParts = [];
  let offset = 0;
  for (const entry of collectZipFiles(rootDir)) {
    const content = fs.readFileSync(entry.fullPath);
    const nameBuffer = Buffer.from(`${rootName}/${entry.relativePath}`, 'utf-8');
    const crc = crc32(content);
    const { dosTime, dosDate } = dosDateTime(fs.statSync(entry.fullPath).mtime);

    const localHeader = Buffer.alloc(30);
    localHeader.writeUInt32LE(0x04034b50, 0);
    localHeader.writeUInt16LE(20, 4);
    localHeader.writeUInt16LE(0x0800, 6);
    localHeader.writeUInt16LE(0, 8);
    localHeader.writeUInt16LE(dosTime, 10);
    localHeader.writeUInt16LE(dosDate, 12);
    localHeader.writeUInt32LE(crc, 14);
    localHeader.writeUInt32LE(content.length, 18);
    localHeader.writeUInt32LE(content.length, 22);
    localHeader.writeUInt16LE(nameBuffer.length, 26);
    localHeader.writeUInt16LE(0, 28);
    localParts.push(localHeader, nameBuffer, content);

    const centralHeader = Buffer.alloc(46);
    centralHeader.writeUInt32LE(0x02014b50, 0);
    centralHeader.writeUInt16LE(20, 4);
    centralHeader.writeUInt16LE(20, 6);
    centralHeader.writeUInt16LE(0x0800, 8);
    centralHeader.writeUInt16LE(0, 10);
    centralHeader.writeUInt16LE(dosTime, 12);
    centralHeader.writeUInt16LE(dosDate, 14);
    centralHeader.writeUInt32LE(crc, 16);
    centralHeader.writeUInt32LE(content.length, 20);
    centralHeader.writeUInt32LE(content.length, 24);
    centralHeader.writeUInt16LE(nameBuffer.length, 28);
    centralHeader.writeUInt16LE(0, 30);
    centralHeader.writeUInt16LE(0, 32);
    centralHeader.writeUInt16LE(0, 34);
    centralHeader.writeUInt16LE(0, 36);
    centralHeader.writeUInt32LE(0, 38);
    centralHeader.writeUInt32LE(offset, 42);
    centralParts.push(centralHeader, nameBuffer);
    offset += localHeader.length + nameBuffer.length + content.length;
  }

  const centralSize = centralParts.reduce((sum, part) => sum + part.length, 0);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(0, 4);
  end.writeUInt16LE(0, 6);
  end.writeUInt16LE(centralParts.length / 2, 8);
  end.writeUInt16LE(centralParts.length / 2, 10);
  end.writeUInt32LE(centralSize, 12);
  end.writeUInt32LE(offset, 16);
  end.writeUInt16LE(0, 20);

  return Buffer.concat([...localParts, ...centralParts, end]);
}

function getCrawlerPythonCommand() {
  const bundledPython = 'D:\\anaconda\\python.exe';
  if (process.env.PYTHON) return process.env.PYTHON;
  if (process.platform === 'win32' && fs.existsSync(bundledPython)) return bundledPython;
  return process.platform === 'win32' ? 'python' : 'python3';
}

function runRound3Crawler(urls) {
  return new Promise((resolve, reject) => {
    if (!fs.existsSync(ROUND3_CRAWLER_SCRIPT)) {
      reject(new Error('round3 爬取脚本不存在'));
      return;
    }

    const pythonCmd = getCrawlerPythonCommand();
    const args = [
      ROUND3_CRAWLER_SCRIPT,
      '--urls-json',
      JSON.stringify(urls),
      '--out-root',
      XMSME_OUTPUT_ROOT
    ];
    const child = spawn(pythonCmd, args, {
      cwd: path.dirname(ROUND3_CRAWLER_SCRIPT),
      windowsHide: true,
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8'
      }
    });

    let stdout = '';
    let stderr = '';
    let timedOut = false;
    const timeoutId = setTimeout(() => {
      timedOut = true;
      child.kill('SIGKILL');
    }, CRAWLER_SCRIPT_TIMEOUT_MS);
    child.stdout.on('data', chunk => { stdout += chunk.toString('utf8'); });
    child.stderr.on('data', chunk => { stderr += chunk.toString('utf8'); });
    child.on('error', err => {
      clearTimeout(timeoutId);
      reject(err);
    });
    child.on('close', code => {
      clearTimeout(timeoutId);
      if (timedOut) {
        reject(new Error(`round3 爬取脚本超时（${Math.round(CRAWLER_SCRIPT_TIMEOUT_MS / 1000)}秒），请检查目标站点或附件下载是否卡住`));
        return;
      }
      if (code !== 0) {
        reject(new Error(`round3 爬取脚本执行失败：${stderr || `exit ${code}`}`));
        return;
      }
      try {
        const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
        const payload = JSON.parse(lines[lines.length - 1] || '{}');
        if (!payload.success) throw new Error(payload.message || 'round3 爬取失败');
        resolve(payload.data || []);
      } catch (err) {
        reject(new Error(`round3 爬取结果解析失败：${err.message}`));
      }
    });
  });
}

function getCrawlerRemoteCategory(preview) {
  return CRAWLER_REMOTE_FOLDER;
}

function normalizeRelativePath(value) {
  return String(value || '').replace(/\\/g, '/').replace(/^\/+/, '');
}

function syncPolicyPreviewToRemote(preview) {
  return new Promise((resolve, reject) => {
    if (!CRAWLER_REMOTE_ENABLED) {
      resolve({ skipped: true, message: 'remote sync disabled' });
      return;
    }
    if (!fs.existsSync(POLICY_REMOTE_SYNC_SCRIPT)) {
      reject(new Error('政策服务器同步脚本不存在'));
      return;
    }
    if (!CRAWLER_REMOTE_HOST || !CRAWLER_REMOTE_USER || !CRAWLER_REMOTE_ROOT || !process.env.CRAWLER_REMOTE_PASSWORD) {
      const err = new Error('请配置 CRAWLER_REMOTE_HOST、CRAWLER_REMOTE_USER、CRAWLER_REMOTE_ROOT 和 CRAWLER_REMOTE_PASSWORD');
      if (CRAWLER_REMOTE_REQUIRED) reject(err);
      else resolve({ skipped: true, message: err.message });
      return;
    }

    const localAttachments = (preview.attachments || []).map(item => ({
      ...item,
      filePath: item.localFilePath || item.filePath
    }));
    const payload = {
      host: CRAWLER_REMOTE_HOST,
      port: CRAWLER_REMOTE_PORT,
      user: CRAWLER_REMOTE_USER,
      remoteRoot: CRAWLER_REMOTE_ROOT,
      relativeRoot: CRAWLER_RELATIVE_ROOT,
      category: getCrawlerRemoteCategory(preview),
      safeTitle: preview.safeTitle || safeCrawlerFileName(preview.title || 'policy'),
      markdownPath: preview.localMarkdownPath || preview.markdownPath,
      attachmentDir: preview.localAttachmentDir || preview.attachmentDir,
      attachments: localAttachments
    };
    const child = spawn(getCrawlerPythonCommand(), [
      POLICY_REMOTE_SYNC_SCRIPT,
      '--payload-json',
      JSON.stringify(payload)
    ], {
      cwd: path.dirname(POLICY_REMOTE_SYNC_SCRIPT),
      windowsHide: true,
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8'
      }
    });

    let stdout = '';
    let stderr = '';
    let timedOut = false;
    const timeoutId = setTimeout(() => {
      timedOut = true;
      child.kill('SIGKILL');
    }, CRAWLER_REMOTE_SYNC_TIMEOUT_MS);
    child.stdout.on('data', chunk => { stdout += chunk.toString('utf8'); });
    child.stderr.on('data', chunk => { stderr += chunk.toString('utf8'); });
    child.on('error', err => {
      clearTimeout(timeoutId);
      reject(err);
    });
    child.on('close', code => {
      clearTimeout(timeoutId);
      if (timedOut) {
        reject(new Error(`政策文件同步服务器超时（${Math.round(CRAWLER_REMOTE_SYNC_TIMEOUT_MS / 1000)}秒），请检查 SSH/SFTP 连接或远端 docs1 目录`));
        return;
      }
      const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
      const lastLine = lines[lines.length - 1] || '{}';
      let result = null;
      try {
        result = JSON.parse(lastLine);
      } catch (err) {
        reject(new Error(`政策服务器同步结果解析失败：${err.message}`));
        return;
      }
      if (code !== 0 || !result.success) {
        reject(new Error(result.message || stderr || `政策服务器同步失败：exit ${code}`));
        return;
      }
      resolve(result);
    });
  });
}

function applyCrawlerRemotePaths(preview, syncResult) {
  preview.localPolicyDir = preview.localPolicyDir || preview.policyDir;
  preview.localMarkdownPath = preview.localMarkdownPath || preview.markdownPath;
  preview.localAttachmentDir = preview.localAttachmentDir || preview.attachmentDir;

  if (!syncResult || syncResult.skipped) {
    preview.remoteSync = syncResult || { skipped: true };
    return preview;
  }

  preview.remoteSync = {
    success: true,
    remotePolicyDir: syncResult.remotePolicyDir,
    remoteMarkdownPath: syncResult.remoteMarkdownPath,
    remoteAttachmentDir: syncResult.remoteAttachmentDir
  };
  preview.policyDir = normalizeRelativePath(syncResult.relativePolicyDir);
  preview.markdownPath = normalizeRelativePath(syncResult.relativeMarkdownPath);
  preview.attachmentDir = normalizeRelativePath(syncResult.relativeAttachmentDir);

  const uploadedByName = new Map((syncResult.attachments || []).map(item => [item.name || item.showName, item]));
  preview.attachments = (preview.attachments || []).map(item => {
    const uploaded = uploadedByName.get(item.name || item.showName);
    if (!uploaded) {
      return {
        ...item,
        localFilePath: item.localFilePath || item.filePath
      };
    }
    return {
      ...item,
      localFilePath: item.localFilePath || item.filePath,
      filePath: normalizeRelativePath(uploaded.filePath),
      remoteFilePath: uploaded.remoteFilePath
    };
  });
  return preview;
}

function resolveCrawlerMarkdownWritePath(preview) {
  const rawPath = String(preview?.localMarkdownPath || preview?.markdownPath || '').trim();
  if (!rawPath) throw new Error('预览记录缺少 Markdown 文件路径');
  const resolved = path.isAbsolute(rawPath) ? path.resolve(rawPath) : path.resolve(__dirname, rawPath);
  const allowedRoots = [path.resolve(__dirname), XMSME_OUTPUT_ROOT];
  const allowed = allowedRoots.some(root => resolved === root || resolved.startsWith(root + path.sep));
  if (!allowed || path.extname(resolved).toLowerCase() !== '.md') {
    throw new Error('Markdown 文件路径不在允许目录内');
  }
  return resolved;
}

async function updateCrawlerPreviewMarkdown(preview, markdown) {
  if (typeof markdown !== 'string') return preview;
  const normalizedMarkdown = markdown.replace(/\r\n/g, '\n');
  const markdownPath = resolveCrawlerMarkdownWritePath(preview);
  await fs.promises.mkdir(path.dirname(markdownPath), { recursive: true });
  await fs.promises.writeFile(markdownPath, normalizedMarkdown, 'utf-8');
  preview.markdown = normalizedMarkdown;
  preview.localMarkdownPath = crawlerPublicPath(markdownPath);
  if (!preview.markdownPath || path.isAbsolute(preview.markdownPath) || !preview.remoteSync || preview.remoteSync.skipped) {
    preview.markdownPath = crawlerPublicPath(markdownPath);
  }
  if (CRAWLER_REMOTE_ENABLED && preview.remoteSync && !preview.remoteSync.skipped) {
    const syncResult = await syncPolicyPreviewToRemote(preview);
    applyCrawlerRemotePaths(preview, syncResult);
  }
  return preview;
}

function applyCrawlerPreviewEdits(preview, edits = {}) {
  const editableFields = ['title', 'department', 'applicableScope', 'publishDate', 'policyCode', 'policyAttribute'];
  for (const field of editableFields) {
    if (!Object.prototype.hasOwnProperty.call(edits, field)) continue;
    preview[field] = String(edits[field] ?? '').trim();
  }
  if (Object.prototype.hasOwnProperty.call(edits, 'documentNo') && !Object.prototype.hasOwnProperty.call(edits, 'policyCode')) {
    preview.policyCode = String(edits.documentNo ?? '').trim();
  }
  preview.documentNo = preview.policyCode || '';
  return preview;
}

function buildCrawlerImportPayload(preview) {
  const payload = {
    ...preview,
    attachments: (preview.attachments || []).map(attachment => {
      const copied = { ...attachment };
      delete copied.localFilePath;
      delete copied.remoteFilePath;
      return copied;
    })
  };
  delete payload.markdown;
  delete payload.localPolicyDir;
  delete payload.localMarkdownPath;
  delete payload.localAttachmentDir;
  delete payload.remoteSync;
  return payload;
}

async function importCrawlerPreviewToDatabase(preview) {
  return fetchFromJDBC('/api/crawler/xmsme/import', 'POST', buildCrawlerImportPayload(preview));
}

async function buildRound3CrawlerPreview(rawUrl) {
  const results = await runRound3Crawler([rawUrl]);
  const preview = results[0];
  if (!preview) throw new Error('round3 爬取脚本未返回政策数据');
  preview.previewId = crypto.randomUUID();
  preview.safeTitle = preview.safeTitle || safeCrawlerFileName(preview.title || 'policy');
  preview.policyAttribute = preview.policyAttribute || '通知公告';
  const syncResult = await syncPolicyPreviewToRemote(preview);
  applyCrawlerRemotePaths(preview, syncResult);
  crawlerPreviews.set(preview.previewId, preview);
  return preview;
}

function postJsonExternal(url, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const data = Buffer.from(JSON.stringify(body), 'utf-8');
    const options = {
      hostname: urlObj.hostname,
      port: urlObj.port || 443,
      path: urlObj.pathname + urlObj.search,
      method: 'POST',
      lookup: XMSME_RESOLVE_IP && urlObj.hostname === 'www.xmsme.cn'
        ? (hostname, options, callback) => callback(null, XMSME_RESOLVE_IP, 4)
        : undefined,
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/plain, */*',
        'User-Agent': 'Mozilla/5.0 PolicyCrawler/round3',
        'Referer': 'https://www.xmsme.cn/policy/market',
        'Content-Length': data.length,
        ...headers
      },
      timeout: 45000
    };
    const req = https.request(options, res => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf-8');
        try {
          resolve(JSON.parse(text));
        } catch (err) {
          reject(new Error(`慧企云接口返回非 JSON：${text.slice(0, 120)}`));
        }
      });
    });
    req.on('timeout', () => req.destroy(new Error('慧企云接口请求超时')));
    req.on('error', reject);
    req.write(data);
    req.end();
  });
}

function normalizeCrawlerDate(value) {
  const text = String(value || '').trim();
  const match = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (!match) throw new Error('日期格式必须为 YYYY-MM-DD');
  return `${match[1]}-${match[2].padStart(2, '0')}-${match[3].padStart(2, '0')}`;
}

function formatLocalDate(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function yesterdayLocalDate() {
  const date = new Date();
  date.setDate(date.getDate() - 1);
  return formatLocalDate(date);
}

function buildCrawlerDateRange(startValue, endValue, maxDays = 31) {
  const startDate = normalizeCrawlerDate(startValue);
  const endDate = normalizeCrawlerDate(endValue);
  if (startDate > endDate) {
    throw new Error('开始日期不能晚于结束日期');
  }
  const dates = [];
  const cursor = new Date(`${startDate}T00:00:00`);
  const end = new Date(`${endDate}T00:00:00`);
  while (cursor <= end) {
    dates.push(formatLocalDate(cursor));
    if (dates.length > maxDays) {
      throw new Error(`日期范围不能超过 ${maxDays} 天`);
    }
    cursor.setDate(cursor.getDate() + 1);
  }
  return dates;
}

function extractXmsmeRows(payload) {
  const candidates = [
    payload?.rows,
    payload?.data?.rows,
    payload?.data?.content,
    payload?.data?.records,
    payload?.data
  ];
  for (const candidate of candidates) {
    if (Array.isArray(candidate)) return candidate;
  }
  return [];
}

function extractXmsmeTotal(payload, fallbackLength) {
  const values = [payload?.total, payload?.data?.total, payload?.data?.totalElements];
  for (const value of values) {
    const number = Number(value);
    if (Number.isFinite(number)) return number;
  }
  return fallbackLength;
}

function extractXmsmeIssuedDate(row) {
  const values = [row?.issuedTime, row?.issuedOn, row?.publishTime, row?.approvedTime, row?.createdTime];
  for (const value of values) {
    const match = String(value || '').match(/\d{4}[-/.]\d{1,2}[-/.]\d{1,2}/);
    if (match) {
      const [year, month, day] = match[0].replace(/[/.]/g, '-').split('-');
      return `${year}-${month.padStart(2, '0')}-${day.padStart(2, '0')}`;
    }
  }
  return '';
}

function compareCrawlerDate(left, right) {
  if (left === right) return 0;
  return left < right ? -1 : 1;
}

async function fetchXmsmeIncrementalUrls(targetDate) {
  const date = normalizeCrawlerDate(targetDate);
  const headers = {};
  if (XMSME_AUTH_TOKEN) headers['X-Auth-Token'] = XMSME_AUTH_TOKEN;
  if (XMSME_COOKIE) headers['Cookie'] = XMSME_COOKIE;
  const urls = [];
  const seen = new Set();
  let total = null;
  let pageNum = 1;
  const pageSize = XMSME_INCREMENTAL_PAGE_SIZE;

  while (pageNum <= 100) {
    let reachedOlderRows = false;
    const body = {
      levels: [],
      categories: [],
      labels: [],
      issueOrgIds: [],
      columnIds: [],
      types: [],
      statuses: ['ENABLED'],
      orderByColumn: 'issuedTime',
      pageNum,
      pageSize,
      isAsc: 'desc'
    };
    const payload = await postJsonExternal('https://www.xmsme.cn/api/gxdp/documents/previewpage', body, headers);
    if (payload?.code !== 200) {
      throw new Error(`慧企云列表接口失败：${payload?.msg || payload?.message || '未知错误'}${XMSME_AUTH_TOKEN || XMSME_COOKIE ? '' : '；如接口需要登录态，请配置 XMSME_AUTH_TOKEN 或 XMSME_COOKIE'}`);
    }
    const rows = extractXmsmeRows(payload);
    if (total === null) total = extractXmsmeTotal(payload, rows.length);
    for (const row of rows) {
      const id = String(row?.id || row?.documentId || row?.fileId || '').trim();
      const issued = extractXmsmeIssuedDate(row);
      if (!id || seen.has(id)) continue;
      if (!issued) continue;
      const dateOrder = compareCrawlerDate(issued, date);
      if (dateOrder > 0) continue;
      if (dateOrder < 0) {
        reachedOlderRows = true;
        continue;
      }
      seen.add(id);
      urls.push(`https://www.xmsme.cn/policy/market/${encodeURIComponent(id)}`);
    }
    if (!rows.length || reachedOlderRows || pageNum * pageSize >= total) break;
    pageNum += 1;
  }
  return urls;
}

let xmsmeIncrementalRunning = false;
let lastXmsmeIncrementalResult = null;
let xmsmeIncrementalTimer = null;

async function runXmsmeIncrementalImport(targetDate, trigger = 'manual') {
  if (xmsmeIncrementalRunning) throw new Error('慧企云增量任务正在运行，请稍后再试');
  const date = normalizeCrawlerDate(targetDate || yesterdayLocalDate());
  xmsmeIncrementalRunning = true;
  const startedAt = new Date().toISOString();
  const result = {
    date,
    trigger,
    startedAt,
    finishedAt: '',
    total: 0,
    imported: 0,
    failed: 0,
    items: []
  };
  try {
    const urls = await fetchXmsmeIncrementalUrls(date);
    result.total = urls.length;
    for (const url of urls) {
      const item = { url, success: false, message: '', fetchStartedAt: new Date().toISOString(), fetchedAt: '', importStartedAt: '', importedAt: '', finishedAt: '' };
      try {
        const preview = await buildRound3CrawlerPreview(url);
        item.fetchedAt = new Date().toISOString();
        item.importStartedAt = new Date().toISOString();
        const importResult = await importCrawlerPreviewToDatabase(preview);
        item.importedAt = new Date().toISOString();
        item.title = preview.title;
        item.previewId = preview.previewId;
        item.importResult = importResult;
        item.success = Boolean(importResult?.success);
        item.message = importResult?.message || (item.success ? '入库成功' : '入库失败');
        if (item.success) result.imported += 1;
        else result.failed += 1;
      } catch (err) {
        item.message = err.message;
        result.failed += 1;
      } finally {
        item.finishedAt = new Date().toISOString();
      }
      result.items.push(item);
    }
    result.finishedAt = new Date().toISOString();
    lastXmsmeIncrementalResult = result;
    return result;
  } finally {
    xmsmeIncrementalRunning = false;
  }
}

async function runXmsmeIncrementalImportRange(startValue, endValue, trigger = 'manual') {
  const dates = buildCrawlerDateRange(startValue, endValue);
  if (dates.length === 1) {
    return runXmsmeIncrementalImport(dates[0], trigger);
  }
  const startedAt = new Date().toISOString();
  const result = {
    date: `${dates[0]} 至 ${dates[dates.length - 1]}`,
    startDate: dates[0],
    endDate: dates[dates.length - 1],
    trigger,
    startedAt,
    finishedAt: '',
    total: 0,
    imported: 0,
    failed: 0,
    items: []
  };
  for (const date of dates) {
    try {
      const dailyResult = await runXmsmeIncrementalImport(date, trigger);
      result.total += Number(dailyResult.total || 0);
      result.imported += Number(dailyResult.imported || 0);
      result.failed += Number(dailyResult.failed || 0);
      result.items.push(...(dailyResult.items || []).map(item => ({ ...item, date })));
    } catch (err) {
      result.failed += 1;
      result.items.push({
        date,
        success: false,
        message: err.message || '增量更新失败',
        finishedAt: new Date().toISOString()
      });
      break;
    }
  }
  result.finishedAt = new Date().toISOString();
  lastXmsmeIncrementalResult = result;
  return result;
}

function fetchFromJDBC(path, method = 'GET', body = null, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(JDBC_SERVICE_URL + path);
    const options = {
      hostname: urlObj.hostname,
      port: urlObj.port,
      path: urlObj.pathname + urlObj.search,
      method: method,
      headers: {
        'Content-Type': 'application/json; charset=utf-8',
        'Accept': 'application/json'
      },
      timeout: timeoutMs
    };

    writeLog('[JDBC Request]', method, options.path);

    const req = http.request(options, (res) => {
      const chunks = [];
      res.on('data', chunk => {
        chunks.push(chunk);
      });
      res.on('end', () => {
        const buffer = Buffer.concat(chunks);
        const data = buffer.toString('utf8');
        writeLog('[JDBC Response]', 'length:', data.length, 'status:', res.statusCode, 'path:', options.path);
        try {
          const json = JSON.parse(data);
          resolve(json);
        } catch (e) {
          writeError('[JDBC Parse Error]', e.message);
          writeError('[JDBC Response Preview]', data.substring(0, 300));
          resolve({ success: false, message: '解析响应失败: ' + e.message });
        }
      });
    });

    req.on('error', (err) => {
      writeError('[JDBC Network Error]', err.message, 'path:', options.path);
      // 超时错误给友好提示
      if (err.code === 'ETIMEDOUT' || err.message.includes('timeout')) {
        reject(new Error('JDBC 服务响应超时，请检查数据库连接状态'));
      } else {
        reject(err);
      }
    });

    // 自定义超时处理（兼容某些 Node 版本）
    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error(`JDBC 服务响应超时（${Math.round(timeoutMs / 1000)}秒），请检查数据库连接状态`));
    });

    if (body) {
      req.write(JSON.stringify(body));
    }
    req.end();
  });
}

function normalizeClientErrorMessage(message, fallback = '请求失败') {
  const text = String(message || '').trim();
  if (!text) return fallback;
  if (/^bad message$/i.test(text)) {
    return '请求被上游服务拒绝：请检查查询条件或政策 URL 是否完整、合法';
  }
  return text;
}

// ============================================
// 工具函数：转发请求到 Python 政策融合 LLM 服务
// ============================================

function fetchFromLLMService(path, method = 'GET', body = null) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(FUSION_LLM_SERVICE_URL + path);
    const options = {
      hostname: urlObj.hostname,
      port: urlObj.port,
      path: urlObj.pathname + urlObj.search,
      method: method,
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
      },
      timeout: 600000  // 10分钟超时（LLM 调用较慢）
    };

    writeLog('[Fusion LLM Request]', method, options.path);

    const req = http.request(options, (res) => {
      const chunks = [];
      res.on('data', chunk => {
        chunks.push(chunk);
      });
      res.on('end', () => {
        const buffer = Buffer.concat(chunks);
        const data = buffer.toString('utf8');
        writeLog('[Fusion LLM Response]', 'length:', data.length, 'status:', res.statusCode, 'path:', options.path);
        try {
          const json = JSON.parse(data);
          resolve(json);
        } catch (e) {
          writeError('[Fusion LLM Parse Error]', e.message);
          writeError('[Fusion LLM Response Preview]', data.substring(0, 300));
          resolve({ success: false, message: '解析 LLM 服务响应失败: ' + e.message });
        }
      });
    });

    req.on('error', (err) => {
      writeError('[Fusion LLM Network Error]', err.message, 'path:', options.path);
      if (err.code === 'ECONNREFUSED') {
        reject(new Error('政策融合 LLM 服务未启动，请运行 python backend\\融合\\app.py'));
      } else if (err.code === 'ETIMEDOUT' || err.message.includes('timeout')) {
        reject(new Error('政策融合 LLM 服务响应超时'));
      } else {
        reject(err);
      }
    });

    req.setTimeout(600000, () => {
      req.destroy(new Error('政策融合 LLM 服务响应超时（10分钟）'));
    });

    if (body) {
      req.write(JSON.stringify(body));
    }
    req.end();
  });
}

// 工具函数：转发请求到 Python PEMatching 匹配服务
function fetchFromPEMatchingService(path, method = 'GET', body = null) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(PEMATCHING_SERVICE_URL + path);
    const requestBody = body === null || body === undefined
      ? null
      : Buffer.from(JSON.stringify(body), 'utf8');
    const options = {
      hostname: urlObj.hostname,
      port: urlObj.port,
      path: urlObj.pathname + urlObj.search,
      method: method,
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
      },
      timeout: 630000 // 两次 5 分钟模型尝试外加本地解析/写回缓冲，仍低于批处理客户端 660 秒
    };
    if (requestBody) {
      options.headers['Content-Length'] = String(requestBody.length);
    }

    writeLog('[PEMatching Request]', method, options.path);

    const req = http.request(options, (res) => {
      const chunks = [];
      res.on('data', chunk => { chunks.push(chunk); });
      res.on('end', () => {
        const buffer = Buffer.concat(chunks);
        const data = buffer.toString('utf8');
        writeLog('[PEMatching Response]', 'length:', data.length, 'status:', res.statusCode);
        try {
          const json = JSON.parse(data);
          if (json && typeof json === 'object') {
            Object.defineProperty(json, '__httpStatus', {
              value: res.statusCode,
              enumerable: false
            });
          }
          resolve(json);
        } catch (e) {
          resolve({ success: false, message: '解析匹配服务响应失败: ' + e.message });
        }
      });
    });

    req.on('error', (err) => {
      writeError('[PEMatching Network Error]', err.message);
      if (err.code === 'ECONNREFUSED') {
        reject(new Error('PEMatching 匹配服务未启动，请运行 python backend\\PEMatching\\api_server.py'));
      } else {
        reject(err);
      }
    });

    req.setTimeout(600000, () => {
      req.destroy(new Error('PEMatching 服务响应超时'));
    });

    if (requestBody) {
      req.write(requestBody);
    }
    req.end();
  });
}

// 工具函数：读取政策推荐企业分页服务
function fetchFromEnterpriseService(path) {
  return new Promise((resolve, reject) => {
    if (!ENTERPRISE_SERVICE_URL) {
      reject(new Error('请设置 ENTERPRISE_SERVICE_URL 以连接企业信息服务'));
      return;
    }
    const urlObj = new URL(ENTERPRISE_SERVICE_URL + path);
    const transport = urlObj.protocol === 'https:' ? https : http;
    const options = {
      hostname: urlObj.hostname,
      port: urlObj.port || (urlObj.protocol === 'https:' ? 443 : 80),
      path: urlObj.pathname + urlObj.search,
      method: 'GET',
      headers: {
        'Accept': 'application/json'
      },
      timeout: 30000
    };

    writeLog('[Enterprise Request]', options.path);

    const req = transport.request(options, (res) => {
      const chunks = [];
      res.on('data', chunk => { chunks.push(chunk); });
      res.on('end', () => {
        const data = Buffer.concat(chunks).toString('utf8');
        writeLog('[Enterprise Response]', 'length:', data.length, 'status:', res.statusCode);
        try {
          const json = JSON.parse(data);
          resolve(json);
        } catch (e) {
          resolve({ code: -1, success: false, message: '解析企业分页服务响应失败: ' + e.message });
        }
      });
    });

    req.on('error', (err) => {
      writeError('[Enterprise Network Error]', err.message);
      reject(err);
    });

    req.setTimeout(30000, () => {
      req.destroy(new Error('企业分页服务响应超时'));
    });

    req.end();
  });
}

// ============================================
// API 路由处理
// ============================================

function normalizeTraceText(value) {
  return String(value || '')
    .replace(/\r/g, '\n')
    .replace(/^[\s　]*(?:\d+[\u3001.．、]|[（(]?\d+[）)]|[-*•])\s*/gm, '')
    .replace(/[《》“”"']/g, '')
    .replace(/\s+/g, '')
    .trim();
}

function compactTraceEvidence(value, maxLength = 900) {
  const text = String(value || '').replace(/\r/g, '').replace(/\n{3,}/g, '\n\n').trim();
  return text.length <= maxLength ? text : text.slice(0, maxLength) + '...';
}

function cleanTraceEvidenceText(value = '') {
  return String(value || '')
    .replace(/\r/g, '\n')
    .replace(/([\u4e00-\u9fa5])\n([\u4e00-\u9fa5])/g, '$1$2')
    .replace(/([A-Za-z0-9%）)])\n([\u4e00-\u9fa5])/g, '$1$2')
    .replace(/([\u4e00-\u9fa5])\n([A-Za-z0-9（(])/g, '$1$2')
    .replace(/\n+/g, ' ')
    .replace(/[ \t]+/g, ' ')
    .replace(/([\u4e00-\u9fa5])\s+([\u4e00-\u9fa5])/g, '$1$2')
    .replace(/\s*([，。；：、！？（）()])\s*/g, '$1')
    .trim();
}

function traceReadableNeedle(fieldValue = '', block = '') {
  const value = cleanTraceEvidenceText(fieldValue);
  const cleanedBlock = cleanTraceEvidenceText(block);
  if (value && cleanedBlock.includes(value)) return value;
  const keywords = traceKeywords(value)
    .filter(keyword => keyword.length >= 2)
    .sort((a, b) => b.length - a.length);
  return keywords.find(keyword => cleanedBlock.includes(keyword)) || keywords[0] || value;
}

function nearestTraceBoundaryBefore(text, index, maxBack = 140) {
  const start = Math.max(0, index - maxBack);
  const slice = text.slice(start, index);
  const boundaryRegex = /[。！？；;.]\s*|(?:\d+[\u3001、.．]|[一二三四五六七八九十百]+[\u3001、.．]|[（(][一二三四五六七八九十\d]+[）)]|第[一二三四五六七八九十百]+条)\s*/g;
  let last = -1;
  let match;
  while ((match = boundaryRegex.exec(slice)) !== null) {
    last = start + match.index + match[0].length;
  }
  return last >= 0 ? last : start;
}

function nearestTraceBoundaryAfter(text, index, maxForward = 220) {
  const end = Math.min(text.length, index + maxForward);
  const slice = text.slice(index, end);
  const boundaryRegex = /[。！？；;.]|(?=(?:\d+[\u3001、.．]|[一二三四五六七八九十百]+[\u3001、.．]|[（(][一二三四五六七八九十\d]+[）)]|第[一二三四五六七八九十百]+条))/g;
  const punctuation = boundaryRegex.exec(slice);
  if (punctuation) return index + punctuation.index + 1;
  const listRegex = /\s(?:\d+[\u3001、.．]|[（(][一二三四五六七八九十\d]+[）)]|第[一二三四五六七八九十百]+条)\s*/g;
  const list = listRegex.exec(slice);
  if (list && list.index > 20) return index + list.index;
  return end;
}

function trimReadableEvidenceAroundNeedle(text, needle, maxLength = 300) {
  if (text.length <= maxLength) return text;
  const normalizedNeedle = cleanTraceEvidenceText(needle);
  const index = normalizedNeedle ? text.indexOf(normalizedNeedle) : -1;
  const center = index >= 0 ? index + Math.floor(normalizedNeedle.length / 2) : Math.floor(text.length / 2);
  const half = Math.floor(maxLength / 2);
  let start = Math.max(0, center - half);
  let end = Math.min(text.length, start + maxLength);
  start = Math.max(0, end - maxLength);
  let clipped = text.slice(start, end).trim();
  clipped = clipped.replace(/^[，、；;：:）)\]]+/, '').replace(/[，、；;：:（(\[]+$/, '');
  if (start > 0) clipped = `...${clipped}`;
  if (end < text.length) clipped = `${clipped}...`;
  return clipped;
}

function buildReadableTraceEvidence(content = '', block = '', fieldValue = '', options = {}) {
  const maxLength = Number(options.maxLength || 240);
  const cleanedBlock = cleanTraceEvidenceText(block);
  const cleanedContent = cleanTraceEvidenceText(content);
  const needle = traceReadableNeedle(fieldValue, cleanedBlock);
  const blockComplete = cleanedBlock.length >= 24
    && cleanedBlock.length <= maxLength
    && /[。！？；;]$/.test(cleanedBlock)
    && (!needle || cleanedBlock.includes(needle) || normalizeTraceText(cleanedBlock).includes(normalizeTraceText(needle)));
  if (blockComplete) return cleanedBlock;

  const normalizedNeedle = normalizeTraceText(needle);
  const normalizedContent = normalizeTraceText(cleanedContent);
  let matchIndex = -1;
  if (needle) matchIndex = cleanedContent.indexOf(needle);
  if (matchIndex < 0 && normalizedNeedle) {
    const normalizedIndex = normalizedContent.indexOf(normalizedNeedle);
    if (normalizedIndex >= 0) {
      const probe = needle.slice(0, Math.min(4, needle.length));
      matchIndex = probe ? cleanedContent.indexOf(probe) : -1;
    }
  }
  if (matchIndex < 0 && cleanedBlock) matchIndex = cleanedContent.indexOf(cleanedBlock);
  if (matchIndex < 0) return trimReadableEvidenceAroundNeedle(cleanedBlock || cleanTraceEvidenceText(fieldValue), needle, maxLength);

  const needleLength = Math.max(needle.length, 8);
  const start = nearestTraceBoundaryBefore(cleanedContent, matchIndex);
  const end = nearestTraceBoundaryAfter(cleanedContent, matchIndex + needleLength);
  let readable = cleanedContent.slice(start, end).trim();
  if (!readable || readable.length < Math.min(24, cleanedBlock.length)) readable = cleanedBlock;
  return trimReadableEvidenceAroundNeedle(readable, needle, maxLength);
}

function splitTraceBlocks(content) {
  const lines = String(content || '').replace(/\r/g, '').split('\n');
  const blocks = [];
  let current = [];
  const pushCurrent = () => {
    const text = current.join('\n').trim();
    if (text) blocks.push(text);
    current = [];
  };
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      pushCurrent();
      continue;
    }
    const startsList = /^[\s　]*(?:\d+[\u3001.．、]|[（(]?\d+[）)]|[-*•])\s*/.test(line);
    const startsHeading = /^#{1,6}\s+/.test(line) || /^第[一二三四五六七八九十百]+[章节条]/.test(line);
    if ((startsList || startsHeading) && current.length) pushCurrent();
    if (line.length > 240) {
      pushCurrent();
      const sentences = line
        .split(/(?<=[。！？；;])\s*/)
        .map(item => item.trim())
        .filter(Boolean);
      if (sentences.length > 1) {
        sentences.forEach(sentence => blocks.push(sentence));
        continue;
      }
    }
    current.push(line);
    if (line.length > 240) pushCurrent();
  }
  pushCurrent();
  return blocks.length ? blocks : [String(content || '').trim()].filter(Boolean);
}

function traceKeywords(value) {
  const normalized = String(value || '')
    .replace(/[，。；;：:、,.!！?？（）()【】\[\]《》“”"']/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  const tokens = normalized.match(/[\u4e00-\u9fa5]{2,}|[A-Za-z0-9][A-Za-z0-9./%-]*/g) || [];
  return [...new Set(tokens)]
    .filter(token => token.length >= 2 && !/^\d+$/.test(token))
    .sort((a, b) => b.length - a.length)
    .slice(0, 12);
}

function findTraceEvidence(fieldValue, content) {
  const value = String(fieldValue || '').trim();
  if (!value) return null;
  const blocks = splitTraceBlocks(content);
  const normalizedValue = normalizeTraceText(value);
  if (!normalizedValue) return null;
  let best = null;
  const remember = (block, method, score) => {
    if (!block || !String(block).trim()) return;
    if (!best || score > best.score) {
      best = {
        evidence: buildReadableTraceEvidence(content, block, value, { maxLength: 240 }),
        evidenceContext: buildReadableTraceEvidence(content, block, value, { maxLength: 700 }),
        matchMethod: method,
        confidence: Math.min(0.99, score),
        score
      };
    }
  };
  for (const block of blocks) {
    const normalizedBlock = normalizeTraceText(block);
    if (!normalizedBlock) continue;
    if (normalizedBlock.includes(normalizedValue)) remember(block, 'exact', 0.98);
    else if (normalizedValue.includes(normalizedBlock) && normalizedBlock.length >= 8) remember(block, 'line_match', 0.9);
  }
  if (best) return best;

  const keywords = traceKeywords(value);
  if (!keywords.length) return null;
  for (const block of blocks) {
    const normalizedBlock = normalizeTraceText(block);
    let hits = 0;
    let weighted = 0;
    for (const keyword of keywords) {
      const normalizedKeyword = normalizeTraceText(keyword);
      if (normalizedKeyword && normalizedBlock.includes(normalizedKeyword)) {
        hits += 1;
        weighted += Math.min(8, normalizedKeyword.length);
      }
    }
    if (hits >= Math.min(2, keywords.length)) {
      remember(block, 'keyword', 0.45 + Math.min(0.4, weighted / Math.max(24, normalizedValue.length)));
    }
  }
  return best && best.confidence >= 0.55 ? best : null;
}

function traceFieldText(field = {}) {
  return [
    field.fieldCode,
    field.fieldName,
    field.fieldPath,
    field.recordType
  ].map(item => String(item || '')).join(' ');
}

function normalizeTraceNumberText(value) {
  return String(value || '')
    .replace(/[０-９]/g, ch => String.fromCharCode(ch.charCodeAt(0) - 0xFEE0))
    .replace(/，/g, ',')
    .trim();
}

function traceNumericValue(value) {
  const text = normalizeTraceNumberText(value)
    .replace(/,/g, '')
    .replace(/\s+/g, '');
  return /^[-+]?\d+(?:\.\d+)?$/.test(text) ? text : '';
}

function isPureNumericTraceValue(value = '') {
  return !!traceNumericValue(value);
}

function traceMeaningfulFieldKeywords(field = {}) {
  const text = traceFieldText(field);
  const rawTokens = text.match(/[\u4e00-\u9fa5]{2,}|[A-Za-z][A-Za-z0-9_]{2,}/g) || [];
  const stopWords = new Set(['数量', '取值', '类型', '字段', '条件', '要求', '信息', '规模', '强度', '数值', 'value']);
  const tokens = new Set();
  for (const raw of rawTokens) {
    const token = String(raw || '').trim();
    if (!token || stopWords.has(token)) continue;
    tokens.add(token);
    const stripped = token.replace(/(数量|取值|类型|字段|条件|要求|信息|规模|强度|数值|Value)$/i, '');
    if (stripped.length >= 2 && !stopWords.has(stripped)) tokens.add(stripped);
    if (/[\u4e00-\u9fa5]/.test(token) && token.length > 2) {
      for (let i = 0; i < token.length - 1; i += 1) {
        const piece = token.slice(i, i + 2);
        if (!stopWords.has(piece)) tokens.add(piece);
      }
    }
  }
  return [...tokens].filter(token => token.length >= 2).slice(0, 16);
}

function isNumericTraceField(field = {}, value = '') {
  return isPureNumericTraceValue(value);
}

function hasQuantityContext(block = '', field = {}) {
  const text = normalizeTraceNumberText(block);
  const fieldText = traceFieldText(field);
  if (/万元|亿元|千元|百万元|人民币|资金|经费|金额|奖励|补助|补贴|资助|投入|费用|收入|营收|营业|销售额|资产|纳税|税收|比例|%|％|不低于|不少于|以上|以下|超过|达到|最高|年度/i.test(text)) {
    return true;
  }
  if (/revenue|income|amount|maxAmount|scaleRevenue|scaleAsset|scaleTax|营业收入|收入|金额/i.test(fieldText)) {
    return /收入|营收|营业|销售|万元|亿元|金额|不少于|不低于|以上|达到/i.test(text);
  }
  return false;
}

function isDateOnlyTraceBlock(block = '') {
  const text = normalizeTraceNumberText(block);
  return /(?:发布日期|发布时间|印发日期|发文日期|申报日期|截止日期|时间|日期)?\s*[:：-]?\s*\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?/.test(text)
    && !/万元|亿元|资金|经费|金额|奖励|补助|收入|营业|投入|资助|%|％/.test(text);
}

function hasNumericTraceEvidenceContext(block = '', field = {}) {
  if (isDateOnlyTraceBlock(block)) return false;
  const text = normalizeTraceNumberText(block);
  if (/https?:\/\/|www\.|原文链接|政策文号|文号[:：]/i.test(text) && !hasQuantityContext(text, field)) {
    return false;
  }
  if (hasQuantityContext(text, field)) return true;
  const keywords = traceMeaningfulFieldKeywords(field);
  if (!keywords.length) return false;
  const normalizedBlock = normalizeTraceText(text);
  const hasFieldKeyword = keywords.some(keyword => normalizeTraceText(keyword).length >= 2 && normalizedBlock.includes(normalizeTraceText(keyword)));
  const hasUnitContext = /个|家|名|项|人|所|年|倍|%|％|以上|以下|不少于|不低于|超过|达到|至少|最高|最低/.test(text);
  return hasFieldKeyword && hasUnitContext;
}

function findNumericTraceEvidence(field, fieldValue, content) {
  const number = traceNumericValue(fieldValue);
  if (!number) return null;
  const numberPattern = number.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const regex = new RegExp(`(^|[^0-9.])${numberPattern}([^0-9.]|$)`);
  let best = null;
  for (const block of splitTraceBlocks(content)) {
    const text = normalizeTraceNumberText(block);
    if (!regex.test(text)) continue;
    if (isDateOnlyTraceBlock(text)) continue;
    if (!hasNumericTraceEvidenceContext(text, field)) continue;
    let score = 0.72;
    if (/万元|亿元|人民币|金额|资金|经费/.test(text)) score += 0.12;
    if (/收入|营收|营业|销售/.test(text) && /revenue|income|scaleRevenue|营业收入|收入/i.test(traceFieldText(field))) score += 0.12;
    if (/奖励|补助|补贴|资助|最高/.test(text) && /amount|maxAmount|subsidy|reward|bonus|award|金额|奖励|补助/i.test(traceFieldText(field))) score += 0.1;
    if (!best || score > best.score) {
      best = {
        evidence: buildReadableTraceEvidence(content, block, fieldValue, { maxLength: 240 }),
        evidenceContext: buildReadableTraceEvidence(content, block, fieldValue, { maxLength: 700 }),
        matchMethod: 'numeric_context',
        confidence: Math.min(0.96, score),
        score
      };
    }
  }
  return best;
}

function isLongTraceField(field = {}, value = '') {
  const text = String(value || '').trim();
  if (text.length < 40 && !/\n/.test(text)) return false;
  return /applicationRequirement|applicationInstruction|contentDesc|materials|method|summary|申报要求|申报说明|扶持内容|申报材料|申报方式|摘要/i
    .test(traceFieldText(field));
}

function splitTraceFieldItems(value) {
  const raw = String(value || '').replace(/\r/g, '\n');
  const lines = raw
    .split(/\n+/)
    .flatMap(line => String(line || '').split(/(?<=[。；;])\s*/))
    .map(line => line
      .replace(/^[\s\u3000]*(?:\d+[\u3001、.．]|\(\d+\)|\uff08\d+\uff09|[-*•])\s*/, '')
      .trim())
    .filter(line => line.length >= 6);
  return [...new Set(lines)].slice(0, 12);
}

function splitNumberedTraceItems(value) {
  const text = String(value || '').replace(/\r/g, '\n').trim();
  if (!text) return [];
  const regex = /(\d+)[\u3001、]\s*/g;
  const matches = [];
  let match;
  while ((match = regex.exec(text)) !== null) {
    matches.push({
      no: match[1],
      markerStart: match.index,
      contentStart: regex.lastIndex
    });
  }
  if (!matches.length) return [];
  return matches.map((item, index) => {
    const next = matches[index + 1];
    const rawItem = text.slice(item.contentStart, next ? next.markerStart : text.length).trim();
    return {
      itemIndex: Number(item.no) || index + 1,
      displayNo: item.no,
      text: rawItem.replace(/\n+/g, ' ').trim()
    };
  }).filter(item => item.text.length >= 6);
}

function isApplicationRequirementField(field = {}) {
  return /applicationRequirement|\u7533\u62a5\u8981\u6c42/i.test(traceFieldText(field));
}

function splitNumberedTraceItems(value) {
  const text = String(value || '').replace(/\r/g, '\n').trim();
  if (!text) return [];
  const regex = /(\d+)[\u3001\uFF0E.]\s*/g;
  const matches = [];
  let match;
  while ((match = regex.exec(text)) !== null) {
    matches.push({
      no: match[1],
      markerStart: match.index,
      contentStart: regex.lastIndex
    });
  }
  if (!matches.length) return [];
  return matches.map((item, index) => {
    const next = matches[index + 1];
    const rawItem = text.slice(item.contentStart, next ? next.markerStart : text.length).trim();
    return {
      itemIndex: Number(item.no) || index + 1,
      displayNo: item.no,
      text: rawItem.replace(/\n+/g, ' ').trim()
    };
  }).filter(item => item.text.length >= 6);
}

function isItemizedTraceField(field = {}) {
  const code = String(field?.fieldCode || field?.fieldPath || '');
  const text = traceFieldText(field);
  if (/policyInfo\.summary/i.test(code)) return false;
  return /applicationRequirement|supportMethods\.[^.]+\.contentDesc|supportMethods\.[^.]+\.amount|materials\.[^.]+\.(?:materials|method)|\u7533\u62a5\u8981\u6c42|\u6276\u6301\u5185\u5bb9|\u6276\u6301\u91d1\u989d|\u652f\u6301\u91d1\u989d|\u7533\u62a5\u6750\u6599|\u7533\u62a5\u65b9\u5f0f/i.test(text);
}

function traceItemPhraseVariants(value) {
  const text = String(value || '').trim();
  const parts = text
    .split(/[\u3001\uff0c,;\uff1b\u3002()\uFF08\uFF09\s]+/)
    .map(item => item.trim())
    .filter(item => item.length >= 2);
  const variants = new Set([text, ...parts]);
  for (const part of parts) {
    String(part || '')
      .split(/\u53ca|\u548c|\u4e0e|\u6216/)
      .map(item => item.trim())
      .filter(item => item.length >= 2)
      .forEach(item => variants.add(item));
    const stripped = part
      .replace(/(\u53ca)?(\u5176\u4ed6)?(\u76f8\u5173|\u6709\u5173)?(\u8bc1\u660e\u6750\u6599|\u8bc1\u660e\u6587\u4ef6|\u8bc1\u660e|\u6750\u6599|\u6587\u4ef6|\u8bc1\u4e66)$/g, '')
      .replace(/(\u53ca)?(\u5176\u4ed6)?(\u76f8\u5173|\u6709\u5173)$/g, '')
      .trim();
    if (stripped.length >= 2) variants.add(stripped);
  }
  return [...variants].filter(item => item.length >= 2);
}

function normalizeTracePhraseText(value = '') {
  return normalizeTraceText(value)
    .replace(/\u5176\u4ed6/g, '')
    .replace(/\u76f8\u5173/g, '\u6709\u5173')
    .replace(/\u8bc1\u660e\u6750\u6599|\u8bc1\u660e\u6587\u4ef6|\u8bc1\u660e|\u6750\u6599|\u6587\u4ef6|\u8bc1\u4e66/g, '');
}

function findTraceEvidenceByPhrases(fieldValue, content) {
  const phrases = traceItemPhraseVariants(fieldValue)
    .flatMap(item => [normalizeTraceText(item), normalizeTracePhraseText(item)])
    .filter(item => item.length >= 2);
  const uniquePhrases = [...new Set(phrases)];
  if (!uniquePhrases.length) return null;
  let best = null;
  const remember = (block, score, hits) => {
    if (!best || score > best.score) {
      best = {
        evidence: buildReadableTraceEvidence(content, block, fieldValue, { maxLength: 240 }),
        evidenceContext: buildReadableTraceEvidence(content, block, fieldValue, { maxLength: 700 }),
        matchMethod: 'itemized_fuzzy',
        confidence: Math.min(0.9, 0.52 + Math.min(0.36, score / 18)),
        score,
        hits
      };
    }
  };
  for (const block of splitTraceBlocks(content)) {
    const normalizedBlock = normalizeTraceText(block);
    const synonymBlock = normalizeTracePhraseText(block);
    if (!normalizedBlock && !synonymBlock) continue;
    let hits = 0;
    let score = 0;
    for (const phrase of uniquePhrases) {
      if (!phrase || (!normalizedBlock.includes(phrase) && !synonymBlock.includes(phrase))) continue;
      hits += 1;
      score += Math.min(8, phrase.length);
    }
    if (hits >= 2 || score >= 5) remember(block, score, hits);
  }
  return best;
}

function itemizedEvidenceHitCount(items = [], evidence = '') {
  const normalizedEvidence = normalizeTracePhraseText(evidence);
  if (!normalizedEvidence) return 0;
  let hits = 0;
  for (const item of items) {
    const phrases = traceItemPhraseVariants(item)
      .map(phrase => normalizeTracePhraseText(phrase))
      .filter(phrase => phrase.length >= 3);
    if (phrases.some(phrase => normalizedEvidence.includes(phrase))) hits += 1;
  }
  return hits;
}

function findItemizedTraceEvidence(field, fieldValue, content) {
  const items = splitTraceFieldItems(fieldValue);
  if (!items.length) return null;
  const snippets = [];
  let scoreTotal = 0;
  for (const item of items) {
    let evidence = findTraceEvidence(item, content);
    if (!evidence || isBroadTraceEvidence(field, evidence.evidence)) {
      evidence = findTraceEvidenceByPhrases(item, content);
    }
    if (!evidence || isBroadTraceEvidence(field, evidence.evidence)) continue;
    if (!snippets.includes(evidence.evidence)) snippets.push(evidence.evidence);
    scoreTotal += Number(evidence.confidence || 0.65);
    if (snippets.length >= 8) break;
  }
  if (!snippets.length) return null;
  const joinedEvidence = snippets.join('\n');
  const hitCount = itemizedEvidenceHitCount(items, joinedEvidence);
  if (items.length >= 3 && snippets.length < 2 && hitCount < 2 && joinedEvidence.length < 180) return null;
  const coverage = snippets.length / Math.max(1, items.length);
  return {
    evidence: compactTraceEvidence(joinedEvidence, isSummaryTraceField(field) ? 2200 : 1400),
    evidenceContext: compactTraceEvidence(joinedEvidence, isSummaryTraceField(field) ? 2400 : 1600),
    matchMethod: 'itemized',
    confidence: Math.min(0.94, Math.max(0.62, scoreTotal / snippets.length) * (0.75 + Math.min(0.25, coverage))),
    score: 0.62 + Math.min(0.3, coverage)
  };
}

function isApplicationMaterialValueField(field = {}) {
  return /(?:^|\.)materials(?:$|\.)|\u7533\u62a5\u6750\u6599/i.test(traceFieldText(field));
}

function findMaterialTraceEvidence(fieldValue, content) {
  const phrases = traceItemPhraseVariants(fieldValue)
    .flatMap(item => [normalizeTraceText(item), normalizeTracePhraseText(item)])
    .filter(item => item.length >= 3);
  const uniquePhrases = [...new Set(phrases)];
  if (!uniquePhrases.length) return null;
  let best = null;
  for (const block of splitTraceBlocks(content)) {
    const normalizedBlock = normalizeTraceText(block);
    const synonymBlock = normalizeTracePhraseText(block);
    if (!normalizedBlock && !synonymBlock) continue;
    let hits = 0;
    let score = 0;
    for (const phrase of uniquePhrases) {
      if (!phrase || (!normalizedBlock.includes(phrase) && !synonymBlock.includes(phrase))) continue;
      hits += 1;
      score += Math.min(10, phrase.length);
    }
    const hasMaterialCue = /\u7533\u8bf7\u8868|\u534f\u8bae|\u6750\u6599|\u8bc1\u660e|\u63d0\u4ea4|\u586b\u5199|\u62a5\u9001/.test(block);
    if (!hasMaterialCue || hits < 1 || score < 8) continue;
    if (!best || score > best.score) {
      best = {
        evidence: buildReadableTraceEvidence(content, block, fieldValue, { maxLength: 240 }),
        evidenceContext: buildReadableTraceEvidence(content, block, fieldValue, { maxLength: 900 }),
        matchMethod: 'material_fuzzy',
        confidence: Math.min(0.9, 0.58 + Math.min(0.3, score / 30)),
        score
      };
    }
  }
  return best;
}

function findTraceEvidenceForField(field = {}, fieldValue, content) {
  if (isNumericTraceField(field, fieldValue)) {
    return findNumericTraceEvidence(field, fieldValue, content);
  }
  if (isApplicationMaterialValueField(field)) {
    return findMaterialTraceEvidence(fieldValue, content);
  }
  if (isLongTraceField(field, fieldValue)) {
    const itemized = findItemizedTraceEvidence(field, fieldValue, content);
    if (itemized) return itemized;
  }
  return findTraceEvidence(fieldValue, content);
}

function findTraceEvidenceForNumberedItem(field = {}, itemText, content) {
  let evidence = findTraceEvidence(itemText, content);
  if (!evidence || isBroadTraceEvidence(field, evidence.evidence)) {
    evidence = findTraceEvidenceByPhrases(itemText, content);
  }
  if (!evidence || isBroadTraceEvidence(field, evidence.evidence)) return null;
  return evidence;
}

function isApplicableScopeTraceField(field = {}) {
  const code = String(field.fieldCode || field.fieldPath || '');
  const name = String(field.fieldName || '');
  return /policyInfo\.applicableScope/i.test(code) || /适用范围|閫傜敤鑼冨洿/.test(name);
}

function findApplicableScopeTraceEvidence(fieldValue, content) {
  const value = String(fieldValue || '').trim();
  const normalizedValue = normalizeTraceText(value);
  if (!value || !normalizedValue) return null;
  const blocks = splitTraceBlocks(content)
    .map(block => String(block || '').trim())
    .filter(Boolean);
  const preferred = blocks.find(block => {
    const normalizedBlock = normalizeTraceText(block);
    return normalizedBlock.includes(normalizedValue) && /适用范围|适用区域|范围|厦门市|本市|閫傜敤|鑼冨洿/.test(block);
  });
  const fallback = preferred || blocks.find(block => normalizeTraceText(block).includes(normalizedValue));
  if (!fallback) return null;
  return {
    evidence: buildReadableTraceEvidence(content, fallback, value, { maxLength: 240 }),
    evidenceContext: buildReadableTraceEvidence(content, fallback, value, { maxLength: 700 }),
    matchMethod: 'scope_exact',
    confidence: 0.92,
    score: 0.92
  };
}

function isSummaryTraceField(field = {}) {
  const code = String(field.fieldCode || field.fieldPath || '');
  const name = String(field.fieldName || '');
  return /policyInfo\.summary/i.test(code) || /摘要|鎽樿/.test(name);
}

function isBroadTraceEvidence(field = {}, evidence = '') {
  const text = String(evidence || '').trim();
  if (!text) return false;
  if (String(field?.fieldCode || '').includes('policyInfo.summary')) return text.length > 2400;
  return text.length > 900 || /^#\s*政策正文/m.test(text) || /原文摘录：\s*#\s*政策正文/.test(text);
}

function isTruncatedTraceEvidence(evidence = '') {
  const text = cleanTraceEvidenceText(evidence);
  if (text.length < 20 || text.length > 260) return false;
  if (/[。！？；;.)）]$/.test(text)) return false;
  if (/[，、：:（(]$/.test(text)) return true;
  return /[\u4e00-\u9fa5]$/.test(text);
}

function isStaleTraceCacheRow(row, field = {}) {
  const method = String(row?.matchMethod || '');
  const evidence = String(row?.evidence || '');
  const finalValue = row?.finalValue || row?.value || field?.value || '';
  if (/itemized_cross_doc/i.test(method)) return true;
  if (/原文摘录：\s*#\s*政策正文|原文摘录:\s*#\s*政策正文/.test(evidence)) return true;
  if (isTruncatedTraceEvidence(evidence)) return true;
  if (isPureNumericTraceValue(finalValue) && !hasNumericTraceEvidenceContext(evidence, field)) {
    return true;
  }
  if (method.startsWith('itemized') && isLongTraceField(field, finalValue)) {
    const items = splitTraceFieldItems(finalValue);
    const hitCount = itemizedEvidenceHitCount(items, evidence);
    if (items.length >= 3 && hitCount < 2 && evidence.length < 180) return true;
  }
  if (isLongTraceField(field, finalValue) && !method.startsWith('itemized')) return true;
  return !method.startsWith('itemized') && !isSummaryTraceField(field) && isBroadTraceEvidence(field, evidence);
}

function traceValueHash(value) {
  return crypto.createHash('sha256').update(String(value || ''), 'utf8').digest('hex');
}

function traceFieldCacheKeys(fieldCode) {
  const code = String(fieldCode || '').trim();
  if (!code) return [];
  const baseCode = traceBaseFieldCode(code);
  return [...new Set([code, baseCode].filter(Boolean))];
}

function traceBaseFieldCode(fieldCode) {
  const code = String(fieldCode || '').trim();
  return code
    .replace(/^([^.]+)\.\d+(\..+)$/, '$1$2')
    .replace(/\.\d+$/, '');
}

function addTraceCacheRow(map, key, row) {
  if (!key) return;
  if (!map.has(key)) map.set(key, []);
  map.get(key).push(row);
}

function normalizeTraceCacheItem(item, field, traceNo) {
  return {
    id: traceNo,
    cacheId: item.id || item.cacheId || '',
    fusionPolicyId: item.fusionPolicyId || '',
    fieldCode: item.fieldCode || field.fieldCode || '',
    fieldName: item.fieldName || field.fieldName || '',
    fieldPath: item.fieldPath || field.fieldPath || field.fieldCode || '',
    recordType: item.recordType || field.recordType || '',
    recordId: item.recordId || field.recordId || '',
    finalValue: item.finalValue || field.value || '',
    valueHash: item.valueHash || traceValueHash(field.value || item.finalValue || ''),
    sourcePolicyId: item.sourcePolicyId || '',
    sourcePolicy: item.sourcePolicy || item.sourceFile || '-',
    sourcePolicyAttribute: item.sourcePolicyAttribute || '',
    sourceType: item.sourceType || '原文直接提取',
    sourceFilePath: item.sourceFilePath || '',
    technicalPath: item.technicalPath || 'POLICY_ORIGINAL_V2.FILE_PATH / POLICY_ATTACHMENT.FILE_PATH',
    evidence: item.evidence || '',
    description: item.description || '',
    validationStatus: item.validationStatus || (item.evidence ? '已验证' : '缺少依据'),
    confirmStatus: item.confirmStatus || (item.evidence ? '自动匹配' : '缺少依据'),
    matchMethod: item.matchMethod || 'cache',
    confidence: item.confidence || ''
  };
}

function createTraceCacheIndex() {
  return { exact: new Map(), byField: new Map(), byBaseField: new Map() };
}

function isRecentTraceCacheRow(row) {
  const updatedAt = String(row?.updatedAt || '').trim();
  return !!updatedAt && updatedAt >= TRACE_IMPORT_PRIORITY_CUTOFF;
}

function indexTraceCacheRow(index, row) {
  const fieldCode = String(row.fieldCode || '').trim();
  const valueHash = String(row.valueHash || '').trim();
  if (!fieldCode) return;
  for (const cacheFieldCode of traceFieldCacheKeys(fieldCode)) {
    const key = `${cacheFieldCode}|${valueHash}`;
    if (valueHash && !index.exact.has(key)) index.exact.set(key, row);
    addTraceCacheRow(index.byField, cacheFieldCode, row);
    addTraceCacheRow(index.byBaseField, traceBaseFieldCode(cacheFieldCode), row);
  }
}

async function loadTraceCache(fusionPolicyId) {
  if (!fusionPolicyId) return { recent: createTraceCacheIndex(), legacy: createTraceCacheIndex() };
  try {
    const result = await fetchFromJDBC(`/api/fusion-trace/cache?fusionPolicyId=${encodeURIComponent(fusionPolicyId)}`);
    const rows = Array.isArray(result?.data) ? result.data : [];
    const recent = createTraceCacheIndex();
    const legacy = createTraceCacheIndex();
    for (const row of rows) {
      indexTraceCacheRow(isRecentTraceCacheRow(row) ? recent : legacy, row);
    }
    return { recent, legacy };
  } catch (err) {
    writeError('[Fusion Trace] load cache failed:', err.message);
    return { recent: createTraceCacheIndex(), legacy: createTraceCacheIndex() };
  }
}

function isCurrentTraceCacheRow(row, value, valueHash) {
  if (!row) return false;
  const cachedHash = String(row.valueHash || '').trim();
  if (cachedHash) return cachedHash === valueHash;
  const cachedValue = String(row.finalValue || row.value || '').trim();
  if (!cachedValue) return false;
  if (traceValueHash(cachedValue) === valueHash) return true;
  return normalizeTraceText(cachedValue) === normalizeTraceText(value);
}

function currentTraceCacheRows(rows, value, valueHash) {
  return (Array.isArray(rows) ? rows : []).filter(row => isCurrentTraceCacheRow(row, value, valueHash));
}

function pickTraceCacheRowsFromIndex(cache, fieldCode, valueHash, value = '') {
  for (const cacheFieldCode of traceFieldCacheKeys(fieldCode)) {
    const exact = cache.exact.get(`${cacheFieldCode}|${valueHash}`);
    if (exact) return [exact];
  }
  for (const cacheFieldCode of traceFieldCacheKeys(fieldCode)) {
    const rows = cache.byField.get(cacheFieldCode);
    const currentRows = currentTraceCacheRows(rows, value, valueHash);
    if (currentRows.length) return currentRows;
  }
  for (const cacheFieldCode of traceFieldCacheKeys(fieldCode)) {
    const rows = cache.byBaseField.get(traceBaseFieldCode(cacheFieldCode));
    const currentRows = currentTraceCacheRows(rows, value, valueHash);
    if (currentRows.length) return currentRows;
  }
  return [];
}

function pickTraceCacheRows(cache, fieldCode, valueHash, value = '') {
  const recentRows = pickTraceCacheRowsFromIndex(cache.recent || createTraceCacheIndex(), fieldCode, valueHash, value);
  if (recentRows.length) return { rows: recentRows, source: 'recent' };
  const legacyRows = pickTraceCacheRowsFromIndex(cache.legacy || createTraceCacheIndex(), fieldCode, valueHash, value);
  if (legacyRows.length) return { rows: legacyRows, source: 'legacy' };
  return { rows: [], source: '' };
}

async function saveTraceCache(item) {
  try {
    return await fetchFromJDBC('/api/fusion-trace/save', 'POST', item);
  } catch (err) {
    writeError('[Fusion Trace] save cache failed:', err.message);
    return null;
  }
}

async function loadX1FusionDocumentContent(doc) {
  const id = String(doc?.id || '').trim();
  if (!id) return null;
  const result = await fetchFromJDBC(`/api/fusion-classification/documents/content?id=${encodeURIComponent(id)}`);
  const data = result?.data || {};
  const content = String(data.content || '');
  if (!result?.success || !content.trim()) return null;
  return {
    id,
    sourcePolicyId: String(doc.sourcePolicyId || ''),
    policyTitle: String(doc.policyTitle || data.policyTitle || data.title || doc.fileName || ''),
    fileName: String(doc.fileName || data.fileName || ''),
    policyAttribute: String(doc.policyAttribute || ''),
    filePath: String(doc.filePath || data.filePath || ''),
    technicalPath: 'X1_POLICY_DOCUMENT.FILE_PATH',
    content
  };
}

async function loadPolicyOriginalContent(sourcePolicyId, sourceDoc) {
  const result = await fetchFromJDBC(`/api/policy-original/content?id=${encodeURIComponent(sourcePolicyId)}`);
  const data = result?.data || {};
  const content = String(data.content || '');
  if (!result?.success || !content.trim() || content.includes('读取文件失败') || content.includes('无文件路径')) return null;
  return {
    id: sourcePolicyId,
    sourcePolicyId,
    policyTitle: String(data.policyTitle || sourceDoc?.policyTitle || sourceDoc?.fileName || ''),
    fileName: String(data.fileName || sourceDoc?.fileName || ''),
    policyAttribute: String(sourceDoc?.policyAttribute || ''),
    filePath: String(data.filePath || sourceDoc?.filePath || ''),
    fileType: String(data.fileType || ''),
    technicalPath: 'POLICY_ORIGINAL_V2.FILE_PATH',
    content
  };
}

async function loadPolicyAttachmentContents(sourcePolicyId, sourceDoc) {
  const listResult = await fetchFromJDBC(`/api/attachment/list?policyId=${encodeURIComponent(sourcePolicyId)}`);
  const attachments = Array.isArray(listResult?.data) ? listResult.data : [];
  const contents = [];
  for (const attachment of attachments) {
    const attachmentId = String(attachment?.id || attachment?.attachmentId || '').trim();
    if (!attachmentId) continue;
    try {
      const result = await fetchFromJDBC(`/api/attachment/content?id=${encodeURIComponent(attachmentId)}`);
      const data = result?.data || {};
      const content = String(data.content || '');
      if (!result?.success || !content.trim() || content.includes('读取文件失败') || content.includes('无文件路径')) continue;
      contents.push({
        id: attachmentId,
        sourcePolicyId,
        policyTitle: String(sourceDoc?.policyTitle || ''),
        fileName: String(data.attachmentShowName || data.attachmentName || attachment.attachmentShowName || attachment.attachmentName || ''),
        policyAttribute: String(sourceDoc?.policyAttribute || ''),
        filePath: String(data.filePath || attachment.filePath || ''),
        fileType: String(data.fileSuffix || attachment.fileSuffix || ''),
        technicalPath: 'POLICY_ATTACHMENT.FILE_PATH',
        content
      });
    } catch (err) {
      writeError('[Fusion Trace] policy attachment load failed:', attachmentId, err.message);
    }
  }
  return contents;
}

async function loadFusionDocumentContents(documents, fallbackSourcePolicyId = '') {
  const rows = Array.isArray(documents) ? documents : [];
  const contents = [];
  const seen = new Set();

  const addContent = item => {
    if (!item || !String(item.content || '').trim()) return;
    const key = `${item.technicalPath || ''}|${item.id || ''}|${item.sourcePolicyId || ''}|${item.filePath || ''}`;
    if (seen.has(key)) return;
    seen.add(key);
    contents.push(item);
  };

  for (const doc of rows) {
    try {
      addContent(await loadX1FusionDocumentContent(doc));
    } catch (err) {
      writeError('[Fusion Trace] X1 fusion document load failed:', String(doc?.id || ''), err.message);
    }
  }

  const sourceDocs = new Map();
  for (const doc of rows) {
    const sourcePolicyId = String(doc?.sourcePolicyId || '').trim();
    if (sourcePolicyId && !sourceDocs.has(sourcePolicyId)) sourceDocs.set(sourcePolicyId, doc);
  }
  const fallbackId = String(fallbackSourcePolicyId || '').trim();
  if (fallbackId && !sourceDocs.has(fallbackId)) sourceDocs.set(fallbackId, rows[0] || {});

  for (const [sourcePolicyId, sourceDoc] of sourceDocs.entries()) {
    try {
      const originalContent = await loadPolicyOriginalContent(sourcePolicyId, sourceDoc);
      addContent(originalContent);
    } catch (err) {
      writeError('[Fusion Trace] policy original load failed:', sourcePolicyId, err.message);
    }

    try {
      for (const attachmentContent of await loadPolicyAttachmentContents(sourcePolicyId, sourceDoc)) {
        addContent(attachmentContent);
      }
    } catch (err) {
      writeError('[Fusion Trace] policy attachment list failed:', sourcePolicyId, err.message);
    }
  }

  return contents;
}

function traceDocumentTitleScore(doc, traceTitle = '') {
  if (!traceTitle) return 0;
  const title = normalizeTraceText(doc.policyTitle || doc.fileName || '');
  const contentHead = normalizeTraceText(String(doc.content || '').slice(0, 800));
  let score = 0;
  if (title && (title.includes(traceTitle) || traceTitle.includes(title))) score += 5;
  if (contentHead.includes(traceTitle) || traceTitle.includes(contentHead.slice(0, Math.min(40, contentHead.length)))) score += 2;
  return score;
}

function traceCandidateScore(evidence, doc, traceTitle = '') {
  const confidence = Number(evidence?.confidence || evidence?.score || 0);
  const method = String(evidence?.matchMethod || '');
  let score = confidence * 100;
  if (/exact|line_match/.test(method)) score += 18;
  if (/keyword|fuzzy/.test(method)) score += 8;
  score += traceDocumentTitleScore(doc, traceTitle);
  const attribute = String(doc?.policyAttribute || '');
  if (/实施细则|申报通知|指导政策|管理办法|工作指引/.test(attribute)) score += 4;
  if (/结果公告/.test(attribute)) score -= 3;
  return score;
}

function traceItemCoverageScore(itemText = '', evidenceText = '') {
  const evidence = normalizeTraceText(evidenceText);
  if (!evidence) return -40;
  const keywords = traceKeywords(itemText)
    .map(keyword => normalizeTraceText(keyword))
    .filter(keyword => keyword.length >= 2);
  const uniqueKeywords = [...new Set(keywords)].slice(0, 16);
  let keywordHits = 0;
  let keywordScore = 0;
  for (const keyword of uniqueKeywords) {
    if (!evidence.includes(keyword)) continue;
    keywordHits += 1;
    keywordScore += Math.min(8, keyword.length);
  }
  const numbers = [...new Set(String(itemText || '').match(/\d+(?:\.\d+)?%?|\d+(?:\.\d+)?\s*(?:万元|亿元|个|家|项|人)/g) || [])]
    .map(item => normalizeTraceNumberText(item).replace(/\s+/g, ''));
  let numberHits = 0;
  const numberEvidence = normalizeTraceNumberText(evidenceText).replace(/\s+/g, '');
  for (const number of numbers) {
    if (number && numberEvidence.includes(number)) numberHits += 1;
  }
  const keywordRatio = uniqueKeywords.length ? keywordHits / uniqueKeywords.length : 0;
  const numberRatio = numbers.length ? numberHits / numbers.length : 1;
  let score = keywordRatio * 70 + Math.min(40, keywordScore);
  if (numbers.length) score += numberRatio * 45;
  if (uniqueKeywords.length >= 4 && keywordHits < 2) score -= 50;
  if (numbers.length && numberHits < Math.min(2, numbers.length)) score -= 35;
  return score;
}

function buildFusionTraceItem({ field, value, valueHash, fusionPolicyId, matched, fieldCode, traceNo }) {
  const itemIndex = matched.item?.itemIndex || null;
  const displayNo = matched.item?.displayNo || (itemIndex ? String(itemIndex) : '');
  const finalValue = matched.item?.text || value;
  const fieldName = String(field.fieldName || '');
  return {
    id: traceNo,
    fieldCode,
    fieldName: itemIndex ? `${fieldName} - 第${displayNo || itemIndex}点` : fieldName,
    fieldPath: String(field.fieldPath || fieldCode),
    recordType: String(field.recordType || ''),
    recordId: String(field.recordId || ''),
    finalValue,
    itemIndex,
    itemDisplayNo: displayNo,
    itemText: finalValue,
    itemKey: itemIndex ? `${fieldCode}#${itemIndex}` : '',
    fusionPolicyId,
    valueHash: itemIndex ? traceValueHash(finalValue) : valueHash,
    fieldValueHash: valueHash,
    sourcePolicyId: String(matched.doc.sourcePolicyId || ''),
    sourcePolicy: matched.doc.policyTitle || matched.doc.fileName || '-',
    sourcePolicyAttribute: matched.doc.policyAttribute || '',
    sourceType: '原文直接提取',
    evidence: matched.evidence.evidence,
    evidenceContext: matched.evidence.evidenceContext || '',
    description: itemIndex
      ? '根据当前序号点在全部融合来源文件中匹配得到。'
      : (matched.evidence.matchMethod === 'keyword' ? '根据当前字段关键词在关联原文中匹配得到。' : '根据当前字段值在关联原文中匹配得到。'),
    validationStatus: '已验证',
    technicalPath: matched.doc.technicalPath || 'POLICY_ORIGINAL_V2.FILE_PATH / POLICY_ATTACHMENT.FILE_PATH',
    sourceFilePath: matched.doc.filePath || '',
    sourceDocumentId: matched.doc.id,
    matchMethod: matched.evidence.matchMethod,
    confidence: matched.evidence.confidence
  };
}

function resolveApplicationRequirementItemTraces({ field, fieldCode, value, valueHash, fusionPolicyId, documentContents, traceTitle, traceNoStart }) {
  const items = splitNumberedTraceItems(value);
  if (!items.length) return null;
  const traces = [];
  let traceNo = traceNoStart;
  for (const item of items) {
    let best = null;
    for (const doc of documentContents) {
      const evidence = findTraceEvidenceForNumberedItem(field, item.text, doc.content);
      if (!evidence) continue;
      const score = traceCandidateScore(evidence, doc, traceTitle) + traceItemCoverageScore(item.text, evidence.evidence);
      if (!best || score > best.score) best = { item, doc, evidence, score };
    }
    if (!best) continue;
    traces.push(buildFusionTraceItem({ field, value, valueHash, fusionPolicyId, matched: best, fieldCode, traceNo: traceNo++ }));
  }
  return { traces, nextTraceNo: traceNo, totalItems: items.length };
}

async function resolveFusionTrace(body = {}) {
  const fields = Array.isArray(body.fields) ? body.fields : [];
  const documents = Array.isArray(body.documents) ? body.documents : [];
  const fusionPolicyId = String(body.fusionPolicyId || body.policyId || '').trim();
  const cache = await loadTraceCache(fusionPolicyId);
  const traceTitle = normalizeTraceText(body.directoryTitle || body.policyTitle || '');
  const documentContents = (await loadFusionDocumentContents(documents, body.sourcePolicyId)).sort((a, b) => {
    return traceDocumentTitleScore(b, traceTitle) - traceDocumentTitleScore(a, traceTitle);
  });
  const resultFields = {};
  let traceNo = 1;
  let covered = 0;

  for (const field of fields) {
    const fieldCode = String(field?.fieldCode || '').trim();
    const value = String(field?.value || '').trim();
    if (!fieldCode || !value) continue;
    if (/^policyInfo\.summary$/i.test(fieldCode)) {
      resultFields[fieldCode] = [];
      continue;
    }
    const valueHash = traceValueHash(value);
    const cacheMatch = pickTraceCacheRows(cache, fieldCode, valueHash, value);
    const cachedRows = cacheMatch.rows;
    const shouldRefreshStaleCache = cachedRows.some(row => isStaleTraceCacheRow(row, field));
    if (cachedRows.length && !shouldRefreshStaleCache) {
      const cacheTraces = cachedRows.map(row => normalizeTraceCacheItem(row, { ...field, value }, traceNo++));
      if (cacheTraces.some(trace => String(trace.evidence || '').trim())) covered += 1;
      resultFields[fieldCode] = cacheTraces;
      continue;
    }
    if (isItemizedTraceField(field)) {
      const itemTraceResult = resolveApplicationRequirementItemTraces({
        field,
        fieldCode,
        value,
        valueHash,
        fusionPolicyId,
        documentContents,
        traceTitle,
        traceNoStart: traceNo
      });
      if (itemTraceResult) {
        traceNo = itemTraceResult.nextTraceNo;
        resultFields[fieldCode] = itemTraceResult.traces;
        if (itemTraceResult.traces.length) covered += 1;
        continue;
      }
    }

    let matched = null;
    for (const doc of documentContents) {
      const evidence = isApplicableScopeTraceField(field)
        ? findApplicableScopeTraceEvidence(value, doc.content)
        : findTraceEvidenceForField(field, value, doc.content);
      if (evidence && evidence.matchMethod !== 'itemized' && !isSummaryTraceField(field) && isBroadTraceEvidence(field, evidence.evidence)) continue;
      if (evidence) {
        matched = { doc, evidence };
        break;
      }
    }

    if (matched) {
      covered += 1;
      resultFields[fieldCode] = [buildFusionTraceItem({ field, value, valueHash, fusionPolicyId, matched, fieldCode, traceNo: traceNo++ })];
      Object.assign(resultFields[fieldCode][0], { confirmStatus: '自动匹配' });
      if (ENABLE_DYNAMIC_TRACE_CACHE_SAVE) {
        saveTraceCache({ ...resultFields[fieldCode][0], id: undefined });
      }
    } else {
      resultFields[fieldCode] = [];
    }
  }

  const total = fields.filter(field => String(field?.fieldCode || '').trim() && String(field?.value || '').trim()).length;
  return {
    success: true,
    data: {
      coverage: {
        total,
        covered,
        status: !total ? '待补充' : (covered >= total ? '已完成' : (covered > 0 ? '部分完成' : '待补充'))
      },
      fields: resultFields
    }
  };
}

const ENTERPRISE_MATCH_RUN_ID = '20260730_093054';

function resolveEnterpriseMatchRunDir() {
  const outputRoot = path.resolve(__dirname, 'backend', 'PEMatching', 'output');
  const direct = path.join(outputRoot, '企业测试目录接口批跑', ENTERPRISE_MATCH_RUN_ID);
  if (fs.existsSync(direct)) return direct;
  const parent = fs.readdirSync(outputRoot, { withFileTypes: true })
    .filter(entry => entry.isDirectory())
    .map(entry => path.join(outputRoot, entry.name))
    .find(dir => fs.existsSync(path.join(dir, ENTERPRISE_MATCH_RUN_ID)));
  if (!parent) throw new Error('企业匹配批跑目录不存在');
  return path.join(parent, ENTERPRISE_MATCH_RUN_ID);
}

function loadEnterpriseMatchStore() {
  const runDir = resolveEnterpriseMatchRunDir();
  const entries = fs.readdirSync(runDir, { withFileTypes: true });
  const statusEntry = entries.find(entry => entry.isFile() && entry.name.endsWith('.json'));
  const responseDirEntry = entries.find(entry => entry.isDirectory());
  if (!statusEntry || !responseDirEntry) throw new Error('企业匹配批跑目录结构不完整');
  const statusPath = path.join(runDir, statusEntry.name);
  const responseDir = path.join(runDir, responseDirEntry.name);
  const status = JSON.parse(fs.readFileSync(statusPath, 'utf8'));
  return { runDir, responseDir, status };
}

function responseNameFromRecord(sourceKey, record) {
  const fromRecord = String(record?.responseJson || '').split(/[\\/]/).pop();
  if (fromRecord) return fromRecord;
  return String(sourceKey || '').replace(/\.json$/i, '_响应.json');
}

function buildEnterpriseMatchManifest() {
  const { responseDir, status } = loadEnterpriseMatchStore();
  const responseNames = new Set(fs.readdirSync(responseDir).filter(name => name.endsWith('.json')));
  const records = Object.entries(status.records || {})
    .filter(([, record]) => record?.status === 'completed')
    .sort(([left], [right]) => left.localeCompare(right, 'zh-Hans-CN', { numeric: true }));
  const enterprises = records.map(([sourceKey, record]) => {
    const responseName = responseNameFromRecord(sourceKey, record);
    if (!responseNames.has(responseName)) return null;
    const id = sourceKey.replace(/\.json$/i, '');
    return {
      id,
      enterpriseName: String(record.enterpriseName || sourceKey.replace(/^\d+_/, '').replace(/\.json$/i, '').replace(/_/g, ' ')).trim(),
      creditCode: '',
      industryField: '',
      enterpriseScale: '',
      registerDate: '',
      updateTime: record.completedAt || '',
      matchResultPath: `/api/enterprise-match-results/result?id=${encodeURIComponent(id)}`,
      sourceFile: sourceKey,
      sourceRunId: status.runId || ENTERPRISE_MATCH_RUN_ID
    };
  }).filter(Boolean);
  return {
    success: true,
    data: {
      sourceRunId: status.runId || ENTERPRISE_MATCH_RUN_ID,
      total: enterprises.length,
      enterprises
    }
  };
}

function loadEnterpriseMatchResult(query = {}) {
  const { responseDir, status } = loadEnterpriseMatchStore();
  const file = String(query.file || '').trim();
  if (file) {
    const safeFile = path.basename(file);
    if (safeFile !== file || !safeFile.endsWith('.json')) throw new Error('非法结果文件名');
    const filePath = path.join(responseDir, safeFile);
    if (!fs.existsSync(filePath)) throw new Error('企业匹配结果不存在');
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  }
  const id = String(query.id || '').trim();
  if (!id) throw new Error('缺少企业匹配结果 id');
  const sourceKey = id.endsWith('.json') ? id : `${id}.json`;
  const record = status.records?.[sourceKey];
  if (!record || record.status !== 'completed') throw new Error('企业匹配结果不存在');
  const responseName = responseNameFromRecord(sourceKey, record);
  const filePath = path.join(responseDir, responseName);
  if (!fs.existsSync(filePath)) throw new Error('企业匹配结果文件不存在');
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

const routes = {
  '/api/enterprise-match-results/manifest': async (req, res) => {
    try {
      sendJson(res, buildEnterpriseMatchManifest());
    } catch (err) {
      sendJson(res, { success: false, message: err.message || '企业匹配结果目录读取失败' }, 500);
    }
  },

  '/api/enterprise-match-results/result': async (req, res, query) => {
    try {
      sendJson(res, loadEnterpriseMatchResult(query));
    } catch (err) {
      sendJson(res, { success: false, message: err.message || '企业匹配结果读取失败' }, 404);
    }
  },

  '/api/auth/login': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/auth/login', 'POST', {
        username: String(body.username || '').trim(),
        password: String(body.password || '')
      });
      if (!result.success) return sendJson(res, result, 401);

      const remember = body.remember === true || body.remember === 'true';
      const maxAge = remember ? 7 * 24 * 60 * 60 : 8 * 60 * 60;
      const token = crypto.randomBytes(32).toString('hex');
      sessions.set(token, { user: result.data, expiresAt: Date.now() + maxAge * 1000 });
      scheduleSaveSessions();
      setSessionCookie(res, token, remember ? maxAge : null);
      sendJson(res, { success: true, message: '登录成功', data: result.data });
    } catch (err) {
      console.error('[API Error] /api/auth/login:', err.message);
      sendJson(res, { success: false, message: '登录服务暂时不可用' }, 503);
    }
  },

  '/api/auth/session': async (req, res) => {
    const session = getSession(req);
    if (!session) return sendJson(res, { success: false, message: '未登录' }, 401);
    sendJson(res, { success: true, data: session.user });
  },

  '/api/auth/logout': async (req, res) => {
    const session = getSession(req);
    if (session) {
      sessions.delete(session.token);
      scheduleSaveSessions();
    }
    res.setHeader('Set-Cookie', `${AUTH_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0`);
    sendJson(res, { success: true, message: '已退出登录' });
  },

  // 获取政策列表
  '/api/policies': async (req, res, query) => {
    try {
      const params = new URLSearchParams(query).toString();
      console.log('[API] /api/policies query:', query);
      const result = await fetchFromJDBC(`/api/policies${params ? '?' + params : ''}`);
      if (result.success && Array.isArray(result.data)) {
        result.data = result.data.map(enrichPolicyFromLocalMarkdown);
      } else if (!result.success) {
        result.message = normalizeClientErrorMessage(result.message, '查询政策列表失败');
      }
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/policies:', err.message);
      sendJson(res, {
        success: false,
        message: '无法连接到数据库服务，请确保 JDBC 服务已启动: ' + err.message
      }, 503);
    }
  },

  // 获取统计数据
  '/api/statistics': async (req, res) => {
    try {
      const result = await fetchFromJDBC('/api/statistics');
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/statistics:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 获取筛选统计
  '/api/filterStats': async (req, res) => {
    try {
      const result = await fetchFromJDBC('/api/filterStats');
      console.log('[FilterStats] JDBC返回:', JSON.stringify(result));
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/filterStats:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 更新政策分类
  '/api/policies/update': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/policies/update', 'POST', withAuditContext(req, body, {
        module: 'policy-classification',
        action: 'update'
      }));
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/policies/update:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 政策属性审核通过
  '/api/policies/approve': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/policies/approve', 'POST', withAuditContext(req, body, {
        module: 'policy-classification',
        action: 'approve'
      }));
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/policies/approve:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 撤销政策属性审核
  '/api/policies/revoke': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/policies/revoke', 'POST', withAuditContext(req, body, {
        module: 'policy-classification',
        action: 'revoke'
      }));
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/policies/revoke:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 标记政策备注/错误记录
  '/api/policies/remark': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/policies/remark', 'POST', withAuditContext(req, body, {
        module: 'policy-classification',
        action: 'remark'
      }));
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/policies/remark:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 获取政策分类列表（从POLICY_TYPE表）
  '/api/policy-types': async (req, res, query) => {
    try {
      const params = new URLSearchParams(query).toString();
      const result = await fetchFromJDBC(`/api/policy-types${params ? '?' + params : ''}`);
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/policy-types:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 更新政策分类（POLICY_TYPE表）
  '/api/policy-types/update': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/policy-types/update', 'POST', body);
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/policy-types/update:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 获取政策原文详情
  '/api/policy-original/detail': async (req, res, query) => {
    try {
      const params = new URLSearchParams(query).toString();
      const result = await fetchFromJDBC(`/api/policy-original/detail${params ? '?' + params : ''}`);
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/policy-original/detail:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 获取政策原文内容（根据FILE_PATH读取文件）
  '/api/policy-original/content': async (req, res, query) => {
    try {
      const params = new URLSearchParams(query).toString();
      const result = await fetchFromJDBC(`/api/policy-original/content${params ? '?' + params : ''}`);
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/policy-original/content:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 政策原文统一读取：只使用数据库 FILE_PATH 指向的文件
  '/api/policy/content': async (req, res, query) => {
    try {
      const id = String(query.id || '').trim();
      if (!id) {
        return sendJson(res, { success: false, message: '缺少政策ID' }, 400);
      }
      const fileResult = await fetchFromJDBC(`/api/policy-original/content?id=${encodeURIComponent(id)}`);
      if (!fileResult.success || !fileResult.data) {
        return sendJson(res, {
          success: false,
          message: fileResult.message || '数据库中未找到政策文件路径'
        }, 404);
      }
      const content = String(fileResult.data.content || '').trim();
      if (!content || /^(无文件路径|文件不存在|读取文件失败)/.test(content)) {
        return sendJson(res, {
          success: false,
          message: content || '数据库中的政策文件路径为空'
        }, 404);
      }
      return sendJson(res, {
        success: true,
        data: {
          ...fileResult.data,
          content,
          source: 'database-path',
          ...parsePolicyMarkdown(content)
        }
      });
    } catch (err) {
      console.error('[API Error] /api/policy/content:', err.message);
      return sendJson(res, { success: false, message: err.message }, 500);
    }
  },

  // 获取附件原文内容
  '/api/attachment/content': async (req, res, query) => {
    try {
      const params = new URLSearchParams(query).toString();
      const result = await fetchFromJDBC(`/api/attachment/content${params ? '?' + params : ''}`);
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/attachment/content:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 浏览器内嵌预览附件，目前主要用于扫描版 PDF。
  '/api/attachment/file': async (req, res, query) => {
    try {
      const target = await resolveAttachmentBinaryTarget(query);
      if (!target) {
        return sendJson(res, { success: false, message: '附件不存在或路径不在允许目录内' }, 404);
      }
      const ext = path.extname(target.file).toLowerCase();
      const contentType = ext === '.pdf' ? 'application/pdf' : 'application/octet-stream';
      const disposition = query.download === '1' ? 'attachment' : 'inline';
      res.writeHead(200, {
        'Content-Type': contentType,
        'Content-Disposition': `${disposition}; filename*=UTF-8''${encodeURIComponent(target.fileName)}`,
        'X-Content-Type-Options': 'nosniff'
      });
      fs.createReadStream(target.file).pipe(res);
    } catch (err) {
      console.error('[API Error] /api/attachment/file:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 获取政策附件列表
  '/api/attachment/list': async (req, res, query) => {
    try {
      const params = new URLSearchParams(query).toString();
      const result = await fetchFromJDBC(`/api/attachment/list${params ? '?' + params : ''}`);
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/attachment/list:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 分类标注页：直接读取 POLICY，并关联 POLICY_ATTACHMENT
  '/api/classification/policies': async (req, res, query) => {
    try {
      const params = new URLSearchParams(query).toString();
      const result = await fetchFromJDBC(`/api/classification/policies${params ? '?' + params : ''}`);
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/classification/policies:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 永久删除政策及其数据库关联记录
  '/api/classification/policies/delete': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/classification/policies/delete', 'POST', body);
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/classification/policies/delete:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  '/api/fusion-classification/policies': async (req, res, query) => {
    try {
      const params = new URLSearchParams(query).toString();
      sendJson(res, await fetchFromJDBC(`/api/fusion-classification/policies${params ? '?' + params : ''}`));
    } catch (err) {
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },
  '/api/fusion-classification/policies/policy-classification': async (req, res, query) => {
    try {
      const params = new URLSearchParams(query).toString();
      sendJson(res, await fetchFromJDBC(`/api/fusion-classification/policies/policy-classification${params ? '?' + params : ''}`));
    } catch (err) {
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },
  '/api/fusion-classification/policies/create': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/policies/create', 'POST', withAuditContext(req, body, { module: 'fusion-policy', action: 'create' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/documents/create': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/documents/create', 'POST', withAuditContext(req, body, { module: 'fusion-document', action: 'create' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/documents/candidates': async (req, res, query) => {
    try {
      const params = new URLSearchParams(query).toString();
      sendJson(res, await fetchFromJDBC(`/api/fusion-classification/documents/candidates${params ? '?' + params : ''}`));
    } catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/documents/link': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/documents/link', 'POST', withAuditContext(req, body, { module: 'fusion-document', action: 'link' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/documents/type': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/documents/type', 'POST', withAuditContext(req, body, { module: 'fusion-document', action: 'type' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/documents/content': async (req, res, query) => {
    try {
      const params = new URLSearchParams(query).toString();
      sendJson(res, await fetchFromJDBC(`/api/fusion-classification/documents/content${params ? '?' + params : ''}`));
    } catch (err) {
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },
  '/api/fusion-classification/policies/delete': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/policies/delete', 'POST', withAuditContext(req, body, { module: 'fusion-policy', action: 'delete' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/policies/restore': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/policies/restore', 'POST', withAuditContext(req, body, { module: 'fusion-policy', action: 'restore' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/policies/candidates': async (req, res, query) => {
    try {
      const params = new URLSearchParams(query).toString();
      sendJson(res, await fetchFromJDBC(`/api/fusion-classification/policies/candidates${params ? '?' + params : ''}`));
    } catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/policies/link-from-original': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/policies/link-from-original', 'POST', withAuditContext(req, body, { module: 'fusion-policy', action: 'link-from-original' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/policies/status': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/policies/status', 'POST', withAuditContext(req, body, { module: 'fusion-policy', action: 'status' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/policies/update': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/policies/update', 'POST', withAuditContext(req, body, { module: 'fusion-policy', action: 'update' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/policies/parent/update': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/policies/parent/update', 'POST', withAuditContext(req, body, { module: 'fusion-policy-relation', action: 'update-parent' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/policies/review/approve': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/policies/review/approve', 'POST', withAuditContext(req, body, { module: 'fusion-policy', action: 'approve' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/policies/review/revoke': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/policies/review/revoke', 'POST', withAuditContext(req, body, { module: 'fusion-policy', action: 'revoke' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/structure/review/approve': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/structure/review/approve', 'POST', withAuditContext(req, body, { module: 'fusion-structure', action: 'approve' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/structure/review/revoke': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/structure/review/revoke', 'POST', withAuditContext(req, body, { module: 'fusion-structure', action: 'revoke' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/stats': async (req, res, query) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/stats')); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/fuse': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/fuse', 'POST', body)); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/fusion-detail': async (req, res, query) => {
    try {
      const params = new URLSearchParams(query).toString();
      sendJson(res, await fetchFromJDBC(`/api/fusion-classification/fusion-detail${params ? '?' + params : ''}`, 'GET', null, 30000));
    } catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/fusion-detail/save': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/fusion-detail/save', 'POST', withAuditContext(req, body, { module: 'fusion-detail', action: 'save' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-trace/resolve': async (req, res, query, body) => {
    try { sendJson(res, await resolveFusionTrace(body)); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 500); }
  },
  '/api/fusion-trace/cache': async (req, res, query) => {
    try {
      const params = new URLSearchParams(query).toString();
      sendJson(res, await fetchFromJDBC(`/api/fusion-trace/cache${params ? '?' + params : ''}`));
    } catch (err) {
      sendJson(res, { success: false, message: err.message }, 500);
    }
  },
  '/api/fusion-trace/save': async (req, res, query, body) => {
    try {
      const finalValue = String(body.finalValue || body.value || '').trim();
      const payload = withAuditContext(req, {
        ...body,
        finalValue,
        valueHash: body.valueHash || traceValueHash(finalValue),
        confirmStatus: body.confirmStatus || '人工修正'
      }, {
        module: 'fusion-trace',
        action: 'save'
      });
      sendJson(res, await fetchFromJDBC('/api/fusion-trace/save', 'POST', payload));
    } catch (err) {
      sendJson(res, { success: false, message: err.message }, 500);
    }
  },
  '/api/fusion-classification/documents/update': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/documents/update', 'POST', withAuditContext(req, body, { module: 'fusion-document', action: 'update' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/documents/delete': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/documents/delete', 'POST', withAuditContext(req, body, { module: 'fusion-document', action: 'delete' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/documents/restore': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/documents/restore', 'POST', withAuditContext(req, body, { module: 'fusion-document', action: 'restore' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion-classification/documents/move': async (req, res, query, body) => {
    try { sendJson(res, await fetchFromJDBC('/api/fusion-classification/documents/move', 'POST', withAuditContext(req, body, { module: 'fusion-document', action: 'move' }))); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },

  // ========== 政策融合 LLM 服务接口（代理到 FastAPI） ==========
  '/api/fusion/llm-fuse': async (req, res, query, body) => {
    try {
      writeLog('[Fusion] 收到 LLM 融合请求:', JSON.stringify(body));
      const result = await fetchFromLLMService('/api/fusion/llm-fuse', 'POST', body);
      sendJson(res, result);
    } catch (err) {
      writeError('[Fusion LLM Error]', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },
  '/api/fusion/health': async (req, res) => {
    try { sendJson(res, await fetchFromLLMService('/api/fusion/health')); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  '/api/fusion/info': async (req, res) => {
    try { sendJson(res, await fetchFromLLMService('/api/fusion/info')); }
    catch (err) { sendJson(res, { success: false, message: err.message }, 503); }
  },
  // 规则表生成（调用 PEMatching 服务）
  '/api/rule-table/generate': async (req, res, query, body) => {
    try {
      const fusionId = body?.fusionPolicyId || query?.fusionPolicyId || '';
      writeLog('[RuleTable] 收到规则表生成请求, fusionPolicyId:', fusionId);
      // 将 fusionPolicyId 同时通过 query 和 body 传递，确保 Python 能收到
      const queryPart = fusionId ? `?fusionPolicyId=${encodeURIComponent(fusionId)}` : '';
      const result = await fetchFromPEMatchingService(
        `/api/rule-table/generate${queryPart}`,
        'POST',
        body || { fusionPolicyId: fusionId }
      );
      sendJson(res, result);
    } catch (err) {
      writeError('[RuleTable Error]', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 企业政策匹配（调用 PEMatching 服务）
  '/api/pematching/match': async (req, res, query, body) => {
    try {
      writeLog('[PEMatching] 收到企业政策匹配请求');
      const result = await fetchFromPEMatchingService(
        '/api/pematching/match',
        'POST',
        body || {}
      );
      sendJson(res, result);
    } catch (err) {
      writeError('[PEMatching Error]', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 政策推荐企业分页（转发上游企业服务）
  '/api/enterprise/page': async (req, res, query) => {
    try {
      const params = new URLSearchParams();
      params.set('pageNum', String(query.pageNum || query.page || 1));
      params.set('pageSize', String(query.pageSize || query.size || 20));
      const result = await fetchFromEnterpriseService(`/cms/tb-base-business-info/web/page?${params.toString()}`);
      sendJson(res, result);
    } catch (err) {
      writeError('[Enterprise Error]', err.message);
      sendJson(res, { success: false, code: -1, message: err.message || '企业分页服务暂时不可用' }, 503);
    }
  },

  // 单企业政策推荐匹配（调用 PEMatching 服务）
  '/api/pematching/match/enterprise': async (req, res, query, body) => {
    if (req.method === 'GET') {
      const enterpriseId = String(query?.enterpriseId || '').trim();
      if (!enterpriseId) {
        sendJson(res, { success: false, message: '缺少 enterpriseId 参数' }, 400);
        return;
      }
      try {
        writeLog('[PEMatching] 查询企业最新匹配结果, enterpriseId:', enterpriseId);
        const result = await fetchFromJDBC(
          `/api/pematching/enterprise-match-results?enterpriseId=${encodeURIComponent(enterpriseId)}`
        );
        sendJson(res, result, result?.success === false ? 500 : 200);
      } catch (err) {
        writeError('[PEMatching Enterprise Query Error]', err.message);
        sendJson(res, { success: false, message: err.message }, 503);
      }
      return;
    }
    if (req.method !== 'POST') {
      sendJson(res, { success: false, message: '只支持 GET、POST 方法' }, 405);
      return;
    }
    const enterpriseRecord = body?.enterprise && typeof body.enterprise === 'object'
      ? body.enterprise
      : (body || {});
    const enterpriseId = String(enterpriseRecord.id || enterpriseRecord.enterpriseId || '').trim();
    const enterpriseName = String(enterpriseRecord.enterpriseName || '').trim();
    if (!enterpriseId) {
      sendJson(res, { success: false, message: '企业信息格式错误：必须提供 id 或 enterpriseId。' }, 400);
      return;
    }
    if (!enterpriseName) {
      sendJson(res, { success: false, message: '企业信息格式错误：必须提供 enterpriseName。' }, 400);
      return;
    }
    try {
      writeLog('[PEMatching] 收到单企业政策推荐请求');
      const allowedParams = new Set([
        'policyId',
        'fusionPolicyId',
        'policyKeyword',
        'policyLimit',
        'includeDocumentContent',
        'reviewStatus',
        'structureReviewStatus'
      ]);
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(query || {})) {
        if (!allowedParams.has(key)) continue;
        if (Array.isArray(value)) {
          value.forEach(item => params.append(key, String(item)));
        } else if (value !== undefined && value !== null && value !== '') {
          params.append(key, String(value));
        }
      }
      const result = await fetchFromPEMatchingService(
        `/api/pematching/match/enterprise${params.toString() ? '?' + params.toString() : ''}`,
        'POST',
        body || {}
      );
      const upstreamStatus = Number(result?.__httpStatus);
      sendJson(res, result, Number.isInteger(upstreamStatus) ? upstreamStatus : (result?.success === false ? 500 : 200));
    } catch (err) {
      writeError('[PEMatching Enterprise Error]', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 已成功跑出匹配结果的企业列表（每个企业只返回最新一次运行）
  '/api/pematching/match/enterprises': async (req, res, query) => {
    if (req.method !== 'GET') {
      sendJson(res, { success: false, message: '只支持 GET 方法' }, 405);
      return;
    }
    const pageText = String(query?.page || query?.pageNum || query?.pageNumber || '1').trim();
    const sizeText = String(query?.size || query?.pageSize || '20').trim();
    const page = Number(pageText);
    const size = Number(sizeText);
    if (!Number.isInteger(page) || page < 1) {
      sendJson(res, { success: false, message: 'page 必须是正整数' }, 400);
      return;
    }
    if (!Number.isInteger(size) || size < 1 || size > 100) {
      sendJson(res, { success: false, message: 'size 必须是 1 到 100 的正整数' }, 400);
      return;
    }
    try {
      const params = new URLSearchParams({ page: String(page), size: String(size) });
      const enterpriseName = String(query?.enterpriseName || '').trim();
      if (enterpriseName) params.set('enterpriseName', enterpriseName);
      const result = await fetchFromJDBC(
        `/api/pematching/enterprise-match-results/enterprises?${params.toString()}`
      );
      sendJson(res, result, result?.success === false ? 500 : 200);
    } catch (err) {
      writeError('[PEMatching Enterprise List Error]', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // LLM 融合（基于文档内容注入，不依赖目录）
  '/api/fusion/llm-fuse-by-content': async (req, res, query, body) => {
    try {
      writeLog('[Fusion] 收到内容注入融合请求:', JSON.stringify({policyTitle: body?.policyTitle, docCount: body?.documents?.length}));
      const result = await fetchFromLLMService('/api/fusion/llm-fuse-by-content', 'POST', body);
      sendJson(res, result);
    } catch (err) {
      writeError('[Fusion LLM Error]', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },
  // LLM 融合（基于 fusionPolicyId，FastAPI 内部自动从 JDBC 获取原文）
  '/api/fusion/llm-fuse-by-policy': async (req, res, query, body) => {
    try {
      writeLog('[Fusion] 收到政策融合请求, fusionPolicyId:', body?.fusionPolicyId);
      const result = await fetchFromLLMService('/api/fusion/llm-fuse-by-policy', 'POST', body);
      sendJson(res, result);
    } catch (err) {
      writeError('[Fusion LLM Error]', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // ========== importantplus 本地政策文件接口 ==========
  // 获取政策主 Markdown 内容及基本信息
  '/api/local/policy/content': async (req, res, query) => {
    try {
      const title = query.title || '';
      if (!title) return sendJson(res, { success: false, message: '缺少政策标题' }, 400);

      const policyDir = findPolicyDirectory(title);
      if (!policyDir) return sendJson(res, { success: false, message: '未找到对应政策目录' }, 404);
      const filePath = findPolicyMainFile(policyDir, title);
      if (!filePath) return sendJson(res, { success: false, message: '未找到政策原文文件' }, 404);

      const content = fs.readFileSync(filePath, 'utf-8');
      sendJson(res, {
        success: true,
        data: {
          filePath,
          fileType: path.extname(filePath).slice(1).toUpperCase(),
          content,
          ...parsePolicyMarkdown(content)
        }
      });
    } catch (err) {
      console.error('[API Error] /api/local/policy/content:', err.message);
      sendJson(res, { success: false, message: err.message }, 500);
    }
  },

  // 获取单条政策的附件类型
  '/api/attachment-types': async (req, res, query) => {
    try {
      const params = new URLSearchParams(query).toString();
      const result = await fetchFromJDBC(`/api/attachment-types${params ? '?' + params : ''}`);
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/attachment-types:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 保存单个附件类型
  '/api/attachment-types/update': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/attachment-types/update', 'POST', withAuditContext(req, body, {
        module: 'policy-attachment',
        action: 'update'
      }));
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/attachment-types/update:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 复核通过单个关联文件
  '/api/attachment-types/approve': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/attachment-types/approve', 'POST', withAuditContext(req, body, {
        module: 'policy-attachment',
        action: 'approve'
      }));
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/attachment-types/approve:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  '/api/attachment-types/revoke': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/attachment-types/revoke', 'POST', withAuditContext(req, body, {
        module: 'policy-attachment',
        action: 'revoke'
      }));
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/attachment-types/revoke:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 获取本地附件列表
  '/api/local/attachments/list': async (req, res, query) => {
    try {
      const title = query.title || '';
      const databaseFilePath = query.filePath || '';
      if (!title && !databaseFilePath) {
        return sendJson(res, { success: false, message: '缺少政策标题或文件路径' });
      }

      const mainPolicyFile = resolveAllowedAttachmentPath(databaseFilePath);
      const policyDir = mainPolicyFile ? path.dirname(mainPolicyFile) : findPolicyDirectory(title);
      if (!policyDir) {
        return sendJson(res, { success: true, data: [], message: '未找到附件目录' });
      }
      const attachDir = path.join(policyDir, 'attachments');

      if (!fs.existsSync(attachDir)) {
        return sendJson(res, { success: true, data: [] });
      }

      // 读取附件列表
      const files = fs.readdirSync(attachDir, { withFileTypes: true })
        .filter(entry => entry.isFile())
        .map(entry => entry.name);
      const attachments = files.map((file, index) => ({
        id: index + 1,
        attachmentName: file,
        attachmentShowName: file.replace(/\.[^.]+$/i, ''),
        fileSuffix: path.extname(file).slice(1).toUpperCase() || '文件',
        filePath: path.join(attachDir, file),
        source: 'importantplus'
      }));

      sendJson(res, { success: true, data: attachments });
    } catch (err) {
      console.error('[API Error] /api/local/attachments/list:', err.message);
      sendJson(res, { success: false, message: err.message }, 500);
    }
  },

  // 获取本地附件内容
  '/api/local/attachment/content': async (req, res, query) => {
    try {
      const filePath = query.filePath;
      if (!filePath) {
        return sendJson(res, { success: false, message: '缺少文件路径' });
      }

      // 安全检查：确保路径在 importantplus 目录下
      const safePath = path.resolve(filePath);
      if (!safePath.startsWith(IMPORTANTPLUS_DIR + path.sep)) {
        return sendJson(res, { success: false, message: '非法路径' }, 400);
      }

      if (!fs.existsSync(safePath)) {
        return sendJson(res, { success: false, message: '文件不存在' }, 404);
      }

      const content = fs.readFileSync(safePath, 'utf-8');
      sendJson(res, { success: true, data: { content } });
    } catch (err) {
      console.error('[API Error] /api/local/attachment/content:', err.message);
      sendJson(res, { success: false, message: err.message }, 500);
    }
  },

  // 获取本地关联政策文档内容
  '/api/local/document/content': async (req, res, query) => {
    try {
      const filePath = String(query.filePath || '').trim();
      if (!filePath) {
        return sendJson(res, { success: false, message: '缺少文件路径' });
      }

      const safePath = resolveAllowedAttachmentPath(filePath);
      if (!safePath) {
        return sendJson(res, { success: false, message: '文件不存在或路径不在允许目录内' }, 404);
      }

      const content = fs.readFileSync(safePath, 'utf-8');
      sendJson(res, {
        success: true,
        data: {
          fileName: path.basename(safePath),
          filePath,
          content
        }
      });
    } catch (err) {
      console.error('[API Error] /api/local/document/content:', err.message);
      sendJson(res, { success: false, message: err.message }, 500);
    }
  },

  // 健康检查
  '/api/health': async (req, res) => {
    try {
      const jdbcHealth = await fetchFromJDBC('/api/health');
      sendJson(res, {
        success: true,
        data: {
          nodejs: { status: 'ok', port: PORT },
          jdbc: jdbcHealth
        }
      });
    } catch (err) {
      sendJson(res, {
        success: false,
        data: {
          nodejs: { status: 'ok', port: PORT },
          jdbc: { status: 'error', message: err.message }
        }
      });
    }
  },

  // ========== 基础数据字典接口 (BASE_DATA_DICT) ==========
  // 基础数据字典 - 查询
  '/api/baseDataDict': async (req, res, query) => {
    try {
      const params = new URLSearchParams(Object.entries(query || {})).toString();
      const result = await fetchFromJDBC(`/api/baseDataDict${params ? '?' + params : ''}`);
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/baseDataDict:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 基础数据字典 - 创建
  '/api/baseDataDict/create': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/baseDataDict/create', 'POST', withAuditContext(req, body, {
        module: 'base-data',
        action: 'create'
      }));
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/baseDataDict/create:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 基础数据字典 - 更新
  '/api/baseDataDict/update': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/baseDataDict/update', 'POST', withAuditContext(req, body, {
        module: 'base-data',
        action: 'update'
      }));
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/baseDataDict/update:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 基础数据字典 - 删除
  '/api/baseDataDict/delete': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/baseDataDict/delete', 'POST', withAuditContext(req, body, {
        module: 'base-data',
        action: 'delete'
      }));
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/baseDataDict/delete:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // ========== 四张基础数据真实表 CRUD ==========
  '/api/baseDataRecords': async (req, res, query) => {
    try {
      const params = new URLSearchParams(Object.entries(query || {})).toString();
      const result = await fetchFromJDBC(`/api/baseDataRecords${params ? '?' + params : ''}`);
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/baseDataRecords:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  '/api/baseDataRecords/create': async (req, res, query, body) => {
    try {
      sendJson(res, await fetchFromJDBC('/api/baseDataRecords/create', 'POST', withAuditContext(req, body, {
        module: 'base-data',
        action: 'create'
      })));
    } catch (err) {
      console.error('[API Error] /api/baseDataRecords/create:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  '/api/baseDataRecords/update': async (req, res, query, body) => {
    try {
      sendJson(res, await fetchFromJDBC('/api/baseDataRecords/update', 'POST', withAuditContext(req, body, {
        module: 'base-data',
        action: 'update'
      })));
    } catch (err) {
      console.error('[API Error] /api/baseDataRecords/update:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  '/api/baseDataRecords/delete': async (req, res, query, body) => {
    try {
      sendJson(res, await fetchFromJDBC('/api/baseDataRecords/delete', 'POST', withAuditContext(req, body, {
        module: 'base-data',
        action: 'delete'
      })));
    } catch (err) {
      console.error('[API Error] /api/baseDataRecords/delete:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // 基础数据记录 - 清空所有
  '/api/baseDataRecords/clear': async (req, res, query, body) => {
    try {
      if (!body || body.type !== 'policy_keyword') {
        sendJson(res, { success: false, message: '当前数据类型不支持清空' }, 400);
        return;
      }
      sendJson(res, await fetchFromJDBC('/api/baseDataRecords/clear', 'POST', withAuditContext(req, body, {
        module: 'base-data',
        action: 'clear'
      })));
    } catch (err) {
      console.error('[API Error] /api/baseDataRecords/clear:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // ========== 申报条件字典接口 (APPLICATION_CONDITION_DICT) ==========
  '/api/applicationConditionDict': async (req, res, query) => {
    try {
      const params = new URLSearchParams(Object.entries(query || {})).toString();
      const result = await fetchFromJDBC(`/api/applicationConditionDict${params ? '?' + params : ''}`);
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/applicationConditionDict:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  '/api/applicationConditionDict/create': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/applicationConditionDict/create', 'POST', withAuditContext(req, body, {
        module: 'base-data',
        action: 'create'
      }));
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/applicationConditionDict/create:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  '/api/applicationConditionDict/update': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/applicationConditionDict/update', 'POST', withAuditContext(req, body, {
        module: 'base-data',
        action: 'update'
      }));
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/applicationConditionDict/update:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  '/api/applicationConditionDict/delete': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/applicationConditionDict/delete', 'POST', withAuditContext(req, body, {
        module: 'base-data',
        action: 'delete'
      }));
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/applicationConditionDict/delete:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // ========== 申报条件字段信息接口 (CATEGORY_FIELD_INFO) ==========
  '/api/categoryFieldInfo': async (req, res, query) => {
    try {
      const params = new URLSearchParams(Object.entries(query || {})).toString();
      const result = await fetchFromJDBC(`/api/categoryFieldInfo${params ? '?' + params : ''}`);
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/categoryFieldInfo:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  '/api/categoryFieldInfo/create': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/categoryFieldInfo/create', 'POST', withAuditContext(req, body, {
        module: 'base-data',
        action: 'create'
      }));
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/categoryFieldInfo/create:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  '/api/categoryFieldInfo/update': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/categoryFieldInfo/update', 'POST', withAuditContext(req, body, {
        module: 'base-data',
        action: 'update'
      }));
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/categoryFieldInfo/update:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  '/api/categoryFieldInfo/delete': async (req, res, query, body) => {
    try {
      const result = await fetchFromJDBC('/api/categoryFieldInfo/delete', 'POST', withAuditContext(req, body, {
        module: 'base-data',
        action: 'delete'
      }));
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/categoryFieldInfo/delete:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // ========== 适用范围选项接口 (DIM_RULE_OPTION scope_level) ==========
  '/api/rule-options/scope-level': async (req, res) => {
    try {
      const result = await fetchFromJDBC('/api/rule-options/scope-level');
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/rule-options/scope-level:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  // ========== 行业领域接口 (SYS_TERRITORY_DATA_NEW / SYS_TERRITORY_INDUSTRY_22N) ==========
  '/api/territory/industry-names': async (req, res) => {
    try {
      const result = await fetchFromJDBC('/api/territory/industry-names');
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/territory/industry-names:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  '/api/territory/industry-22n': async (req, res, query) => {
    try {
      const params = new URLSearchParams(Object.entries(query || {})).toString();
      const result = await fetchFromJDBC(`/api/territory/industry-22n${params ? '?' + params : ''}`);
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/territory/industry-22n:', err.message);
      sendJson(res, { success: false, message: err.message }, 503);
    }
  },

  '/api/crawler/xmsme/preview': async (req, res, query, body) => {
    try {
      const url = String(body?.url || '').trim();
      if (!url) return sendJson(res, { success: false, message: '请输入政策 URL' }, 400);
      const preview = await buildRound3CrawlerPreview(url);
      sendJson(res, { success: true, message: '抓取成功', data: preview });
    } catch (err) {
      console.error('[API Error] /api/crawler/xmsme/preview:', err.message);
      sendJson(res, { success: false, message: normalizeClientErrorMessage(err.message, '政策 URL 查看失败') }, 500);
    }
  },

  '/api/crawler/xmsme/import': async (req, res, query, body) => {
    try {
      const previewId = String(body?.previewId || '').trim();
      const preview = crawlerPreviews.get(previewId);
      if (!preview) return sendJson(res, { success: false, message: '预览记录不存在，请重新查看 URL' }, 404);
      applyCrawlerPreviewEdits(preview, body);
      await updateCrawlerPreviewMarkdown(preview, body?.markdown);
      const result = await importCrawlerPreviewToDatabase(preview);
      sendJson(res, result);
    } catch (err) {
      console.error('[API Error] /api/crawler/xmsme/import:', err.message);
      sendJson(res, { success: false, message: normalizeClientErrorMessage(err.message, '政策入库失败') }, 503);
    }
  },

  '/api/crawler/xmsme/incremental': async (req, res, query, body) => {
    try {
      const trigger = String(body?.trigger || query?.trigger || 'manual').trim() || 'manual';
      const startDate = body?.startDate || query?.startDate;
      const endDate = body?.endDate || query?.endDate;
      const result = startDate || endDate
        ? await runXmsmeIncrementalImportRange(startDate || endDate, endDate || startDate, trigger)
        : await runXmsmeIncrementalImport(body?.date || query?.date || yesterdayLocalDate(), trigger);
      const dateText = result.startDate && result.endDate ? `${result.startDate} 至 ${result.endDate}` : result.date;
      sendJson(res, { success: true, message: `增量更新完成（${dateText}）：共 ${result.total} 条，成功 ${result.imported} 条，失败 ${result.failed} 条`, data: result });
    } catch (err) {
      console.error('[API Error] /api/crawler/xmsme/incremental:', err.message);
      sendJson(res, { success: false, message: err.message }, 500);
    }
  },

  '/api/crawler/xmsme/incremental/status': async (req, res) => {
    sendJson(res, {
      success: true,
      data: {
        running: xmsmeIncrementalRunning,
        lastResult: lastXmsmeIncrementalResult,
        scheduledHour: xmsmeIncrementalHour,
        scheduledMinute: xmsmeIncrementalMinute,
        scheduledTime: `${String(xmsmeIncrementalHour).padStart(2, '0')}:${String(xmsmeIncrementalMinute).padStart(2, '0')}`,
        enabled: xmsmeIncrementalEnabled
      }
    });
  },

  '/api/crawler/xmsme/incremental/schedule': async (req, res, query, body) => {
    try {
      const timeText = String(body?.time || query?.time || '').trim();
      let hour = Number(body?.hour ?? query?.hour ?? xmsmeIncrementalHour);
      let minute = Number(body?.minute ?? query?.minute ?? xmsmeIncrementalMinute);
      if (/^\d{1,2}:\d{2}$/.test(timeText)) {
        const parts = timeText.split(':').map(Number);
        hour = parts[0];
        minute = parts[1];
      }
      if (!Number.isInteger(hour) || hour < 0 || hour > 23 || !Number.isInteger(minute) || minute < 0 || minute > 59) {
        return sendJson(res, { success: false, message: '定时开始时间不合法' }, 400);
      }
      xmsmeIncrementalEnabled = body?.enabled !== undefined ? Boolean(body.enabled) : query?.enabled !== 'false';
      xmsmeIncrementalHour = hour;
      xmsmeIncrementalMinute = minute;
      scheduleNextXmsmeIncrementalRun();
      sendJson(res, {
        success: true,
        message: '定时增量更新设置已保存',
        data: {
          enabled: xmsmeIncrementalEnabled,
          scheduledHour: xmsmeIncrementalHour,
          scheduledMinute: xmsmeIncrementalMinute,
          scheduledTime: `${String(xmsmeIncrementalHour).padStart(2, '0')}:${String(xmsmeIncrementalMinute).padStart(2, '0')}`
        }
      });
    } catch (err) {
      sendJson(res, { success: false, message: err.message || '定时设置保存失败' }, 500);
    }
  },

  '/api/crawler/xmsme/download': async (req, res) => {
    try {
      const urlObj = new URL(req.url, `http://${req.headers.host}`);
      const previewId = String(urlObj.searchParams.get('previewId') || '').trim();
      const preview = crawlerPreviews.get(previewId);
      if (!preview) return sendJson(res, { success: false, message: '预览记录不存在，请重新查看 URL' }, 404);
      const localPolicyDir = preview.localPolicyDir || preview.policyDir;
      if (!localPolicyDir || !fs.existsSync(localPolicyDir)) {
        return sendJson(res, { success: false, message: '本地政策目录不存在' }, 404);
      }
      const zipBuffer = createZipBuffer(localPolicyDir, preview.safeTitle || safeCrawlerFileName(preview.title));
      const zipName = encodeURIComponent(`${preview.safeTitle || safeCrawlerFileName(preview.title)}.zip`);
      res.writeHead(200, {
        'Content-Type': 'application/zip',
        'Content-Disposition': `attachment; filename*=UTF-8''${zipName}`,
        'Content-Length': zipBuffer.length,
        'Access-Control-Allow-Origin': '*'
      });
      res.end(zipBuffer);
    } catch (err) {
      console.error('[API Error] /api/crawler/xmsme/download:', err.message);
      sendJson(res, { success: false, message: err.message }, 500);
    }
  }
};

// ============================================
// HTTP 服务器
// ============================================

function sendJson(res, data, statusCode = 200) {
  res.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type'
  });
  res.end(JSON.stringify(data));
}

// ============================================
// 静态文件服务
// ============================================

const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.md': 'text/markdown; charset=utf-8',
  '.pdf': 'application/pdf',
  '.doc': 'application/msword',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.xls': 'application/vnd.ms-excel',
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2'
};

const COMPRESSIBLE_EXTENSIONS = new Set(['.html', '.css', '.js', '.json', '.svg']);

function createStaticEtag(stat) {
  return `W/"${stat.size.toString(16)}-${Math.floor(stat.mtimeMs).toString(16)}"`;
}

function shouldGzipStatic(req, ext) {
  const acceptEncoding = String(req.headers['accept-encoding'] || '');
  return COMPRESSIBLE_EXTENSIONS.has(ext) && acceptEncoding.includes('gzip');
}

function getStaticCacheControl(pathname, ext) {
  if (ext === '.html') {
    return 'no-cache';
  }
  return 'public, max-age=0, must-revalidate';
}

function serveStaticFile(req, res, pathname) {
  // 将 / 指向 index.html
  if (pathname === '/') {
    pathname = '/index.html';
  }

  const filePath = path.join(__dirname, pathname);
  const ext = path.extname(filePath);
  const contentType = MIME_TYPES[ext] || 'application/octet-stream';

  fs.stat(filePath, (err, stat) => {
    if (err || !stat.isFile()) {
      // 文件不存在返回 404
      res.writeHead(404, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ success: false, message: `文件不存在: ${pathname}` }));
      return;
    }

    const etag = createStaticEtag(stat);
    const lastModified = stat.mtime.toUTCString();
    const cacheControl = getStaticCacheControl(pathname, ext);
    const ifNoneMatch = String(req.headers['if-none-match'] || '');
    const ifModifiedSince = req.headers['if-modified-since']
      ? Date.parse(String(req.headers['if-modified-since']))
      : NaN;

    if (
      ifNoneMatch === etag ||
      (!Number.isNaN(ifModifiedSince) && Math.floor(stat.mtimeMs / 1000) <= Math.floor(ifModifiedSince / 1000))
    ) {
      res.writeHead(304, {
        'Cache-Control': cacheControl,
        'ETag': etag,
        'Last-Modified': lastModified,
        'Vary': 'Accept-Encoding'
      });
      res.end();
      return;
    }

    const useGzip = shouldGzipStatic(req, ext);
    const headers = {
      'Content-Type': contentType,
      'Cache-Control': cacheControl,
      'ETag': etag,
      'Last-Modified': lastModified,
      'X-Content-Type-Options': 'nosniff',
      'Vary': 'Accept-Encoding'
    };

    if (!useGzip) {
      headers['Content-Length'] = stat.size;
    } else {
      headers['Content-Encoding'] = 'gzip';
    }

    res.writeHead(200, headers);
    if (req.method === 'HEAD') {
      res.end();
      return;
    }

    const fileStream = fs.createReadStream(filePath);
    fileStream.on('error', (streamErr) => {
      writeError('[静态文件读取失败]', pathname, streamErr.message);
      if (!res.headersSent) {
        res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
      }
      res.end(JSON.stringify({ success: false, message: `读取文件失败: ${pathname}` }));
    });

    if (useGzip) {
      fileStream.pipe(zlib.createGzip({ level: zlib.constants.Z_BEST_SPEED })).pipe(res);
      return;
    }

    fileStream.pipe(res);
  });
}

const server = http.createServer(async (req, res) => {
  const startTime = Date.now();
  const myURL = new URL(req.url, 'http://localhost');
  const pathname = myURL.pathname;
  const query = {};
  for (const [key, value] of myURL.searchParams.entries()) {
    if (Object.prototype.hasOwnProperty.call(query, key)) {
      query[key] = Array.isArray(query[key]) ? [...query[key], value] : [query[key], value];
    } else {
      query[key] = value;
    }
  }

  // 每 100 次请求输出一次统计
  const requestId = (server.requestCount = (server.requestCount || 0) + 1);
  if (requestId % 100 === 0) {
    const mem = process.memoryUsage();
    writeLog(`[请求统计] 总请求数: ${requestId}, 内存: RSS=${Math.round(mem.rss / 1024 / 1024)}MB, 堆=${Math.round(mem.heapUsed / 1024 / 1024)}/${Math.round(mem.heapTotal / 1024 / 1024)}MB`);
  }

  // 记录所有非静态文件的 API 请求（减少日志噪声）
  if (pathname.startsWith('/api/')) {
    writeLog(`[请求] #${requestId} ${req.method} ${pathname}`);
  }

  if (req.method === 'GET' && pathname === '/') {
    res.writeHead(302, { Location: getSession(req) ? '/index.html' : '/login.html' });
    res.end();
    return;
  }

  if (req.method === 'GET' && (pathname === '/index.html' || pathname.startsWith('/pages/')) && !getSession(req)) {
    res.writeHead(302, { Location: `/login.html?redirect=${encodeURIComponent(pathname + myURL.search)}` });
    res.end();
    return;
  }

  // CORS 预检请求
  if (req.method === 'OPTIONS') {
    res.writeHead(200, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type'
    });
    res.end();
    return;
  }

  // 路由匹配
  const route = routes[pathname];
  if (route) {
    if (req.method === 'POST' || req.method === 'PUT') {
      const chunks = [];
      req.on('data', chunk => { chunks.push(Buffer.from(chunk)); });
      req.on('end', async () => {
        try {
          const body = Buffer.concat(chunks).toString('utf8');
          const data = body ? JSON.parse(body) : {};
          await route(req, res, query, data);
        } catch (e) {
          const isJsonParseError = e instanceof SyntaxError;
          sendJson(res, {
            success: false,
            message: isJsonParseError
              ? '请求体不是合法 JSON，请检查前端提交数据'
              : normalizeClientErrorMessage(e.message, '请求处理错误')
          }, isJsonParseError ? 400 : 500);
        }
        logRequestDuration();
      });
    } else {
      try {
        await route(req, res, query);
        logRequestDuration();
      } catch (e) {
        console.error('[路由错误]', pathname, e.message);
        sendJson(res, { success: false, message: '服务器内部错误' }, 500);
        logRequestDuration();
      }
    }
  } else {
    // 静态文件服务
    serveStaticFile(req, res, pathname);
    logRequestDuration();
  }

  // 记录慢请求（超过 3 秒的请求）
  function logRequestDuration() {
    const duration = Date.now() - startTime;
    if (duration > 3000) {
      writeError(`[慢请求] #${requestId} ${req.method} ${pathname} 耗时 ${duration}ms`);
    } else if (duration > 1000) {
      writeLog(`[慢请求] #${requestId} ${req.method} ${pathname} 耗时 ${duration}ms`);
    }
  }
});

// ============================================
// 启动
// ============================================

server.listen(PORT, () => {
  writeLog('='.repeat(55));
  writeLog('🚀 政策解读系统后端服务已启动');
  writeLog('='.repeat(55));
  writeLog(`📡 API 服务:    http://localhost:${PORT}`);
  writeLog(`🔗 JDBC 服务:   ${JDBC_SERVICE_URL}`);
  writeLog(`🤖 LLM 服务:    ${FUSION_LLM_SERVICE_URL}`);
  writeLog(`📂 日志文件:    ${LOG_FILE}`);
  writeLog('='.repeat(55));
  writeLog('📋 API 接口:');
  writeLog('   GET  /api/policies        - 获取政策列表');
  writeLog('   GET  /api/statistics      - 获取统计数据');
  writeLog('   GET  /api/filterStats     - 获取筛选统计');
  writeLog('   POST /api/policies/update - 更新政策分类');
  writeLog('   GET  /api/health          - 健康检查');
  writeLog('   [融合分类 - JDBC]');
  writeLog('   GET  /api/fusion-classification/policies   - 融合政策列表');
  writeLog('   POST /api/fusion-classification/fuse       - 融合查询');
  writeLog('   POST /api/fusion-classification/fusion-detail/save - 融合详情保存');
  writeLog('   [融合 LLM - FastAPI]');
  writeLog('   POST /api/fusion/llm-fuse   - LLM 政策融合');
  writeLog('   GET  /api/fusion/health     - 融合服务健康检查');
  writeLog('   GET  /api/fusion/info       - 融合服务信息');
  writeLog('   [基础数据字典 - BASE_DATA_DICT]');
  writeLog('   GET  /api/baseDataDict            - 获取基础数据列表');
  writeLog('   POST /api/baseDataDict/create     - 创建基础数据');
  writeLog('   POST /api/baseDataDict/update     - 更新基础数据');
  writeLog('   POST /api/baseDataDict/delete     - 删除基础数据');
  writeLog('='.repeat(55));
  writeLog('[启动完成] 服务已就绪，等待请求...');
});

server.on('error', (err) => {
  writeError('服务错误:', err.message);
  if (err.code === 'EADDRINUSE') {
    writeError(`⚠️  端口 ${PORT} 已被占用，请先释放`);
  } else if (err.code === 'EACCES') {
    writeError(`⚠️  没有权限监听端口 ${PORT}`);
  } else if (err.code === 'ENOMEM') {
    writeError('⚠️  系统内存不足，无法启动服务');
  }
});

// ============================================
// 看门狗 — 自动重启（每 30 秒检查一次）
// ============================================

const WATCHDOG_INTERVAL = 30 * 1000; // 30 秒

function startWatchdog(serverInstance, port) {
  const timer = setInterval(() => {
    try {
      // 额外检查：进程是否还活着
      const isRunning = serverInstance.listening;
      if (!isRunning) {
        restartCount++;
        writeError(`[看门狗] 检测到服务停止! 第 ${restartCount} 次重启，PID: ${process.pid}`);
        writeError(`[看门狗] 尝试重新监听端口 ${port}...`);
        try {
          serverInstance.listen(port);
          writeLog(`✅ [看门狗] 服务已成功重启 (第 ${restartCount} 次)，端口: ${port}`);
        } catch (listenErr) {
          writeError(`[看门狗] 重启失败:`, listenErr.message, `(code: ${listenErr.code})`);
          if (listenErr.code === 'EADDRINUSE') {
            writeError(`[看门狗] 端口 ${port} 仍被占用，等待下次检查...`);
          } else if (listenErr.code === 'EADDRNOTAVAIL') {
            writeError(`[看门狗] 端口 ${port} 不可用，服务可能已完全不可恢复`);
          }
        }
      } else {
        // 正常运行时，定期输出心跳日志（每 30 秒一次太频繁，改为每 10 次输出一次）
        if (restartCount > 0) {
          writeLog(`[看门狗] 服务运行正常，上次重启次数: ${restartCount}`);
        }
      }
    } catch (err) {
      writeError('[看门狗] 检查过程中异常:', err.message);
    }
  }, WATCHDOG_INTERVAL);

  // 看门狗不阻止进程退出
  timer.unref();
}

// 启动看门狗
startWatchdog(server, PORT);

// ============================================
// 心跳 — 每 5 分钟记录 uptime 和内存（非看门狗，仅用于诊断）
// ============================================

const HEARTBEAT_INTERVAL = 5 * 60 * 1000; // 5 分钟
function startHeartbeat() {
  const startUptime = Date.now();
  const timer = setInterval(() => {
    const uptime = Math.round((Date.now() - startUptime) / 1000);
    const mem = process.memoryUsage();
    writeLog(
      `[心跳] 运行 ${Math.floor(uptime / 60)} 分 ${uptime % 60} 秒, ` +
      `重启 ${restartCount} 次, ` +
      `会话 ${sessions.size} 个, ` +
      `内存 RSS ${Math.round(mem.rss / 1024 / 1024)}MB, ` +
      `堆 ${Math.round(mem.heapUsed / 1024 / 1024)}/${Math.round(mem.heapTotal / 1024 / 1024)}MB`
    );
  }, HEARTBEAT_INTERVAL);
  timer.unref();
}
startHeartbeat();

function msUntilNextXmsmeIncrementalRun() {
  const now = new Date();
  const next = new Date(now);
  next.setHours(xmsmeIncrementalHour, xmsmeIncrementalMinute, 0, 0);
  if (next <= now) next.setDate(next.getDate() + 1);
  return next.getTime() - now.getTime();
}

function scheduleNextXmsmeIncrementalRun() {
  if (xmsmeIncrementalTimer) {
    clearTimeout(xmsmeIncrementalTimer);
    xmsmeIncrementalTimer = null;
  }
  if (!xmsmeIncrementalEnabled) {
    writeLog('[XMSME Incremental] 定时增量更新已禁用');
    return;
  }
  const delay = msUntilNextXmsmeIncrementalRun();
  xmsmeIncrementalTimer = setTimeout(async () => {
    xmsmeIncrementalTimer = null;
    const date = yesterdayLocalDate();
    writeLog(`[XMSME Incremental] 开始执行 ${date} 增量抓取`);
    try {
      const result = await runXmsmeIncrementalImport(date, 'schedule');
      writeLog(`[XMSME Incremental] 完成 ${date}：共 ${result.total} 条，成功 ${result.imported} 条，失败 ${result.failed} 条`);
    } catch (err) {
      writeError('[XMSME Incremental] 执行失败:', err.message);
    } finally {
      scheduleNextXmsmeIncrementalRun();
    }
  }, delay);
  xmsmeIncrementalTimer.unref();
  writeLog(`[XMSME Incremental] 下一次执行将在 ${Math.round(delay / 1000)} 秒后，时间: ${String(xmsmeIncrementalHour).padStart(2, '0')}:${String(xmsmeIncrementalMinute).padStart(2, '0')}`);
}

scheduleNextXmsmeIncrementalRun();

// 优雅关闭
process.on('SIGINT', () => {
  writeLog('🛑 收到 SIGINT 信号，正在关闭服务... (PID:', process.pid, ')');
  writeLog(`[关闭] 当前活跃会话数: ${sessions.size}`);
  writeLog(`[关闭] 重启总次数: ${restartCount}`);
  server.close(() => {
    writeLog('✅ 服务已停止');
    process.exit(0);
  });
  // 5 秒后强制退出
  setTimeout(() => {
    writeError('[关闭] 超时 5 秒仍未关闭，强制退出');
    process.exit(1);
  }, 5000);
});

process.on('SIGTERM', () => {
  writeLog('🛑 收到 SIGTERM 信号，正在关闭服务... (PID:', process.pid, ')');
  server.close(() => {
    writeLog('✅ 服务已停止');
    process.exit(0);
  });
  setTimeout(() => {
    writeError('[关闭] 超时 5 秒仍未关闭，强制退出');
    process.exit(1);
  }, 5000);
});

// 记录进程被 kill -9 等无法捕获的信号
process.on('SIGHUP', () => {
  writeLog('[信号] 收到 SIGHUP (终端断开)');
});
