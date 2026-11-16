/**
 * 应用主逻辑
 * 政策解读系统
 */

// 初始化应用
document.addEventListener('DOMContentLoaded', function() {
  initApp();
});

function initApp() {
  const currentPath = menuConfig.getCurrentPath();

  // 渲染Logo
  menuConfig.renderLogo();

  // 渲染菜单
  if (document.getElementById('sidebar-menu')) {
    menuConfig.renderMenu('sidebar-menu');
  }

  // 渲染底部
  if (document.getElementById('sidebar-footer')) {
    menuConfig.renderFooter();
  }

  // 渲染面包屑
  if (document.querySelector('.header-breadcrumb')) {
    renderBreadcrumb(currentPath);
  }

  // 初始化页面特定逻辑
  initPageLogic();
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

// 页面特定逻辑
function initPageLogic() {
  const path = getCurrentPath();

  // 根据页面初始化不同组件
  if (path.includes('dashboard')) {
    initDashboard();
  } else if (path.includes('crawler')) {
    initCrawler();
  } else if (path.includes('category')) {
    initCategory();
  } else if (path.includes('statistics')) {
    initStatistics();
  }
}

// 初始化首页
function initDashboard() {
  // 渲染统计卡片
  renderStatCards();

  // 渲染快捷入口
  renderQuickAccess();

  // 渲染近期动态
  renderActivities();

  // 渲染待办事项
  renderTodos();

  // 渲染图表
  initCharts();
}

// 渲染统计卡片
function renderStatCards() {
  const container = document.getElementById('stat-cards');
  if (!container) return;

  const stats = mockData.statistics;
  container.innerHTML = `
    <div class="stat-card fade-in">
      <div class="stat-card-icon blue">
        <i class="el-icon-document"></i>
      </div>
      <div class="stat-card-content">
        <div class="stat-card-value">${stats.totalPolicies.toLocaleString()}</div>
        <div class="stat-card-label">政策总数</div>
        <div class="stat-card-trend up"><i class="el-icon-top"></i> 12.5% 较上月</div>
      </div>
    </div>
    <div class="stat-card fade-in" style="animation-delay: 0.1s">
      <div class="stat-card-icon green">
        <i class="el-icon-download"></i>
      </div>
      <div class="stat-card-content">
        <div class="stat-card-value">${stats.todayCrawled}</div>
        <div class="stat-card-label">今日爬取</div>
        <div class="stat-card-trend up"><i class="el-icon-top"></i> 8条 实时</div>
      </div>
    </div>
    <div class="stat-card fade-in" style="animation-delay: 0.2s">
      <div class="stat-card-icon orange">
        <i class="el-icon-collection-tag"></i>
      </div>
      <div class="stat-card-content">
        <div class="stat-card-value">${stats.taggedCount.toLocaleString()}</div>
        <div class="stat-card-label">已标注数</div>
        <div class="stat-card-trend">占比 70.9%</div>
      </div>
    </div>
    <div class="stat-card fade-in" style="animation-delay: 0.3s">
      <div class="stat-card-icon red">
        <i class="el-icon-s-custom"></i>
      </div>
      <div class="stat-card-content">
        <div class="stat-card-value">${stats.matchedCount.toLocaleString()}</div>
        <div class="stat-card-label">已匹配企业</div>
        <div class="stat-card-trend up"><i class="el-icon-top"></i> 36.3% 较上月</div>
      </div>
    </div>
  `;
}

// 渲染快捷入口
function renderQuickAccess() {
  const container = document.getElementById('quick-access');
  if (!container) return;

  container.innerHTML = `
    <a href="pages/crawler.html" class="quick-access-item">
      <i class="el-icon-download"></i>
      <span>政策爬取</span>
      <small>数据采集管理</small>
    </a>
    <a href="pages/merge.html" class="quick-access-item">
      <i class="el-icon-connection"></i>
      <span>融合标注</span>
      <small>关联关系管理</small>
    </a>
    <a href="pages/match.html" class="quick-access-item">
      <i class="el-icon-s-custom"></i>
      <span>企业匹配</span>
      <small>精准匹配服务</small>
    </a>
  `;
}

// 渲染近期动态
function renderActivities() {
  const container = document.getElementById('activities');
  if (!container) return;

  container.innerHTML = mockData.activities.map(activity => `
    <div class="activity-item fade-in">
      <div class="activity-icon ${activity.type}">
        <i class="el-icon-${activity.type === 'crawl' ? 'download' : activity.type === 'classify' ? 'collection-tag' : activity.type === 'merge' ? 'connection' : 'data-analysis'}"></i>
      </div>
      <div class="activity-content">
        <div class="activity-title">${activity.title}</div>
        <div class="activity-time">${activity.time}</div>
      </div>
    </div>
  `).join('');
}

// 渲染待办事项
function renderTodos() {
  const container = document.getElementById('todos');
  if (!container) return;

  container.innerHTML = mockData.todos.map(todo => `
    <div class="todo-item ${todo.completed ? 'completed' : ''}">
      <div class="todo-checkbox ${todo.completed ? 'checked' : ''}" onclick="toggleTodo(${todo.id})">
        ${todo.completed ? '<i class="el-icon-check"></i>' : ''}
      </div>
      <div class="todo-content">
        <div class="todo-title">${todo.title}</div>
        <div class="todo-meta">截止: ${todo.deadline}</div>
      </div>
    </div>
  `).join('');
}

// 切换待办状态
function toggleTodo(id) {
  const todo = mockData.todos.find(t => t.id === id);
  if (todo) {
    todo.completed = !todo.completed;
    renderTodos();
  }
}

// 初始化图表
function initCharts() {
  // 趋势图
  const trendChart = document.getElementById('trend-chart');
  if (trendChart && typeof echarts !== 'undefined') {
    const trendChartInstance = echarts.init(trendChart);
    const option = {
      tooltip: {
        trigger: 'axis'
      },
      grid: {
        left: '3%',
        right: '4%',
        bottom: '3%',
        top: '10%',
        containLabel: true
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: mockData.trendData.map(d => d.date)
      },
      yAxis: {
        type: 'value'
      },
      series: [
        {
          name: '政策数量',
          type: 'line',
          smooth: true,
          areaStyle: {
            color: {
              type: 'linear',
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(22, 93, 255, 0.3)' },
                { offset: 1, color: 'rgba(22, 93, 255, 0.05)' }
              ]
            }
          },
          lineStyle: {
            color: '#165dff',
            width: 2
          },
          itemStyle: {
            color: '#165dff'
          },
          data: mockData.trendData.map(d => d.count)
        }
      ]
    };
    trendChartInstance.setOption(option);

    window.addEventListener('resize', () => {
      trendChartInstance.resize();
    });
  }

  // 分类饼图
  const categoryChart = document.getElementById('category-chart');
  if (categoryChart && typeof echarts !== 'undefined') {
    const categoryChartInstance = echarts.init(categoryChart);
    const option = {
      tooltip: {
        trigger: 'item',
        formatter: '{b}: {c} ({d}%)'
      },
      legend: {
        orient: 'vertical',
        right: '10%',
        top: 'center'
      },
      color: ['#165dff', '#4080ff', '#79b8ff', '#adc6ff', '#d0dff5'],
      series: [
        {
          name: '政策分类',
          type: 'pie',
          radius: ['40%', '70%'],
          center: ['35%', '50%'],
          avoidLabelOverlap: false,
          itemStyle: {
            borderRadius: 4,
            borderColor: '#fff',
            borderWidth: 2
          },
          label: {
            show: false
          },
          emphasis: {
            label: {
              show: true,
              fontSize: 14,
              fontWeight: 'bold'
            }
          },
          data: mockData.categoryDistribution
        }
      ]
    };
    categoryChartInstance.setOption(option);

    window.addEventListener('resize', () => {
      categoryChartInstance.resize();
    });
  }
}

