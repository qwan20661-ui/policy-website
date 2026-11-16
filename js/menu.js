/**
 * 菜单配置 - 政策解读系统 V2
 * 企业级导航设计
 */

const menuConfig = {
  // 系统Logo配置
  logo: {
    title: '政策解读系统',
    subtitle: ''
  },

  // 获取基础路径
  getBasePath() {
    const path = window.location.pathname;
    if (path.includes('/pages/')) {
      return ''; // 已在 pages 目录下
    }
    return ''; // 从根目录访问
  },

  // 菜单数据 - 按业务模块分组 (使用绝对路径)
  items: [
    {
      id: 'dashboard',
      path: '/pages/dashboard.html',
      title: '首页',
      icon: 'el-icon-s-platform',
      group: 'main'
    },
    {
      id: 'policy-group',
      title: '政策管理',
      icon: 'el-icon-folder-opened',
      group: 'policy',
      children: [
        {
          id: 'crawler',
          path: '/pages/crawler.html',
          title: '政策数据获取',
          icon: 'el-icon-download'
        }
      ]
    },
    {
      id: 'annotation-group',
      title: '数据标注',
      icon: 'el-icon-edit',
      group: 'annotation',
      children: [
        {
          id: 'merge',
          path: '/pages/merge.html',
          title: '政策及附件标注',
          icon: 'el-icon-connection'
        },
        {
          id: 'policy-classification',
          path: '/pages/policy-classification.html',
          title: '政策归类标注',
          icon: 'el-icon-collection-tag'
        },
        {
          id: 'structure',
          path: '/pages/structure.html',
          title: '政策融合结构化',
          icon: 'el-icon-data-analysis'
        }
      ]
    },
    {
      id: 'enterprise-group',
      title: '政策推荐',
      icon: 'el-icon-office-building',
      group: 'enterprise',
      children: [
        {
          id: 'match',
          path: '/pages/match.html',
          title: '企业匹配政策',
          icon: 'el-icon-s-custom'
        }
      ]
    },
    // ========== 基础数据维护菜单组 (合并新增) ==========
    {
      id: 'base-data-group',
      title: '基础数据',
      icon: 'el-icon-document-copy',
      group: 'baseData',
      children: [
        {
          id: 'base-data-usage',
          path: '/pages/base-data.html?type=policy_usage',
          title: '场景用途管理',
          icon: 'el-icon-s-operation'
        },
        {
          id: 'base-data-keyword',
          path: '/pages/base-data.html?type=policy_keyword',
          title: '政策关键词管理',
          icon: 'el-icon-price-tag'
        },
        {
          id: 'base-data-object',
          path: '/pages/base-data.html?type=applicable_object',
          title: '适用对象管理',
          icon: 'el-icon-s-custom'
        },
        {
          id: 'base-data-support',
          path: '/pages/base-data.html?type=support_type',
          title: '支持方式管理',
          icon: 'el-icon-coin'
        },
        {
          id: 'base-data-scope',
          path: '/pages/base-data.html?type=applicable_scope',
          title: '适用范围管理',
          icon: 'el-icon-map-location'
        },
        {
          id: 'base-data-condition',
          path: '/pages/base-data.html?type=application_condition',
          title: '申报条件管理',
          icon: 'el-icon-document-checked'
        }
      ]
    },
    {
      id: 'system-group',
      title: '系统管理',
      icon: 'el-icon-setting',
      group: 'system',
      children: [
        {
          id: 'setting',
          path: '/pages/setting.html',
          title: '系统设置',
          icon: 'el-icon-tools'
        }
      ]
    }
  ],

  // 分组标题
  groupTitles: {
    main: '主导航',
    policy: '政策管理',
    annotation: '数据标注',
    enterprise: '企业服务',
    system: '系统管理'
  },

  hiddenChildIds: ['base-data-keyword', 'base-data-usage'],
  defaultBaseDataType: 'applicable_object',

  // 获取当前页面路径 (含路径和查询参数)
  getCurrentPath() {
    // 返回完整路径，包含 pathname + search
    // 例如: /pages/base-data.html?type=policy_usage
    const path = window.location.pathname;
    const search = window.location.search;
    return path + search;
  },

  // 判断当前页面是否为 base-data.html
  isBaseDataPage() {
    const path = window.location.pathname;
    return path.endsWith('base-data.html');
  },

  // 获取 base-data.html 的类型参数
  getBaseDataType() {
    const params = new URLSearchParams(window.location.search);
    const requestedType = params.get('type') || this.defaultBaseDataType;
    return requestedType === 'policy_usage' ? this.defaultBaseDataType : requestedType;
  },

  isHiddenChild(child) {
    return (this.hiddenChildIds || []).includes(child?.id);
  },

  // 判断菜单项是否匹配当前页面 (支持精确匹配和 base-data 模糊匹配)
  isMenuItemActive(menuPath) {
    const currentPath = this.getCurrentPath();

    // 精确匹配
    if (menuPath === currentPath) {
      return true;
    }

    // base-data.html 特殊匹配: 只要当前页面是 base-data.html 就算激活
    // 但要确保 URL 参数 type 匹配
    if (this.isBaseDataPage()) {
      const menuUrl = new URL(menuPath, window.location.origin);
      const menuType = menuUrl.searchParams.get('type');
      const currentType = this.getBaseDataType();
      return menuType === currentType;
    }

    return false;
  },

  // 判断父菜单是否有激活的子菜单 (用于展开状态)
  isGroupActive(children) {
    if (!children) return false;
    return children.some(child => this.isMenuItemActive(child.path));
  },

  // 获取面包屑
  getBreadcrumb(path) {
    const breadcrumb = [{ title: '首页', path: '/pages/dashboard.html' }];

    for (const item of this.items) {
      if (item.children) {
        const child = item.children.find(c => !this.isHiddenChild(c) && c.path === path);
        if (child) {
          breadcrumb.push({ title: item.title, path: '' });
          breadcrumb.push({ title: child.title, path: child.path });
          return breadcrumb;
        }
      }
      if (item.path === path) {
        breadcrumb.push({ title: item.title, path: item.path });
        return breadcrumb;
      }
    }
    return breadcrumb;
  },

  // 渲染Logo
  renderLogo() {
    const logoEl = document.querySelector('.sidebar-logo');
    if (!logoEl) return;

    logoEl.innerHTML = `
      <div class="logo-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
          <polyline points="14 2 14 8 20 8"></polyline>
          <line x1="16" y1="13" x2="8" y2="13"></line>
          <line x1="16" y1="17" x2="8" y2="17"></line>
          <polyline points="10 9 9 9 8 9"></polyline>
        </svg>
      </div>
      <div class="logo-text">
        <div class="logo-title">${this.logo.title}</div>
        <div class="logo-subtitle">${this.logo.subtitle}</div>
      </div>
    `;
  },

  // 渲染菜单
  renderMenu(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const currentPath = this.getCurrentPath();
    const hiddenChildIds = new Set(this.hiddenChildIds || []);
    let html = '';

    this.items.forEach(item => {
      if (item.children) {
        // 有子菜单
        const visibleChildren = item.children.filter(child => !hiddenChildIds.has(child.id));
        const hasActive = this.isGroupActive(visibleChildren);
        html += `
          <div class="menu-submenu">
            <div class="menu-submenu-title ${hasActive ? 'expanded' : ''}">
              <span class="menu-icon"><i class="${item.icon}"></i></span>
              <span>${item.title}</span>
              <i class="el-icon-arrow-right arrow"></i>
            </div>
            <div class="submenu-items ${hasActive ? 'expanded' : ''}">
              ${visibleChildren.map(child => {
                const isActive = this.isMenuItemActive(child.path);
                return `
                  <a href="${child.path}"
                     class="submenu-item ${isActive ? 'active' : ''}"
                     data-menu-id="${child.id}"
                     data-menu-path="${child.path}"
                     data-menu-title="${child.title}"
                     data-menu-icon="${child.icon}">
                    <span class="menu-icon"><i class="${child.icon}"></i></span>
                    <span>${child.title}</span>
                  </a>
                `;
              }).join('')}
            </div>
          </div>
        `;
      } else if (item.path) {
        // 主菜单项
        const isActive = this.isMenuItemActive(item.path);
        html += `
          <a href="${item.path}"
             class="menu-item ${isActive ? 'active' : ''}"
             data-menu-id="${item.id}"
             data-menu-path="${item.path}"
             data-menu-title="${item.title}"
             data-menu-icon="${item.icon}">
            <span class="menu-icon"><i class="${item.icon}"></i></span>
            <span>${item.title}</span>
          </a>
        `;
      }
    });

    container.innerHTML = html;

    // 使用事件委托处理子菜单点击（用 class 控制，不设 inline style）
    container.querySelectorAll('.menu-submenu-title').forEach(function(title) {
      title.addEventListener('click', function(e) {
        if (e.target.tagName === 'A') return;
        const submenu = this.nextElementSibling;
        if (submenu) submenu.style.display = '';
        this.classList.toggle('expanded');
      });
    });

    // 点击委托：捕获所有菜单项点击，通过 TabManager 打开标签页（仅 SPA 外壳）
    container.addEventListener('click', function(e) {
      const item = e.target.closest('[data-menu-path]');
      if (!item || !window.TabManager || !window.__IS_SPA_SHELL__) return;

      e.preventDefault();
      TabManager.openTab(
        item.dataset.menuId || item.dataset.menuTitle,
        item.dataset.menuTitle,
        item.dataset.menuPath,
        item.dataset.menuIcon || ''
      );
    });
  },

  // 渲染底部
  renderFooter() {
    const footerEl = document.querySelector('.sidebar-footer');
    if (!footerEl) return;

    footerEl.innerHTML = `
      <div class="sidebar-footer-item" onclick="toggleSidebar()" title="收起/展开侧边栏">
        <i class="el-icon-s-fold"></i>
        <span>收起菜单</span>
      </div>
    `;
  }
};

