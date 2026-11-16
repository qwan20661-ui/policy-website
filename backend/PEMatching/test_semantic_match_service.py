"""模型语义事实服务的 JSON 与字段路径兼容性测试。"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from unittest.mock import patch

import pytest
import requests

from service.matching_service import _deduplicate_policies, _match_single_task
from service.rule_engine_service import score_policy_against_rule_profile
from service.semantic_match_service import (
    SemanticMatchRetryableError,
    _build_prompt,
    _flatten_json,
    _model_input_candidates,
    _policy_items,
    _post_semantic_request,
    _sanitize_json,
    _validate_assessment,
    assess_enterprise_policy,
)


def test_model_candidates_keep_all_mapped_fields_before_array_compaction():
    enterprise = {
        "原始数据": {
            "mainTechnologyList": [
                {"patentName": f"专利{index}", "brief": "专利说明", "pctPublishNum": f"CN{index}"}
                for index in range(100)
            ],
            "honorQualificationList": [{"isHighTechEnterprise": "1"}],
            "operationInfoList": [{"year": "2025", "operatingIncome": 11560299.64, "techContractAmount": 0}],
            "接口未定义字段": "不得进入模型候选",
        },
        "销售收入(万元)": 1156.029964,
    }

    candidates = _model_input_candidates(enterprise)

    assert len(candidates) == 94
    assert candidates["$.operationInfoList[0].operatingIncome"] == 11560299.64
    assert candidates["$.operationInfoList[0].techContractAmount"] == 0
    assert candidates["$.honorQualificationList[0].isHighTechEnterprise"] == "1"
    assert "$normalized.销售收入(万元)" not in candidates
    assert "$[接口未定义字段]" not in candidates


def test_declared_money_unit_is_used_for_semantic_prompt_item():
    profile = {
        "hardRuleGroups": [{
            "conditionFieldItems": [{
                "itemId": "revenue",
                "itemName": "营收规模",
                "matchRule": {"fieldType": "number", "operator": "gt", "targetValue": 1000, "unit": "万元"},
            }],
        }],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }

    assert _policy_items(profile)[0]["expectedUnit"] == "万元"


def test_duplicate_fusion_policy_id_is_removed_before_matching():
    policies = _deduplicate_policies([
        {"id": "6569", "title": "集美区国家级高新技术企业、科技进步奖扶持"},
        {"id": "6569", "title": "集美区国家级高新技术企业、科技进步奖扶持"},
        {"id": "6548", "title": "厦门市科技型中小企业评价"},
    ])

    assert [policy["id"] for policy in policies] == ["6569", "6548"]


def test_flatten_json_path_with_chinese_punctuation_has_no_embedded_quotes():
    entries = list(_flatten_json({"指标1：注册地": "符合"}))
    assert entries == [{"sourcePath": "$[指标1：注册地]", "sourceValue": "符合"}]


def test_invalid_model_json_exposes_compact_diagnostic_excerpt():
    with pytest.raises(ValueError, match="模型返回的 JSON 格式无效") as error:
        _sanitize_json('{"facts": [{"itemId": "1" "status": "supported"}]}')
    assert "附近内容" in str(error.value)


def test_policy_items_include_group_and_sibling_branch_context():
    profile = {
        "hardRuleGroups": [],
        "softRuleGroups": [
            {
                "conditionNo": 1,
                "rawText": "金额低于100万元支付50%，达到100万元支付30%",
                "conditionOperator": "ANY",
                "conditionFieldItems": [
                    {
                        "itemId": "1-1",
                        "itemName": "低金额分支",
                        "policyFragment": "低于100万元支付50%",
                        "matchRule": {"fieldType": "number", "operator": "lt", "targetValue": 100, "unit": "万元"},
                    },
                    {
                        "itemId": "1-2",
                        "itemName": "高金额分支",
                        "policyFragment": "达到100万元支付30%",
                        "matchRule": {"fieldType": "number", "operator": "gte", "targetValue": 100, "unit": "万元"},
                    },
                ],
            }
        ],
        "specialPassRuleGroups": [],
    }

    items = _policy_items(profile)
    assert items[0]["groupRawText"] == "金额低于100万元支付50%，达到100万元支付30%"
    assert items[0]["conditionOperator"] == "ANY"
    assert len(items[0]["siblingBranches"]) == 2
    assert "互斥分支" in items[0]["branchExplanation"]
    prompt = _build_prompt({"企业名称": "企业A", "原始数据": {}}, {"title": "分支政策"}, profile)
    assert "siblingBranches" in prompt
    assert "低于100万元支付50%" in prompt


def test_validate_assessment_preserves_branch_applicability_and_reason():
    result = _validate_assessment(
        {
            "facts": [
                {
                    "itemId": "1-1",
                    "semanticItemKey": "test:hard:1:1",
                    "status": "supported",
                    "mappingMethod": "direct",
                    "mappedFields": ["$.amount"],
                    "normalizedValue": 120,
                    "normalizedUnit": "万元",
                    "displayValue": "120万元",
                    "confidence": 0.98,
                    "branchApplicability": "not_applicable",
                    "branchReason": "企业金额已达到100万元，不适用低金额分支。",
                    "evidence": [{"sourcePath": "$.amount"}],
                    "reason": "互斥分支判定。",
                }
            ]
        },
        [{"semanticItemKey": "test:hard:1:1", "itemId": "1-1"}],
        {"$.amount": "120"},
    )

    fact = result["facts"]["test:hard:1:1"]
    assert fact["branchApplicability"] == "not_applicable"
    assert fact["branchReason"] == "企业金额已达到100万元，不适用低金额分支。"


def test_supported_fact_is_downgraded_when_explanation_admits_requested_year_is_missing():
    result = _validate_assessment(
        {
            "facts": [
                {
                    "itemId": "rd-2023",
                    "semanticItemKey": "test:hard:1:1",
                    "status": "supported",
                    "mappingMethod": "direct",
                    "mappedFields": ["$.operationInfoList[0].rdExpense"],
                    "normalizedValue": 3024.7,
                    "normalizedUnit": "万元",
                    "displayValue": "2025年研发费用3024.7万元",
                    "confidence": 0.9,
                    "branchApplicability": "applicable",
                    "branchReason": "企业接口没有2023年研发费用数据，因此无法判断是否满足指定年度要求。",
                    "evidence": [
                        {"sourcePath": "$.operationInfoList[0].rdExpense", "sourceText": "3024.7"}
                    ],
                    "reason": "仅找到2025年度数据。",
                }
            ]
        },
        [{"semanticItemKey": "test:hard:1:1", "itemId": "rd-2023"}],
    )

    fact = result["facts"]["test:hard:1:1"]
    assert fact["status"] == "missing"
    assert fact["branchApplicability"] == "unknown"
    assert fact["mappingMethod"] == "none"
    assert fact["mappedFields"] == []
    assert fact["normalizedValue"] is None
    assert fact["displayValue"] == ""
    assert fact["evidence"] == []


def test_requested_year_missing_keeps_enterprise_value_null_and_scores_ten():
    assessment = _validate_assessment(
        {
            "facts": [
                {
                    "itemId": "rd-2023",
                    "semanticItemKey": "test:hard:1:1",
                    "status": "supported",
                    "mappingMethod": "direct",
                    "mappedFields": ["$.operationInfoList[1].rdExpense"],
                    "normalizedValue": 3024.7,
                    "normalizedUnit": "万元",
                    "displayValue": "2025年研发费用3024.7万元",
                    "confidence": 0.9,
                    "branchApplicability": "applicable",
                    "branchReason": "没有2023年数据，只有2024、2025年度数据，无法判断。",
                    "evidence": [
                        {"sourcePath": "$.operationInfoList[1].rdExpense", "sourceText": "3024.7"}
                    ],
                    "reason": "使用了2025年度数据。",
                }
            ]
        },
        [{"semanticItemKey": "test:hard:1:1", "itemId": "rd-2023"}],
    )
    profile = {
        "name": "指定年度研发费用测试",
        "schemaVersion": "test",
        "hardRuleGroups": [
            {
                "conditionNo": 1,
                "rawText": "2023年度自主研发费用超过1000万元",
                "conditionOperator": "AND",
                "conditionFieldItems": [
                    {
                        "itemId": "rd-2023",
                        "itemName": "2023年度自主研发费用",
                        "itemStatus": "rule",
                        "enterpriseFields": ["研发费用总额(万元)"],
                        "policyFragment": "2023年度自主研发费用超过1000万元",
                        "matchRule": {
                            "ruleId": "rd-2023",
                            "ruleKind": "direct",
                            "fieldType": "number",
                            "operator": "gt",
                            "targetValue": 1000,
                            "unit": "万元",
                        },
                    }
                ],
            }
        ],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }
    result = score_policy_against_rule_profile(
        {
            "原始数据": {
                "operationInfoList": [
                    {"year": "2024", "rdExpense": 2024.7},
                    {"year": "2025", "rdExpense": 3024.7},
                ]
            }
        },
        {"title": "厦门市企业创新中心授牌申报"},
        profile,
        assessment,
    )

    detail = result["条件匹配详情"][0]
    assert detail["企业值"] is None
    assert detail["evalStatus"] == "pass"
    assert detail["passSource"] == "default_missing_assumed"
    assert detail["得分"] == 10.0
    assert result["默认假定条件项数"] == 1
    assert result["是否推荐"] == 0


def test_semantic_timeout_retries_then_raises_retryable_error():
    with (
        patch("service.semantic_match_service.SEMANTIC_LLM_RETRIES", 2),
        patch("service.semantic_match_service.SEMANTIC_LLM_RETRY_BACKOFF", 0),
        patch("service.semantic_match_service.requests.post", side_effect=requests.Timeout("读取超时")) as mocked_post,
    ):
        with pytest.raises(SemanticMatchRetryableError) as error:
            _post_semantic_request({}, {"model": "test"})

    assert mocked_post.call_count == 3
    assert error.value.error_type == "semantic_model_timeout"
    assert error.value.attempts == 3


def test_assess_enterprise_policy_calls_llm_once_per_condition_and_merges_facts():
    profile = {
        "hardRuleGroups": [
            {
                "conditionNo": 1,
                "conditionFieldItems": [
                    {
                        "itemId": "1-1",
                        "itemName": "注册地",
                        "policyFragment": "企业注册地在厦门市",
                        "matchRule": {"fieldType": "text", "operator": "llm_judge"},
                    },
                    {
                        "itemId": "1-2",
                        "itemName": "成立年限",
                        "policyFragment": "企业成立满三年",
                        "matchRule": {"fieldType": "number", "operator": "gte", "targetValue": 3},
                    },
                ],
            }
        ],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }
    captured_payloads = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(self.payload)}}]}

    def fake_post(*_args, **kwargs):
        captured_payloads.append(kwargs["json"])
        item = json.loads(kwargs["json"]["messages"][1]["content"])["政策"]["规则项"][0]
        return Response({"facts": [{"semanticItemKey": item["semanticItemKey"], "itemId": item["itemId"], "status": "supported", "confidence": 1}]})

    progress_messages = []
    with (
        patch("service.semantic_match_service.SEMANTIC_LLM_ENABLED", True),
        patch("service.semantic_match_service.SEMANTIC_LLM_API_URL", "http://llm.test/v1/chat/completions"),
        patch("service.semantic_match_service.SEMANTIC_LLM_MODEL_NAME", "test-model"),
        patch("service.semantic_match_service.requests.post", side_effect=fake_post),
    ):
        assessment = assess_enterprise_policy(
            {"企业名称": "企业A", "原始数据": {"注册地址": "厦门市", "成立年限": 4}},
            {"title": "测试政策"},
            profile,
            progress_messages.append,
        )

    assert len(captured_payloads) == 2
    assert sorted(
        json.loads(payload["messages"][1]["content"])["政策"]["规则项"][0]["itemId"]
        for payload in captured_payloads
    ) == ["1-1", "1-2"]
    assert len(assessment["facts"]) == 2
    assert {fact["itemId"] for fact in assessment["facts"].values()} == {"1-1", "1-2"}
    assert len([message for message in progress_messages if message.startswith("模型条件批次完成")]) == 2


def test_policy_conditions_run_concurrently_in_shared_semantic_queue():
    profile = {
        "hardRuleGroups": [
            {
                "conditionNo": 1,
                "conditionFieldItems": [
                    {"itemId": "1-1", "itemName": "条件一", "matchRule": {"fieldType": "text"}},
                    {"itemId": "1-2", "itemName": "条件二", "matchRule": {"fieldType": "text"}},
                ],
            }
        ],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }
    active_requests = 0
    peak_requests = 0
    request_lock = Lock()

    class Response:
        def __init__(self, item):
            self.item = item

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({"facts": [{"semanticItemKey": self.item["semanticItemKey"], "itemId": self.item["itemId"], "status": "supported"}]})}}]}

    def fake_post(*_args, **kwargs):
        nonlocal active_requests, peak_requests
        item = json.loads(kwargs["json"]["messages"][1]["content"])["政策"]["规则项"][0]
        with request_lock:
            active_requests += 1
            peak_requests = max(peak_requests, active_requests)
        time.sleep(0.05)
        with request_lock:
            active_requests -= 1
        return Response(item)

    with ThreadPoolExecutor(max_workers=2) as executor:
        with (
            patch("service.semantic_match_service.SEMANTIC_LLM_ENABLED", True),
            patch("service.semantic_match_service.SEMANTIC_LLM_API_URL", "http://llm.test/v1/chat/completions"),
            patch("service.semantic_match_service.SEMANTIC_LLM_MODEL_NAME", "test-model"),
            patch("service.semantic_match_service._semantic_executor", return_value=executor),
            patch("service.semantic_match_service.requests.post", side_effect=fake_post),
        ):
            assessment = assess_enterprise_policy({"企业名称": "企业A"}, {"title": "测试政策"}, profile)

    assert peak_requests == 2
    assert {fact["itemId"] for fact in assessment["facts"].values()} == {"1-1", "1-2"}


def test_matching_task_marks_exhausted_timeout_as_retryable_failure():
    retryable_error = SemanticMatchRetryableError(
        "语义模型调用失败，已尝试 3 次：读取超时",
        error_type="semantic_model_timeout",
        attempts=3,
    )
    with (
        patch("service.matching_service.resolve_rule_profile", return_value={"hardRuleGroups": []}),
        patch("service.matching_service.assess_enterprise_policy", side_effect=retryable_error),
    ):
        result = _match_single_task(({}, {"title": "超时测试政策"}, "企业A"))

    assert result["匹配状态"] == "匹配失败待重试"
    assert result["错误类型"] == "semantic_model_timeout"
    assert result["可重试"] is True
    assert result["重试次数"] == 2
    assert result["是否推荐"] is None


def _batch_profile(item_count: int, repeated_item_id: bool = False) -> dict:
    return {
        "hardRuleGroups": [{
            "conditionNo": 1,
            "conditionFieldItems": [
                {
                    "itemId": "duplicate" if repeated_item_id else f"item-{index}",
                    "itemName": f"条件{index}",
                    "policyFragment": f"条件{index}原文",
                    "matchRule": {"fieldType": "text", "operator": "llm_judge"},
                }
                for index in range(1, item_count + 1)
            ],
        }],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }


class _JsonResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": json.dumps(self.payload)}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}


def test_compact_prompt_aggregates_array_and_omits_metadata():
    enterprise = {
        "企业名称": "企业A",
        "原始数据": {
            "enterpriseName": "企业A",
            "operationInfoList": [{"employeeCount": 10, "rdPersonnelRatio": "0.2"}],
            "mainTechnologyList": [
                {"id": "patent-1", "patentName": "发明专利A", "brief": "专利摘要A"},
                {"id": "patent-2", "patentName": "发明专利B", "brief": "专利摘要B"},
            ],
            "未定义字段": "不应传给模型",
        },
    }
    profile = {
        "hardRuleGroups": [{
            "conditionFieldItems": [{
                "itemId": "patent", "itemName": "发明专利", "policyFragment": "企业拥有发明专利。",
                "matchRule": {"fieldType": "text", "operator": "llm_judge"},
            }],
        }],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }
    payload = json.loads(_build_prompt(enterprise, {"id": "p1", "title": "政策"}, profile))

    candidates = {entry["字段名称"]: entry for entry in payload["企业字段候选"]}
    assert candidates == {
        "经营信息列表": {
            "字段名称": "经营信息列表",
            "字段值": "记录1：员工总人数=10；研发人员占比=0.2",
        },
        "主要技术列表": {
            "字段名称": "主要技术列表",
            "字段值": "记录1：专利名称=发明专利A；专利摘要=专利摘要A | 记录2：专利名称=发明专利B；专利摘要=专利摘要B",
        },
    }
    assert all(set(entry) == {"字段名称", "字段值"} for entry in payload["企业字段候选"])
    assert payload["政策"]["规则项"][0]["semanticItemKey"] == "p1:hardRuleGroups:1:1"
    assert "\n" not in _build_prompt(enterprise, {"id": "p1", "title": "政策"}, profile)


def test_prompt_uses_chinese_name_for_policy_recommendation_field():
    enterprise = {
        "企业名称": "企业A",
        "原始数据": {"operationInfoList": [{"rdPersonnelRatio": "0.2667"}]},
    }
    payload = json.loads(_build_prompt(enterprise, {"id": "p1", "title": "政策"}, _batch_profile(1)))

    candidate = next(entry for entry in payload["企业字段候选"] if entry["字段名称"] == "经营信息列表")
    assert candidate == {
        "字段名称": "经营信息列表",
        "字段值": "记录1：研发人员占比=0.2667",
    }


def test_academic_requirement_keeps_enterprise_data_but_does_not_label_rd_as_academic():
    enterprise = {
        "企业名称": "企业A",
        "原始数据": {"operationInfoList": [{"employeeCount": 10, "rdPersonnelRatio": "0.2"}]},
    }
    profile = {
        "hardRuleGroups": [{
            "conditionFieldItems": [{
                "itemId": "education", "itemName": "大专以上学历员工占比",
                "policyFragment": "具有大专以上学历的员工占企业职工总数的50%以上。",
                "matchRule": {"fieldType": "text", "operator": "llm_judge"},
            }],
        }],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }

    payload = json.loads(_build_prompt(enterprise, {"id": "p1", "title": "政策"}, profile))

    assert payload["企业字段候选"] == [{
        "字段名称": "经营信息列表",
        "字段值": "记录1：员工总人数=10；研发人员占比=0.2",
    }]


def test_duplicate_item_id_has_independent_semantic_keys_and_facts():
    profile = _batch_profile(2, repeated_item_id=True)
    items = _policy_items(profile, {"id": "p1"})
    result = _validate_assessment(
        {"facts": [
            {"semanticItemKey": items[0]["semanticItemKey"], "itemId": "duplicate", "status": "supported"},
            {"semanticItemKey": items[1]["semanticItemKey"], "itemId": "duplicate", "status": "contradicted"},
        ]},
        items,
    )

    assert items[0]["semanticItemKey"] != items[1]["semanticItemKey"]
    assert len(result["facts"]) == 2
    assert {fact["status"] for fact in result["facts"].values()} == {"supported", "contradicted"}


def test_micro_batch_returns_all_facts_in_one_request():
    captured = []

    def fake_post(*_args, **kwargs):
        captured.append(kwargs["json"])
        items = json.loads(kwargs["json"]["messages"][1]["content"])["政策"]["规则项"]
        return _JsonResponse({"facts": [
            {"semanticItemKey": item["semanticItemKey"], "itemId": item["itemId"], "status": "supported"}
            for item in items
        ]})

    with (
        patch("service.semantic_match_service.SEMANTIC_LLM_ENABLED", True),
        patch("service.semantic_match_service.SEMANTIC_LLM_API_URL", "http://llm.test/v1/chat/completions"),
        patch("service.semantic_match_service.SEMANTIC_LLM_MODEL_NAME", "test-model"),
        patch("service.semantic_match_service.SEMANTIC_LLM_RETRIES", 0),
        patch("service.semantic_match_service.SEMANTIC_MATCH_BATCH_SIZE", 10),
        patch("service.semantic_match_service.requests.post", side_effect=fake_post),
    ):
        assessment = assess_enterprise_policy({"企业名称": "企业A", "原始数据": {"地址": "厦门"}}, {"id": "p1", "title": "政策"}, _batch_profile(2))

    assert len(captured) == 1
    assert len(assessment["facts"]) == 2
    assert assessment["totalRequestCount"] == 1


def test_micro_batch_omission_only_retries_the_missing_item():
    request_sizes = []

    def fake_post(*_args, **kwargs):
        items = json.loads(kwargs["json"]["messages"][1]["content"])["政策"]["规则项"]
        request_sizes.append(len(items))
        emitted = items[:1] if len(items) == 2 else items
        return _JsonResponse({"facts": [
            {"semanticItemKey": item["semanticItemKey"], "itemId": item["itemId"], "status": "missing"}
            for item in emitted
        ]})

    with (
        patch("service.semantic_match_service.SEMANTIC_LLM_ENABLED", True),
        patch("service.semantic_match_service.SEMANTIC_LLM_API_URL", "http://llm.test/v1/chat/completions"),
        patch("service.semantic_match_service.SEMANTIC_LLM_MODEL_NAME", "test-model"),
        patch("service.semantic_match_service.SEMANTIC_LLM_RETRIES", 0),
        patch("service.semantic_match_service.SEMANTIC_MATCH_BATCH_SIZE", 10),
        patch("service.semantic_match_service.requests.post", side_effect=fake_post),
    ):
        assessment = assess_enterprise_policy({"企业名称": "企业A"}, {"id": "p1", "title": "政策"}, _batch_profile(2))

    assert request_sizes == [2, 1]
    assert assessment["fallbackCount"] == 1
    assert len(assessment["facts"]) == 2


def test_invalid_micro_batch_json_is_split_to_single_items():
    request_sizes = []

    class InvalidResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "not-json"}}]}

    def fake_post(*_args, **kwargs):
        items = json.loads(kwargs["json"]["messages"][1]["content"])["政策"]["规则项"]
        request_sizes.append(len(items))
        if len(items) == 2:
            return InvalidResponse()
        item = items[0]
        return _JsonResponse({"facts": [{"semanticItemKey": item["semanticItemKey"], "itemId": item["itemId"], "status": "supported"}]})

    with (
        patch("service.semantic_match_service.SEMANTIC_LLM_ENABLED", True),
        patch("service.semantic_match_service.SEMANTIC_LLM_API_URL", "http://llm.test/v1/chat/completions"),
        patch("service.semantic_match_service.SEMANTIC_LLM_MODEL_NAME", "test-model"),
        patch("service.semantic_match_service.SEMANTIC_LLM_RETRIES", 0),
        patch("service.semantic_match_service.SEMANTIC_MATCH_BATCH_SIZE", 10),
        patch("service.semantic_match_service.requests.post", side_effect=fake_post),
    ):
        assessment = assess_enterprise_policy({"企业名称": "企业A"}, {"id": "p1", "title": "政策"}, _batch_profile(2))

    assert request_sizes == [2, 1, 1]
    assert assessment["fallbackCount"] == 2
    assert len(assessment["facts"]) == 2
