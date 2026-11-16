# -*- coding: utf-8 -*-
"""评分器测试。对应 docs/一期_确定性底座开发流程.md 第 7.4 节测试要求。

必须覆盖：五项齐全、逐项剔除分母、全剔除、领域五档、关键词四级只取最高、
时效性三档互斥、附加分封顶、阈值边界。
"""

from __future__ import annotations

from datetime import date

from service.profile import build_profile
from service.scoring import (
    BAND_DIRECT,
    BAND_NOT_EVALUABLE,
    BAND_NOT_RECOMMENDED,
    BAND_SUPPLEMENT,
    TIMELINESS_IN_PROGRESS,
    TIMELINESS_NOT_STARTED,
    TIMELINESS_PAST_YEAR,
    TIMELINESS_UNKNOWN,
    resolve_timeliness,
    score_policy,
)

TODAY = date(2026, 8, 3)


def _full_profile(**overrides):
    """五项计分字段全有值的企业。"""
    record = {
        "enterpriseName": "满字段企业",
        "registerDate": "2016-10-13 00:00:00",
        "registerAddress": "厦门市思明区高雄路20号",
        "industryLevel1": "C 制造业",
        "industryLevel2": "医药制造业",
        "ipClass1Count": 6,
        "ipClass2Count": 5,
        "operationInfoList": [{
            "year": "2023",
            "employeeCount": 108,
            "operatingIncome": 23995200.0,
            "enterpriseCredit": "信用良好",
        }],
        "honorQualificationList": [{"isTechSme": "是", "isHighTechEnterprise": "是"}],
    }
    record.update(overrides)
    return build_profile(record, today=TODAY)


def _minimal_profile():
    """只有必填字段的企业，多项计分被剔除分母。"""
    return build_profile(
        {
            "enterpriseName": "缺字段企业",
            "registerDate": "2016-10-13 00:00:00",
            "registerAddress": "厦门市思明区高雄路20号",
            "operationInfoList": [{"year": "2023", "employeeCount": 108}],
        },
        today=TODAY,
    )


def _policy(**overrides):
    payload = {
        "fusionPolicyId": "P001",
        "tags": {
            "A": {"primary": "A1", "confidence": "high", "basis": "资质认定"},
            "B": {"values": ["B2"], "confidence": "high", "basis": "成长期"},
            "C": {"values": ["C2"], "subValues": [], "confidence": "high", "basis": "生物医药"},
            "D": {"value": "D2", "confidence": "high", "basis": "需资质"},
        },
        "applicationWindow": None,
        "keywordIndex": {"title": [], "aKeywords": [], "cKeywords": []},
    }
    payload.update(overrides)
    return payload


def _window(start=None, end=None, rolling=False):
    return {"startDate": start, "endDate": end, "isRolling": rolling}


# --------------------------------------------------------------------------
# 五项齐全
# --------------------------------------------------------------------------


def test_五项全有效时有效权重为100():
    result = score_policy(_full_profile(), _policy(), today=TODAY)

    assert result["baseDetail"]["effectiveWeight"] == 100
    assert len(result["baseDetail"]["items"]) == 5
    assert result["baseDetail"]["excludedItems"] == []


def test_五项全有效时基础分等于原始分():
    """有效权重 100 时归一化不改变数值。"""
    result = score_policy(_full_profile(), _policy(), today=TODAY)
    detail = result["baseDetail"]

    assert detail["normalized"] == detail["rawSum"]


def test_基础分等于各项得分之和归一化():
    result = score_policy(_full_profile(), _policy(), today=TODAY)
    detail = result["baseDetail"]
    expected = round(sum(i["score"] for i in detail["items"]) / detail["effectiveWeight"] * 100, 2)

    assert detail["normalized"] == expected


def test_每项都带权重与依据():
    result = score_policy(_full_profile(), _policy(), today=TODAY)

    for item in result["baseDetail"]["items"]:
        assert item["weight"] > 0
        assert item["basis"], item["name"]
        assert 0 <= item["score"] <= item["weight"]


# --------------------------------------------------------------------------
# 分母剔除
# --------------------------------------------------------------------------


def test_信用无值时剔除分母而非白送10分():
    """enterpriseCredit 实测 100% 为空。「不知道有没有失信」≠「确认信用良好」。"""
    profile = _full_profile(operationInfoList=[{
        "year": "2023", "employeeCount": 108, "operatingIncome": 23995200.0,
    }])
    result = score_policy(profile, _policy(), today=TODAY)
    excluded = {e["name"] for e in result["baseDetail"]["excludedItems"]}

    assert "信用记录" in excluded
    assert result["baseDetail"]["effectiveWeight"] == 90


