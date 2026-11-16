const fs = require('fs');

// 读取JSON数据
const jsonData = fs.readFileSync('e:/PEMatching/llm_matching_results.json', 'utf8');
const data = JSON.parse(jsonData);

// 嵌入式数据
const embeddedData = JSON.stringify(data).replace(/'/g, "\\'").replace(/\n/g, ' ');

const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>政策匹配分析报告 - Material You</title>
    <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        'md-surface': '#FFFBFE',
                        'md-on-surface': '#1C1B1F',
                        'md-primary': '#6750A4',
                        'md-on-primary': '#FFFFFF',
                        'md-secondary-container': '#E8DEF8',
                        'md-on-secondary-container': '#1D192B',
                        'md-tertiary': '#7D5260',
                        'md-surface-container': '#F3EDF7',
                        'md-surface-container-low': '#E7E0EC',
                        'md-outline': '#79747E',
                        'md-on-surface-variant': '#49454F',
                    },
                    fontFamily: { 'Roboto': ['Roboto', 'sans-serif'] },
                    borderRadius: {
                        'md-sm': '12px', 'md': '16px', 'md-lg': '24px',
                        'md-xl': '28px', 'md-2xl': '32px', 'md-3xl': '48px',
                    }
                }
            }
        }
    </script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Roboto', sans-serif; background: #FFFBFE; color: #1C1B1F; min-height: 100vh; }

        .blur-shape { position: fixed; pointer-events: none; z-index: 0; filter: blur(64px); opacity: 0.25; }
        .blur-1 { width: 500px; height: 500px; background: radial-gradient(circle, #6750A4 0%, transparent 70%); top: -150px; right: -100px; }
        .blur-2 { width: 400px; height: 400px; background: radial-gradient(circle, #7D5260 0%, transparent 70%); bottom: 20%; left: -100px; }
        .blur-3 { width: 350px; height: 350px; background: radial-gradient(circle, #E8DEF8 0%, transparent 70%); top: 40%; right: -50px; }

        .card-hover { transition: all 0.3s cubic-bezier(0.2, 0, 0, 1); }
        .card-hover:hover { transform: scale(1.02); box-shadow: 0 8px 24px rgba(0,0,0,0.12); }

        .btn-material { transition: all 0.3s cubic-bezier(0.2, 0, 0, 1); }
        .btn-material:hover { opacity: 0.9; }
        .btn-material:active { transform: scale(0.95); }

        .score-ring { transition: stroke-dashoffset 0.5s cubic-bezier(0.2, 0, 0, 1); }

        .grade-a { background: linear-gradient(135deg, #4CAF50, #2E7D32); }
        .grade-b { background: linear-gradient(135deg, #8BC34A, #558B2F); }
        .grade-c { background: linear-gradient(135deg, #FF9800, #F57C00); }
        .grade-d { background: linear-gradient(135deg, #9E9E9E, #616161); }

        .badge-a { background: linear-gradient(135deg, #4CAF50, #2E7D32); color: white; }
        .badge-b { background: linear-gradient(135deg, #8BC34A, #558B2F); color: white; }
        .badge-c { background: linear-gradient(135deg, #FF9800, #F57C00); color: white; }
        .badge-d { background: linear-gradient(135deg, #9E9E9E, #616161); color: white; }
        .badge-top { background: linear-gradient(135deg, #FFD700, #FF8C00); color: #1a1a1a; font-weight: 700; }
        .star-5 { background: linear-gradient(135deg, #FFD700, #FF8C00); color: #1a1a1a; }
        .star-4 { background: linear-gradient(135deg, #4CAF50, #2E7D32); color: white; }
        .star-3 { background: linear-gradient(135deg, #2196F3, #1565C0); color: white; }
        .star-2 { background: linear-gradient(135deg, #FF9800, #F57C00); color: white; }
        .star-1 { background: linear-gradient(135deg, #9E9E9E, #616161); color: white; }
        .star-0 { background: linear-gradient(135deg, #f44336, #c62828); color: white; }

        .match-full { background: #DCFCE7; color: #16A34A; }
        .match-partial { background: #FEF9C3; color: #CA8A04; }
        .match-none { background: #FEE2E2; color: #DC2626; }
        .match-pending { background: #E0E7FF; color: #4338CA; }

        .condition-met { background: rgba(76, 175, 80, 0.08); border-left: 4px solid #4CAF50; }
        .condition-unmet { background: rgba(244, 67, 54, 0.08); border-left: 4px solid #F44336; }
        .condition-partial { background: rgba(255, 152, 0, 0.08); border-left: 4px solid #FF9800; }
        .condition-pending { background: rgba(67, 56, 202, 0.08); border-left: 4px solid #4338CA; }

        .score-bar-fill.high { background: linear-gradient(90deg, #4CAF50, #81C784); }
        .score-bar-fill.medium { background: linear-gradient(90deg, #FF9800, #FFB74D); }
        .score-bar-fill.low { background: linear-gradient(90deg, #9E9E9E, #BDBDBD); }

        @keyframes pop { 0% { transform: scale(0); opacity: 0; } 70% { transform: scale(1.05); } 100% { transform: scale(1); opacity: 1; } }
        .policy-card { animation: pop 0.4s cubic-bezier(0.34, 1.56, 0.64, 1) forwards; }

        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: #F3EDF7; }
        ::-webkit-scrollbar-thumb { background: #79747E; border-radius: 4px; }

        .search-input-wrap {
            position: relative; flex: 1; min-width: 240px;
        }
        .search-input-wrap svg {
            position: absolute; left: 14px; top: 50%; transform: translateY(-50%);
            width: 20px; height: 20px; color: #79747E; pointer-events: none;
        }
        .search-input-wrap input {
            width: 100%; padding: 12px 40px 12px 44px;
            border: 2px solid #E7E0EC; border-radius: 9999px;
            font-size: 0.9rem; transition: all 0.2s;
        }
        .search-input-wrap input:focus { outline: none; border-color: #6750A4; box-shadow: 0 0 0 3px rgba(103,80,164,0.12); }

        .search-clear {
            position: absolute; right: 14px; top: 50%; transform: translateY(-50%);
            width: 20px; height: 20px; border-radius: 50%; background: #79747E;
            color: white; border: none; cursor: pointer; font-size: 14px; line-height: 20px;
            display: none; text-align: center;
        }
        .search-clear.show { display: block; }

        .filter-row {
            display: flex; align-items: center; flex-wrap: wrap; gap: 8px;
            padding: 12px 16px; background: #F3EDF7; border-radius: 16px;
        }

        .filter-chip {
            padding: 6px 14px; border: 1.5px solid #E7E0EC; border-radius: 9999px;
            font-size: 0.8rem; font-weight: 500; cursor: pointer;
            background: white; color: #49454F; transition: all 0.2s;
        }
        .filter-chip:hover { border-color: #6750A4; color: #6750A4; }
        .filter-chip.active { background: #6750A4; color: white; border-color: #6750A4; }

        .sort-btn {
            padding: 6px 12px; border: 1.5px solid #E7E0EC; border-radius: 9999px;
            font-size: 0.8rem; cursor: pointer; background: white; color: #49454F;
            display: flex; align-items: center; gap: 4px; transition: all 0.2s;
        }
        .sort-btn:hover { border-color: #6750A4; }
        .sort-btn.active { background: #E8DEF8; color: #1D192B; border-color: #6750A4; }

        .ent-tab {
            padding: 8px 16px; border: 2px solid #E7E0EC; border-radius: 9999px;
            font-size: 0.82rem; font-weight: 500; cursor: pointer; background: white;
            color: #49454F; transition: all 0.2s; white-space: nowrap;
        }
        .ent-tab:hover { border-color: #6750A4; color: #6750A4; }
        .ent-tab.active { background: #6750A4; color: white; border-color: #6750A4; }

        .ent-select {
            padding: 8px 36px 8px 14px; border: 2px solid #E7E0EC; border-radius: 9999px;
            font-size: 0.82rem; font-weight: 500; background: #fff; cursor: pointer;
            transition: all 0.2s; appearance: none;
            background-image: url("data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%2379747E' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3e%3cpolyline points='6 9 12 15 18 9'%3e%3c/polyline%3e%3c/svg%3e");
            background-repeat: no-repeat; background-position: right 12px center;
            max-width: 220px;
        }
        .ent-select:hover { border-color: #6750A4; }
        .ent-select:focus { outline: none; border-color: #6750A4; box-shadow: 0 0 0 3px rgba(103,80,164,0.12); }

        .top-chip {
            padding: 8px 18px; border: 2px solid #FFD700; border-radius: 9999px;
            font-size: 0.82rem; font-weight: 700; cursor: pointer;
            background: linear-gradient(135deg, #FFF8E1, #FFECB3); color: #E65100;
            transition: all 0.2s; white-space: nowrap;
        }
        .top-chip:hover { box-shadow: 0 2px 10px rgba(255,152,0,0.3); transform: translateY(-1px); }
        .top-chip.active {
            background: linear-gradient(135deg, #FFD700, #FF8C00); color: #1a1a1a;
            border-color: #FF8C00; box-shadow: 0 2px 10px rgba(255,152,0,0.4);
        }

        .top-card-glow {
            box-shadow: 0 4px 20px rgba(255, 180, 0, 0.18), 0 0 0 2px rgba(255, 215, 0, 0.35);
        }

        .result-count { color: #79747E; font-size: 0.85rem; }

        .no-result-icon { font-size: 3rem; margin-bottom: 12px; }

        @media (max-width: 768px) {
            .filter-row { padding: 10px 12px; gap: 8px; }
            .search-input-wrap { min-width: 100%; }
        }
    </style>
</head>
<body class="relative">
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
                <button class="sort-btn active" data-sort="score-desc">评分↓</button>
                <button class="sort-btn" data-sort="score-asc">评分↑</button>
                <button class="sort-btn" data-sort="name">A→Z</button>
            </div>
            <div class="flex items-center justify-between mt-4 pt-3 border-t border-dashed border-gray-200">
                <span class="result-count" id="resultCount"></span>
                <button class="text-sm text-md-primary hover:underline cursor-pointer btn-material" onclick="resetAll()">重置所有筛选</button>
            </div>
        </div>
    </div>

    <!-- Enterprise Tabs -->
    <div class="relative z-10 max-w-7xl mx-auto px-6 pb-2">
        <div class="flex gap-2 overflow-x-auto pb-2" id="enterpriseTabs"></div>
    </div>

    <!-- Stats -->
    <div class="relative z-10 max-w-7xl mx-auto px-6 py-4">
        <div class="grid grid-cols-2 md:grid-cols-5 gap-4" id="statsBar"></div>
    </div>

    <!-- File Picker -->
    <div id="filePicker" class="relative z-10 max-w-7xl mx-auto px-6 py-12 hidden">
        <div class="bg-md-surface-container rounded-[32px] p-12 text-center">
            <h2 class="text-xl font-medium text-md-on-surface mb-2">请加载数据文件</h2>
            <p class="text-sm text-md-on-surface-variant mb-6">请点击选择 <code class="bg-md-surface-container-low px-2 py-1 rounded">llm_matching_results.json</code> 文件</p>
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

    <script>
        const EMBEDDED_DATA = [];

        let allData = [];
        let currentScoreFilter = 'all';
        let currentSort = 'score-desc';
        let currentView = 'top';
        let currentEnterprise = 'all';
        let searchTerm = '';
        let currentEntSelect = null;

        function parseAndRender(jsonStr) {
            allData = JSON.parse(jsonStr);
            document.getElementById('filePicker').classList.add('hidden');
            document.getElementById('statsBar').parentElement.classList.remove('hidden');
            renderStats();
            renderEnterpriseTabs();
            renderContent();
        }

        async function loadData() {
            try {
                const res = await fetch('llm_matching_results.json');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const text = await res.text();
                parseAndRender(text);
            } catch (error) {
                console.warn('Fetch failed:', error.message);
                document.getElementById('statsBar').parentElement.classList.add('hidden');
                document.getElementById('filePicker').classList.remove('hidden');
            }
        }

        function getVisiblePolicies(policy, entName) {
            const score = policy['总分'] || calcPolicyScore(policy);
            if (currentScoreFilter === 's5' && score < 90) return false;
            if (currentScoreFilter === 's4' && (score < 80 || score >= 90)) return false;
            if (currentScoreFilter === 's3' && (score < 70 || score >= 80)) return false;
            if (currentScoreFilter === 's2' && (score < 60 || score >= 70)) return false;
            if (currentScoreFilter === 's1' && (score < 50 || score >= 60)) return false;
            if (currentScoreFilter === 's0' && score >= 50) return false;
            if (searchTerm) {
                const term = searchTerm.toLowerCase();
                if (!policy['政策标题']?.toLowerCase().includes(term) && !(entName || '').toLowerCase().includes(term)) return false;
            }
            if (currentView === 'top90' && score < 90) return false;
            if (currentView === 'top' && score < 70) return false;
            return true;
        }

        function calcPolicyScore(policy) {
            const conditions = policy['条件匹配详情'] || [];
            if (!conditions.length) return 0;
            const total = conditions.length;
            const fullMatch = conditions.filter(c => c['匹配度'] === '完全匹配').length;
            return Math.round((fullMatch / total) * 100);
        }

        function getStarDisplay(score) {
            if (score >= 90) return '⭐️⭐️⭐️⭐️⭐️ 五星推荐';
            if (score >= 80) return '⭐️⭐️⭐️⭐️ 四星建议';
            if (score >= 70) return '⭐️⭐️⭐️ 三星参考';
            if (score >= 60) return '⭐️⭐️ 二星谨慎';
            if (score >= 50) return '⭐️ 一星观望';
            return '❌ 不推荐';
        }

        function getStarOnly(score) {
            if (score >= 90) return '⭐️⭐️⭐️⭐️⭐️';
            if (score >= 80) return '⭐️⭐️⭐️⭐️';
            if (score >= 70) return '⭐️⭐️⭐️';
            if (score >= 60) return '⭐️⭐️';
            if (score >= 50) return '⭐️';
            return '❌';
        }

        function renderPolicyCard(ent, policy, delay) {
            const score = policy['总分'] || calcPolicyScore(policy);
            const conditions = policy['条件匹配详情'] || [];
            const fullMatch = conditions.filter(c => c['匹配度'] === '完全匹配').length;
            const partialMatch = conditions.filter(c => c['匹配度'] === '部分匹配').length;
            const pendingMatch = conditions.filter(c => c['匹配度'] === '待确认').length;
            const noMatch = conditions.filter(c => c['匹配度'] === '不匹配').length;
            const scorePercent = (score / 100) * 251;
            const circumference = 2 * Math.PI * 40;

            return \`
                <div class="policy-card bg-white rounded-[24px] border-2 border-md-on-surface overflow-hidden shadow-md \${score >= 90 ? 'top-card-glow' : ''}" style="animation-delay: \${delay}ms" data-score="\${score}">
                    <div class="p-6">
                        <div class="flex items-start justify-between mb-4">
                            <div class="flex-1">
                                <h3 class="text-lg font-medium text-md-on-surface mb-1">\${policy['政策标题'] || '未知政策'}</h3>
                                <p class="text-sm text-md-on-surface-variant">\${ent['企业名称']}</p>
                            </div>
                            \${getStarOnly(score)}
                        </div>
                        <div class="flex items-center gap-2 mb-2 flex-wrap">
                            <span class="text-2xl font-bold" style="color: \${score >= 90 ? '#FFD700' : score >= 70 ? '#6750A4' : '#79747E'}">\${score}</span>
                            <span class="text-sm text-md-on-surface-variant">分</span>
                        </div>
                        <div class="flex flex-wrap gap-2 mt-3">
                            <span class="px-3 py-1 rounded-full text-xs font-medium match-full">\${fullMatch} 完全匹配</span>
                            \${partialMatch > 0 ? '<span class="px-3 py-1 rounded-full text-xs font-medium match-partial">' + partialMatch + ' 部分匹配</span>' : ''}
                            \${pendingMatch > 0 ? '<span class="px-3 py-1 rounded-full text-xs font-medium match-pending">' + pendingMatch + ' 待确认</span>' : ''}
                            \${noMatch > 0 ? '<span class="px-3 py-1 rounded-full text-xs font-medium match-none">' + noMatch + ' 不匹配</span>' : ''}
                        </div>
                    </div>
                    <div class="px-6 py-4 bg-md-surface-container">
                        <div class="flex gap-2">
                            <span class="text-sm text-md-on-surface-variant">条件总数：\${conditions.length}</span>
                            <span class="text-sm text-md-on-surface-variant">|</span>
                            <span class="text-sm text-md-on-surface-variant">\${getStarDisplay(score)}</span>
                        </div>
                    </div>
                    <div class="p-4 border-t border-gray-100">
                        <button onclick="showDetail('\${ent['企业名称']}', '\${policy['政策标题']}')" class="w-full py-2 rounded-full bg-md-primary text-white font-medium btn-material">
                            查看详情 →
                        </button>
                    </div>
                </div>
            \`;
        }

        function renderContent() {
            let targetEnts = currentEnterprise === 'all' ? allData : allData.filter(e => e['企业名称'] === currentEnterprise);
            let cards = [];
            targetEnts.forEach(ent => {
                ent['政策匹配结果'].forEach(policy => {
                    if (getVisiblePolicies(policy, ent['企业名称'])) {
                        cards.push({ ent, policy });
                    }
                });
            });

            // Sort
            if (currentSort === 'score-desc') {
                cards.sort((a, b) => ((b.policy['总分'] || calcPolicyScore(b.policy)) - (a.policy['总分'] || calcPolicyScore(a.policy))));
            } else if (currentSort === 'score-asc') {
                cards.sort((a, b) => ((a.policy['总分'] || calcPolicyScore(a.policy)) - (b.policy['总分'] || calcPolicyScore(b.policy))));
            } else if (currentSort === 'name') {
                cards.sort((a, b) => (a.policy['政策标题'] || '').localeCompare(b.policy['政策标题'] || '', 'zh-CN'));
            }

            if (cards.length === 0) {
                document.getElementById('mainContent').innerHTML = \`
                    <div class="text-center py-16">
                        <div class="no-result-icon">🔍</div>
                        <h3 class="text-xl font-medium text-md-on-surface mb-2">没有找到匹配的政策</h3>
                        <p class="text-md-on-surface-variant">请尝试调整筛选条件</p>
                    </div>
                \`;
            } else {
                let html = '<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">';
                cards.forEach((item, i) => {
                    html += renderPolicyCard(item.ent, item.policy, i * 80);
                });
                html += '</div>';
                document.getElementById('mainContent').innerHTML = html;
            }

            document.getElementById('resultCount').textContent = \`显示 \${cards.length} 个匹配结果\`;
        }

        function showDetail(entName, policyTitle) {
            const ent = allData.find(e => e['企业名称'] === entName);
            if (!ent) return;
            const policy = ent['政策匹配结果'].find(p => p['政策标题'] === policyTitle);
            if (!policy) return;

            const score = policy['总分'] || calcPolicyScore(policy);
            const conditions = policy['条件匹配详情'] || [];
            const fullMatch = conditions.filter(c => c['匹配度'] === '完全匹配').length;
            const partialMatch = conditions.filter(c => c['匹配度'] === '部分匹配').length;
            const pendingMatch = conditions.filter(c => c['匹配度'] === '待确认').length;
            const noMatch = conditions.filter(c => c['匹配度'] === '不匹配').length;

            let conditionsHtml = '<div class="grid grid-cols-1 md:grid-cols-2 gap-4">';
            conditions.forEach(c => {
                const matchClass = c['匹配度'] === '完全匹配' ? 'match-full' : c['匹配度'] === '部分匹配' ? 'match-partial' : c['匹配度'] === '待确认' ? 'match-pending' : 'match-none';
                conditionsHtml += \`
                    <div class="bg-white rounded-[16px] p-4 border border-gray-100">
                        <div class="flex items-start justify-between gap-4">
                            <div class="flex-1">
                                <div class="font-medium text-md-on-surface mb-1">\${c['条件名称']}</div>
                                <div class="text-sm text-md-on-surface-variant">政策要求: \${c['政策要求'] || c['政策原文'] || ''}</div>
                                \${c['企业现状'] ? '<div class="text-sm text-md-on-surface-variant mt-1">企业现状: ' + c['企业现状'] + '</div>' : ''}
                                \${c['差距分析'] && c['差距分析'] !== '无差距' ? '<div class="text-sm text-orange-600 mt-1">差距: ' + c['差距分析'] + '</div>' : ''}
                            </div>
                            <span class="px-3 py-1 rounded-full text-sm font-medium \${matchClass} shrink-0">\${c['匹配度']}</span>
                        </div>
                    </div>
                \`;
            });
            conditionsHtml += '</div>';

            document.getElementById('modalTitle').textContent = entName + ' - ' + policyTitle;
            document.getElementById('modalContent').innerHTML = \`
                <div class="mb-6">
                    <div class="flex flex-wrap gap-2 mb-2">
                        \${getStarDisplay(score)}
                    </div>
                    <div class="flex gap-4 text-sm text-md-on-surface-variant">
                        <span>条件总数: \${conditions.length}</span>
                        <span>完全匹配: \${fullMatch}</span>
                        <span>部分匹配: \${partialMatch}</span>
                        <span>待确认: \${pendingMatch}</span>
                        <span>不匹配: \${noMatch}</span>
                    </div>
                </div>
                <div class="bg-md-surface-container rounded-[24px] p-6 mb-6">
                    <h3 class="text-lg font-medium mb-4">企业信息</h3>
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                        <div><span class="text-md-on-surface-variant">职工总数</span><br><span class="font-medium">\${ent['企业信息']?.职工总数 || 0} 人</span></div>
                        <div><span class="text-md-on-surface-variant">科技人员</span><br><span class="font-medium">\${ent['企业信息']?.科技人员数 || 0} 人</span></div>
                        <div><span class="text-md-on-surface-variant">知识产权</span><br><span class="font-medium">\${ent['企业信息']?.知识产权总数 || 0} 件</span></div>
                        <div><span class="text-md-on-surface-variant">研发投入</span><br><span class="font-medium">\${ent['企业信息']?.研发投入 || 0} 万元</span></div>
                    </div>
                </div>
                <h3 class="text-lg font-medium mb-4">条件匹配详情</h3>
                \${conditionsHtml}
                \${policy['总体建议'] ? '<div class="mt-6 bg-blue-50 rounded-[24px] p-6"><h3 class="text-lg font-medium text-blue-700 mb-2">💡 总体建议</h3><p class="text-blue-600">' + policy['总体建议'] + '</p></div>' : ''}
            \`;
            document.getElementById('detailModal').classList.remove('hidden');
            document.body.style.overflow = 'hidden';
        }

        function closeModal() {
            document.getElementById('detailModal').classList.add('hidden');
            document.body.style.overflow = '';
        }

        function computeStats(data) {
            let totalStar5 = 0, totalStar4 = 0, totalStar3 = 0, totalStar2 = 0, totalStar1 = 0, totalStar0 = 0;
            let totalMatches = 0;
            data.forEach(ent => {
                ent['政策匹配结果'].forEach(p => {
                    const score = p['总分'] || calcPolicyScore(p);
                    totalMatches++;
                    if (score >= 90) totalStar5++;
                    else if (score >= 80) totalStar4++;
                    else if (score >= 70) totalStar3++;
                    else if (score >= 60) totalStar2++;
                    else if (score >= 50) totalStar1++;
                    else totalStar0++;
                });
            });
            return { totalEnterprises: data.length, totalMatches, totalStar5, totalStar4, totalStar3, totalStar2, totalStar1, totalStar0 };
        }

        function renderStats() {
            const stats = computeStats(allData);
            document.getElementById('statsBar').innerHTML = \`
                <div class="bg-md-surface-container rounded-[24px] p-4 text-center shadow-sm">
                    <div class="text-2xl font-bold text-md-primary">\${stats.totalEnterprises}</div>
                    <div class="text-xs text-md-on-surface-variant">家企业</div>
                </div>
                <div class="bg-md-surface-container rounded-[24px] p-4 text-center shadow-sm">
                    <div class="text-2xl font-bold" style="color: #FF8C00;">\${stats.totalStar5}</div>
                    <div class="text-xs text-md-on-surface-variant">五星推荐</div>
                </div>
                <div class="bg-md-surface-container rounded-[24px] p-4 text-center shadow-sm">
                    <div class="text-2xl font-bold" style="color: #4CAF50;">\${stats.totalStar4}</div>
                    <div class="text-xs text-md-on-surface-variant">四星建议</div>
                </div>
                <div class="bg-md-surface-container rounded-[24px] p-4 text-center shadow-sm">
                    <div class="text-2xl font-bold" style="color: #2196F3;">\${stats.totalStar3}</div>
                    <div class="text-xs text-md-on-surface-variant">三星参考</div>
                </div>
                <div class="bg-md-surface-container rounded-[24px] p-4 text-center shadow-sm">
                    <div class="text-2xl font-bold text-md-tertiary">\${stats.totalMatches}</div>
                    <div class="text-xs text-md-on-surface-variant">总匹配数</div>
                </div>
            \`;
        }

        function renderEnterpriseTabs() {
            const container = document.getElementById('enterpriseTabs');
            let html = \`<div class="ent-tab \${currentEnterprise === 'all' ? 'active' : ''}" data-ent="all">全部企业</div>\`;
            allData.forEach(ent => {
                const isActive = currentEnterprise === ent['企业名称'];
                html += \`<div class="ent-tab \${isActive ? 'active' : ''}" data-ent="\${ent['企业名称']}">\${ent['企业名称']}</div>\`;
            });
            container.innerHTML = html;
            container.querySelectorAll('.ent-tab').forEach(tab => {
                tab.addEventListener('click', () => {
                    currentEnterprise = tab.dataset.ent;
                    if (currentEntSelect) currentEntSelect.value = currentEnterprise;
                    renderEnterpriseTabs();
                    renderContent();
                });
            });

            if (currentEntSelect && currentEntSelect.options.length <= 1) {
                allData.forEach(ent => {
                    const opt = document.createElement('option');
                    opt.value = ent['企业名称'];
                    opt.textContent = ent['企业名称'];
                    currentEntSelect.appendChild(opt);
                });
            }
        }

        function setupControls() {
            currentEntSelect = document.getElementById('entFilterSelect');
            const searchInput = document.getElementById('searchInput');
            const searchClear = document.getElementById('searchClear');

            searchInput.addEventListener('input', (e) => {
                searchTerm = e.target.value;
                searchClear.classList.toggle('show', searchTerm.length > 0);
                renderContent();
            });

            searchClear.addEventListener('click', () => {
                searchTerm = '';
                searchInput.value = '';
                searchClear.classList.remove('show');
                renderContent();
            });

            document.querySelectorAll('.filter-chip[data-score]').forEach(btn => {
                btn.addEventListener('click', () => {
                    currentScoreFilter = btn.dataset.score;
                    document.querySelectorAll('.filter-chip[data-score]').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    renderContent();
                });
            });

            document.querySelectorAll('.filter-chip[data-view]').forEach(btn => {
                btn.addEventListener('click', () => {
                    currentView = btn.dataset.view;
                    document.querySelectorAll('.filter-chip[data-view]').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    renderContent();
                });
            });

            document.querySelectorAll('.top-chip').forEach(btn => {
                btn.addEventListener('click', () => {
                    if (currentView === 'top90') {
                        currentView = 'top';
                        btn.classList.remove('active');
                    } else {
                        currentView = 'top90';
                        document.querySelectorAll('.filter-chip[data-view]').forEach(b => b.classList.remove('active'));
                        btn.classList.add('active');
                    }
                    renderContent();
                });
            });

            document.querySelectorAll('.sort-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    currentSort = btn.dataset.sort;
                    document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    renderContent();
                });
            });

            if (currentEntSelect) {
                currentEntSelect.addEventListener('change', (e) => {
                    currentEnterprise = e.target.value;
                    renderEnterpriseTabs();
                    renderContent();
                });
            }
        }

        function resetAll() {
            currentScoreFilter = 'all';
            currentSort = 'score-desc';
            currentView = 'top';
            currentEnterprise = 'all';
            searchTerm = '';
            document.querySelectorAll('.filter-chip[data-score]').forEach(b => b.classList.remove('active'));
            document.querySelector('.filter-chip[data-score="all"]').classList.add('active');
            document.querySelectorAll('.filter-chip[data-view]').forEach(b => b.classList.remove('active'));
            document.querySelector('.filter-chip[data-view="top"]').classList.add('active');
            document.querySelectorAll('.top-chip').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
            document.querySelector('.sort-btn[data-sort="score-desc"]').classList.add('active');
            document.getElementById('searchInput').value = '';
            document.getElementById('searchClear').classList.remove('show');
            if (currentEntSelect) currentEntSelect.value = 'all';
            renderEnterpriseTabs();
            renderContent();
        }

        function loadLocalFile(event) {
            const file = event.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = function(e) { parseAndRender(e.target.result); };
            reader.readAsText(file);
        }

        document.addEventListener('DOMContentLoaded', () => {
            setupControls();
            loadData();
        });
    </script>
</body>
</html>`;

fs.writeFileSync('e:/PEMatching/llm_matching_results.html', html);
console.log('HTML文件已重建');