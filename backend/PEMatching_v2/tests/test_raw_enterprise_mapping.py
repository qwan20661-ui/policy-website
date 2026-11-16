from __future__ import annotations

from service.raw_enterprise_mapping import (
    build_fact_index,
    build_raw_facts,
    map_record_to_chinese,
    select_model_facts,
)


def _record():
    return {
        "enterpriseName": "原始事实测试企业",
        "creditCode": "91350200TEST00001",
        "operationInfoList": [
            {"year": 2024, "operatingIncome": 12_500_000, "employeeCount": 35},
            {"year": 2023, "operatingIncome": 8_000_000, "employeeCount": 20},
        ],
        "honorQualificationList": [{"isHighTechEnterprise": "是"}],
        "telephone": "13800000000",
    }


def test_原始数据按V2副本映射为中文字段并展开数组路径():
    facts = build_raw_facts(_record())

    assert {fact["fieldName"] for fact in facts} >= {"企业全称", "营业收入", "员工总人数", "高新技术企业"}
    revenue_paths = [fact["sourcePath"] for fact in facts if fact["fieldName"] == "营业收入"]
    assert revenue_paths == ["$.operationInfoList[0].operatingIncome", "$.operationInfoList[1].operatingIncome"]


def test_完整原始数据映射为中文且保留经营记录关联关系():
    index = build_fact_index(_record())
    mapped = map_record_to_chinese(_record())

    assert mapped["企业全称"] == "原始事实测试企业"
    assert mapped["经营信息列表"] == [
        {"统计年份": 2024, "营业收入": 12_500_000, "员工总人数": 35},
        {"统计年份": 2023, "营业收入": 8_000_000, "员工总人数": 20},
    ]
    assert mapped["企业联系电话"] == "13800000000"
    assert index["mappedRecord"] == mapped


def test_模型可按中文字段名快速读取原始事实():
    index = build_fact_index(_record())
    facts = select_model_facts(index, ["营业收入", "高新技术企业", "企业联系电话"])

    assert facts == [
        {"fieldName": "企业联系电话", "value": "13800000000"},
        {"fieldName": "营业收入", "value": 12_500_000},
        {"fieldName": "营业收入", "value": 8_000_000},
        {"fieldName": "高新技术企业", "value": "是"},
    ]
    assert "营业收入" in index["availableFieldNames"]
