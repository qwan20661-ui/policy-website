# -*- coding: utf-8 -*-
"""taxonomy 模块测试。对应 docs/一期_确定性底座开发流程.md 第 3.3 节测试要求。"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from service import taxonomy as tx


# --------------------------------------------------------------------------
# 配置加载与 schema 校验
# --------------------------------------------------------------------------


def test_三个配置文件都能加载():
    assert tx.load_taxonomy()["schemaVersion"] == tx.TAXONOMY_SCHEMA
    assert tx.load_industry_map()["schemaVersion"] == tx.INDUSTRY_MAP_SCHEMA
    assert tx.load_keyword_library()["schemaVersion"] == tx.KEYWORD_LIBRARY_SCHEMA


def test_schema版本不匹配时抛错并指明文件路径(tmp_path):
    bad = tmp_path / "bad_taxonomy.json"
    bad.write_text(json.dumps({"schemaVersion": "wrong-version"}), encoding="utf-8")
    with patch.object(tx, "TAXONOMY_PATH", bad):
        tx.reset_caches()
        with pytest.raises(ValueError) as exc:
            tx.load_taxonomy()
        assert "schemaVersion" in str(exc.value)
        assert str(bad) in str(exc.value)
    tx.reset_caches()


def test_配置文件缺失时抛错并给出生成提示(tmp_path):
    missing = tmp_path / "not_exist.json"
    with patch.object(tx, "INDUSTRY_MAP_PATH", missing):
        tx.reset_caches()
        with pytest.raises(FileNotFoundError) as exc:
            tx.load_industry_map()
        assert "run_build_industry_map" in str(exc.value)
    tx.reset_caches()


def test_配置文件不是合法json时抛错(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with patch.object(tx, "KEYWORD_LIBRARY_PATH", broken):
        tx.reset_caches()
        with pytest.raises(ValueError) as exc:
            tx.load_keyword_library()
        assert "合法 JSON" in str(exc.value)
    tx.reset_caches()


# --------------------------------------------------------------------------
# 枚举校验：防政策库词表污染匹配轴（开发计划决策 10）
# --------------------------------------------------------------------------


def test_C维度合法编号包含11个大类与4个子类():
    tags = tx.valid_tags("C")
    for major in [f"C{i}" for i in range(1, 12)]:
        assert major in tags
    for sub in ("C1.1", "C1.2", "C1.3", "C10.1"):
        assert sub in tags
    assert len(tags) == 15


def test_C维度拒绝政策库原始词表值():
    """这是二期打标校验依赖的关键断言。"""
    for illegal in ("通用行业", "半导体和集成电路", "生物医药", "新材料", "电子信息-新型显示"):
        assert tx.is_valid_tag("C", illegal) is False


def test_is_valid_tag对空值与非字符串返回False():
    assert tx.is_valid_tag("C", "") is False
    assert tx.is_valid_tag("C", "   ") is False
    assert tx.is_valid_tag("C", None) is False


def test_is_valid_tag忽略首尾空格():
    assert tx.is_valid_tag("C", "  C1.2  ") is True


def test_未定义维度返回False而非抛错():
    assert tx.is_valid_tag("Z", "Z1") is False


def test_valid_tags可排除子类():
    assert "C1.2" not in tx.valid_tags("C", include_sub=False)
    assert "C1" in tx.valid_tags("C", include_sub=False)


def test_sub_tag_parent():
    assert tx.sub_tag_parent("C1.2") == "C1"
    assert tx.sub_tag_parent("C10.1") == "C10"
    assert tx.sub_tag_parent("C1") == ""
    assert tx.sub_tag_parent("不存在") == ""


# --------------------------------------------------------------------------
# validate_tag_payload
# --------------------------------------------------------------------------


def _valid_payload():
    return {
        "A": {"primary": "A1", "secondary": ["A3"], "confidence": "high", "basis": "支持方式为资质认定"},
        "B": {"values": ["B1", "B2"], "confidence": "medium", "basis": "未限定年限"},
        "C": {"values": ["C9"], "subValues": [], "confidence": "high", "basis": "通用行业无产业限定"},
        "D": {"value": "D1", "confidence": "high", "basis": "入库即享"},
        "E": {"value": None, "confidence": "low", "basis": "未给金额"},
        "F": {"value": "F3", "confidence": "medium", "basis": "年度申报"},
    }


def test_合法payload无错误():
    assert tx.validate_tag_payload(_valid_payload()) == []


def test_payload里C输出政策库词表值被拒绝():
    payload = _valid_payload()
    payload["C"]["values"] = ["通用行业"]
    errors = tx.validate_tag_payload(payload)
    assert any("C.values" in e for e in errors)


def test_payload里basis为空被拒绝():
    payload = _valid_payload()
    payload["A"]["basis"] = "   "
    errors = tx.validate_tag_payload(payload)
    assert any("A.basis" in e for e in errors)


def test_payload里置信度非法被拒绝():
    payload = _valid_payload()
    payload["D"]["confidence"] = "very_high"
    errors = tx.validate_tag_payload(payload)
    assert any("D.confidence" in e for e in errors)


def test_payload缺必填维度被拒绝():
    payload = _valid_payload()
    del payload["C"]
    errors = tx.validate_tag_payload(payload)
    assert any("缺少必填维度 C" in e for e in errors)


def test_payload缺可选维度不报错():
    payload = _valid_payload()
    del payload["E"]
    del payload["F"]
    assert tx.validate_tag_payload(payload) == []


def test_payload里subValues放了大类编号被拒绝():
    payload = _valid_payload()
    payload["C"]["subValues"] = ["C1"]
    errors = tx.validate_tag_payload(payload)
    assert any("不是子类编号" in e for e in errors)


def test_payload非字典直接返回错误():
    assert tx.validate_tag_payload("not a dict") == ["tags 必须是对象"]


# --------------------------------------------------------------------------
# resolve_c_tags：国标行业 → C 大类，三级优先
# --------------------------------------------------------------------------


def test_resolve_c_tags优先用level2():
    tags, confidence = tx.resolve_c_tags("C 制造业", "医药制造业")
    assert tags == ["C2"]
    assert confidence == "high"


def test_resolve_c_tags在level2未命中时降级level1():
    tags, confidence = tx.resolve_c_tags("I 信息传输、软件和信息技术服务业", "不存在的二级行业")
    assert "C1" in tags
    assert confidence in ("high", "medium", "low")


def test_resolve_c_tags两级都未命中返回none():
    tags, confidence = tx.resolve_c_tags("不存在的门类", "不存在的二级")
    assert tags == []
    assert confidence == "none"


def test_resolve_c_tags对空输入返回none():
    """空值与「查不到」要区分：空值表示字段缺失，由调用方记 unknown。"""
    tags, confidence = tx.resolve_c_tags("", "")
    assert tags == []
    assert confidence == "none"


def test_resolve_c_tags在口径过宽的行业上返回空但置信度不是none():
    """专业技术服务业这类口径过宽的，映射表里有记录但 cTags 为空，
    语义是「明确落通用」，与「查不到」不同。"""
    tags, confidence = tx.resolve_c_tags("M 科学研究和技术服务业", "专业技术服务业")
    assert tags == []
    assert confidence != "none"


# --------------------------------------------------------------------------
# match_c_subtags
# --------------------------------------------------------------------------


def test_match_c_subtags基础命中():
    assert tx.match_c_subtags("从事集成电路设计") == ["C1.2"]


def test_match_c_subtags可命中多个子类():
    result = tx.match_c_subtags("既做OLED面板也做人工智能算法")
    assert "C1.1" in result
    assert "C10.1" in result


def test_match_c_subtags大小写不敏感():
    assert tx.match_c_subtags("采用 oled 技术") == ["C1.1"]
    assert tx.match_c_subtags("使用 saas 模式") == ["C1.3"]


def test_match_c_subtags英文短词要求词边界():
    """AI 不能命中 CHAIN、MAINTAIN；EDA 不能命中 EDABC。"""
    assert tx.match_c_subtags("SUPPLY CHAIN MAINTAIN") == []
    assert tx.match_c_subtags("EDABCD") == []
    assert tx.match_c_subtags("专注 AI 领域") == ["C10.1"]


def test_match_c_subtags空文本返回空():
    assert tx.match_c_subtags("") == []
    assert tx.match_c_subtags(None) == []
    assert tx.match_c_subtags("   ") == []


def test_match_c_subtags带命中词明细():
    hits = tx.match_c_subtags("芯片与晶圆制造", with_hits=True)
    assert set(hits["C1.2"]) == {"芯片", "晶圆"}


def test_关键词库不含已剔除的0命中词():
    """一期确立的原则：hits=0 的词不入库。"""
    library = tx.load_keyword_library()
    all_words = {
        str(entry.get("kw"))
        for entries in (library.get("cSubTags") or {}).values()
        for entry in entries
    }
    for removed in ("IP核", "封测", "CMMI", "智能算法", "CAR-T", "钠电"):
        assert removed not in all_words


def test_关键词库每个词都有来源标记():
    library = tx.load_keyword_library()
    for sub_tag, entries in (library.get("cSubTags") or {}).items():
        for entry in entries:
            assert entry.get("source"), f"{sub_tag} 的 {entry.get('kw')} 缺 source"


def test_关键词库子类只有docx明文定义的4个():
    """决策 11：不自行扩充子类。"""
    library = tx.load_keyword_library()
    assert set((library.get("cSubTags") or {}).keys()) == {"C1.1", "C1.2", "C1.3", "C10.1"}


# --------------------------------------------------------------------------
# match_regions：三级回退
# --------------------------------------------------------------------------


def test_match_regions仅有address也能出结果():
    """registerAddress 覆盖率 100%，是属地判定的兜底源。"""
    assert tx.match_regions(address="厦门市思明区高雄路20号") == ["思明区"]


def test_match_regions行政区与园区叠加():
    """docx R13/R14 的市区园区三级叠加语义。"""
    result = tx.match_regions(address="厦门市湖里区火炬高新区创新二路1号")
    assert "湖里区" in result
    assert "火炬高新区" in result


def test_match_regions从district取值():
    assert tx.match_regions(district="厦门市/集美区") == ["集美区"]


def test_match_regions从zone取值():
    assert tx.match_regions(zone="厦门火炬高新区") == ["火炬高新区"]


def test_match_regions火炬变体写法():
    assert "火炬高新区" in tx.match_regions(address="火炬园内")


def test_match_regions全空返回空():
    assert tx.match_regions() == []
    assert tx.match_regions("", "", "") == []


def test_match_regions结果去重且稳定排序():
    first = tx.match_regions(district="思明区", address="思明区某路")
    second = tx.match_regions(district="思明区", address="思明区某路")
    assert first == second == ["思明区"]


# --------------------------------------------------------------------------
# 信用负面
# --------------------------------------------------------------------------


def test_has_credit_negative命中():
    hit, words = tx.has_credit_negative("该企业存在经营异常且被列入异常名录")
    assert hit is True
    assert "经营异常" in words


def test_has_credit_negative未命中():
    hit, words = tx.has_credit_negative("信用良好，无违法记录")
    assert hit is False
    assert words == []


def test_has_credit_negative空值不视为负面():
    """enterpriseCredit 实测 100% 为空，空值必须走「未校验」而非「有负面」。"""
    assert tx.has_credit_negative("") == (False, [])
    assert tx.has_credit_negative(None) == (False, [])


# --------------------------------------------------------------------------
# 评分参数
# --------------------------------------------------------------------------


def test_scoring_config含五项基础权重且合计100():
    weights = tx.scoring_config()["baseWeights"]
    assert set(weights) == {
        "industryMatch", "stageMatch", "innovationActivity", "scaleMatch", "creditRecord",
    }
    assert sum(weights.values()) == 100


def test_scoring_config含领域匹配五档():
    """docx 原为三档。本方案两处调整见开发计划 3.1.1：
    (1) 加 majorTagExactIntersect=25，因只有 4 个子类，需不依赖子类的区分档；
    (2) 子类分强弱——业务描述命中 30，仅行业名命中 25。
    """
    scores = tx.scoring_config()["industryMatchScores"]
    assert scores["strongSubTagIntersect"] == 30
    assert scores["weakSubTagIntersect"] == 25
    assert scores["majorTagExactIntersect"] == 25
    assert scores["genericOnly"] == 10
    assert scores["noIntersect"] == 0


def test_scoring_config含发展阶段按信号数计分():
    """实测 B2 命中率 100%，按信号数计分才有区分度。"""
    by_signal = tx.scoring_config()["stageMatchScores"]["bySignalCount"]
    assert by_signal["3"] == 20
    assert by_signal["2"] == 15
    assert by_signal["1"] == 10


def test_scoring_config附加分封顶50():
    assert tx.scoring_config()["bonus"]["cap"] == 50


def test_scoring_config阈值为85与60():
    thresholds = tx.scoring_config()["thresholds"]
    assert thresholds["directPush"] == 85
    assert thresholds["needsSupplement"] == 60


def test_B维度含相邻阶段定义():
    """发展阶段匹配的「相邻阶段得 10 分」需要相邻关系定义。"""
    adjacency = tx.load_taxonomy()["dimensions"]["B"]["adjacency"]
    assert adjacency["B1"] == ["B2"]
    assert set(adjacency["B2"]) == {"B1", "B3"}


def test_B维度三信号阈值与docx一致():
    signals = tx.load_taxonomy()["dimensions"]["B"]["signals"]
    assert signals["B1"]["yearsMax"] == 3
    assert signals["B1"]["revenueMaxWan"] == 2000
    assert signals["B2"]["yearsMin"] == 3
    assert signals["B2"]["yearsMax"] == 7
    assert signals["B3"]["yearsMin"] == 7
    assert signals["B3"]["revenueMinWan"] == 20000


def test_better_confidence():
    assert tx.better_confidence("high", "low") == "high"
    assert tx.better_confidence("low", "medium") == "medium"
    assert tx.better_confidence("high", "high") == "high"
