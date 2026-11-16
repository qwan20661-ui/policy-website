"""规则表成长性评分回归测试。"""

from __future__ import annotations

from service.rule_engine_service import score_policy_against_rule_profile
from service.scoring_service import score_policy_against_enterprise
from service.evidence_tiering import classify_runtime_evidence


def _item(item_id, field_name, field_type, operator, target, rule_kind="direct", unit=""):
    return {
        "itemId": item_id,
        "itemName": field_name,
        "itemStatus": "rule",
        "enterpriseFields": [field_name],
        "policyFragment": field_name,
        "matchRule": {
            "ruleId": item_id,
            "ruleKind": rule_kind,
            "fieldType": field_type,
            "operator": operator,
            "targetValue": target,
            "unit": unit,
        },
    }


def _profile(*items):
    return {
        "name": "测试规则表",
        "schemaVersion": "test",
        "hardRuleGroups": [{"conditionNo": 1, "rawText": "测试", "conditionOperator": "AND", "conditionFieldItems": list(items)}],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }


def _soft_profile(*items):
    profile = _profile()
    profile["hardRuleGroups"] = []
    profile["softRuleGroups"] = [{
        "conditionNo": 1,
        "rawText": "测试",
        "conditionOperator": "AND",
        "conditionFieldItems": list(items),
    }]
    return profile


def test_soft_lower_bound_uses_thirty_point_base_plus_target_ratio():
    result = score_policy_against_rule_profile(
        {"职工总数": 3},
        {"title": "成长性测试"},
        _soft_profile(_item("1", "职工总数", "number", "gte", 50)),
    )

    assert result["hard_pass"] is True
    assert result["final_score"] == 36.0
    assert result["条件匹配详情"][0]["evalStatus"] == "pass"
    assert result["条件匹配详情"][0]["得分"] == 36.0


def test_hard_lower_bound_failure_zeroes_policy_score():
    result = score_policy_against_rule_profile(
        {"职工总数": 3},
        {"title": "硬性人数测试"},
        _profile(_item("1", "职工总数", "number", "gte", 50)),
    )

    assert result["hard_pass"] is False
    assert result["final_score"] == 0.0
    assert result["条件匹配详情"][0]["evalStatus"] == "fail"
    assert result["条件匹配详情"][0]["得分"] == 0.0


def test_upper_bound_exceeded_is_hard_failure():
    result = score_policy_against_rule_profile(
        {"职工总数": 600},
        {"title": "规模上限测试"},
        _profile(_item("1", "职工总数", "number", "lte", 500)),
    )

    assert result["hard_pass"] is False
    assert result["final_score"] == 0.0
    assert result["条件匹配详情"][0]["evalStatus"] == "fail"


def test_missing_hard_qualitative_item_defaults_to_ten_and_cannot_recommend_alone():
    result = score_policy_against_rule_profile(
        {},
        {"title": "定性默认通过测试"},
        _profile(_item("1", "企业信用", "string", "llm_judge", "无重大违法")),
    )

    detail = result["条件匹配详情"][0]
    assert result["hard_pass"] is True
    assert result["final_score"] == 10.0
    assert detail["evalStatus"] == "pass"
    assert detail["passSource"] == "default_missing_assumed"
    assert detail["得分"] == 10.0
    assert result["默认假定条件项数"] == 1
    assert result["是否推荐"] == 0
    assert result["规则判定模型已启用"] is False


def test_runtime_evidence_tiering_ignores_rule_table_rule_kind_for_final_score():
    profile = _profile(_item("a-1", "职工总数", "number", "lte", 500))
    profile["softRuleGroups"] = [
        {
            "conditionNo": 2,
            "rawText": "成长指标",
            "conditionOperator": "AND",
            "conditionFieldItems": [_item("b-1", "营收", "number", "gte", 2000, "reference")],
        }
    ]

    result = score_policy_against_rule_profile(
        {"职工总数": 100, "营收": 100},
        {"title": "A/B 权重测试"},
        profile,
    )

    assert result["hard_pass"] is True
    assert result["A类直接匹配得分"] == 67.5
    assert result["B类间接匹配得分"] == 0.0
    assert result["A类权重"] == 100.0
    assert result["final_score"] == 67.5
    assert [item["evidenceTier"] for item in result["条件匹配详情"]] == ["A1_exact", "A1_exact"]

    profile["softRuleGroups"][0]["conditionFieldItems"][0]["matchRule"]["ruleKind"] = "direct"
    direct_result = score_policy_against_rule_profile(
        {"职工总数": 100, "营收": 100},
        {"title": "规则表分类不影响评分"},
        profile,
    )
    assert direct_result["final_score"] == result["final_score"]


