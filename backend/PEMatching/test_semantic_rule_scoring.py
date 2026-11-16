"""模型语义事实层与确定性规则评分的回归测试。"""

from __future__ import annotations

import json

import pytest

from script.run_fusion_policy_semantic_match import (
    rescore_existing_results,
    saved_semantic_assessment,
    validate_existing_result_metadata,
)
from service.matching_service import _apply_recommendation_guards
from service.rule_engine_service import _calculate_tech_sme_evaluation, score_policy_against_rule_profile


def _item(item_id, field_name, field_type, operator, target, unit=""):
    return {
        "itemId": item_id,
        "itemName": field_name,
        "itemStatus": "rule",
        "enterpriseFields": [field_name],
        "policyFragment": field_name,
        "matchRule": {"ruleKind": "direct", "fieldType": field_type, "operator": operator, "targetValue": target, "unit": unit},
    }


def _profile(*items):
    return {
        "name": "语义事实测试规则表",
        "schemaVersion": "test",
        "hardRuleGroups": [{"conditionNo": 1, "rawText": "测试", "conditionOperator": "AND", "conditionFieldItems": list(items)}],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }


def _assessment(facts):
    return {"enabled": True, "model": "test-model", "facts": facts}


def test_semantic_numeric_fact_still_uses_local_upper_bound_gate():
    result = score_policy_against_rule_profile(
        {"原始数据": {"finance": {"revenue": "21000 万元"}}},
        {"title": "数值映射测试"},
        _profile(_item("1", "营收规模", "number", "lte", 2)),
        _assessment(
            {
                "1": {
                    "status": "supported", "mappingMethod": "semantic", "mappedFields": ["$.finance.revenue"],
                    "normalizedValue": 2.1, "normalizedUnit": "亿元", "displayValue": "21000 万元",
                    "confidence": 0.97, "evidence": [{"sourcePath": "$.finance.revenue", "sourceText": "21000 万元"}],
                    "reason": "年销售收入换算后为 2.1 亿元。",
                }
            }
        ),
    )
    detail = result["条件匹配详情"][0]
    assert result["hard_pass"] is False
    assert result["final_score"] == 0.0
    assert detail["evalStatus"] == "fail"
    assert detail["匹配方式"] == "semantic"
    assert detail["证据路径"] == ["$.finance.revenue"]


def test_direct_money_fact_uses_raw_value_and_unit_before_scoring():
    result = score_policy_against_rule_profile(
        {
            "原始数据": {
                "营收规模": 0.204191,
                "营收规模单位": "亿元",
            }
        },
        {"title": "科技型中小企业评价"},
        _profile(_item("2-2", "营收规模", "number", "lte", 20000, "万元")),
        _assessment(
            {
                "2-2": {
                    "status": "supported", "mappingMethod": "direct", "mappedFields": ["$.营收规模"],
                    "normalizedValue": 2.04191, "normalizedUnit": "亿元", "displayValue": "2.04191 亿元",
                    "confidence": 0.9, "evidence": [{"sourcePath": "$.营收规模", "sourceText": "0.204191"}],
                    "reason": "模型错误地将原始值放大了十倍。",
                }
            }
        ),
    )
    detail = result["条件匹配详情"][0]
    fact = result["模型语义事实"]["2-2"]
    assert result["hard_pass"] is True
    assert detail["evalStatus"] == "pass"
    assert detail["企业值"] == "2041.91 万元"
    assert fact["normalizedValue"] == 0.204191
    assert fact["normalizedUnit"] == "亿元"
    assert "原始字段 营收规模=0.204191亿元" in fact["reason"]


def test_qualification_boolean_cannot_be_used_as_revenue_evidence():
    result = score_policy_against_rule_profile(
        {
            "销售收入(万元)": 1156.029964,
            "原始数据": {
                "honorQualificationList": [{"isHighTechEnterprise": "1"}],
                "operationInfoList": [{"operatingIncome": 11560299.64}],
            },
        },
        {"title": "营收资格测试"},
        _profile(_item("revenue", "营收规模", "number", "gt", 1000, "万元")),
        _assessment(
            {
                "revenue": {
                    "status": "supported", "mappingMethod": "direct",
                    "mappedFields": ["$.honorQualificationList[0].isHighTechEnterprise"],
                    "normalizedValue": 1, "normalizedUnit": "万元", "displayValue": "1 万元",
                    "confidence": 0.9,
                    "evidence": [{"sourcePath": "$.honorQualificationList[0].isHighTechEnterprise", "sourceText": "1"}],
                    "reason": "错误引用高新技术企业资格。",
                }
            }
        ),
    )

    detail = result["条件匹配详情"][0]
    assert detail["企业值"] == "1156.029964"
    assert detail["得分"] == 100.0
    assert result["是否推荐"] == 1
    assert result["模型语义事实"]["revenue"]["status"] == "missing"


