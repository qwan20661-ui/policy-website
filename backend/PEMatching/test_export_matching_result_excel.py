"""离线匹配结果 Excel 导出脚本回归测试。"""

from __future__ import annotations

import json

from openpyxl import load_workbook

from script.export_matching_result_excel import load_matching_results, main


def _match_result() -> dict:
    return {
        "企业名称": "测试企业",
        "企业信息": {"企业ID": "E-001"},
        "政策匹配结果": [
            {
                "政策标题": "测试政策",
                "hard_pass": True,
                "final_score": 70,
                "是否推荐": 1,
                "条件匹配详情": [],
                "规则组匹配详情": [],
            }
        ],
    }


def test_load_matching_results_supports_api_archive(tmp_path) -> None:
    source_path = tmp_path / "enterprise_match.json"
    source_path.write_text(json.dumps({"matchResult": _match_result()}, ensure_ascii=False), encoding="utf-8")

    results = load_matching_results(source_path)

    assert results == [_match_result()]


def test_main_exports_api_archive_to_excel(tmp_path) -> None:
    source_path = tmp_path / "enterprise_match.json"
    output_path = tmp_path / "enterprise_match.xlsx"
    source_path.write_text(json.dumps({"matchResult": _match_result()}, ensure_ascii=False), encoding="utf-8")

    exit_code = main(["--input", str(source_path), "--output", str(output_path)])

    assert exit_code == 0
    workbook = load_workbook(output_path, data_only=True)
    assert workbook.sheetnames == [
        "匹配汇总",
        "企业条件得分",
        "总分计算说明",
        "规则组明细",
        "条件明细",
        "硬性明细",
        "直通车明细",
        "软性明细",
    ]
    assert workbook["匹配汇总"].cell(2, 1).value == "测试企业"
    assert workbook["匹配汇总"].cell(2, 2).value == "测试政策"
