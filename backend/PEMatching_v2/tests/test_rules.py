# -*- coding: utf-8 -*-
"""规则引擎测试。对应 docs/一期_确定性底座开发流程.md 第 6.4 节测试要求。

每条规则至少覆盖命中/未命中/跳过三态。
"""

from __future__ import annotations

from datetime import date

import pytest

from service import fields as F
from service.profile import build_profile
from service.rules import (
    MATCHED,
    SKIPPED,
    UNMATCHED,
    collect_unmatched_signals,
    evaluate_rule,
    evaluate_rules,
    load_rules,
)

TODAY = date(2026, 8, 3)


def _profile(**overrides):
    """构造画像。默认给足必填字段，按需覆盖。"""
    record = {
        "enterpriseName": "测试企业",
        "registerDate": "2016-10-13 00:00:00",
        "registerAddress": "厦门市思明区高雄路20号",
        "operationInfoList": [{"year": "2023", "employeeCount": 108}],
    }
    record.update(overrides)
    return build_profile(record, today=TODAY)


def _operation(**kwargs):
    row = {"year": "2023", "employeeCount": 108}
    row.update(kwargs)
    return [row]


def _rule(rule_id: str):
    return next(r for r in load_rules() if r["ruleId"] == rule_id)


def _state(profile, rule_id: str) -> str:
    return evaluate_rule(profile, _rule(rule_id))["state"]


# --------------------------------------------------------------------------
# 规则配置完整性
# --------------------------------------------------------------------------


def test_规则总数为34条():
    """docx 规则库：R00(1) + R05-R18(14) + R19-R23(5) + R24-R29(6) + R30-R37(8)。"""
    assert len(load_rules()) == 34


def test_规则ID无重复():
    ids = [r["ruleId"] for r in load_rules()]
    assert len(ids) == len(set(ids))


def test_docx未定义R01到R04():
    ids = {r["ruleId"] for r in load_rules()}
    for absent in ("R01", "R02", "R03", "R04"):
        assert absent not in ids


def test_每条规则都有必要字段():
    for rule in load_rules():
        rule_id = rule["ruleId"]
        assert rule.get("channel") in ("G0", "G2", "G3", "G4"), rule_id
        assert rule.get("priority") in ("P0", "P1", "P2", "P3"), rule_id
        assert rule.get("name"), rule_id
        assert isinstance(rule.get("conditions"), list), rule_id
        assert isinstance(rule.get("requiredFields"), list), rule_id


def test_每条规则都声明requiredFields():
    """requiredFields 是三态判定的依据，漏填会导致误判而非 skip。"""
    for rule in load_rules():
        assert rule["requiredFields"], f"{rule['ruleId']} 缺 requiredFields"


def test_通道分布符合docx():
    by_channel = {}
    for rule in load_rules():
        by_channel.setdefault(rule["channel"], []).append(rule["ruleId"])

    assert by_channel["G0"] == ["R00"]
    # G2 = R05-R18 的 14 条 + R30/R31/R32/R33/R34/R35/R36 的 7 条
    assert len(by_channel["G2"]) == 21
    assert len(by_channel["G3"]) == 5
    assert len(by_channel["G4"]) == 7
    assert sum(len(v) for v in by_channel.values()) == 34


# --------------------------------------------------------------------------
# G0 信用拦截：字段缺失时既不拦截也不算通过
# --------------------------------------------------------------------------


def test_信用字段无值时通过但标记未校验():
    """enterpriseCredit 实测 100% 为空。不能因缺失而拦截（否则全部企业被拦），
    也不能因缺失而标记信用良好（否则白送 10 分）。"""
    result = evaluate_rules(_profile())
    gate = result["gateResult"]

    assert gate["passed"] is True
    assert gate["creditChecked"] is False
    assert "未执行信用校验" in gate["reason"]


def test_信用字段无值时R00判skipped():
    assert _state(_profile(), "R00") == SKIPPED


def test_有信用负面时拦截且后续通道不执行():
    profile = _profile(operationInfoList=_operation(enterpriseCredit="存在经营异常"))
    result = evaluate_rules(profile)
    gate = result["gateResult"]

    assert gate["passed"] is False
    assert gate["blockedBy"] == "R00"
    assert gate["creditChecked"] is True
    for channel in ("G2", "G3", "G4"):
        assert result["channels"][channel]["executed"] is False
        assert result["channels"][channel]["matched"] == []


