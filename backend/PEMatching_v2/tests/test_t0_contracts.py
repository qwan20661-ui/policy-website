# -*- coding: utf-8 -*-
"""T0 契约、离线夹具与基线测试。"""

from __future__ import annotations

import json
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = PACKAGE_ROOT / "schemas"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "t0"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_T0_T2四份JSON_Schema固定且不含旧150分口径():
    schema_names = (
        "policy_condition_package.schema.json",
        "policy_condition_match_input.schema.json",
        "policy_condition_match_output.schema.json",
        "policy_match_response.schema.json",
    )

    schemas = [_load_json(SCHEMA_DIR / name) for name in schema_names]

    assert all(schema["$schema"].endswith("2020-12/schema") for schema in schemas)
    assert all(schema["additionalProperties"] is False for schema in schemas)
    assert "150" not in json.dumps(schemas, ensure_ascii=False)


def test_模型输出Schema固定状态枚举和百分制范围():
    schema = _load_json(SCHEMA_DIR / "policy_condition_match_output.schema.json")
    condition = schema["$defs"]["ruleConditionResult"]["properties"]
    discovered = schema["$defs"]["discoveredConditionResult"]["properties"]

    assert condition["status"]["enum"] == [
        "satisfied",
        "partially_satisfied",
        "not_satisfied",
        "missing",
    ]
    assert condition["score"]["minimum"] == 0
    assert condition["score"]["maximum"] == 100
    assert discovered["groupOperator"]["enum"] == ["AND", "OR"]
    assert condition["evidencePaths"]["items"]["pattern"] == "^\\$"


def test_代表性政策夹具包含原文和规则表():
    for policy_id in ("6451", "6509", "6534"):
        payload = _load_json(FIXTURE_DIR / "policies" / f"{policy_id}.json")
        detail = payload["data"]

        assert payload["success"] is True
        assert str(detail["id"]) == policy_id
        assert detail["conditions"][0]["applicationRequirement"]
        assert isinstance(detail["ruleTableJson"], dict)


def test_企业夹具覆盖普通企业高企和专精特新():
    ordinary = _load_json(FIXTURE_DIR / "enterprises" / "普通企业.json")
    high_tech = _load_json(FIXTURE_DIR / "enterprises" / "高企.json")
    specialized = _load_json(FIXTURE_DIR / "enterprises" / "专精特新.json")

    assert ordinary["honorQualificationList"][0]["isHighTechEnterprise"] is None
    assert high_tech["honorQualificationList"][0]["isHighTechEnterprise"] == "是"
    assert specialized["honorQualificationList"][0]["isSpecializedSme"] == "是"


def test_基线同时记录需求确认值和接口实测值():
    baseline = _load_json(FIXTURE_DIR / "baseline.json")

    assert baseline["requirementBaseline"]["structureReviewApprovedPolicyCount"] == 78
    assert baseline["requirementBaseline"]["ruleItemCount"] == 220
    assert baseline["observedApiBaseline"]["reviewedPolicyCount"] > 0
    assert baseline["performanceBaseline"]["singlePolicyLlmAverageSeconds"] == 12
    assert baseline["performanceBaseline"]["legacyDeterministicBatch"]["enterpriseCount"] == 100
