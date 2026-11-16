"""政策推荐企业接口适配测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from service.enterprise_loader import load_enterprise_from_record, load_enterprises_from_api
from service.rule_engine_service import _calculate_tech_sme_evaluation


class _Headers:
    def get_content_charset(self) -> str:
        return "utf-8"


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.headers = _Headers()

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def _enterprise_record(name: str, enterprise_id: str) -> dict:
    return {
        "id": enterprise_id,
        "enterpriseName": name,
        "creditCode": f"91350200{enterprise_id:0>10}",
        "registerDate": "2020-03-23 00:00:00",
        "registerAddress": "厦门市思明区软件园",
        "registerCapital": "5000",
        "industryField": "软件和信息技术服务业",
        "highTechField": "新一代信息技术",
        "enterpriseScale": "小型",
        "brief": "人工智能软件研发企业",
        "inventionPatentCount": 3,
        "ipCount": 5,
        "updateTime": "2026-07-24 17:07:58",
        "honorQualificationList": [
            {
                "isTechSme": "1",
                "isHighTechEnterprise": "1",
                "provTechAwardCount": 1,
                "standardFormulationCount": 2,
            }
        ],
        "mainTechnologyList": [
            {"patentType": "Ⅰ类", "patentName": "发明专利"},
            {"patentType": "II类", "patentName": "实用新型"},
        ],
        "mainProductList": [
            {"productName": "工业软件", "productDescription": "智能制造平台"}
        ],
        "operationInfoList": [
            {"year": "2024", "operatingIncome": 99000000, "rdExpense": 1000000},
            {
                "year": "2025",
                "operatingIncome": 40000000,
                "mainBusinessIncome": 32000000,
                "rdExpense": 3000000,
                "rdExpenseRatio": "0.075",
                "techContractAmount": 1000000,
                "netProfit": 5600000,
                "endOwnerEquity": 1200000,
                "incomeGrowthRate": "0.7",
                "roe": "0.79",
                "employeeCount": 54,
                "rdPersonnelCount": 8,
                "rdPersonnelRatio": "0.1481",
            },
        ],
    }


def test_load_enterprises_from_api_paginates_and_normalizes_units() -> None:
    payloads = [
        {
            "code": 0,
            "msg": None,
            "data": {
                "totalCount": 2,
                "pageSize": 1,
                "totalPage": 2,
                "currPage": 1,
                "list": [_enterprise_record("A公司", "123")],
            },
        },
        {
            "code": 0,
            "msg": None,
            "data": {
                "totalCount": 2,
                "pageSize": 1,
                "totalPage": 2,
                "currPage": 2,
                "list": [_enterprise_record("B公司", "456")],
            },
        },
    ]

    with patch(
        "service.enterprise_loader.urlopen",
        side_effect=[_Response(payload) for payload in payloads],
    ) as mocked_urlopen:
        enterprises = load_enterprises_from_api(
            base_url="http://enterprise.test",
            page_size=1,
        )

    assert [item["企业名称"] for item in enterprises] == ["A公司", "B公司"]
    first = enterprises[0]
    assert first["销售收入(万元)"] == 4000
    assert first["主营业务收入(万元)"] == 3200
    assert first["研发费用总额(万元)"] == 300
    assert first["技术合同成交额(万元)"] == 100
    assert first["职工总数"] == 54
    assert first["科技人员占企业职工总数比例"] == 14.81
    assert first["研发费用占销售收入总额比例"] == 7.5
    assert first["I类知识产权数量"] == 1
    assert first["II类知识产权数量"] == 1
    assert first["指标5_高新技术企业"] == "是"
    assert first["_enterprise_standard_index"]["企业规模"]["标准值"] == "小型"
    assert first["_enterprise_standard_index"]["销售收入(万元)"]["单位"] == "万元"
    assert "pageNum=1" in mocked_urlopen.call_args_list[0].args[0].full_url
    assert "pageNum=2" in mocked_urlopen.call_args_list[1].args[0].full_url


def test_load_enterprises_from_api_rejects_business_error() -> None:
    with patch(
        "service.enterprise_loader.urlopen",
        return_value=_Response({"code": 1001, "msg": "参数错误", "data": None}),
    ):
        with pytest.raises(RuntimeError, match="code=1001"):
            load_enterprises_from_api(base_url="http://enterprise.test")


def test_load_enterprises_from_api_accepts_null_child_lists() -> None:
    record = _enterprise_record("空关联企业", "789")
    record.update(
        {
            "operationInfoList": None,
            "mainTechnologyList": None,
            "mainProductList": None,
            "honorQualificationList": None,
        }
    )
    payload = {
        "code": 0,
        "data": {"totalCount": 1, "totalPage": 1, "currPage": 1, "list": [record]},
    }

    with patch("service.enterprise_loader.urlopen", return_value=_Response(payload)):
        enterprises = load_enterprises_from_api(base_url="http://enterprise.test")

    assert len(enterprises) == 1
    assert enterprises[0]["企业名称"] == "空关联企业"
    assert enterprises[0]["销售收入(万元)"] is None
    assert enterprises[0]["资产总额(万元)"] is None
    assert enterprises[0]["研发费用占成本费用支出总额比例"] is None
    assert enterprises[0]["_enterprise_standard_index"]["销售收入(万元)"]["数据状态"] == "缺失"


def test_api_explicit_zero_is_not_replaced_by_feature_default() -> None:
    record = _enterprise_record("零值企业", "790")
    record["mainTechnologyList"] = [
        {"patentType": "Ⅰ类", "patentName": "发明专利"}
    ]
    payload = {
        "code": 0,
        "data": {"totalCount": 1, "totalPage": 1, "currPage": 1, "list": [record]},
    }

    with patch("service.enterprise_loader.urlopen", return_value=_Response(payload)):
        enterprises = load_enterprises_from_api(base_url="http://enterprise.test")

    assert enterprises[0]["I类知识产权数量"] == 1
    assert enterprises[0]["II类知识产权数量"] == 0
    assert enterprises[0]["_enterprise_standard_index"]["II类知识产权数量"]["数据状态"] == "原始"


def test_load_enterprises_from_api_falls_back_when_page_does_not_advance() -> None:
    first = _enterprise_record("A公司", "123")
    second = _enterprise_record("B公司", "456")
    responses = [
        _Response(
            {
                "code": 0,
                "data": {"totalCount": 2, "totalPage": 2, "currPage": 1, "list": [first]},
            }
        ),
        _Response(
            {
                "code": 0,
                "data": {"totalCount": 2, "totalPage": 2, "currPage": 1, "list": [first]},
            }
        ),
        _Response(
            {
                "code": 0,
                "data": {"totalCount": 2, "totalPage": 1, "currPage": 1, "list": [first, second]},
            }
        ),
    ]

    with patch("service.enterprise_loader.urlopen", side_effect=responses) as mocked_urlopen:
        enterprises = load_enterprises_from_api(base_url="http://enterprise.test", page_size=1)

    assert [item["企业名称"] for item in enterprises] == ["A公司", "B公司"]
    assert "pageSize=2" in mocked_urlopen.call_args_list[2].args[0].full_url


def test_load_enterprises_from_api_rejects_oversized_page() -> None:
    with pytest.raises(ValueError, match="1 到 100"):
        load_enterprises_from_api(base_url="http://enterprise.test", page_size=101)


def test_load_enterprise_from_record_normalizes_one_raw_api_enterprise() -> None:
    enterprise = load_enterprise_from_record(_enterprise_record("单企业", "900"))

    assert enterprise["企业名称"] == "单企业"
    assert enterprise["统一社会信用代码"].endswith("0000000900")
    assert enterprise["销售收入(万元)"] == 4000
    assert enterprise["研发费用总额(万元)"] == 300
    assert enterprise["科技人员占企业职工总数比例"] == 14.81
    assert enterprise["原始数据"]["enterpriseName"] == "单企业"
    assert enterprise["_source_type"] == "policy_recommendation_api_record"


def test_load_enterprise_from_record_rejects_non_api_shape() -> None:
    with pytest.raises(ValueError, match="enterpriseName"):
        load_enterprise_from_record({"企业名称": "不是接口原始结构"})


def test_fixed_enterprise_2252_preserves_assets_ip_counts_and_percentage_units() -> None:
    record_path = PROJECT_ROOT / "企业测试目录" / "2252_测试企业名称1.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))

    enterprise = load_enterprise_from_record(record)

    assert enterprise["资产总额(万元)"] == 976.51
    assert enterprise["I类知识产权数量"] == 0
    assert enterprise["II类知识产权数量"] == 11
    assert enterprise["科技人员占企业职工总数比例"] == 50.0
    assert enterprise["研发费用占销售收入总额比例"] == 30.65
    assert enterprise["_enterprise_standard_index"]["I类知识产权数量"]["数据状态"] == "原始"
    assert enterprise["_enterprise_standard_index"]["II类知识产权数量"]["标准值"] == 11

    evaluation = _calculate_tech_sme_evaluation(None, enterprise)
    assert evaluation["科技人员指标得分"] == 20
    assert evaluation["研发投入指标得分"] == 50
    assert evaluation["科技成果指标得分"] == 24
    assert evaluation["评价总分"] == 94


def test_patent_type_name_is_classified_into_class_one_and_two() -> None:
    """真实接口的 patentType 是"中国发明专利"这类种类名称，不是"I类/II类"。

    旧实现按首字母前缀判断，导致全部专利归类失败、科技成果指标被误计为 0 分。
    """
    record = _enterprise_record("专利种类企业", "901")
    record["mainTechnologyList"] = [
        {"patentType": "中国发明专利", "patentName": "发明一"},
        {"patentType": "中国发明专利", "patentName": "发明二"},
        {"patentType": "中国实用新型专利", "patentName": "实用新型一"},
        {"patentType": "中国外观设计专利", "patentName": "外观一"},
        {"patentType": "计算机软件著作权", "patentName": "软著一"},
    ]

    enterprise = load_enterprise_from_record(record)

    assert enterprise["I类知识产权数量"] == 2
    assert enterprise["II类知识产权数量"] == 3


def test_explicit_class_prefix_patent_type_still_supported() -> None:
    """兼容历史数据里直接写"Ⅰ类/II类"的写法。"""
    record = _enterprise_record("显式类别企业", "902")
    record["mainTechnologyList"] = [
        {"patentType": "Ⅰ类", "patentName": "发明专利"},
        {"patentType": "II类", "patentName": "实用新型"},
        {"patentType": "II类", "patentName": "外观设计"},
    ]

    enterprise = load_enterprise_from_record(record)

    assert enterprise["I类知识产权数量"] == 1
    assert enterprise["II类知识产权数量"] == 2


def test_unclassifiable_patent_types_are_missing_not_verified_zero() -> None:
    """全部 patentType 都无法归类时应按缺失处理。

    报 0 件会被当作"已核验的真实 0"参与计分，比缺失更有害 —— 缺失会走召回优先
    默认放行，而假的 0 会直接把科技成果指标锁死在 0 分。
    """
    record = _enterprise_record("未知类型企业", "903")
    record["mainTechnologyList"] = [
        {"patentType": "PCT国际专利", "patentName": "未知一"},
        {"patentType": "未知类型", "patentName": "未知二"},
    ]

    enterprise = load_enterprise_from_record(record)

    assert enterprise["I类知识产权数量"] is None
    assert enterprise["II类知识产权数量"] is None


def test_tech_sme_achievement_score_uses_class_one_patents() -> None:
    """科技成果指标必须按修复后的 I 类数量分档，而非固定 0 分。"""
    from service.rule_engine_service import _calculate_tech_sme_evaluation

    record = _enterprise_record("成果计分企业", "904")
    record["mainTechnologyList"] = [
        {"patentType": "中国发明专利", "patentName": "发明一"},
    ]
    enterprise = load_enterprise_from_record(record)

    evaluation = _calculate_tech_sme_evaluation(None, enterprise)

    assert evaluation["I类知识产权数量"] == 1
    assert evaluation["科技成果指标得分"] == 30
