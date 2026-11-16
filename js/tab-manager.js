/**
 * 标签页管理器 - Tab Manager
 * 支持多标签页打开/关闭/切换，使用 iframe 加载页面内容
 * 状态持久化到 sessionStorage（延迟写入）
 */
;(function () {
  'use strict';

  const STORAGE_KEY = 'kejiju_tabs';
  const MAX_TABS = 10;
  const MIN_TABS = 1;

  const TabManager = {
    // DOM 引用
    tabListEl: null,
    contentAreaEl: null,

    // 状态
    tabs: [],
    activeTabId: null,
    iframeMap: {},

    // 延迟保存计时器
    _saveTimer: null,

    /**
     * 初始化标签管理器
     */
    init(tabListId, contentAreaId) {
      this.tabListEl = document.getElementById(tabListId);
      this.contentAreaEl = document.getElementById(contentAreaId);
      const initialHashId = window.location.hash.replace('#', '');
      if (!this.tabListEl || !this.contentAreaEl) {
        console.error('[TabManager] 找不到标签栏或内容区容器');
        return;
      }

      // 从 sessionStorage 恢复或创建默认标签
      if (!this.restoreState()) {
        this.openTab('dashboard', '首页', '/pages/dashboard.html', 'el-icon-s-platform', true);
      }

      // 支持直接访问 /index.html#merge 这类 hash 入口
      this._openTabByHash(initialHashId);

      // 监听 hash 变化 (浏览器回退/前进)
      window.addEventListener('hashchange', () => {
        this._openTabByHash(window.location.hash.replace('#', ''));
      });
    },

    /**
     * 打开（或切换到）一个标签页
     */
    openTab(id, title, path, icon, isDefault) {
      const existing = this.tabs.find(t => t.id === id);
      if (existing) {
        this.switchTab(id);
        return;
      }

      if (this.tabs.length >= MAX_TABS) {
        this._showToast(`最多同时打开 ${MAX_TABS} 个标签页`);
        return;
      }

      // 新增标签（先 push 再增量添加 DOM，避免全量重建）
      this.tabs.push({ id, title, path, icon, isDefault: !!isDefault });
      this._addTabElement(this.tabs[this.tabs.length - 1]);
      this.switchTab(id);
      this._scheduleSave();
    },

    /**
     * 切换到指定标签
     */
    switchTab(id) {
      // 隐藏当前显示的 iframe（仅隐藏一个，不遍历所有）
      if (this.activeTabId && this.iframeMap[this.activeTabId]) {
        this.iframeMap[this.activeTabId].style.display = 'none';
      }

      // 显示目标 iframe（懒创建）
      let iframe = this.iframeMap[id];
      if (!iframe) {
        const tab = this.tabs.find(t => t.id === id);
        if (!tab) return;

        iframe = document.createElement('iframe');
        iframe.className = 'tab-iframe';
        iframe.setAttribute('data-tab-id', id);

        iframe.addEventListener('load', () => {
          this.hideShellInIframe(iframe);
          iframe.classList.add('loaded');
        });

        iframe.src = tab.path;
        this.contentAreaEl.appendChild(iframe);
        this.iframeMap[id] = iframe;
      }

      iframe.style.display = 'block';
      this.activeTabId = id;

      // 更新标签栏视觉状态（只改 class，不重建 DOM）
      this._updateActiveState(id);
      this._updateMenuActiveState(id);
      this._updateBreadcrumb(id);

      const tab = this.tabs.find(t => t.id === id);
      if (tab) {
        document.title = `${tab.title} - 政策解读系统`;
      }

      window.location.hash = id;
      this._scheduleSave();
    },

    /**
     * 关闭指定标签
     */
    closeTab(id, event) {
      if (event) event.stopPropagation();

      if (this.tabs.length <= MIN_TABS) {
        this._showToast('至少保留一个标签页');
        return;
      }

      const tab = this.tabs.find(t => t.id === id);
      if (tab && tab.isDefault && this.tabs.length <= 1) {
        this._showToast('不能关闭首页标签');
        return;
      }

      const idx = this.tabs.findIndex(t => t.id === id);
      if (idx === -1) return;

      // 移除 iframe DOM
      if (this.iframeMap[id]) {
        this.iframeMap[id].remove();
        delete this.iframeMap[id];
      }

      // 移除标签 DOM（增量删除，不重建全部）
      const tabEl = this.tabListEl?.querySelector(`[data-tab-id="${id}"]`);
      if (tabEl) tabEl.remove();

      // 从数组中移除
      this.tabs.splice(idx, 1);

      // 如果关闭的是当前激活标签，切换到邻近标签
      if (this.activeTabId === id) {
        const nextIdx = Math.min(idx, this.tabs.length - 1);
        this.switchTab(this.tabs[nextIdx].id);
        // switchTab 内部已经更新了标签栏 active 状态
      } else {
        // 虽然 active 不变，但需刷新标签栏 active 状态
        this._updateActiveState(this.activeTabId);
      }

      this._scheduleSave();
    },

    /**
     * 增量添加一个标签 DOM 元素（不重建全部标签）
     */
    _addTabElement(tab) {
      if (!this.tabListEl) return;
      const div = document.createElement('div');
      div.className = `tab-item${this.activeTabId === tab.id ? ' active' : ''}`;
      div.dataset.tabId = tab.id;
      div.onclick = () => TabManager.switchTab(tab.id);
      div.innerHTML = `
        <i class="${tab.icon} tab-item-icon"></i>
        <span class="tab-item-title">${tab.title}</span>
        <span class="tab-close" onclick="TabManager.closeTab('${tab.id}', event)" title="关闭标签">×</span>
      `;
      this.tabListEl.appendChild(div);
    },

    /**
     * 在 iframe 加载完成后，隐藏页面内部的 sidebar/header
     */
    hideShellInIframe(iframe) {
      try {
        const doc = iframe.contentDocument || iframe.contentWindow.document;
        if (!doc || !doc.head) return;
        if (doc.getElementById('tab-iframe-hide-chrome')) return;

        const style = doc.createElement('style');
        style.id = 'tab-iframe-hide-chrome';
        style.textContent = `
          .sidebar, .sidebar-footer, .header, #hc-heartbeat-indicator { display: none !important; }
          .main-content { overflow: auto !important; min-height: auto !important; }
          .content-wrapper { min-height: auto !important; }
          html, body { height: 100%; margin: 0; padding: 0; }
        `;
        doc.head.appendChild(style);
      } catch (e) {
        console.warn('[TabManager] 无法访问 iframe 内容:', e.message);
      }
    },

    /**
     * 延迟保存标签状态（合并多次操作为一次写入）
     */
    _scheduleSave() {
      if (this._saveTimer) clearTimeout(this._saveTimer);
      this._saveTimer = setTimeout(() => {
        this._saveTimer = null;
        this._saveState();
      }, 300);
    },

    _saveState() {
      try {
        const data = {
          tabs: this.tabs.map(t => ({ id: t.id, title: t.title, path: t.path, icon: t.icon, isDefault: t.isDefault })),
          activeTabId: this.activeTabId
        };
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));
      } catch (e) { /* ignore */ }
    },

    restoreState() {
      try {
        const raw = sessionStorage.getItem(STORAGE_KEY);
        if (!raw) return false;

        const data = JSON.parse(raw);
        if (!data.tabs || !Array.isArray(data.tabs) || data.tabs.length === 0) return false;

        this.tabs = data.tabs.filter(tab => !this._isHiddenMenuTab(tab));
        if (this.tabs.length === 0) return false;
        this.activeTabId = this._isHiddenMenuTab({ id: data.activeTabId }) ? null : data.activeTabId;

        // 增量构建标签 DOM（逐个添加，不一次性 innerHTML）
        if (this.tabListEl) {
          this.tabListEl.innerHTML = '';
          this.tabs.forEach(tab => this._addTabElement(tab));
        }

        if (this.activeTabId && this.tabs.some(t => t.id === this.activeTabId)) {
          this.switchTab(this.activeTabId);
        } else {
          this.switchTab(this.tabs[0].id);
        }

        return true;
      } catch (e) {
        return false;
      }
    },

    // ========== 内部方法 ==========

    _updateActiveState(id) {
      if (!this.tabListEl) return;
      this.tabListEl.querySelectorAll('.tab-item').forEach(el => {
        el.classList.toggle('active', el.dataset.tabId === id);
      });
    },

    _updateBreadcrumb(id) {
      const tab = this.tabs.find(t => t.id === id);
      if (!tab) return;
      const bc = menuConfig?.getBreadcrumb(tab.path);
      if (bc && bc.length > 0 && typeof renderBreadcrumb === 'function') {
        renderBreadcrumb(tab.path);
      }
    },

    _updateMenuActiveState(id) {
      const tab = this.tabs.find(t => t.id === id);
      if (!tab) return;

      requestAnimationFrame(() => {
        document.querySelectorAll('.menu-item.active, .submenu-item.active').forEach(el => {
          el.classList.remove('active');
        });
        document.querySelectorAll(`[data-menu-path="${tab.path}"]`).forEach(el => {
          el.classList.add('active');
        });
        document.querySelectorAll('.menu-submenu').forEach(group => {
          const sub = group.querySelector('.submenu-items');
          const title = group.querySelector('.menu-submenu-title');
          if (group.querySelector('.submenu-item.active')) {
            title?.classList.add('expanded');
          }
          if (sub) sub.style.display = '';
        });
      });
    },

    _openTabByHash(hashId) {
      const targetId = String(hashId || '').trim();
      if (!targetId) return;

      if (this.tabs.some(t => t.id === targetId)) {
        if (this.activeTabId !== targetId) {
          this.switchTab(targetId);
        }
        return;
      }

      const menuItem = this._findMenuItemById(targetId);
      if (!menuItem || !menuItem.path || this._isHiddenMenuTab(menuItem)) return;

      this.openTab(
        menuItem.id,
        menuItem.title,
        menuItem.path,
        menuItem.icon || '',
        menuItem.id === 'dashboard'
      );
    },

    _findMenuItemById(targetId) {
      const items = Array.isArray(menuConfig?.items) ? menuConfig.items : [];
      for (const item of items) {
        if (item.id === targetId) return item;
        if (Array.isArray(item.children)) {
          const child = item.children.find(subItem => subItem.id === targetId);
          if (child) return child;
        }
      }
      return null;
    },

    _isHiddenMenuTab(tab) {
      const hiddenIds = new Set(menuConfig?.hiddenChildIds || []);
      return hiddenIds.has(tab?.id) || String(tab?.path || '').includes('/pages/base-data.html?type=policy_usage');
    },

    _showToast(msg) {
      if (window.ElementPlus && window.ElementPlus.ElMessage) {
        window.ElementPlus.ElMessage.warning(msg);
      } else if (window.ElMessage) {
        window.ElMessage.warning(msg);
      } else {
        alert(msg);
      }
    }
  };

  window.TabManager = TabManager;
})();
