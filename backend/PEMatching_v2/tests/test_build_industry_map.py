# -*- coding: utf-8 -*-
"""行业映射表生成脚本测试。LLM 调用全部 mock，不发真实请求。"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run_build_industry_map as builder
from service import taxonomy as tx
from service.llm_client import LLMError


def _write_record(directory: Path, name: str, level1: str, level2: str) -> None:
    payload = {
        "enterpriseName": name,
        "industryLevel1": level1,
        "industryLevel2": level2,
    }
    (directory / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


# --------------------------------------------------------------------------
# collect_industry_values
# --------------------------------------------------------------------------


def test_统计行业取值分布(tmp_path):
    _write_record(tmp_path, "甲", "C 制造业", "医药制造业")
    _write_record(tmp_path, "乙", "C 制造业", "医药制造业")
    _write_record(tmp_path, "丙", "I 信息传输、软件和信息技术服务业", "软件和信息技术服务业")

    level1, level2, scanned, with_industry = builder.collect_industry_values(tmp_path)

    assert scanned == 3
    assert with_industry == 3
    assert level1["C 制造业"] == 2
    assert level2["医药制造业"] == 2


def test_行业字段全空的记录计入扫描数但不计入有值数(tmp_path):
    """实测 69.2% 的企业行业字段为空，这条路径必须正确统计。"""
    _write_record(tmp_path, "甲", "", "")
    _write_record(tmp_path, "乙", "C 制造业", "医药制造业")

    level1, level2, scanned, with_industry = builder.collect_industry_values(tmp_path)

    assert scanned == 2
    assert with_industry == 1
    assert sum(level1.values()) == 1


def test_跳过缺industryLevel1键的旧格式文件(tmp_path):
    (tmp_path / "old.json").write_text(
        json.dumps({"name": "旧格式", "user_id": 1}, ensure_ascii=False), encoding="utf-8"
    )
    _write_record(tmp_path, "新", "C 制造业", "医药制造业")

    _, _, scanned, _ = builder.collect_industry_values(tmp_path)

    assert scanned == 1


def test_跳过损坏的json文件不抛异常(tmp_path):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    _write_record(tmp_path, "正常", "C 制造业", "医药制造业")

    _, _, scanned, _ = builder.collect_industry_values(tmp_path)

    assert scanned == 1


def test_空目录返回零计数(tmp_path):
    level1, level2, scanned, with_industry = builder.collect_industry_values(tmp_path)

    assert scanned == 0
    assert with_industry == 0
    assert level1 == Counter()


# --------------------------------------------------------------------------
# _validate_mapping
# --------------------------------------------------------------------------


def _counters():
    return Counter({"C 制造业": 5}), Counter({"医药制造业": 3})


def _valid_mapping():
    return {
        "byLevel1": {"C 制造业": {"cTags": [], "confidence": "low", "reason": "门类过宽"}},
        "byLevel2": {"医药制造业": {"cTags": ["C2"], "confidence": "high", "reason": "属生物医药"}},
    }


def test_合法映射无错误():
    level1, level2 = _counters()
    assert builder._validate_mapping(_valid_mapping(), level1, level2) == []


def test_映射里出现非法C编号被拒绝():
    """防模型输出行业名称而非编号。"""
    level1, level2 = _counters()
    mapping = _valid_mapping()
    mapping["byLevel2"]["医药制造业"]["cTags"] = ["生物医药"]

    errors = builder._validate_mapping(mapping, level1, level2)

    assert any("非法编号" in e for e in errors)


def test_映射漏了取值被拒绝():
    level1, level2 = _counters()
    mapping = _valid_mapping()
    del mapping["byLevel2"]["医药制造业"]

    errors = builder._validate_mapping(mapping, level1, level2)

    assert any("漏了" in e for e in errors)


def test_映射缺reason被拒绝():
    level1, level2 = _counters()
    mapping = _valid_mapping()
    mapping["byLevel2"]["医药制造业"]["reason"] = "  "

    errors = builder._validate_mapping(mapping, level1, level2)

    assert any("reason" in e for e in errors)


def test_映射置信度非法被拒绝():
    level1, level2 = _counters()
    mapping = _valid_mapping()
    mapping["byLevel1"]["C 制造业"]["confidence"] = "very_low"

    errors = builder._validate_mapping(mapping, level1, level2)

    assert any("confidence" in e for e in errors)


def test_cTags为空数组是合法的():
    """空数组表示「口径过宽，明确落 C9」，是有效产出而非缺失。"""
    level1, level2 = _counters()
    mapping = _valid_mapping()
    mapping["byLevel2"]["医药制造业"] = {
        "cTags": [], "confidence": "low", "reason": "口径过宽",
    }

    assert builder._validate_mapping(mapping, level1, level2) == []


def test_cTags不是数组被拒绝():
    level1, level2 = _counters()
    mapping = _valid_mapping()
    mapping["byLevel2"]["医药制造业"]["cTags"] = "C2"

    errors = builder._validate_mapping(mapping, level1, level2)

    assert any("必须是数组" in e for e in errors)


# --------------------------------------------------------------------------
# generate_mapping：校验失败后把错误回传给模型重试
# --------------------------------------------------------------------------


def test_首次产出合法则不重试():
    level1, level2 = _counters()
    with patch.object(builder, "chat_with_retry") as mocked:
        mocked.return_value = {"content": json.dumps(_valid_mapping(), ensure_ascii=False)}
        result = builder.generate_mapping(level1, level2)

    assert mocked.call_count == 1
    assert result["byLevel2"]["医药制造业"]["cTags"] == ["C2"]


def test_首次非法第二次合法则重试成功():
    level1, level2 = _counters()
    bad = _valid_mapping()
    bad["byLevel2"]["医药制造业"]["cTags"] = ["生物医药"]

    with patch.object(builder, "chat_with_retry") as mocked:
        mocked.side_effect = [
            {"content": json.dumps(bad, ensure_ascii=False)},
            {"content": json.dumps(_valid_mapping(), ensure_ascii=False)},
        ]
        result = builder.generate_mapping(level1, level2)

    assert mocked.call_count == 2
    assert result["byLevel2"]["医药制造业"]["cTags"] == ["C2"]


def test_重试时把校验错误回传给模型():
    """比原样重试有效：模型需要知道错在哪。"""
    level1, level2 = _counters()
    bad = _valid_mapping()
    bad["byLevel2"]["医药制造业"]["cTags"] = ["生物医药"]

    with patch.object(builder, "chat_with_retry") as mocked:
        mocked.side_effect = [
            {"content": json.dumps(bad, ensure_ascii=False)},
            {"content": json.dumps(_valid_mapping(), ensure_ascii=False)},
        ]
        builder.generate_mapping(level1, level2)

    second_messages = mocked.call_args_list[1][0][0]
    assert second_messages[-1]["role"] == "user"
    assert "非法编号" in second_messages[-1]["content"]


def test_持续非法则抛LLMError():
    level1, level2 = _counters()
    bad = _valid_mapping()
    bad["byLevel2"]["医药制造业"]["cTags"] = ["生物医药"]

    with patch.object(builder, "chat_with_retry") as mocked:
        mocked.return_value = {"content": json.dumps(bad, ensure_ascii=False)}
        with pytest.raises(LLMError):
            builder.generate_mapping(level1, level2, max_retries=1)


def test_响应不是合法json则重试():
    level1, level2 = _counters()
    with patch.object(builder, "chat_with_retry") as mocked:
        mocked.side_effect = [
            {"content": "这不是 JSON"},
            {"content": json.dumps(_valid_mapping(), ensure_ascii=False)},
        ]
        result = builder.generate_mapping(level1, level2)

    assert mocked.call_count == 2
    assert "byLevel2" in result


# --------------------------------------------------------------------------
# prompt 契约
# --------------------------------------------------------------------------


def test_prompt含全部C大类编号():
    level1, level2 = _counters()
    messages = builder._build_prompt(level1, level2)
    text = "\n".join(m["content"] for m in messages)

    for code in [f"C{i}" for i in range(1, 12)]:
        assert code in text


def test_prompt明确禁止输出行业名称():
    level1, level2 = _counters()
    messages = builder._build_prompt(level1, level2)
    system = messages[0]["content"]

    assert "禁止输出行业名称" in system


def test_prompt说明不属高企领域时填空数组():
    """防模型硬凑领域。"""
    level1, level2 = _counters()
    messages = builder._build_prompt(level1, level2)
    system = messages[0]["content"]

    assert "不要硬凑" in system


def test_prompt含实际行业取值与样本数():
    level1, level2 = _counters()
    messages = builder._build_prompt(level1, level2)
    user = messages[1]["content"]

    assert "医药制造业" in user
    assert "3 家" in user


# --------------------------------------------------------------------------
# 现有种子映射表的完整性
# --------------------------------------------------------------------------


def test_种子映射表覆盖样本全部行业取值():
    """映射表漏了取值会导致该行业企业的 C 维度落 unknown，白丢 30 分。"""
    from service.paths import sample_enterprise_dir

    sample_dir = sample_enterprise_dir()
    if not sample_dir.exists():
        pytest.skip(f"样本目录不存在：{sample_dir}")

    level1, level2, _, _ = builder.collect_industry_values(sample_dir)
    mapping = tx.load_industry_map()

    assert set(level1) - set(mapping.get("byLevel1") or {}) == set()
    assert set(level2) - set(mapping.get("byLevel2") or {}) == set()


def test_种子映射表所有cTags都是合法C编号():
    mapping = tx.load_industry_map()
    for key in ("byLevel1", "byLevel2"):
        for value, entry in (mapping.get(key) or {}).items():
            for tag in entry.get("cTags") or []:
                assert tx.is_valid_tag("C", tag), f"{key}.{value} 含非法编号 {tag}"


def test_种子映射表每条都有reason与confidence():
    mapping = tx.load_industry_map()
    for key in ("byLevel1", "byLevel2"):
        for value, entry in (mapping.get(key) or {}).items():
            assert entry.get("confidence") in ("high", "medium", "low"), f"{key}.{value}"
            assert str(entry.get("reason") or "").strip(), f"{key}.{value} 缺 reason"