def test_balanced_condition_mean_prevents_one_verified_item_from_overriding_nine_missing_items():
    items = [_item("verified", "营业收入", "number", "gte", 1000)]
    items.extend(_item(f"missing-{index}", f"人才条件{index}", "string", "llm_judge", "满足") for index in range(9))

    result = score_policy_against_rule_profile(
        {"营业收入": 2000},
        {"title": "人才项目低证据误推荐回归"},
        _soft_profile(*items),
    )

    assert result["final_score"] == 19.0
    assert result["是否推荐"] == 0
    assert result["证据覆盖率"] == 0.1
    assert result["默认假定条件项数"] == 9


def test_local_standard_money_fields_are_converted_from_wanyuan_to_yiyuan():
    enterprise = {
        "销售收入(万元)": 2399.52,
        "资产总额(万元)": 976.51,
        "_enterprise_standard_index": {
            "销售收入(万元)": {"标准值": 2399.52, "单位": "万元"},
            "资产总额(万元)": {"标准值": 976.51, "单位": "万元"},
        },
    }
    result = score_policy_against_rule_profile(
        enterprise,
        {"title": "科技型中小企业金额单位回归"},
        _soft_profile(
            _item("revenue", "年销售收入", "number", "lte", 2, unit="亿元"),
            _item("assets", "资产规模", "number", "lte", 2, unit="亿元"),
        ),
    )

    details = {item["itemId"]: item for item in result["条件匹配详情"]}
    assert details["revenue"]["企业值"] == "0.239952 亿元"
    assert details["assets"]["企业值"] == "0.097651 亿元"
    assert details["revenue"]["得分"] == 100.0
    assert details["assets"]["得分"] == 100.0
    assert result["final_score"] == 100.0


def test_rejected_model_evidence_can_be_replaced_by_independent_local_fact():
    tier = classify_runtime_evidence({
        "status": "pass",
        "pass_source": "semantic_quantitative_rule",
        "resolved_values": [2399.52],
        "semantic_fact": {"errorCode": "semantic_evidence_path_invalid"},
    })

    assert tier.tier == "A1_exact"
    assert tier.bucket == "verified"
    assert tier.evidence_backed is True


def test_missing_money_field_keeps_real_value_null_and_scores_ten():
    result = score_policy_against_rule_profile(
        {},
        {"title": "缺失金额暂定分测试"},
        _profile(_item("1", "研发费用总额(万元)", "number", "gte", 100, unit="万元")),
    )

    detail = result["条件匹配详情"][0]
    assert detail["企业值"] is None
    assert detail["evalStatus"] == "pass"
    assert detail["passSource"] == "default_missing_assumed"
    assert detail["得分"] == 10.0
    assert result["final_score"] == 10.0
    assert result["待确认条件项数"] == 0
    assert result["默认假定条件项数"] == 1
    assert result["是否推荐"] == 0


def test_legacy_scoring_converts_internal_fraction_ratio_to_policy_percentage():
    result = score_policy_against_enterprise(
        {
            "_enterprise_standard_index": {
                "科技人员占企业职工总数比例": {"标准值": 0.1481, "数据状态": "原始"},
            }
        },
        {
            "title": "旧链路比例测试",
            "structured_conditions": [{"条件名称": "科技人员占比不低于10%"}],
        },
    )

    detail = result["条件匹配详情"][0]
    assert detail["企业值"] == 14.81
    assert detail["evalStatus"] == "pass"
    assert result["final_score"] == 100.0
    assert result["是否推荐"] == 1


def test_legacy_scoring_uses_ten_for_missing_and_thirty_for_soft_upper_miss():
    missing_result = score_policy_against_enterprise(
        {
            "_enterprise_standard_index": {
                "销售收入(万元)": {"标准值": 1000, "数据状态": "原始"},
                "主营业务收入(万元)": {"标准值": 1000, "数据状态": "原始"},
                "资产总额(万元)": {"标准值": None, "数据状态": "缺失"},
            }
        },
        {
            "title": "旧链路待确认门禁测试",
            "structured_conditions": [
                {"条件名称": "销售收入不低于100万元"},
                {"条件名称": "主营业务收入不低于100万元"},
                {"条件名称": "资产总额不低于100万元"},
            ],
        },
    )
    failed_result = score_policy_against_enterprise(
        {"_enterprise_standard_index": {"销售收入(万元)": {"标准值": 200, "数据状态": "原始"}}},
        {
            "title": "旧链路失败门禁测试",
            "structured_conditions": [{"条件名称": "销售收入不超过100万元"}],
        },
    )

    assert missing_result["final_score"] == 70.0
    assert missing_result["待确认条件项数"] == 0
    assert missing_result["条件匹配详情"][2]["evalStatus"] == "pass"
    assert missing_result["条件匹配详情"][2]["得分"] == 10.0
    assert missing_result["条件匹配详情"][2]["企业值"] is None
    assert missing_result["条件匹配详情"][2]["passSource"] == "default_missing_assumed"
    assert missing_result["默认假定条件项数"] == 1
    assert missing_result["是否推荐"] == 1
    assert failed_result["final_score"] == 30.0
    assert failed_result["未满足条件项数"] == 0
    assert failed_result["是否推荐"] == 0
