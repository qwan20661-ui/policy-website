"""规则表生成后的成长性分流与统计口径保护测试。"""

from __future__ import annotations

import json
from datetime import datetime

from service import rule_table_generator
from service.policy_loader import StructurePolicyClient
from service.rule_table_generator import (
    _call_llm_for_generation,
    _build_generation_prompt,
    _build_numbered_requirement_context,
    _generate_rule_table_by_requirement_sections,
    _sanitize_llm_json,
    _ensure_hard_exclusion_coverage,
    _filter_items_not_grounded_in_requirements,
    _normalize_financing_ratio_rules,
    _normalize_rule_table_items,
    _route_rule_groups,
    _validate_rule_table_json,
)


def _item(item_id: str, name: str, fragment: str, *, target: object, unit: str = "") -> dict:
    return {
        "itemId": item_id,
        "sourceFragmentId": "C1-A1",
        "itemName": name,
        "itemStatus": "rule",
        "enterpriseFields": [name],
        "policyFragment": fragment,
        "matchRule": {
            "ruleId": item_id,
            "ruleKind": "direct",
            "fieldType": "number",
            "operator": "gte",
            "targetValue": target,
            "unit": unit,
            "description": fragment,
        },
    }


def _group(number: int, text: str, *items: dict) -> dict:
    return {
        "conditionNo": number,
        "rawText": text,
        "conditionOperator": "AND",
        "conditionFieldItems": list(items),
        "unresolvedFragments": [],
    }


def _fields() -> list[dict]:
    return [
        {"rowNo": 1, "level3Category": "成立年限", "coreField": "成立年限、经营年限"},
        {"rowNo": 2, "level3Category": "核心产品收入占比", "coreField": "核心产品收入占比"},
        {"rowNo": 3, "level3Category": "科技人员数量", "coreField": "科技人员人数"},
        {"rowNo": 4, "level3Category": "企业信用", "coreField": "企业信用"},
    ]


def test_quantitative_thresholds_are_routed_to_independent_soft_signals():
    compliance = _item("3-1", "企业信用", "企业未发生重大违法失信行为", target="符合")
    compliance["matchRule"].update({"fieldType": "string", "operator": "llm_judge"})
    table = {
        "hardRuleGroups": [
            _group(1, "企业须注册成立365个日历天数以上", _item("1-1", "成立年限", "企业须注册成立365个日历天数以上", target=365, unit="天")),
            _group(2, "主要产品收入之和超过50%", _item("2-1", "核心产品收入占比", "主要产品收入之和超过50%", target=50, unit="%")),
            _group(3, "企业未发生重大违法失信行为", compliance),
        ],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }

    _normalize_rule_table_items(table, _fields())

    assert table["hardRuleGroups"] == []
    assert [group["conditionNo"] for group in table["softRuleGroups"]] == [1, 2, 3]
    assert [group["evaluationRole"] for group in table["softRuleGroups"]] == [
        "quantitative_signal", "quantitative_signal", "eligibility_signal",
    ]
    assert all(len(group["conditionFieldItems"]) == 1 for group in table["softRuleGroups"])


