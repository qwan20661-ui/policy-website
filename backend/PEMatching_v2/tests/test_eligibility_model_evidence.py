# -*- coding: utf-8 -*-
"""逐条件模型证据的路径回读与语义安全测试。"""

from __future__ import annotations

from service.eligibility import model_evidence as evidence


def _item(key="主营业务收入"):
    return {
        "semanticItemKey": key,
        "itemName": "年主营业务收入",
        "enterpriseFields": ["年主营业务收入"],
        "policyFragment": "年主营业务收入原则上应达到5亿元以上（含）",
        "matchRule": {"operator": "gte", "targetValue": 5, "unit": "亿元"},
    }


def test_模型可选择企业画像中的主营业务收入并由服务端回读(monkeypatch):
    monkeypatch.setattr(evidence, "chat_with_retry", lambda *_args, **_kwargs: {
        "content": """{
          \"conditionFacts\": [{
            \"semanticItemKey\": \"主营业务收入\",
            \"factStatus\": \"exact\",
            \"evidencePath\": \"$.metrics.mainBusinessIncomeWan\",
            \"semanticCategory\": \"financial.main_business_revenue\",
            \"qualitativeStatus\": \"unknown\",
            \"evidenceText\": \"企业主营业务收入为 2399.52 万元\"
          }]
        }"""
    })
    profile = {"metrics": {"mainBusinessIncomeWan": 2399.52}}

    facts = evidence.resolve_condition_facts(profile, [_item()], enabled=True)

    fact = facts["主营业务收入"]
    assert fact["evidenceTier"] == "A1_exact"
    assert fact["value"] == 2399.52
    assert fact["unit"] == "万元"
    assert fact["sourcePaths"] == ["$.metrics.mainBusinessIncomeWan"]


def test_模型不能把不存在的路径作为证据(monkeypatch):
    monkeypatch.setattr(evidence, "chat_with_retry", lambda *_args, **_kwargs: {
        "content": """{
          \"conditionFacts\": [{
            \"semanticItemKey\": \"主营业务收入\",
            \"factStatus\": \"exact\",
            \"evidencePath\": \"$.metrics.不存在\",
            \"semanticCategory\": \"financial.main_business_revenue\",
            \"qualitativeStatus\": \"unknown\",
            \"evidenceText\": \"不存在\"
          }]
        }"""
    })

    facts = evidence.resolve_condition_facts({"metrics": {"mainBusinessIncomeWan": 2399.52}}, [_item()], enabled=True)

    assert facts == {}