// 切换侧边栏
function toggleSidebar() {
  const sidebar = document.querySelector('.sidebar');
  const footerItem = document.querySelector('.sidebar-footer-item i');
  const footerText = document.querySelector('.sidebar-footer-item span');
  const isNarrow = window.innerWidth <= 1080;

  if (isNarrow) {
    // 窄屏模式：滑入/滑出
    sidebar.classList.toggle('open');
    let overlay = document.querySelector('.sidebar-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.className = 'sidebar-overlay';
      overlay.addEventListener('click', closeSidebar);
      document.body.appendChild(overlay);
    }
    overlay.classList.toggle('show');
    return;
  }

  // 宽屏模式：折叠/展开
  sidebar.classList.toggle('collapsed');
  if (sidebar.classList.contains('collapsed')) {
    if (footerText) footerText.textContent = '展开菜单';
    if (footerItem) footerItem.className = 'el-icon-s-unfold';
  } else {
    if (footerText) footerText.textContent = '收起菜单';
    if (footerItem) footerItem.className = 'el-icon-s-fold';
  }
}

function closeSidebar() {
  const sidebar = document.querySelector('.sidebar');
  const overlay = document.querySelector('.sidebar-overlay');
  if (sidebar) sidebar.classList.remove('open');
  if (overlay) overlay.classList.remove('show');
}

