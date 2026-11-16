# -*- coding: utf-8 -*-
"""条件匹配批处理命令的实时进度输出测试。"""

from __future__ import annotations

import json

import run_condition_match_batch as cli


def test_CLI默认输出进度且最终汇总保持在stdout(tmp_path, monkeypatch, capsys):
    source = tmp_path / "enterprise.json"
    source.write_text(json.dumps({"enterpriseName": "测试企业"}, ensure_ascii=False), encoding="utf-8")

    def fake_batch(records, **kwargs):
        callback = kwargs["progress_callback"]
        callback({
            "event": "enterprise_started", "enterpriseIndex": 1, "enterpriseTotal": 1,
            "enterpriseName": "测试企业", "policyCount": 2, "candidateCount": 1,
            "prefilterExcludedCount": 1, "policyWorkers": 1,
        })
        callback({
            "event": "policy_completed", "enterpriseIndex": 1, "enterpriseTotal": 1,
            "completed": 1, "total": 1, "fusionPolicyId": "P1", "policyTitle": "测试政策",
            "outcome": "recommended", "finalScore": 88, "modelElapsedMs": 1200,
            "queueWaitMs": 0, "cacheHit": False, "retryCount": 0,
        })
        callback({
            "event": "enterprise_completed", "enterpriseIndex": 1, "enterpriseTotal": 1,
            "enterpriseName": "测试企业", "elapsedMs": 1300, "recommendationCount": 1,
            "returnedCount": 1, "modelCallCount": 1, "cacheHitCount": 0, "failedCount": 0,
        })
        return {
            "data": {"results": [{
                "success": True, "meta": {"elapsedMs": 1300, "modelCallCount": 1, "cacheHitCount": 0},
                "data": {"recommendations": [{}]},
            }]},
            "meta": {"succeeded": 1, "failed": 0},
        }

    monkeypatch.setattr(cli, "run_condition_match_batch", fake_batch)

    assert cli.main(["--file", str(source), "--output", str(tmp_path / "output")]) == 0

    captured = capsys.readouterr()
    assert "[政策 1/1]" in captured.err
    assert "可推荐" in captured.err
    assert '"succeeded": 1' in captured.out


def test_CLI_quiet不注册进度回调(tmp_path, monkeypatch, capsys):
    source = tmp_path / "enterprise.json"
    source.write_text(json.dumps({"enterpriseName": "测试企业"}, ensure_ascii=False), encoding="utf-8")

    def fake_batch(_records, **kwargs):
        assert kwargs["progress_callback"] is None
        return {"data": {"results": []}, "meta": {"succeeded": 0, "failed": 0}}

    monkeypatch.setattr(cli, "run_condition_match_batch", fake_batch)

    assert cli.main(["--file", str(source), "--output", str(tmp_path / "output"), "--quiet"]) == 0
    assert capsys.readouterr().err == ""


def test_HTML导出按新schema渲染推荐失败与排除(tmp_path):
    """新链路 viewer 应渲染推荐政策、逐条件账目、失败政策、预过滤排除、企业原始数据。"""
    from service.report_export import export_condition_match_viewer

    results = [{
        "success": True,
        "enterpriseName": "测试企业",
        "enterpriseInput": {"enterpriseName": "测试企业", "revenueWan": 100},
        "data": {
            "enterpriseName": "测试企业",
            "recommendations": [{
                "fusionPolicyId": "6543",
                "policyTitle": "厦门市生物医药产业人才项目认定",
                "rank": 1,
                "finalScore": 88.0,
                "conditionScore": 80.0,
                "timelinessScore": 50.0,
                "reason": "已核验 1 个条件",
                "applicationWindow": {"status": "last_year", "startTime": "2025.05.21", "endTime": "2025.07.04"},
                "audit": {
                    "modelName": "qwen3-8b",
                    "conditionResults": [{
                        "conditionKey": "soft:1:4/S4-1",
                        "sourceType": "rule_table",
                        "sourceText": "工资薪金总额不低于30万元",
                        "groupKey": "soft:1:4", "groupType": "soft", "groupOperator": "AND",
                        "status": "satisfied", "failureEffect": "none", "score": 100.0,
                        "evidence": [{"path": "$.metrics.salaryWan", "value": 50, "unit": "万元"}],
                        "reason": "满足门槛", "confidence": "high",
                        "scoreSource": "server_numeric", "serverMessage": "服务端复算满足",
                    }],
                    "groupResults": [{
                        "groupKey": "soft:1:4", "groupType": "soft", "operator": "AND",
                        "score": 100.0, "conditionKeys": ["soft:1:4/S4-1"],
                    }],
                    "sourceConflicts": [],
                },
            }],
        },
        "meta": {
            "policyCount": 66, "candidateCount": 66, "excludedCount": 23,
            "failedCount": 1, "allRecommendationCount": 1, "elapsedMs": 1000,
            "failures": [{"fusionPolicyId": "6543", "error": "模型判断失败"}],
            "prefilterExclusions": [{
                "fusionPolicyId": "6462",
                "reason": {"code": "SUBJECT_NOT_ENTERPRISE", "message": "申报对象非企业", "evidence": [{"text": "个人；组织"}]},
            }],
        },
    }]

    target = export_condition_match_viewer(tmp_path, results, {"succeeded": 1, "failed": 0, "enterpriseCount": 1})
    content = target.read_text(encoding="utf-8")

    assert target.name == "condition_match_viewer.html"
    assert "政策条件匹配结果查看" in content
    assert "厦门市生物医药产业人才项目认定" in content          # 推荐政策标题
    assert "工资薪金总额不低于30万元" in content                  # 逐条件原文
    assert "模型判断失败" in content                              # 失败政策
    assert "申报对象非企业" in content                            # 预过滤排除
    assert "测试企业" in content                                  # 企业原始数据

