from __future__ import annotations

from copy import deepcopy
import json
from unittest.mock import patch

from service.policy_association_service import (
    DIMENSION_WEIGHTS,
    assess_policy_association,
    build_enterprise_association_profile,
    build_policy_association_profile,
    validate_and_score_association,
)
from service.matching_service import run_matching


def _profiles() -> tuple[dict, dict]:
    enterprise = {
        "一级行业": "软件和信息技术服务业",
        "企业规模": "小型",
        "注册日期": "2020-01-01",
        "研发费用总额(万元)": 120,
        "职工总数": 80,
        "注册地": "厦门市思明区",
        "博士人数": 2,
    }
    policy = {
        "policyId": "6617",
        "title": "软件企业研发与人才扶持政策",
        "industryField": "软件和信息技术服务业",
        "applicableScope": "厦门市企业",
        "applicationRequirements": ["支持开展研发活动的软件企业"],
        "supportMethods": ["研发费用补助"],
        "policyTypes": ["科技创新类"],
    }
    return enterprise, policy


def _dimension(
    name: str,
    status: str,
    enterprise_field: str = "一级行业",
    enterprise_value: object = "软件和信息技术服务业",
    policy_field: str = "industryField",
    policy_text: object = "软件和信息技术服务业",
) -> dict:
    return {
        "dimension": name,
        "status": status,
        "enterpriseEvidence": [{"field": enterprise_field, "value": enterprise_value}],
        "policyEvidence": [{"field": policy_field, "text": policy_text}],
        "reason": f"{name} 关联理由",
    }


def test_association_profile_uses_existing_input_without_mutation_or_sensitive_fields() -> None:
    enterprise = {
        "一级行业": "软件和信息技术服务业",
        "职工总数": 80,
        "统一社会信用代码": "913502000000000000",
        "联系电话": "13800000000",
        "原始数据": {
            "registerType": "有限责任公司",
            "nationalHighTechZone": "厦门火炬高新区",
            "telephone": "13800000000",
        },
    }
    before = deepcopy(enterprise)

    profile = build_enterprise_association_profile(enterprise)

    assert enterprise == before
    assert profile["一级行业"] == "软件和信息技术服务业"
    assert profile["注册类型"] == "有限责任公司"
    assert profile["所属国家高新区"] == "厦门火炬高新区"
    assert "统一社会信用代码" not in profile
    assert "联系电话" not in profile
    assert "telephone" not in profile


def test_policy_profile_is_bounded_and_does_not_include_application_materials() -> None:
    policy = {
        "id": "6617",
        "title": "软件企业政策",
        "materials": [{"materials": "营业执照复印件"}],
        "raw_detail": {
            "policyInfo": {"summary": "支持软件企业", "industryField": "软件业"},
            "documents": [{"policyTitle": "科技创新类", "content": "不应在摘要充分时发送正文"}],
            "conditions": [{"applicableTarget": "软件企业", "applicationRequirement": "在厦注册"}],
            "supportMethods": [{"contentDesc": "资金补助"}],
        },
    }
    before = deepcopy(policy)

    profile = build_policy_association_profile(policy)

    assert policy == before
    assert profile["policyId"] == "6617"
    assert profile["applicableTargets"] == ["软件企业"]
    assert profile["applicationRequirements"] == ["在厦注册"]
    assert profile["policyTypes"] == ["科技创新类"]
    assert profile["documentExcerpt"] == []
    assert "营业执照复印件" not in str(profile)


def test_sparse_policy_profile_only_uses_sanitized_document_excerpt() -> None:
    policy = {
        "id": "6617",
        "title": "稀疏政策",
        "association_document_excerpt": "已清洗且受限的正文摘录",
        "raw_detail": {
            "policyInfo": {},
            "documents": [{"policyTitle": "申报通知", "content": "不得直接发送的完整附件正文"}],
        },
    }

    profile = build_policy_association_profile(policy)

    assert profile["documentExcerpt"] == ["已清洗且受限的正文摘录"]
    assert "不得直接发送的完整附件正文" not in str(profile)