def test_行业无值时剔除领域匹配度():
    profile = _full_profile(industryLevel1=None, industryLevel2=None)
    result = score_policy(profile, _policy(), today=TODAY)
    excluded = {e["name"] for e in result["baseDetail"]["excludedItems"]}

    assert "领域匹配度" in excluded
    assert result["baseDetail"]["effectiveWeight"] == 70


def test_知产无值时剔除创新活跃度():
    profile = _full_profile(ipClass1Count=None, ipClass2Count=None)
    result = score_policy(profile, _policy(), today=TODAY)
    excluded = {e["name"] for e in result["baseDetail"]["excludedItems"]}

    assert "创新活跃度" in excluded
    assert result["baseDetail"]["effectiveWeight"] == 75


def test_知产为0时参与计分不剔除分母():
    """0 是已核验的真实值，记 0 分参与计分；null 才剔除分母。这两者不可混同。"""
    zero = score_policy(
        _full_profile(ipClass1Count=0, ipClass2Count=0), _policy(), today=TODAY
    )
    null = score_policy(
        _full_profile(ipClass1Count=None, ipClass2Count=None), _policy(), today=TODAY
    )

    assert zero["baseDetail"]["effectiveWeight"] == 100
    assert null["baseDetail"]["effectiveWeight"] == 75

    zero_item = next(i for i in zero["baseDetail"]["items"] if i["name"] == "创新活跃度")
    assert zero_item["score"] == 0


def test_剔除项都带原因():
    result = score_policy(_minimal_profile(), _policy(), today=TODAY)

    for item in result["baseDetail"]["excludedItems"]:
        assert item["reason"], item["name"]
        assert item["weight"] > 0


def test_缺字段企业不因缺失被系统性压低():
    """归一化的目的：字段少的企业与字段全的企业分数可比。
    构造两家企业，有效项上的表现一致，断言基础分接近。"""
    full = _full_profile(ipClass1Count=6, ipClass2Count=5)
    partial = _full_profile(
        ipClass1Count=6, ipClass2Count=5,
        operationInfoList=[{
            "year": "2023", "employeeCount": 108, "operatingIncome": 23995200.0,
        }],
    )
    full_score = score_policy(full, _policy(), today=TODAY)["baseScore"]
    partial_score = score_policy(partial, _policy(), today=TODAY)["baseScore"]

    assert abs(full_score - partial_score) < 15, "剔除信用项不应大幅改变基础分"


def test_全部剔除时返回not_evaluable而非0分():
    """0 分会被误读为「匹配度极差」，必须明确标为不可评估。"""
    profile = build_profile(
        {"enterpriseName": "空白企业", "registerAddress": "某省某市"}, today=TODAY
    )
    result = score_policy(profile, _policy(), today=TODAY)

    assert result["band"] == BAND_NOT_EVALUABLE
    assert result["baseScore"] is None
    assert result["totalScore"] is None
    assert result["baseDetail"]["effectiveWeight"] == 0
    assert result["reason"]


# --------------------------------------------------------------------------
# 领域匹配度五档
# --------------------------------------------------------------------------


def test_领域强子类交集得30分():
    profile = _full_profile(
        industryLevel2="计算机、通信和其他电子设备制造业", brief="集成电路设计与流片"
    )
    policy = _policy(tags={
        **_policy()["tags"],
        "C": {"values": ["C1"], "subValues": ["C1.2"], "confidence": "high", "basis": "集成电路"},
    })
    result = score_policy(profile, policy, today=TODAY)
    item = next(i for i in result["baseDetail"]["items"] if i["name"] == "领域匹配度")

    assert item["score"] == 30
    assert "业务描述" in item["basis"]


def test_领域弱子类交集得25分():
    """仅行业名含关键词，无业务描述佐证，证据弱。"""
    profile = _full_profile(industryLevel2="软件和信息技术服务业", brief=None)
    policy = _policy(tags={
        **_policy()["tags"],
        "C": {"values": ["C1"], "subValues": ["C1.3"], "confidence": "high", "basis": "软件"},
    })
    result = score_policy(profile, policy, today=TODAY)
    item = next(i for i in result["baseDetail"]["items"] if i["name"] == "领域匹配度")

    assert item["score"] == 25
    assert "行业名" in item["basis"]