def test_拦截时返回信用修复引导():
    profile = _profile(operationInfoList=_operation(enterpriseCredit="严重失信"))
    result = evaluate_rules(profile)

    assert result["gateResult"]["guidance"]


def test_信用良好时R00判unmatched并标记已校验():
    profile = _profile(operationInfoList=_operation(enterpriseCredit="信用良好"))
    result = evaluate_rules(profile)

    assert _state(profile, "R00") == UNMATCHED
    assert result["gateResult"]["passed"] is True
    assert result["gateResult"]["creditChecked"] is True


def test_拦截时不产出任何信号():
    profile = _profile(operationInfoList=_operation(enterpriseCredit="经营异常"))
    result = evaluate_rules(profile)

    assert result["matchedRuleIds"] == []
    assert all(not v for v in result["aggregatedSignals"].values())


# --------------------------------------------------------------------------
# R17 永久跳过
# --------------------------------------------------------------------------


def test_R17永久跳过且标记permanent():
    """对外投资、分支机构字段在接口契约里就不存在，不是数据缺失。"""
    result = evaluate_rule(_profile(), _rule("R17"))

    assert result["state"] == SKIPPED
    assert result["permanent"] is True
    assert "接口契约" in result["skipReason"]


def test_R17在配置里标了permanentlySkipped():
    assert _rule("R17").get("permanentlySkipped") is True


def test_只有R17是永久跳过():
    """其余缺字段的规则靠 requiredFields 自然 skip，
    新接口补齐字段后自动生效，不该标 permanentlySkipped。"""
    permanent = [r["ruleId"] for r in load_rules() if r.get("permanentlySkipped")]

    assert permanent == ["R17"]


# --------------------------------------------------------------------------
# G2 梯度培育 R05 R06 R07
# --------------------------------------------------------------------------


def test_R05初创期命中():
    profile = _profile(
        registerDate="2025-01-01",
        honorQualificationList=[{"isTechSme": "否", "isHighTechEnterprise": "否"}],
    )
    assert "B1" in profile["tags"]["B"]
    assert _state(profile, "R05") == MATCHED


def test_R05非初创期未命中():
    profile = _profile(
        registerDate="2010-01-01",
        operationInfoList=_operation(operatingIncome=500000000.0),
    )
    if "B1" not in profile["tags"]["B"]:
        assert _state(profile, "R05") == UNMATCHED


def test_R05在B为unknown时跳过():
    profile = build_profile(
        {"enterpriseName": "无信息", "registerAddress": "厦门市"}, today=TODAY
    )
    assert profile["fieldAvailability"]["bTags"] == F.UNKNOWN
    assert _state(profile, "R05") == SKIPPED


def test_R06成长期命中():
    profile = _profile(honorQualificationList=[{"isHighTechEnterprise": "是"}])
    assert _state(profile, "R06") == MATCHED


def test_R07高企领域且知产达标命中():
    profile = _profile(
        industryLevel1="C 制造业", industryLevel2="医药制造业",
        ipClass1Count=3, ipClass2Count=4,
    )
    assert _state(profile, "R07") == MATCHED


def test_R07知产不足未命中():
    profile = _profile(
        industryLevel1="C 制造业", industryLevel2="医药制造业",
        ipClass1Count=1, ipClass2Count=1,
    )
    assert _state(profile, "R07") == UNMATCHED


def test_R07知产为0时未命中而非跳过():
    """0 是已核验的真实值，能判定「不满足」；null 才是判不了。"""
    profile = _profile(
        industryLevel1="C 制造业", industryLevel2="医药制造业",
        ipClass1Count=0, ipClass2Count=0,
    )
    assert profile["fieldAvailability"]["ipTotal"] == F.AVAILABLE
    assert _state(profile, "R07") == UNMATCHED


def test_R07知产为null时跳过():
    profile = _profile(industryLevel1="C 制造业", industryLevel2="医药制造业")
    assert profile["fieldAvailability"]["ipTotal"] == F.UNKNOWN
    assert _state(profile, "R07") == SKIPPED


# --------------------------------------------------------------------------
# G2 产业专项 R08-R12
# --------------------------------------------------------------------------


