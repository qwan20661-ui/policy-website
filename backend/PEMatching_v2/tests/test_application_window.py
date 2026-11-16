# -*- coding: utf-8 -*-
"""申报期提取测试。

确定性提取优先是本模块的核心设计——实测覆盖 96.4%，
从设计上消除了 LLM 编造日期的风险。
"""

from __future__ import annotations

import pytest

from service.application_window import (
    SOURCE_INSTRUCTION,
    SOURCE_NONE,
    SOURCE_ROLLING,
    SOURCE_STRUCTURED,
    extract_window,
    merge_llm_window,
    parse_date_text,
    window_stats,
)


def _policy(*conditions):
    return {"structuredConditions": list(conditions)}


# --------------------------------------------------------------------------
# parse_date_text
# --------------------------------------------------------------------------


def test_解析点分日期():
    assert parse_date_text("2026.04.10") == "2026-04-10"


def test_解析中文日期():
    assert parse_date_text("2026年04月10日") == "2026-04-10"


def test_解析短横线与斜杠日期():
    assert parse_date_text("2026-4-10") == "2026-04-10"
    assert parse_date_text("2026/4/10") == "2026-04-10"


def test_解析嵌在文本里的日期():
    assert parse_date_text("受理时间为2026年04月10日起") == "2026-04-10"


def test_非法日期返回None():
    assert parse_date_text("2026.13.45") is None
    assert parse_date_text("不是日期") is None
    assert parse_date_text("") is None
    assert parse_date_text(None) is None


def test_年份越界返回None():
    assert parse_date_text("1800.01.01") is None
    assert parse_date_text("2500.01.01") is None


# --------------------------------------------------------------------------
# 一级：结构化字段（实测覆盖 84%）
# --------------------------------------------------------------------------


def test_优先取结构化字段():
    policy = _policy({
        "applicationStartTime": "2026.04.10",
        "applicationEndTime": "2026.05.11",
        "applicationInstruction": "2026年04月10日-2026年05月11日",
    })
    window = extract_window(policy)

    assert window["source"] == SOURCE_STRUCTURED
    assert window["startDate"] == "2026-04-10"
    assert window["endDate"] == "2026-05-11"
    assert window["confidence"] == "high"
    assert window["needsLlm"] is False


def test_结构化字段只有开始时间():
    policy = _policy({"applicationStartTime": "2026.04.10", "applicationEndTime": ""})
    window = extract_window(policy)

    assert window["startDate"] == "2026-04-10"
    assert window["endDate"] is None
    assert window["source"] == SOURCE_STRUCTURED


def test_结构化字段的yearScope取开始年份():
    policy = _policy({"applicationStartTime": "2025.12.29", "applicationEndTime": "2026.02.13"})
    window = extract_window(policy)

    assert window["yearScope"] == "2025"


def test_结构化字段依据可追溯():
    policy = _policy({"applicationStartTime": "2026.04.10", "applicationEndTime": "2026.05.11"})
    window = extract_window(policy)

    assert "applicationStartTime" in window["basis"]


# --------------------------------------------------------------------------
# 二级：申报说明文本
# --------------------------------------------------------------------------


def test_识别长期受理():
    for word in ("长期受理", "常年受理", "长期有效", "随时申报", "全年受理"):
        window = extract_window(_policy({"applicationInstruction": word}))
        assert window["isRolling"] is True, word
        assert window["source"] == SOURCE_ROLLING
        assert window["needsLlm"] is False


def test_长期受理时起止日期为None():
    window = extract_window(_policy({"applicationInstruction": "长期受理"}))

    assert window["startDate"] is None
    assert window["endDate"] is None


def test_从申报说明文本抽取日期区间():
    policy = _policy({"applicationInstruction": "2026年06月2日-2026年8月31日"})
    window = extract_window(policy)

    assert window["source"] == SOURCE_INSTRUCTION
    assert window["startDate"] == "2026-06-02"
    assert window["endDate"] == "2026-08-31"
    assert window["confidence"] == "medium"


def test_申报说明只有单个日期时endDate为None():
    policy = _policy({"applicationInstruction": "自2026年04月10日起受理"})
    window = extract_window(policy)

    assert window["startDate"] == "2026-04-10"
    assert window["endDate"] is None


def test_日期区间自动按先后排序():
    policy = _policy({"applicationInstruction": "截止2026年08月31日，自2026年06月02日开始"})
    window = extract_window(policy)

    assert window["startDate"] == "2026-06-02"
    assert window["endDate"] == "2026-08-31"


# --------------------------------------------------------------------------
# 三级：无线索时交 LLM
# --------------------------------------------------------------------------


