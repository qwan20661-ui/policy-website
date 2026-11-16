# -*- coding: utf-8 -*-
"""一期统计脚本测试。"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run_phase1_stats as stats_module

TODAY = date(2026, 8, 3)


def _write(directory: Path, name: str, **overrides) -> None:
    record = {
        "enterpriseName": name,
        "registerDate": "2016-10-13 00:00:00",
        "registerAddress": "厦门市思明区高雄路20号",
        "industryLevel1": "C 制造业",
        "industryLevel2": "医药制造业",
        "operationInfoList": [{"year": "2023", "employeeCount": 108}],
    }
    record.update(overrides)
    (directory / f"{name}.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )


# --------------------------------------------------------------------------
# _percentiles
# --------------------------------------------------------------------------


def test_分位数计算():
    result = stats_module._percentiles([float(i) for i in range(1, 101)])

    assert result["count"] == 100
    assert result["min"] == 1
    assert result["max"] == 100
    assert 45 <= result["p50"] <= 55


def test_分位数对空列表返回全None():
    result = stats_module._percentiles([])

    assert result["count"] is None
    assert result["p50"] is None


def test_分位数对单值列表不报错():
    result = stats_module._percentiles([42.0])

    assert result["count"] == 1
    assert result["p50"] == 42


# --------------------------------------------------------------------------
# collect
# --------------------------------------------------------------------------


def test_统计基本字段(tmp_path):
    _write(tmp_path, "甲")
    _write(tmp_path, "乙")

    result = stats_module.collect(tmp_path, None, TODAY)

    assert result["total"] == 2
    assert result["fieldAvailable"]["registerDate"] == 2
    assert result["fieldAvailable"]["industryLevel2"] == 2


def test_统计跳过旧格式与损坏文件(tmp_path):
    _write(tmp_path, "正常")
    (tmp_path / "old.json").write_text(json.dumps({"name": "旧"}), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{bad", encoding="utf-8")

    result = stats_module.collect(tmp_path, None, TODAY)

    assert result["total"] == 1


def test_统计跳过无企业名的记录(tmp_path):
    _write(tmp_path, "正常")
    (tmp_path / "noname.json").write_text(
        json.dumps({"industryLevel1": "C 制造业", "enterpriseName": ""}), encoding="utf-8"
    )

    result = stats_module.collect(tmp_path, None, TODAY)

    assert result["total"] == 1


def test_limit参数生效(tmp_path):
    for index in range(5):
        _write(tmp_path, f"企业{index}")

    result = stats_module.collect(tmp_path, 2, TODAY)

    assert result["total"] == 2


def test_统计规则三态(tmp_path):
    _write(tmp_path, "甲")

    result = stats_module.collect(tmp_path, None, TODAY)

    # R17 永久跳过
    assert result["ruleStates"]["R17"]["skipped"] == 1
    # R00 因 enterpriseCredit 无值跳过
    assert result["ruleStates"]["R00"]["skipped"] == 1


def test_统计跳过原因记录缺失字段(tmp_path):
    _write(tmp_path, "甲")

    result = stats_module.collect(tmp_path, None, TODAY)

    assert "isInnovativeSme" in result["skipReasons"]["R19"]


def test_信用负面企业计入blocked(tmp_path):
    _write(tmp_path, "失信企业", operationInfoList=[
        {"year": "2023", "employeeCount": 10, "enterpriseCredit": "存在经营异常"}
    ])

    result = stats_module.collect(tmp_path, None, TODAY)

    assert result["blocked"] == 1
    assert result["ruleStates"]["R00"]["matched"] == 1


def test_统计标签分布(tmp_path):
    _write(tmp_path, "甲")

    result = stats_module.collect(tmp_path, None, TODAY)

    assert result["cMajor"]["C2"] == 1
    # 成立 9.8 年（>7）命中 B3；营收与资质字段缺失，故不命中 B1/B2
    assert result["bTags"]["B3"] == 1
    assert result["regions"]["思明区"] == 1


def test_统计B信号数分布(tmp_path):
    _write(tmp_path, "甲")

    result = stats_module.collect(tmp_path, None, TODAY)

    assert any("信号" in key for key in result["bSignals"])


def test_按有无行业值分组统计分数(tmp_path):
    """这组对比是验证归一化有效性的关键数据。"""
    _write(tmp_path, "有行业")
    _write(tmp_path, "无行业", industryLevel1=None, industryLevel2=None)

    result = stats_module.collect(tmp_path, None, TODAY)
    label = stats_module.SAMPLE_POLICIES[0]["label"]

    assert len(result["scoresWithIndustry"][label]) == 1
    assert len(result["scoresWithoutIndustry"][label]) == 1


def test_统计有效权重分布(tmp_path):
    _write(tmp_path, "甲")

    result = stats_module.collect(tmp_path, None, TODAY)

    assert result["effectiveWeights"]
    assert all(0 < w <= 100 for w in result["effectiveWeights"])


def test_统计档位分布(tmp_path):
    _write(tmp_path, "甲")

    result = stats_module.collect(tmp_path, None, TODAY)
    label = stats_module.SAMPLE_POLICIES[0]["label"]

    assert sum(result["bands"][label].values()) == 1


def test_空目录返回零(tmp_path):
    result = stats_module.collect(tmp_path, None, TODAY)

    assert result["total"] == 0


# --------------------------------------------------------------------------
# 样例政策配置
# --------------------------------------------------------------------------


def test_样例政策覆盖三种口径():
    """一期无真实政策标签，样例需覆盖通用/子类专项/有规模门槛三类，
    才能看出分数分布形态。"""
    assert len(stats_module.SAMPLE_POLICIES) == 3

    all_c_tags = set()
    all_sub_tags = set()
    has_scale_threshold = False
    for policy in stats_module.SAMPLE_POLICIES:
        c_entry = policy["tags"]["C"]
        all_c_tags.update(c_entry["values"])
        all_sub_tags.update(c_entry.get("subValues") or [])
        if policy.get("scaleThresholds"):
            has_scale_threshold = True

    assert "C9" in all_c_tags
    assert all_sub_tags
    assert has_scale_threshold


def test_样例政策标签都合法():
    from service import taxonomy as tx

    for policy in stats_module.SAMPLE_POLICIES:
        assert tx.validate_tag_payload(policy["tags"]) == [], policy["label"]


def test_样例政策覆盖三种时效性():
    from service.scoring import resolve_timeliness

    bands = {
        resolve_timeliness(p.get("applicationWindow"), TODAY)["band"]
        for p in stats_module.SAMPLE_POLICIES
    }

    assert len(bands) >= 2


# --------------------------------------------------------------------------
# 导出
# --------------------------------------------------------------------------


def test_导出Excel含六张表页(tmp_path, monkeypatch):
    _write(tmp_path, "甲")
    _write(tmp_path, "乙", industryLevel1=None, industryLevel2=None)
    monkeypatch.setattr(stats_module, "OUTPUT_DIR", tmp_path / "out")

    stats = stats_module.collect(tmp_path, None, TODAY)
    target = stats_module.export(stats)

    assert target.exists()

    from openpyxl import load_workbook

    workbook = load_workbook(target)
    titles = workbook.sheetnames
    assert len(titles) == 6
    for keyword in ("字段填充率", "规则三态", "标签分布", "基础分分布", "有效权重", "档位分布"):
        assert any(keyword in t for t in titles), keyword


def test_导出的规则表含全部34条(tmp_path, monkeypatch):
    _write(tmp_path, "甲")
    monkeypatch.setattr(stats_module, "OUTPUT_DIR", tmp_path / "out")

    stats = stats_module.collect(tmp_path, None, TODAY)
    target = stats_module.export(stats)

    from openpyxl import load_workbook

    sheet = load_workbook(target)["2-规则三态分布"]
    assert sheet.max_row == 35, "34 条规则 + 表头"


def test_print_summary不抛异常(tmp_path, capsys):
    _write(tmp_path, "甲")
    _write(tmp_path, "乙", industryLevel1=None, industryLevel2=None)

    stats = stats_module.collect(tmp_path, None, TODAY)
    stats_module.print_summary(stats)

    output = capsys.readouterr().out
    assert "样本" in output
    assert "跳过的规则" in output


def test_print_summary标出100百分比跳过的规则(tmp_path, capsys):
    _write(tmp_path, "甲")

    stats = stats_module.collect(tmp_path, None, TODAY)
    stats_module.print_summary(stats)

    output = capsys.readouterr().out
    assert "R17" in output


def test_print_summary提示归一化差异(tmp_path, capsys):
    """两组中位数差异是四期校准的核心输入，必须打印出来。"""
    _write(tmp_path, "有行业")
    _write(tmp_path, "无行业", industryLevel1=None, industryLevel2=None)

    stats = stats_module.collect(tmp_path, None, TODAY)
    stats_module.print_summary(stats)

    output = capsys.readouterr().out
    assert "有行业值" in output
    assert "无行业值" in output
