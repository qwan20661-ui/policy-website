# -*- coding: utf-8 -*-
"""T6 评分验证器的数学、证据和排除边界。"""

from __future__ import annotations

from datetime import date

from service.condition_scoring import condition_catalog, score_policy_match


def _profile(revenue=2000):
    return {
        "metrics": {"revenueWan": revenue, "employeeCount": 0},
        "qualifications": {}, "registerInfo": {}, "industryInfo": {}, "credit": {}, "eligibilityFacts": {},
        "_rawFactIndex": {"facts": []},
    }


def _package(operator="gte", target=2000):
    return {
        "fusionPolicyId": "P1", "title": "测试政策", "policyContentHash": "a" * 64,
        "policyInfo": {"policyTitle": "测试政策"},
        "conditions": [{"applicationRequirement": "营业收入不低于2000万元。", "applicationStartTime": "2026-08-01", "applicationEndTime": "2026-08-31", "applicationInstruction": "在线申报"}],
        "ruleTableJson": {"hardRuleGroups": [], "softRuleGroups": [{
            "conditionNo": 1, "conditionBranchId": "C1", "conditionOperator": "AND", "conditionFieldItems": [{
                "itemId": "1", "itemName": "营业收入", "policyFragment": "营业收入不低于2000万元", "enterpriseFields": ["营业收入"],
                "matchRule": {"ruleId": "S1", "operator": operator, "targetValue": target, "unit": "万元"},
            }],
        }], "specialPassRuleGroups": []},
    }


def _raw(package, values):
    output = {
        "schemaVersion": "policy-condition-match-output-v2",
        "sourceConflicts": [], "conditionResults": [], "discoveredConditions": [],
    }
    for descriptor, value in zip(condition_catalog(package), values):
        path, actual, status, score = value
        output["conditionResults"].append({
            "conditionKey": descriptor["conditionKey"],
            "status": status, "failureEffect": "growable" if status == "partially_satisfied" else "none",
            "score": score, "evidencePaths": [] if path is None else [path],
            "reason": "测试", "confidence": "high",
        })
    return output


def test_最低门槛服务端精确复算1400除以2000等于70():
    package = _package()
    raw = _raw(package, [("$.metrics.revenueWan", 1400, "partially_satisfied", 1)])

    result = score_policy_match(package, _profile(1400), raw, today=date(2026, 8, 7))

    assert result["verifiedConditionScore"] == 70
    assert result["finalScore"] == 73
    assert result["conditionResults"][0]["scoreSource"] == "server_numeric"


def test_AND取平均_OR取最大且正式分数由服务端计算():
    package = _package()
    items = package["ruleTableJson"]["softRuleGroups"][0]["conditionFieldItems"]
    items.append({
        "itemId": "2", "itemName": "员工数", "policyFragment": "员工数不少于10人", "enterpriseFields": ["员工数"],
        "matchRule": {"ruleId": "S2", "operator": "gte", "targetValue": 10, "unit": "人"},
    })
    raw = _raw(package, [
        ("$.metrics.revenueWan", 2000, "satisfied", 100),
        ("$.metrics.employeeCount", 0, "not_satisfied", 0),
    ])

    result = score_policy_match(package, _profile(), raw, today=date(2026, 8, 7))

    assert result["groupResults"][0]["score"] == 50
    assert result["verifiedConditionScore"] == 50

    package["ruleTableJson"]["softRuleGroups"][0]["conditionOperator"] = "OR"
    or_result = score_policy_match(package, _profile(), raw, today=date(2026, 8, 7))
    assert or_result["groupResults"][0]["score"] == 100


def test_不可回读证据转缺失分且不推荐_上限超标直接排除():
    package = _package()
    fake = _raw(package, [("$.metrics.notExists", 9999, "satisfied", 100)])
    result = score_policy_match(package, _profile(), fake, today=date(2026, 8, 7))
    assert result["conditionResults"][0]["status"] == "missing"
    assert result["conditionResults"][0]["score"] == 10
    assert result["recommendable"] is False

    upper = _package(operator="lte", target=1000)
    over = _raw(upper, [("$.metrics.revenueWan", 2000, "satisfied", 100)])
    upper_result = score_policy_match(upper, _profile(), over, today=date(2026, 8, 7))
    assert upper_result["directExclusion"]["code"] == "IRREVERSIBLE_UPPER_BOUND"


