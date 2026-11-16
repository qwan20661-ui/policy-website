"""企业匹配提示词的结构与运行时契约测试。"""

from pathlib import Path

from service.rule_engine_service import _build_rule_item_judge_prompt


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _read_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def test_semantic_system_prompt_uses_layered_contract_and_keeps_context_last():
    prompt = _read_prompt("semantic_policy_match_system_prompt.md")

    headings = [
        "# 角色与任务",
        "# 核心原则",
        "# 内部执行流程",
        "# 要求与限制",
        "# 输出规范",
        "# 判定示例",
        "# 输入上下文",
    ]
    positions = [prompt.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert prompt.count("**证据闭环**") == 1
    assert "不要输出分析过程" in prompt
    assert "semanticItemKey" in prompt
    assert "branchApplicability" in prompt
    assert "研发人员数量或占比不能代替大专以上学历员工数量或占比" in prompt


def test_rule_item_prompt_keeps_all_placeholders_and_renders_context_at_end():
    template = _read_prompt("rule_item_judge_user_prompt.md")
    placeholders = {
        "__GROUP_RAW_TEXT__",
        "__CONDITION_NO__",
        "__ITEM_NAME__",
        "__POLICY_FRAGMENT__",
        "__ITEM_STATUS__",
        "__RULE_KIND__",
        "__OPERATOR__",
        "__FIELD_TYPE__",
        "__TARGET_VALUE__",
        "__RULE_DESCRIPTION__",
        "__ENTERPRISE_FIELDS__",
        "__ITEM_SPECIFIC_GUIDANCE__",
        "__ENTERPRISE_CONTEXT_JSON__",
    }
    assert all(placeholder in template for placeholder in placeholders)
    assert template.index("# 输出规范") < template.index("# 输入上下文")

    prompt = _build_rule_item_judge_prompt(
        {"企业名称": "测试企业", "营收规模": 1200},
        {
            "itemName": "营业收入",
            "policyFragment": "营业收入不低于1000万元",
            "itemStatus": "rule",
            "enterpriseFields": ["营收规模"],
            "matchRule": {
                "ruleKind": "direct",
                "operator": "gte",
                "fieldType": "number",
                "description": "营业收入门槛",
            },
        },
        {"conditionNo": 1, "rawText": "营业收入不低于1000万元"},
        [1200],
        1000,
    )

    assert not any(placeholder in prompt for placeholder in placeholders)
    assert prompt.rstrip().endswith("}")
    assert prompt.index("# 输入上下文") < prompt.index('"企业名称": "测试企业"')