def test_R08集成电路命中():
    profile = _profile(industryLevel2="计算机、通信和其他电子设备制造业", brief="集成电路设计")
    assert _state(profile, "R08") == MATCHED


def test_R09软件专项命中():
    profile = _profile(industryLevel2="软件和信息技术服务业")
    assert "C1.3" in profile["tags"]["cSub"]
    assert _state(profile, "R09") == MATCHED


def test_R10人工智能命中():
    profile = _profile(brief="专注人工智能大模型")
    assert _state(profile, "R10") == MATCHED


def test_R12新型显示命中():
    profile = _profile(brief="OLED 面板研发")
    assert _state(profile, "R12") == MATCHED


def test_产业专项在C为unknown时跳过():
    profile = _profile()
    assert profile["fieldAvailability"]["cTags"] == F.UNKNOWN
    for rule_id in ("R08", "R09", "R10", "R12"):
        assert _state(profile, rule_id) == SKIPPED, rule_id


def test_产业专项行业不符时未命中():
    profile = _profile(industryLevel2="医药制造业")
    for rule_id in ("R08", "R09", "R10", "R12"):
        assert _state(profile, rule_id) == UNMATCHED, rule_id


def test_R11数据产业在文本源缺失时跳过():
    profile = _profile()
    assert _state(profile, "R11") == SKIPPED


# --------------------------------------------------------------------------
# G2 属地专项 R13 R14 R35 R36
# --------------------------------------------------------------------------


def test_R13火炬高新区命中():
    profile = _profile(registerAddress="厦门市湖里区火炬高新区创新二路1号")
    assert _state(profile, "R13") == MATCHED


def test_R13非火炬未命中():
    profile = _profile(registerAddress="厦门市思明区某路")
    assert _state(profile, "R13") == UNMATCHED


def test_R14行政区命中():
    profile = _profile(registerAddress="厦门市集美区某路")
    assert _state(profile, "R14") == MATCHED


def test_R35科学城命中():
    profile = _profile(registerAddress="厦门科学城创新园")
    assert _state(profile, "R35") == MATCHED


def test_R36自贸片区命中():
    profile = _profile(registerAddress="厦门自贸片区某路")
    assert _state(profile, "R36") == MATCHED


def test_属地规则在无法识别地址时跳过():
    profile = _profile(registerAddress="某省某市某县")
    assert profile["fieldAvailability"]["regions"] == F.UNKNOWN
    for rule_id in ("R13", "R14", "R35", "R36"):
        assert _state(profile, rule_id) == SKIPPED, rule_id


# --------------------------------------------------------------------------
# G2 规模普惠 R30 R31 R32 R15 R33 R34
# --------------------------------------------------------------------------


def test_R30小微企业命中():
    profile = _profile(operationInfoList=_operation(employeeCount=50, operatingIncome=10000000.0))
    assert _state(profile, "R30") == MATCHED


def test_R30规模超标未命中():
    profile = _profile(operationInfoList=_operation(employeeCount=50, operatingIncome=5000000000.0))
    assert _state(profile, "R30") == UNMATCHED


def test_R30营收缺失时跳过():
    profile = _profile(operationInfoList=_operation(employeeCount=50))
    assert _state(profile, "R30") == SKIPPED


def test_R31中小微命中():
    assert _state(_profile(), "R31") == MATCHED


def test_R32科小命中():
    profile = _profile(honorQualificationList=[{"isTechSme": "是"}])
    assert _state(profile, "R32") == MATCHED


def test_R32非科小未命中():
    profile = _profile(honorQualificationList=[{"isTechSme": "否"}])
    assert _state(profile, "R32") == UNMATCHED


def test_R32资质字段为null时跳过():
    assert _state(_profile(), "R32") == SKIPPED


def test_R15小规模命中():
    assert _state(_profile(), "R15") == MATCHED


def test_R33新增参保命中():
    profile = _profile(operationInfoList=_operation(newRecruitCount=15))
    assert _state(profile, "R33") == MATCHED


def test_R33新增为0时未命中():
    profile = _profile(operationInfoList=_operation(newRecruitCount=0))
    assert _state(profile, "R33") == UNMATCHED


def test_R34有职工命中():
    assert _state(_profile(), "R34") == MATCHED