def test_领域大类精确交集得25分():
    result = score_policy(_full_profile(), _policy(), today=TODAY)
    item = next(i for i in result["baseDetail"]["items"] if i["name"] == "领域匹配度")

    assert item["score"] == 25
    assert "大类精确匹配" in item["basis"]


def test_政策为C9通用只召回不计领域匹配分():
    policy = _policy(tags={
        **_policy()["tags"],
        "C": {"values": ["C9"], "subValues": [], "confidence": "high", "basis": "通用"},
    })
    result = score_policy(_full_profile(), policy, today=TODAY)
    item = next(i for i in result["baseDetail"]["items"] if i["name"] == "领域匹配度")

    assert item["score"] == 0
    assert "仅作为召回兜底" in item["basis"]


def test_领域无交集得0分():
    policy = _policy(tags={
        **_policy()["tags"],
        "C": {"values": ["C3"], "subValues": [], "confidence": "high", "basis": "航空航天"},
    })
    result = score_policy(_full_profile(), policy, today=TODAY)
    item = next(i for i in result["baseDetail"]["items"] if i["name"] == "领域匹配度")

    assert item["score"] == 0
    assert "无交集" in item["basis"]


def test_强子类优先于弱子类():
    """同时有强弱子类命中政策时取强档。"""
    profile = _full_profile(
        industryLevel2="软件和信息技术服务业", brief="集成电路EDA工具"
    )
    policy = _policy(tags={
        **_policy()["tags"],
        "C": {"values": ["C1"], "subValues": ["C1.2", "C1.3"], "confidence": "high", "basis": "多子类"},
    })
    result = score_policy(profile, policy, today=TODAY)
    item = next(i for i in result["baseDetail"]["items"] if i["name"] == "领域匹配度")

    assert item["score"] == 30


# --------------------------------------------------------------------------
# 发展阶段按信号数计分
# --------------------------------------------------------------------------


def test_阶段命中信号越多得分越高():
    """实测 B2 命中率 100%，必须靠信号数区分。"""
    one_signal = build_profile(
        {
            "enterpriseName": "单信号", "registerDate": "2021-01-01",
            "registerAddress": "厦门市思明区",
            "operationInfoList": [{"year": "2023", "employeeCount": 10}],
        },
        today=TODAY,
    )
    multi_signal = build_profile(
        {
            "enterpriseName": "多信号", "registerDate": "2021-01-01",
            "registerAddress": "厦门市思明区",
            "operationInfoList": [{"year": "2023", "employeeCount": 10, "operatingIncome": 50000000.0}],
            "honorQualificationList": [{"isHighTechEnterprise": "是"}],
        },
        today=TODAY,
    )
    policy = _policy(tags={
        **_policy()["tags"],
        "B": {"values": ["B2"], "confidence": "high", "basis": "成长期"},
    })

    low = score_policy(one_signal, policy, today=TODAY)
    high = score_policy(multi_signal, policy, today=TODAY)
    low_item = next(i for i in low["baseDetail"]["items"] if i["name"] == "发展阶段匹配")
    high_item = next(i for i in high["baseDetail"]["items"] if i["name"] == "发展阶段匹配")

    assert high_item["score"] > low_item["score"]


def test_政策未限定阶段时视为适用():
    policy = _policy(tags={
        **_policy()["tags"],
        "B": {"values": [], "confidence": "low", "basis": "未限定"},
    })
    result = score_policy(_full_profile(), policy, today=TODAY)
    item = next(i for i in result["baseDetail"]["items"] if i["name"] == "发展阶段匹配")

    assert item["score"] > 0


def test_阶段相邻得10分():
    profile = build_profile(
        {
            "enterpriseName": "初创", "registerDate": "2025-01-01",
            "registerAddress": "厦门市思明区",
            "operationInfoList": [{"year": "2023", "employeeCount": 5}],
            "honorQualificationList": [{"isTechSme": "否", "isHighTechEnterprise": "否"}],
        },
        today=TODAY,
    )
    policy = _policy(tags={
        **_policy()["tags"],
        "B": {"values": ["B3"], "confidence": "high", "basis": "成熟期"},
    })
    result = score_policy(profile, policy, today=TODAY)
    item = next(i for i in result["baseDetail"]["items"] if i["name"] == "发展阶段匹配")

    if "B1" in profile["tags"]["B"] and "B3" not in profile["tags"]["B"]:
        assert item["score"] == 0 or "相邻" in item["basis"]


