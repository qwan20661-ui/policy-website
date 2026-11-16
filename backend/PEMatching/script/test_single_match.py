"""
单次匹配测试：1 家企业 × 2 条政策
"""
import json
import os
import sys
import io
from pathlib import Path
from dotenv import load_dotenv

# 解决 Windows 控制台 GBK 编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 加载 .env
load_dotenv(Path(__file__).parent.parent / '.env')

from match_with_llm import call_llm_match, format_enterprise_info, format_policy_conditions

# === 选择企业（2025年） ===
ent_file = Path(os.getenv("ENTERPRISE_DIR")) / "科小数据样例.json"
with open(ent_file, 'r', encoding='utf-8') as f:
    all_ent = json.load(f)

ent_name = "厦门奥普拓自控科技有限公司"
enterprise = all_ent["2025"][ent_name]
enterprise['_year'] = '2025'

# === 选择 2 条政策 ===
policy_dir = Path(os.getenv("POLICY_FILE")).parent
test_policies = [
    "厦门市企业研发费用补助管理办法.json",
    "国家高新技术企业认定.json",
]

policies = []
for pname in test_policies:
    pfile = policy_dir / pname
    with open(pfile, 'r', encoding='utf-8') as f:
        policies.append(json.load(f))

# === 逐条匹配 ===
print("=" * 70)
print(f"企业: {ent_name}")
print(f"政策数: {len(policies)}")
print("=" * 70)

for i, pol in enumerate(policies, 1):
    pol_title = pol.get('title', pol.get('policy_title', '未知'))
    print(f"\n[{i}/{len(policies)}] 政策: {pol_title}")
    print("-" * 70)

    result = call_llm_match(enterprise, pol)

    if "error" in result:
        print(f"  ❌ 失败: {result['error']}")
        continue

    conditions = result.get("政策条件列表", [])
    print(f"  ✅ 提取到 {len(conditions)} 个条件\n")

    # 匹配度统计
    match_dist = {}
    for c in conditions:
        m = c.get("匹配度", "未知")
        match_dist[m] = match_dist.get(m, 0) + 1
    print(f"  匹配度分布: {match_dist}\n")

    # 逐条件打印
    for j, c in enumerate(conditions, 1):
        match_emoji = {
            "完全匹配": "🟢", "部分匹配": "🟡",
            "不匹配": "🔴", "待确认": "🔵"
        }.get(c.get("匹配度"), "⚪")
        print(f"  {j}. {match_emoji} {c.get('条件名称', '?')}")
        print(f"     政策原文: {str(c.get('政策原文', c.get('政策要求', '')))[:80]}")
        print(f"     企业现状: {str(c.get('企业现状', ''))[:80]}")
        print(f"     匹配度: {c.get('匹配度', '?')}  差距: {c.get('差距分析', '')[:60]}")
        print()

    # 优势/劣势/风险/建议
    print("  【优势】")
    for a in result.get("优势分析", [])[:3]:
        print(f"    ✓ {a[:80]}")
    print("  【劣势】")
    for d in result.get("劣势分析", [])[:3]:
        print(f"    ✗ {d[:80]}")
    print("  【风险提示】")
    for r in result.get("风险提示", [])[:3]:
        print(f"    ⚠ {r[:80]}")
    print(f"  【总体建议】{str(result.get('总体建议', ''))[:120]}")
    print()
