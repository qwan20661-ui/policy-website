const fs = require('fs');
let html = fs.readFileSync('e:/PEMatching/llm_matching_results.html', 'utf8');

// 移除侧边栏相关代码
html = html.replace(/<!-- Sidebar -->[\s\S]*?<!-- Mobile Toggle -->[\s\S]*?<\/button>\n\n    /, '');
html = html.replace(/<!-- Main Content Wrapper -->[\s\S]*?<div class="blur-shape blur-3"><\/div>\n\n        /, '');

// 移除 main-wrapper 结束标签
html = html.replace(/\n    <\/div>\n\n    <script>[\s\S]*$/, '\n    <script>');

// 移除侧边栏样式
html = html.replace(/\/\* Sidebar Styles \*\/[\s\S]*?@media \(max-width: 900px\) \{[\s\S]*?\n        \}\n/, '');

// 移除 main-wrapper 样式
html = html.replace(/\.main-wrapper \{[\s\S]*?\}\n/, '');

// 恢复 original body
const originalBody = `<body class="relative">
    <div class="blur-shape blur-1"></div>
    <div class="blur-shape blur-2"></div>
    <div class="blur-shape blur-3"></div>

    <!-- Header -->`;

html = html.replace(/<body class="relative">[\s\S]*?<!-- Header -->/, originalBody);

// 移除 renderSidebar 调用
html = html.replace(/renderSidebar\(\);\n            /, '');

// 移除侧边栏相关函数
html = html.replace(/\/\/ ========== Sidebar Functions ==========[\s\S]*?selectEnterprise\(entName\);[\s\S]*?\}\n/, '');

// 恢复 enterpriseTabsContainer 显示
html = html.replace(/id="enterpriseTabsContainer" style="display:none"/, 'id="enterpriseTabsContainer"');

// 恢复 fetch URL
html = html.replace(/'llm_matching_results\.json\?t=' \+ Date\.now\(\)/, "'llm_matching_results.json'");

fs.writeFileSync('e:/PEMatching/llm_matching_results.html', html);
console.log('已恢复原始布局');