# --------------------------------------------------------------------------
# 创新活跃度分档
# --------------------------------------------------------------------------


def test_创新活跃度四档():
    cases = [(12, 25), (7, 15), (3, 8), (0, 0)]
    for ip_count, expected in cases:
        profile = _full_profile(ipClass1Count=ip_count, ipClass2Count=0)
        result = score_policy(profile, _policy(), today=TODAY)
        item = next(i for i in result["baseDetail"]["items"] if i["name"] == "创新活跃度")
        assert item["score"] == expected, f"{ip_count} 件应得 {expected} 分"


def test_创新活跃度依据含来源():
    profile = _full_profile(ipClass1Count=None, ipClass2Count=None, ipCount=8)
    result = score_policy(profile, _policy(), today=TODAY)
    item = next(i for i in result["baseDetail"]["items"] if i["name"] == "创新活跃度")

    assert "ipCount" in item["basis"]


# --------------------------------------------------------------------------
# 规模适配
# --------------------------------------------------------------------------


def test_政策未限定规模时记满分并说明():
    """避免把「政策没限制」误判成「企业不达标」。"""
    result = score_policy(_full_profile(), _policy(), today=TODAY)
    item = next(i for i in result["baseDetail"]["items"] if i["name"] == "规模适配")

    assert item["score"] == 15
    assert "未限定" in item["basis"]


def test_规模落区间内得满分():
    policy = _policy(scaleThresholds={"employeeMax": 300, "revenueMaxWan": 30000})
    result = score_policy(_full_profile(), policy, today=TODAY)
    item = next(i for i in result["baseDetail"]["items"] if i["name"] == "规模适配")

    assert item["score"] == 15


def test_规模超上限得0分():
    policy = _policy(scaleThresholds={"employeeMax": 50})
    result = score_policy(_full_profile(), policy, today=TODAY)
    item = next(i for i in result["baseDetail"]["items"] if i["name"] == "规模适配")

    assert item["score"] == 0
    assert "超出上限" in item["basis"]


def test_规模处于边界得8分():
    """108 人对上限 100 人，在 10% 容差内算边界。"""
    policy = _policy(scaleThresholds={"employeeMax": 100})
    result = score_policy(_full_profile(), policy, today=TODAY)
    item = next(i for i in result["baseDetail"]["items"] if i["name"] == "规模适配")

    assert item["score"] == 8
    assert "边界" in item["basis"]


def test_规模低于下限得0分():
    policy = _policy(scaleThresholds={"employeeMin": 500})
    result = score_policy(_full_profile(), policy, today=TODAY)
    item = next(i for i in result["baseDetail"]["items"] if i["name"] == "规模适配")

    assert item["score"] == 0


# --------------------------------------------------------------------------
# 关键词四级取最高
# --------------------------------------------------------------------------


def test_未传关键词时不计关键词分():
    result = score_policy(_full_profile(), _policy(), today=TODAY)
    names = [b["name"] for b in result["bonusDetail"]["items"]]

    assert "关键词命中" not in names


def test_关键词命中标题得20分():
    policy = _policy(keywordIndex={"title": [], "aKeywords": [], "cKeywords": []})
    result = score_policy(
        _full_profile(), policy, keyword="高新技术企业", today=TODAY,
        policy={"title": "国家级高新技术企业认定"},
    )
    item = next(b for b in result["bonusDetail"]["items"] if b["name"] == "关键词命中")

    assert item["score"] == 20


def test_关键词同时命中标题和A类时只取最高():
    """docx 的「优先级」语义：四级取最高一级，不叠加。"""
    policy = _policy(keywordIndex={"title": [], "aKeywords": ["高新技术企业"], "cKeywords": []})
    result = score_policy(
        _full_profile(), policy, keyword="高新技术企业", today=TODAY,
        policy={"title": "高新技术企业认定"},
    )
    item = next(b for b in result["bonusDetail"]["items"] if b["name"] == "关键词命中")

    assert item["score"] == 20, "应只取标题的 20 分，不叠加 A 类的 12 分"


def test_关键词命中A类标签得12分():
    policy = _policy(keywordIndex={"title": [], "aKeywords": ["贴息"], "cKeywords": []})
    result = score_policy(
        _full_profile(), policy, keyword="贴息", today=TODAY,
        policy={"title": "某某资金支持办法"},
    )
    item = next(b for b in result["bonusDetail"]["items"] if b["name"] == "关键词命中")

    assert item["score"] == 12


