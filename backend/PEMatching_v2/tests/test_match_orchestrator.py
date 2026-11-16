# -*- coding: utf-8 -*-
"""T7 编排器：实时批量一致、缓存命中、单政策失败隔离。"""

from __future__ import annotations

from datetime import date

from service.condition_scoring import condition_catalog_for_model
from service.match_cache import MatchCache
from service.match_orchestrator import run_condition_match, run_condition_match_batch


def _record(name="测试企业"):
    return {
        "enterpriseName": name, "registerDate": "2020-01-01", "registerAddress": "厦门市思明区",
        "registerDistrict": "厦门市思明区", "industryLevel1": "信息传输业", "industryLevel2": "软件和信息技术服务业",
        "ipClass1Count": 2, "ipClass2Count": 0,
        "operationInfoList": [{"year": "2025", "operatingIncome": 2_000_000, "employeeCount": 10}],
        "honorQualificationList": [{"isTechSme": None, "isInnovativeSme": None, "isHighTechEnterprise": None, "isSpecializedSme": None, "isSpecializedLittleGiant": None}],
    }


def _package(policy_id="P1"):
    return {
        "fusionPolicyId": policy_id, "title": f"测试通用政策{policy_id}", "policyContentHash": ("a" if policy_id == "P1" else "b") * 64,
        "policyInfo": {"policyTitle": f"测试通用政策{policy_id}", "applicableScope": "通用", "industryField": "通用行业"},
        "conditions": [{"applicableTarget": "企业", "applicationRequirement": "营业收入不低于100万元", "applicationStartTime": "2026-08-01", "applicationEndTime": "2026-08-31"}],
        "ruleTableJson": {"hardRuleGroups": [], "softRuleGroups": [{
            "conditionNo": 1, "conditionOperator": "AND", "conditionFieldItems": [{
                "itemId": "1", "itemName": "营业收入", "policyFragment": "营业收入不低于100万元", "enterpriseFields": ["营业收入"],
                "matchRule": {"ruleId": "S1", "operator": "gte", "targetValue": 100, "unit": "万元"},
            }],
        }], "specialPassRuleGroups": []},
        "supportMethods": [], "keywords": [], "documents": [],
    }


def _judge(package, profile, *, rules):
    descriptor = condition_catalog_for_model(package)[0]
    return {
        "output": {
            "schemaVersion": "policy-condition-match-output-v2",
            "sourceConflicts": [], "discoveredConditions": [],
            "conditionResults": [{
                "conditionKey": descriptor,
                "status": "satisfied", "failureEffect": "none", "score": 100,
                "evidencePaths": ["$.metrics.revenueWan"],
                "reason": "满足", "confidence": "high",
            }],
        },
        "modelName": "fake", "promptVersion": "test", "retryCount": 0, "elapsedMs": 1,
    }


def test_实时命中缓存后不再次调用模型():
    cache = MatchCache(10)
    packages = [_package()]
    first = run_condition_match(_record(), today=date(2026, 8, 7), policy_packages=packages, model_judge=_judge, cache=cache)
    second = run_condition_match(_record(), today=date(2026, 8, 7), policy_packages=packages, model_judge=_judge, cache=cache)

    assert first["meta"]["modelCallCount"] == 1
    assert second["meta"]["modelCallCount"] == 0
    assert second["meta"]["cacheHitCount"] == 1
    assert [
        (item["fusionPolicyId"], item["finalScore"], item["conditionScore"])
        for item in first["data"]["recommendations"]
    ] == [
        (item["fusionPolicyId"], item["finalScore"], item["conditionScore"])
        for item in second["data"]["recommendations"]
    ]


def test_实时批量同输入返回相同排序与分数():
    packages = [_package("P1"), _package("P2")]
    realtime = run_condition_match(_record(), today=date(2026, 8, 7), policy_packages=packages, model_judge=_judge)
    batch = run_condition_match_batch([_record()], today=date(2026, 8, 7), policy_packages=packages, model_judge=_judge)
    batch_recommendations = batch["data"]["results"][0]["data"]["recommendations"]

    assert [(item["fusionPolicyId"], item["finalScore"]) for item in realtime["data"]["recommendations"]] == [
        (item["fusionPolicyId"], item["finalScore"]) for item in batch_recommendations
    ]


def test_批量进度回调包含企业和逐政策完成状态(monkeypatch):
    monkeypatch.setenv("PEMATCHING_V2_MATCH_CACHE_ENABLED", "false")
    events = []

    result = run_condition_match_batch(
        [_record()],
        today=date(2026, 8, 7),
        policy_packages=[_package("P1"), _package("P2")],
        model_judge=_judge,
        progress_callback=events.append,
    )

    assert result["meta"]["succeeded"] == 1
    assert [event["event"] for event in events].count("enterprise_started") == 1
    policy_events = [event for event in events if event["event"] == "policy_completed"]
    assert len(policy_events) == 2
    assert [event["completed"] for event in policy_events] == [1, 2]
    assert all(event["enterpriseIndex"] == 1 and event["enterpriseTotal"] == 1 for event in events)
    assert events[-1]["event"] == "enterprise_completed"
