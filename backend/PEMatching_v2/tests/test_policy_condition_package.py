# -*- coding: utf-8 -*-
"""T2 政策条件包组装与质量标记测试。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from service import llm_client
from service import policy_condition_package as packages
from service import policy_sync

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "t0" / "policies"


def _fixture(policy_id: str):
    return json.loads((FIXTURE_DIR / f"{policy_id}.json").read_text(encoding="utf-8"))


def _record(policy_id: str):
    detail = _fixture(policy_id)["data"]
    return policy_sync._normalize(detail, detail)


def _issue_codes(package):
    return {issue["code"] for issue in package["qualityFlags"]["issues"]}


def test_6451保留完整OR原文和原始规则结构():
    fixture = _fixture("6451")["data"]
    record = _record("6451")
    original = deepcopy(record)

    package = packages.build_condition_package(record)

    assert package["conditions"][0]["applicationRequirement"] == fixture["conditions"][0]["applicationRequirement"]
    assert package["ruleTableJson"] == fixture["ruleTableJson"]
    assert package["qualityFlags"]["originalContainsOrExpression"] is True
    assert package["qualityFlags"]["ruleTableAllAnd"] is True
    assert package["qualityFlags"]["suspectedOrOperatorMismatch"] is True
    assert "OR_OPERATOR_MISMATCH_RISK" in _issue_codes(package)
    assert record == original


def test_6534规则评分项为空仍保留完整高企原文():
    fixture = _fixture("6534")["data"]

    package = packages.build_condition_package(_record("6534"))

    assert package["qualityFlags"]["ruleTableEmpty"] is True
    assert package["qualityFlags"]["ruleItemCount"] == 0
    assert package["qualityFlags"]["hasApplicationRequirement"] is True
    assert package["conditions"][0]["applicationRequirement"] == fixture["conditions"][0]["applicationRequirement"]
    assert "企业申请认定前1年内未发生重大安全" in package["conditions"][0]["applicationRequirement"]
    assert package["ruleTableJson"] == fixture["ruleTableJson"]


def test_6509标记unresolved和llm_judge但不丢弃政策():
    package = packages.build_condition_package(_record("6509"))
    flags = package["qualityFlags"]

    assert flags["ruleTableEmpty"] is False
    assert flags["ruleItemCount"] == 2
    assert flags["unresolvedItemCount"] == 2
    assert flags["llmJudgeItemCount"] == 1
    assert flags["hasUnresolvedItems"] is True
    assert flags["hasLlmJudgeItems"] is True
    assert package["fusionPolicyId"] == "6509"


def test_定性空目标只记录信息不判为定量目标缺失():
    record = _record("6509")
    item = record["ruleTableJson"]["softRuleGroups"][1]["conditionFieldItems"][0]
    item["matchRule"]["operator"] = "llm_judge"
    item["matchRule"]["targetValue"] = ""

    flags = packages.build_condition_package(record)["qualityFlags"]

    assert flags["hasEmptyTargetValues"] is True
    assert flags["hasMissingRequiredTargetValues"] is False
    assert "EMPTY_TARGET_VALUES" in {
        issue["code"] for issue in flags["issues"]
    }
    assert "MISSING_REQUIRED_TARGET_VALUES" not in {
        issue["code"] for issue in flags["issues"]
    }


def test_定量运算符空目标标记为警告且数值0不是空值():
    missing_record = _record("6509")
    missing_item = missing_record["ruleTableJson"]["softRuleGroups"][0]["conditionFieldItems"][0]
    missing_item["matchRule"]["operator"] = "gte"
    missing_item["matchRule"]["targetValue"] = ""

    zero_record = _record("6509")
    zero_item = zero_record["ruleTableJson"]["softRuleGroups"][0]["conditionFieldItems"][0]
    zero_item["matchRule"]["operator"] = "gte"
    zero_item["matchRule"]["targetValue"] = 0

    missing_flags = packages.build_condition_package(missing_record)["qualityFlags"]
    zero_flags = packages.build_condition_package(zero_record)["qualityFlags"]

    assert missing_flags["hasMissingRequiredTargetValues"] is True
    assert missing_flags["missingRequiredTargetValueCount"] == 1
    assert zero_flags["hasMissingRequiredTargetValues"] is False


def test_条件包剔除联系方式附件和申报材料但保留支持方式():
    record = _record("6509")
    record["attachments"] = [{"name": "不应进入模型"}]

    package = packages.build_condition_package(record)

    assert "contactInfo" not in package["policyInfo"]
    assert "attachments" not in package
    assert "materials" not in package
    assert package["supportMethods"][0]["supportType"] == "研发专项补助"
    assert package["keywords"][0]["text"] == "研发经费补助"


def test_条件包生成不调用模型且可以JSON序列化():
    with patch.object(llm_client, "chat", side_effect=AssertionError("不得调用模型")) as mocked:
        package = packages.build_condition_package(_record("6451"))

    mocked.assert_not_called()
    serialized = json.dumps(package, ensure_ascii=False, sort_keys=True)
    assert "applicationRequirement" in serialized
    assert len(package["conditionPackageHash"]) == 64
    packages.validate_condition_package(package)


def test_相同政策内容生成稳定条件包哈希():
    first = packages.build_condition_package(_record("6509"))
    second = packages.build_condition_package(_record("6509"))

    assert first["conditionPackageHash"] == second["conditionPackageHash"]
    assert first == second


def test_条件包快照保留全部政策并生成稳定版本():
    records = [_record("6451"), _record("6509"), _record("6534")]
    policy_snapshot = {
        "schemaVersion": policy_sync.SNAPSHOT_SCHEMA,
        "versionHash": "a" * 64,
        "count": len(records),
        "data": records,
    }

    first = packages.build_condition_package_snapshot(policy_snapshot)
    second = packages.build_condition_package_snapshot(policy_snapshot)

    assert first["count"] == 3
    assert [item["fusionPolicyId"] for item in first["data"]] == ["6451", "6509", "6534"]
    assert first["versionHash"] == second["versionHash"]
    assert packages.condition_package_stats(first)["ruleTableEmpty"] == 1


def test_条件包快照可原子写入并校验读取(tmp_path, monkeypatch):
    record = _record("6509")
    snapshot = packages.build_condition_package_snapshot({
        "schemaVersion": policy_sync.SNAPSHOT_SCHEMA,
        "versionHash": "b" * 64,
        "count": 1,
        "data": [record],
    })
    target = tmp_path / "condition_snapshot.json"
    monkeypatch.setattr(packages, "POLICY_CONDITION_SNAPSHOT_PATH", target)

    packages.write_condition_package_snapshot(snapshot)
    loaded = packages.load_condition_package_snapshot()

    assert loaded == snapshot
    assert loaded["data"][0]["conditions"][0]["applicationRequirement"]