def test_local_association_scoring_uses_fixed_weights_and_keeps_scores_independent() -> None:
    enterprise, policy = _profiles()
    payload = {
        "policyId": "6617",
        "dimensions": [
            _dimension("industry", "strong"),
            _dimension("scale", "medium", "企业规模", "小型", "title", "软件企业研发与人才扶持政策"),
            _dimension("lifecycle_qualification", "weak", "注册日期", "2020-01-01", "title", "软件企业研发与人才扶持政策"),
            _dimension("research_development", "strong", "研发费用总额(万元)", 120, "supportMethods", "研发费用补助"),
            _dimension("employment", "unknown"),
            _dimension("region_park", "strong", "注册地", "厦门市思明区", "applicableScope", "厦门市企业"),
            _dimension("education_talent", "not_applicable"),
        ],
    }

    result = validate_and_score_association(payload, enterprise, policy)

    assert sum(DIMENSION_WEIGHTS.values()) == 100
    assert result["score"] == 73.89
    assert result["level"] == "medium"
    assert result["isAssociated"] is True
    assert result["status"] == "matched"
    assert len(result["reasons"]) == 3
    assert result["calculation"] == {
        "formula": "Σ(维度权重×状态系数)÷有效维度权重合计×100",
        "expression": "66.5÷90.0×100=73.89",
        "weightedContribution": 66.5,
        "effectiveWeight": 90.0,
        "score": 73.89,
    }
    dimensions = {item["dimension"]: item for item in result["dimensions"]}
    assert dimensions["region_park"]["contribution"] == 15.0
    assert dimensions["region_park"]["normalizedContribution"] == 16.67
    assert dimensions["region_park"]["includedInDenominator"] is True
    assert dimensions["education_talent"]["normalizedContribution"] is None
    assert dimensions["education_talent"]["includedInDenominator"] is False


def test_association_score_31_returns_reproducible_formula_and_all_dimensions() -> None:
    enterprise, policy = _profiles()
    enterprise["职工总数"] = 50
    payload = {
        "policyId": "6617",
        "dimensions": [
            _dimension("industry", "unknown"),
            _dimension("scale", "weak", "企业规模", "小型", "title", "软件企业研发与人才扶持政策"),
            _dimension("lifecycle_qualification", "weak", "注册日期", "2020-01-01", "title", "软件企业研发与人才扶持政策"),
            _dimension("research_development", "unknown"),
            _dimension("employment", "weak", "职工总数", 50, "title", "软件企业研发与人才扶持政策"),
            _dimension("region_park", "strong", "注册地", "厦门市思明区", "applicableScope", "厦门市企业"),
            _dimension("education_talent", "unknown"),
        ],
    }

    result = validate_and_score_association(payload, enterprise, policy)
    dimensions = {item["dimension"]: item for item in result["dimensions"]}

    assert result["score"] == 31.0
    assert result["calculation"]["weightedContribution"] == 31.0
    assert result["calculation"]["effectiveWeight"] == 100.0
    assert result["calculation"]["expression"] == "31.0÷100.0×100=31.0"
    assert dimensions["region_park"]["normalizedContribution"] == 15.0
    assert dimensions["scale"]["normalizedContribution"] == 6.0
    assert dimensions["lifecycle_qualification"]["normalizedContribution"] == 6.0
    assert dimensions["employment"]["normalizedContribution"] == 4.0
    assert len(result["dimensions"]) == 7
    assert len(result["reasons"]) == 3


def test_invalid_or_one_sided_evidence_is_downgraded_to_unknown() -> None:
    enterprise, policy = _profiles()
    payload = {
        "policyId": "6617",
        "dimensions": [
            _dimension("industry", "strong", "不存在的企业字段", "软件企业"),
            _dimension("industry", "strong"),
            _dimension("scale", "illegal"),
        ],
    }

    result = validate_and_score_association(payload, enterprise, policy)
    by_name = {item["dimension"]: item for item in result["dimensions"]}

    assert by_name["industry"]["status"] == "unknown"
    assert by_name["scale"]["status"] == "unknown"
    assert result["score"] is None
    assert result["level"] == "unknown"
    assert any(item["reason"] == "重复维度" for item in result["rejectedEvidence"])
    assert any(item["reason"] == "非法状态" for item in result["rejectedEvidence"])


def test_region_evidence_cannot_score_scale_dimension() -> None:
    enterprise, policy = _profiles()
    payload = {
        "policyId": "6617",
        "dimensions": [
            _dimension("scale", "weak", "注册地", "厦门市思明区", "applicableScope", "厦门市企业"),
            _dimension("region_park", "strong", "注册地", "厦门市思明区", "applicableScope", "厦门市企业"),
        ],
    }

    result = validate_and_score_association(payload, enterprise, policy)
    dimensions = {item["dimension"]: item for item in result["dimensions"]}

    assert dimensions["scale"]["status"] == "unknown"
    assert dimensions["scale"]["contribution"] == 0.0
    assert dimensions["region_park"]["status"] == "strong"
    assert dimensions["region_park"]["contribution"] == 15.0
    assert {"dimension": "scale", "reason": "字段不属于当前维度：注册地"} in result["rejectedEvidence"]
    assert {"dimension": "scale", "reason": "字段不属于当前维度：applicableScope"} in result["rejectedEvidence"]


