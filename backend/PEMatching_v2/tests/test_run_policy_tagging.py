# -*- coding: utf-8 -*-
"""批量打标脚本测试。LLM 全部 mock。

对应 docs/二期_政策打标开发流程.md 第 5.5 节测试要求。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run_policy_tagging as runner
from service.tagging import TaggingError


def _policy(policy_id="6548", update_time="2026-07-28 09:23:56"):
    return {
        "fusionPolicyId": policy_id,
        "title": f"政策{policy_id}",
        "updateTime": update_time,
        "policyInfo": {"policyTitle": f"政策{policy_id}", "industryField": "通用行业"},
        "supportMethods": [{"supportType": "一次性奖励补助"}],
        "structuredConditions": [{
            "applicationStartTime": "2026.06.02", "applicationEndTime": "2026.08.31",
        }],
        "keywords": [], "materials": [], "documents": [],
        "relatedPolicies": {},
    }


def _tagged(policy_id="6548", **overrides):
    record = {
        "fusionPolicyId": policy_id,
        "title": f"政策{policy_id}",
        "tags": {
            "A": {"primary": "A3", "secondary": [], "confidence": "high", "basis": "资金奖补"},
            "B": {"values": ["B2"], "confidence": "medium", "basis": "成长期"},
            "C": {"values": ["C9"], "subValues": [], "confidence": "high", "basis": "通用行业"},
            "D": {"value": "D1", "confidence": "high", "basis": "登记即享"},
            "E": {"value": None, "confidence": "low", "basis": "无金额"},
            "F": {"value": "F3", "confidence": "high", "basis": "年度申报"},
        },
        "applicability": {"applicable": True, "reason": "企业可申报", "audience": "企业"},
        "applicationWindow": {
            "startDate": "2026-06-02", "endDate": "2026-08-31", "isRolling": False,
            "confidence": "high", "basis": "结构化字段", "source": "structured_field",
        },
        "growthChain": {"prerequisite": None, "nextStage": None, "basis": "不属链路"},
        "keywordIndex": {"title": ["政策"], "aKeywords": [], "cKeywords": []},
        "notes": "",
        "warnings": [],
        "_attempts": 1,
    }
    record.update(overrides)
    return record


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """把产出路径隔离到 tmp_path，避免污染真实配置。"""
    tags_path = tmp_path / "policy_tags.json"
    output_dir = tmp_path / "output"
    monkeypatch.setattr(runner, "POLICY_TAGS_PATH", tags_path)
    monkeypatch.setattr(runner, "OUTPUT_DIR", output_dir)
    return tags_path, output_dir


# --------------------------------------------------------------------------
# 指纹
# --------------------------------------------------------------------------


def test_同一政策指纹稳定():
    policy = _policy()

    assert runner._fingerprint(policy) == runner._fingerprint(policy)


def test_政策updateTime变化导致指纹变化():
    first = runner._fingerprint(_policy(update_time="2026-01-01 00:00:00"))
    second = runner._fingerprint(_policy(update_time="2026-07-28 09:23:56"))

    assert first != second


def test_不同政策指纹不同():
    assert runner._fingerprint(_policy("A")) != runner._fingerprint(_policy("B"))


def test_prompt文件mtime变化导致指纹变化(tmp_path, monkeypatch):
    """prompt 改了打标口径就变了，必须重打。"""
    fake_prompt = tmp_path / "prompt.md"
    fake_prompt.write_text("v1", encoding="utf-8")
    monkeypatch.setattr(runner, "PROMPT_PATH", fake_prompt)
    import os

    os.utime(fake_prompt, (1000000, 1000000))
    first = runner._fingerprint(_policy())

    os.utime(fake_prompt, (2000000, 2000000))
    second = runner._fingerprint(_policy())

    assert first != second


def test_指纹未变时跳过(isolated):
    tags_path, _ = isolated
    policy = _policy()
    existing = {"6548": {**_tagged(), "_fingerprint": runner._fingerprint(policy)}}

    pending, skipped = runner._select_policies([policy], existing, False, None, None)

    assert pending == []
    assert skipped == ["6548"]


def test_指纹变化时重打(isolated):
    policy = _policy(update_time="2026-08-01 00:00:00")
    existing = {"6548": {**_tagged(), "_fingerprint": "stale-fingerprint"}}

    pending, skipped = runner._select_policies([policy], existing, False, None, None)

    assert len(pending) == 1
    assert skipped == []


def test_force时忽略指纹(isolated):
    policy = _policy()
    existing = {"6548": {**_tagged(), "_fingerprint": runner._fingerprint(policy)}}

    pending, skipped = runner._select_policies([policy], existing, True, None, None)

    assert len(pending) == 1


# --------------------------------------------------------------------------
# 筛选参数
# --------------------------------------------------------------------------


def test_ids参数只打指定政策():
    policies = [_policy("A"), _policy("B"), _policy("C")]

    pending, _ = runner._select_policies(policies, {}, False, ["B"], None)

    assert [p["fusionPolicyId"] for p in pending] == ["B"]


def test_limit参数只打前N条():
    policies = [_policy(str(i)) for i in range(5)]

    pending, _ = runner._select_policies(policies, {}, False, None, 2)

    assert len(pending) == 2


def test_ids与limit组合():
    policies = [_policy("A"), _policy("B"), _policy("C")]

    pending, _ = runner._select_policies(policies, {}, False, ["A", "B", "C"], 2)

    assert len(pending) == 2


# --------------------------------------------------------------------------
# 写入与原子性
# --------------------------------------------------------------------------


def test_写入产出结构(isolated):
    tags_path, _ = isolated
    runner._write_tags({"6548": _tagged()}, [])

    payload = json.loads(tags_path.read_text(encoding="utf-8"))

    assert payload["schemaVersion"] == runner.TAGS_SCHEMA
    assert payload["count"] == 1
    assert payload["data"][0]["fusionPolicyId"] == "6548"


def test_产出按政策ID排序(isolated):
    tags_path, _ = isolated
    runner._write_tags({"6600": _tagged("6600"), "6500": _tagged("6500")}, [])

    payload = json.loads(tags_path.read_text(encoding="utf-8"))

    assert [r["fusionPolicyId"] for r in payload["data"]] == ["6500", "6600"]


def test_失败清单单独落盘(isolated):
    tags_path, output_dir = isolated
    runner._write_tags({"6548": _tagged()}, [{"fusionPolicyId": "9999", "error": "boom"}])

    failures = json.loads((output_dir / "tagging_failures.json").read_text(encoding="utf-8"))

    assert failures["count"] == 1
    assert failures["data"][0]["fusionPolicyId"] == "9999"


def test_无失败时不产出失败清单(isolated):
    tags_path, output_dir = isolated
    runner._write_tags({"6548": _tagged()}, [])

    assert not (output_dir / "tagging_failures.json").exists()


def test_原子写失败时原文件不被破坏(isolated):
    tags_path, _ = isolated
    tags_path.parent.mkdir(parents=True, exist_ok=True)
    tags_path.write_text(json.dumps({
        "schemaVersion": runner.TAGS_SCHEMA, "count": 1,
        "data": [{"fusionPolicyId": "ORIGINAL"}],
    }, ensure_ascii=False), encoding="utf-8")

    with patch("service.paths.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            runner._write_tags({"6548": _tagged()}, [])

    payload = json.loads(tags_path.read_text(encoding="utf-8"))
    assert payload["data"][0]["fusionPolicyId"] == "ORIGINAL"


def test_读取schema不匹配的旧产出返回空(isolated):
    tags_path, _ = isolated
    tags_path.parent.mkdir(parents=True, exist_ok=True)
    tags_path.write_text(json.dumps({"schemaVersion": "old", "data": [{"fusionPolicyId": "X"}]}),
                         encoding="utf-8")

    assert runner._load_existing() == {}


def test_读取损坏的旧产出返回空(isolated):
    tags_path, _ = isolated
    tags_path.parent.mkdir(parents=True, exist_ok=True)
    tags_path.write_text("{broken", encoding="utf-8")

    assert runner._load_existing() == {}


# --------------------------------------------------------------------------
# 汇总统计
# --------------------------------------------------------------------------


def test_汇总统计各维度置信度():
    stats = runner._summarize({"6548": _tagged()})

    assert stats["confidence"]["A"]["high"] == 1
    assert stats["confidence"]["E"]["low"] == 1


def test_汇总统计标签分布():
    stats = runner._summarize({"6548": _tagged()})

    assert stats["aPrimary"]["A3"] == 1
    assert stats["cMajor"]["C9"] == 1
    assert stats["dLevels"]["D1"] == 1


def test_汇总统计申报期来源():
    stats = runner._summarize({"6548": _tagged()})

    assert stats["windowSources"]["structured_field"] == 1


def test_汇总统计受众分布():
    stats = runner._summarize({"6548": _tagged()})

    assert stats["audiences"]["企业"] == 1


def test_汇总列出不可申报政策():
    """这批会被检索排除，必须显式列出供复核。"""
    not_applicable = _tagged("6503", applicability={
        "applicable": False, "reason": "面向个人专家", "audience": "个人",
    })
    stats = runner._summarize({"6503": not_applicable, "6548": _tagged()})

    assert len(stats["notApplicable"]) == 1
    assert "6503" in stats["notApplicable"][0]


def test_汇总统计警告数():
    tagged = _tagged(warnings=["警告1", "警告2"])
    stats = runner._summarize({"6548": tagged})

    assert stats["warningCount"] == 2


# --------------------------------------------------------------------------
# 复核表
# --------------------------------------------------------------------------


def test_复核表生成且含表头(isolated):
    tags_path, output_dir = isolated
    target = runner.export_review_excel({"6548": _tagged()})

    assert target.exists()

    from openpyxl import load_workbook

    sheet = load_workbook(target).active
    headers = [c.value for c in sheet[1]]
    for expected in ("政策ID", "可申报", "受众", "A主", "C大类", "C子类", "申报开始", "复核意见"):
        assert expected in headers, expected


def test_复核表一行一政策(isolated):
    target = runner.export_review_excel({
        "6548": _tagged("6548"), "6549": _tagged("6549"),
    })

    from openpyxl import load_workbook

    sheet = load_workbook(target).active
    assert sheet.max_row == 3, "2 条政策 + 表头"


def test_复核表标注不可申报(isolated):
    target = runner.export_review_excel({
        "6503": _tagged("6503", applicability={
            "applicable": False, "reason": "面向个人", "audience": "个人",
        }),
    })

    from openpyxl import load_workbook

    sheet = load_workbook(target).active
    headers = [c.value for c in sheet[1]]
    column = headers.index("可申报") + 1
    assert sheet.cell(row=2, column=column).value == "否"


def test_复核表冻结首行(isolated):
    target = runner.export_review_excel({"6548": _tagged()})

    from openpyxl import load_workbook

    assert load_workbook(target).active.freeze_panes == "C2"


def test_复核表对缺字段的记录不报错(isolated):
    minimal = {"fusionPolicyId": "X", "title": "缺字段政策"}
    target = runner.export_review_excel({"X": minimal})

    assert target.exists()


# --------------------------------------------------------------------------
# 真实产出校验
# --------------------------------------------------------------------------


def test_真实产出全部政策标签合法():
    from service import taxonomy as tx
    from service.paths import POLICY_TAGS_PATH

    if not POLICY_TAGS_PATH.exists():
        pytest.skip("尚未打标")

    payload = json.loads(POLICY_TAGS_PATH.read_text(encoding="utf-8"))
    for record in payload["data"]:
        errors = tx.validate_tag_payload(record["tags"])
        assert errors == [], f"{record['fusionPolicyId']} 标签非法：{errors}"


def test_真实产出无政策库词表污染():
    """C 维度绝不能出现「通用行业」这类原始取值（开发计划决策 10）。"""
    from service.paths import POLICY_TAGS_PATH

    if not POLICY_TAGS_PATH.exists():
        pytest.skip("尚未打标")

    payload = json.loads(POLICY_TAGS_PATH.read_text(encoding="utf-8"))
    polluted = []
    for record in payload["data"]:
        values = (record["tags"].get("C") or {}).get("values") or []
        subs = (record["tags"].get("C") or {}).get("subValues") or []
        for value in list(values) + list(subs):
            if not str(value).startswith("C") or not str(value)[1:2].isdigit():
                polluted.append((record["fusionPolicyId"], value))

    assert polluted == [], f"出现非 C 编号：{polluted[:5]}"


def test_真实产出每条都有applicability():
    from service.paths import POLICY_TAGS_PATH

    if not POLICY_TAGS_PATH.exists():
        pytest.skip("尚未打标")

    payload = json.loads(POLICY_TAGS_PATH.read_text(encoding="utf-8"))
    for record in payload["data"]:
        applicability = record.get("applicability") or {}
        assert isinstance(applicability.get("applicable"), bool), record["fusionPolicyId"]
        assert applicability.get("reason"), record["fusionPolicyId"]


def test_真实产出每条都有指纹():
    from service.paths import POLICY_TAGS_PATH

    if not POLICY_TAGS_PATH.exists():
        pytest.skip("尚未打标")

    payload = json.loads(POLICY_TAGS_PATH.read_text(encoding="utf-8"))
    for record in payload["data"]:
        assert record.get("_fingerprint"), record["fusionPolicyId"]


def test_真实产出申报期确定性来源占多数():
    from service.paths import POLICY_TAGS_PATH

    if not POLICY_TAGS_PATH.exists():
        pytest.skip("尚未打标")

    payload = json.loads(POLICY_TAGS_PATH.read_text(encoding="utf-8"))
    sources = [
        (r.get("applicationWindow") or {}).get("source") for r in payload["data"]
    ]
    deterministic = sum(1 for s in sources if s in ("structured_field", "rolling_text"))

    assert deterministic >= len(sources) * 0.9


def test_真实产出所有基础分可算():
    """打标产出要能直接喂给一期的 score_policy。"""
    from datetime import date

    from service.paths import POLICY_TAGS_PATH
    from service.profile import build_profile
    from service.scoring import score_policy

    if not POLICY_TAGS_PATH.exists():
        pytest.skip("尚未打标")

    profile = build_profile({
        "enterpriseName": "测试企业",
        "registerDate": "2016-10-13",
        "registerAddress": "厦门市思明区",
        "industryLevel2": "软件和信息技术服务业",
        "ipClass1Count": 5, "ipClass2Count": 3,
        "operationInfoList": [{"year": "2023", "employeeCount": 100, "operatingIncome": 30000000.0}],
    }, today=date(2026, 8, 3))

    payload = json.loads(POLICY_TAGS_PATH.read_text(encoding="utf-8"))
    for record in payload["data"]:
        result = score_policy(profile, record, today=date(2026, 8, 3))
        assert result["band"], record["fusionPolicyId"]
