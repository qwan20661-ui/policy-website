# -*- coding: utf-8 -*-
"""四期校准与调优的结构性回归。"""

from __future__ import annotations

import json
from datetime import date

import pytest
from openpyxl import load_workbook

import run_apply_review as review
import run_policy_tagging as tagging_cli
from service import policy_index
from script.build_policy_gap_report import aggregate
from script.evaluate_calibration import evaluate_thresholds
from service.scoring import _score_scale, build_secondary_sort_key
from service.tagging import validate_tagging_result

TODAY = date(2026, 8, 4)


def _entry(value_key, value):
    return {value_key: value, "confidence": "high", "basis": "测试依据"}


def _tagged(policy_id="1"):
    return {
        "fusionPolicyId": policy_id, "title": "测试政策",
        "tags": {
            "A": {**_entry("primary", "A1"), "secondary": []},
            "B": {**_entry("values", ["B2"])},
            "C": {**_entry("values", ["C9"]), "subValues": []},
            "D": _entry("value", "D2"), "E": _entry("value", "E2"),
            "F": _entry("value", "F3"),
        },
        "applicability": {"applicable": True, "audience": "企业", "reason": "企业可申报"},
        "applicationWindow": {"startDate": None, "endDate": None, "isRolling": True,
                              "source": "rolling_text", "basis": "长期受理"},
        "growthChain": {"prerequisite": None, "nextStage": None, "basis": "不属链路"},
        "keywordIndex": {"title": ["测试"], "aKeywords": [], "cKeywords": []},
        "scaleThresholds": {"employeeMin": None, "employeeMax": 500,
                            "revenueMinWan": None, "revenueMaxWan": 20000,
                            "confidence": "high", "basis": "职工不超过500人、营收不超过2亿元"},
        "notes": "",
    }


def test_次级排序优先临近截止再比较支持力度():
    urgent = _tagged()
    urgent["applicationWindow"] = {"endDate": "2026-08-10", "isRolling": False}
    later = _tagged()
    later["applicationWindow"] = {"endDate": "2026-09-10", "isRolling": False}
    rolling = _tagged()

    assert build_secondary_sort_key(urgent, TODAY) < build_secondary_sort_key(later, TODAY)
    assert build_secondary_sort_key(later, TODAY) < build_secondary_sort_key(rolling, TODAY)


def test_次级排序缺失支持力度排在有值之后():
    present = _tagged()
    missing = _tagged()
    missing["tags"]["E"]["value"] = None

    assert build_secondary_sort_key(present, TODAY) < build_secondary_sort_key(missing, TODAY)


def test_规模门槛拒绝非法数字并要求依据():
    payload = _tagged()
    payload["scaleThresholds"]["employeeMax"] = "五百"
    fatal, _ = validate_tagging_result(payload, "1")
    assert any("employeeMax" in message for message in fatal)

    payload = _tagged()
    payload["scaleThresholds"]["basis"] = ""
    fatal, _ = validate_tagging_result(payload, "1")
    assert any("basis" in message for message in fatal)


def test_中置信度规模门槛不参与确定性评分():
    profile = {
        "fieldAvailability": {"employeeCount": "available", "revenueWan": "available"},
        "metrics": {"employeeCount": 1000, "revenueWan": 50000},
    }
    policy = _tagged()
    policy["scaleThresholds"]["confidence"] = "medium"

    result = _score_scale(profile, policy)

    assert result["score"] == 15
    assert "置信度不足" in result["basis"]


def test_复核回写只产出差异且保留规模门槛(tmp_path, monkeypatch):
    base = _tagged("6548")
    tags_path = tmp_path / "policy_tags.json"
    tags_path.write_text(json.dumps({
        "schemaVersion": "pematching-v2-policy-tags-v1", "data": [base]
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(review, "POLICY_TAGS_PATH", tags_path)
    monkeypatch.setattr(tagging_cli, "OUTPUT_DIR", tmp_path)
    source = tagging_cli.export_review_excel({"6548": base})
    workbook = load_workbook(source)
    sheet = workbook["政策标签复核"]
    headers = {cell.value: cell.column for cell in sheet[1]}
    sheet.cell(2, headers["C大类"], "C2")
    reviewed = tmp_path / "reviewed.xlsx"
    workbook.save(reviewed)

    overrides = review.build_overrides(reviewed)

    assert len(overrides) == 1
    assert overrides[0]["tags"]["C"]["values"] == ["C2"]
    assert "scaleThresholds" not in overrides[0]
    assert json.loads(tags_path.read_text(encoding="utf-8"))["data"][0]["tags"]["C"]["values"] == ["C9"]


def test_非法人工override在索引加载时失败(tmp_path, monkeypatch):
    tags_path = tmp_path / "policy_tags.json"
    tags_path.write_text(json.dumps({
        "schemaVersion": "pematching-v2-policy-tags-v1", "data": [_tagged("6548")]
    }, ensure_ascii=False), encoding="utf-8")
    overrides_path = tmp_path / "policy_tags_overrides.json"
    overrides_path.write_text(json.dumps({
        "schemaVersion": "pematching-v2-policy-tags-overrides-v1",
        "data": [{
            "fusionPolicyId": "6548",
            "scaleThresholds": {
                "employeeMin": None, "employeeMax": "五百",
                "revenueMinWan": None, "revenueMaxWan": None,
                "confidence": "high", "basis": "非法人工值",
            },
        }],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(policy_index, "POLICY_TAGS_PATH", tags_path)
    monkeypatch.setattr(policy_index, "POLICY_TAGS_OVERRIDES_PATH", overrides_path)
    policy_index.reset_cache()

    with pytest.raises(policy_index.PolicyIndexError, match="人工 override 非法"):
        policy_index.load_policy_tags()

    policy_index.reset_cache()


def test_缺口报告按触发企业数排序并去重():
    results = [
        {"enterpriseName": "甲", "data": {"unmatchedSignals": [
            {"ruleId": "R1", "priority": "P1", "policyPackDesc": "创新券，技术服务"},
            {"ruleId": "R2", "priority": "P0", "policyPackDesc": "算力券"},
        ]}},
        {"enterpriseName": "乙", "data": {"unmatchedSignals": [
            {"ruleId": "R1", "priority": "P1", "policyPackDesc": "创新券，技术服务"},
        ]}},
    ]
    rows = aggregate(results)
    assert rows[0]["ruleId"] == "R1"
    assert rows[0]["enterpriseCount"] == 2
    assert rows[0]["suggestedDirection"] == "创新券"


def test_阈值推荐满足推送量与零推荐双重口径():
    rows = []
    for enterprise in ("甲", "乙"):
        rows.extend({"enterpriseName": enterprise, "totalScore": score} for score in (96, 90, 89, 88, 87, 70))
    result = evaluate_thresholds(rows)
    assert result["recommended"]["threshold"] == 88
