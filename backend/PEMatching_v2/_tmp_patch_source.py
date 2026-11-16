# -*- coding: utf-8 -*-
"""给源文件 主政策申报条件.json 中 6561/6565 两个区级高企政策补高企资质规则。"""
import json
import os
from pathlib import Path

SRC = Path(r'E:\workspace\kejiju-frontend\backend\PEMatching\config\主政策申报条件.json')
payload = json.loads(SRC.read_text(encoding='utf-8'))
data = payload.get('data')

targets = {
    '6561': '同安区国家高新技术企业认定奖励',
    '6565': '湖里区国家级高新技术企业奖励兑现',
}

RULE_GROUP = {
    "conditionNo": 1,
    "conditionOperator": "AND",
    "conditionBranchId": "C1-B1",
    "conditionBranchText": "申报对象为国家高新技术企业",
    "conditionFieldItems": [
        {
            "itemId": "1-1",
            "itemName": "国家高新技术企业资质",
            "conditionBranchId": "C1-B1",
            "conditionBranchText": "申报对象为国家高新技术企业",
            "enterpriseFields": ["企业资质"],
            "itemStatus": "resolved",
            "matchRule": {
                "ruleId": "S1-1",
                "ruleKind": "direct",
                "fieldType": "boolean",
                "operator": "is_true",
                "targetValue": True,
                "unit": "",
                "description": "须为国家高新技术企业"
            },
            "policyFragment": "国家高新技术企业",
            "sourceFragmentId": "C1-A1",
            "sourceStandardField": "企业资质"
        }
    ],
    "rawText": "申报对象为国家高新技术企业",
    "unresolvedFragments": []
}

changed = []
for entry in data:
    pid = str(entry.get('fusionPolicyId') or entry.get('id') or '')
    if pid not in targets:
        continue
    rt = entry.get('ruleTableJson') or {}
    if not isinstance(rt, dict):
        print(f"[{pid}] ruleTableJson 不是 dict，跳过")
        continue
    groups = rt.get('softRuleGroups')
    if not isinstance(groups, list):
        rt['softRuleGroups'] = []
        groups = rt['softRuleGroups']
    # 避免重复添加
    existing_names = {
        (it.get('itemName') or '')
        for g in groups
        for it in (g.get('conditionFieldItems') or [])
        if isinstance(it, dict)
    }
    if '国家高新技术企业资质' in existing_names:
        print(f"[{pid}] 已存在高企规则，跳过")
        continue
    groups.append(json.loads(json.dumps(RULE_GROUP)))
    # 同步 rule_table_json（内容一致性）
    if isinstance(entry.get('rule_table_json'), dict):
        entry['rule_table_json']['softRuleGroups'] = groups
    changed.append(pid)
    print(f"[{pid}] 已补 {targets[pid]} softRuleGroups -> {len(groups)} 组")

if changed:
    # 保留原格式：ensure_ascii=False，缩进与原文件一致
    SRC.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"\n已写入 {SRC}，改动政策: {changed}")
else:
    print("无改动")