def test_R34职工为0时未命中():
    profile = _profile(operationInfoList=_operation(employeeCount=0))
    assert _state(profile, "R34") == UNMATCHED


# --------------------------------------------------------------------------
# G2 成长提示 R16 R18
# --------------------------------------------------------------------------


def test_R16股份公司命中():
    profile = _profile(registerType="股份有限公司（上市）")
    assert _state(profile, "R16") == MATCHED


def test_R16有限责任公司未命中():
    profile = _profile(registerType="有限责任公司（自然人独资）")
    assert _state(profile, "R16") == UNMATCHED


def test_R16企业类型缺失时跳过():
    assert _state(_profile(), "R16") == SKIPPED


def test_R18无专利且成立满3年且科技行业命中():
    profile = _profile(
        industryLevel2="软件和信息技术服务业", ipClass1Count=0, ipClass2Count=0
    )
    assert _state(profile, "R18") == MATCHED


def test_R18有专利时未命中():
    profile = _profile(
        industryLevel2="软件和信息技术服务业", ipClass1Count=5, ipClass2Count=0
    )
    assert _state(profile, "R18") == UNMATCHED


# --------------------------------------------------------------------------
# G3 链路推荐 R19-R23
# --------------------------------------------------------------------------


def test_R21科小未获高企时命中():
    profile = _profile(
        honorQualificationList=[{"isTechSme": "是", "isHighTechEnterprise": "否"}]
    )
    assert _state(profile, "R21") == MATCHED


def test_R21已获高企时未命中():
    profile = _profile(
        honorQualificationList=[{"isTechSme": "是", "isHighTechEnterprise": "是"}]
    )
    assert _state(profile, "R21") == UNMATCHED


def test_R22国家级高企命中():
    profile = _profile(honorQualificationList=[{"isHighTechEnterprise": "是"}])
    assert _state(profile, "R22") == MATCHED


def test_G3三条规则因资质字段全空而跳过():
    """isInnovativeSme / isSpecializedSme / isTechLeaderEnterprise 实测 0%。"""
    profile = _profile(honorQualificationList=[{"isTechSme": "是"}])
    for rule_id in ("R19", "R20", "R23"):
        result = evaluate_rule(profile, _rule(rule_id))
        assert result["state"] == SKIPPED, rule_id
        assert result["permanent"] is False, f"{rule_id} 应是缺字段跳过而非永久跳过"


def test_G3规则字段补齐后即可命中():
    """验证「新接口补齐字段后自动生效」这一设计。"""
    profile = _profile(honorQualificationList=[{"isInnovativeSme": "是"}])
    assert _state(profile, "R19") == MATCHED


def test_链路规则命中时产出chainStage():
    profile = _profile(honorQualificationList=[{"isHighTechEnterprise": "是"}])
    result = evaluate_rules(profile)

    stages = {s["ruleId"] for s in result["chainStages"]}
    assert "R22" in stages


# --------------------------------------------------------------------------
# G4 第二轮精推
# --------------------------------------------------------------------------


def test_G4默认不执行():
    result = evaluate_rules(_profile(), enable_stage2=False)

    assert result["channels"]["G4"]["executed"] is False
    assert result["channels"]["G4"]["matched"] == []
    assert "enableStage2" in result["channels"]["G4"]["reason"]


def test_G4启用后执行():
    profile = _profile(operationInfoList=_operation(rdExpense=3000000.0))
    result = evaluate_rules(profile, enable_stage2=True)

    assert result["channels"]["G4"]["executed"] is True
    assert "R24" in [m["ruleId"] for m in result["channels"]["G4"]["matched"]]


def test_R24研发费达标命中():
    profile = _profile(operationInfoList=_operation(rdExpense=3000000.0))
    assert _state(profile, "R24") == MATCHED


def test_R24研发费不足未命中():
    profile = _profile(operationInfoList=_operation(rdExpense=500000.0))
    assert _state(profile, "R24") == UNMATCHED


def test_R24研发费缺失时跳过():
    assert _state(_profile(), "R24") == SKIPPED


def test_R27博士人数命中():
    profile = _profile(operationInfoList=_operation(doctorCount=3))
    assert _state(profile, "R27") == MATCHED