def test_rd_personnel_ratio_cannot_prove_education_ratio():
    item = _item("education", "大专以上学历员工占比", "number", "gte", 50, "%")
    profile = _profile(item)
    result = score_policy_against_rule_profile(
        {"原始数据": {"operationInfoList": [{"rdPersonnelRatio": 0.6}]}},
        {"title": "学历人员比例测试"},
        profile,
        _assessment(
            {
                "education": {
                    "status": "supported",
                    "mappingMethod": "semantic",
                    "mappedFields": ["$.operationInfoList[0].rdPersonnelRatio"],
                    "normalizedValue": 60,
                    "normalizedUnit": "%",
                    "displayValue": "60%",
                    "confidence": 0.95,
                    "evidence": [{"sourcePath": "$.operationInfoList[0].rdPersonnelRatio", "sourceText": "0.6"}],
                    "reason": "错误地使用研发人员占比。",
                }
            }
        ),
    )

    detail = result["条件匹配详情"][0]
    fact = result["模型语义事实"]["education"]
    assert fact["status"] == "missing"
    assert fact["errorCode"] == "semantic_evidence_incompatible"
    assert "语义证据类型校验拒绝" in fact["reason"]
    assert detail["得分"] == 10.0
    assert detail["passSource"] == "default_missing_assumed"


def test_explicit_zero_numeric_value_does_not_fall_back_to_missing_default_score():
    result = score_policy_against_rule_profile(
        {"技术合同成交额(万元)": 0},
        {"title": "技术交易奖励测试"},
        _profile(_item("contract", "技术交易额", "number", "gt", 3000, "万元")),
        _assessment(
            {
                "contract": {
                    "status": "missing", "mappingMethod": "none", "mappedFields": [],
                    "normalizedValue": None, "normalizedUnit": "", "displayValue": "", "confidence": 0.0,
                    "branchApplicability": "unknown", "evidence": [], "reason": "模型未识别经营字段。",
                }
            }
        ),
    )

    detail = result["条件匹配详情"][0]
    assert detail["企业值"] == "0"
    assert detail["得分"] == 0.0
    assert detail["passSource"] == "semantic_quantitative_rule"
    assert result["是否推荐"] == 0


def test_legacy_recommendation_guard_entrypoint_does_not_block_rule_score():
    guarded = _apply_recommendation_guards(
        {"注册地": "厦门市同安区软件园", "企业简介": "芯片模组企业"},
        {
            "policy_info": {"applicableScope": "集美区"},
            "raw_summary": {"applicationEndTime": "2026.07.26"},
            "structured_conditions": [
                {"条件名称": "适用对象", "政策要求": "技术转移机构"},
            ],
        },
        {"是否推荐": 1, "风险提示": []},
    )

    assert guarded["是否推荐"] == 1
    assert guarded.get("匹配状态") != "资格不满足"
    assert guarded["推荐阻断项"] == []
    assert guarded["资格前置校验"] == {"通过": True, "阻断项": []}


def test_saved_semantic_assessment_rebuilds_tech_sme_metric_evidence_from_source_record():
    enterprise = {
        "原始数据": {
            "科技人员占企业职工总数比例": 0.28,
            "研发费用占销售收入总额比例": 0.0981,
            "研发费用占成本费用支出总额比例": 0.0985,
            "I类知识产权数量": 2,
            "II类知识产权数量": 44,
        }
    }
    assessment = saved_semantic_assessment(
        {
            "科技型中小企业评价得分": {
                "科技人员占比": 0.28,
                "研发费用占销售收入比例": 0.0981,
                "研发费用占成本费用比例": 0.0985,
                "I类知识产权数量": 2,
                "II类知识产权数量": 44,
            }
        },
        enterprise,
    )
    assert assessment["techSmeEvaluation"]["techPersonnelRatio"]["evidence"] == [
        {"sourcePath": "$.科技人员占企业职工总数比例", "sourceText": "0.28"}
    ]
    assert assessment["techSmeEvaluation"]["classIICount"]["evidence"] == [
        {"sourcePath": "$.II类知识产权数量", "sourceText": "44"}
    ]


