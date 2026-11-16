/**
 * 政策解读系统 - 前端工具函数
 * 提供自动重试、心跳检测等功能
 */

// ============================================
// 带重试的 fetch（仅对 GET/HEAD 等幂等请求重试）
// ============================================

/**
 * 带自动重试的 fetch 封装
 * @param {string} url - 请求地址
 * @param {object} options - fetch 选项
 * @param {number} retries - 重试次数（默认2次）
 * @param {number} delay - 重试延迟毫秒（默认1000ms）
 * @returns {Promise<Response>}
 */
async function fetchWithRetry(url, options = {}, retries = 2, delay = 1000) {
  const method = (options.method || 'GET').toUpperCase();
  const { timeoutMs = 30000, ...fetchOptions } = options;
  // 只对幂等请求重试
  const isIdempotent = ['GET', 'HEAD', 'OPTIONS', 'PUT', 'DELETE'].includes(method);

  let lastError;
  for (let attempt = 0; attempt <= (isIdempotent ? retries : 0); attempt++) {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
      const response = await fetch(url, { ...fetchOptions, signal: controller.signal });
      clearTimeout(timeoutId);
      return response;
    } catch (err) {
      lastError = err;
      // 非幂等请求或最后一次尝试，不再重试
      if (!isIdempotent || attempt >= retries) break;
      // 只对网络错误重试（不是 HTTP 错误码，那是正常响应）
      if (err.name === 'AbortError') break;
      console.warn(`[fetchWithRetry] 第 ${attempt + 1} 次失败，${delay}ms 后重试:`, url, err.message);
      await new Promise(r => setTimeout(r, delay));
      delay *= 1.5; // 退避：1s → 1.5s → 2.25s
    }
  }
  throw lastError;
}

// ============================================
// 心跳检测 — 自动检查后端是否存活
// ============================================

const HEARTBEAT_INTERVAL = 30000; // 30秒

/**
 * 创建心跳指示器 DOM 元素
 */
function createHeartbeatIndicator() {
  // 避免重复创建
  if (document.getElementById('hc-heartbeat-indicator')) return;

  const div = document.createElement('div');
  div.id = 'hc-heartbeat-indicator';
  div.style.cssText = `
    position: fixed;
    bottom: 12px;
    right: 12px;
    z-index: 99999;
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 12px;
    font-size: 12px;
    color: #fff;
    opacity: 0.7;
    transition: opacity 0.3s;
    cursor: default;
    user-select: none;
  `;
  div.innerHTML = '<span id="hc-dot" style="width:8px;height:8px;border-radius:50%;display:inline-block;background:#67c23a;"></span><span id="hc-text">服务正常</span>';
  document.body.appendChild(div);
}

/**
 * 更新心跳指示器
 */
function updateHeartbeat(status, message) {
  const dot = document.getElementById('hc-dot');
  const text = document.getElementById('hc-text');
  if (!dot || !text) return;

  if (status === 'ok') {
    dot.style.background = '#67c23a';
    text.textContent = message || '服务正常';
  } else if (status === 'degraded') {
    dot.style.background = '#e6a23c';
    text.textContent = message || '服务异常';
  } else {
    dot.style.background = '#f56c6c';
    text.textContent = message || '无法连接';
  }
}

let heartbeatTimer = null;
let heartbeatElapsed = 0;

/**
 * 启动心跳检测
 */
function startHeartbeat() {
  // 只在浏览器环境下运行
  if (typeof window === 'undefined') return;

  // 页面加载后创建指示器
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', createHeartbeatIndicator);
  } else {
    createHeartbeatIndicator();
  }

  if (heartbeatTimer) return; // 已经启动

  const check = async () => {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 10000); // 10 秒超时
      const res = await fetch('/api/health', { signal: controller.signal });
      clearTimeout(timeoutId);
      const result = await res.json();
      if (result.success && result.data?.jdbc?.status === 'ok') {
        updateHeartbeat('ok', '服务正常');
        heartbeatElapsed = 0;
      } else if (result.success) {
        updateHeartbeat('degraded', 'JDBC 服务异常');
      } else {
        updateHeartbeat('error', '服务异常');
      }
    } catch (err) {
      heartbeatElapsed += HEARTBEAT_INTERVAL;
      const seconds = Math.round(heartbeatElapsed / 1000);
      updateHeartbeat('error', `断开 ${seconds}s`);
    }
  };

  // 立即执行一次，然后定时执行
  check();
  heartbeatTimer = setInterval(check, HEARTBEAT_INTERVAL);
}

/**
 * 停止心跳检测
 */
function stopHeartbeat() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
  const indicator = document.getElementById('hc-heartbeat-indicator');
  if (indicator) indicator.remove();
}

// 页面加载时自动启动心跳（仅在顶层窗口，iframe 内跳过）
if (typeof window !== 'undefined' && window.self === window.top) {
  if (document.readyState === 'complete') {
    startHeartbeat();
  } else {
    window.addEventListener('load', startHeartbeat);
  }
}
