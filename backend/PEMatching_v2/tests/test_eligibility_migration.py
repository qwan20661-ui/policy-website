# -*- coding: utf-8 -*-
"""规则快照迁移与稳定键测试。"""

from __future__ import annotations

import pytest

from service.eligibility.models import ProfileValidationError, migrate_rule_table
from service.eligibility.sync import sync_from_policy_snapshot


def _table(items):
    return {"schemaVersion": "1.2", "softRuleGroups": [{"conditionNo": 1, "conditionOperator": "AND", "conditionFieldItems": items}], "hardRuleGroups": [], "specialPassRuleGroups": [], "definitionNotes": [], "applicationChecklistGroups": []}


def test_重复itemId仍生成位置唯一的语义键():
    item = {"itemId": "same", "enterpriseFields": ["收入"], "matchRule": {"ruleKind": "direct", "fieldType": "number", "operator": "gte", "targetValue": 1, "unit": "万元"}}
    profile = migrate_rule_table("P1", "政策", _table([item, item]))

    keys = [item["semanticItemKey"] for item in profile["softRuleGroups"][0]["conditionFieldItems"]]
    assert keys == ["P1:softRuleGroups:1:1", "P1:softRuleGroups:1:2"]
    assert profile["coverage"] == "complete"


def test_未知操作符会阻断迁移():
    item = {"itemId": "x", "matchRule": {"ruleKind": "direct", "operator": "between", "targetValue": 1}}
    with pytest.raises(ProfileValidationError, match="未知操作符"):
        migrate_rule_table("P1", "政策", _table([item]))


def test_未解析项保留并降为partial():
    item = {"itemId": "x", "itemStatus": "unresolved", "matchRule": {"ruleKind": "reference", "operator": "llm_judge"}}
    profile = migrate_rule_table("P1", "政策", _table([item]))

    assert profile["coverage"] == "partial"
    assert profile["migrationWarnings"]


def test_资格迁移复用政策快照而不重复请求接口():
    snapshot = {
        "schemaVersion": "pematching-v2-policy-snapshot-v2",
        "versionHash": "a" * 64,
        "count": 1,
        "data": [{
            "fusionPolicyId": "P1",
            "title": "政策",
            "updateTime": "2026-08-06 10:00:00",
            "ruleTableJson": _table([{
                "itemId": "x",
                "matchRule": {
                    "ruleKind": "direct",
                    "fieldType": "number",
                    "operator": "gte",
                    "targetValue": 1,
                    "unit": "万元",
                },
            }]),
        }],
    }

    manifest = sync_from_policy_snapshot(snapshot, write=False)

    assert manifest["sourceType"] == "policy_snapshot_rule_table_json"
    assert manifest["sourceSnapshotVersion"] == "a" * 64
    assert manifest["fetchedPolicyCount"] == 1
    assert manifest["profileCount"] == 1
