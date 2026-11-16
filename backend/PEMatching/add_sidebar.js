const fs = require('fs');
let html = fs.readFileSync('e:/PEMatching/llm_matching_results.html', 'utf8');

// 1. 添加侧边栏样式
const sidebarStyles = `
        /* Sidebar Styles */
        .sidebar {
            position: fixed;
            left: 0;
            top: 0;
            width: 280px;
            height: 100vh;
            background: linear-gradient(180deg, #1C1B1F 0%, #2D2C32 100%);
            color: white;
            z-index: 50;
            display: flex;
            flex-direction: column;
            transition: transform 0.3s ease;
        }
        .sidebar-header {
            padding: 20px;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }
        .sidebar-title {
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 4px;
        }
        .sidebar-subtitle {
            font-size: 0.75rem;
            color: rgba(255,255,255,0.6);
        }
        .sidebar-stats {
            padding: 16px 20px;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }
        .sidebar-stat {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 6px 0;
            font-size: 0.85rem;
        }
        .sidebar-stat-label {
            color: rgba(255,255,255,0.7);
        }
        .sidebar-stat-value {
            font-weight: 600;
        }
        .sidebar-ents {
            flex: 1;
            overflow-y: auto;
            padding: 12px 0;
        }
        .sidebar-ent {
            display: flex;
            align-items: center;
            padding: 12px 20px;
            cursor: pointer;
            transition: all 0.2s;
            border-left: 3px solid transparent;
        }
        .sidebar-ent:hover {
            background: rgba(255,255,255,0.08);
        }
        .sidebar-ent.active {
            background: rgba(103,80,164,0.3);
            border-left-color: #BB86FC;
        }
        .sidebar-ent-icon {
            width: 36px;
            height: 36px;
            border-radius: 10px;
            background: rgba(255,255,255,0.1);
            display: flex;
            align-items: center;
            justify-content: center;
            margin-right: 12px;
            font-size: 0.9rem;
        }
        .sidebar-ent.active .sidebar-ent-icon {
            background: #6750A4;
        }
        .sidebar-ent-info {
            flex: 1;
            min-width: 0;
        }
        .sidebar-ent-name {
            font-size: 0.85rem;
            font-weight: 500;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .sidebar-ent-meta {
            font-size: 0.7rem;
            color: rgba(255,255,255,0.5);
            margin-top: 2px;
        }
        .sidebar-ent-stars {
            font-size: 0.7rem;
            margin-top: 2px;
        }
        .sidebar-ent-star-full { color: #FFD700; }
        .sidebar-ent-star-empty { color: rgba(255,255,255,0.2); }

        /* Main content area */
        .main-wrapper {
            margin-left: 280px;
            min-height: 100vh;
        }

        /* Mobile toggle button */
        .sidebar-toggle {
            display: none;
            position: fixed;
            bottom: 20px;
            right: 20px;
            width: 56px;
            height: 56px;
            border-radius: 50%;
            background: #6750A4;
            color: white;
            z-index: 60;
            align-items: center;
            justify-content: center;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            cursor: pointer;
        }

        @media (max-width: 900px) {
            .sidebar {
                transform: translateX(-100%);
            }
            .sidebar.open {
                transform: translateX(0);
            }
            .main-wrapper {
                margin-left: 0;
            }
            .sidebar-toggle {
                display: flex;
            }
        }
`;

// 在 </style> 前插入侧边栏样式
html = html.replace('    </style>', sidebarStyles + '\n    </style>');

