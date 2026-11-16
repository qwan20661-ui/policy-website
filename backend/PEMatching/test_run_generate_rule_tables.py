"""批量规则表生成入口的本地回归测试。"""

from __future__ import annotations

from typing import Any, Dict

import run_generate_rule_tables as batch

from run_generate_rule_tables import (
    _safe_text,
    build_application_requirement_audit,
    evaluate_rule_table_usability,
    generate_rule_table_via_api,
    parse_args,
    save_rule_table,
    write_csv_report,
)


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self.payload = payload
        self.raise_called = False

    def raise_for_status(self) -> None:
        self.raise_called = True

    def json(self) -> Dict[str, Any]:
        return self.payload


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.request: Dict[str, Any] = {}

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.request = {"url": url, **kwargs}
        return self.response


def _table(*statuses: str) -> Dict[str, Any]:
    return {
        "hardRuleGroups": [
            {
                "conditionNo": 1,
                "conditionFieldItems": [
                    {"itemId": f"1-{index}", "itemStatus": status}
                    for index, status in enumerate(statuses, start=1)
                ],
            }
        ],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }


def test_batch_generation_uses_the_same_rule_table_api_as_structure_page() -> None:
    response = _FakeResponse({"success": True, "data": {"ruleTableJson": _table("rule"), "stats": {"itemCount": 1}}})
    session = _FakeSession(response)

    result = generate_rule_table_via_api(
        session=session,
        base_url="http://structure.example/",
        fusion_policy_id="12345",
        timeout=90,
    )

    assert result["stats"]["itemCount"] == 1
    assert response.raise_called is True
    assert session.request == {
        "url": "http://structure.example/api/rule-table/generate",
        "params": {"fusionPolicyId": "12345"},
        "json": {"fusionPolicyId": "12345"},
        "timeout": 90,
    }


def test_rule_table_usability_requires_at_least_one_executable_item() -> None:
    usable = evaluate_rule_table_usability(_table("rule", "reference"))
    assert usable["ruleTableUsable"] is True
    assert usable["usabilityConclusion"] == "可用于规则匹配"
    assert usable["conditionFieldItemCount"] == 2

    reference_only = evaluate_rule_table_usability(_table("reference", "supporting"))
    assert reference_only["ruleTableUsable"] is True
    assert reference_only["usabilityConclusion"] == "可用于规则匹配（需人工复核）"

    unavailable = evaluate_rule_table_usability(_table())
    assert unavailable["ruleTableUsable"] is False
    assert unavailable["usabilityConclusion"] == "不可用于规则匹配"


def test_batch_filter_text_decodes_literal_unicode_escapes() -> None:
    assert _safe_text("\\u53a6\\u95e8\\u5e02") == "厦门市"


def test_workers_defaults_to_serial_and_accepts_ten_concurrent_tasks() -> None:
    assert parse_args([]).workers == 1
    assert parse_args(["--workers", "10"]).workers == 10


def test_write_back_payload_contains_only_rule_table_data() -> None:
    response = _FakeResponse({"success": True, "data": {}})
    session = _FakeSession(response)

    save_rule_table(
        session=session,
        base_url="http://structure.example",
        fusion_policy_id="6452",
        rule_table_json=_table("unresolved"),
        timeout=90,
    )

    assert session.request["json"] == {
        "fusionPolicyId": "6452",
        "ruleTableJson": _table("unresolved"),
        "ruleTableOnly": True,
    }


def test_application_requirement_audit_compares_source_sections_with_extracted_items() -> None:
    detail = {
        "conditions": [{
            "id": "1005",
            "applicableTarget": "科技型中小企业",
            "applicationInstruction": "网上办理",
            "applicationRequirement": "1、企业须为居民企业。；2、职工总数不超过500人。；3、拥有高新技术企业资格证书。",
        }]
    }
    rule_table = {
        "hardRuleGroups": [],
        "softRuleGroups": [{
            "conditionNo": 2,
            "rawText": "职工总数不超过500人",
            "evaluationRole": "quantitative_signal",
            "conditionOperator": "AND",
            "conditionFieldItems": [{
                "itemId": "2-1",
                "sourceFragmentId": "C2-A1",
                "itemName": "企业职工总数",
                "policyFragment": "职工总数不超过500人",
                "itemStatus": "rule",
                "enterpriseFields": ["企业职工总数"],
                "sourceStandardField": {"level3Category": "企业职工总数"},
                "matchRule": {"fieldType": "number", "operator": "lte", "targetValue": 500, "unit": "人"},
            }],
        }],
        "specialPassRuleGroups": [],
        "definitionNotes": [],
        "applicationChecklistGroups": [],
    }

    audit = build_application_requirement_audit(detail, rule_table)

    assert audit["sourceRequirementSectionCount"] == 3
    assert audit["coveredConditionNoCount"] == 1
    assert audit["uncoveredConditionNos"] == [1, 3]
    assert [section["extractionStatus"] for section in audit["sections"]] == [
        "not_extracted", "extracted", "not_extracted",
    ]
    extracted = audit["sections"][1]["extractedGroups"][0]["extractedItems"][0]
    assert extracted["itemName"] == "企业职工总数"
    assert extracted["policyFragment"] == "职工总数不超过500人"
    assert extracted["matchRule"]["targetValue"] == 500