// 渲染面包屑
function renderBreadcrumb(path) {
  const breadcrumb = menuConfig.getBreadcrumb(path);
  const container = document.querySelector('.header-breadcrumb');

  if (!container) return;

  container.innerHTML = breadcrumb.map((item, index) => {
    if (index === breadcrumb.length - 1) {
      return `<span class="current">${item.title}</span>`;
    }
    return `
      <a href="${item.path || 'javascript:void(0);'}">${item.title}</a>
      <span class="separator">/</span>
    `;
  }).join('');
}

async function initCurrentUser() {
  // 在 iframe 中跳过用户初始化（由外壳统一处理）
  if (window.self !== window.top) return;

  const MAX_RETRIES = 2;
  const RETRY_DELAY = 1500;

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 10000);
      const response = await fetch('/api/auth/session', {
        credentials: 'same-origin',
        signal: controller.signal
      });
      clearTimeout(timeoutId);
      const result = await response.json();
      if (!result.success) {
        // 401 — 确实未登录，才跳登录页
        window.location.replace(`/login.html?redirect=${encodeURIComponent(window.location.pathname + window.location.search)}`);
        return;
      }

      const user = result.data || {};
      const displayName = user.realName || user.username || '用户';
      const roleNames = { admin: '系统管理员', manager: '业务管理员', enterprise: '企业用户' };
      document.querySelectorAll('.header-user').forEach(el => {
        const avatar = el.querySelector('.avatar');
        const name = el.querySelector('.name');
        const role = el.querySelector('.role');
        if (avatar) avatar.textContent = displayName.slice(0, 1);
        if (name) name.textContent = displayName;
        if (role) role.textContent = roleNames[user.role] || user.role || '系统用户';
        el.style.cursor = 'pointer';
        el.title = '点击退出登录';
        el.addEventListener('click', async () => {
          if (!window.confirm('确定退出当前账号吗？')) return;
          await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
          window.location.replace('/login.html');
        });
      });

      const welcomeTitle = document.querySelector('.welcome-section h2');
      if (welcomeTitle) welcomeTitle.textContent = `您好，${displayName}`;
      return; // 成功
    } catch (err) {
      // 最后尝试也失败，才跳登录页
      if (attempt >= MAX_RETRIES) {
        window.location.replace('/login.html');
      } else {
        console.warn(`[initCurrentUser] 第 ${attempt + 1} 次失败，${RETRY_DELAY}ms 后重试:`, err.message);
        await new Promise(r => setTimeout(r, RETRY_DELAY));
      }
    }
  }
}

if (typeof window !== 'undefined' && window.self === window.top) initCurrentUser();

// 导出
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { menuConfig, toggleSidebar, renderBreadcrumb };
}
