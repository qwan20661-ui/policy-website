# -*- coding: utf-8 -*-
"""SYSDBA 导出 Excel → 独立政策库快照转换测试。

覆盖：字段清洗（--/NULL）、日期规范化、适用范围映射、同名政策保留、
转换后记录结构可供 policy_sync/application_window 消费。
"""

from __future__ import annotations

import json

import openpyxl

from script.convert_sysdba_policy_excel import (
    clean_date,
    convert_excel_to_records,
    map_scope_path,
    write_snapshot,
)

HEADERS = [
    "ID", "政策标题（政策标题）", "政策层级", "发布地区（适用范围）", "政策批次",
    "主管部门（发文部门）", "申请对象（适用对象）", "补贴对象", "行业类别（行业领域）",
    "企业规模", "申报类型", "支持方式（扶持类型）", "文件依据",
    "申报开始时间（申报时间）", "申报结束时间（申报时间）", "原文链接",
    "申报条件（申报要求）", "扶持内容（扶持内容）", "申报流程（申报方式）",
    "申报材料（申报材料）", "咨询电话", "相关附件（申报材料类附件）", "DEL_FLAG",
    "CREATED_AT", "UPDATED_AT",
]


def _write_excel(path, rows):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    workbook.close()


def _sample_rows():
    return [
        [
            "uuid-1", "2026年行业龙头企业年会补助", "市级", "厦门市", "2026年",
            "厦门市工业和信息化局", "企事业单位", "企事业单位", "不限行业",
            "不限规模", "线上申报", "资金扶持", "某办法的通知",
            "2026-03-05 00:00:00", "2026-11-30 23:59:59", "https://example.com/1",
            "一、申报条件\n（一）具备一定规模", "最高补助50万元", "网上申报",
            "申请表", "0592-123456", "[]", "0", "2026-08-05 02:23:10", "2026-08-05 02:23:10",
        ],
        [
            "uuid-2", "2025年某区级人才引进奖励", "海沧区", "海沧区", "2025年",
            "海沧区科技和工信商务局", "个人、企事业单位", "个人", "--",
            "--", "线下申报", "人才建设", "--",
            "NULL", "NULL", "https://example.com/2",
            "符合条件的人才可申报", "给予生活补贴", "线下提交", "", "0592-654321", "[]", "0",
            "2026-08-05 02:23:11", "2026-08-05 02:23:11",
        ],
        [
            "uuid-3", "2026年行业龙头企业年会补助", "国家级", "国家级", "2026年",
            "厦门市工业和信息化局", "企事业单位", "企事业单位", "不限行业",
            "大型企业、中型企业、小型企业", "线上申报", "资金扶持", "某办法的通知",
            "2026-03-05", "2026-11-30", "https://example.com/3",
            "申报条件", "扶持内容", "申报流程", "材料", "电话", "[]", "0",
            "2026-08-05 02:23:12", "2026-08-05 02:23:12",
        ],
    ]


def _convert(tmp_path):
    excel = tmp_path / "input.xlsx"
    _write_excel(excel, _sample_rows())
    return convert_excel_to_records(excel)


def test_转换条数与结构(tmp_path):
    records, report = _convert(tmp_path)
    assert len(records) == 3
    assert report["total"] == 3
    record = records[0]
    for key in ("fusionPolicyId", "sourcePolicyId", "title", "status", "reviewStatus",
                "policyInfo", "supportMethods", "structuredConditions",
                "materials", "relatedPolicies"):
        assert key in record
    assert record["status"] == "ACTIVE"
    assert record["reviewStatus"] == ""
    assert record["policyInfo"]["policyTitle"] == "2026年行业龙头企业年会补助"


def test_日期规范化(tmp_path):
    records, _ = _convert(tmp_path)
    condition = records[0]["structuredConditions"][0]
    assert condition["applicationStartTime"] == "2026-03-05"
    assert condition["applicationEndTime"] == "2026-11-30"
    # NULL 日期 → 空串
    condition2 = records[1]["structuredConditions"][0]
    assert condition2["applicationStartTime"] == ""
    assert condition2["applicationEndTime"] == ""


def test_占位符清洗(tmp_path):
    records, _ = _convert(tmp_path)
    record = records[1]
    assert record["policyInfo"]["industryField"] == ""
    assert record["policyInfo"]["policyOriginalTitle"] == ""
    assert record["policyInfo"]["issuingDepartment"] == "海沧区科技和工信商务局"


def test_适用范围映射(tmp_path):
    records, _ = _convert(tmp_path)
    scopes = [r["policyInfo"]["applicableScopePath"] for r in records]
    assert scopes == ["市级-厦门市", "海沧区", "国家级"]
    assert map_scope_path("厦门自贸片区", "厦门自贸片区") == "自贸片区"
    assert map_scope_path("福建省", "福建省") == "省级-福建省"
    assert map_scope_path("", "湖里区") == "湖里区"


def test_同名政策各自保留(tmp_path):
    records, report = _convert(tmp_path)
    titles = [r["title"] for r in records]
    assert titles.count("2026年行业龙头企业年会补助") == 2
    assert report["duplicateTitles"] == {"2026年行业龙头企业年会补助": 2}


def test_申报期可被确定性提取(tmp_path):
    from service.application_window import SOURCE_STRUCTURED, extract_window

    records, _ = _convert(tmp_path)
    window = extract_window(records[0])
    assert window["source"] == SOURCE_STRUCTURED
    assert window["startDate"] == "2026-03-05"
    assert window["endDate"] == "2026-11-30"
    assert window["needsLlm"] is False


def test_企业规模写入申报条件(tmp_path):
    records, _ = _convert(tmp_path)
    requirement = records[0]["structuredConditions"][0]["applicationRequirement"]
    assert "不限规模" in requirement


def test_write_snapshot(tmp_path):
    records, _ = _convert(tmp_path)
    target = write_snapshot(tmp_path / "lib", records, tmp_path / "input.xlsx")
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == "pematching-v2-policy-snapshot-v1"
    assert payload["count"] == 3
    assert len(payload["data"]) == 3
