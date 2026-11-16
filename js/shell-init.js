/**
 * SPA 外壳初始化脚本
 * 在 index.html (SPA Shell) 中加载，负责初始化菜单、标签页
 */
;(function () {
  'use strict';

  // 标记当前为 SPA 外壳环境（menu.js 将据此拦截菜单点击）
  window.__IS_SPA_SHELL__ = true;

  function initShell() {
    // 渲染侧边栏 Logo
    if (typeof menuConfig !== 'undefined') {
      menuConfig.renderLogo();
      menuConfig.renderMenu('sidebar-menu');
      menuConfig.renderFooter();
    } else {
      console.warn('[Shell] menuConfig 未加载');
    }

    // 初始化标签管理器
    if (typeof TabManager !== 'undefined') {
      TabManager.init('tab-list', 'tab-content');
    } else {
      console.error('[Shell] TabManager 未加载');
    }

    // 处理侧边栏折叠/展开
    const toggle = document.querySelector('.header-toggle');
    if (toggle && typeof toggleSidebar === 'function') {
      // toggleSidebar 已经在 menu.js 中定义，onclick 已在 HTML 中绑定
    }
  }

  // DOM 就绪后执行
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initShell);
  } else {
    initShell();
  }
})();