def test_field_reference_uses_api_data_without_reading_local_cache(monkeypatch, tmp_path):
    api_fields = [
        {"level1Category": "企业情况", "level2Category": "基本信息", "level3Category": "企业名称", "coreField": "企业名称"},
        {"level1Category": "企业情况", "level2Category": "基本信息", "level3Category": "注册时间", "coreField": "注册时间"},
    ]
    stale_snapshot = tmp_path / "申报条件管理_20260726.json"
    stale_snapshot.write_text(json.dumps({"data": _fields()}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(rule_table_generator, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(rule_table_generator, "_try_fetch_api_fields", lambda: api_fields)

    reference = rule_table_generator._sync_and_load_field_reference()

    saved = json.loads((tmp_path / "申报条件管理.json").read_text(encoding="utf-8"))
    assert "企业名称 → 企业名称" in reference
    assert "成立年限" not in reference
    assert saved["data"] == api_fields


def test_field_reference_does_not_fallback_to_local_cache_when_api_is_empty(monkeypatch, tmp_path):
    (tmp_path / "申报条件管理_20260726.json").write_text(
        json.dumps({"data": _fields()}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(rule_table_generator, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(rule_table_generator, "_try_fetch_api_fields", lambda: [])

    assert rule_table_generator._sync_and_load_field_reference() == "（未找到标准字段配置）"


def test_field_reference_reuses_today_snapshot_without_calling_api(monkeypatch, tmp_path):
    today_snapshot = tmp_path / "申报条件管理.json"
    today_snapshot.write_text(
        json.dumps({"source": {"exportedAt": datetime.now().isoformat()}, "data": _fields()}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(rule_table_generator, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(rule_table_generator, "_try_fetch_api_fields", lambda: (_ for _ in ()).throw(AssertionError("不应调用 API")))

    reference = rule_table_generator._sync_and_load_field_reference()

    assert "成立年限 → 成立年限、经营年限" in reference


def test_generate_rule_table_reuses_synced_field_entries_for_normalization(monkeypatch):
    field_entries = [{"rowNo": 1, "level3Category": "企业名称", "coreField": "企业名称"}]
    normalized_entries = []
    generated_table = {
        "hardRuleGroups": [],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
        "definitionNotes": [],
        "applicationChecklistGroups": [],
    }

    monkeypatch.setattr(
        StructurePolicyClient,
        "fetch_policy_detail",
        lambda self, fusion_policy_id: {"policyInfo": {"policyTitle": "测试政策"}, "conditions": []},
    )
    monkeypatch.setattr(rule_table_generator, "_load_prompt_template", lambda: "提示词")
    monkeypatch.setattr(rule_table_generator, "_sync_and_load_field_entries", lambda: field_entries)
    monkeypatch.setattr(
        rule_table_generator,
        "_generate_rule_table_by_requirement_sections",
        lambda detail_data, prompt_template, field_reference: generated_table,
    )
    monkeypatch.setattr(rule_table_generator, "_filter_items_not_grounded_in_requirements", lambda table, detail: None)
    monkeypatch.setattr(
        rule_table_generator,
        "_normalize_rule_table_items",
        lambda table, entries: normalized_entries.extend(entries),
    )
    monkeypatch.setattr(rule_table_generator, "_deduplicate_scoring_groups", lambda table: None)

    result = rule_table_generator.generate_rule_table("6450")

    assert normalized_entries == field_entries
    assert result["ruleTableJson"]["fusionPolicyId"] == "6450"


def test_api_field_loader_requests_total_records_from_pagination(monkeypatch):
    raw_fields = [
        {"id": str(index), "name": f"字段{index}", "coreField": f"字段{index}"}
        for index in range(1, 13)
    ]
    requests = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(url, *, params, timeout):
        requests.append(params)
        if params == {"page": 1, "size": 1}:
            return Response({"success": True, "data": [raw_fields[0]], "pagination": {"total": 12}})
        assert params == {"page": 1, "size": 12}
        return Response({"success": True, "data": raw_fields, "pagination": {"total": 12}})

    monkeypatch.setattr(rule_table_generator.requests, "get", fake_get)

    fields = rule_table_generator._try_fetch_api_fields()

    assert requests == [{"page": 1, "size": 1}, {"page": 1, "size": 12}]
    assert [field["level3Category"] for field in fields] == [f"字段{index}" for index in range(1, 13)]


def test_non_matchable_qualitative_group_is_not_kept_as_hard_rule():
    source_text = "国家级高企资格即将到期应重新认定的企业"
    table = {
        "hardRuleGroups": [_group(1, source_text, _item("1-1", "企业资质", source_text, target="符合"))],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }

    _normalize_rule_table_items(table, _fields())

    assert table["hardRuleGroups"] == []
    assert table["softRuleGroups"] == []


def test_original_scoring_wording_routes_numeric_rule_to_soft_group():
    source_text = "综合评价得分达到70分以上（不含70分）为符合认定要求"
    table = {
        "hardRuleGroups": [_group(1, source_text, _item("1-1", "综合评价得分", source_text, target=70, unit="分"))],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }

    _normalize_rule_table_items(table, _fields())

    assert table["hardRuleGroups"] == []
    assert [group["conditionNo"] for group in table["softRuleGroups"]] == [1]
    assert table["softRuleGroups"][0]["evaluationRole"] == "quantitative_signal"


def test_staff_workday_definition_does_not_create_staff_count_threshold():
    source_text = "科技人员指直接从事研发及相关技术创新活动，累计实际工作时间在183天以上的人员"
    table = {
        "hardRuleGroups": [_group(5, source_text, _item("5-1", "科技人员数量", source_text, target=183, unit="天"))],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }

    _normalize_rule_table_items(table, _fields())

    assert table["hardRuleGroups"] == []
    assert table["softRuleGroups"] == []
    assert table["definitionNotes"] == [
        {
            "noteId": "D1",
            "subject": "科技人员",
            "noteType": "统计口径",
            "content": source_text,
            "affectsFields": ["科技人员数量"],
        }
    ]


def test_application_material_group_is_preserved_without_scoring():
    source_text = "申报材料包括营业执照复印件、审计报告和近三年财务会计报告"
    table = {
        "hardRuleGroups": [_group(8, source_text, _item("8-1", "营业执照", source_text, target="有"))],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }

    _normalize_rule_table_items(table, _fields())

    assert table["hardRuleGroups"] == []
    assert table["softRuleGroups"] == []
    assert table["applicationChecklistGroups"][0]["checklistNo"] == 8
    assert table["applicationChecklistGroups"][0]["checklistType"] == "材料"


def test_rule_kind_must_use_the_supported_enum():
    table = {
        "hardRuleGroups": [_group(1, "测试", _item("1-1", "成立年限", "测试", target=365, unit="天"))],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }
    table["hardRuleGroups"][0]["conditionFieldItems"][0]["matchRule"]["ruleKind"] = "typo"

    valid, message = _validate_rule_table_json(table)

    assert valid is False
    assert "ruleKind" in message


def test_generation_request_uses_configured_model_and_corrects_invalid_json(monkeypatch):
    class FakeResponse:
        status_code = 200

        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": self.content}}]}

    calls = []
    valid_json = '{"hardRuleGroups":[{"conditionNo":1,"conditionFieldItems":[{"itemId":"1-1","sourceFragmentId":"C1-A1","policyFragment":"测试","itemStatus":"rule"}]}],"softRuleGroups":[],"specialPassRuleGroups":[]}'

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse('{"hardRuleGroups":[' if len(calls) == 1 else valid_json)

    monkeypatch.setattr(rule_table_generator, "LLM_API_URL", "http://llm.example:3000/v1/chat/completions")
    monkeypatch.setattr(rule_table_generator, "LLM_MODEL_NAME", "qwen3-8b")
    monkeypatch.setattr(rule_table_generator, "LLM_CONNECT_TIMEOUT", 10)
    monkeypatch.setattr(rule_table_generator, "LLM_TIMEOUT", 660)
    monkeypatch.setattr(rule_table_generator.requests, "post", fake_post)

    result = _call_llm_for_generation("测试提示词")

    assert result["hardRuleGroups"][0]["conditionNo"] == 1
    assert len(calls) == 2
    assert calls[0]["url"] == "http://llm.example:3000/v1/chat/completions"
    assert calls[0]["json"]["model"] == "qwen3-8b"
    assert calls[0]["timeout"] == (10, 660)
    assert "上一轮输出不是合法 JSON" in calls[1]["json"]["messages"][-1]["content"]


def test_empty_rule_groups_are_a_valid_no_matchable_result():
    valid, message = _validate_rule_table_json(
        {"hardRuleGroups": [], "softRuleGroups": [], "specialPassRuleGroups": []}
    )

    assert valid is True
    assert message == ""


def test_numbered_requirement_context_preserves_original_condition_numbers():
    detail = {
        "conditions": [
            {
                "applicationRequirement": (
                    "7、工业总产值3亿元以上且一季度产值增速达8%以上；"
                    "或1亿元以下且增速达15%以上。"
                )
            }
        ]
    }

    context = _build_numbered_requirement_context(detail)

    assert context.startswith("[C7]\n")
    assert "工业总产值3亿元以上" in context
    assert "或1亿元以下且增速达15%以上" in context


def test_single_condition_prompt_requires_exhaustive_quantitative_extraction():
    detail = {"conditions": [{"applicationRequirement": "18、上年度营业额1000万元以上且同比增长10%以上。"}]}
    prompt = _build_generation_prompt(
        detail,
        requirement_context="[C18]\n上年度营业额1000万元以上且同比增长10%以上。",
        prompt_template="{application_requirements}\n{condition_task_contract}\n{standard_field_reference}",
        field_reference="字段参考",
    )

    assert "本次只处理输入中唯一的 `[C数字]` 原始条件" in prompt
    assert "由“且”连接多个事实时，每个事实分别输出" in prompt
    assert "主体资格、注册属地、行业限制、企业资质" in prompt
    assert "C18-A1" in prompt


def test_prompt_allows_qualitative_eligibility_facts_without_numeric_thresholds():
    prompt = _build_generation_prompt(
        {"conditions": [{"applicationRequirement": "6、企业拥有有效期内高新技术企业资格证书。"}]},
        requirement_context="[C6]\n企业拥有有效期内高新技术企业资格证书。",
        prompt_template=rule_table_generator._load_prompt_template(),
        field_reference="企业资质 → 高新技术企业资格证书",
    )

    assert "也包括“主体/资质/属地/行业/荣誉/机构/信用 + 明确要求”的资格事实" in prompt
    assert "资格事实统一使用 `operator: \"llm_judge\"`" in prompt
    assert "仍进入 `conditionFieldItems` 并标记为 `unresolved/reference`" in prompt


def test_items_must_be_grounded_in_their_numbered_source_requirement():
    detail = {"conditions": [{"applicationRequirement": "2、创新券申领门槛：上一年度营业收入大于2000万元（含）。"}]}
    valid_item = _item("2-1", "营业收入", "上一年度营业收入大于2000万元（含）", target=2000, unit="万元")
    valid_item["sourceFragmentId"] = "C2-A1"
    wrong_number_item = _item("3-1", "营业收入", "上一年度营业收入大于2000万元（含）", target=2000, unit="万元")
    wrong_number_item["sourceFragmentId"] = "C3-A1"
    table = {
        "hardRuleGroups": [_group(2, "上一年度营业收入大于2000万元（含）。", valid_item, wrong_number_item)],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }

    _filter_items_not_grounded_in_requirements(table, detail)

    items = table["hardRuleGroups"][0]["conditionFieldItems"]
    assert [item["itemId"] for item in items] == ["2-1"]
    assert table["hardRuleGroups"][0]["conditionContext"] == "创新券申领门槛"


def test_items_keep_their_original_alternative_branch_context():
    detail = {
        "conditions": [
            {
                "applicationRequirement": (
                    "7、产值门槛：产值3亿元以上且增速8%以上；"
                    "或产值1亿元以下且增速15%以上。"
                )
            }
        ]
    }
    item = _item("7-1", "产值", "产值3亿元以上", target=3, unit="亿元")
    item["sourceFragmentId"] = "C7-A1"
    table = {
        "hardRuleGroups": [_group(7, item["policyFragment"], item)],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }

    _filter_items_not_grounded_in_requirements(table, detail)

    group = table["hardRuleGroups"][0]
    assert group["conditionBranchId"] == "C7-B1"
    assert group["conditionBranchText"] == "产值3亿元以上且增速8%以上"


def test_items_in_one_model_group_keep_separate_branch_contexts_after_routing():
    detail = {
        "conditions": [
            {
                "applicationRequirement": (
                    "7、产值门槛：产值3亿元以上且增速8%以上；"
                    "或产值1亿元以下且增速15%以上。"
                )
            }
        ]
    }
    first_branch_item = _item("7-1", "产值", "产值3亿元以上", target=3, unit="亿元")
    first_branch_item["sourceFragmentId"] = "C7-A1"
    second_branch_item = _item("7-2", "产值", "产值1亿元以下", target=1, unit="亿元")
    second_branch_item["sourceFragmentId"] = "C7-A2"
    table = {
        "hardRuleGroups": [_group(7, "产值门槛", first_branch_item, second_branch_item)],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }

    _filter_items_not_grounded_in_requirements(table, detail)
    _route_rule_groups(table)

    groups = table["softRuleGroups"]
    assert [group["conditionBranchId"] for group in groups] == ["C7-B1", "C7-B2"]
    assert [group["conditionBranchText"] for group in groups] == [
        "产值3亿元以上且增速8%以上",
        "或产值1亿元以下且增速15%以上",
    ]


def test_each_original_condition_is_sent_as_an_independent_parallel_task(monkeypatch):
    detail = {
        "conditions": [
            {
                "applicationRequirement": (
                    "2、上一年度营业收入大于2000万元（含）。"
                    "7、工业总产值3亿元以上且一季度产值增速达8%以上。"
                )
            }
        ]
    }
    labels = []

    def fake_call(prompt, task_label=""):
        labels.append(task_label)
        condition_no = int(task_label.removeprefix("C"))
        item = _item(
            f"{condition_no}-1",
            "测试指标",
            "上一年度营业收入大于2000万元（含）" if condition_no == 2 else "工业总产值3亿元以上",
            target=2000 if condition_no == 2 else 3,
            unit="万元" if condition_no == 2 else "亿元",
        )
        item["sourceFragmentId"] = f"C{condition_no}-A1"
        items = [item]
        if condition_no == 7:
            growth_item = _item("7-2", "一季度产值增速", "一季度产值增速达8%以上", target=8, unit="%")
            growth_item["sourceFragmentId"] = "C7-A2"
            items.append(growth_item)
        return {
            "hardRuleGroups": [_group(condition_no, item["policyFragment"], *items)],
            "softRuleGroups": [],
            "specialPassRuleGroups": [],
            "definitionNotes": [],
            "applicationChecklistGroups": [],
        }

    monkeypatch.setattr(rule_table_generator, "RULE_TABLE_CONDITION_WORKERS", 2)
    monkeypatch.setattr(rule_table_generator, "_call_llm_for_generation", fake_call)

    merged = _generate_rule_table_by_requirement_sections(
        detail,
        "{application_requirements}",
        "",
    )

    assert sorted(labels) == ["C2", "C7"]
    assert [group["conditionNo"] for group in merged["hardRuleGroups"]] == [2, 7]


def test_any_failed_original_condition_stops_the_policy_merge(monkeypatch):
    detail = {"conditions": [{"applicationRequirement": "1、营业收入大于100万元。2、职工人数不少于10人。"}]}

    def fake_call(prompt, task_label=""):
        if task_label == "C2":
            raise RuntimeError("模型超时")
        item = _item("1-1", "营业收入", "营业收入大于100万元", target=100, unit="万元")
        item["sourceFragmentId"] = "C1-A1"
        return {
            "hardRuleGroups": [_group(1, item["policyFragment"], item)],
            "softRuleGroups": [],
            "specialPassRuleGroups": [],
            "definitionNotes": [],
            "applicationChecklistGroups": [],
        }

    monkeypatch.setattr(rule_table_generator, "RULE_TABLE_CONDITION_WORKERS", 2)
    monkeypatch.setattr(rule_table_generator, "_call_llm_for_generation", fake_call)

    try:
        _generate_rule_table_by_requirement_sections(detail, "{application_requirements}", "")
        assert False, "任一原始条件失败时不应合并规则表"
    except RuntimeError as exc:
        assert "不合并、不写回" in str(exc)


def test_double_escaped_unicode_from_model_is_decoded_before_rule_normalization():
    result = _sanitize_llm_json('{"hardRuleGroups":[],"policyDisplayName":"\\\\u53a6\\\\u95e8"}')

    assert result["policyDisplayName"] == "厦门"


def test_multi_escaped_unicode_from_model_does_not_leave_a_backslash():
    result = _sanitize_llm_json(
        r'{"hardRuleGroups":[],"policyDisplayName":"\\\\\\\\u5c5e\\\\\\\\u5730\\\\\\\\u4fe1\\\\\\\\u606f"}'
    )

    assert result["policyDisplayName"] == "属地信息"


def test_qualitative_credit_reference_without_evidence_is_not_extracted():
    source_text = "存在逾期未验收科技计划项目，或有尚在惩戒执行期内的科研失信记录、信用联合惩戒记录的不予受理。"
    table = {
        "hardRuleGroups": [
            _group(7, source_text, _item("7-1", "科研失信限制", source_text, target="无")),
        ],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
        "applicationChecklistGroups": [],
    }
    fields = [
        {"rowNo": 1, "level3Category": "科研信用", "coreField": "科研失信记录"},
        {"rowNo": 2, "level3Category": "企业信用", "coreField": "是否存在失信记录"},
    ]

    _normalize_rule_table_items(table, fields)

    assert table["hardRuleGroups"] == []
    assert table["softRuleGroups"] == []
    assert table["applicationChecklistGroups"] == []


def test_inexact_standard_fields_without_a_matcher_are_skipped():
    verification_text = "技术供给方提供的技术/产品需已通过试验验证、检测认证，具备规模化应用条件"
    shareholding_text = "双方不得互相直接持股25%及以上，或同为第三方直接持股25%及以上"
    table = {
        "hardRuleGroups": [
            _group(
                2,
                f"{verification_text}；{shareholding_text}",
                _item("2-1", "技术交易额", verification_text, target="已通过试验验证、检测认证"),
                _item("2-2", "投资单位", shareholding_text, target="不得直接持股25%及以上"),
            ),
        ],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }
    for item in table["hardRuleGroups"][0]["conditionFieldItems"]:
        item["itemStatus"] = "unresolved"
        item["matchRule"].update({"fieldType": "string", "operator": "llm_judge"})

    fields = [
        {"rowNo": 1, "level3Category": "技术交易额", "coreField": "技术合同成交额"},
        {"rowNo": 2, "level3Category": "投资单位", "coreField": "投资单位名称、投资金额、股份占比"},
    ]
    _normalize_rule_table_items(table, fields)

    assert table["hardRuleGroups"] == []
    assert [group["evaluationRole"] for group in table["softRuleGroups"]] == [
        "eligibility_signal", "eligibility_signal",
    ]
    items = [group["conditionFieldItems"][0] for group in table["softRuleGroups"]]
    assert all(item["itemStatus"] == "unresolved" for item in items)
    assert all(item["matchRule"]["ruleKind"] == "reference" for item in items)


def test_subject_qualification_without_a_matcher_is_skipped():
    source_text = "牵头方须为在厦实际经营的主体，无知识产权纠纷，且为技术产品权属单位。"
    table = {
        "hardRuleGroups": [_group(1, source_text, _item("1-1", "主体资格", source_text, target="符合"))],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }
    table["hardRuleGroups"][0]["conditionFieldItems"][0]["matchRule"].update(
        {"fieldType": "string", "operator": "llm_judge"}
    )
    fields = [{"rowNo": 1, "level3Category": "主体资格", "coreField": "独立法人资格、主体资格"}]

    _normalize_rule_table_items(table, fields)

    assert table["hardRuleGroups"] == []
    assert len(table["softRuleGroups"]) == 1
    item = table["softRuleGroups"][0]["conditionFieldItems"][0]
    assert item["itemStatus"] == "unresolved"
    assert item["matchRule"]["operator"] == "llm_judge"


def test_hard_exclusion_is_removed_from_original_application_checklist():
    exclusion_text = "存在尚在惩戒执行期内的科研失信记录、信用联合惩戒记录的不予受理。"
    table = {
        "hardRuleGroups": [],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
        "applicationChecklistGroups": [
            {"checklistNo": 5, "checklistType": "承诺", "rawText": exclusion_text, "items": ["科研失信记录"]},
        ],
    }

    _route_rule_groups(table)

    assert table["applicationChecklistGroups"] == []


def test_unmapped_exclusion_reference_without_evidence_is_not_extracted():
    source_text = "近两年连续获得重大科技项目立项或已有两项在研重大科技项目的不予受理。"
    table = {
        "hardRuleGroups": [_group(3, source_text, _item("3-1", "项目立项限制", source_text, target="不予受理"))],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }

    _normalize_rule_table_items(table, _fields())

    assert table["hardRuleGroups"] == []
    assert table["softRuleGroups"] == []


def test_missing_exclusion_reference_is_routed_as_non_hard_signal():
    source_text = "涉及人体研究的须通过伦理审查；存在逾期未验收科技计划项目，或有科研失信记录、信用联合惩戒记录的不予受理。"
    table = {
        "hardRuleGroups": [],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
        "applicationChecklistGroups": [
            {"checklistNo": 7, "checklistType": "承诺", "rawText": source_text, "items": ["科研失信记录"]},
        ],
    }
    detail = {"conditions": [{"applicationRequirement": f"7、{source_text}"}]}

    _ensure_hard_exclusion_coverage(table, detail)

    assert table["hardRuleGroups"] == []
    assert [group["conditionNo"] for group in table["softRuleGroups"]] == [7, 7]
    items = [group["conditionFieldItems"][0] for group in table["softRuleGroups"]]
    assert [item["itemName"] for item in items] == ["伦理合规审查", "科研信用与项目验收限制"]
    assert all(item["matchRule"]["ruleKind"] == "reference" for item in items)
    assert table["applicationChecklistGroups"] == []


def test_qualitative_reference_groups_without_a_matcher_are_skipped():
    table = {
        "hardRuleGroups": [
            _group(1, "牵头方须为在厦实际经营主体，无知识产权纠纷", _item("1-1", "主体资格", "牵头方须为在厦实际经营主体", target="符合")),
            _group(2, "技术产品需通过检测认证", _item("2-1", "技术验证", "技术产品需通过检测认证", target="通过")),
        ],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }
    for item in (group["conditionFieldItems"][0] for group in table["hardRuleGroups"]):
        item["itemStatus"] = "unresolved"
        item["matchRule"].update({"fieldType": "string", "operator": "llm_judge", "ruleKind": "reference"})

    _normalize_rule_table_items(table, _fields())

    assert table["hardRuleGroups"] == []
    assert len(table["softRuleGroups"]) == 2
    assert all(group["evaluationRole"] == "eligibility_signal" for group in table["softRuleGroups"])


def test_financing_ratio_keeps_the_denominator_in_its_unit():
    table = {
        "hardRuleGroups": [],
        "softRuleGroups": [
            _group(
                3,
                "自筹经费比例不低于2:1。",
                _item("3-2", "自筹经费比例", "自筹经费比例不低于2:1。", target=2, unit="1"),
            )
        ],
        "specialPassRuleGroups": [],
    }

    _normalize_financing_ratio_rules(table)

    match_rule = table["softRuleGroups"][0]["conditionFieldItems"][0]["matchRule"]
    assert match_rule["targetValue"] == 2
    assert match_rule["unit"] == ":1"
    assert match_rule["operator"] == "gte"


def test_funding_ratio_string_is_normalized_to_numeric_rule():
    item = _item(
        "3-2",
        "自筹资金与资助金额比例",
        "申报企业自筹资金与资助金额比例不少于2:1",
        target="自筹资金与资助金额比例不少于2:1",
    )
    item["itemStatus"] = "unresolved"
    item["matchRule"].update({"fieldType": "string", "operator": "llm_judge", "ruleKind": "reference"})
    table = {
        "hardRuleGroups": [],
        "softRuleGroups": [_group(3, item["policyFragment"], item)],
        "specialPassRuleGroups": [],
    }

    _normalize_rule_table_items(table, _fields())

    rule = table["softRuleGroups"][0]["conditionFieldItems"][0]["matchRule"]
    assert rule["fieldType"] == "number"
    assert rule["operator"] == "gte"
    assert rule["targetValue"] == 2
    assert rule["unit"] == ":1"


def test_inclusive_strict_wording_is_normalized_to_inclusive_operator():
    item = _item("8-1", "工业总产值增速", "同比增速高于10%（含）", target=10, unit="%")
    table = {
        "hardRuleGroups": [],
        "softRuleGroups": [_group(8, item["policyFragment"], item)],
        "specialPassRuleGroups": [],
    }

    _normalize_rule_table_items(table, _fields())

    rule = table["softRuleGroups"][0]["conditionFieldItems"][0]["matchRule"]
    assert rule["operator"] == "gte"


def test_nonzero_score_threshold_is_kept_as_strict_lower_bound():
    item = _item("5-2", "科技人员指标得分", "科技人员指标得分不得为0分", target=0, unit="分")
    item["matchRule"]["operator"] = "gt"
    table = {
        "hardRuleGroups": [],
        "softRuleGroups": [_group(5, item["policyFragment"], item)],
        "specialPassRuleGroups": [],
    }

    _normalize_rule_table_items(table, _fields())

    rule = table["softRuleGroups"][0]["conditionFieldItems"][0]["matchRule"]
    assert rule["operator"] == "gt"
    assert rule["targetValue"] == 0


def test_missing_second_numeric_threshold_triggers_quantitative_retry(monkeypatch):
    detail = {
        "conditions": [{
            "applicationRequirement": "5、综合评价所得分值不低于60分，且科技人员指标得分不得为0分。"
        }]
    }
    labels = []

    def fake_call(prompt, task_label=""):
        labels.append(task_label)
        total = _item("5-1", "综合评价得分", "综合评价所得分值不低于60分", target=60, unit="分")
        total["sourceFragmentId"] = "C5-A1"
        items = [total]
        if task_label.endswith("quantitative-retry"):
            personnel = _item("5-2", "科技人员指标得分", "科技人员指标得分不得为0分", target=0, unit="分")
            personnel["sourceFragmentId"] = "C5-A2"
            personnel["matchRule"]["operator"] = "gt"
            items.append(personnel)
        return {
            "hardRuleGroups": [_group(5, detail["conditions"][0]["applicationRequirement"], *items)],
            "softRuleGroups": [],
            "specialPassRuleGroups": [],
            "definitionNotes": [],
            "applicationChecklistGroups": [],
        }

    monkeypatch.setattr(rule_table_generator, "_call_llm_for_generation", fake_call)

    merged = _generate_rule_table_by_requirement_sections(detail, "{application_requirements}", "")

    assert labels == ["C5", "C5-quantitative-retry"]
    assert sum(len(group["conditionFieldItems"]) for group in merged["hardRuleGroups"]) == 3


def test_ip_type_enumeration_and_generic_legal_status_are_not_scoring_rules():
    source_text = (
        "项目的核心技术拥有受中国法律保护的知识产权。知识产权的类型包括："
        "近3年内获得授权的发明专利、登记的集成电路布图设计专有权；"
        "近2年内获得授权的实用新型专利、登记的软件著作权。"
    )
    table = {
        "hardRuleGroups": [],
        "softRuleGroups": [
            _group(
                4,
                source_text,
                _item("4-1", "知识产权", "核心技术拥有受中国法律保护的知识产权", target="符合"),
                _item("4-2", "知识产权", "近3年内获得授权的发明专利", target="符合"),
                _item("4-3", "知识产权", "登记的集成电路布图设计专有权", target="符合"),
                _item("4-4", "知识产权", "近2年内获得授权的实用新型专利", target="符合"),
                _item("4-5", "知识产权", "登记的软件著作权", target="符合"),
            )
        ],
        "specialPassRuleGroups": [],
    }

    _normalize_rule_table_items(table, _fields())

    items = [
        item
        for group_key in ("hardRuleGroups", "softRuleGroups", "specialPassRuleGroups")
        for group in table[group_key]
        for item in group["conditionFieldItems"]
    ]
    assert items == []


def test_unsupported_qualitative_assertions_are_not_scoring_rules():
    patent = _item("1-1", "专利", "核心技术拥有受中国法律保护的知识产权", target="有效")
    patent["matchRule"].update({"fieldType": "string", "operator": "llm_judge"})
    compliance = _item("1-2", "产品合规", "产品应通过国家认证认可检测机构的检测（评测）", target="通过")
    compliance["matchRule"].update({"fieldType": "string", "operator": "llm_judge"})
    high_tech = _item("1-3", "高新技术领域", "核心技术属于《国家重点支持的高新技术领域》规定的范围", target="符合")
    high_tech["matchRule"].update({"fieldType": "string", "operator": "llm_judge"})
    revenue = _item("1-4", "核心产品收入占比", "主要产品收入之和超过50%", target=50, unit="%")
    table = {
        "hardRuleGroups": [],
        "softRuleGroups": [_group(1, "测试条件", patent, compliance, high_tech, revenue)],
        "specialPassRuleGroups": [],
    }

    _normalize_rule_table_items(table, _fields())

    items = [
        item
        for group_key in ("hardRuleGroups", "softRuleGroups", "specialPassRuleGroups")
        for group in table[group_key]
        for item in group["conditionFieldItems"]
    ]
    assert [item["itemId"] for item in items] == ["1-1", "1-2", "1-3", "1-4"]
    assert all(item["itemStatus"] == "unresolved" for item in items[:3])


def test_growth_metrics_without_an_original_numeric_threshold_are_not_rules():
    generic_research_intensity = _item(
        "1-1",
        "研发投入强度",
        "企业研发投入强度符合要求",
        target="符合要求",
    )
    generic_core_revenue = _item(
        "1-2",
        "核心产品收入占比",
        "核心产品收入占比满足有关标准",
        target="符合要求",
    )
    actual_core_revenue = _item(
        "1-3",
        "核心产品收入占比",
        "主要产品（服务）收入之和超过50%",
        target=50,
        unit="%",
    )
    table = {
        "hardRuleGroups": [],
        "softRuleGroups": [
            _group(
                1,
                "主要产品（服务）收入之和超过50%",
                generic_research_intensity,
                generic_core_revenue,
                actual_core_revenue,
            )
        ],
        "specialPassRuleGroups": [],
    }

    _normalize_rule_table_items(table, _fields())

    items = [group["conditionFieldItems"][0] for group in table["softRuleGroups"]]
    assert [item["itemId"] for item in items] == ["1-3"]


def test_specific_registration_location_and_establishment_time_are_retained():
    registration_location = _item(
        "1-1",
        "注册地",
        "申报单位注册地应在厦门市行政区域内",
        target="厦门市",
    )
    registration_location["matchRule"].update({"fieldType": "string", "operator": "llm_judge", "ruleKind": "reference"})
    establishment_time = _item(
        "2-1",
        "成立时间",
        "企业应于2023年1月1日后注册成立",
        target="2023年1月1日",
    )
    establishment_time["matchRule"].update({"fieldType": "string", "operator": "llm_judge", "ruleKind": "reference"})
    fields = [
        {"rowNo": 1, "level3Category": "注册地址", "coreField": "营业期限、设立地"},
        {"rowNo": 2, "level3Category": "成立年限", "coreField": "成立年限、经营年限"},
    ]
    table = {
        "hardRuleGroups": [_group(1, registration_location["policyFragment"], registration_location)],
        "softRuleGroups": [_group(2, establishment_time["policyFragment"], establishment_time)],
        "specialPassRuleGroups": [],
    }

    _normalize_rule_table_items(table, fields)

    assert table["hardRuleGroups"] == []
    items = [
        item
        for group in table["softRuleGroups"]
        for item in group["conditionFieldItems"]
    ]
    assert [item["sourceStandardField"]["level3Category"] for item in items] == ["注册地址", "成立年限"]


def test_personal_salary_threshold_is_not_mapped_to_enterprise_revenue():
    salary = _item(
        "4-1",
        "营收规模",
        "申报人才上一年度在申报单位的工资薪金总额不低于30万元",
        target=30,
        unit="万元",
    )
    salary["enterpriseFields"] = ["年营业收入"]
    salary["sourceStandardField"] = {
        "rowNo": 1,
        "level3Category": "营收规模",
        "coreField": "年营业收入、核心产品销售收入",
    }
    table = {
        "hardRuleGroups": [],
        "softRuleGroups": [_group(4, salary["policyFragment"], salary)],
        "specialPassRuleGroups": [],
    }
    fields = [{"rowNo": 1, "level3Category": "营收规模", "coreField": "年营业收入、核心产品销售收入"}]

    _normalize_rule_table_items(table, fields)

    assert table["hardRuleGroups"] == []
    item = table["softRuleGroups"][0]["conditionFieldItems"][0]
    assert item["itemName"] == "工资薪金总额"
    assert item["enterpriseFields"] == ["工资薪金总额"]
    assert item["sourceStandardField"] is None
    assert item["itemStatus"] == "unresolved"
    assert item["matchRule"]["ruleKind"] == "reference"
    assert item["matchRule"]["fieldType"] == "number"
    assert item["matchRule"]["targetValue"] == 30


def test_alternative_talent_salary_thresholds_are_independent_soft_signals():
    b_salary = _item(
        "6-1",
        "年薪",
        "世界500强等二级部门负责人累计3年以上且年薪达社平工资4倍以上",
        target=4,
        unit="倍",
    )
    c_salary = _item(
        "7-1",
        "年薪",
        "世界500强等从业累计8年以上或作为骨干引进且年薪达社平工资3倍以上",
        target=3,
        unit="倍",
    )
    table = {
        "hardRuleGroups": [
            _group(6, "申报B类核心人才须满足世界500强等二级部门负责人累计3年以上且年薪达社平工资4倍以上等条件之一", b_salary),
            _group(7, "申报C类专业人才须满足世界500强等从业累计8年以上或作为骨干引进且年薪达社平工资3倍以上等条件之一", c_salary),
        ],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }

    _normalize_rule_table_items(table, _fields())

    assert table["hardRuleGroups"] == []
    assert [group["conditionOperator"] for group in table["softRuleGroups"]] == ["AND", "AND"]
    assert [group["evaluationRole"] for group in table["softRuleGroups"]] == [
        "quantitative_signal", "quantitative_signal",
    ]
    items = [group["conditionFieldItems"][0] for group in table["softRuleGroups"]]
    assert [item["itemName"] for item in items] == ["B类核心人才年薪门槛", "C类专业人才年薪门槛"]
    assert [item["matchRule"]["targetValue"] for item in items] == [4, 3]


def test_unknown_quantitative_field_is_retained_with_its_original_name():
    equipment_value = _item(
        "1-1",
        "设备原值",
        "购置的科研仪器设备原值不低于80万元",
        target=80,
        unit="万元",
    )
    table = {
        "hardRuleGroups": [_group(1, equipment_value["policyFragment"], equipment_value)],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }
    unrelated_fields = [{"rowNo": 1, "level3Category": "营收规模", "coreField": "年营业收入"}]

    _normalize_rule_table_items(table, unrelated_fields)

    assert table["hardRuleGroups"] == []
    item = table["softRuleGroups"][0]["conditionFieldItems"][0]
    assert item["itemName"] == "设备原值"
    assert item["sourceStandardField"] is None
    assert item["itemStatus"] == "unresolved"
    assert item["matchRule"]["ruleKind"] == "reference"
    assert item["matchRule"]["fieldType"] == "number"
    assert item["matchRule"]["targetValue"] == 80


def test_incompatible_revenue_mapping_uses_original_quantitative_field_names():
    industrial_output = _item(
        "7-1",
        "工业总产值",
        "2024年度工业总产值3亿元（含）以上",
        target=3,
        unit="亿元",
    )
    industrial_growth = _item(
        "7-2",
        "一季度工业产值增长率",
        "一季度产值增速达8%（含）以上",
        target=8,
        unit="%",
    )
    building_area = _item(
        "22-1",
        "建筑面积",
        "建筑面积3万平方米以上",
        target=3,
        unit="万平方米",
    )
    attendees = _item(
        "24-1",
        "参会人数",
        "参会人数100人（含）以上",
        target=100,
        unit="人",
    )
    table = {
        "hardRuleGroups": [
            _group(7, "工业产值门槛", industrial_output, industrial_growth),
            _group(22, "商圈发展门槛", building_area),
            _group(24, "会议引进门槛", attendees),
        ],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }
    fields = [
        {"rowNo": 1, "level3Category": "营收规模", "coreField": "年营业收入、销售收入"},
        {"rowNo": 2, "level3Category": "营收增长率", "coreField": "营业收入同比增长率"},
    ]

    _normalize_rule_table_items(table, fields)

    assert table["hardRuleGroups"] == []
    items = [group["conditionFieldItems"][0] for group in table["softRuleGroups"]]
    assert [item["itemName"] for item in items] == [
        "工业总产值",
        "一季度工业产值增长率",
        "建筑面积",
        "参会人数",
    ]
    assert all(item["itemStatus"] == "unresolved" for item in items)
    assert all(item["sourceStandardField"] is None for item in items)
    assert [item["matchRule"]["unit"] for item in items] == ["亿元", "%", "万平方米", "人"]
    assert all(item["matchRule"]["fieldType"] == "number" for item in items)
    assert all(item["matchRule"]["ruleKind"] == "reference" for item in items)


def test_ambiguous_ranges_and_enumerations_are_not_numeric_rules():
    range_item = _item("7-3", "营收规模", "1亿至3亿元以下", target=3, unit="亿元")
    fabricated_institution_count = _item(
        "24-1",
        "营收规模",
        "实行独立核算的金融机构总部一级分支机构",
        target=1,
        unit="个",
    )
    tiered_scale = _item("26-1", "营收规模", "在管规模10亿/20亿/30亿等", target=10, unit="亿元")
    table = {
        "hardRuleGroups": [
            _group(7, "工业总产值3亿元以上；或1亿至3亿元以下且增速达10%以上", range_item),
            _group(24, "金融机构门槛", fabricated_institution_count),
            _group(26, "私募投资门槛：备案机构（在管规模10亿/20亿/30亿等）", tiered_scale),
        ],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }

    _normalize_rule_table_items(table, _fields())

    assert table["hardRuleGroups"] == []
    assert table["softRuleGroups"] == []


def test_omitted_subject_uses_full_requirement_context_not_revenue_growth():
    industrial_growth = _item("7-4", "一季度工业产值增长率", "增速达10%（含）以上", target=10, unit="%")
    turnover_growth = _item("18-2", "营业额同比增长率", "当年度营业额同比增长10%以上", target=10, unit="%")
    table = {
        "hardRuleGroups": [
            _group(7, "2024年度工业总产值3亿元以上且一季度产值增速达8%以上；或1亿至3亿元以下且增速达10%（含）以上", industrial_growth),
            _group(18, "上年度营业额1000万元以上，当年度营业额同比增长10%以上", turnover_growth),
        ],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }
    fields = [
        {"rowNo": 1, "level3Category": "营收增长率", "coreField": "营业收入同比增长率"},
    ]

    _normalize_rule_table_items(table, fields)

    assert table["hardRuleGroups"] == []
    items = [group["conditionFieldItems"][0] for group in table["softRuleGroups"]]
    assert [item["itemName"] for item in items] == ["一季度工业产值增长率", "营业额同比增长率"]
    assert all(item["itemStatus"] == "unresolved" for item in items)
    assert all(item["sourceStandardField"] is None for item in items)


def test_wrong_generic_standard_name_is_rejected_instead_of_being_reinterpreted():
    wrong_item = _item("22-1", "营收规模", "建筑面积3万平方米以上", target=3, unit="万平方米")
    table = {
        "hardRuleGroups": [_group(22, "商圈发展门槛", wrong_item)],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }
    fields = [{"rowNo": 1, "level3Category": "营收规模", "coreField": "年营业收入、销售收入"}]

    _normalize_rule_table_items(table, fields)

    assert table["hardRuleGroups"] == []
    assert table["softRuleGroups"] == []


def test_non_matchable_qualitative_compliance_items_are_not_extracted_or_restored():
    source_text = "企业未发生重大安全、重大质量事故或严重环境违法、科研失信行为，且未列入严重违法失信名单。"
    blacklist = _item("1-1", "企业信用", "企业未列入严重违法失信名单", target="否")
    blacklist["matchRule"].update({"fieldType": "string", "operator": "llm_judge"})
    safety = _item("1-2", "安全生产", "未发生重大安全事故", target="否")
    safety["matchRule"].update({"fieldType": "string", "operator": "llm_judge"})
    quality = _item("1-3", "质量合规", "未发生重大质量事故", target="否")
    quality["matchRule"].update({"fieldType": "string", "operator": "llm_judge"})
    table = {
        "hardRuleGroups": [_group(1, source_text, blacklist, safety, quality)],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
    }

    _normalize_rule_table_items(table, _fields())
    _ensure_hard_exclusion_coverage(table, {"conditions": [{"applicationRequirement": f"1、{source_text}"}]})

    assert table["hardRuleGroups"] == []
    assert len(table["softRuleGroups"]) == 3
    assert all(group["evaluationRole"] == "eligibility_signal" for group in table["softRuleGroups"])


def test_duplicate_checklists_with_same_source_text_are_collapsed():
    source_text = "需提交项目可行性研究报告和审计报告。"
    table = {
        "hardRuleGroups": [],
        "softRuleGroups": [],
        "specialPassRuleGroups": [],
        "applicationChecklistGroups": [
            {"checklistNo": 1, "checklistType": "材料", "rawText": source_text, "items": ["项目可行性研究报告"]},
            {"checklistNo": 4, "checklistType": "中介机构", "rawText": source_text, "items": ["审计报告"]},
        ],
    }

    _route_rule_groups(table)

    assert table["applicationChecklistGroups"] == [
        {"checklistNo": 1, "checklistType": "材料", "rawText": source_text, "items": ["项目可行性研究报告"]},
    ]
