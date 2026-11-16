"""单企业政策匹配 HTTP 接口回归测试。"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from unittest.mock import patch

from api_server import (
    ApiHandler,
    _RequestEventLog,
    _build_enterprise_recommendation_item,
    _build_condition_scores,
    _build_matched_conditions,
    _log_api_event,
    _match_score_level,
    _persist_enterprise_match_result,
    _startup_sync_policy_cache,
)


def _enterprise_record() -> dict:
    return {
        "id": "900",
        "enterpriseName": "接口单企业",
        "creditCode": "913502000000000900",
        "registerAddress": "厦门市思明区软件园",
        "operationInfoList": [
            {
                "year": "2025",
                "operatingIncome": 40000000,
                "rdExpense": 3000000,
                "rdPersonnelRatio": "0.1481",
            }
        ],
        "mainTechnologyList": [],
        "mainProductList": [],
        "honorQualificationList": [],
    }


def test_matched_conditions_keep_real_zero_and_mark_default_assumption() -> None:
    conditions = _build_matched_conditions({
        "条件匹配详情": [
            {
                "itemId": "zero",
                "条件名称": "二类知识产权数量",
                "政策原文": "二类知识产权数量不超过5项",
                "企业字段": "$.classIICount",
                "企业值": 0,
                "单位": "项",
                "得分": 100,
                "evalStatus": "pass",
                "passSource": "semantic_quantitative_rule",
                "evidenceTier": "A1_exact",
                "计分归类": "verified",
                "bucketReason": "运行时企业事实与政策条件可直接比较。",
                "证据路径": ["$.classIICount"],
                "证据文本": ["0"],
            },
            {
                "itemId": "missing",
                "条件名称": "研发投入",
                "政策原文": "研发投入不低于100万元",
                "企业值": None,
                "得分": 70,
                "evalStatus": "pass",
                "passSource": "default_missing_assumed",
            },
            {
                "itemId": "failed",
                "条件名称": "销售收入",
                "evalStatus": "fail",
            },
        ]
    })

    assert [item["itemId"] for item in conditions] == ["zero", "missing"]
    assert conditions[0]["enterpriseValue"] == 0
    assert conditions[0]["hasEnterpriseEvidence"] is True
    assert conditions[0]["evidenceTier"] == "A1_exact"
    assert conditions[0]["scoreBucket"] == "verified"
    assert conditions[0]["bucketReason"] == "运行时企业事实与政策条件可直接比较。"
    assert conditions[1]["enterpriseValue"] is None
    assert conditions[1]["passSource"] == "default_missing_assumed"
    assert conditions[1]["hasEnterpriseEvidence"] is False


def test_condition_scores_include_failed_and_excluded_conditions() -> None:
    conditions = _build_condition_scores({
        "条件匹配详情": [
            {"itemId": "pass", "条件名称": "通过项", "得分": 100, "evalStatus": "pass", "是否参与计分": True},
            {"itemId": "fail", "条件名称": "失败项", "得分": 0, "evalStatus": "fail", "是否参与计分": True},
            {"itemId": "pending", "条件名称": "不可计算项", "得分": 0, "evalStatus": "not_evaluable", "是否参与计分": False},
        ]
    })

    assert [item["itemId"] for item in conditions] == ["pass", "fail", "pending"]
    assert [item["score"] for item in conditions] == [100, 0, 0]
    assert [item["countsTowardsScore"] for item in conditions] == [True, True, False]


def test_match_score_level_uses_specified_score_boundaries() -> None:
    cases = {
        None: None,
        0: 0,
        1: 0,
        1.01: 1,
        40: 1,
        40.01: 2,
        60: 2,
        60.01: 3,
        70: 3,
        70.01: 4,
        90: 4,
        90.01: 5,
        100: 5,
    }

    for match_score, expected_level in cases.items():
        assert _match_score_level(match_score) == expected_level


def test_default_assumed_with_non_empty_value_is_not_marked_as_evidence() -> None:
    """缺失默认放行即使 enterpriseValue 非空也不算企业证据。

    字段映射可能把无关字段填进来 —— 真实数据里「大专以上学历占比≥50%」这条
    被填入了「职工总数 15」，旧实现只看 enterpriseValue 非空就标 true，
    于是出现 hasEnterpriseEvidence=true 但 evidence=[]、evidenceRatio=0 的矛盾。
    """
    conditions = _build_matched_conditions({
        "条件匹配详情": [
            {
                "itemId": "3-1",
                "条件名称": "企业职工总数中大专以上学历占比",
                "政策原文": "具有大专以上学历的员工占企业职工总数的50%以上；",
                "企业字段": "企业职工总数",
                "企业值": "15",
                "得分": 70.0,
                "evalStatus": "pass",
                "passSource": "default_missing_assumed",
            },
            {
                "itemId": "5-1",
                "条件名称": "中小企业评价指标",
                "政策原文": "综合评价得分不低于60分",
                "企业值": "评价总分81分",
                "得分": 81.0,
                "evalStatus": "pass",
                "passSource": "tech_sme_evaluation_partial_assumed",
            },
            {
                "itemId": "5-2",
                "条件名称": "评价指标全核验",
                "政策原文": "综合评价得分不低于60分",
                "企业值": "评价总分74分",
                "得分": 74.0,
                "evalStatus": "pass",
                "passSource": "tech_sme_evaluation_total_score",
            },
        ]
    })

    by_id = {item["itemId"]: item for item in conditions}
    assert by_id["3-1"]["hasEnterpriseEvidence"] is False
    # 含缺失维度补足分的评价条件同样不算已核验证据
    assert by_id["5-1"]["hasEnterpriseEvidence"] is False
    # 三维度全部核验的评价条件是真实证据
    assert by_id["5-2"]["hasEnterpriseEvidence"] is True


def test_rejected_evidence_is_never_exposed_as_enterprise_evidence() -> None:
    conditions = _build_matched_conditions({
        "条件匹配详情": [{
            "itemId": "rejected",
            "条件名称": "年销售收入",
            "企业值": "2399.52 亿元",
            "得分": 30,
            "evalStatus": "pass",
            "passSource": "semantic_quantitative_rule",
            "evidenceTier": "R_rejected",
            "计分归类": "unverified",
            "证据路径": ["$.invalid.path"],
            "证据文本": ["2399.52"],
        }],
    })

    assert conditions[0]["hasEnterpriseEvidence"] is False
    assert conditions[0]["evidence"] == []


def test_local_verified_condition_backfills_normalized_evidence() -> None:
    conditions = _build_matched_conditions({
        "条件匹配详情": [{
            "itemId": "local",
            "条件名称": "资产规模",
            "企业字段": "资产总额(万元)",
            "企业值": "976.51",
            "单位": "万元",
            "得分": 100,
            "evalStatus": "pass",
            "passSource": "semantic_quantitative_rule",
            "evidenceTier": "A1_exact",
            "计分归类": "verified",
        }],
    })

    assert conditions[0]["hasEnterpriseEvidence"] is True
    assert conditions[0]["evidence"] == [{
        "sourcePath": "$normalized.资产总额(万元)",
        "sourceText": "976.51万元",
    }]


def test_same_title_policies_keep_distinct_ids() -> None:
    """同名不同政策必须各自保留真实 id。

    匹配结果原先不带 id，下游只能按标题反查政策表回填，集美区两条同名扶持政策
    （6529 / 6569）会被回填成同一个 id，前端无法区分。
    """
    from api_server import _build_enterprise_recommendations

    title = "集美区国家级高新技术企业、科技进步奖扶持"
    policies = [
        {"id": "6529", "title": title, "policy_info": {"applicableScope": "集美区"}},
        {"id": "6569", "title": title, "policy_info": {"applicableScope": "集美区"}},
    ]
    match_result = {
        "政策匹配结果": [
            {"政策标题": title, "fusionPolicyId": "6529", "final_score": 70.0, "是否推荐": 0},
            {"政策标题": title, "fusionPolicyId": "6569", "final_score": 0.0, "是否推荐": 0},
        ]
    }

    recommendations = _build_enterprise_recommendations(policies, match_result)

    assert len(recommendations) == 2
    assert [item["fusionPolicyId"] for item in recommendations] == ["6529", "6569"]
    assert [item["matchScore"] for item in recommendations] == [70.0, 0.0]


def test_match_result_carries_its_own_policy_id() -> None:
    """_match_single_task 必须把政策自身 id 写进结果，供下游区分同名政策。"""
    from service.matching_service import _match_single_task

    enterprise = {"企业名称": "测试企业", "注册地": "厦门市", "原始数据": {}}
    policy = {"id": "6529", "title": "集美区国家级高新技术企业、科技进步奖扶持"}

    result = _match_single_task((enterprise, policy, "测试企业"))

    assert result["fusionPolicyId"] == "6529"
    assert result["政策标题"] == policy["title"]


def test_public_recommendation_keeps_only_lean_association_fields() -> None:
    policy = {"id": "6617", "title": "软件企业政策"}
    match_result = {
        "政策标题": "软件企业政策",
        "fusionPolicyId": "6617",
        "final_score": 68.5,
        "是否推荐": 1,
        "associationMatch": {
            "score": 86.0,
            "level": "strong",
            "isAssociated": True,
            "status": "matched",
            "reasons": ["行业一致", "研发方向一致", "区域一致", "不应返回的第四条"],
            "calculation": {
                "formula": "Σ(维度权重×状态系数)÷有效维度权重合计×100",
                "expression": "86.0÷100.0×100=86.0",
                "weightedContribution": 86.0,
                "effectiveWeight": 100.0,
                "score": 86.0,
            },
            "dimensions": [{
                "dimension": "industry",
                "weight": 20,
                "status": "strong",
                "coefficient": 1.0,
                "contribution": 20.0,
                "normalizedContribution": 20.0,
                "includedInDenominator": True,
                "reason": "行业一致",
                "enterpriseEvidence": [{"field": "一级行业", "value": "软件业"}],
                "policyEvidence": [{"field": "industryField", "text": "软件业"}],
            }],
            "modelInput": {"enterpriseProfile": {"一级行业": "软件业"}},
            "modelOutput": {"policyId": "6617"},
            "metrics": {"totalTokens": 100},
        },
    }

    item = _build_enterprise_recommendation_item(policy, match_result)

    assert item["matchScore"] == 68.5
    assert item["isRecommended"] is True
    assert item["associationMatch"] == {
        "score": 86.0,
        "level": "strong",
        "isAssociated": True,
        "status": "matched",
        "reasons": ["行业一致", "研发方向一致", "区域一致"],
        "calculation": {
            "formula": "Σ(维度权重×状态系数)÷有效维度权重合计×100",
            "expression": "86.0÷100.0×100=86.0",
            "weightedContribution": 86.0,
            "effectiveWeight": 100.0,
            "score": 86.0,
            "dimensions": [{
                "dimension": "industry",
                "name": "所属行业",
                "weight": 20,
                "status": "strong",
                "coefficient": 1.0,
                "rawContribution": 20.0,
                "normalizedContribution": 20.0,
                "includedInDenominator": True,
                "reason": "行业一致",
            }],
            "note": "关联分仅表示企业特征与政策方向的关联程度，不表示企业满足申报条件。",
        },
    }


def test_request_event_log_writes_to_request_specific_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("api_server.API_LOG_DIR", tmp_path)
    request_log = _RequestEventLog("request123")

    _log_api_event("单企业匹配进度", request_log=request_log, requestId="request123", message="政策=测试政策")

    log_files = list(tmp_path.glob("api_request_*_request123.log"))
    assert len(log_files) == 1
    content = log_files[0].read_text(encoding="utf-8")
    assert '"event": "单企业匹配进度"' in content
    assert '"message": "政策=测试政策"' in content


def test_startup_sync_policy_cache_loads_full_policy_set() -> None:
    with patch("api_server.load_structure_policies", return_value=[{"id": "6617"}]) as mocked_load_policies:
        _startup_sync_policy_cache()

    assert mocked_load_policies.call_args.kwargs["max_policies"] is None
    assert mocked_load_policies.call_args.kwargs["include_document_content"] is False


def test_single_enterprise_match_api_accepts_raw_upstream_record() -> None:
    policies = [
        {
            "id": "6617",
            "title": "厦门市企业创新中心授牌申报",
            "ruleTableJson": None,
            "structured_conditions": [{"条件名称": "研发投入", "政策要求": "研发投入不低于100万元"}],
            "raw_detail": {
                "mainFileTitle": "厦门市企业创新中心授牌申报",
                "issuingDepartment": "厦门市科学技术局",
                "maxAmount": "100万元",
                "applicableScope": "厦门市企业",
                "policyInfo": {
                    "industryField": "软件和信息技术服务业",
                    "industryFieldPath": "信息技术/软件",
                },
                "conditions": {
                    "applicationStartTime": "2026-08-01 00:00:00",
                    "applicationEndTime": "2026-08-31 23:59:59",
                },
                "documents": [{
                    "id": "doc-1001",
                    "policyTitle": "科技创新类",
                    "fileName": "完整附件.pdf",
                    "attachmentId": 123,
                    "content": "仅应保留在 runId 归档的完整文档内容",
                }],
                "supportMethods": [{"contentDesc": "资金奖励"}],
            },
        }
    ]
    match_result = [
        {
            "企业名称": "接口单企业",
            "企业信息": {"企业名称": "接口单企业"},
            "政策匹配结果": [
                {
                    "政策标题": "厦门市企业创新中心授牌申报",
                    "final_score": 88.5,
                    "是否推荐": 1,
                    "条件匹配详情": [
                        {
                            "itemId": "C1",
                            "条件名称": "研发投入",
                            "政策原文": "研发投入不低于100万元",
                            "企业字段": "$.operationInfoList[0].rdExpense",
                            "企业值": "300万元",
                            "单位": "万元",
                            "得分": 100,
                            "evalStatus": "pass",
                            "passSource": "semantic_quantitative_rule",
                            "证据路径": ["$.operationInfoList[0].rdExpense"],
                            "证据文本": ["3000000"],
                        }
                    ],
                }
            ],
        }
    ]

    def run_matching_with_progress(**kwargs):
        kwargs["progress_callback"]("匹配完成：企业=接口单企业 政策=厦门市企业创新中心授牌申报")
        return match_result

    server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        enterprise_record = _enterprise_record()
        enterprise_record.pop("creditCode")
        request = Request(
            f"http://127.0.0.1:{server.server_port}/api/pematching/match/enterprise?policyId=6617",
            data=json.dumps(enterprise_record, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with TemporaryDirectory() as detail_dir:
            with (
                patch("api_server.load_structure_policies", return_value=policies) as mocked_load_policies,
                patch("api_server.run_matching", side_effect=run_matching_with_progress) as mocked_run_matching,
                patch("api_server.ASSOCIATION_LLM_ENABLED", False),
                patch("api_server.ENTERPRISE_MATCH_DETAIL_DIR", Path(detail_dir)),
                patch("api_server._persist_enterprise_match_result") as mocked_persist_result,
                patch("api_server._log_api_event") as mocked_log_api_event,
                urlopen(request, timeout=5) as response,
            ):
                payload = json.loads(response.read().decode("utf-8"))

            detail_files = list(Path(detail_dir).glob("enterprise_match_*.json"))
            assert len(detail_files) == 1
            detail_file_size = detail_files[0].stat().st_size
            detail_payload = json.loads(detail_files[0].read_text(encoding="utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert payload["success"] is True
    assert payload["data"] == {
        "policies": [
            {
                "fusionPolicyId": "6617",
                "applicationStartTime": "2026-08-01 00:00:00",
                "applicationEndTime": "2026-08-31 23:59:59",
                "mainFileTitle": "厦门市企业创新中心授牌申报",
                "issuingDepartment": "厦门市科学技术局",
                "matchScore": 88.5,
                "level": 4,
                "isRecommended": True,
                # 证据充分度字段与 matchScore 正交，用于同分政策排序；
                # 本用例的匹配结果桩数据未提供这些统计，因此透传为 None。
                "evidenceRatio": None,
                "confidenceLevel": None,
                "verifiedConditionCount": None,
                "assumedConditionCount": None,
                "scoredConditionCount": None,
                "maxAmount": "100万元",
                "documents": [{
                    "id": "doc-1001",
                    "policyTitle": "科技创新类",
                    "fileName": "完整附件.pdf",
                    "filePath": "",
                    "policyAttribute": "",
                    "sourcePolicyId": "",
                }],
                "applicableScope": "厦门市企业",
                "industryField": "软件和信息技术服务业",
                "industryFieldPath": "信息技术/软件",
                "supportMethods": ["资金奖励"],
                "matchStatus": "matched",
                "recommendationBlockers": [],
                    "matchedConditions": [
                        {
                        "itemId": "C1",
                        "conditionName": "研发投入",
                        "applicationRequirement": "研发投入不低于100万元",
                        "enterpriseField": "$.operationInfoList[0].rdExpense",
                        "enterpriseValue": "300万元",
                            "unit": "万元",
                            "score": 100,
                            "evalStatus": "pass",
                            "countsTowardsScore": True,
                            "passSource": "semantic_quantitative_rule",
                            "evidenceTier": "",
                            "scoreBucket": "",
                            "bucketReason": "",
                            "hasEnterpriseEvidence": True,
                        "evidence": [
                            {
                                "sourcePath": "$.operationInfoList[0].rdExpense",
                                "sourceText": "3000000",
                            }
                            ],
                        }
                    ],
                    "conditionScores": [
                        {
                            "itemId": "C1",
                            "conditionName": "研发投入",
                            "applicationRequirement": "研发投入不低于100万元",
                            "enterpriseField": "$.operationInfoList[0].rdExpense",
                            "enterpriseValue": "300万元",
                            "unit": "万元",
                            "score": 100,
                            "evalStatus": "pass",
                            "countsTowardsScore": True,
                            "passSource": "semantic_quantitative_rule",
                            "evidenceTier": "",
                            "scoreBucket": "",
                            "bucketReason": "",
                            "hasEnterpriseEvidence": True,
                            "evidence": [
                                {
                                    "sourcePath": "$.operationInfoList[0].rdExpense",
                                    "sourceText": "3000000",
                                }
                            ],
                        }
                    ],
                    "associationMatch": {
                    "score": None,
                    "level": "unknown",
                    "isAssociated": None,
                    "status": "disabled",
                    "reasons": [],
                    "calculation": {
                        "formula": "Σ(维度权重×状态系数)÷有效维度权重合计×100",
                        "expression": "",
                        "weightedContribution": None,
                        "effectiveWeight": None,
                        "score": None,
                        "dimensions": [],
                        "note": "关联分仅表示企业特征与政策方向的关联程度，不表示企业满足申报条件。",
                    },
                },
                "retryable": False,
            }
        ]
    }
    assert detail_payload["runId"] == payload["meta"]["runId"]
    assert detail_payload["matchResult"] == match_result[0]
    assert detail_payload["policyInputs"][0]["raw_detail"]["documents"][0]["content"] == "仅应保留在 runId 归档的完整文档内容"
    assert detail_payload["request"]["creditCode"] == ""
    assert payload["meta"]["policyCount"] == 1

    persist_call = mocked_persist_result.call_args.kwargs
    assert persist_call["enterprise"]["企业ID"] == "900"
    assert persist_call["enterprise"]["企业名称"] == "接口单企业"
    assert persist_call["response_data"] == payload["data"]
    assert persist_call["detail_payload"] == detail_payload
    assert persist_call["detail_file"]["detailFileName"] == detail_files[0].name
    assert persist_call["detail_file"]["detailFileSize"] == detail_file_size
    assert persist_call["policy_count"] == 1

    policy_call = mocked_load_policies.call_args.kwargs
    assert policy_call["max_policies"] is None
    assert policy_call["include_document_content"] is False
    assert policy_call["include_sparse_document_content"] is False

    matching_call = mocked_run_matching.call_args.kwargs
    assert len(matching_call["enterprises"]) == 1
    assert matching_call["enterprises"][0]["企业名称"] == "接口单企业"
    assert matching_call["enterprises"][0]["销售收入(万元)"] == 4000
    assert matching_call["enterprises"][0]["原始数据"]["enterpriseName"] == "接口单企业"
    assert matching_call["policies"] == policies
    assert matching_call["output_file"] is None

    log_events = [call.args[0] for call in mocked_log_api_event.call_args_list]
    assert log_events == [
        "请求已接收",
        "单企业匹配参数已解析",
        "单企业匹配已加载结构化政策",
        "单企业匹配开始执行",
        "单企业匹配进度",
        "单企业匹配执行完成",
    ]
    parsed_log_fields = mocked_log_api_event.call_args_list[1].kwargs
    assert parsed_log_fields["enterpriseName"] == "接口单企业"
    assert "creditCode" not in parsed_log_fields
    progress_log_fields = mocked_log_api_event.call_args_list[4].kwargs
    assert progress_log_fields["message"] == "匹配完成：企业=接口单企业 政策=厦门市企业创新中心授牌申报"


def test_single_enterprise_match_api_rejects_missing_enterprise_id_before_policy_loading() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        enterprise_record = _enterprise_record()
        enterprise_record.pop("id")
        request = Request(
            f"http://127.0.0.1:{server.server_port}/api/pematching/match/enterprise",
            data=json.dumps(enterprise_record, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with patch("api_server.load_structure_policies") as mocked_load_policies:
            try:
                urlopen(request, timeout=5)
                raise AssertionError("缺少企业 ID 时应返回 HTTP 400")
            except HTTPError as exc:
                assert exc.code == 400
                payload = json.loads(exc.read().decode("utf-8"))
        mocked_load_policies.assert_not_called()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert payload == {
        "success": False,
        "message": "企业信息格式错误：必须提供 id 或 enterpriseId。",
    }


def test_single_enterprise_match_api_reports_empty_request_body() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/api/pematching/match/enterprise",
            data=b"",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(request, timeout=5)
            raise AssertionError("空请求体应返回 HTTP 400")
        except HTTPError as exc:
            assert exc.code == 400
            payload = json.loads(exc.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert payload == {
        "success": False,
        "message": "企业信息请求体为空，请使用 application/json 提交单个企业对象。",
    }


def test_persist_enterprise_match_result_sends_exact_run_and_json_payload() -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return b'{"success":true}'

    enterprise = {
        "企业ID": "900",
        "企业名称": "接口单企业",
        "统一社会信用代码": "913502000000000900",
    }
    response_data = {"policies": [{"fusionPolicyId": "6617", "matchScore": 88.5}]}
    detail_payload = {
        "schemaVersion": "enterprise-match-detail-v1",
        "runId": "a" * 32,
        "request": {"enterpriseId": "900"},
        "policyInputs": [],
        "matchResult": {},
    }

    with patch("api_server.urlopen", return_value=Response()) as mocked_urlopen:
        _persist_enterprise_match_result(
            enterprise=enterprise,
            response_data=response_data,
            detail_payload=detail_payload,
            detail_file={
                "detailFileName": f"enterprise_match_{detail_payload['runId']}.json",
                "detailFileSize": 7865401,
            },
            policy_count=1,
        )

    request = mocked_urlopen.call_args.args[0]
    sent_payload = json.loads(request.data.decode("utf-8"))
    assert sent_payload["runId"] == detail_payload["runId"]
    assert sent_payload["enterpriseId"] == "900"
    assert sent_payload["enterpriseName"] == "接口单企业"
    assert sent_payload["creditCode"] == "913502000000000900"
    assert sent_payload["policyCount"] == 1
    assert json.loads(sent_payload["responseJson"]) == response_data
    assert sent_payload["detailFileName"] == f"enterprise_match_{detail_payload['runId']}.json"
    assert sent_payload["detailFileSize"] == 7865401
    assert "detailJson" not in sent_payload


def test_persist_enterprise_match_result_reports_database_service_failure() -> None:
    with patch("api_server.urlopen", side_effect=URLError("连接被拒绝")):
        try:
            _persist_enterprise_match_result(
                enterprise={"企业ID": "900", "企业名称": "接口单企业"},
                response_data={"policies": []},
                detail_payload={
                    "schemaVersion": "enterprise-match-detail-v1",
                    "runId": "b" * 32,
                },
                detail_file={
                    "detailFileName": f"enterprise_match_{'b' * 32}.json",
                    "detailFileSize": 1024,
                },
                policy_count=0,
            )
            raise AssertionError("数据库服务不可达时应抛出运行错误")
        except RuntimeError as exc:
            assert "无法连接匹配结果数据库服务" in str(exc)


def test_inline_rule_table_match_removes_request_temp_file() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/api/pematching/match",
            data=json.dumps({"ruleTableJson": {"schemaVersion": "test"}}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with TemporaryDirectory() as temp_root_text:
            temp_root = Path(temp_root_text)

            def load_inline_rule_table(rule_path: str, _title: str) -> list[dict]:
                assert Path(rule_path).exists()
                return [{"id": "inline", "title": "临时规则表"}]

            with (
                patch("api_server.PROJECT_ROOT", temp_root),
                patch(
                    "api_server._load_enterprises_for_request",
                    return_value=([{"企业名称": "测试企业"}], {"enterpriseSource": "test"}),
                ),
                patch("api_server._load_rule_profile_policy", side_effect=load_inline_rule_table),
                patch("api_server.run_matching", return_value=[]),
                urlopen(request, timeout=5) as response,
            ):
                payload = json.loads(response.read().decode("utf-8"))

            temp_rule_table_dir = temp_root / "_temp_rule_tables"
            assert not list(temp_rule_table_dir.glob("temp_*.json"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert payload["success"] is True