def test_语义范围排除由服务端根据已核验证据生成():
    package = _package(operator="llm_judge", target=None)
    raw = _raw(package, [("$.metrics.revenueWan", 2000, "not_satisfied", 0)])
    raw["conditionResults"][0]["failureEffect"] = "out_of_scope"
    raw["conditionResults"][0]["reason"] = "企业所属范围不在政策支持对象内"

    result = score_policy_match(package, _profile(), raw, today=date(2026, 8, 7))

    assert result["directExclusion"]["code"] == "SEMANTIC_OUT_OF_SCOPE"
    assert result["directExclusion"]["evidence"][0]["value"] == 2000


def test_6508规则表只有一条时正文其余六条不参与评分():
    package = _package()
    package["conditions"][0]["applicationRequirement"] = "\n".join([
        "1、企业依法在厦注册，且为研发项目的牵头实施单位。",
        "2、研发项目应由申报企业自筹资金、自行立项。",
        "3、研发项目应于2021年之后立项、2024年之前完成验收。",
        "4、研发项目自主研发费用应达500万元及以上。",
        "5、研发项目未获得过国家、省、市科技项目立项。",
        "6、企业没有逾期未验收项目且不在科研失信惩戒期内。",
        "7、每家企业每年最多申报两个项目。",
    ])
    raw = _raw(package, [("$.metrics.revenueWan", 2000, "satisfied", 100)])
    discovered_texts = [
        "企业依法在厦注册，且为研发项目的牵头实施单位",
        "研发项目应由申报企业自筹资金、自行立项",
        "研发项目应于2021年之后立项、2024年之前完成验收",
        "研发项目未获得过国家、省、市科技项目立项",
        "企业没有逾期未验收项目且不在科研失信惩戒期内",
        "每家企业每年最多申报两个项目",
    ]
    raw["discoveredConditions"] = [
        {
            "conditionKey": f"hard:discovered:{index}",
            "status": "missing",
            "failureEffect": "missing_data",
            "score": 10,
            "evidencePaths": [],
            "reason": "企业档案没有项目级事实",
            "confidence": "low",
            "sourceText": source_text,
            "groupKey": "hard:discovered",
            "groupType": "hard",
            "groupOperator": "AND",
        }
        for index, source_text in enumerate(discovered_texts, start=1)
    ]

    result = score_policy_match(package, _profile(), raw, today=date(2026, 8, 7))

    assert len(result["conditionResults"]) == 1
    assert result["verifiedConditionScore"] == 100
    assert result["reason"] == "已核验 1/1 个存在企业事实的政策条件。"
    assert result["invalidDiscoveredConditionCount"] == 6


def test_规则表为空时允许从正文回退发现条件():
    package = _package()
    package["ruleTableJson"] = {}
    source_text = package["conditions"][0]["applicationRequirement"]
    raw = _raw(package, [])
    raw["discoveredConditions"] = [{
        "conditionKey": "soft:discovered:1",
        "status": "satisfied",
        "failureEffect": "none",
        "score": 100,
        "evidencePaths": ["$.metrics.revenueWan"],
        "reason": "营业收入达到要求",
        "confidence": "high",
        "sourceText": source_text,
        "groupKey": "soft:discovered",
        "groupType": "soft",
        "groupOperator": "AND",
    }]

    result = score_policy_match(package, _profile(), raw, today=date(2026, 8, 7))

    assert len(result["conditionResults"]) == 1
    assert result["conditionResults"][0]["sourceType"] == "discovered"
    assert result["verifiedConditionScore"] == 100
    assert result["invalidDiscoveredConditionCount"] == 0
