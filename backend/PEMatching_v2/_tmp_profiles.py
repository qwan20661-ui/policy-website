# -*- coding: utf-8 -*-
import json
from pathlib import Path

profiles = {}
for line in Path('config/eligibility/policy_profiles.jsonl').read_text(encoding='utf-8').splitlines():
    if not line.strip():
        continue
    p = json.loads(line)
    profiles[str(p.get('fusionPolicyId'))] = p

# 空 profile 示例
for pid in ('6561', '6466'):
    print("=" * 70)
    print("### [{}] 空 profile ###".format(pid))
    print(json.dumps(profiles.get(pid), ensure_ascii=False, indent=1)[:1200])

# 有规则的 profile 示例
print("=" * 70)
print("### [6451] 有规则 profile（结构参考）###")
p = profiles.get('6451')
print(json.dumps({k: p[k] for k in ('fusionPolicyId', 'policyTitle', 'coverage', 'schemaVersion')}, ensure_ascii=False, indent=1))
print("...softRuleGroups 第一条...")
sg = p.get('softRuleGroups') or []
if sg:
    print(json.dumps(sg[0], ensure_ascii=False, indent=1)[:1500])