def test_rescore_existing_rejects_an_entry_without_the_target_policy():
    with pytest.raises(ValueError, match="拒绝使用其他政策结果重评分"):
        rescore_existing_results(
            [{"企业名称": "企业A", "企业信息": {"企业ID": "A"}, "政策匹配结果": [{"政策标题": "其他政策"}]}],
            [{"企业ID": "A", "企业名称": "企业A", "原始数据": {}}],
            {"id": "6548", "title": "目标政策", "ruleTableJson": {"schemaVersion": "1.1"}},
        )


def test_rescore_existing_rejects_an_entry_without_rule_table_version():
    with pytest.raises(ValueError, match="缺少规则表版本"):
        rescore_existing_results(
            [{
                "企业名称": "企业A",
                "企业信息": {"企业ID": "A"},
                "政策匹配结果": [{"政策标题": "目标政策", "政策ID": "6548"}],
            }],
            [{"企业ID": "A", "企业名称": "企业A", "原始数据": {}}],
            {"id": "6548", "title": "目标政策", "ruleTableJson": {"schemaVersion": "1.1"}},
        )


def test_rescore_existing_rejects_summary_with_a_different_rule_table_version(tmp_path):
    output_file = tmp_path / "1632.json"
    output_file.write_text("[]", encoding="utf-8")
    summary_file = tmp_path / "1632_summary.json"
    summary_file.write_text(
        json.dumps(
            {
                "政策接口": "http://127.0.0.1:8080/api/fusion-classification/fusion-detail?fusionPolicyId=6548",
                "政策名称": "目标政策",
                "规则表版本": "1.0",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="规则表版本不一致"):
        validate_existing_result_metadata(
            output_file,
            {"id": "6548", "title": "目标政策", "ruleTableJson": {"schemaVersion": "1.1"}},
        )


def test_semantic_contradiction_blocks_qualitative_hard_rule():
    result = score_policy_against_rule_profile(
        {"原始数据": {"credit": {"penalty": "存在重大违法记录"}}},
        {"title": "定性反向证据测试"},
        _profile(_item("1", "企业信用", "string", "llm_judge", "无重大违法")),
        _assessment(
            {
                "1": {
                    "status": "contradicted", "mappingMethod": "semantic", "mappedFields": ["$.credit.penalty"],
                    "normalizedValue": None, "normalizedUnit": "", "displayValue": "存在重大违法记录",
                    "confidence": 0.96, "evidence": [{"sourcePath": "$.credit.penalty", "sourceText": "存在重大违法记录"}],
                    "reason": "接口字段明确记录重大违法。",
                }
            }
        ),
    )
    assert result["hard_pass"] is False
    assert result["条件匹配详情"][0]["passSource"] == "semantic_contradicted"


def test_missing_qualitative_hard_fact_defaults_to_ten_and_is_traceable():
    result = score_policy_against_rule_profile(
        {},
        {"title": "定性缺失测试"},
        _profile(_item("1", "企业信用", "string", "llm_judge", "无重大违法")),
        _assessment(
            {
                "1": {
                    "status": "missing", "mappingMethod": "none", "mappedFields": [], "normalizedValue": None,
                    "normalizedUnit": "", "displayValue": "", "confidence": 0.0, "evidence": [], "reason": "未提供信用字段。",
                }
            }
        ),
    )
    detail = result["条件匹配详情"][0]
    assert result["hard_pass"] is True
    assert detail["evalStatus"] == "pass"
    assert detail["passSource"] == "default_missing_assumed"
    assert detail["模型事实状态"] == "missing"
    assert detail["得分"] == 10.0
    assert detail["计分归类"] == "unverified"
    assert detail["evidenceTier"] == "U_missing"
    assert detail["企业值"] is None


def test_missing_qualitative_fact_must_not_display_an_incompatible_candidate_value():
    result = score_policy_against_rule_profile(
        {"企业资质": "是"},
        {"title": "施工资质测试"},
        _profile(_item("qualification", "施工总承包资质", "text", "equals", "施工总承包资质")),
        _assessment(
            {
                "qualification": {
                    "status": "missing",
                    "mappingMethod": "none",
                    "mappedFields": [],
                    "confidence": 0.0,
                    "evidence": [],
                    "reason": "企业资料仅包含通用资质标记，不能证明施工总承包资质。",
                }
            }
        ),
    )

    detail = result["条件匹配详情"][0]
    assert detail["得分"] == 10.0
    assert detail["passSource"] == "default_missing_assumed"
    assert detail["企业值"] is None
    assert detail["企业字段状态"] == "rule"
    assert result["是否推荐"] == 0


def test_missing_soft_eligibility_fact_defaults_to_ten_and_cannot_recommend_alone():
    item = _item("1", "企业资质", "string", "llm_judge", "具备高新技术企业资格")
    item["itemStatus"] = "unresolved"
    item["matchRule"]["ruleKind"] = "reference"
    profile = {
        "name": "软性资格事实测试",
        "schemaVersion": "1.2",
        "hardRuleGroups": [],
        "softRuleGroups": [{
            "conditionNo": 1,
            "rawText": "企业拥有有效期内高新技术企业资格证书",
            "conditionOperator": "AND",
            "evaluationRole": "eligibility_signal",
            "conditionFieldItems": [item],
        }],
        "specialPassRuleGroups": [],
    }
    result = score_policy_against_rule_profile(
        {},
        {"title": "软性资格事实测试"},
        profile,
        _assessment({
            "1": {
                "status": "missing", "mappingMethod": "none", "mappedFields": [],
                "normalizedValue": None, "normalizedUnit": "", "displayValue": "",
                "confidence": 0.0, "evidence": [], "reason": "企业数据未提供资质证书。",
            }
        }),
    )
    detail = result["条件匹配详情"][0]
    assert detail["evalStatus"] == "pass"
    assert detail["passSource"] == "default_missing_assumed"
    assert result["score_A_direct"] == 0.0
    assert result["score_B_indirect"] == 0.0
    assert result["未核验证据条件得分"] == 10.0
    assert result["final_score"] == 10.0
    assert result["是否推荐"] == 0


def test_api_nested_money_fact_is_recomputed_from_yuan_to_yi_yuan():
    enterprise = {
        "_source_type": "policy_recommendation_api_record",
        "原始数据": {"operationInfoList": [{"year": "2025", "operatingIncome": 40000000}]},
    }
    result = score_policy_against_rule_profile(
        enterprise,
        {"title": "营收上限测试"},
        _profile(_item("revenue", "营收规模", "number", "lte", 2, "亿元")),
        _assessment(
            {
                "revenue": {
                    "status": "supported",
                    "mappingMethod": "direct",
                    "mappedFields": ["$.operationInfoList[0].operatingIncome"],
                    "normalizedValue": 40000000,
                    "normalizedUnit": "亿元",
                    "displayValue": "40000000 亿元",
                    "confidence": 0.9,
                    "evidence": [
                        {"sourcePath": "$.operationInfoList[0].operatingIncome", "sourceText": "40000000"}
                    ],
                    "reason": "模型未正确换算单位。",
                }
            }
        ),
    )

    detail = result["条件匹配详情"][0]
    assert detail["evalStatus"] == "pass"
    assert detail["企业值"] == "0.4 亿元"
    assert result["final_score"] == 100.0
    assert result["是否推荐"] == 1
    assert result["全部规则已确认"] is True


def test_api_fraction_ratio_is_compared_as_percentage():
    enterprise = {
        "_source_type": "policy_recommendation_api_record",
        "原始数据": {"operationInfoList": [{"rdPersonnelRatio": "0.1481"}]},
    }
    result = score_policy_against_rule_profile(
        enterprise,
        {"title": "科技人员比例测试"},
        _profile(_item("ratio", "科技人员占比", "number", "gte", 10, "%")),
        _assessment(
            {
                "ratio": {
                    "status": "supported",
                    "mappingMethod": "direct",
                    "mappedFields": ["$.operationInfoList[0].rdPersonnelRatio"],
                    "normalizedValue": 0.8028,
                    "normalizedUnit": "小数",
                    "displayValue": "0.1481",
                    "confidence": 0.95,
                    "evidence": [
                        {"sourcePath": "$.operationInfoList[0].rdPersonnelRatio", "sourceText": "0.1481"}
                    ],
                    "reason": "模型错误改写了接口比例。",
                }
            }
        ),
    )

    detail = result["条件匹配详情"][0]
    assert detail["企业值"] == "14.81 %"
    assert detail["得分"] == 100.0
    assert result["是否推荐"] == 1


def test_api_tech_sme_evaluation_uses_local_fields_instead_of_model_defaults():
    evaluation = _calculate_tech_sme_evaluation(
        {
            "techSmeEvaluation": {
                "techPersonnelRatio": {"value": 0.1481},
                "rdExpenseRevenueRatio": {"value": 0.75},
                "rdExpenseCostRatio": {"value": 0.125},
                "classICount": {"value": 2},
                "classIICount": {"value": 23},
            }
        },
        {
            "_source_type": "policy_recommendation_api_record",
            "科技人员占企业职工总数比例": 0.1481,
            "研发费用占销售收入总额比例": 0.75,
            "研发费用占成本费用支出总额比例": None,
            "I类知识产权数量": 2,
            "II类知识产权数量": 0,
        },
    )

    assert evaluation["研发费用占成本费用比例"] is None
    assert evaluation["II类知识产权数量"] == 0
    assert evaluation["科技成果指标得分"] == 30
    assert evaluation["评价总分"] == 84


def test_below_hard_lower_bound_zeroes_score():
    result = score_policy_against_rule_profile(
        {"年研发投入(万元)": 200},
        {"title": "研发投入成长测试"},
        _profile(_item("rd", "年研发投入", "number", "gte", 2000, "万元")),
        None,
    )

    detail = result["条件匹配详情"][0]
    assert detail["evalStatus"] == "fail"
    assert detail["得分"] == 0.0
    assert result["final_score"] == 0.0
    assert result["是否推荐"] == 0


def test_missing_rule_evidence_scores_ten_and_balanced_mean_prevents_recommendation():
    passed = _item("pass", "营收规模", "number", "lte", 2, "亿元")
    pending = _item("pending", "指定年度增长", "string", "llm_judge", "较2023年增长")
    pending["itemStatus"] = "unresolved"
    pending["matchRule"]["ruleKind"] = "reference"
    profile = {
        "name": "待确认推荐门禁测试",
        "schemaVersion": "test",
        "hardRuleGroups": [],
        "softRuleGroups": [
            {"conditionNo": 1, "rawText": "营收", "conditionOperator": "AND", "conditionFieldItems": [passed]},
            {"conditionNo": 2, "rawText": "增长", "conditionOperator": "AND", "conditionFieldItems": [pending]},
        ],
        "specialPassRuleGroups": [],
    }
    enterprise = {"原始数据": {"revenue": 0.4}}
    result = score_policy_against_rule_profile(
        enterprise,
        {"title": "待确认推荐门禁测试"},
        profile,
        _assessment(
            {
                "pass": {
                    "status": "supported", "mappingMethod": "direct", "mappedFields": ["$.revenue"],
                    "normalizedValue": 0.4, "normalizedUnit": "亿元", "displayValue": "0.4亿元",
                    "confidence": 1.0, "evidence": [{"sourcePath": "$.revenue", "sourceText": "0.4"}], "reason": "有证据",
                },
                "pending": {
                    "status": "missing", "mappingMethod": "none", "mappedFields": [],
                    "normalizedValue": None, "normalizedUnit": "", "displayValue": "",
                    "confidence": 0.0, "evidence": [], "reason": "缺少2023年数据",
                },
            }
        ),
    )

    details = {item["itemId"]: item for item in result["条件匹配详情"]}
    assert details["pending"]["evalStatus"] == "pass"
    assert details["pending"]["passSource"] == "default_missing_assumed"
    assert details["pending"]["得分"] == 10.0
    assert result["final_score"] == 55.0
    assert result["是否推荐"] == 0
    assert result["全部规则已确认"] is True
    assert result["待确认条件项数"] == 0
    assert result["默认假定条件项数"] == 1


def test_mutually_exclusive_inapplicable_branch_is_skipped_without_lowering_score():
    low_branch = _item("low", "低金额分支", "number", "lt", 100, "万元")
    high_branch = _item("high", "高金额分支", "number", "gte", 100, "万元")
    profile = {
        "name": "互斥分支测试",
        "schemaVersion": "test",
        "hardRuleGroups": [
            {
                "conditionNo": 1,
                "rawText": "低于100万元适用低金额分支，达到100万元适用高金额分支",
                "conditionOperator": "ANY",
                "conditionFieldItems": [low_branch, high_branch],
            }
        ],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }
    result = score_policy_against_rule_profile(
        {"原始数据": {"amount": 120}},
        {"title": "互斥分支测试"},
        profile,
        _assessment(
            {
                "low": {
                    "status": "supported", "mappingMethod": "direct", "mappedFields": ["$.amount"],
                    "normalizedValue": 120, "normalizedUnit": "万元", "displayValue": "120万元",
                    "confidence": 1.0, "branchApplicability": "not_applicable",
                    "branchReason": "企业金额已达到100万元，不适用低金额分支。",
                    "evidence": [{"sourcePath": "$.amount", "sourceText": "120"}], "reason": "互斥分支判定。",
                },
                "high": {
                    "status": "supported", "mappingMethod": "direct", "mappedFields": ["$.amount"],
                    "normalizedValue": 120, "normalizedUnit": "万元", "displayValue": "120万元",
                    "confidence": 1.0, "branchApplicability": "applicable",
                    "branchReason": "企业金额达到100万元，适用高金额分支。",
                    "evidence": [{"sourcePath": "$.amount", "sourceText": "120"}], "reason": "达到门槛。",
                },
            }
        ),
    )

    details = {item["itemId"]: item for item in result["条件匹配详情"]}
    assert details["low"]["evalStatus"] == "skipped"
    assert details["high"]["evalStatus"] == "pass"
    assert result["规则组匹配详情"][0]["groupStatus"] == "pass"
    assert result["final_score"] == 100.0
    assert result["待确认条件项数"] == 0
    assert result["全部规则已确认"] is True
    assert result["是否推荐"] == 1


def test_unknown_branch_applicability_defaults_to_ten():
    result = score_policy_against_rule_profile(
        {"原始数据": {}},
        {"title": "分支适用性未知测试"},
        _profile(_item("unknown", "指定分支", "number", "gte", 100, "万元")),
        _assessment(
            {
                "unknown": {
                    "status": "missing", "mappingMethod": "none", "mappedFields": [],
                    "normalizedValue": None, "normalizedUnit": "", "displayValue": "",
                    "confidence": 0.0, "branchApplicability": "unknown",
                    "branchReason": "接口未提供判断该分支所需的企业金额。",
                    "evidence": [], "reason": "缺少真实企业字段。",
                }
            }
        ),
    )

    detail = result["条件匹配详情"][0]
    assert detail["evalStatus"] == "pass"
    assert detail["passSource"] == "default_missing_assumed"
    assert detail["企业值"] is None
    assert detail["得分"] == 10.0
    assert detail["条件分支适用性"] == "unknown"
    assert result["待确认条件项数"] == 0
    assert result["全部规则已确认"] is True
    assert result["是否推荐"] == 0


def test_model_fact_with_nonexistent_path_is_rejected():
    result = score_policy_against_rule_profile(
        {"原始数据": {}},
        {"title": "不存在字段测试"},
        _profile(_item("fake", "核心产品收入占比", "number", "gte", 60, "%")),
        _assessment(
            {
                "fake": {
                    "status": "supported", "mappingMethod": "direct",
                    "mappedFields": ["$normalized.高新技术产品收入占比"],
                    "normalizedValue": 0.8028, "normalizedUnit": "小数", "displayValue": "0.8028",
                    "confidence": 0.95,
                    "evidence": [{"sourcePath": "$normalized.高新技术产品收入占比", "sourceText": "0.8028"}],
                    "reason": "模型杜撰字段。",
                }
            }
        ),
    )

    detail = result["条件匹配详情"][0]
    assert detail["evalStatus"] == "pass"
    assert detail["模型事实状态"] == "missing"
    assert detail["passSource"] == "default_missing_assumed"
    assert detail["企业值"] is None
    assert detail["得分"] == 10.0
    assert result["是否推荐"] == 0


def test_empty_rule_table_cannot_receive_a_default_full_score():
    result = score_policy_against_rule_profile(
        {},
        {"title": "空规则表测试"},
        {
            "name": "空规则表",
            "schemaVersion": "1.2",
            "hardRuleGroups": [],
            "softRuleGroups": [],
            "specialPassRuleGroups": [],
        },
        None,
    )
    assert result["规则判定条件项总数"] == 0
    assert result["final_score"] == 0.0
    assert result["是否推荐"] == 0
    assert "没有任何可评分" in "；".join(result["风险提示"])


def test_tech_sme_evaluation_uses_local_bands_and_best_rd_path():
    result = _calculate_tech_sme_evaluation(
        {
            "techSmeEvaluation": {
                "techPersonnelRatio": {"value": 0.25},
                "rdExpenseRevenueRatio": {"value": 0.045},
                "rdExpenseCostRatio": {"value": 0.26},
                "classICount": {"value": 0},
                "classIICount": {"value": 3},
            }
        }
    )
    assert result["科技人员指标得分"] == 16
    assert result["研发投入指标得分"] == 40
    assert result["研发投入计分口径"] == "研发费用/成本费用"
    assert result["科技成果指标得分"] == 18
    assert result["评价总分"] == 74
    assert result["科技人员指标是否为0分"] is False


def test_tech_sme_total_score_uses_same_scale_in_soft_group():
    """评价总分口径（总分/100）在软性组必须与硬性组一致。

    覆盖逻辑原先只挂在硬性组循环里，而真实政策把该条件放在 softRuleGroups，
    于是 81 分被当成普通下限型指标走 1.5 倍成长性补强：
    min((81/60)*1.5, 1.0) = 1.0 -> 100 分，且任何 >=40 分的总分都被拉满，
    该条件完全丧失区分度，与"不使用 1.5 倍补强"的既定口径冲突。
    """
    enterprise = {
        "原始数据": {},
        "科技人员占企业职工总数比例": 0.2667,
        "I类知识产权数量": 35,
        "II类知识产权数量": 28,
        "_source_type": "policy_recommendation_api_record",
    }
    item = {
        "itemId": "5-1",
        "itemName": "中小企业评价指标",
        "itemStatus": "unresolved",
        "enterpriseFields": ["总得分"],
        "policyFragment": "综合评价得分不低于60分",
        "matchRule": {
            "ruleKind": "reference", "fieldType": "number",
            "operator": "gte", "targetValue": 60, "unit": "分",
        },
    }
    group = {"conditionNo": 1, "rawText": "评价", "conditionOperator": "AND", "conditionFieldItems": [item]}

    scores = {}
    for group_key in ("hardRuleGroups", "softRuleGroups"):
        profile = {
            "name": "评价口径一致性", "schemaVersion": "test",
            "hardRuleGroups": [], "softRuleGroups": [], "specialPassRuleGroups": [],
        }
        profile[group_key] = [group]
        result = score_policy_against_rule_profile(
            enterprise, {"title": "科技型中小企业评价"}, profile, semantic_assessment=None,
        )
        detail = next(x for x in result["条件匹配详情"] if x.get("itemId") == "5-1")
        scores[group_key] = (detail["得分"], detail["passSource"])

    # 科技人员 16 + 科技成果 30 + 研发投入缺失补足 5 = 51
    assert scores["hardRuleGroups"] == (51.0, "tech_sme_evaluation_partial_assumed")
    assert scores["softRuleGroups"] == (51.0, "tech_sme_evaluation_partial_assumed")


def test_tech_sme_partial_dimension_uses_ten_percent_fill():
    """单个维度缺失时按该维度满分的10%补足。"""
    result = _calculate_tech_sme_evaluation(
        {
            "techSmeEvaluation": {
                "techPersonnelRatio": {"value": 0.2667},
                "rdExpenseRevenueRatio": {"value": None},
                "rdExpenseCostRatio": {"value": None},
                "classICount": {"value": 35},
                "classIICount": {"value": 28},
            }
        }
    )
    assert result["科技人员指标得分"] == 16
    assert result["科技成果指标得分"] == 30
    assert result["研发投入指标得分"] is None
    assert result["已核验维度得分"] == 46
    assert result["缺失维度"] == ["研发投入"]
    # 研发投入满分50，按10%补足5分
    assert result["缺失维度补足分"] == 5.0
    assert result["评价总分"] == 51
    assert result["总分是否达到60分"] is False
    assert "研发投入维度数据缺失" in result["评价结论"]


def test_tech_sme_all_dimensions_missing_stays_none():
    """三项全缺失时保持 None，交由条件级缺失默认放行处理。"""
    result = _calculate_tech_sme_evaluation({"techSmeEvaluation": {}})

    assert result["评价总分"] is None
    assert result["已核验维度得分"] is None
    assert result["评价结论"] == "未核验"
    assert result["总分是否达到60分"] is None


def test_tech_sme_fully_verified_total_has_no_assumed_fill():
    """三项齐全时不产生补足分，总分等于实际相加。"""
    result = _calculate_tech_sme_evaluation(
        {
            "techSmeEvaluation": {
                "techPersonnelRatio": {"value": 0.25},
                "rdExpenseRevenueRatio": {"value": 0.045},
                "rdExpenseCostRatio": {"value": 0.26},
                "classICount": {"value": 0},
                "classIICount": {"value": 3},
            }
        }
    )
    assert result["缺失维度"] == []
    assert result["缺失维度补足分"] == 0
    assert result["评价总分"] == 74
    assert result["评价结论"] == "达到60分"


def test_tech_sme_partial_assumed_is_not_counted_as_evidence():
    """含补足分的评价条件不得计入证据覆盖率，否则补足分会被当成真实证据。"""
    from service.rule_engine_service import _is_evidence_backed

    assert _is_evidence_backed("tech_sme_evaluation_total_score") is True
    assert _is_evidence_backed("tech_sme_evaluation_partial_assumed") is False


def test_tech_sme_total_score_uses_actual_score_not_growth_multiplier():
    profile = _profile(
        _item("evaluation", "中小企业评价指标", "number", "gte", 60),
        _item("personnel", "科技人员指标得分", "number", "gte", 0),
    )
    result = score_policy_against_rule_profile(
        {},
        {"title": "科技型中小企业评价"},
        profile,
        {
            "enabled": True,
            "model": "test-model",
            "facts": {},
            "techSmeEvaluation": {
                "techPersonnelRatio": {"value": 0.16},
                "rdExpenseRevenueRatio": {"value": 0.06},
                "rdExpenseCostRatio": {"value": 0.08},
                "classICount": {"value": 0},
                "classIICount": {"value": 0},
            },
        },
    )
    details = {item["条件名称"]: item for item in result["条件匹配详情"]}
    assert result["科技型中小企业评价得分"]["评价总分"] == 58
    assert details["中小企业评价指标"]["得分"] == 58.0
    assert "总得分" not in details["中小企业评价指标"]["来源字段"]
    assert "科技人员占企业职工总数比例" in details["中小企业评价指标"]["来源字段"]
    assert "评价总分=58分" in details["中小企业评价指标"]["企业值"]
    assert result["模型语义事实"]["evaluation"]["mappedFields"] == []
    assert result["模型语义事实"]["evaluation"]["displayValue"] == "本地评价总分58分"
    assert details["科技人员指标得分"]["得分"] == 100.0
    assert result["hard_pass"] is True
    assert result["final_score"] == 79.0


def test_tech_sme_zero_personnel_score_remains_hard_failure():
    profile = _profile(_item("personnel", "科技人员指标得分", "number", "gte", 0))
    result = score_policy_against_rule_profile(
        {},
        {"title": "科技型中小企业评价"},
        profile,
        {
            "enabled": True,
            "model": "test-model",
            "facts": {},
            "techSmeEvaluation": {
                "techPersonnelRatio": {"value": 0.09},
                "rdExpenseRevenueRatio": {"value": 0.06},
                "rdExpenseCostRatio": {"value": 0.08},
                "classICount": {"value": 1},
                "classIICount": {"value": 0},
            },
        },
    )
    assert result["科技型中小企业评价得分"]["科技人员指标得分"] == 0
    assert result["hard_pass"] is False
    assert result["final_score"] == 0.0
