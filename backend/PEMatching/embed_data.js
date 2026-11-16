const fs = require('fs');
const data = JSON.parse(fs.readFileSync('e:/PEMatching/llm_matching_results.json', 'utf8'));

let html = fs.readFileSync('e:/PEMatching/llm_matching_results.html', 'utf8');

// 找到 EMBEDDED_DATA 行的位置并替换整行
const lines = html.split('\n');
for (let i = 0; i < lines.length; i++) {
    if (lines[i].includes('const EMBEDDED_DATA = ')) {
        // 找到开始行
        let startLine = i;
        // 找到结束行 (];)
        let endLine = i;
        while (endLine < lines.length && !lines[endLine].trim().endsWith('];')) {
            endLine++;
        }

        // 替换为新数据 (多行格式)
        const jsonLines = JSON.stringify(data, null, 4).split('\n');
        const newData = 'const EMBEDDED_DATA = ' + JSON.stringify(data) + ';';

        lines.splice(startLine, endLine - startLine + 1, newData);
        break;
    }
}

html = lines.join('\n');

// 确保 loadData 正确调用 renderSidebar
html = html.replace(
    /renderEnterpriseTabs\(\);\s*renderContent\(\);\s*renderSidebar\(\);/,
    'renderEnterpriseTabs(); renderContent(); renderSidebar();'
);

fs.writeFileSync('e:/PEMatching/llm_matching_results.html', html);
console.log('已修复，数据嵌入完成');