def test_audit_does_not_attach_same_condition_number_to_another_source_record() -> None:
    detail = {
        "conditions": [
            {"id": "a", "applicationRequirement": "1、企业注册成立1年以上。"},
            {"id": "b", "applicationRequirement": "1、研发费用占比不低于5%。"},
        ]
    }
    item = {
        "itemId": "1-1",
        "sourceFragmentId": "C1-A1",
        "itemName": "研发费用占比",
        "policyFragment": "研发费用占比不低于5%",
        "itemStatus": "rule",
        "enterpriseFields": ["研发费用占比"],
        "matchRule": {"fieldType": "number", "operator": "gte", "targetValue": 5, "unit": "%"},
    }
    rule_table = {
        "hardRuleGroups": [],
        "softRuleGroups": [{
            "conditionNo": 1,
            "rawText": "研发费用占比不低于5%",
            "conditionFieldItems": [item],
        }],
        "specialPassRuleGroups": [],
    }

    audit = build_application_requirement_audit(detail, rule_table)

    assert [section["extractionStatus"] for section in audit["sections"]] == ["not_extracted", "extracted"]
    assert audit["coveredSourceSectionCount"] == 1
    assert audit["uncoveredSectionKeys"] == ["R1-C1-S1"]


def test_csv_report_contains_source_requirements_and_uncovered_condition_numbers(tmp_path) -> None:
    output_path = tmp_path / "rule-table.csv"
    audit = build_application_requirement_audit(
        {"conditions": [{"applicationRequirement": "1、企业须为居民企业。；2、职工总数不超过500人。"}]},
        _table("rule"),
    )
    write_csv_report(
        output_path,
        [{
            "index": 1,
            "fusionPolicyId": "6548",
            "title": "科技型中小企业评价",
            "status": "success",
            "structuredConditionCount": 1,
            "writeBackStatus": "saved",
            "ruleTableUsable": True,
            "usabilityConclusion": "可用于规则匹配",
            "usabilityReason": "测试",
            "conditionFieldItemCount": 1,
            "itemStatusCounts": {"rule": 1, "unresolved": 0, "reference": 0, "supporting": 0},
            "applicationRequirementAudit": audit,
        }],
    )

    content = output_path.read_text(encoding="utf-8-sig")
    assert "申报条件原文" in content
    assert "企业须为居民企业" in content
    assert "未提取编号" in content
    assert ",2," in content


def test_no_matchable_rule_table_overwrites_the_previous_rule_table(monkeypatch: Any) -> None:
    empty_table = _table()
    saved: Dict[str, Any] = {}

    monkeypatch.setattr(
        batch.StructurePolicyClient,
        "fetch_policy_detail",
        lambda self, fusion_policy_id: {"conditions": [{"applicationRequirement": "仅有申报流程"}]},
    )
    monkeypatch.setattr(
        batch,
        "generate_rule_table_via_api",
        lambda **kwargs: {"ruleTableJson": empty_table, "stats": {"noMatchableRules": True, "itemCount": 0}},
    )
    monkeypatch.setattr(
        batch,
        "save_rule_table",
        lambda **kwargs: saved.update(kwargs) or {"success": True},
    )

    result = batch.process_policy(
        index=1,
        total=1,
        summary={"id": "6457", "mainFileTitle": "测试政策"},
        structure_api_base_url="http://structure.example",
        rule_table_api_base_url="http://gateway.example",
        timeout=90,
        skip_existing=False,
        check_only=False,
        log=lambda message: None,
    )

    assert result["status"] == "saved_no_matchable_rules"
    assert result["writeBackStatus"] == "saved"
    assert "覆盖旧规则表" in result["message"]
    assert result["applicationRequirementAudit"]["conditions"][0]["applicationRequirement"] == "仅有申报流程"
    assert saved["fusion_policy_id"] == "6457"
    assert saved["rule_table_json"] == empty_table