def test_association_is_disabled_without_changing_existing_input() -> None:
    enterprise = {"企业名称": "测试企业", "一级行业": "软件业"}
    policy = {"id": "6617", "title": "软件政策"}
    enterprise_before = deepcopy(enterprise)
    policy_before = deepcopy(policy)

    with patch("service.policy_association_service.ASSOCIATION_LLM_ENABLED", False):
        result = assess_policy_association(enterprise, policy)

    assert result["status"] == "disabled"
    assert result["score"] is None
    assert result["calculation"]["formula"] == "Σ(维度权重×状态系数)÷有效维度权重合计×100"
    assert result["calculation"]["effectiveWeight"] is None
    assert enterprise == enterprise_before
    assert policy == policy_before


def test_enabled_association_calls_model_and_scores_validated_evidence() -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            dimensions = [
                _dimension("industry", "strong", "一级行业", "软件和信息技术服务业", "industryField", "软件和信息技术服务业")
            ]
            dimensions.extend(
                {
                    "dimension": name,
                    "status": "not_applicable",
                    "enterpriseEvidence": [],
                    "policyEvidence": [],
                    "reason": "本维度与政策无关",
                }
                for name in DIMENSION_WEIGHTS
                if name != "industry"
            )
            return {
                "choices": [{"message": {"content": json.dumps({"policyId": "6617", "dimensions": dimensions}, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            }

    enterprise = {"一级行业": "软件和信息技术服务业"}
    policy = {
        "id": "6617",
        "title": "软件企业扶持政策",
        "policy_info": {"industryField": "软件和信息技术服务业"},
    }

    with (
        patch("service.policy_association_service.ASSOCIATION_LLM_ENABLED", True),
        patch("service.policy_association_service.ASSOCIATION_LLM_API_URL", "http://llm.test/v1/chat/completions"),
        patch("service.policy_association_service.ASSOCIATION_LLM_MODEL_NAME", "test-model"),
        patch("service.policy_association_service.requests.post", return_value=Response()) as mocked_post,
    ):
        result = assess_policy_association(enterprise, policy)

    assert result["status"] == "matched"
    assert result["score"] == 100.0
    assert result["level"] == "strong"
    assert result["isAssociated"] is True
    assert result["metrics"]["totalTokens"] == 150
    assert result["modelInput"]["policyId"] == "6617"
    sent_request = mocked_post.call_args.kwargs
    assert sent_request["timeout"] > 0
    assert sent_request["json"]["temperature"] == 0.0
    assert sent_request["json"]["response_format"] == {"type": "json_object"}


def test_run_matching_attaches_second_score_without_changing_original_order() -> None:
    enterprise = {"企业名称": "测试企业", "一级行业": "软件业"}
    policies = [
        {"id": "p-high", "title": "原分高政策"},
        {"id": "p-low", "title": "关联分高政策"},
    ]

    def original_match(args: tuple, _progress_callback=None) -> dict:
        _enterprise, policy, _enterprise_name = args
        score = 90 if policy["id"] == "p-high" else 30
        return {
            "政策标题": policy["title"],
            "fusionPolicyId": policy["id"],
            "final_score": score,
            "证据覆盖率": 1.0,
            "是否推荐": 1 if score >= 60 else 0,
        }

    def association_match(_enterprise: dict, policy: dict, _progress_callback=None) -> dict:
        score = 20 if policy["id"] == "p-high" else 95
        return {
            "score": score,
            "level": "strong" if score >= 80 else "none",
            "isAssociated": score >= 60,
            "status": "matched",
            "reasons": [policy["title"]],
        }

    with (
        patch("service.matching_service._match_single_task", side_effect=original_match),
        patch("service.matching_service.assess_policy_association", side_effect=association_match),
    ):
        result = run_matching([enterprise], policies, max_workers=2)

    policy_results = result[0]["政策匹配结果"]
    assert [item["fusionPolicyId"] for item in policy_results] == ["p-high", "p-low"]
    assert [item["final_score"] for item in policy_results] == [90, 30]
    assert [item["associationMatch"]["score"] for item in policy_results] == [20, 95]
    assert [item["是否推荐"] for item in policy_results] == [1, 0]


def test_run_matching_isolates_association_failure_from_original_result() -> None:
    enterprise = {"企业名称": "测试企业"}
    policy = {"id": "p-1", "title": "测试政策"}
    original = {
        "政策标题": "测试政策",
        "fusionPolicyId": "p-1",
        "final_score": 88,
        "证据覆盖率": 0.8,
        "是否推荐": 1,
    }

    with (
        patch("service.matching_service._match_single_task", return_value=deepcopy(original)),
        patch("service.matching_service.assess_policy_association", side_effect=RuntimeError("关联服务异常")),
    ):
        result = run_matching([enterprise], [policy], max_workers=1)

    policy_result = result[0]["政策匹配结果"][0]
    assert {key: policy_result[key] for key in original} == original
    assert policy_result["associationMatch"]["status"] == "待重试"
    assert policy_result["associationMatch"]["errorType"] == "association_internal_error"
