# -*- coding: utf-8 -*-
"""打标 prompt 契约测试。

prompt 是产出质量的第一道关口，契约不能被误删——本文件守住那些
「删掉后模型会立刻开始输出非法值」的关键约束。
"""

from __future__ import annotations

import json
import re

import pytest

from service import taxonomy as tx
from service.paths import PROMPTS_DIR

PROMPT_PATH = PROMPTS_DIR / "policy_tagging_prompt.md"


@pytest.fixture(scope="module")
def prompt_text() -> str:
    if not PROMPT_PATH.exists():
        pytest.fail(f"打标 prompt 不存在：{PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 存在性
# --------------------------------------------------------------------------


def test_prompt文件存在且非空(prompt_text):
    assert len(prompt_text) > 1000


# --------------------------------------------------------------------------
# 标签编号完整性：漏写会导致模型输出非法值
# --------------------------------------------------------------------------


def test_prompt含全部A编号(prompt_text):
    for code in tx.valid_tags("A"):
        assert code in prompt_text, f"prompt 漏了 {code}"


def test_prompt含全部B编号(prompt_text):
    for code in tx.valid_tags("B"):
        assert code in prompt_text, f"prompt 漏了 {code}"


def test_prompt含全部11个C大类编号(prompt_text):
    for code in tx.valid_tags("C", include_sub=False):
        assert re.search(rf"\b{re.escape(code)}\b", prompt_text), f"prompt 漏了 {code}"


def test_prompt含全部4个C子类编号(prompt_text):
    for code in ("C1.1", "C1.2", "C1.3", "C10.1"):
        assert code in prompt_text, f"prompt 漏了 {code}"


def test_prompt含全部D编号(prompt_text):
    for code in tx.valid_tags("D"):
        assert code in prompt_text, f"prompt 漏了 {code}"


def test_prompt含全部E编号(prompt_text):
    for code in tx.valid_tags("E"):
        assert code in prompt_text, f"prompt 漏了 {code}"


def test_prompt含全部F编号(prompt_text):
    for code in tx.valid_tags("F"):
        assert code in prompt_text, f"prompt 漏了 {code}"


def test_prompt不含未定义的子类编号(prompt_text):
    """决策 11：只保留 docx 明文定义的 4 个子类，不自行扩充。"""
    for absent in ("C2.1", "C2.2", "C4.1", "C6.1", "C10.2", "C10.3"):
        assert absent not in prompt_text, f"prompt 出现了未定义的子类 {absent}"


# --------------------------------------------------------------------------
# 硬约束字样：删掉后模型行为会立刻退化
# --------------------------------------------------------------------------


def test_prompt明确industryField是依据不是答案(prompt_text):
    """这是防政策库词表污染匹配轴的核心约束（开发计划决策 10）。"""
    assert "是判断依据，不是答案" in prompt_text
    assert "industryField" in prompt_text


def test_prompt明确禁止输出中文名称与自造编号(prompt_text):
    assert "禁止输出中文名称" in prompt_text
    assert "禁止自造编号" in prompt_text


def test_prompt明确basis必填(prompt_text):
    assert "basis` 必填" in prompt_text or "basis 必填" in prompt_text


def test_prompt明确不确定时给low置信度(prompt_text):
    assert "low" in prompt_text
    assert "宁可标不确定" in prompt_text


def test_prompt明确子类未点明时只填大类(prompt_text):
    """防模型从「电子信息领域」推测出 C1.2。"""
    assert "只有政策明确针对该细分赛道时才填" in prompt_text


def test_prompt明确无金额信息填null(prompt_text):
    assert "null" in prompt_text
    assert "无金额" in prompt_text


def test_prompt说明申报期由系统提取(prompt_text):
    """申报期改为确定性提取（覆盖 96.4%），模型不再负责，
    从设计上消除编造日期的风险。"""
    assert "applicationWindow" in prompt_text
    assert "由系统另行提取" in prompt_text


def test_prompt要求直接输出JSON不用代码块(prompt_text):
    assert "不要用代码块包裹" in prompt_text


# --------------------------------------------------------------------------
# 示例可解析且合法
# --------------------------------------------------------------------------


def _json_blocks(text: str):
    return re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)


def test_prompt里的json示例都可解析(prompt_text):
    blocks = _json_blocks(prompt_text)
    assert len(blocks) >= 3, "应有输出格式说明 + 2 个 few-shot 示例"
    for index, block in enumerate(blocks):
        try:
            json.loads(block)
        except json.JSONDecodeError as exc:
            pytest.fail(f"第 {index + 1} 个 JSON 示例不可解析：{exc}")


def test_prompt里的示例标签全部合法(prompt_text):
    """示例本身出现非法标签会直接教坏模型。"""
    for block in _json_blocks(prompt_text):
        payload = json.loads(block)
        tags = payload.get("tags")
        if not tags:
            continue
        errors = tx.validate_tag_payload(tags)
        assert errors == [], f"示例标签非法：{errors}"


def test_prompt示例含通用普惠与产业专项两种典型(prompt_text):
    blocks = [json.loads(b) for b in _json_blocks(prompt_text)]
    c_values = [
        (b.get("tags") or {}).get("C", {}).get("values") or []
        for b in blocks if b.get("tags")
    ]
    sub_values = [
        (b.get("tags") or {}).get("C", {}).get("subValues") or []
        for b in blocks if b.get("tags")
    ]

    assert any("C9" in v for v in c_values), "缺通用普惠类示例"
    assert any(s for s in sub_values), "缺含子类的产业专项示例"


def test_prompt示例含D1与D3两个极端(prompt_text):
    blocks = [json.loads(b) for b in _json_blocks(prompt_text)]
    d_values = {
        (b.get("tags") or {}).get("D", {}).get("value")
        for b in blocks if b.get("tags")
    }

    assert "D1" in d_values
    assert "D3" in d_values


def test_prompt示例的basis都非空(prompt_text):
    for block in _json_blocks(prompt_text):
        payload = json.loads(block)
        for dimension, entry in (payload.get("tags") or {}).items():
            assert str(entry.get("basis") or "").strip(), f"示例 {dimension}.basis 为空"


def test_prompt示例不含applicationWindow字段(prompt_text):
    """申报期由系统提取，示例里不该出现，否则模型会照着输出。"""
    for block in _json_blocks(prompt_text):
        payload = json.loads(block)
        assert "applicationWindow" not in payload


def test_prompt示例含growthChain与keywordIndex(prompt_text):
    blocks = [json.loads(b) for b in _json_blocks(prompt_text)]
    full_examples = [b for b in blocks if b.get("tags")]

    assert full_examples
    for example in full_examples:
        assert "growthChain" in example
        assert "keywordIndex" in example


# --------------------------------------------------------------------------
# 常见错误对照表
# --------------------------------------------------------------------------


def test_prompt含常见错误对照表(prompt_text):
    """把易犯错误列出来比只说规则有效。"""
    assert "常见错误" in prompt_text
    assert '"values": ["通用行业"]' in prompt_text


def test_prompt含行业词到C编号的映射参考(prompt_text):
    """模型需要知道「半导体和集成电路」该翻译成什么。"""
    for term in ("半导体和集成电路", "软件与信息服务", "生物医药", "新材料", "新能源"):
        assert term in prompt_text, f"缺 {term} 的映射参考"
