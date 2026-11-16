# -*- coding: utf-8 -*-
"""T5 单政策模型契约与失败重试测试，不访问真实模型。"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from service.condition_scoring import condition_catalog_for_model
from service.paths import POLICY_CONDITION_MATCH_PROMPT_PATH
from service.policy_match_model import (
    PROMPT_VERSION,
    PolicyMatchModelError,
    build_model_input,
    judge_policy,
    validate_model_output,
    model_raw_facts,
)


def _profile():
    return {
        "metrics": {"revenueWan": 2000}, "qualifications": {}, "registerInfo": {},
        "industryInfo": {}, "credit": {}, "eligibilityFacts": {}, "_rawFactIndex": {"facts": []},
    }


def _package():
    return {
        "schemaVersion": "policy-condition-package-v1", "fusionPolicyId": "P1", "sourcePolicyId": "S1",
        "title": "测试政策", "policyInfo": {"policyTitle": "测试政策"},
        "conditions": [{"applicationRequirement": "营业收入不低于100万元"}],
        "ruleTableJson": {"hardRuleGroups": [], "softRuleGroups": [{
            "conditionNo": 1, "conditionOperator": "AND", "conditionFieldItems": [{
                "itemId": "1", "itemName": "营业收入", "policyFragment": "营业收入不低于100万元",
                "enterpriseFields": ["营业收入"],
                "matchRule": {"ruleId": "S1", "operator": "gte", "targetValue": 100, "unit": "万元"},
            }],
        }], "specialPassRuleGroups": []},
        "supportMethods": [], "keywords": [], "documents": [], "relatedPolicies": {},
        "policyContentHash": "a" * 64, "qualityFlags": {}, "builderVersion": "t2-v1", "conditionPackageHash": "b" * 64,
    }


def _output(package):
    descriptor = condition_catalog_for_model(package)[0]
    return {
        "schemaVersion": "policy-condition-match-output-v2",
        "sourceConflicts": [], "discoveredConditions": [],
        "conditionResults": [{
            "conditionKey": descriptor,
            "status": "satisfied", "failureEffect": "none", "score": 100,
            "evidencePaths": ["$.metrics.revenueWan"],
            "reason": "达到门槛", "confidence": "high",
        }],
    }


def test_模型输出只回填条件键且禁止额外字段():
    package = _package()
    output = _output(package)

    assert validate_model_output(output, package)["schemaVersion"] == "policy-condition-match-output-v2"

    output["conditionResults"][0]["sourceText"] = "不应重复回填"
    with pytest.raises(PolicyMatchModelError, match="额外"):
        validate_model_output(output, package)


def test_模型只能引用本次输入包含的证据路径():
    package = _package()
    output = _output(package)

    with pytest.raises(PolicyMatchModelError, match="输入中不存在"):
        validate_model_output(output, package, allowed_evidence_paths={"$.metrics.employeeCount"})


def test_模型输入删除完整条件包和重复画像字段():
    model_input = build_model_input(_package(), _profile())

    assert model_input["schemaVersion"] == "policy-condition-match-input-v2"
    assert set(model_input["enterprise"]) == {"facts"}
    assert "supportMethods" not in model_input["policy"]
    assert "documents" not in model_input["policy"]
    assert "conditionPackageHash" not in model_input["policy"]
    assert set(model_input["evaluationRules"]) == {
        "missingScore", "minimumThresholdFormula", "conditionKeys",
    }
    assert model_input["enterprise"]["facts"] == [{
        "path": "$.metrics.revenueWan", "value": 2000,
        "label": "企业字段 $.metrics.revenueWan",
    }]


def test_规则表优先提示词版本已升级():
    prompt = POLICY_CONDITION_MATCH_PROMPT_PATH.read_text(encoding="utf-8")

    assert PROMPT_VERSION == "policy-condition-match-prompt-v3"
    assert f"`{PROMPT_VERSION}`" in prompt
    assert "evaluationRules.conditionKeys` 非空时" in prompt
    assert "discoveredConditions` 必须为空" in prompt


def test_标准化失败时保留原始业务事实避免数据丢失():
    profile = _profile()
    profile["metrics"]["revenueWan"] = None
    profile["_rawFactIndex"]["facts"] = [{
        "fieldName": "营业收入",
        "sourcePath": "$.operationInfoList[0].operatingIncome",
        "value": "待核实",
    }]

    assert model_raw_facts(profile) == [{
        "path": "$.operationInfoList[0].operatingIncome",
        "value": "待核实",
        "label": "营业收入",
    }]


def test_非法JSON输出重试一次后成功():
    package = _package()
    good = _output(package)
    responses = [{"content": "not-json"}, {"content": json.dumps(good, ensure_ascii=False)}]

    with patch("service.policy_match_model.chat", side_effect=responses) as mocked:
        result = judge_policy(package, _profile())

    assert result["retryCount"] == 1
    assert mocked.call_count == 2
    assert result["output"] == good
