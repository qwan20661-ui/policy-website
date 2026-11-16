"""Excel 业务评分视图回归测试。"""

from __future__ import annotations

from copy import deepcopy

from openpyxl import load_workbook

from service.matching_service import _write_excel_output


def test_excel_contains_readable_condition_scores_and_formula(tmp_path):
    results = [
        {
            "企业名称": "测试企业",
            "企业信息": {"企业ID": "E-001"},
            "政策匹配结果": [
                {
                    "政策标题": "测试政策",
                    "hard_pass": True,
                    "score_A": 100,
                    "score_B": 100,
                    "score_A_direct": 100,
                    "score_B_indirect": None,
                    "未核验证据条件得分": None,
                    "评分聚合算法": "runtime_evidence_tiering_balanced_mean_v1",
                    "coeff_C": 1.0,
                    "raw_score": 100,
                    "final_score": 100,
                    "special_pass_hit": False,
                    "总体建议": "可进入人工复核。",
                    "条件匹配详情": [
                        {
                            "conditionNo": 1,
                            "itemId": "1-1",
                            "条件名称": "企业信用",
                            "政策要求": "无重大违法",
                            "企业字段": "企业信用记录",
                            "企业值": "未发现重大违法",
                            "评分组别": "A",
                            "计分归类": "verified",
                            "evidenceTier": "A1_exact",
                            "bucketReason": "运行时企业事实与政策条件可直接比较。",
                            "规则表原始ruleKind": "reference",
                            "规则类型": "rule",
                            "evalStatus": "pass",
                            "得分": 100.0,
                            "得分说明": "硬性正式判断项：命中记 100 分。",
                            "说明": "企业信用记录支持该结论。",
                            "证据文本": ["未发现重大违法"],
                        }
                    ],
                    "规则组匹配详情": [
                        {
                            "conditionNo": 1,
                            "groupType": "硬性",
                            "groupStatus": "pass",
                            "conditionOperator": "AND",
                            "rawText": "无重大违法",
                            "items": [
                                {
                                    "itemId": "1-1",
                                    "itemName": "企业信用",
                                    "itemStatus": "rule",
                                    "evalStatus": "pass",
                                    "score": 100,
                                    "message": "企业信用记录支持该结论。",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    ]

    output_path = tmp_path / "匹配结果.xlsx"
    _write_excel_output(results, output_path)

    workbook = load_workbook(output_path, data_only=True)
    assert "企业条件得分" in workbook.sheetnames
    assert "总分计算说明" in workbook.sheetnames

    score_sheet = workbook["企业条件得分"]
    headers = [cell.value for cell in score_sheet[1]]
    row = [cell.value for cell in score_sheet[2]]
    assert "本条件得分（100分制）" in headers
    assert "本条件如何得分" in headers
    assert row[headers.index("判定结论")] == "命中"
    assert row[headers.index("本条件得分（100分制）")] == 100
    assert row[headers.index("基础分贡献")] == 100
    assert row[headers.index("运行时证据层级")] == "A类（运行时精确/派生证据）"
    assert row[headers.index("证据分层原因")] == "运行时企业事实与政策条件可直接比较。"
    assert row[headers.index("规则表原始ruleKind（仅审计）")] == "reference"
    assert row[headers.index("单条件实际权重")] == "100.00%"
    assert "A/B证据层级不改变权重" in row[headers.index("本条件对总分的作用")]

    formula_sheet = workbook["总分计算说明"]
    formula_headers = [cell.value for cell in formula_sheet[1]]
    formula_text = formula_sheet.cell(2, formula_headers.index("本次总分计算公式") + 1).value
    assert "coeff_C 固定为 1.0" in formula_text
    assert "每条条件同权" in formula_text

    legacy_results = deepcopy(results)
    legacy_results[0]["政策匹配结果"][0]["条件匹配详情"][0].pop("计分归类")
    legacy_path = tmp_path / "历史匹配结果.xlsx"
    _write_excel_output(legacy_results, legacy_path)
    legacy_workbook = load_workbook(legacy_path, data_only=True)
    legacy_formula_sheet = legacy_workbook["总分计算说明"]
    legacy_formula_headers = [cell.value for cell in legacy_formula_sheet[1]]
    legacy_formula = legacy_formula_sheet.cell(2, legacy_formula_headers.index("本次总分计算公式") + 1).value
    assert "无法可靠还原条件贡献" in legacy_formula
    legacy_score_sheet = legacy_workbook["企业条件得分"]
    legacy_score_headers = [cell.value for cell in legacy_score_sheet[1]]
    assert legacy_score_sheet.cell(2, legacy_score_headers.index("运行时证据层级") + 1).value == "历史结果未记录计分归类"
