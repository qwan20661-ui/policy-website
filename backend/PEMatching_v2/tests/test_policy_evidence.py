# -*- coding: utf-8 -*-
"""政策级证据与直推保护测试。"""

from __future__ import annotations

from service.policy_evidence import assess_recommendation_safety, rule_evidence_for_policy


def _policy(*, d_level="D2", keywords=None):
    return {
        "title": "厦门市联合资助项目申报",
        "tags": {"D": {"value": d_level}},
        "keywordIndex": {"title": keywords or ["联合资助", "项目申报"], "aKeywords": [], "cKeywords": []},
    }


def _rule(rule_id="R15", keywords=None):
    return {"ruleId": rule_id, "signals": {"keywords": keywords or ["增信基金", "创新券"]}}


def test_A3标签相同但关键词无交集不构成政策背书():
    assert rule_evidence_for_policy([_rule()], _policy()) == []


def test_规则关键词与政策关键词直接交集才构成背书():
    evidence = rule_evidence_for_policy([_rule("R09", ["软件专项", "软件"])], _policy(keywords=["软件项目", "项目申报"]))

    assert evidence == [{
        "ruleId": "R09",
        "matchedKeywords": ["软件"],
        "basis": "规则信号关键词与政策关键词存在直接交集",
    }]


def test_无资格规则时有政策证据也不能绕过逐条件资格评估直推():
    safety = assess_recommendation_safety("不存在", _policy(d_level="D3"), [{"ruleId": "R09"}], {})

    assert safety["status"] == "needs_requirement_evaluation"
    assert safety["strictEligibility"] == "not_evaluated"
    assert safety["directPushAllowed"] is False


def test_无政策级证据不能直推():
    safety = assess_recommendation_safety("不存在", _policy(d_level="D1"), [], {})

    assert safety["status"] == "needs_policy_evidence"
    assert safety["directPushAllowed"] is False