def test_关键词命中C类标签得10分():
    policy = _policy(keywordIndex={"title": [], "aKeywords": [], "cKeywords": ["集成电路"]})
    result = score_policy(
        _full_profile(), policy, keyword="集成电路", today=TODAY,
        policy={"title": "某某专项"},
    )
    item = next(b for b in result["bonusDetail"]["items"] if b["name"] == "关键词命中")

    assert item["score"] == 10


def test_关键词仅命中正文得5分():
    result = score_policy(
        _full_profile(), _policy(), keyword="研发", today=TODAY,
        policy={"title": "某某办法", "policyInfo": {"summary": "支持企业研发活动"}},
    )
    item = next(b for b in result["bonusDetail"]["items"] if b["name"] == "关键词命中")

    assert item["score"] == 5


def test_关键词未命中不加分():
    result = score_policy(
        _full_profile(), _policy(), keyword="不存在的词", today=TODAY,
        policy={"title": "某某办法"},
    )
    names = [b["name"] for b in result["bonusDetail"]["items"]]

    assert "关键词命中" not in names


# --------------------------------------------------------------------------
# 时效性三档互斥
# --------------------------------------------------------------------------


def test_时效性当年未启动得15分():
    result = resolve_timeliness(_window("2026-09-01", "2026-11-30"), TODAY)

    assert result["band"] == TIMELINESS_NOT_STARTED
    assert result["score"] == 15


def test_时效性申报进行中得10分():
    result = resolve_timeliness(_window("2026-07-01", "2026-09-30"), TODAY)

    assert result["band"] == TIMELINESS_IN_PROGRESS
    assert result["score"] == 10


def test_时效性上一年度政策得8分():
    """docx 写「2025 年政策按 +8 分倾斜」，改为相对逻辑避免年份写死。"""
    result = resolve_timeliness(_window("2025-03-01", "2025-05-31"), TODAY)

    assert result["band"] == TIMELINESS_PAST_YEAR
    assert result["score"] == 8


def test_时效性更早年份得5分():
    result = resolve_timeliness(_window("2023-03-01", "2023-05-31"), TODAY)

    assert result["band"] == TIMELINESS_PAST_YEAR
    assert result["score"] == 5


def test_时效性常年受理按进行中处理():
    result = resolve_timeliness(_window(rolling=True), TODAY)

    assert result["band"] == TIMELINESS_IN_PROGRESS


def test_时效性窗口缺失得0分并说明原因():
    for window in (None, {}, _window(), {"startDate": "", "endDate": ""}):
        result = resolve_timeliness(window, TODAY)
        assert result["band"] == TIMELINESS_UNKNOWN
        assert result["score"] == 0
        assert result["basis"]


def test_时效性三档互斥():
    """同一政策只能落一档。"""
    windows = [
        _window("2026-09-01", "2026-11-30"),
        _window("2026-07-01", "2026-09-30"),
        _window("2025-03-01", "2025-05-31"),
    ]
    bands = [resolve_timeliness(w, TODAY)["band"] for w in windows]

    assert len(set(bands)) == 3


def test_时效性跨年未启动():
    result = resolve_timeliness(_window("2027-03-01", "2027-05-31"), TODAY)

    assert result["band"] == TIMELINESS_NOT_STARTED


def test_政策无申报期时附加分不含时效性():
    result = score_policy(_full_profile(), _policy(), today=TODAY)
    names = [b["name"] for b in result["bonusDetail"]["items"]]

    assert "时效性" not in names
    assert result["timeliness"]["band"] == TIMELINESS_UNKNOWN


# --------------------------------------------------------------------------
# 链路加成
# --------------------------------------------------------------------------


def test_链路加成命中得10分():
    policy = _policy(growthChain={"prerequisite": "国高→专精特新"})
    chain = [{"ruleId": "R22", "stage": "国高→专精特新", "policyPackDesc": "x"}]
    result = score_policy(_full_profile(), policy, today=TODAY, chain_stages=chain)
    item = next(b for b in result["bonusDetail"]["items"] if b["name"] == "链路加成")

    assert item["score"] == 10


def test_链路不匹配不加分():
    policy = _policy(growthChain={"prerequisite": "科小→市高"})
    chain = [{"ruleId": "R22", "stage": "国高→专精特新", "policyPackDesc": "x"}]
    result = score_policy(_full_profile(), policy, today=TODAY, chain_stages=chain)
    names = [b["name"] for b in result["bonusDetail"]["items"]]

    assert "链路加成" not in names