// 2. 修改 body 结构 - 添加侧边栏和主内容区
const newBody = `<!-- Sidebar -->
    <aside class="sidebar" id="sidebar">
        <div class="sidebar-header">
            <div class="sidebar-title">政策匹配分析</div>
            <div class="sidebar-subtitle">企业-政策智能匹配系统</div>
        </div>
        <div class="sidebar-stats" id="sidebarStats"></div>
        <div class="sidebar-ents" id="sidebarEnts"></div>
    </aside>

    <!-- Mobile Toggle -->
    <button class="sidebar-toggle" onclick="toggleSidebar()">
        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path>
        </svg>
    </button>

    <!-- Main Content Wrapper -->
    <div class="main-wrapper">
        <div class="blur-shape blur-1"></div>
        <div class="blur-shape blur-2"></div>
        <div class="blur-shape blur-3"></div>

        <!-- Header -->
        <header class="relative z-10 backdrop-blur-md bg-md-surface/80 border-b-2 border-md-on-surface">
            <div class="max-w-7xl mx-auto px-6 py-4">
                <div class="flex items-center justify-between flex-wrap gap-3">
                    <div class="flex items-center gap-4">
                        <div class="w-12 h-12 rounded-full bg-md-primary flex items-center justify-center">
                            <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                            </svg>
                        </div>
                        <div>
                            <h1 class="text-xl font-medium text-md-on-surface">科小企业政策匹配分析报告</h1>
                            <p class="text-sm text-md-on-surface-variant">基于AI智能匹配的申报推荐</p>
                        </div>
                    </div>
                    <span class="px-4 py-2 rounded-full bg-md-secondary-container text-md-on-secondary-container text-sm font-medium">
                        LLM分析结果
                    </span>
                </div>
            </div>
        </header>

        <!-- Control Panel -->
        <div class="relative z-10 max-w-7xl mx-auto px-6 py-6">
            <div class="bg-white border-2 border-md-on-surface rounded-[24px] p-5 shadow-sm">
                <!-- Row 1: Search + Enterprise Select -->
                <div class="filter-row mb-3">
                    <div class="search-input-wrap">
                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
                        </svg>
                        <input type="text" id="searchInput" placeholder="搜索企业名称、政策名称、行业关键词...">
                        <button class="search-clear" id="searchClear" onclick="clearSearch()">×</button>
                    </div>
                    <select id="entFilterSelect" class="ent-select">
                        <option value="all">全部企业</option>
                    </select>
                </div>

                <!-- Row 2: All Filters -->
                <div class="filter-row">
                    <span class="text-sm font-medium text-md-on-surface-variant">星级:</span>
                    <button class="filter-chip active" data-score="all">全部</button>
                    <button class="filter-chip" data-score="s5">五星 (90+)</button>
                    <button class="filter-chip" data-score="s4">四星 (80-89)</button>
                    <button class="filter-chip" data-score="s3">三星 (70-79)</button>
                    <button class="filter-chip" data-score="s2">二星 (60-69)</button>
                    <button class="filter-chip" data-score="s1">一星 (50-59)</button>
                    <button class="filter-chip" data-score="s0">不推荐 (&lt;50)</button>

                    <span class="w-px h-6 bg-gray-200 mx-1 hidden sm:block"></span>

                    <span class="text-sm font-medium text-md-on-surface-variant">排序:</span>
                    <button class="sort-btn active" data-sort="score-desc" title="评分从高到低">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4h13M3 8h9M3 12h5m4 0l4-4m0 0l4 4m-4-4v12"/></svg>
                        评分↓
                    </button>
                    <button class="sort-btn" data-sort="score-asc" title="评分从低到高">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4h13M3 8h9M3 12h5m4 0l4 4m0 0l4-4m-4-4v12"/></svg>
                        评分↑
                    </button>
                    <button class="sort-btn" data-sort="ent-score" title="按企业最佳匹配评分排序">企业分↓</button>
                    <button class="sort-btn" data-sort="name" title="按政策名称排序">A→Z</button>

                    <span class="w-px h-6 bg-gray-200 mx-1 hidden sm:block"></span>

                    <span class="text-sm font-medium text-md-on-surface-variant">显示:</span>
                    <button class="top-chip" data-view="top90" title="仅显示A级(90分+)TOP推荐">TOP推荐</button>
                    <button class="filter-chip active" data-view="top">仅推荐(≥70)</button>
                    <button class="filter-chip" data-view="all">全部政策</button>
                </div>

                <!-- Result count bar -->
                <div class="flex items-center justify-between mt-4 pt-3 border-t border-dashed border-gray-200">
                    <span class="result-count" id="resultCount"></span>
                    <button class="text-sm text-md-primary hover:underline cursor-pointer btn-material" onclick="resetAll()">重置所有筛选</button>
                </div>
            </div>
        </div>

        <!-- Stats -->
        <div class="relative z-10 max-w-7xl mx-auto px-6 py-4">
            <div class="grid grid-cols-2 md:grid-cols-5 gap-4" id="statsBar"></div>
        </div>

        <!-- File Picker -->
        <div id="filePicker" class="relative z-10 max-w-7xl mx-auto px-6 py-12 hidden">
            <div class="bg-md-surface-container rounded-[32px] p-12 text-center">
                <div class="w-20 h-20 rounded-full bg-md-primary/10 flex items-center justify-center mx-auto mb-6">
                    <svg class="w-10 h-10 text-md-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"></path>
                    </svg>
                </div>
                <h2 class="text-xl font-medium text-md-on-surface mb-2">请加载数据文件</h2>
                <p class="text-sm text-md-on-surface-variant mb-6">由于浏览器安全限制，请选择 <code class="bg-md-surface-container-low px-2 py-1 rounded">llm_matching_results.json</code> 文件</p>
                <label class="inline-flex items-center gap-2 px-8 py-3 rounded-full bg-md-primary text-white font-medium cursor-pointer btn-material">
                    选择 JSON 文件
                    <input type="file" accept=".json" class="hidden" onchange="loadLocalFile(event)">
                </label>
            </div>
        </div>

        <!-- Main Content -->
        <main class="relative z-10 max-w-7xl mx-auto px-6 pb-12" id="mainContent"></main>

        <!-- Modal -->
        <div id="detailModal" class="fixed inset-0 z-[100] hidden">
            <div class="absolute inset-0 bg-black/40 backdrop-blur-sm" onclick="closeModal()"></div>
            <div class="absolute inset-4 md:inset-8 bg-md-surface rounded-[32px] overflow-hidden shadow-2xl flex flex-col">
                <div class="flex items-center justify-between p-6 border-b-2 border-md-outline/20">
                    <h2 class="text-xl font-medium" id="modalTitle">匹配详情</h2>
                    <button onclick="closeModal()" class="w-10 h-10 rounded-full bg-md-surface-container hover:bg-md-surface-container-low flex items-center justify-center btn-material">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                        </svg>
                    </button>
                </div>
                <div class="flex-1 overflow-y-auto p-6" id="modalContent"></div>
            </div>
        </div>
    </div>
`;

