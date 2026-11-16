# -*- coding: utf-8 -*-
"""一期验收：端到端一致性与不变量检查。

前面各模块的单测覆盖各自逻辑，本文件检查跨模块的不变量——
这些是一期 DoD 里「不能放宽」的结构性断言，四期调参时也必须保持。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from service import fields as F
from service import taxonomy as tx
from service.paths import sample_enterprise_dir
from service.profile import build_profile
from service.rules import evaluate_rules, load_rules
from service.scoring import BAND_NOT_EVALUABLE, score_policy

TODAY = date(2026, 8, 3)

POLICY = {
    "fusionPolicyId": "ACC-001",
    "tags": {
        "A": {"primary": "A3", "confidence": "high", "basis": "资金奖补"},
        "B": {"values": ["B2", "B3"], "confidence": "high", "basis": "成长成熟期"},
        "C": {"values": ["C1"], "subValues": ["C1.3"], "confidence": "high", "basis": "软件"},
        "D": {"value": "D2", "confidence": "high", "basis": "需资质"},
    },
    "applicationWindow": {"startDate": "2026-09-01", "endDate": "2026-11-30", "isRolling": False},
    "keywordIndex": {"title": ["研发补助"], "aKeywords": ["资金奖补"], "cKeywords": ["软件开发"]},
}


def _sample_records(limit: int):
    sample_dir = sample_enterprise_dir()
    if not sample_dir.exists():
        pytest.skip(f"样本目录不存在：{sample_dir}")
    records = []
    for path in sorted(sample_dir.glob("*.json"))[: limit * 3]:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(record, dict) or "industryLevel1" not in record:
            continue
        if not F.text(record.get("enterpriseName")):
            continue
        records.append(record)
        if len(records) >= limit:
            break
    if not records:
        pytest.skip("没有可用样本")
    return records


# --------------------------------------------------------------------------
# 端到端不抛异常
# --------------------------------------------------------------------------


def test_全链路对300份真实样本不抛异常():
    for record in _sample_records(300):
        profile = build_profile(record, today=TODAY)
        rule_result = evaluate_rules(profile, enable_stage2=True)
        score = score_policy(
            profile, POLICY, keyword="研发补助", today=TODAY,
            chain_stages=rule_result["chainStages"],
            policy={"title": "软件企业研发补助专项"},
        )
        assert score["band"]


# --------------------------------------------------------------------------
# 确定性：同输入同输出
# --------------------------------------------------------------------------


def test_同一企业两次跑出完全相同结果():
    """确定性是回归测试与三期双模式比对的基础。"""
    for record in _sample_records(20):
        first_profile = build_profile(record, today=TODAY)
        second_profile = build_profile(record, today=TODAY)
        assert first_profile["tags"] == second_profile["tags"]

        first_rules = evaluate_rules(first_profile, enable_stage2=True)
        second_rules = evaluate_rules(second_profile, enable_stage2=True)
        assert first_rules["matchedRuleIds"] == second_rules["matchedRuleIds"]
        assert first_rules["skippedRuleIds"] == second_rules["skippedRuleIds"]

        first_score = score_policy(first_profile, POLICY, today=TODAY)
        second_score = score_policy(second_profile, POLICY, today=TODAY)
        assert first_score == second_score


# --------------------------------------------------------------------------
# 分数不变量
# --------------------------------------------------------------------------


def test_基础分永不超100():
    for record in _sample_records(200):
        profile = build_profile(record, today=TODAY)
        score = score_policy(profile, POLICY, today=TODAY)
        if score["baseScore"] is not None:
            assert 0 <= score["baseScore"] <= 100, profile["enterpriseName"]


def test_附加分永不超50():
    for record in _sample_records(200):
        profile = build_profile(record, today=TODAY)
        rule_result = evaluate_rules(profile, enable_stage2=True)
        score = score_policy(
            profile, POLICY, keyword="研发补助", today=TODAY,
            chain_stages=rule_result["chainStages"],
            policy={"title": "软件企业研发补助专项"},
        )
        assert score["bonusScore"] <= 50


def test_总分永不超150():
    for record in _sample_records(200):
        profile = build_profile(record, today=TODAY)
        rule_result = evaluate_rules(profile, enable_stage2=True)
        score = score_policy(
            profile, POLICY, keyword="研发补助", today=TODAY,
            chain_stages=rule_result["chainStages"],
            policy={"title": "软件企业研发补助专项"},
        )
        if score["totalScore"] is not None:
            assert score["totalScore"] <= 150


def test_归一化可复算():
    """响应里的 rawSum / effectiveWeight / normalized 三者必须自洽，
    否则分数无法被调用方验证。"""
    for record in _sample_records(100):
        profile = build_profile(record, today=TODAY)
        score = score_policy(profile, POLICY, today=TODAY)
        detail = score["baseDetail"]
        if detail["effectiveWeight"] == 0:
            continue
        expected = round(detail["rawSum"] / detail["effectiveWeight"] * 100, 2)
        assert abs(detail["normalized"] - expected) < 0.01


def test_有效权重等于计分项权重之和():
    for record in _sample_records(100):
        profile = build_profile(record, today=TODAY)
        score = score_policy(profile, POLICY, today=TODAY)
        detail = score["baseDetail"]
        assert detail["effectiveWeight"] == sum(i["weight"] for i in detail["items"])


def test_计分项与剔除项合计等于五项():
    for record in _sample_records(100):
        profile = build_profile(record, today=TODAY)
        score = score_policy(profile, POLICY, today=TODAY)
        detail = score["baseDetail"]
        assert len(detail["items"]) + len(detail["excludedItems"]) == 5


def test_每项得分不超其权重():
    for record in _sample_records(100):
        profile = build_profile(record, today=TODAY)
        score = score_policy(profile, POLICY, today=TODAY)
        for item in score["baseDetail"]["items"]:
            assert 0 <= item["score"] <= item["weight"], item["name"]


# --------------------------------------------------------------------------
# 可解释性：每个结论都有依据
# --------------------------------------------------------------------------


def test_每个计分项都有非空依据():
    for record in _sample_records(100):
        profile = build_profile(record, today=TODAY)
        score = score_policy(profile, POLICY, today=TODAY)
        for item in score["baseDetail"]["items"]:
            assert item["basis"].strip(), f"{profile['enterpriseName']} 的 {item['name']} 缺依据"


def test_每个剔除项都说明原因():
    for record in _sample_records(100):
        profile = build_profile(record, today=TODAY)
        score = score_policy(profile, POLICY, today=TODAY)
        for item in score["baseDetail"]["excludedItems"]:
            assert item["reason"].strip(), item["name"]


def test_每个跳过规则都指明缺失字段():
    for record in _sample_records(100):
        profile = build_profile(record, today=TODAY)
        rule_result = evaluate_rules(profile, enable_stage2=True)
        for channel_data in rule_result["channels"].values():
            for item in channel_data.get("skipped") or []:
                assert item["skipReason"].strip(), item["ruleId"]
                assert item["missingFields"], item["ruleId"]


def test_每个标签都有依据():
    for record in _sample_records(100):
        profile = build_profile(record, today=TODAY)
        if profile["tags"]["B"]:
            assert profile["tagBasis"]["B"]
        if profile["tags"]["C"]:
            assert profile["tagBasis"]["C"]


# --------------------------------------------------------------------------
# 0 与 null 的区分贯穿全链路
# --------------------------------------------------------------------------


def test_知产0与null在全链路产生不同结果():
    """一期最容易写错的地方，端到端再验一次。"""
    base = {
        "enterpriseName": "对照企业",
        "registerDate": "2016-10-13",
        "registerAddress": "厦门市思明区",
        "industryLevel2": "软件和信息技术服务业",
        "operationInfoList": [{"year": "2023", "employeeCount": 100}],
    }
    zero_profile = build_profile({**base, "ipClass1Count": 0, "ipClass2Count": 0}, today=TODAY)
    null_profile = build_profile(base, today=TODAY)

    # 画像层
    assert zero_profile["metrics"]["ipTotal"] == 0
    assert null_profile["metrics"]["ipTotal"] is None

    # 规则层：0 能判定「不满足」，null 判不了
    zero_rules = evaluate_rules(zero_profile)
    null_rules = evaluate_rules(null_profile)
    assert "R07" not in zero_rules["skippedRuleIds"]
    assert "R07" in null_rules["skippedRuleIds"]

    # 评分层：0 参与计分，null 剔除分母
    zero_score = score_policy(zero_profile, POLICY, today=TODAY)
    null_score = score_policy(null_profile, POLICY, today=TODAY)
    assert zero_score["baseDetail"]["effectiveWeight"] > null_score["baseDetail"]["effectiveWeight"]


# --------------------------------------------------------------------------
# G0 与信用项的联动
# --------------------------------------------------------------------------


def test_信用缺失时G0放行且信用项剔除分母():
    """两者必须联动：不拦截 + 不白送 10 分。"""
    profile = build_profile(
        {
            "enterpriseName": "无信用信息",
            "registerDate": "2016-10-13",
            "registerAddress": "厦门市思明区",
            "operationInfoList": [{"year": "2023", "employeeCount": 100}],
        },
        today=TODAY,
    )
    rule_result = evaluate_rules(profile)
    score = score_policy(profile, POLICY, today=TODAY)

    assert rule_result["gateResult"]["passed"] is True
    assert rule_result["gateResult"]["creditChecked"] is False
    excluded = {e["name"] for e in score["baseDetail"]["excludedItems"]}
    assert "信用记录" in excluded


def test_信用负面时拦截且不产出推荐():
    profile = build_profile(
        {
            "enterpriseName": "失信企业",
            "registerDate": "2016-10-13",
            "registerAddress": "厦门市思明区",
            "operationInfoList": [
                {"year": "2023", "employeeCount": 100, "enterpriseCredit": "存在经营异常"}
            ],
        },
        today=TODAY,
    )
    rule_result = evaluate_rules(profile, enable_stage2=True)

    assert rule_result["gateResult"]["passed"] is False
    assert rule_result["matchedRuleIds"] == []
    for channel in ("G2", "G3", "G4"):
        assert rule_result["channels"][channel]["executed"] is False


# --------------------------------------------------------------------------
# 配置驱动：改配置即改行为，不需改代码
# --------------------------------------------------------------------------


def test_权重全部来自配置():
    weights = tx.scoring_config()["baseWeights"]
    assert sum(weights.values()) == 100


def test_规则全部来自配置且数量为34():
    assert len(load_rules()) == 34


def test_C标签枚举来自配置():
    assert len(tx.valid_tags("C")) == 15


def test_阈值来自配置():
    thresholds = tx.scoring_config()["thresholds"]
    assert thresholds["directPush"] > thresholds["needsSupplement"]


# --------------------------------------------------------------------------
# not_evaluable 路径
# --------------------------------------------------------------------------


def test_全字段缺失时返回not_evaluable而非0分():
    profile = build_profile(
        {"enterpriseName": "空白企业", "registerAddress": "某省某市"}, today=TODAY
    )
    score = score_policy(profile, POLICY, today=TODAY)

    assert score["band"] == BAND_NOT_EVALUABLE
    assert score["baseScore"] is None
    assert score["reason"]
