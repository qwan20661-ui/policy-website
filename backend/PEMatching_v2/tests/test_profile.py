# -*- coding: utf-8 -*-
"""企业画像测试。对应 docs/一期_确定性底座开发流程.md 第 5.4 节测试要求。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from service import fields as F
from service.paths import sample_enterprise_dir
from service.profile import build_profile

TODAY = date(2026, 8, 3)


def _record(**overrides):
    """最小可用企业记录。只有必填三项，其余按需覆盖。"""
    base = {
        "enterpriseName": "测试企业",
        "registerDate": "2016-10-13 00:00:00",
        "registerAddress": "厦门市思明区高雄路20号",
        "operationInfoList": [{"year": "2023", "employeeCount": 108}],
    }
    base.update(overrides)
    return base


def _operation(**kwargs):
    row = {"year": "2023"}
    row.update(kwargs)
    return [row]


# --------------------------------------------------------------------------
# 用例 1：全字段齐全（真实数据）
# --------------------------------------------------------------------------


def test_真实企业数据全流程():
    path = sample_enterprise_dir() / "2252_测试企业名称1.json"
    if not path.exists():
        pytest.skip(f"样本文件不存在：{path}")
    record = json.loads(path.read_text(encoding="utf-8"))

    profile = build_profile(record, today=TODAY)

    assert profile["enterpriseName"] == "测试企业名称1"
    assert profile["metrics"]["companyAgeYears"] == 9.8
    assert profile["metrics"]["employeeCount"] == 108
    assert profile["metrics"]["revenueWan"] == 2399.52
    assert profile["metrics"]["mainBusinessIncomeWan"] == 2399.52
    assert profile["metrics"]["rdExpenseWan"] == 735.39
    assert profile["metrics"]["rdExpenseRatioPct"] == 30.65
    assert profile["tags"]["regions"] == ["思明区"]
    assert profile["qualifications"]["isTechSme"] is True
    assert profile["qualifications"]["isHighTechEnterprise"] is True


# --------------------------------------------------------------------------
# 用例 2：仅必填字段
# --------------------------------------------------------------------------


def test_仅必填字段时B与属地仍能算出而C为unknown():
    """registerDate 与 registerAddress 覆盖率 100%，这两个标签应总能算出。"""
    profile = build_profile(_record(), today=TODAY)

    assert profile["tags"]["B"], "B 应能靠成立年限算出"
    assert profile["tags"]["regions"] == ["思明区"]
    assert profile["tags"]["C"] == [], "行业字段缺失时 C 应为空"
    assert profile["fieldAvailability"]["cTags"] == F.UNKNOWN


def test_缺enterpriseName时抛错():
    record = _record()
    del record["enterpriseName"]

    with pytest.raises(ValueError) as exc:
        build_profile(record, today=TODAY)

    assert "enterpriseName" in str(exc.value)


def test_非字典输入抛错():
    with pytest.raises(ValueError):
        build_profile("not a dict", today=TODAY)


# --------------------------------------------------------------------------
# 用例 3：0 vs null（硬门槛）
# --------------------------------------------------------------------------


def test_知产为0与为null产生不同结果():
    """全期最容易写错的地方：
    ipClass1Count=0 是确认没有，计 0 分；=null 是不知道，剔除分母。
    """
    zero = build_profile(
        _record(ipClass1Count=0, ipClass2Count=0), today=TODAY
    )
    null = build_profile(
        _record(ipClass1Count=None, ipClass2Count=None), today=TODAY
    )

    assert zero["metrics"]["ipTotal"] == 0
    assert zero["fieldAvailability"]["ipTotal"] == F.AVAILABLE
    assert zero["metrics"]["ipSource"] == "ipClass1+2"

    assert null["metrics"]["ipTotal"] is None
    assert null["fieldAvailability"]["ipTotal"] == F.UNKNOWN
    assert null["metrics"]["ipSource"] == ""


def test_知产一项为0另一项有值时求和():
    profile = build_profile(_record(ipClass1Count=0, ipClass2Count=11), today=TODAY)

    assert profile["metrics"]["ipTotal"] == 11


def test_知产仅一项有值另一项为null时按有值项算():
    profile = build_profile(_record(ipClass1Count=3, ipClass2Count=None), today=TODAY)

    assert profile["metrics"]["ipTotal"] == 3


def test_员工数为0与为null产生不同可用性():
    zero = build_profile(_record(operationInfoList=_operation(employeeCount=0)), today=TODAY)
    null = build_profile(_record(operationInfoList=_operation(employeeCount=None)), today=TODAY)

    assert zero["metrics"]["employeeCount"] == 0
    assert zero["fieldAvailability"]["employeeCount"] == F.AVAILABLE
    assert null["metrics"]["employeeCount"] is None
    assert null["fieldAvailability"]["employeeCount"] == F.UNKNOWN


def test_资质为null不误判为false():
    """资质字段实测 5 项 100% 为 null，当 False 会误判成「确认没有资质」。"""
    profile = build_profile(_record(), today=TODAY)

    assert profile["qualifications"]["isInnovativeSme"] is None
    assert profile["qualifications"]["isSpecializedSme"] is None


def test_资质明确为否时记false():
    profile = build_profile(
        _record(honorQualificationList=[{"isTechSme": "否"}]), today=TODAY
    )

    assert profile["qualifications"]["isTechSme"] is False
    assert profile["fieldAvailability"]["isTechSme"] == F.AVAILABLE


def test_资格补充事实保留并登记可用性():
    profile = build_profile(_record(eligibilityFacts={
        "isProvincialNewResearchInstitution": True,
        "positiveEvaluationConclusion": False,
    }), today=TODAY)

    assert profile["eligibilityFacts"]["isProvincialNewResearchInstitution"] is True
    assert profile["fieldAvailability"]["eligibilityFacts.isProvincialNewResearchInstitution"] == F.AVAILABLE
    assert profile["fieldAvailability"]["eligibilityFacts.positiveEvaluationConclusion"] == F.AVAILABLE


# --------------------------------------------------------------------------
# 用例 4：子表为空或为 null
# --------------------------------------------------------------------------


def test_子表为空数组不抛异常():
    profile = build_profile(
        _record(operationInfoList=[], honorQualificationList=[]), today=TODAY
    )

    assert profile["metrics"]["employeeCount"] is None
    assert profile["fieldAvailability"]["operationInfoList"] == F.UNKNOWN


def test_子表为null不抛异常():
    profile = build_profile(
        _record(operationInfoList=None, honorQualificationList=None), today=TODAY
    )

    assert profile["metrics"]["revenueWan"] is None
    assert all(v is None for v in profile["qualifications"].values())


def test_子表元素不是字典时跳过():
    profile = build_profile(
        _record(operationInfoList=["坏数据", {"year": "2023", "employeeCount": 50}]),
        today=TODAY,
    )

    assert profile["metrics"]["employeeCount"] == 50


def test_子表多年度取最新年份():
    profile = build_profile(
        _record(operationInfoList=[
            {"year": "2021", "employeeCount": 10},
            {"year": "2023", "employeeCount": 30},
            {"year": "2022", "employeeCount": 20},
        ]),
        today=TODAY,
    )

    assert profile["metrics"]["employeeCount"] == 30
    assert profile["metrics"]["metricsYear"] == "2023"


# --------------------------------------------------------------------------
# 用例 5：单位换算
# --------------------------------------------------------------------------


def test_营收元转万元():
    profile = build_profile(
        _record(operationInfoList=_operation(operatingIncome=23995200.0)), today=TODAY
    )

    assert profile["metrics"]["revenueWan"] == 2399.52


def test_主营业务收入元转万元且不复用营业收入():
    profile = build_profile(
        _record(operationInfoList=_operation(
            operatingIncome=10000000.0,
            mainBusinessIncome=23995200.0,
        )),
        today=TODAY,
    )

    assert profile["metrics"]["revenueWan"] == 1000.0
    assert profile["metrics"]["mainBusinessIncomeWan"] == 2399.52


def test_研发费与技术合同额同样转万元():
    profile = build_profile(
        _record(operationInfoList=_operation(rdExpense=7353900.0, techContractAmount=1000000.0)),
        today=TODAY,
    )

    assert profile["metrics"]["rdExpenseWan"] == 735.39
    assert profile["metrics"]["techContractWan"] == 100.0


def test_研发投入占比小数标准化为百分数():
    decimal_profile = build_profile(
        _record(operationInfoList=_operation(rdExpenseRatio="0.3065")), today=TODAY
    )
    percent_profile = build_profile(
        _record(operationInfoList=_operation(rdExpenseRatio="30.65%")), today=TODAY
    )

    assert decimal_profile["metrics"]["rdExpenseRatioPct"] == 30.65
    assert percent_profile["metrics"]["rdExpenseRatioPct"] == 30.65


def test_注册资本已是万元不再换算():
    profile = build_profile(_record(registerCapital=500.0), today=TODAY)

    assert profile["metrics"]["registerCapitalWan"] == 500.0


# --------------------------------------------------------------------------
# 用例 6：B 多归属
# --------------------------------------------------------------------------


def test_B维度可同时归入多个阶段():
    """docx 三信号「或」逻辑：成立 10 年命中 B3，营收 1000 万命中 B1。"""
    profile = build_profile(
        _record(
            registerDate="2016-01-01 00:00:00",
            operationInfoList=_operation(operatingIncome=10000000.0),
        ),
        today=TODAY,
    )

    assert "B3" in profile["tags"]["B"], "成立 10.6 年应命中 B3"
    assert "B1" in profile["tags"]["B"], "营收 1000 万应命中 B1"


def test_B维度依据可追溯():
    profile = build_profile(_record(), today=TODAY)
    evidence = profile["tagBasis"]["B"]

    assert evidence
    assert all("stage" in item and item["signals"] for item in evidence)


def test_B维度记录每个阶段的命中信号数():
    """实测 B2 命中率 100%，仅靠「是否命中」无区分度，计分需看命中强度。"""
    profile = build_profile(
        _record(
            registerDate="2016-10-13",
            operationInfoList=_operation(operatingIncome=23995200.0),
            honorQualificationList=[{"isTechSme": "是", "isHighTechEnterprise": "是"}],
        ),
        today=TODAY,
    )
    by_stage = {e["stage"]: e for e in profile["tagBasis"]["B"]}

    assert by_stage["B2"]["signalCount"] == 2, "营收 + 资质两个信号"
    assert by_stage["B3"]["signalCount"] == 1, "仅成立年限"


def test_B维度信号数与signals列表长度一致():
    profile = build_profile(_record(), today=TODAY)

    for item in profile["tagBasis"]["B"]:
        assert item["signalCount"] == len(item["signals"])


def test_资质命中B2():
    profile = build_profile(
        _record(honorQualificationList=[{"isHighTechEnterprise": "是"}]), today=TODAY
    )

    assert "B2" in profile["tags"]["B"]
    b2 = next(e for e in profile["tagBasis"]["B"] if e["stage"] == "B2")
    assert any("isHighTechEnterprise" in s for s in b2["signals"])


def test_股份有限公司命中B3():
    profile = build_profile(
        _record(registerType="股份有限公司（上市）"), today=TODAY
    )

    assert "B3" in profile["tags"]["B"]


def test_无任何资质时命中B1的无资质信号():
    """需要资质字段有值才能判定「无资质」，全 null 时不能下这个结论。"""
    profile = build_profile(
        _record(
            registerDate="2025-01-01 00:00:00",
            honorQualificationList=[{"isTechSme": "否", "isHighTechEnterprise": "否"}],
        ),
        today=TODAY,
    )

    b1 = next(e for e in profile["tagBasis"]["B"] if e["stage"] == "B1")
    assert any("无任何科技资质" in s for s in b1["signals"])


def test_资质全null时不产生无资质信号():
    profile = build_profile(_record(registerDate="2025-01-01 00:00:00"), today=TODAY)

    b1_items = [e for e in profile["tagBasis"]["B"] if e["stage"] == "B1"]
    if b1_items:
        assert not any("无任何科技资质" in s for s in b1_items[0]["signals"])


def test_三信号全缺时B为空():
    profile = build_profile(
        {"enterpriseName": "无信息企业", "registerAddress": "厦门市"}, today=TODAY
    )

    assert profile["tags"]["B"] == []
    assert profile["fieldAvailability"]["bTags"] == F.UNKNOWN


# --------------------------------------------------------------------------
# 用例 7：today 注入
# --------------------------------------------------------------------------


def test_成立年限用注入的today计算():
    """不用 date.today()，否则测试随时间失效。"""
    profile = build_profile(_record(registerDate="2016-10-13 00:00:00"), today=date(2026, 8, 3))

    assert profile["metrics"]["companyAgeYears"] == 9.8


def test_不同today得到不同年限():
    early = build_profile(_record(), today=date(2020, 1, 1))
    late = build_profile(_record(), today=date(2026, 8, 3))

    assert early["metrics"]["companyAgeYears"] < late["metrics"]["companyAgeYears"]


def test_多种日期格式都能解析():
    for raw in ("2016-10-13 00:00:00", "2016-10-13", "2016/10/13", "20161013"):
        profile = build_profile(_record(registerDate=raw), today=TODAY)
        assert profile["metrics"]["companyAgeYears"] == 9.8, raw


def test_日期无法解析时年限为None():
    profile = build_profile(_record(registerDate="不是日期"), today=TODAY)

    assert profile["metrics"]["companyAgeYears"] is None
    assert profile["fieldAvailability"]["companyAgeYears"] == F.UNKNOWN


# --------------------------------------------------------------------------
# 用例 8：知产三级回退
# --------------------------------------------------------------------------


def test_知产回退到ipCount():
    profile = build_profile(_record(ipCount=7), today=TODAY)

    assert profile["metrics"]["ipTotal"] == 7
    assert profile["metrics"]["ipSource"] == "ipCount"


def test_知产回退到inventionPatentCount():
    profile = build_profile(_record(inventionPatentCount=3), today=TODAY)

    assert profile["metrics"]["ipTotal"] == 3
    assert profile["metrics"]["ipSource"] == "inventionPatentCount"


def test_知产优先用ipClass1加2():
    profile = build_profile(
        _record(ipClass1Count=2, ipClass2Count=3, ipCount=99, inventionPatentCount=88),
        today=TODAY,
    )

    assert profile["metrics"]["ipTotal"] == 5
    assert profile["metrics"]["ipSource"] == "ipClass1+2"


def test_知产全缺时source为空():
    profile = build_profile(_record(), today=TODAY)

    assert profile["metrics"]["ipTotal"] is None
    assert profile["metrics"]["ipSource"] == ""


# --------------------------------------------------------------------------
# C 维度
# --------------------------------------------------------------------------


def test_C大类由行业映射得出():
    profile = build_profile(
        _record(industryLevel1="C 制造业", industryLevel2="医药制造业"), today=TODAY
    )

    assert profile["tags"]["C"] == ["C2"]
    assert profile["tags"]["cConfidence"] == "high"


def test_C子类由关键词命中():
    profile = build_profile(
        _record(
            industryLevel1="C 制造业",
            industryLevel2="计算机、通信和其他电子设备制造业",
            brief="从事集成电路设计与晶圆制造",
        ),
        today=TODAY,
    )

    assert "C1.2" in profile["tags"]["cSub"]
    assert "C1" in profile["tags"]["C"]


def test_子类命中时反推大类():
    """行业字段缺失但文本命中子类，应补上对应大类。"""
    profile = build_profile(_record(brief="专注人工智能大模型研发"), today=TODAY)

    assert "C10.1" in profile["tags"]["cSub"]
    assert "C10" in profile["tags"]["C"]


def test_口径过宽的行业落C9并说明原因():
    profile = build_profile(
        _record(industryLevel1="M 科学研究和技术服务业", industryLevel2="专业技术服务业"),
        today=TODAY,
    )

    assert profile["tags"]["C"] == ["C9"]
    assert any("口径过宽" in b for b in profile["tagBasis"]["C"])


def test_行业字段全空时C为空而非C9():
    """「不知道行业」与「已知为通用行业」语义不同，前者要剔除分母。"""
    profile = build_profile(_record(), today=TODAY)

    assert profile["tags"]["C"] == []
    assert profile["fieldAvailability"]["cTags"] == F.UNKNOWN


def test_C依据可追溯():
    profile = build_profile(
        _record(industryLevel2="软件和信息技术服务业", brief="软件开发服务"), today=TODAY
    )

    assert profile["tagBasis"]["C"]


def test_子类文本源可用性被标记():
    with_text = build_profile(_record(brief="芯片设计"), today=TODAY)
    without_text = build_profile(_record(), today=TODAY)

    assert with_text["fieldAvailability"]["cSubTagTextSource"] == F.AVAILABLE
    assert without_text["fieldAvailability"]["cSubTagTextSource"] == F.UNKNOWN


# --------------------------------------------------------------------------
# C 子类强弱两档
# --------------------------------------------------------------------------


def test_仅行业名含关键词时算弱子类():
    """「软件和信息技术服务业」含「软件」，但这只说明行业口径沾边，
    不等于企业真做软件开发。实测会让 461 家企业全部命中，故降为弱档。"""
    profile = build_profile(
        _record(industryLevel2="软件和信息技术服务业"), today=TODAY
    )

    assert profile["tags"]["cSubWeak"] == ["C1.3"]
    assert profile["tags"]["cSubStrong"] == []
    assert "C1.3" in profile["tags"]["cSub"]


def test_业务描述命中时算强子类():
    profile = build_profile(
        _record(industryLevel2="软件和信息技术服务业", brief="专注企业级软件开发"),
        today=TODAY,
    )

    assert profile["tags"]["cSubStrong"] == ["C1.3"]
    assert profile["tags"]["cSubWeak"] == []


def test_同一子类同时被业务描述和行业名命中时只算强档():
    """不重复计入，避免同一子类既算 30 又算 25。"""
    profile = build_profile(
        _record(industryLevel2="软件和信息技术服务业", brief="软件开发服务"),
        today=TODAY,
    )

    assert "C1.3" in profile["tags"]["cSubStrong"]
    assert "C1.3" not in profile["tags"]["cSubWeak"]


def test_业务描述与行业名指向不同子类时各归其档():
    profile = build_profile(
        _record(industryLevel2="软件和信息技术服务业", brief="从事集成电路EDA工具研发"),
        today=TODAY,
    )

    assert profile["tags"]["cSubStrong"] == ["C1.2"]
    assert profile["tags"]["cSubWeak"] == ["C1.3"]


def test_强弱子类依据文案可区分():
    profile = build_profile(
        _record(industryLevel2="软件和信息技术服务业", brief="集成电路设计"),
        today=TODAY,
    )
    basis_text = " ".join(profile["tagBasis"]["C"])

    assert "强子类" in basis_text
    assert "弱子类" in basis_text
    assert "未见业务描述佐证" in basis_text


def test_cSub是强弱两档的并集():
    profile = build_profile(
        _record(industryLevel2="软件和信息技术服务业", brief="集成电路设计"),
        today=TODAY,
    )
    tags = profile["tags"]

    assert set(tags["cSub"]) == set(tags["cSubStrong"]) | set(tags["cSubWeak"])


def test_子类文本源包含产品与技术子表():
    profile = build_profile(
        _record(
            mainProductList=[{"productName": "显示面板", "productDescription": "OLED 模组"}],
            mainTechnologyList=[{"patentName": "背光技术", "brief": "光电器件"}],
        ),
        today=TODAY,
    )

    assert "C1.1" in profile["tags"]["cSub"]


# --------------------------------------------------------------------------
# 属地
# --------------------------------------------------------------------------


def test_属地三级回退到address():
    profile = build_profile(_record(registerAddress="厦门市集美区某路1号"), today=TODAY)

    assert profile["tags"]["regions"] == ["集美区"]


def test_属地行政区与园区叠加():
    profile = build_profile(
        _record(registerAddress="厦门市湖里区火炬高新区创新二路1号"), today=TODAY
    )

    assert "湖里区" in profile["tags"]["regions"]
    assert "火炬高新区" in profile["tags"]["regions"]


def test_属地优先用district字段():
    profile = build_profile(
        _record(registerDistrict="厦门市/海沧区", registerAddress="无法识别的地址"),
        today=TODAY,
    )

    assert profile["tags"]["regions"] == ["海沧区"]


def test_属地全无法识别时为空():
    profile = build_profile(_record(registerAddress="某省某市某路"), today=TODAY)

    assert profile["tags"]["regions"] == []
    assert profile["fieldAvailability"]["regions"] == F.UNKNOWN


# --------------------------------------------------------------------------
# 信用
# --------------------------------------------------------------------------


def test_信用字段为空时checked为false():
    """enterpriseCredit 实测 100% 为空，必须记「未校验」而非「通过」。"""
    profile = build_profile(_record(), today=TODAY)

    assert profile["credit"]["checked"] is False
    assert profile["credit"]["negative"] is None


def test_信用有负面记录时negative为true():
    profile = build_profile(
        _record(operationInfoList=_operation(enterpriseCredit="存在经营异常")), today=TODAY
    )

    assert profile["credit"]["checked"] is True
    assert profile["credit"]["negative"] is True
    assert "经营异常" in profile["credit"]["matchedWords"]


def test_信用良好时negative为false():
    profile = build_profile(
        _record(operationInfoList=_operation(enterpriseCredit="信用良好")), today=TODAY
    )

    assert profile["credit"]["checked"] is True
    assert profile["credit"]["negative"] is False


# --------------------------------------------------------------------------
# 可用性汇总
# --------------------------------------------------------------------------


def test_fieldAvailability含全部下游依赖字段():
    profile = build_profile(_record(), today=TODAY)
    availability = profile["fieldAvailability"]

    for field in (
        "registerDate", "registerAddress", "industryLevel1", "industryLevel2",
        "ipClass1Count", "ipClass2Count", "employeeCount", "operatingIncome",
        "rdExpense", "techContractAmount", "enterpriseCredit", "registerType",
        "registerDistrict", "nationalHighTechZone", "isTechSme", "isInnovativeSme",
        "cTags", "bTags", "regions",
    ):
        assert field in availability, f"缺 {field} 的可用性登记"


def test_dataCompleteness三项自洽():
    profile = build_profile(_record(), today=TODAY)
    completeness = profile["dataCompleteness"]

    assert completeness["available"] + completeness["unknown"] == completeness["total"]


def test_字段越全available越多():
    minimal = build_profile(_record(), today=TODAY)
    rich = build_profile(
        _record(
            industryLevel1="C 制造业",
            industryLevel2="医药制造业",
            ipClass1Count=5,
            ipClass2Count=3,
            registerType="有限责任公司",
            registerDistrict="厦门市/思明区",
            operationInfoList=_operation(
                employeeCount=100, operatingIncome=50000000.0, rdExpense=5000000.0
            ),
            honorQualificationList=[{"isTechSme": "是", "isHighTechEnterprise": "是"}],
        ),
        today=TODAY,
    )

    assert rich["dataCompleteness"]["available"] > minimal["dataCompleteness"]["available"]


def test_unknownFields列出缺失字段名():
    profile = build_profile(_record(), today=TODAY)

    assert "enterpriseCredit" in profile["unknownFields"]
    assert "industryLevel2" in profile["unknownFields"]


def test_画像携带availability对象供下游复用():
    profile = build_profile(_record(), today=TODAY)

    assert isinstance(profile["_availability"], F.Availability)


# --------------------------------------------------------------------------
# 全量样本不崩
# --------------------------------------------------------------------------


def test_全量样本前200份都不抛异常():
    sample_dir = sample_enterprise_dir()
    if not sample_dir.exists():
        pytest.skip(f"样本目录不存在：{sample_dir}")

    checked = 0
    for path in sorted(sample_dir.glob("*.json"))[:200]:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or "industryLevel1" not in record:
            continue
        if not F.text(record.get("enterpriseName")):
            continue
        profile = build_profile(record, today=TODAY)
        assert profile["enterpriseName"]
        checked += 1

    assert checked > 0, "没有可用样本"