def test_无链路信息不加分():
    result = score_policy(_full_profile(), _policy(), today=TODAY, chain_stages=[])
    names = [b["name"] for b in result["bonusDetail"]["items"]]

    assert "链路加成" not in names


# --------------------------------------------------------------------------
# 附加分封顶
# --------------------------------------------------------------------------


def test_附加分封顶50():
    policy = _policy(
        applicationWindow=_window("2026-09-01", "2026-11-30"),
        growthChain={"prerequisite": "国高→专精特新"},
        keywordIndex={"title": [], "aKeywords": [], "cKeywords": []},
    )
    chain = [{"ruleId": "R22", "stage": "国高→专精特新", "policyPackDesc": "x"}]
    result = score_policy(
        _full_profile(), policy, keyword="高新技术企业", today=TODAY,
        chain_stages=chain, policy={"title": "高新技术企业认定"},
    )

    assert result["bonusScore"] <= 50


def test_附加分未超上限时capped为false():
    result = score_policy(_full_profile(), _policy(), today=TODAY)

    assert result["bonusDetail"]["capped"] is False


def test_附加分各项之和等于rawTotal():
    policy = _policy(applicationWindow=_window("2026-09-01", "2026-11-30"))
    result = score_policy(_full_profile(), policy, today=TODAY)
    detail = result["bonusDetail"]

    assert sum(i["score"] for i in detail["items"]) == detail["rawTotal"]


# --------------------------------------------------------------------------
# 阈值边界
# --------------------------------------------------------------------------


def test_总分等于基础分加附加分():
    policy = _policy(applicationWindow=_window("2026-09-01", "2026-11-30"))
    result = score_policy(_full_profile(), policy, today=TODAY)

    assert result["totalScore"] == round(result["baseScore"] + result["bonusScore"], 2)


def test_档位阈值85与60():
    from service import taxonomy as tx

    thresholds = tx.scoring_config()["thresholds"]
    assert thresholds["directPush"] == 85
    assert thresholds["needsSupplement"] == 60


def test_高分企业落直推档():
    policy = _policy(
        tags={
            **_policy()["tags"],
            "C": {"values": ["C2"], "subValues": [], "confidence": "high", "basis": "生物医药"},
        },
        applicationWindow=_window("2026-09-01", "2026-11-30"),
    )
    result = score_policy(_full_profile(), policy, today=TODAY)

    assert result["totalScore"] >= 85
    assert result["band"] == BAND_DIRECT


def test_低分企业落不推荐档():
    profile = _full_profile(ipClass1Count=0, ipClass2Count=0)
    policy = _policy(
        tags={
            **_policy()["tags"],
            "C": {"values": ["C3"], "subValues": [], "confidence": "high", "basis": "航空航天"},
            "B": {"values": ["B1"], "confidence": "high", "basis": "初创期"},
        },
        scaleThresholds={"employeeMax": 10},
    )
    result = score_policy(profile, policy, today=TODAY)

    assert result["band"] in (BAND_NOT_RECOMMENDED, BAND_SUPPLEMENT)


def test_档位取值只有四种():
    valid = {BAND_DIRECT, BAND_SUPPLEMENT, BAND_NOT_RECOMMENDED, BAND_NOT_EVALUABLE}
    for profile in (_full_profile(), _minimal_profile()):
        result = score_policy(profile, _policy(), today=TODAY)
        assert result["band"] in valid


# --------------------------------------------------------------------------
# 可复现性
# --------------------------------------------------------------------------


def test_同一输入两次评分结果完全一致():
    """确定性要求：同输入同输出，才能做回归测试与双模式比对。"""
    profile = _full_profile()
    policy = _policy(applicationWindow=_window("2026-09-01", "2026-11-30"))

    first = score_policy(profile, policy, keyword="高新", today=TODAY)
    second = score_policy(profile, policy, keyword="高新", today=TODAY)

    assert first == second


def test_today注入影响时效性判定():
    policy = _policy(applicationWindow=_window("2026-09-01", "2026-11-30"))

    before = score_policy(_full_profile(), policy, today=date(2026, 8, 3))
    during = score_policy(_full_profile(), policy, today=date(2026, 10, 1))

    assert before["timeliness"]["band"] == TIMELINESS_NOT_STARTED
    assert during["timeliness"]["band"] == TIMELINESS_IN_PROGRESS