// 初始化爬取页面
function initCrawler() {
  renderPolicyTable();
}

// 初始化分类页面
function initCategory() {
  renderCategoryTable();
}

// 初始化统计页面
function initStatistics() {
  initFullCharts();
}

// 渲染政策列表表格
function renderPolicyTable() {
  const tbody = document.getElementById('policy-table-body');
  if (!tbody) return;

  tbody.innerHTML = mockData.policies.map(policy => `
    <tr>
      <td>${policy.id}</td>
      <td><a href="javascript:void(0);" onclick="showPolicyDetail(${policy.id})">${policy.title}</a></td>
      <td>${formatDate(policy.publishDate)}</td>
      <td>${policy.department}</td>
      <td>${getStatusTag(policy.status)}</td>
      <td>
        <a href="javascript:void(0);" class="el-button el-button--text el-button--small" onclick="viewAttachment('${policy.attachment}')">
          <i class="el-icon-view"></i> 查看
        </a>
      </td>
    </tr>
  `).join('');
}

// 渲染分类表格
function renderCategoryTable() {
  const tbody = document.getElementById('category-table-body');
  if (!tbody) return;

  tbody.innerHTML = mockData.policies.map(policy => `
    <tr>
      <td>${policy.id}</td>
      <td><a href="javascript:void(0);" onclick="showPolicyDetail(${policy.id})">${policy.title}</a></td>
      <td>${formatDate(policy.publishDate)}</td>
      <td>${policy.department}</td>
      <td>${getTypeTag(policy.type)}</td>
      <td>${policy.attachmentType || '-'}</td>
      <td>${getStatusTag(policy.status)}</td>
      <td>
        <el-button type="primary" size="small" onclick="editCategory(${policy.id})">编辑</el-button>
      </td>
    </tr>
  `).join('');
}

