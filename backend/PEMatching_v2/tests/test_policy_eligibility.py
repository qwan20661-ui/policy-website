# -*- coding: utf-8 -*-
"""逐政策资格评估测试。"""

from __future__ import annotations

import json

from service import policy_eligibility as pe


def _install(monkeypatch, tmp_path, policies):
    path = tmp_path / "policy_eligibility_rules.json"
    path.write_text(json.dumps({"schemaVersion": pe.SCHEMA, "policies": policies}), encoding="utf-8")
    monkeypatch.setattr(pe, "POLICY_ELIGIBILITY_RULES_PATH", path)
    pe.reset_cache()


def test_所有完整硬条件通过才能严格符合(tmp_path, monkeypatch):
    _install(monkeypatch, tmp_path, {"P1": {
        "coverage": "complete",
        "hardConditions": [{"ruleId": "employee", "label": "人数", "field": "metrics.employeeCount", "operator": "lte", "value": 500}],
    }})

    result = pe.evaluate_policy_eligibility("P1", {"metrics": {"employeeCount": 100}})

    assert result["strictEligibility"] == "eligible"
    assert result["directPushAllowed"] is True


def test_缺企业字段为unknown而非通过(tmp_path, monkeypatch):
    _install(monkeypatch, tmp_path, {"P1": {
        "coverage": "complete",
        "hardConditions": [{"ruleId": "employee", "label": "人数", "field": "metrics.employeeCount", "operator": "lte", "value": 500}],
    }})

    result = pe.evaluate_policy_eligibility("P1", {"metrics": {}})

    assert result["strictEligibility"] == "unknown"
    assert result["directPushAllowed"] is False
    assert result["conditionResults"][0]["result"] == "unknown"


def test_6548已获资质走维护而非重复申报():
    pe.reset_cache()
    result = pe.evaluate_policy_eligibility("6548", {
        "qualifications": {"isTechSme": True},
        "metrics": {"employeeCount": 10, "revenueWan": 100, "totalAssetsWan": 100},
    })

    assert result["strictEligibility"] == "not_applicable"
    assert result["action"] == "maintain"
    assert result["directPushAllowed"] is False


def test_6590原文冲突时拒绝资格结论():
    pe.reset_cache()
    result = pe.evaluate_policy_eligibility("6590", {})

    assert result["status"] == "source_conflict"
    assert result["strictEligibility"] == "not_evaluated"
