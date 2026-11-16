/**
 * 独立页面初始化工具
 * 用于所有非 SPA 外壳页面，统一菜单、面包屑和进入动画初始化
 */
;(function () {
  'use strict';

  function initStandalonePage() {
    if (typeof menuConfig === 'undefined') {
      console.warn('[PageInit] menuConfig 未加载');
      return;
    }

    menuConfig.renderLogo();
    menuConfig.renderMenu('sidebar-menu');
    menuConfig.renderFooter();

    if (typeof renderBreadcrumb === 'function') {
      renderBreadcrumb(menuConfig.getCurrentPath());
    }

    const animatedRoot = document.querySelector(
      '.content-wrapper, .classification-page, .fusion-page, .merge-shell, .page-wrapper'
    );
    if (animatedRoot) {
      requestAnimationFrame(() => animatedRoot.classList.add('fade-in'));
    }
  }

  window.PageInit = {
    initStandalonePage
  };
})();