// 显示政策详情
function showPolicyDetail(id) {
  const policy = mockData.policies.find(p => p.id === id);
  if (!policy) return;

  // 显示详情抽屉
  const drawer = document.getElementById('detail-drawer');
  if (drawer) {
    drawer.style.display = 'block';
    drawer.classList.add('show');

    const content = document.getElementById('detail-content');
    if (content) {
      content.innerHTML = `
        <h2 style="text-align:center;margin-bottom:20px;">${policy.title}</h2>
        <div class="detail-item">
          <div class="detail-label">发布部门</div>
          <div class="detail-value">${policy.department}</div>
        </div>
        <div class="detail-item">
          <div class="detail-label">发布日期</div>
          <div class="detail-value">${formatDate(policy.publishDate)}</div>
        </div>
        <div class="detail-item">
          <div class="detail-label">政策类型</div>
          <div class="detail-value">${policy.type}</div>
        </div>
        <div class="detail-item">
          <div class="detail-label">附件类型</div>
          <div class="detail-value">${policy.attachmentType || '-'}</div>
        </div>
        <div class="detail-item">
          <div class="detail-label">标注状态</div>
          <div class="detail-value">${getStatusTag(policy.status)}</div>
        </div>
        ${policy.abstract ? `
        <div class="detail-item">
          <div class="detail-label">政策摘要</div>
          <div class="detail-value">${policy.abstract}</div>
        </div>
        ` : ''}
      `;
    }
  }
}

// 关闭抽屉
function closeDrawer() {
  const drawer = document.getElementById('detail-drawer');
  if (drawer) {
    drawer.classList.remove('show');
    setTimeout(() => {
      drawer.style.display = 'none';
    }, 300);
  }
}

// 初始化完整统计图表
function initFullCharts() {
  // 政策趋势图
  const mainTrendChart = document.getElementById('main-trend-chart');
  if (mainTrendChart && typeof echarts !== 'undefined') {
    const chart = echarts.init(mainTrendChart);
    chart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['爬取数量', '标注数量', '匹配数量'] },
      grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
      xAxis: {
        type: 'category',
        data: ['1月', '2月', '3月', '4月', '5月', '6月']
      },
      yAxis: { type: 'value' },
      series: [
        {
          name: '爬取数量',
          type: 'line',
          data: [1200, 1350, 1580, 1420, 1680, 1850],
          smooth: true,
          lineStyle: { color: '#165dff' },
          itemStyle: { color: '#165dff' }
        },
        {
          name: '标注数量',
          type: 'line',
          data: [800, 920, 1100, 980, 1200, 1380],
          smooth: true,
          lineStyle: { color: '#67c23a' },
          itemStyle: { color: '#67c23a' }
        },
        {
          name: '匹配数量',
          type: 'line',
          data: [300, 450, 580, 620, 800, 950],
          smooth: true,
          lineStyle: { color: '#e6a23c' },
          itemStyle: { color: '#e6a23c' }
        }
      ]
    });

    window.addEventListener('resize', () => chart.resize());
  }

  // 分类分布图
  const categoryPieChart = document.getElementById('category-pie-chart');
  if (categoryPieChart && typeof echarts !== 'undefined') {
    const chart = echarts.init(categoryPieChart);
    chart.setOption({
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { orient: 'vertical', right: '5%', top: 'center' },
      color: ['#165dff', '#4080ff', '#79b8ff', '#adc6ff', '#d0d5df'],
      series: [{
        type: 'pie',
        radius: ['45%', '70%'],
        center: ['35%', '50%'],
        itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
        label: { show: false },
        data: mockData.categoryDistribution
      }]
    });

    window.addEventListener('resize', () => chart.resize());
  }
}

// 开始爬取
function startCrawl() {
  if (typeof ElMessage !== 'undefined') {
    ElMessage.success('开始爬取政策数据...');
  } else {
    alert('开始爬取政策数据...');
  }
}

// 导出
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { initApp, renderBreadcrumb, toggleTodo, showPolicyDetail, closeDrawer, startCrawl };
}