def test_无任何申报期线索时标记需LLM兜底():
    window = extract_window(_policy({"applicableTarget": "企业"}))

    assert window["source"] == SOURCE_NONE
    assert window["needsLlm"] is True
    assert window["startDate"] is None


def test_子表缺失时也返回需LLM():
    window = extract_window({})

    assert window["needsLlm"] is True


def test_申报要求里的日期作为LLM线索():
    policy = _policy({"applicationRequirement": "项目应于2026年05月01日前完成备案"})
    window = extract_window(policy)

    assert window["needsLlm"] is True
    assert window["hints"], "应把含日期的申报要求作为线索传给 LLM"


def test_子表元素非字典时跳过():
    policy = {"structuredConditions": ["坏数据", {"applicationStartTime": "2026.04.10"}]}
    window = extract_window(policy)

    assert window["startDate"] == "2026-04-10"


# --------------------------------------------------------------------------
# merge_llm_window：确定性结果优先
# --------------------------------------------------------------------------


def test_确定性成功时忽略LLM结果():
    """结构化字段不可能编造，永远优先。"""
    deterministic = extract_window(
        _policy({"applicationStartTime": "2026.04.10", "applicationEndTime": "2026.05.11"})
    )
    merged = merge_llm_window(deterministic, {"startDate": "1999-01-01", "endDate": "1999-12-31"})

    assert merged["startDate"] == "2026-04-10"
    assert merged["source"] == SOURCE_STRUCTURED


def test_确定性失败时采用LLM结果但降置信度():
    deterministic = extract_window(_policy({"applicableTarget": "企业"}))
    merged = merge_llm_window(
        deterministic,
        {"startDate": "2026-03-01", "endDate": "2026-05-31", "basis": "申报指南第三条"},
    )

    assert merged["startDate"] == "2026-03-01"
    assert merged["confidence"] == "low"
    assert merged["source"] == "llm"


def test_LLM也抽不出时保持null不编造():
    deterministic = extract_window(_policy({"applicableTarget": "企业"}))
    merged = merge_llm_window(deterministic, {"startDate": None, "endDate": None})

    assert merged["startDate"] is None
    assert merged["source"] == SOURCE_NONE


def test_LLM返回非法日期时不采用():
    deterministic = extract_window(_policy({"applicableTarget": "企业"}))
    merged = merge_llm_window(deterministic, {"startDate": "明年三月", "endDate": ""})

    assert merged["startDate"] is None


def test_LLM返回rolling被采用():
    deterministic = extract_window(_policy({"applicableTarget": "企业"}))
    merged = merge_llm_window(deterministic, {"isRolling": True, "basis": "常年受理"})

    assert merged["isRolling"] is True


def test_merge结果不含内部字段():
    """needsLlm / hints 是内部标记，不该出现在最终产出里。"""
    deterministic = extract_window(_policy({"applicationStartTime": "2026.04.10"}))
    merged = merge_llm_window(deterministic, None)

    assert "needsLlm" not in merged
    assert "hints" not in merged


def test_LLM结果为None时不报错():
    deterministic = extract_window(_policy({"applicableTarget": "企业"}))
    merged = merge_llm_window(deterministic, None)

    assert merged["startDate"] is None


# --------------------------------------------------------------------------
# 真实快照覆盖率
# --------------------------------------------------------------------------


def test_真实快照确定性提取覆盖率达标():
    """这是把申报期从 LLM 手里拿回来的依据：实测 96.4% 无需模型。"""
    from service.paths import POLICY_SNAPSHOT_PATH
    from service.policy_sync import load_snapshot

    if not POLICY_SNAPSHOT_PATH.exists():
        pytest.skip("尚未同步政策快照")

    stats = window_stats(load_snapshot()["data"])

    assert stats["deterministicRatio"] >= 90, (
        f"确定性提取覆盖率 {stats['deterministicRatio']}% 低于预期，"
        f"来源分布 {stats['bySource']}"
    )


def test_真实快照里结构化字段是主力来源():
    from service.paths import POLICY_SNAPSHOT_PATH
    from service.policy_sync import load_snapshot

    if not POLICY_SNAPSHOT_PATH.exists():
        pytest.skip("尚未同步政策快照")

    stats = window_stats(load_snapshot()["data"])

    assert stats["bySource"].get(SOURCE_STRUCTURED, 0) > stats["total"] * 0.7


def test_真实快照所有窗口日期格式合法():
    from service.paths import POLICY_SNAPSHOT_PATH
    from service.policy_sync import load_snapshot

    if not POLICY_SNAPSHOT_PATH.exists():
        pytest.skip("尚未同步政策快照")

    import re

    for policy in load_snapshot()["data"]:
        window = extract_window(policy)
        for key in ("startDate", "endDate"):
            value = window.get(key)
            if value:
                assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), f"{policy['fusionPolicyId']} {key}={value}"
