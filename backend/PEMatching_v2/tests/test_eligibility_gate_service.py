# -*- coding: utf-8 -*-
"""T4-T5 条件、组、资格门禁特征测试。"""

from __future__ import annotations

import json

from service.eligibility import enterprise_adapter as adapter
from service.eligibility import gate_service as gate


def _install_mapping(monkeypatch, tmp_path):
    path = tmp_path / "field_mapping.json"
    path.write_text(json.dumps({"schemaVersion": "pematching-v2-eligibility-field-map-v1", "mappings": [
        {"v1Field": "收入", "v2Paths": ["$.metrics.revenueWan"], "semanticCategory": "financial.revenue", "canonicalUnit": "万元", "yearPolicy": "latest_available", "mappingStatus": "verified"},
        {"v1Field": "年主营业务收入", "v2Paths": ["$.metrics.mainBusinessIncomeWan"], "semanticCategory": "financial.main_business_revenue", "canonicalUnit": "万元", "yearPolicy": "latest_available", "mappingStatus": "verified"},
        {"v1Field": "研发投入占比", "v2Paths": ["$.metrics.rdExpenseRatioPct"], "semanticCategory": "financial.rd_expense_ratio", "canonicalUnit": "%", "yearPolicy": "latest_available", "mappingStatus": "verified"},
        {"v1Field": "资质", "v2Paths": ["$.qualifications.isHighTechEnterprise"], "semanticCategory": "qualification.high_tech", "canonicalUnit": "", "yearPolicy": "current", "mappingStatus": "verified"}
    ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(adapter, "ELIGIBILITY_FIELD_MAPPING_PATH", path)
    adapter.reset_cache()


def _item(name, field, operator, target=None, unit=""):
    rule = {"ruleKind": "direct", "fieldType": "number" if operator in {"gt", "gte", "lt", "lte", "eq"} else "boolean", "operator": operator, "unit": unit}
    if target is not None:
        rule["targetValue"] = target
    return {"itemId": name, "semanticItemKey": name, "itemName": name, "enterpriseFields": [field], "matchRule": rule}


def _profile(*, hard=None, soft=None, special=None, coverage="complete"):
    return {"fusionPolicyId": "P1", "policyTitle": "测试政策", "coverage": coverage, "hardRuleGroups": hard or [], "softRuleGroups": soft or [], "specialPassRuleGroups": special or []}


def _group(items, operator="AND"):
    return {"conditionNo": 1, "conditionOperator": operator, "conditionFieldItems": items}


def test_金额先换算再比较且真实零不是缺失(tmp_path, monkeypatch):
    _install_mapping(monkeypatch, tmp_path)
    profile = _profile(soft=[_group([_item("收入", "收入", "lte", 2, "亿元")])])
    monkeypatch.setattr(gate, "get_profile", lambda _: profile)

    result = gate.evaluate_policy({"metrics": {"revenueWan": 0}}, "P1")

    assert result["gateStatus"] == "direct_push"
    assert result["conditionDetails"][0]["normalisedEnterpriseValue"] == 0
    assert "×" in result["conditionDetails"][0]["conversionFormula"]


def test_硬性明确失败归零(tmp_path, monkeypatch):
    _install_mapping(monkeypatch, tmp_path)
    profile = _profile(hard=[_group([_item("硬门槛", "收入", "lte", 100, "万元")])])
    monkeypatch.setattr(gate, "get_profile", lambda _: profile)

    result = gate.evaluate_policy({"metrics": {"revenueWan": 101}}, "P1")

    assert result["gateStatus"] == "not_recommended"
    assert result["conditionMatchScore"] == 0
    assert "HARD_CONDITION_FAILED" in result["gateReasons"]


def test_缺失沿用10分但绝不直推(tmp_path, monkeypatch):
    _install_mapping(monkeypatch, tmp_path)
    profile = _profile(soft=[_group([_item("收入", "收入", "gte", 100, "万元")])])
    monkeypatch.setattr(gate, "get_profile", lambda _: profile)

    result = gate.evaluate_policy({"metrics": {}}, "P1")

    assert result["conditionMatchScore"] == 10
    assert result["gateStatus"] == "needs_supplement"
    assert result["directPushAllowed"] is False


def test_研发投入占比使用同一原始字段并按百分数比较(tmp_path, monkeypatch):
    _install_mapping(monkeypatch, tmp_path)
    profile = _profile(soft=[_group([_item("研发投入占比", "研发投入占比", "gte", 3, "%")])])
    monkeypatch.setattr(gate, "get_profile", lambda _: profile)

    result = gate.evaluate_policy({"metrics": {"rdExpenseRatioPct": 30.65}}, "P1")

    assert result["conditionMatchScore"] == 100
    assert result["conditionDetails"][0]["normalisedEnterpriseValue"] == 30.65
    assert result["conditionDetails"][0]["enterpriseUnit"] == "%"


def test_主营业务收入使用精确字段并按真实数值评分(tmp_path, monkeypatch):
    _install_mapping(monkeypatch, tmp_path)
    profile = _profile(soft=[_group([_item("年主营业务收入", "年主营业务收入", "gte", 5, "亿元")])])
    monkeypatch.setattr(gate, "get_profile", lambda _: profile)

    result = gate.evaluate_policy({"metrics": {"revenueWan": 99999, "mainBusinessIncomeWan": 2399.52}}, "P1")

    detail = result["conditionDetails"][0]
    assert detail["evalStatus"] == "fail"
    assert detail["enterpriseValue"] == 2399.52
    assert detail["normalisedEnterpriseValue"] == 0.239952
    assert detail["score"] == 0.348
    assert result["conditionMatchScore"] == 34.8


def test_模型逐条件事实可覆盖未登记静态映射的字段(monkeypatch):
    item = _item("主营业务收入", "不存在于静态映射", "gte", 5, "亿元")
    profile = _profile(soft=[_group([item])])
    monkeypatch.setattr(gate, "get_profile", lambda _: profile)
    monkeypatch.setattr(gate, "resolve_condition_facts", lambda *_args, **_kwargs: {
        "主营业务收入": {
            "evidenceTier": "A1_exact",
            "bucket": "verified",
            "reason": "模型逐条件选取企业事实，服务端已回读证据路径",
            "value": 23995200.0,
            "sourcePaths": ["$._eligibilityEvidence.operation.mainBusinessIncome"],
            "unit": "元",
            "semanticCategory": "financial.main_business_revenue",
            "modelJudgement": "unknown",
            "modelEvidence": {"factStatus": "exact", "evidenceText": "主营业务收入为 23995200 元"},
        }
    })

    result = gate.evaluate_policy({"metrics": {}}, "P1", use_llm_evidence=True)

    detail = result["conditionDetails"][0]
    assert detail["evalStatus"] == "fail"
    assert detail["normalisedEnterpriseValue"] == 0.239952
    assert detail["score"] == 0.348
    assert detail["evidence"]["sourcePaths"] == ["$._eligibilityEvidence.operation.mainBusinessIncome"]
    assert result["modelEvidence"] == {"requested": True, "resolvedConditionCount": 1}


def test_直通车只由精确事实触发(tmp_path, monkeypatch):
    _install_mapping(monkeypatch, tmp_path)
    profile = _profile(special=[_group([_item("资质", "资质", "is_true")])])
    monkeypatch.setattr(gate, "get_profile", lambda _: profile)

    result = gate.evaluate_policy({"qualifications": {"isHighTechEnterprise": True}}, "P1")

    assert result["specialPassHit"] is True
    assert result["conditionMatchScore"] == 90
    assert result["gateStatus"] == "direct_push"


def test_无画像保留为不可评估(monkeypatch):
    monkeypatch.setattr(gate, "get_profile", lambda _: None)

    result = gate.evaluate_policy({}, "不存在")

    assert result["gateStatus"] == "not_evaluable"
    assert result["gateReasons"] == ["RULE_PROFILE_NOT_FOUND"]


def test_单条政策异常不影响同请求的其他政策(monkeypatch):
    def flaky(_profile, policy_id, **_kwargs):
        if policy_id == "坏政策":
            raise RuntimeError("规则损坏")
        return {"fusionPolicyId": policy_id, "gateStatus": "not_evaluable"}

    monkeypatch.setattr(gate, "evaluate_policy", flaky)
    monkeypatch.setattr(gate, "load_snapshot", lambda: ({"contentSha256": "hash"}, {}))

    result = gate.evaluate_policies({}, ["好政策", "坏政策"])

    assert [item["fusionPolicyId"] for item in result["eligibilityEvaluations"]] == ["好政策", "坏政策"]
    assert result["eligibilityEvaluations"][1]["gateReasons"][0] == "POLICY_EVALUATION_FAILED"