def test_R26技术合同额字段全空时跳过():
    """techContractAmount 实测 100% 为空。"""
    assert _state(_profile(), "R26") == SKIPPED


def test_R37研发机构字段全空时跳过():
    assert _state(_profile(), "R37") == SKIPPED


# --------------------------------------------------------------------------
# 优先级排序与聚合
# --------------------------------------------------------------------------


def test_命中规则按优先级排序():
    profile = _profile(
        industryLevel2="软件和信息技术服务业",
        registerAddress="厦门市湖里区火炬高新区创新二路1号",
        honorQualificationList=[{"isTechSme": "是", "isHighTechEnterprise": "是"}],
        operationInfoList=_operation(operatingIncome=10000000.0),
    )
    result = evaluate_rules(profile)
    priorities = [m["priority"] for m in result["channels"]["G2"]["matched"]]

    assert priorities == sorted(priorities), "P1 应排在 P2 之前"


def test_聚合signals去重():
    profile = _profile(
        industryLevel2="软件和信息技术服务业",
        honorQualificationList=[{"isTechSme": "是"}],
    )
    result = evaluate_rules(profile)

    for key, values in result["aggregatedSignals"].items():
        assert len(values) == len(set(values)), f"{key} 有重复"


def test_三态数量之和等于通道规则数():
    profile = _profile()
    result = evaluate_rules(profile, enable_stage2=True)

    for channel, expected in (("G2", 21), ("G3", 5), ("G4", 7)):
        data = result["channels"][channel]
        total = len(data["matched"]) + len(data["unmatched"]) + len(data["skipped"])
        assert total == expected, f"{channel} 三态之和 {total} != {expected}"


def test_skipped都带原因与缺失字段():
    profile = _profile()
    result = evaluate_rules(profile, enable_stage2=True)

    for channel_data in result["channels"].values():
        for item in channel_data.get("skipped") or []:
            assert item["skipReason"], item["ruleId"]
            assert item["missingFields"], item["ruleId"]


def test_unmatched带判定明细():
    profile = _profile(registerType="有限责任公司")
    result = evaluate_rules(profile)
    unmatched = {u["ruleId"]: u for u in result["channels"]["G2"]["unmatched"]}

    assert unmatched["R16"]["details"]


def test_matchedRuleIds与通道结果一致():
    profile = _profile(honorQualificationList=[{"isTechSme": "是"}])
    result = evaluate_rules(profile, enable_stage2=True)

    from_channels = [
        m["ruleId"]
        for channel in ("G2", "G3", "G4")
        for m in result["channels"][channel]["matched"]
    ]
    assert sorted(result["matchedRuleIds"]) == sorted(from_channels)


# --------------------------------------------------------------------------
# unmatchedSignals：政策库缺口
# --------------------------------------------------------------------------


def test_收集命中规则的政策包描述():
    """规则命中但库内无对应政策时，这份清单是补库的输入，不静默丢弃。"""
    profile = _profile(honorQualificationList=[{"isTechSme": "是"}])
    result = evaluate_rules(profile)
    signals = collect_unmatched_signals(result)

    assert signals
    assert all(item["policyPackDesc"] for item in signals)
    assert all(item["ruleId"] and item["priority"] for item in signals)


# --------------------------------------------------------------------------
# 运算符
# --------------------------------------------------------------------------


def test_未知运算符抛错():
    profile = _profile()
    bad_rule = {
        "ruleId": "RXX", "channel": "G2", "priority": "P1", "name": "坏规则",
        "conditions": [{"field": "tags.B", "op": "unknownOp", "value": 1}],
        "requiredFields": [],
    }
    with pytest.raises(ValueError) as exc:
        evaluate_rule(profile, bad_rule)

    assert "未知运算符" in str(exc.value)


def test_全量样本前100份规则引擎不抛异常():
    import json
    from service.paths import sample_enterprise_dir

    sample_dir = sample_enterprise_dir()
    if not sample_dir.exists():
        pytest.skip(f"样本目录不存在：{sample_dir}")

    checked = 0
    for path in sorted(sample_dir.glob("*.json"))[:100]:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or not F.text(record.get("enterpriseName")):
            continue
        profile = build_profile(record, today=TODAY)
        result = evaluate_rules(profile, enable_stage2=True)
        assert "gateResult" in result
        checked += 1

    assert checked > 0