html = html.replace(
    /<body class="relative">[\s\S]*?<!-- Header -->/,
    newBody
);

// 3. 添加侧边栏渲染函数和切换函数
const sidebarFunctions = `
// ========== Sidebar Functions ==========
function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}

function renderSidebar() {
    // Stats
    const stats = computeStats(allData);
    document.getElementById('sidebarStats').innerHTML = '<div class="sidebar-stat"><span class="sidebar-stat-label">企业总数</span><span class="sidebar-stat-value">' + stats.totalEnterprises + '</span></div><div class="sidebar-stat"><span class="sidebar-stat-label">五星推荐</span><span class="sidebar-stat-value" style="color:#FFD700">' + stats.totalStar5 + '</span></div><div class="sidebar-stat"><span class="sidebar-stat-label">四星建议</span><span class="sidebar-stat-value" style="color:#4CAF50">' + stats.totalStar4 + '</span></div><div class="sidebar-stat"><span class="sidebar-stat-label">三星参考</span><span class="sidebar-stat-value" style="color:#2196F3">' + stats.totalStar3 + '</span></div>';

    // Enterprise list
    let entHtml = '<div class="sidebar-ent ' + (currentEnterprise === 'all' ? 'active' : '') + '" onclick="selectEnterprise(\'all\')"><div class="sidebar-ent-icon">🏢</div><div class="sidebar-ent-info"><div class="sidebar-ent-name">全部企业</div><div class="sidebar-ent-meta">显示所有企业数据</div></div></div>';

    allData.forEach(function(ent) {
        const bestScore = Math.max.apply(null, ent['政策匹配结果'].map(function(p) { return p['总分'] || calcPolicyScore(p); }));
        const stars = getStarsHtml(bestScore);
        const entName = ent['企业名称'];
        const shortName = entName.length > 10 ? entName.substring(0, 10) + '...' : entName;
        const isActive = currentEnterprise === entName;

        entHtml += '<div class="sidebar-ent ' + (isActive ? 'active' : '') + '" onclick="selectEnterprise(\'' + entName.replace(/'/g, "\\'") + '\')"><div class="sidebar-ent-icon">🏭</div><div class="sidebar-ent-info"><div class="sidebar-ent-name" title="' + entName + '">' + shortName + '</div><div class="sidebar-ent-meta">' + (ent['年份'] || '') + '</div><div class="sidebar-ent-stars">' + stars + '</div></div></div>';
    });

    document.getElementById('sidebarEnts').innerHTML = entHtml;
}

function getStarsHtml(score) {
    let html = '';
    const starCount = score >= 90 ? 5 : score >= 80 ? 4 : score >= 70 ? 3 : score >= 60 ? 2 : score >= 50 ? 1 : 0;
    for (let i = 0; i < 5; i++) {
        html += i < starCount ? '<span class="sidebar-ent-star-full">⭐</span>' : '<span class="sidebar-ent-star-empty">☆</span>';
    }
    return html;
}

function selectEnterprise(entName) {
    currentEnterprise = entName;
    // Update dropdown
    if (currentEntSelect) currentEntSelect.value = entName;
    // Re-render
    renderSidebar();
    renderEnterpriseTabs();
    renderContent();
    // Close sidebar on mobile
    if (window.innerWidth <= 900) {
        document.getElementById('sidebar').classList.remove('open');
    }
}
`;

// 在 loadData 函数前插入
html = html.replace(
    'function parseAndRender(jsonStr) {',
    sidebarFunctions + '\n        function parseAndRender(jsonStr) {'
);

// 4. 修改 parseAndRender 末尾添加 renderSidebar()
html = html.replace(
    'function renderContent();\n        }',
    'function renderContent();\n            renderSidebar();\n        }'
);

// 5. 隐藏原有的 enterpriseTabsContainer
html = html.replace(
    '<div class="relative z-10 max-w-7xl mx-auto px-6 pb-2" id="enterpriseTabsContainer">',
    '<div class="relative z-10 max-w-7xl mx-auto px-6 pb-2" id="enterpriseTabsContainer" style="display:none">'
);

fs.writeFileSync('e:/PEMatching/llm_matching_results.html', html);
console.log('侧边栏布局已添加');