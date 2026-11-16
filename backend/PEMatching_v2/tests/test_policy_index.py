# -*- coding: utf-8 -*-
"""政策索引与检索测试。

对应 docs/二期_政策打标开发流程.md 第 6.4 节测试要求。
检索原则「取交集但不苛刻」是本模块的核心，多数用例在验证这一点。
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest

from service import policy_index as pi
from service.scoring import (
    TIMELINESS_IN_PROGRESS,
    TIMELINESS_NOT_STARTED,
    TIMELINESS_PAST_YEAR,
    TIMELINESS_UNKNOWN,
    resolve_timeliness,
)

TODAY = date(2026, 8, 3)


def _tagged(policy_id, c_values=("C9",), c_subs=(), a_primary="A3",
            b_values=("B2",), d_value="D2", confidence="high",
            applicable=True, title=None, keyword_index=None, window=None):
    return {
        "fusionPolicyId": policy_id,
        "title": title or f"政策{policy_id}",
        "tags": {
            "A": {"primary": a_primary, "secondary": [], "confidence": confidence, "basis": "x"},
            "B": {"values": list(b_values), "confidence": confidence, "basis": "x"},
            "C": {"values": list(c_values), "subValues": list(c_subs),
                  "confidence": confidence, "basis": "x"},
            "D": {"value": d_value, "confidence": confidence, "basis": "x"},
            "E": {"value": None, "confidence": "low", "basis": "x"},
            "F": {"value": "F3", "confidence": "high", "basis": "x"},
        },
        "applicability": {
            "applicable": applicable,
            "reason": "企业可申报" if applicable else "面向个人专家",
            "audience": "企业" if applicable else "个人",
        },
        "applicationWindow": window or {
            "startDate": "2026-09-01", "endDate": "2026-11-30", "isRolling": False,
            "confidence": "high", "basis": "结构化字段", "source": "structured_field",
        },
        "growthChain": {"prerequisite": None, "nextStage": None, "basis": "不属链路"},
        "keywordIndex": keyword_index or {"title": [], "aKeywords": [], "cKeywords": []},
        "notes": "",
        "_source": "llm",
    }


def _snapshot(policy_id, scope="市级-厦门市", summary="摘要"):
    return {
        "fusionPolicyId": policy_id,
        "title": f"政策{policy_id}",
        "policyInfo": {
            "policyTitle": f"政策{policy_id}",
            "summary": summary,
            "applicableScopePath": scope,
            "industryField": "通用行业",
        },
        "supportMethods": [{"supportType": "一次性奖励补助"}],
        "structuredConditions": [{"applicationRequirement": "需具备资质"}],
        "materials": [{"materials": "申报书"}],
        "keywords": [],
    }


def _install(monkeypatch, tagged, snapshots=None):
    """把索引替换为测试数据。"""
    tags = {r["fusionPolicyId"]: r for r in tagged}
    snaps = {s["fusionPolicyId"]: s for s in (snapshots or [_snapshot(k) for k in tags])}
    monkeypatch.setattr(pi, "load_policy_tags", lambda: tags)
    monkeypatch.setattr(pi, "_snapshot_index", lambda: snaps)
    pi._scope_index.cache_clear()
    return tags, snaps


# --------------------------------------------------------------------------
# 加载与 schema
# --------------------------------------------------------------------------


def test_标签文件不存在时提示先打标(tmp_path, monkeypatch):
    monkeypatch.setattr(pi, "POLICY_TAGS_PATH", tmp_path / "missing.json")
    pi.load_policy_tags.cache_clear()

    with pytest.raises(pi.PolicyIndexError) as exc:
        pi.load_policy_tags()

    assert "run_policy_tagging" in str(exc.value)
    pi.load_policy_tags.cache_clear()


def test_schema不匹配时抛错(tmp_path, monkeypatch):
    path = tmp_path / "tags.json"
    path.write_text(json.dumps({"schemaVersion": "old", "data": []}), encoding="utf-8")
    monkeypatch.setattr(pi, "POLICY_TAGS_PATH", path)
    monkeypatch.setattr(pi, "POLICY_TAGS_OVERRIDES_PATH", tmp_path / "no_override.json")
    pi.load_policy_tags.cache_clear()

    with pytest.raises(pi.PolicyIndexError) as exc:
        pi.load_policy_tags()

    assert "schemaVersion" in str(exc.value)
    pi.load_policy_tags.cache_clear()


# --------------------------------------------------------------------------
# 人工 overrides：与 LLM 产出分离，重打标不丢
# --------------------------------------------------------------------------


def test_人工overrides覆盖LLM产出(tmp_path, monkeypatch):
    tags_path = tmp_path / "tags.json"
    tags_path.write_text(json.dumps({
        "schemaVersion": pi.TAGS_SCHEMA,
        "data": [_tagged("6548", c_values=("C9",))],
    }, ensure_ascii=False), encoding="utf-8")
    override_path = tmp_path / "overrides.json"
    override_path.write_text(json.dumps({
        "data": [{
            "fusionPolicyId": "6548",
            "tags": {"C": {"values": ["C2"], "subValues": [], "confidence": "high",
                           "basis": "人工修正为生物医药"}},
        }],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(pi, "POLICY_TAGS_PATH", tags_path)
    monkeypatch.setattr(pi, "POLICY_TAGS_OVERRIDES_PATH", override_path)
    pi.load_policy_tags.cache_clear()

    records = pi.load_policy_tags()

    assert records["6548"]["tags"]["C"]["values"] == ["C2"]
    assert records["6548"]["_source"] == "manual"
    pi.load_policy_tags.cache_clear()


def test_政策原文修正可标记为非人工来源(tmp_path, monkeypatch):
    tags_path = tmp_path / "tags.json"
    tags_path.write_text(json.dumps({
        "schemaVersion": pi.TAGS_SCHEMA, "data": [_tagged("6548")],
    }, ensure_ascii=False), encoding="utf-8")
    override_path = tmp_path / "overrides.json"
    override_path.write_text(json.dumps({
        "data": [{"fusionPolicyId": "6548", "sourceKind": "policy_source",
                  "tags": {"B": {"values": ["B1"], "confidence": "high", "basis": "原文"}}}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(pi, "POLICY_TAGS_PATH", tags_path)
    monkeypatch.setattr(pi, "POLICY_TAGS_OVERRIDES_PATH", override_path)
    pi.load_policy_tags.cache_clear()

    assert pi.load_policy_tags()["6548"]["_source"] == "policy_source"
    pi.load_policy_tags.cache_clear()


def test_overrides只覆盖指定维度(tmp_path, monkeypatch):
    """未提及的维度保留 LLM 产出，不被整条替换。"""
    tags_path = tmp_path / "tags.json"
    tags_path.write_text(json.dumps({
        "schemaVersion": pi.TAGS_SCHEMA,
        "data": [_tagged("6548", a_primary="A3")],
    }, ensure_ascii=False), encoding="utf-8")
    override_path = tmp_path / "overrides.json"
    override_path.write_text(json.dumps({
        "data": [{"fusionPolicyId": "6548",
                  "tags": {"C": {"values": ["C2"], "confidence": "high", "basis": "修正"}}}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(pi, "POLICY_TAGS_PATH", tags_path)
    monkeypatch.setattr(pi, "POLICY_TAGS_OVERRIDES_PATH", override_path)
    pi.load_policy_tags.cache_clear()

    record = pi.load_policy_tags()["6548"]

    assert record["tags"]["C"]["values"] == ["C2"]
    assert record["tags"]["A"]["primary"] == "A3", "未覆盖的维度应保留"
    pi.load_policy_tags.cache_clear()


def test_overrides文件不存在时不报错(tmp_path, monkeypatch):
    tags_path = tmp_path / "tags.json"
    tags_path.write_text(json.dumps({
        "schemaVersion": pi.TAGS_SCHEMA, "data": [_tagged("6548")],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(pi, "POLICY_TAGS_PATH", tags_path)
    monkeypatch.setattr(pi, "POLICY_TAGS_OVERRIDES_PATH", tmp_path / "none.json")
    pi.load_policy_tags.cache_clear()

    assert pi.load_policy_tags()["6548"]["_source"] == "llm"
    pi.load_policy_tags.cache_clear()


# --------------------------------------------------------------------------
# 不可申报政策默认排除
# --------------------------------------------------------------------------


def test_不可申报政策默认被排除(monkeypatch):
    """专家征集、报告报送这类不该推给企业。"""
    _install(monkeypatch, [
        _tagged("A", applicable=True), _tagged("B", applicable=False),
    ])

    results = pi.search_policies(c_tags=["C9"])

    assert [r["fusionPolicyId"] for r in results] == ["A"]


def test_显式要求时可包含不可申报政策(monkeypatch):
    _install(monkeypatch, [_tagged("A"), _tagged("B", applicable=False)])

    results = pi.search_policies(c_tags=["C9"], include_not_applicable=True)

    assert len(results) == 2


def test_缺applicability时按可申报处理(monkeypatch):
    """不因缺字段而漏推。"""
    record = _tagged("A")
    del record["applicability"]
    _install(monkeypatch, [record])

    assert len(pi.search_policies(c_tags=["C9"])) == 1


# --------------------------------------------------------------------------
# 取交集但不苛刻
# --------------------------------------------------------------------------


def test_C9通用政策对任意c_tags查询保留但不算领域命中(monkeypatch):
    """C9 是全行业普惠召回兜底，不能被产业查询过滤，也不是领域证据。"""
    _install(monkeypatch, [_tagged("A", c_values=("C9",))])

    for wanted in (["C1"], ["C2"], ["C8"], ["C10"]):
        results = pi.search_policies(c_tags=wanted)
        assert len(results) == 1, wanted
        assert "C" not in results[0]["matchedDimensions"]


def test_政策无B标签时不被b_tags过滤(monkeypatch):
    _install(monkeypatch, [_tagged("A", b_values=())])

    assert len(pi.search_policies(b_tags=["B2"])) == 1


def test_low置信度维度不参与过滤(monkeypatch):
    """低置信度标签不可靠，只影响排序不做硬过滤。"""
    _install(monkeypatch, [_tagged("A", c_values=("C1",), confidence="low")])

    results = pi.search_policies(c_tags=["C2"])

    assert len(results) == 1
    assert "C" not in results[0]["matchedDimensions"]


def test_high置信度不匹配时被过滤(monkeypatch):
    _install(monkeypatch, [_tagged("A", c_values=("C1",), confidence="high")])

    assert pi.search_policies(c_tags=["C2"]) == []


def test_未指定维度时不过滤(monkeypatch):
    _install(monkeypatch, [_tagged("A", c_values=("C1",))])

    assert len(pi.search_policies()) == 1


def test_D门槛过滤生效(monkeypatch):
    _install(monkeypatch, [_tagged("A", d_value="D1"), _tagged("B", d_value="D3")])

    results = pi.search_policies(d_levels=["D1"])

    assert [r["fusionPolicyId"] for r in results] == ["A"]


def test_A支持方式过滤生效(monkeypatch):
    _install(monkeypatch, [_tagged("A", a_primary="A1"), _tagged("B", a_primary="A3")])

    results = pi.search_policies(a_tags=["A1"])

    assert [r["fusionPolicyId"] for r in results] == ["A"]


# --------------------------------------------------------------------------
# 属地筛选
# --------------------------------------------------------------------------


def test_parse_scope识别全市通用层级():
    for scope in ("市级-厦门市", "省级-福建省", "国家级"):
        universal, regions = pi.parse_scope(scope)
        assert universal is True, scope
        assert regions == []


def test_parse_scope识别区级限定():
    universal, regions = pi.parse_scope("集美区")

    assert universal is False
    assert "集美区" in regions


def test_parse_scope空值按通用处理():
    """不因缺字段而排除政策。"""
    universal, regions = pi.parse_scope("")

    assert universal is True


def test_区级政策只推给本区企业(monkeypatch):
    _install(
        monkeypatch,
        [_tagged("A"), _tagged("B")],
        [_snapshot("A", scope="集美区"), _snapshot("B", scope="市级-厦门市")],
    )

    results = pi.search_policies(c_tags=["C9"], region_tags=["思明区"])

    assert [r["fusionPolicyId"] for r in results] == ["B"]


def test_区级政策推给本区企业时算命中(monkeypatch):
    _install(monkeypatch, [_tagged("A")], [_snapshot("A", scope="集美区")])

    results = pi.search_policies(c_tags=["C9"], region_tags=["集美区"])

    assert len(results) == 1
    assert "属地" in results[0]["matchedDimensions"]


def test_市级政策对任意属地都保留(monkeypatch):
    _install(monkeypatch, [_tagged("A")], [_snapshot("A", scope="市级-厦门市")])

    for region in (["思明区"], ["集美区"], []):
        assert len(pi.search_policies(c_tags=["C9"], region_tags=region)) == 1


# --------------------------------------------------------------------------
# 关键词只影响排序
# --------------------------------------------------------------------------


def test_关键词不做硬过滤(monkeypatch):
    """打错字不该让结果全空。"""
    _install(monkeypatch, [_tagged("A")])

    assert len(pi.search_policies(c_tags=["C9"], keyword="不存在的词")) == 1


def test_关键词命中提升排序(monkeypatch):
    _install(monkeypatch, [
        _tagged("A"),
        _tagged("B", keyword_index={"title": ["研发补助"], "aKeywords": [], "cKeywords": []}),
    ])

    results = pi.search_policies(c_tags=["C9"], keyword="研发补助")

    assert results[0]["fusionPolicyId"] == "B"
    assert "关键词" in results[0]["matchedDimensions"]


# --------------------------------------------------------------------------
# 排序与稳定性
# --------------------------------------------------------------------------


def test_命中维度多的排前面(monkeypatch):
    _install(monkeypatch, [
        _tagged("A", c_values=("C9",), b_values=(), d_value=None),
        _tagged("B", c_values=("C1",), b_values=("B2",), d_value="D1"),
    ])

    results = pi.search_policies(c_tags=["C1"], b_tags=["B2"], d_levels=["D1"])

    assert results[0]["fusionPolicyId"] == "B"


def test_子类命中权重高于大类(monkeypatch):
    _install(monkeypatch, [
        _tagged("A", c_values=("C1",), c_subs=()),
        _tagged("B", c_values=("C1",), c_subs=("C1.3",)),
    ])

    results = pi.search_policies(c_tags=["C1"], c_sub_tags=["C1.3"])

    assert results[0]["fusionPolicyId"] == "B"
    assert "C子类" in results[0]["matchedDimensions"]


def test_排序稳定可复现(monkeypatch):
    """三期双模式比对依赖检索结果可复现。"""
    _install(monkeypatch, [_tagged(str(i)) for i in range(10)])

    first = [r["fusionPolicyId"] for r in pi.search_policies(c_tags=["C9"], limit=5)]
    second = [r["fusionPolicyId"] for r in pi.search_policies(c_tags=["C9"], limit=5)]

    assert first == second


def test_limit生效(monkeypatch):
    _install(monkeypatch, [_tagged(str(i)) for i in range(30)])

    assert len(pi.search_policies(c_tags=["C9"], limit=5)) == 5


# --------------------------------------------------------------------------
# 摘要不含全文
# --------------------------------------------------------------------------


def test_摘要不含申报条件全文(monkeypatch):
    """防 Agent 上下文膨胀（开发计划 7.2）。"""
    _install(monkeypatch, [_tagged("A")])

    result = pi.search_policies(c_tags=["C9"])[0]

    assert "structuredConditions" not in result
    assert "supportMethods" not in result
    assert "materials" not in result


def test_摘要截断到200字(monkeypatch):
    _install(monkeypatch, [_tagged("A")], [_snapshot("A", summary="很长" * 500)])

    result = pi.search_policies(c_tags=["C9"])[0]

    assert len(result["summary"]) <= pi.SUMMARY_LIMIT


def test_摘要含检索决策所需字段(monkeypatch):
    _install(monkeypatch, [_tagged("A")])

    result = pi.search_policies(c_tags=["C9"])[0]

    for field in ("fusionPolicyId", "title", "tags", "applicationWindow",
                  "applicability", "matchedDimensions", "matchScore"):
        assert field in result, field


# --------------------------------------------------------------------------
# get_policy_detail
# --------------------------------------------------------------------------


def test_详情含全文字段(monkeypatch):
    _install(monkeypatch, [_tagged("A")])

    detail = pi.get_policy_detail("A")

    assert detail["structuredConditions"]
    assert detail["supportMethods"]
    assert detail["tags"]


def test_详情不含关联政策与文档(monkeypatch):
    """三期 3.2 节要求裁掉这两项。"""
    _install(monkeypatch, [_tagged("A")])

    detail = pi.get_policy_detail("A")

    assert "relatedPolicies" not in detail
    assert "documents" not in detail


def test_详情对不存在的政策抛错(monkeypatch):
    _install(monkeypatch, [_tagged("A")])

    with pytest.raises(pi.PolicyIndexError):
        pi.get_policy_detail("NOT_EXIST")


def test_摘要按ID查询(monkeypatch):
    _install(monkeypatch, [_tagged("A")])

    assert pi.get_policy_summary("A")["fusionPolicyId"] == "A"
    assert pi.get_policy_summary("NOT_EXIST") is None


# --------------------------------------------------------------------------
# 时效性判定（五档）
# --------------------------------------------------------------------------


def test_时效性当年未启动():
    window = {"startDate": "2026-09-01", "endDate": "2026-11-30", "isRolling": False}

    assert resolve_timeliness(window, TODAY)["band"] == TIMELINESS_NOT_STARTED


def test_时效性进行中():
    window = {"startDate": "2026-07-01", "endDate": "2026-09-30", "isRolling": False}

    assert resolve_timeliness(window, TODAY)["band"] == TIMELINESS_IN_PROGRESS


def test_时效性上一年度得8分():
    window = {"startDate": "2025-03-01", "endDate": "2025-05-31", "isRolling": False}
    result = resolve_timeliness(window, TODAY)

    assert result["band"] == TIMELINESS_PAST_YEAR
    assert result["score"] == 8


def test_时效性更早年份得5分():
    window = {"startDate": "2023-03-01", "endDate": "2023-05-31", "isRolling": False}

    assert resolve_timeliness(window, TODAY)["score"] == 5


def test_时效性长期受理按进行中():
    assert resolve_timeliness({"isRolling": True}, TODAY)["band"] == TIMELINESS_IN_PROGRESS


def test_时效性窗口缺失得0分():
    assert resolve_timeliness(None, TODAY)["band"] == TIMELINESS_UNKNOWN
    assert resolve_timeliness({}, TODAY)["score"] == 0


def test_时效性跨年边界():
    """today=2026-01-01 时，2026 年窗口是未启动，2025 年窗口是上一年度。"""
    new_year = date(2026, 1, 1)

    assert resolve_timeliness(
        {"startDate": "2026-03-01", "endDate": "2026-05-31"}, new_year
    )["band"] == TIMELINESS_NOT_STARTED
    assert resolve_timeliness(
        {"startDate": "2025-03-01", "endDate": "2025-05-31"}, new_year
    )["score"] == 8


# --------------------------------------------------------------------------
# index_stats
# --------------------------------------------------------------------------


def test_索引统计(monkeypatch):
    _install(monkeypatch, [
        _tagged("A", c_values=("C9",), applicable=True),
        _tagged("B", c_values=("C1",), c_subs=("C1.3",), applicable=False),
    ])

    stats = pi.index_stats()

    assert stats["total"] == 2
    assert stats["applicable"] == 1
    assert stats["notApplicable"] == 1
    assert stats["cMajor"]["C9"] == 1
    assert stats["cSub"]["C1.3"] == 1


def test_索引统计区级限定政策(monkeypatch):
    _install(monkeypatch, [_tagged("A")], [_snapshot("A", scope="集美区")])

    stats = pi.index_stats()

    assert stats["regionRestricted"]["集美区"] == 1


# --------------------------------------------------------------------------
# 真实数据
# --------------------------------------------------------------------------


def test_真实索引可加载():
    from service.paths import POLICY_TAGS_PATH

    if not POLICY_TAGS_PATH.exists():
        pytest.skip("尚未打标")

    pi.reset_cache()
    stats = pi.index_stats()

    assert stats["total"] > 0
    assert stats["applicable"] > 0
    assert stats["notApplicable"] > 0, "应识别出不可申报政策"


def test_真实检索能出结果():
    from service.paths import POLICY_TAGS_PATH

    if not POLICY_TAGS_PATH.exists():
        pytest.skip("尚未打标")

    pi.reset_cache()
    results = pi.search_policies(
        c_tags=["C1", "C5", "C11"], c_sub_tags=["C1.3"],
        b_tags=["B2"], region_tags=["思明区"], limit=10,
    )

    assert results
    assert all(r["title"] for r in results)
    assert all(pi.is_applicable(r) for r in results if "applicability" in r)


def test_真实检索C9政策不被产业查询排除():
    from service.paths import POLICY_TAGS_PATH

    if not POLICY_TAGS_PATH.exists():
        pytest.skip("尚未打标")

    pi.reset_cache()
    results = pi.search_policies(c_tags=["C3"], limit=100)
    c9_count = sum(
        1 for r in results
        if "C9" in ((r["tags"].get("C") or {}).get("values") or [])
    )

    assert c9_count > 0, "C9 通用政策应对任意产业查询命中"


def test_真实详情可读取():
    from service.paths import POLICY_TAGS_PATH

    if not POLICY_TAGS_PATH.exists():
        pytest.skip("尚未打标")

    pi.reset_cache()
    policy_id = next(iter(pi.load_policy_tags()))
    detail = pi.get_policy_detail(policy_id)

    assert detail["fusionPolicyId"] == policy_id
    assert detail["title"]
