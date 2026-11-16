# -*- coding: utf-8 -*-
"""批跑 CLI 测试。run_match 被 mock，只验批跑逻辑（续跑、容错、汇总、Excel）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run_tagmatch_batch as batch


def _record(name="测试企业"):
    return {
        "enterpriseName": name,
        "industryLevel1": "I 信息传输、软件和信息技术服务业",
        "industryLevel2": "软件和信息技术服务业",
        "registerDate": "2016-10-13 00:00:00",
        "registerAddress": "厦门市思明区高雄路20号",
        "operationInfoList": [{"year": "2023", "employeeCount": 100}],
    }


def _result(name="测试企业", degraded=False, truncated=False,
            elapsed=1200, turns=5, bands=("direct_push", "needs_supplement")):
    recommendations = [
        {
            "fusionPolicyId": f"P{index}", "rank": index + 1,
            "policyTitle": f"政策{index}", "conditionMatchScore": 90.0 - index,
            "gateStatus": band, "directPushAllowed": band == "direct_push",
            "gateReasons": [], "conditionDetails": [],
            "matchedRules": ["R09"],
            "policyEvidence": [{
                "ruleId": "R09", "matchedKeywords": ["软件"],
                "basis": "规则信号关键词与政策关键词存在直接交集",
            }],
            "reason": "理由", "reasonSource": "template",
        }
        for index, band in enumerate(bands)
    ]
    return {
        "data": {
            "enterprise": {"enterpriseName": name, "tags": {}, "dataCompleteness": {}},
            "gateResult": {"passed": True, "creditChecked": False},
            "channelResults": {"G2": {"matched": [{"ruleId": "R09"}], "skipped": [{"ruleId": "R17"}]}},
            "aggregatedSignals": {},
            "recommendations": recommendations,
            "eligibilityExclusions": [],
            "eligibilityResult": {
                "eligibilityEvaluations": [
                    {
                        "fusionPolicyId": f"P{index}",
                        "policyTitle": f"政策{index}",
                        "conditionMatchScore": 90.0 - index,
                        "gateStatus": band,
                        "directPushAllowed": band == "direct_push",
                        "gateReasons": [], "conditionDetails": [],
                    }
                    for index, band in enumerate(bands)
                ],
                "eligibilitySchemaVersion": "pematching-v2-eligibility-result-v1",
                "ruleManifestHash": "fixture-hash",
            },
            "supplementSuggestions": [], "unmatchedSignals": [],
            "summary": "总结", "candidateCount": 30, "scoredCount": 30,
            "recommendableCount": 2,
        },
        "meta": {
            "mode": "agent", "requestId": "abc123", "elapsedMs": elapsed, "turns": turns,
            "degraded": degraded, "truncated": truncated,
            "degradedReason": "truncated" if truncated else None,
            "dataCompleteness": {"availableFields": 29, "unknownFields": 28},
        },
    }


def _eligibility_result(*policy_ids):
    return {
        "eligibilityEvaluations": [
            {
                "fusionPolicyId": policy_id,
                "policyTitle": f"资格政策{index}",
                "conditionMatchScore": 80.0 - index,
                "gateStatus": "direct_push" if index == 0 else "needs_supplement",
                "directPushAllowed": index == 0,
                "hardPass": True,
                "specialPassHit": False,
                "ruleCoverage": "complete",
                "scoredConditionCount": 2,
                "pendingConditionCount": index,
                "failedConditionCount": 0,
                "defaultMissingCount": index,
                "evidenceCoverage": 1.0,
                "gateReasons": [] if index == 0 else ["MISSING_ENTERPRISE_FACT"],
                "conditionDetails": [],
                "ruleGroupDetails": [],
            }
            for index, policy_id in enumerate(policy_ids)
        ],
        "eligibilitySchemaVersion": "pematching-v2-eligibility-result-v1",
        "ruleManifestHash": "fixture-hash",
    }


def _write_records(directory: Path, count: int) -> None:
    for index in range(count):
        (directory / f"{index:04d}_企业.json").write_text(
            json.dumps(_record(f"企业{index}"), ensure_ascii=False), encoding="utf-8"
        )


# --------------------------------------------------------------------------
# 记录加载
# --------------------------------------------------------------------------


def test_从目录加载(tmp_path):
    _write_records(tmp_path, 3)

    records = batch._load_records(tmp_path, None, None)

    assert len(records) == 3


def test_limit生效(tmp_path):
    _write_records(tmp_path, 5)

    assert len(batch._load_records(tmp_path, None, 2)) == 2


def test_跳过旧格式与损坏文件(tmp_path):
    _write_records(tmp_path, 1)
    (tmp_path / "old.json").write_text(json.dumps({"name": "旧格式"}), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{bad", encoding="utf-8")

    assert len(batch._load_records(tmp_path, None, None)) == 1


def test_单文件加载(tmp_path):
    path = tmp_path / "one.json"
    path.write_text(json.dumps(_record("甲"), ensure_ascii=False), encoding="utf-8")

    records = batch._load_records(None, path, None)

    assert len(records) == 1
    assert records[0][1]["enterpriseName"] == "甲"


def test_单文件含企业数组(tmp_path):
    path = tmp_path / "many.json"
    path.write_text(
        json.dumps([_record("甲"), _record("乙")], ensure_ascii=False), encoding="utf-8"
    )

    records = batch._load_records(None, path, None)

    assert len(records) == 2
    assert [name for name, _ in records] == ["many#0", "many#1"]


def test_文件名安全化():
    assert "/" not in batch._safe_name("a/b：c*d")
    assert batch._safe_name("") == "unnamed"
    assert len(batch._safe_name("很长" * 50)) <= 40


# --------------------------------------------------------------------------
# 进度与续跑
# --------------------------------------------------------------------------


def test_进度文件不存在时返回空(tmp_path):
    progress = batch._load_progress(tmp_path)

    assert progress == {"done": [], "failed": []}


def test_进度读写往返(tmp_path):
    batch._save_progress(tmp_path, {"done": ["a.json"], "failed": []})

    progress = batch._load_progress(tmp_path)

    assert progress["done"] == ["a.json"]
    assert (tmp_path / batch.PROGRESS_NAME).exists()


def test_损坏的进度文件返回空(tmp_path):
    (tmp_path / batch.PROGRESS_NAME).write_text("{broken", encoding="utf-8")

    assert batch._load_progress(tmp_path)["done"] == []


def test_续跑跳过已完成(tmp_path, monkeypatch, capsys):
    _write_records(tmp_path, 3)
    run_root = tmp_path / "out"
    monkeypatch.setattr(batch, "BATCH_ROOT", run_root)

    args = [
        "run_tagmatch_batch.py", "--mode", "deterministic",
        "--dir", str(tmp_path), "--top-n", "2",
    ]
    with patch.object(sys, "argv", args), \
         patch.object(batch, "run_match", side_effect=lambda record, **kw: _result(
             record["enterpriseName"]
         )):
        batch.main()
        first = capsys.readouterr().out
        run_id = first.split("--resume ")[-1].strip()

        args_resume = args + ["--resume", run_id]
        with patch.object(sys, "argv", args_resume):
            batch.main()
        second = capsys.readouterr().out

    assert "续跑跳过 3 家" in second
    assert "全部已完成" in second


def test_续跑读回已完成结果用于汇总(tmp_path, monkeypatch, capsys):
    """续跑时汇总必须包含之前完成的，否则统计不完整。"""
    _write_records(tmp_path, 2)
    monkeypatch.setattr(batch, "BATCH_ROOT", tmp_path / "out")

    args = ["run_tagmatch_batch.py", "--mode", "deterministic",
            "--dir", str(tmp_path), "--top-n", "2"]
    with patch.object(sys, "argv", args), \
         patch.object(batch, "run_match", side_effect=lambda record, **kw: _result(
             record["enterpriseName"]
         )):
        batch.main()
        run_id = capsys.readouterr().out.split("--resume ")[-1].strip()
        with patch.object(sys, "argv", args + ["--resume", run_id]):
            batch.main()
        output = capsys.readouterr().out

    assert "成功 2" in output


# --------------------------------------------------------------------------
# 容错
# --------------------------------------------------------------------------


def test_单企业失败不中断整批(tmp_path, monkeypatch, capsys):
    _write_records(tmp_path, 3)
    monkeypatch.setattr(batch, "BATCH_ROOT", tmp_path / "out")

    def flaky(record, **kwargs):
        if record["enterpriseName"] == "企业1":
            raise RuntimeError("模拟失败")
        return _result(record["enterpriseName"])

    args = ["run_tagmatch_batch.py", "--mode", "deterministic",
            "--dir", str(tmp_path), "--top-n", "2"]
    with patch.object(sys, "argv", args), patch.object(batch, "run_match", side_effect=flaky):
        batch.main()

    output = capsys.readouterr().out
    assert "成功 2" in output
    assert "失败 1" in output


def test_失败企业不进done列表(tmp_path, monkeypatch, capsys):
    """失败的下次续跑要重试。"""
    _write_records(tmp_path, 2)
    monkeypatch.setattr(batch, "BATCH_ROOT", tmp_path / "out")

    def flaky(record, **kwargs):
        if record["enterpriseName"] == "企业0":
            raise RuntimeError("boom")
        return _result(record["enterpriseName"])

    args = ["run_tagmatch_batch.py", "--mode", "deterministic",
            "--dir", str(tmp_path), "--top-n", "2"]
    with patch.object(sys, "argv", args), patch.object(batch, "run_match", side_effect=flaky):
        batch.main()

    run_id = capsys.readouterr().out.split("--resume ")[-1].strip()
    progress = batch._load_progress(tmp_path / "out" / run_id)

    assert len(progress["done"]) == 1
    assert len(progress["failed"]) == 1


def test_空目录返回错误码(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(batch, "BATCH_ROOT", tmp_path / "out")
    args = ["run_tagmatch_batch.py", "--dir", str(tmp_path)]

    with patch.object(sys, "argv", args):
        assert batch.main() == 1

    assert "没有可用企业样本" in capsys.readouterr().out


# --------------------------------------------------------------------------
# 汇总统计
# --------------------------------------------------------------------------


def test_汇总档位分布():
    results = [
        {"success": True, **_result(bands=("direct_push", "direct_push"))},
        {"success": True, **_result(bands=("needs_supplement",))},
    ]

    summary = batch._summarize(results)

    assert summary["bands"]["direct_push"] == 2
    assert summary["bands"]["needs_supplement"] == 1


def test_汇总统计降级与超轮次():
    results = [
        {"success": True, **_result(degraded=True)},
        {"success": True, **_result(truncated=True, degraded=True)},
        {"success": True, **_result()},
    ]

    summary = batch._summarize(results)

    assert summary["degraded"] == 2
    assert summary["truncated"] == 1


def test_汇总耗时分位():
    results = [
        {"success": True, **_result(elapsed=elapsed)}
        for elapsed in (100, 200, 300, 400, 500)
    ]

    summary = batch._summarize(results)

    assert summary["elapsedMs"]["p50"] == 300
    assert summary["elapsedMs"]["max"] == 500


def test_汇总跳过失败项():
    results = [
        {"success": False, "error": "boom"},
        {"success": True, **_result()},
    ]

    summary = batch._summarize(results)

    assert summary["degraded"] == 0
    assert sum(summary["bands"].values()) == 2


def test_汇总统计G0拦截():
    blocked = _result()
    blocked["data"]["gateResult"]["passed"] = False
    blocked["data"]["recommendations"] = []

    summary = batch._summarize([{"success": True, **blocked}])

    assert summary["gateBlocked"] == 1
    assert summary["noRecommendation"] == 1


def test_汇总空结果不报错():
    summary = batch._summarize([])

    assert summary["bands"] == {}
    assert summary["elapsedMs"]["p50"] is None
    assert summary["eligibility"]["evaluatedEnterprises"] == 0


def test_汇总资格门禁状态():
    payload = _result()
    payload["data"]["eligibilityResult"] = _eligibility_result("P0", "P1")
    payload["meta"]["eligibility"] = {"requested": True, "evaluated": True}

    summary = batch._summarize([{"success": True, **payload}])

    assert summary["eligibility"]["evaluatedEnterprises"] == 1
    assert summary["eligibility"]["statuses"] == {"direct_push": 1, "needs_supplement": 1}


# --------------------------------------------------------------------------
# Excel 导出
# --------------------------------------------------------------------------


def test_Excel三张表页(tmp_path):
    results = [{"success": True, "source": "a.json", **_result("甲")}]
    summary = batch._summarize(results)

    target = batch.export_excel(tmp_path, results, summary)

    from openpyxl import load_workbook

    titles = load_workbook(target).sheetnames
    assert "企业汇总" in titles
    assert "推荐明细" in titles
    assert "批次统计" in titles


def test_Excel汇总页一行一企业(tmp_path):
    results = [
        {"success": True, "source": "a.json", **_result("甲")},
        {"success": True, "source": "b.json", **_result("乙")},
    ]

    target = batch.export_excel(tmp_path, results, batch._summarize(results))

    from openpyxl import load_workbook

    sheet = load_workbook(target)["企业汇总"]
    assert sheet.max_row == 3


def test_Excel明细页一行一推荐(tmp_path):
    results = [{"success": True, "source": "a.json",
                **_result("甲", bands=("direct_push", "needs_supplement"))}]

    target = batch.export_excel(tmp_path, results, batch._summarize(results))

    from openpyxl import load_workbook

    sheet = load_workbook(target)["推荐明细"]
    assert sheet.max_row == 3


def test_Excel含失败企业行(tmp_path):
    results = [{"success": False, "source": "bad.json", "enterpriseName": "乙",
                "mode": "agent", "requestId": "r1", "error": "RuntimeError: boom"}]

    target = batch.export_excel(tmp_path, results, batch._summarize(results))

    from openpyxl import load_workbook

    sheet = load_workbook(target)["企业汇总"]
    row = [cell.value for cell in sheet[2]]
    assert "乙" in row
    assert "否" in row


def test_Excel统计页含关键指标(tmp_path):
    results = [{"success": True, "source": "a.json", **_result("甲")}]

    target = batch.export_excel(tmp_path, results, batch._summarize(results))

    from openpyxl import load_workbook

    sheet = load_workbook(target)["批次统计"]
    labels = [sheet.cell(row=row, column=1).value for row in range(1, sheet.max_row + 1)]
    for expected in ("企业总数", "降级", "耗时 P95 ms", "Top1 符合度 P50"):
        assert expected in labels, expected


def test_Excel明细页展示符合度与档位(tmp_path):
    payload = _result("甲")
    result = {"success": True, "source": "a.json", **payload}

    target = batch.export_excel(tmp_path, [result], batch._summarize([result]))

    from openpyxl import load_workbook

    sheet = load_workbook(target, data_only=True)["推荐明细"]
    headers = [cell.value for cell in sheet[1]]
    assert sheet.cell(row=2, column=headers.index("符合度") + 1).value == 90.0
    assert sheet.cell(row=2, column=headers.index("档位") + 1).value == "direct_push"


def test_批次同时导出详细链路证据和简易报告(tmp_path):
    payload = _result("甲", bands=("needs_supplement",))
    payload["data"]["eligibilityResult"] = _eligibility_result("P0")
    results = [{"success": True, "source": "a.json", **payload}]

    detailed, simplified = batch.export_evidence_reports(
        tmp_path, results, batch._summarize(results)
    )

    from openpyxl import load_workbook

    detailed_book = load_workbook(detailed, data_only=True)
    simple_book = load_workbook(simplified, data_only=True)
    assert detailed_book.sheetnames == [
        "批次概览", "推荐证据", "企业规则证据", "政策级证据", "排除候选", "企业画像",
        "资格门禁", "资格条件明细", "资格评分公式",
    ]
    assert simple_book.sheetnames == [
        "01_企业原始映射", "02_推荐总览", "03_政策条件评分账目", "04_政策评分汇总",
        "05_V2推理与规则证据", "06_政策标签证据",
    ]
    assert simple_book["02_推荐总览"].max_row == 4
    assert simple_book["02_推荐总览"].cell(row=4, column=5).value == 90.0  # 申报要求符合度
    raw_headers = [cell.value for cell in simple_book["01_企业原始映射"][3]]
    assert raw_headers == ["企业名称", "数据分组", "记录序号", "中文字段", "原始取值"]


def test_资格Excel展示逐条件证据换算与公式(tmp_path):
    payload = _result("甲", bands=("needs_supplement",))
    eligibility = _eligibility_result("P0")
    entry = eligibility["eligibilityEvaluations"][0]
    entry.update({
        "conditionMatchScore": 34.8,
        "modelEvidence": {"requested": True, "resolvedConditionCount": 1},
        "conditionDetails": [{
            "semanticItemKey": "P0:1", "itemId": "2-1", "itemName": "年主营业务收入",
            "operator": "gte", "enterpriseValue": 23995200.0, "enterpriseUnit": "元",
            "targetValue": 5, "targetUnit": "亿元", "normalisedEnterpriseValue": 0.239952,
            "normalisedTargetValue": 5.0,
            "conversionFormula": "23995200元 × 1e-08 = 0.239952亿元",
            "evalStatus": "fail", "score": 0.348, "contributesToScore": True,
            "passSource": "exact_rule", "message": "企业事实未达到软性条件，按 V1 口径记分。",
            "evidence": {
                "evidenceTier": "A1_exact", "value": 23995200.0,
                "unit": "元", "sourcePaths": ["$._eligibilityEvidence.operation.mainBusinessIncome"],
                "semanticCategory": "financial.main_business_revenue",
                "reason": "模型逐条件选取企业事实，服务端已回读证据路径",
                "modelEvidence": {"factStatus": "exact", "evidenceText": "主营业务收入为 23995200 元"},
            },
        }],
        "ruleGroupDetails": [{
            "groupType": "softRuleGroups", "conditionNo": 2, "conditionBranchId": "C2-B1",
            "rawText": "年主营业务收入原则上应达到5亿元以上（含）",
            "items": [{"semanticItemKey": "P0:1", "itemId": "2-1", "itemName": "年主营业务收入"}],
        }],
    })
    payload["data"]["eligibilityResult"] = eligibility
    payload["meta"]["eligibility"] = {"requested": True, "evaluated": True}
    results = [{"success": True, "source": "a.json", **payload}]

    detailed, simplified = batch.export_evidence_reports(tmp_path, results, batch._summarize(results))

    from openpyxl import load_workbook

    detailed_book = load_workbook(detailed, data_only=True)
    headers = [cell.value for cell in detailed_book["资格条件明细"][1]]
    condition_row = detailed_book["资格条件明细"][2]
    assert condition_row[headers.index("企业事实值")].value == 23995200.0
    assert condition_row[headers.index("单位换算过程")].value == "23995200元 × 1e-08 = 0.239952亿元"
    assert condition_row[headers.index("判定状态")].value == "不满足"
    assert condition_row[headers.index("单项得分（百分制）")].value == "34.80"
    assert condition_row[headers.index("模型证据说明")].value == "主营业务收入为 23995200 元"
    formula = detailed_book["资格评分公式"].cell(row=2, column=9).value
    assert "最终资格条件分" in formula
    assert "34.80" in formula
    simple_book = load_workbook(simplified, data_only=True)
    headers = [cell.value for cell in simple_book["03_政策条件评分账目"][3]]
    condition_row = simple_book["03_政策条件评分账目"][4]
    assert condition_row[headers.index("企业原始事实值")].value == "23995200.0元"
    assert condition_row[headers.index("可比企业值/单位换算")].value == "0.239952亿元\n换算：23995200元 × 1e-08 = 0.239952亿元"
    assert condition_row[headers.index("判定状态")].value == "不满足"
    assert condition_row[headers.index("单项条件分（0-100）")].value == 34.8
    formula_row = simple_book["04_政策评分汇总"][4]
    formula_headers = [cell.value for cell in simple_book["04_政策评分汇总"][3]]
    assert "34.80" in formula_row[formula_headers.index("最终分公式")].value


def test_离线查看页融合逐条件证据(tmp_path):
    payload = _result("甲", bands=("needs_supplement",))
    eligibility = _eligibility_result("P0")
    entry = eligibility["eligibilityEvaluations"][0]
    entry.update({
        "conditionMatchScore": 10.0,
        "conditionDetails": [{
            "semanticItemKey": "P0:1", "itemId": "2-1", "itemName": "年销售收入",
            "operator": "gte", "enterpriseValue": 2399.52, "enterpriseUnit": "万元",
            "targetValue": 300, "targetUnit": "万元", "normalisedEnterpriseValue": 2399.52,
            "evalStatus": "pass", "score": 1.0, "contributesToScore": True,
            "passSource": "exact_rule", "message": "企业年销售收入达到政策要求。",
            "evidence": {"evidenceTier": "A1_exact", "sourcePaths": ["$.operationInfoList[0].operatingIncome"]},
        }],
        "ruleGroupDetails": [{
            "conditionNo": 1, "rawText": "年销售收入达到300万元以上",
            "items": [{"semanticItemKey": "P0:1", "itemId": "2-1", "itemName": "年销售收入"}],
        }],
    })
    payload["data"]["eligibilityResult"] = eligibility
    results = [{"success": True, "source": "a.json", "enterpriseInput": {"enterpriseName": "甲"}, **payload}]

    target = batch.export_evidence_viewer(tmp_path, results, batch._summarize(results))

    content = target.read_text(encoding="utf-8")
    assert "政策推荐证据查看" in content
    assert "年销售收入达到300万元以上" in content
    assert "企业年销售收入达到政策要求。" in content
    assert "政策条件匹配分" in content


def test_批跑透传推荐结果含资格评估(tmp_path, monkeypatch, capsys):
    _write_records(tmp_path, 1)
    monkeypatch.setattr(batch, "BATCH_ROOT", tmp_path / "out")
    recommendation = _result("企业0")
    expected_scores = [item["conditionMatchScore"] for item in recommendation["data"]["recommendations"]]
    args = ["run_tagmatch_batch.py", "--mode", "deterministic", "--dir", str(tmp_path), "--top-n", "2"]
    with patch.object(sys, "argv", args), \
         patch.object(batch, "run_match", return_value=recommendation):
        assert batch.main() == 0

    run_id = capsys.readouterr().out.split("--resume ")[-1].strip()
    payload = json.loads(next((tmp_path / "out" / run_id).glob("*.json")).read_text(encoding="utf-8"))
    data = payload["data"]
    assert [item["conditionMatchScore"] for item in data["recommendations"]] == expected_scores
    assert [item["fusionPolicyId"] for item in data["eligibilityResult"]["eligibilityEvaluations"]] == ["P0", "P1"]


def test_批跑资格模型开关透传给run_match(tmp_path, monkeypatch, capsys):
    _write_records(tmp_path, 1)
    monkeypatch.setattr(batch, "BATCH_ROOT", tmp_path / "out")
    captured = {}

    def run_match_with_model(*_args, **kwargs):
        captured["use_llm_evidence"] = kwargs.get("use_llm_evidence")
        return _result("企业0")

    args = [
        "run_tagmatch_batch.py", "--mode", "deterministic", "--dir", str(tmp_path),
        "--top-n", "2", "--eligibility-llm",
    ]
    with patch.object(sys, "argv", args), \
         patch.object(batch, "run_match", side_effect=run_match_with_model):
        assert batch.main() == 0

    capsys.readouterr()
    assert captured["use_llm_evidence"] is True


# --------------------------------------------------------------------------
# 端到端（deterministic，不 mock）
# --------------------------------------------------------------------------


def test_端到端deterministic批跑(tmp_path, monkeypatch, capsys):
    """不 mock run_match，真实跑通完整链路。"""
    _write_records(tmp_path, 3)
    monkeypatch.setattr(batch, "BATCH_ROOT", tmp_path / "out")

    args = ["run_tagmatch_batch.py", "--mode", "deterministic",
            "--dir", str(tmp_path), "--top-n", "3", "--today", "2026-08-03"]
    with patch.object(sys, "argv", args):
        assert batch.main() == 0

    output = capsys.readouterr().out
    assert "成功 3" in output
    run_id = output.split("--resume ")[-1].strip()
    run_dir = tmp_path / "out" / run_id
    assert (run_dir / "summary.xlsx").exists()
    assert (run_dir / "推荐链路证据.xlsx").exists()
    assert (run_dir / "推荐链路证据_简易版.xlsx").exists()
    assert (run_dir / "推荐链路证据.html").exists()
    assert (run_dir / "_summary.json").exists()
    assert (run_dir / batch.PROGRESS_NAME).exists()


def test_端到端产出单企业JSON(tmp_path, monkeypatch, capsys):
    _write_records(tmp_path, 2)
    monkeypatch.setattr(batch, "BATCH_ROOT", tmp_path / "out")

    args = ["run_tagmatch_batch.py", "--mode", "deterministic",
            "--dir", str(tmp_path), "--top-n", "2", "--today", "2026-08-03"]
    with patch.object(sys, "argv", args):
        batch.main()

    run_id = capsys.readouterr().out.split("--resume ")[-1].strip()
    files = list((tmp_path / "out" / run_id).glob("*.json"))
    payloads = [
        json.loads(f.read_text(encoding="utf-8"))
        for f in files if not f.name.startswith("_")
    ]

    assert len(payloads) == 2
    for payload in payloads:
        assert payload["success"] is True
        data = payload["data"]
        assert data["scoredCount"] >= len(data["recommendations"])
        assert all(item["gateStatus"] in ("direct_push", "needs_supplement")
                   for item in data["recommendations"])
        assert data["eligibilityResult"]["eligibilityEvaluations"]
