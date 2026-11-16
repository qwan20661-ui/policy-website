"""企业数据加载、聚合与标准 JSON 构建。"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from html import unescape
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_SOURCE = "backend/PEMatching/Enterprisedata/企业数据(1).json"
DEFAULT_ENTERPRISE_API_BASE_URL = "http://112.48.19.225:8500"
DEFAULT_ENTERPRISE_API_PATH = "/cms/tb-base-business-info/web/page"
DEFAULTS_PATH = PROJECT_ROOT / "config" / "enterprise_feature_defaults.json"


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_float(value: Any) -> float | None:
    text = _safe_text(value)
    if not text:
        return None
    text = text.replace(",", "").replace("，", "")
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    number = _safe_float(value)
    if number is None:
        return None
    return int(round(number))


def _split_values(value: Any) -> List[str]:
    text = _safe_text(value)
    if not text:
        return []
    parts = re.split(r"[;,；，、]\s*", text)
    return [part.strip() for part in parts if part and part.strip()]


def _strip_html(value: Any) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_datetime_text(value: Any) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:19]

    for pattern in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            from datetime import datetime

            parsed = datetime.strptime(text, pattern)
            if "H" in pattern:
                return parsed.strftime("%Y-%m-%d %H:%M:%S")
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text


def _parse_territories(value: Any) -> List[Dict[str, str]]:
    territories: List[Dict[str, str]] = []
    text = _safe_text(value)
    if not text:
        return territories

    for raw_item in text.split(";"):
        item = raw_item.strip()
        if not item:
            continue
        parts = [part.strip() for part in item.split("->")]
        while len(parts) < 3:
            parts.append("")
        territories.append(
            {
                "一级": parts[0],
                "二级": parts[1],
                "三级": parts[2],
                "完整路径": "->".join([part for part in parts if part]),
            }
        )
    return territories


def _unique_join(values: List[str], sep: str = "；") -> str:
    seen = set()
    ordered: List[str] = []
    for value in values:
        text = _safe_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return sep.join(ordered)


def _dedupe_list(values: List[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for value in values:
        text = _safe_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _load_feature_defaults() -> Dict[str, Dict[str, Any]]:
    if not DEFAULTS_PATH.exists():
        return {}
    with DEFAULTS_PATH.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    return data if isinstance(data, dict) else {}


FEATURE_DEFAULTS = _load_feature_defaults()


def _contains_any(text: str, keywords: List[str]) -> bool:
    haystack = _safe_text(text)
    return any(keyword in haystack for keyword in keywords)


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _safe_text(value)
        if text:
            return text
    return ""


def _to_optional_float(*values: Any) -> float | None:
    for value in values:
        number = _safe_float(value)
        if number is not None:
            return number
    return None


def _to_optional_int(*values: Any) -> int | None:
    for value in values:
        number = _safe_int(value)
        if number is not None:
            return number
    return None


def _build_direct_industry_paths(record: Dict[str, Any]) -> List[str]:
    values = [
        _safe_text(record.get("所属行业领域(22+N)")),
        _safe_text(record.get("战略性新兴产业分类")),
        _safe_text(record.get("高新技术领域")),
    ]
    return [value for value in values if value]


def _is_policy_recommendation_api_record(record: Dict[str, Any]) -> bool:
    """单企业推荐接口仅以企业名称作为最低识别条件。"""
    return bool(_safe_text(record.get("enterpriseName")))


def _latest_operation_info(record: Dict[str, Any]) -> Dict[str, Any]:
    operations = [item for item in (record.get("operationInfoList") or []) if isinstance(item, dict)]
    if not operations:
        return {}
    return max(
        operations,
        key=lambda item: (
            _safe_int(item.get("year")) or 0,
            _normalize_datetime_text(item.get("updateTime")),
        ),
    )


def _amount_yuan_to_wanyuan(value: Any) -> float | None:
    amount = _safe_float(value)
    if amount is None:
        return None
    return amount / 10000


def _ratio_to_percentage(value: Any) -> float | None:
    """推荐接口的比例字段使用0-1小数，内部标准字段统一保存为百分数。"""
    ratio = _safe_float(value)
    if ratio is None:
        return None
    return ratio * 100 if abs(ratio) <= 1 else ratio


def _enabled_qualification_names(record: Dict[str, Any]) -> List[str]:
    field_names = {
        "isTechSme": "科技型中小企业",
        "isInnovativeSme": "创新型中小企业",
        "isHighTechEnterprise": "高新技术企业",
        "isSpecializedLittleGiant": "专精特新小巨人企业",
        "isSpecializedSme": "专精特新中小企业",
        "isTechLeaderEnterprise": "科技领军企业",
    }
    names: List[str] = []
    for qualification in record.get("honorQualificationList") or []:
        if not isinstance(qualification, dict):
            continue
        for field_name, display_name in field_names.items():
            if _safe_text(qualification.get(field_name)).lower() in {"1", "true", "是", "yes"}:
                names.append(display_name)
    return _dedupe_list(names)


# 科技型中小企业评价口径：I 类为发明专利、植物新品种、国家级农作物品种、
# 国家新药、国家一级中药保护品种、集成电路布图设计专有权；
# II 类为实用新型、外观设计、软件著作权等其余知识产权。
_CLASS_ONE_PATENT_KEYWORDS = (
    "发明专利",
    "植物新品种",
    "农作物品种",
    "国家新药",
    "中药保护品种",
    "集成电路布图设计",
)
_CLASS_TWO_PATENT_KEYWORDS = (
    "实用新型",
    "外观设计",
    "软件著作权",
    "著作权",
    "商标",
)


def _patent_type_category(value: Any) -> int | None:
    """判定知识产权类别。

    接口 patentType 既可能直接写"I类/II类"，也可能是"中国发明专利"这类专利种类名称，
    因此先按显式类别前缀判断，再按专利种类关键词归类。识别不出时返回 None，
    交由上层按缺失处理，避免把未知类型误计为"已核验的 0 件"。
    """
    text = _safe_text(value)
    if not text:
        return None
    normalized = text.upper().replace("Ⅱ", "II").replace("Ⅰ", "I")
    # 显式类别写法必须整体成立（如"II类""I 类"），不能让"IC 设计"这类文本误命中。
    if re.match(r"^II\s*类", normalized):
        return 2
    if re.match(r"^I\s*类", normalized):
        return 1
    if any(keyword in text for keyword in _CLASS_ONE_PATENT_KEYWORDS):
        return 1
    if any(keyword in text for keyword in _CLASS_TWO_PATENT_KEYWORDS):
        return 2
    return None


def _normalize_policy_recommendation_api_record(record: Dict[str, Any], data_source: str) -> Dict[str, Any]:
    latest_operation = _latest_operation_info(record)
    technology_list_present = isinstance(record.get("mainTechnologyList"), list)
    technologies = [item for item in (record.get("mainTechnologyList") or []) if isinstance(item, dict)]
    products = [item for item in (record.get("mainProductList") or []) if isinstance(item, dict)]
    qualifications = [item for item in (record.get("honorQualificationList") or []) if isinstance(item, dict)]

    patent_categories = [_patent_type_category(item.get("patentType")) for item in technologies]
    patent_type_one_count = sum(1 for category in patent_categories if category == 1)
    patent_type_two_count = sum(1 for category in patent_categories if category == 2)
    # 列表里有记录但没有一条能归类时，说明 patentType 取值超出已知口径，
    # 此时报 0 件会被当成"已核验的真实 0"参与计分（比缺失更有害），应按缺失处理。
    patent_classification_available = not technologies or any(
        category is not None for category in patent_categories
    )
    product_text = _unique_join(
        [
            _unique_join(
                [_safe_text(item.get("productName")), _safe_text(item.get("productDescription"))],
                sep="：",
            )
            for item in products
        ]
    )
    technology_text = _unique_join(
        [
            _unique_join(
                [_safe_text(item.get("patentName")), _safe_text(item.get("brief"))],
                sep="：",
            )
            for item in technologies
        ]
    )
    qualification_names = _enabled_qualification_names(record)
    update_time = _normalize_datetime_text(record.get("updateTime"))
    register_address = re.sub(r"\s+", " ", _safe_text(record.get("registerAddress"))).strip()
    industry_field = _safe_text(record.get("industryField"))
    high_tech_field = _safe_text(record.get("highTechField"))
    employee_count = _to_optional_int(latest_operation.get("employeeCount"))
    rd_personnel_count = _to_optional_int(latest_operation.get("rdPersonnelCount"))

    return {
        "_source_type": "policy_recommendation_api_record",
        "_data_source": data_source,
        "_record_updated_at": update_time,
        "_year": _safe_text(latest_operation.get("year")) or update_time[:4],
        "_source_record_count": 1,
        "_source_record_years": [
            _safe_text(latest_operation.get("year"))
        ] if _safe_text(latest_operation.get("year")) else [],
        "企业ID": _first_non_empty(record.get("id"), record.get("enterpriseId"), record.get("creditCode")),
        "企业名称": _safe_text(record.get("enterpriseName")),
        "统一社会信用代码": _safe_text(record.get("creditCode")),
        "注册日期": _normalize_datetime_text(record.get("registerDate")),
        "注册地": register_address,
        "注册地详细地址": register_address,
        "注册资金": _safe_float(record.get("registerCapital")),
        "注册资金单位": "万元",
        "所在省": "",
        "所在市": "厦门市" if "厦门" in register_address else "",
        "所在区": "",
        "详细地址": register_address,
        "办公地址": "",
        "一级行业": industry_field,
        "二级行业": "",
        "三级行业": "",
        "单位类型": ["企业"],
        "荣誉资质": qualification_names,
        "业务标签": product_text,
        "科技服务领域": [high_tech_field] if high_tech_field else [],
        "行业领域层级": [value for value in [industry_field, high_tech_field] if value],
        "企业简介": _unique_join([_strip_html(record.get("brief")), product_text, technology_text], sep="\n\n"),
        "企业规模": _safe_text(record.get("enterpriseScale")),
        "联系人": _safe_text(record.get("legalPerson")),
        "联系人职务": "法定代表人" if _safe_text(record.get("legalPerson")) else "",
        "法人电话": _safe_text(record.get("legalPersonPhone")),
        "联系电话": _safe_text(record.get("telephone")),
        "邮箱": _safe_text(record.get("email")),
        "官网": _safe_text(record.get("website")),
        "预留链接": "",
        "审核记录": "",
        "职工总数": employee_count,
        "在职职工人数": employee_count,
        "兼职职工人数": None,
        "临时职工人数": None,
        "科技人员总数": rd_personnel_count,
        "科技人员在职人数": rd_personnel_count,
        "科技人员兼职人数": None,
        "科技人员临时人数": None,
        "本年新增人数": None,
        "吸纳高校毕业生人数": None,
        "博士人数": None,
        "硕士人数": None,
        "本科人数": None,
        "大专及以下人数": None,
        "高级职称人数": None,
        "中级职称人数": None,
        "初级职称人数": None,
        "高级技工人数": None,
        "指标1_注册地": "符合" if "厦门" in register_address else "",
        "指标2_企业规模": _safe_text(record.get("enterpriseScale")),
        "指标3_产品及服务范围": product_text,
        "指标4_企业信用": "",
        "指标5_高新技术企业": "是" if "高新技术企业" in qualification_names else "否",
        "指标6_研发机构": "",
        "指标7_科技奖励": "是" if any(
            (_safe_int(qualification.get(field_name)) or 0) > 0
            for qualification in qualifications
            for field_name in ("cityTechAwardCount", "provTechAwardCount", "provAboveTechAwardCount")
        ) else "否",
        "指标8_制定标准": "是" if any(
            (_safe_int(qualification.get("standardFormulationCount")) or 0) > 0
            for qualification in qualifications
        ) else "否",
        "是否享受加计扣除": "",
        "享受加计扣除金额(元)": None,
        "总得分": None,
        "科技人员占企业职工总数比例": _ratio_to_percentage(latest_operation.get("rdPersonnelRatio")),
        "研发费用占销售收入总额比例": _ratio_to_percentage(latest_operation.get("rdExpenseRatio")),
        "研发费用占成本费用支出总额比例": _ratio_to_percentage(latest_operation.get("rdCostExpenseRatio")),
        "I类知识产权数量": _to_optional_int(
            record.get("ipClass1Count"),
            patent_type_one_count if technology_list_present and patent_classification_available else None,
        ),
        "II类知识产权数量": _to_optional_int(
            record.get("ipClass2Count"),
            patent_type_two_count if technology_list_present and patent_classification_available else None,
        ),
        "知识产权总数": _to_optional_int(record.get("ipCount"), len(technologies) or None),
        "发明专利授权总量": _to_optional_int(record.get("inventionPatentCount")),
        "资产总额(万元)": _amount_yuan_to_wanyuan(latest_operation.get("totalAssets")),
        "净资产(万元)": _amount_yuan_to_wanyuan(latest_operation.get("netAssets")),
        "成本费用总额(万元)": _amount_yuan_to_wanyuan(latest_operation.get("costExpense")),
        "销售收入(万元)": _amount_yuan_to_wanyuan(latest_operation.get("operatingIncome")),
        "主营业务收入(万元)": _amount_yuan_to_wanyuan(latest_operation.get("mainBusinessIncome")),
        "研发费用总额(万元)": _amount_yuan_to_wanyuan(latest_operation.get("rdExpense")),
        "利润总额(万元)": _amount_yuan_to_wanyuan(latest_operation.get("totalProfit")),
        "净利润(万元)": _amount_yuan_to_wanyuan(latest_operation.get("netProfit")),
        "技术合同成交额(万元)": _amount_yuan_to_wanyuan(latest_operation.get("techContractAmount")),
        "营业收入增长率": _to_optional_float(latest_operation.get("incomeGrowthRate")),
        "净资产收益率": _to_optional_float(latest_operation.get("roe")),
        "纳税总额(万元)": _amount_yuan_to_wanyuan(latest_operation.get("totalTax")),
        "原始数据": deepcopy(record),
    }


def _is_direct_enterprise_profile_record(record: Dict[str, Any]) -> bool:
    return bool(_safe_text(record.get("企业名称")) and _safe_text(record.get("统一社会信用代码")) and not _safe_text(record.get("name")))


def _normalize_direct_enterprise_profile_record(record: Dict[str, Any], data_source: str) -> Dict[str, Any]:
    year_text = _safe_text(record.get("填报年份"))
    normalized = deepcopy(record)
    normalized.update(
        {
            "_source_type": "enterprise_profile_record",
            "_data_source": data_source,
            "_record_updated_at": year_text,
            "_year": year_text,
            "_source_record_count": 1,
            "_source_record_years": [year_text] if year_text else [],
            "企业ID": _first_non_empty(record.get("企业ID"), record.get("序号"), record.get("统一社会信用代码")),
            "原始数据": deepcopy(record),
        }
    )
    return normalized


def normalize_enterprise_record(record: Dict[str, Any], data_source: str = DEFAULT_DATA_SOURCE) -> Dict[str, Any]:
    """将企业平台联合查询结果归一化为内部统一结构。"""

    if _is_policy_recommendation_api_record(record):
        return _normalize_policy_recommendation_api_record(record, data_source)

    if _is_direct_enterprise_profile_record(record):
        return _normalize_direct_enterprise_profile_record(record, data_source)

    territories = _parse_territories(record.get("territory_names"))
    level_one = [item["一级"] for item in territories if item.get("一级")]
    level_two = [item["二级"] for item in territories if item.get("二级")]
    level_three = [item["三级"] for item in territories if item.get("三级")]
    service_fields = _split_values(record.get("technology_server_wide_field_name")) + _split_values(
        record.get("technology_server_detail_field_name")
    )
    honours = _split_values(record.get("honour_type_names"))
    unit_types = _split_values(record.get("unit_type_names"))

    register_place = _unique_join(
        [
            record.get("contacts_province_address"),
            record.get("contacts_city_address"),
            record.get("contacts_district_address"),
        ],
        sep=" ",
    )
    if not register_place:
        register_place = _unique_join(
            [
                record.get("province_address"),
                record.get("city_address"),
                record.get("district_address"),
            ],
            sep=" ",
        )
    register_detailed_address = _safe_text(record.get("contacts_detailed_address"))

    current_province = _safe_text(record.get("province_address"))
    current_city = _safe_text(record.get("city_address"))
    current_district = _safe_text(record.get("district_address"))
    current_address = _safe_text(record.get("detail_address"))

    register_date = _normalize_datetime_text(record.get("register_time") or record.get("create_time"))
    update_time = _normalize_datetime_text(record.get("update_time"))
    description = _strip_html(record.get("content"))
    site_intro = _strip_html(record.get("site_introduction"))
    summary = _strip_html(record.get("summarize"))
    merged_description = "\n\n".join([item for item in [description, site_intro, summary] if item])

    capital = _safe_float(record.get("capital_num"))
    staff_total = _safe_int(record.get("emp_num"))

    normalized = {
        "_source_type": "xm_enterprise_record",
        "_data_source": data_source,
        "_record_updated_at": update_time,
        "_year": update_time[:4] if update_time else "",
        "_source_record_count": 1,
        "_source_record_years": [update_time[:4]] if update_time[:4] else [],
        "企业ID": _safe_text(record.get("user_id") or record.get("company_id")),
        "企业名称": _safe_text(record.get("name")),
        "统一社会信用代码": _safe_text(record.get("institution_code") or record.get("username")),
        "注册日期": register_date,
        "注册地": register_place,
        "注册地详细地址": register_detailed_address,
        "注册资金": capital,
        "注册资金单位": _safe_text(record.get("funding_unit")) or "万元",
        "所在省": current_province,
        "所在市": current_city,
        "所在区": current_district,
        "详细地址": current_address,
        "办公地址": _safe_text(record.get("office_address")),
        "一级行业": _unique_join(level_one, sep=" / "),
        "二级行业": _unique_join(level_two, sep=" / "),
        "三级行业": _unique_join(level_three, sep=" / "),
        "单位类型": unit_types,
        "荣誉资质": honours,
        "业务标签": _safe_text(record.get("business_name")),
        "科技服务领域": _dedupe_list(service_fields),
        "行业领域层级": [item["完整路径"] for item in territories],
        "企业简介": merged_description,
        "联系人": _safe_text(record.get("contacts")),
        "联系人职务": _safe_text(record.get("contacts_position")),
        "法人电话": _safe_text(record.get("legal_phone")),
        "联系电话": _safe_text(record.get("user_phone") or record.get("phone")),
        "邮箱": _safe_text(record.get("mail")),
        "官网": _safe_text(record.get("website_address")),
        "预留链接": _safe_text(record.get("reserve_url")),
        "审核记录": _safe_text(record.get("audit_records_json")),
        "职工总数": staff_total,
        "在职职工人数": staff_total,
        "兼职职工人数": None,
        "临时职工人数": None,
        "科技人员总数": None,
        "科技人员在职人数": None,
        "科技人员兼职人数": None,
        "科技人员临时人数": None,
        "本年新增人数": None,
        "吸纳高校毕业生人数": None,
        "博士人数": None,
        "硕士人数": None,
        "本科人数": None,
        "大专及以下人数": None,
        "高级职称人数": None,
        "中级职称人数": None,
        "初级职称人数": None,
        "高级技工人数": None,
        "指标1_注册地": "符合" if "厦门" in register_place or current_city == "厦门市" else "",
        "指标2_企业规模": "",
        "指标3_产品及服务范围": _safe_text(record.get("business_name")),
        "指标4_企业信用": "",
        "指标5_高新技术企业": "",
        "指标6_研发机构": "",
        "指标7_科技奖励": "",
        "指标8_制定标准": "",
        "是否享受加计扣除": "",
        "享受加计扣除金额(元)": None,
        "总得分": None,
        "科技人员占企业职工总数比例": None,
        "研发费用占销售收入总额比例": None,
        "研发费用占成本费用支出总额比例": None,
        "I类知识产权数量": None,
        "II类知识产权数量": None,
        "知识产权总数": None,
        "资产总额(万元)": None,
        "净资产(万元)": None,
        "成本费用总额(万元)": None,
        "销售收入(万元)": None,
        "主营业务收入(万元)": None,
        "研发费用总额(万元)": None,
        "利润总额(万元)": None,
        "纳税总额(万元)": None,
        "原始数据": record,
    }
    return normalized


def _sort_key_for_record(record: Dict[str, Any]) -> str:
    return _first_non_empty(record.get("_record_updated_at"), record.get("注册日期"), record.get("_year"))


def _aggregate_enterprise_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(records) == 1:
        aggregated = deepcopy(records[0])
        aggregated["_source_records"] = [records[0].get("原始数据", {})]
        return aggregated

    ordered = sorted(records, key=_sort_key_for_record)
    latest = deepcopy(ordered[-1])
    aggregated = latest
    aggregated["_source_records"] = [item.get("原始数据", {}) for item in ordered]
    aggregated["_source_record_count"] = len(ordered)
    aggregated["_source_record_years"] = sorted({item.get("_year", "") for item in ordered if item.get("_year", "")})

    aggregated["单位类型"] = _dedupe_list([value for item in ordered for value in item.get("单位类型", []) or []])
    aggregated["荣誉资质"] = _dedupe_list([value for item in ordered for value in item.get("荣誉资质", []) or []])
    aggregated["科技服务领域"] = _dedupe_list([value for item in ordered for value in item.get("科技服务领域", []) or []])
    aggregated["行业领域层级"] = _dedupe_list([value for item in ordered for value in item.get("行业领域层级", []) or []])

    for field in ["一级行业", "二级行业", "三级行业"]:
        aggregated[field] = _unique_join([item.get(field, "") for item in ordered], sep=" / ")

    aggregated["业务标签"] = _first_non_empty(*[item.get("业务标签", "") for item in reversed(ordered)])
    aggregated["注册地"] = _first_non_empty(*[item.get("注册地", "") for item in reversed(ordered)])
    aggregated["注册地详细地址"] = _first_non_empty(*[item.get("注册地详细地址", "") for item in reversed(ordered)])
    aggregated["办公地址"] = _first_non_empty(*[item.get("办公地址", "") for item in reversed(ordered)])

    descriptions = [item.get("企业简介", "") for item in ordered if _safe_text(item.get("企业简介"))]
    aggregated["企业简介"] = max(descriptions, key=len) if descriptions else ""

    numeric_max_fields = [
        "注册资金",
        "职工总数",
        "在职职工人数",
        "享受加计扣除金额(元)",
        "总得分",
        "科技人员总数",
        "科技人员在职人数",
        "博士人数",
        "硕士人数",
        "本科人数",
        "高级职称人数",
        "中级职称人数",
        "I类知识产权数量",
        "II类知识产权数量",
        "知识产权总数",
        "资产总额(万元)",
        "净资产(万元)",
        "成本费用总额(万元)",
        "销售收入(万元)",
        "主营业务收入(万元)",
        "研发费用总额(万元)",
        "利润总额(万元)",
        "纳税总额(万元)",
        "科技人员占企业职工总数比例",
        "研发费用占销售收入总额比例",
        "研发费用占成本费用支出总额比例",
    ]
    for field in numeric_max_fields:
        values = [item.get(field) for item in ordered if item.get(field) is not None]
        aggregated[field] = max(values) if values else latest.get(field)

    return aggregated


def _infer_boolean_value(enterprise: Dict[str, Any], keywords: List[str]) -> Tuple[str, str]:
    text_pool = "；".join(
        [
            enterprise.get("企业简介", ""),
            enterprise.get("业务标签", ""),
            "；".join(enterprise.get("荣誉资质", []) or []),
            "；".join(enterprise.get("科技服务领域", []) or []),
            "；".join(enterprise.get("行业领域层级", []) or []),
        ]
    )
    if _contains_any(text_pool, keywords):
        evidence = "；".join([keyword for keyword in keywords if keyword in text_pool])
        return "是", evidence
    return "否", ""


def _resolve_value(field_name: str, value: Any, allow_defaults: bool = True) -> Tuple[Any, str]:
    config = FEATURE_DEFAULTS.get(field_name, {})
    treat_zero_as_missing = bool(config.get("treat_zero_as_missing"))
    default_value = config.get("default_mean_value")

    if isinstance(value, (int, float)):
        if allow_defaults and treat_zero_as_missing and float(value) == 0 and default_value is not None:
            return default_value, "缺失补齐"
        return value, "原始"

    if value not in (None, ""):
        return value, "原始"

    if not allow_defaults:
        return value, "缺失"

    if default_value is not None:
        return default_value, "缺失补齐"
    return value, "缺失补齐"


def _field_entry(
    field_name: str,
    value: Any,
    unit: str,
    source_fields: List[str],
    evidence: str,
    status: str,
    data_type: str,
) -> Dict[str, Any]:
    return {
        "字段名": field_name,
        "标准值": value,
        "单位": unit,
        "原始来源字段": source_fields,
        "原始证据文本": evidence,
        "数据状态": status,
        "数据类型": data_type,
    }


def _build_standard_snapshot(enterprise: Dict[str, Any]) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Dict[str, Any]]]:
    snapshot: Dict[str, List[Dict[str, Any]]] = {
        "基础信息": [],
        "经营与行业": [],
        "研发与人员": [],
        "知识产权与成果": [],
        "经营规模与成长性": [],
        "合规与资质": [],
        "元数据": [],
    }
    index: Dict[str, Dict[str, Any]] = {}
    allow_feature_defaults = enterprise.get("_source_type") != "policy_recommendation_api_record"

    def add(group: str, field_name: str, value: Any, unit: str, source_fields: List[str], evidence: str, status: str, data_type: str) -> None:
        entry = _field_entry(field_name, value, unit, source_fields, evidence, status, data_type)
        snapshot[group].append(entry)
        index[field_name] = entry

    register_hit = "厦门" in "".join(
        [
            enterprise.get("注册地", ""),
            enterprise.get("注册地详细地址", ""),
            enterprise.get("办公地址", ""),
            enterprise.get("所在市", ""),
            enterprise.get("详细地址", ""),
        ]
    )
    subject_type = "企业"
    if enterprise.get("单位类型"):
        subject_type = enterprise["单位类型"][0]

    high_tech_value, high_tech_evidence = _infer_boolean_value(enterprise, ["高新技术企业"])
    tech_sme_value, tech_sme_evidence = _infer_boolean_value(enterprise, ["科技型中小企业"])
    specialized_value, specialized_evidence = _infer_boolean_value(enterprise, ["专精特新", "小巨人"])
    new_rd_value, new_rd_evidence = _infer_boolean_value(enterprise, ["新型研发机构", "研发机构", "研发中心"])
    rd_org_value = "是" if new_rd_value == "是" else "否"
    compliance_value = "未见重大违法异常记录"

    numeric_fields = [
        ("职工总数", "研发与人员", ["emp_num"], enterprise.get("职工总数"), "人"),
        ("在职职工人数", "研发与人员", ["emp_num"], enterprise.get("在职职工人数"), "人"),
        ("科技人员总数", "研发与人员", ["科技人员总数"], enterprise.get("科技人员总数"), "人"),
        ("科技人员在职人数", "研发与人员", ["科技人员在职人数"], enterprise.get("科技人员在职人数"), "人"),
        ("博士人数", "研发与人员", ["博士人数"], enterprise.get("博士人数"), "人"),
        ("硕士人数", "研发与人员", ["硕士人数"], enterprise.get("硕士人数"), "人"),
        ("本科人数", "研发与人员", ["本科人数"], enterprise.get("本科人数"), "人"),
        ("高级职称人数", "研发与人员", ["高级职称人数"], enterprise.get("高级职称人数"), "人"),
        ("中级职称人数", "研发与人员", ["中级职称人数"], enterprise.get("中级职称人数"), "人"),
        ("科技人员占企业职工总数比例", "研发与人员", ["科技人员占企业职工总数比例"], enterprise.get("科技人员占企业职工总数比例"), "%"),
        ("研发费用占销售收入总额比例", "研发与人员", ["研发费用占销售收入总额比例"], enterprise.get("研发费用占销售收入总额比例"), "%"),
        ("研发费用占成本费用支出总额比例", "研发与人员", ["研发费用占成本费用支出总额比例"], enterprise.get("研发费用占成本费用支出总额比例"), "%"),
        ("I类知识产权数量", "知识产权与成果", ["I类知识产权数量"], enterprise.get("I类知识产权数量"), "件"),
        ("II类知识产权数量", "知识产权与成果", ["II类知识产权数量"], enterprise.get("II类知识产权数量"), "件"),
        ("知识产权总数", "知识产权与成果", ["知识产权总数"], enterprise.get("知识产权总数"), "件"),
        ("注册资金", "经营规模与成长性", ["capital_num"], enterprise.get("注册资金"), enterprise.get("注册资金单位", "万元") or "万元"),
        ("资产总额(万元)", "经营规模与成长性", ["资产总额(万元)"], enterprise.get("资产总额(万元)"), "万元"),
        ("净资产(万元)", "经营规模与成长性", ["净资产(万元)"], enterprise.get("净资产(万元)"), "万元"),
        ("成本费用总额(万元)", "经营规模与成长性", ["成本费用总额(万元)"], enterprise.get("成本费用总额(万元)"), "万元"),
        ("销售收入(万元)", "经营规模与成长性", ["销售收入(万元)"], enterprise.get("销售收入(万元)"), "万元"),
        ("主营业务收入(万元)", "经营规模与成长性", ["主营业务收入(万元)"], enterprise.get("主营业务收入(万元)"), "万元"),
        ("研发费用总额(万元)", "研发与人员", ["研发费用总额(万元)"], enterprise.get("研发费用总额(万元)"), "万元"),
        ("利润总额(万元)", "经营规模与成长性", ["利润总额(万元)"], enterprise.get("利润总额(万元)"), "万元"),
        ("纳税总额(万元)", "经营规模与成长性", ["纳税总额(万元)"], enterprise.get("纳税总额(万元)"), "万元"),
        ("净利润(万元)", "经营规模与成长性", ["operationInfoList[].netProfit"], enterprise.get("净利润(万元)"), "万元"),
        ("技术合同成交额(万元)", "经营规模与成长性", ["operationInfoList[].techContractAmount"], enterprise.get("技术合同成交额(万元)"), "万元"),
        ("营业收入增长率", "经营规模与成长性", ["operationInfoList[].incomeGrowthRate"], enterprise.get("营业收入增长率"), "%"),
        ("净资产收益率", "经营规模与成长性", ["operationInfoList[].roe"], enterprise.get("净资产收益率"), "%"),
        ("发明专利授权总量", "知识产权与成果", ["inventionPatentCount"], enterprise.get("发明专利授权总量"), "件"),
    ]
    for field_name, group, source_fields, raw_value, unit in numeric_fields:
        resolved_value, status = _resolve_value(field_name, raw_value, allow_defaults=allow_feature_defaults)
        enterprise[field_name] = resolved_value
        add(group, field_name, resolved_value, unit, source_fields, "", status, "定量")

    if not enterprise.get("知识产权总数") and enterprise.get("I类知识产权数量") is not None and enterprise.get("II类知识产权数量") is not None:
        enterprise["知识产权总数"] = int((enterprise.get("I类知识产权数量") or 0) + (enterprise.get("II类知识产权数量") or 0))
        index_entry = index.get("知识产权总数")
        if index_entry:
            index_entry["标准值"] = enterprise["知识产权总数"]
            index_entry["数据状态"] = "推断"

    if enterprise.get("职工总数") and enterprise.get("科技人员总数"):
        if not enterprise.get("科技人员占企业职工总数比例"):
            ratio = round(float(enterprise["科技人员总数"]) / float(enterprise["职工总数"]), 4)
            enterprise["科技人员占企业职工总数比例"] = ratio
            if "科技人员占企业职工总数比例" in index:
                index["科技人员占企业职工总数比例"]["标准值"] = ratio
                index["科技人员占企业职工总数比例"]["数据状态"] = "推断"

    if enterprise.get("销售收入(万元)") and enterprise.get("研发费用总额(万元)"):
        if not enterprise.get("研发费用占销售收入总额比例"):
            ratio = round(float(enterprise["研发费用总额(万元)"]) / float(enterprise["销售收入(万元)"]), 4)
            enterprise["研发费用占销售收入总额比例"] = ratio
            if "研发费用占销售收入总额比例" in index:
                index["研发费用占销售收入总额比例"]["标准值"] = ratio
                index["研发费用占销售收入总额比例"]["数据状态"] = "推断"

    add("基础信息", "企业名称", enterprise.get("企业名称", ""), "", ["name"], enterprise.get("企业名称", ""), "原始", "定性")
    add(
        "基础信息",
        "统一社会信用代码",
        enterprise.get("统一社会信用代码", ""),
        "",
        ["institution_code", "username"],
        enterprise.get("统一社会信用代码", ""),
        "原始",
        "定性",
    )
    add("基础信息", "注册日期", enterprise.get("注册日期", ""), "", ["register_time", "create_time"], enterprise.get("注册日期", ""), "原始", "定性")
    add("基础信息", "注册地", enterprise.get("注册地", ""), "", ["contacts_*", "province_address", "city_address", "district_address"], enterprise.get("注册地", ""), "原始", "定性")
    add("基础信息", "注册地详细地址", enterprise.get("注册地详细地址", ""), "", ["contacts_detailed_address"], enterprise.get("注册地详细地址", ""), "原始", "定性")
    add("基础信息", "企业规模", enterprise.get("企业规模", ""), "", ["enterpriseScale"], enterprise.get("企业规模", ""), "原始", "定性")
    add("基础信息", "主体类型", subject_type, "", ["unit_type_names"], "；".join(enterprise.get("单位类型", []) or []), "推断", "布尔")
    add("基础信息", "是否在厦注册", "是" if register_hit else "否", "", ["contacts_*", "province_address", "city_address", "district_address"], enterprise.get("注册地", ""), "推断", "布尔")

    main_product_text = _first_non_empty(enterprise.get("业务标签", ""), "；".join(enterprise.get("科技服务领域", []) or []))
    core_field_text = _first_non_empty("；".join(enterprise.get("科技服务领域", []) or []), "；".join(enterprise.get("行业领域层级", []) or []))
    add("经营与行业", "一级行业", enterprise.get("一级行业", ""), "", ["territory_names"], enterprise.get("一级行业", ""), "原始", "定性")
    add("经营与行业", "二级行业", enterprise.get("二级行业", ""), "", ["territory_names"], enterprise.get("二级行业", ""), "原始", "定性")
    add("经营与行业", "三级行业", enterprise.get("三级行业", ""), "", ["territory_names"], enterprise.get("三级行业", ""), "原始", "定性")
    add("经营与行业", "科技服务领域", enterprise.get("科技服务领域", []), "", ["technology_server_wide_field_name", "technology_server_detail_field_name"], "；".join(enterprise.get("科技服务领域", []) or []), "原始", "定性")
    add("经营与行业", "行业领域层级", enterprise.get("行业领域层级", []), "", ["territory_names"], "；".join(enterprise.get("行业领域层级", []) or []), "原始", "定性")
    add("经营与行业", "业务标签", enterprise.get("业务标签", ""), "", ["business_name"], enterprise.get("业务标签", ""), "原始", "定性")
    add("经营与行业", "主要产品或服务", main_product_text, "", ["business_name", "technology_server_detail_field_name"], main_product_text, "推断", "定性")
    add("经营与行业", "核心技术领域", core_field_text, "", ["technology_server_wide_field_name", "technology_server_detail_field_name", "territory_names"], core_field_text, "推断", "定性")

    enterprise["指标5_高新技术企业"] = high_tech_value
    enterprise["指标6_研发机构"] = new_rd_value
    enterprise["指标7_科技奖励"] = "是" if enterprise.get("荣誉资质") else "否"
    add("知识产权与成果", "高新技术企业认定", high_tech_value, "", ["content", "honour_type_names"], high_tech_evidence, "推断", "布尔")
    add("知识产权与成果", "科技型中小企业认定", tech_sme_value, "", ["content", "honour_type_names"], tech_sme_evidence, "推断", "布尔")
    add("知识产权与成果", "专精特新认定", specialized_value, "", ["content", "honour_type_names"], specialized_evidence, "推断", "布尔")
    add("知识产权与成果", "新型研发机构认定", new_rd_value, "", ["content", "honour_type_names", "technology_server_detail_field_name"], new_rd_evidence, "推断", "布尔")
    add("研发与人员", "研发机构认定", rd_org_value, "", ["content", "honour_type_names", "technology_server_detail_field_name"], new_rd_evidence, "推断", "布尔")
    add("研发与人员", "是否享受加计扣除", enterprise.get("是否享受加计扣除", "") or "待确认", "", ["是否享受加计扣除"], "", "缺失补齐", "布尔")

    add("合规与资质", "合规经营状态", compliance_value, "", ["audit_records_json", "content"], compliance_value, "推断", "布尔")
    add("合规与资质", "企业信用状态", "未见异常", "", ["audit_records_json"], "未见异常", "推断", "布尔")
    add("合规与资质", "荣誉资质", enterprise.get("荣誉资质", []), "", ["honour_type_names"], "；".join(enterprise.get("荣誉资质", []) or []), "原始", "定性")
    add("合规与资质", "单位类型", enterprise.get("单位类型", []), "", ["unit_type_names"], "；".join(enterprise.get("单位类型", []) or []), "原始", "定性")

    add("元数据", "数据来源", enterprise.get("_data_source", DEFAULT_DATA_SOURCE), "", ["_data_source"], enterprise.get("_data_source", DEFAULT_DATA_SOURCE), "原始", "定性")
    add("元数据", "数据更新时间", enterprise.get("_record_updated_at", ""), "", ["update_time"], enterprise.get("_record_updated_at", ""), "原始", "定性")
    add("元数据", "源记录数量", enterprise.get("_source_record_count", 1), "条", ["RECORDS"], str(enterprise.get("_source_record_count", 1)), "推断", "定量")
    add("元数据", "源记录年份", enterprise.get("_source_record_years", []), "", ["update_time"], "；".join(enterprise.get("_source_record_years", []) or []), "推断", "定性")

    return snapshot, index


def _enrich_enterprise(enterprise: Dict[str, Any]) -> Dict[str, Any]:
    snapshot, index = _build_standard_snapshot(enterprise)
    enterprise["_enterprise_standard_snapshot"] = snapshot
    enterprise["_enterprise_standard_index"] = index
    enterprise["_source_records"] = enterprise.get("_source_records", [enterprise.get("原始数据", {})])
    return enterprise


def _normalize_enterprise_records(records: List[Any], data_source: str) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        normalized = normalize_enterprise_record(record, data_source=data_source)
        enterprise_name = _safe_text(normalized.get("企业名称"))
        if not enterprise_name:
            continue
        credit_code = _safe_text(normalized.get("统一社会信用代码"))
        group_key = (enterprise_name, credit_code)
        grouped.setdefault(group_key, []).append(normalized)

    enterprises: List[Dict[str, Any]] = []
    for _, items in grouped.items():
        aggregated = _aggregate_enterprise_records(items)
        if aggregated.get("_source_type") == "enterprise_profile_record":
            aggregated["_source_records"] = aggregated.get("_source_records", [aggregated.get("原始数据", {})])
            enterprises.append(aggregated)
        else:
            enterprises.append(_enrich_enterprise(aggregated))

    enterprises.sort(key=lambda item: item.get("企业名称", ""))
    return enterprises


def load_enterprise_from_record(
    record: Dict[str, Any],
    data_source: str = "policy_recommendation_api_request",
) -> Dict[str, Any]:
    """将政策推荐企业接口中的单条原始记录转换为可直接匹配的企业画像。"""

    if not isinstance(record, dict):
        raise ValueError("企业信息必须是 JSON 对象。")
    if not _is_policy_recommendation_api_record(record):
        raise ValueError("企业信息格式错误：必须提供 enterpriseName。")

    enterprises = _normalize_enterprise_records([record], data_source)
    if len(enterprises) != 1:
        raise ValueError("企业信息无法转换为有效的单企业画像。")
    return enterprises[0]


def load_enterprises(path: str | Path) -> List[Dict[str, Any]]:
    """从企业数据 JSON 加载、聚合并标准化企业列表。"""

    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as fp:
        raw = json.load(fp)

    if isinstance(raw, dict) and isinstance(raw.get("RECORDS"), list):
        records = raw["RECORDS"]
    elif isinstance(raw, list):
        records = raw
    elif isinstance(raw, dict):
        records = [raw]
    else:
        raise ValueError("企业数据格式不支持，期望为对象或数组。")
    return _normalize_enterprise_records(records, str(file_path))


def _request_enterprise_api_page(
    endpoint: str,
    page_number: int,
    page_size: int,
    timeout: float,
    request_headers: Mapping[str, str],
) -> Dict[str, Any]:
    query = urlencode({"pageNum": page_number, "pageSize": page_size})
    request = Request(f"{endpoint}?{query}", headers=dict(request_headers), method="GET")
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        payload = json.loads(response.read().decode(charset))

    if not isinstance(payload, dict):
        raise ValueError("企业接口响应格式错误：顶层必须是对象。")
    if _safe_int(payload.get("code")) != 0:
        raise RuntimeError(f"企业接口返回失败：code={payload.get('code')}，msg={payload.get('msg') or ''}")
    page_data = payload.get("data")
    if not isinstance(page_data, dict) or not isinstance(page_data.get("list"), list):
        raise ValueError("企业接口响应格式错误：缺少 data.list 数组。")
    return page_data


def _enterprise_page_signature(records: List[Any]) -> Tuple[str, ...]:
    return tuple(
        _first_non_empty(record.get("id"), record.get("enterpriseId"), record.get("creditCode"), record.get("enterpriseName"))
        for record in records
        if isinstance(record, dict)
    )


def load_enterprises_from_api(
    base_url: str = DEFAULT_ENTERPRISE_API_BASE_URL,
    api_path: str = DEFAULT_ENTERPRISE_API_PATH,
    page_size: int = 100,
    max_pages: int = 0,
    timeout: float = 15,
    pagination_fallback_limit: int = 10000,
    headers: Mapping[str, str] | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> List[Dict[str, Any]]:
    """从政策推荐企业分页接口加载全部企业，并转换为匹配侧统一企业画像。"""

    if page_size < 1 or page_size > 100:
        raise ValueError("企业接口 pageSize 必须在 1 到 100 之间。")
    if max_pages < 0:
        raise ValueError("企业接口 max_pages 不能小于 0。")
    if pagination_fallback_limit < 1:
        raise ValueError("企业接口 pagination_fallback_limit 必须大于 0。")

    endpoint = f"{base_url.rstrip('/')}/{api_path.lstrip('/')}"
    request_headers = {"Accept": "application/json", **dict(headers or {})}
    records: List[Any] = []
    page_number = 1
    total_pages: int | None = None
    seen_page_signatures: set[Tuple[str, ...]] = set()

    while total_pages is None or page_number <= total_pages:
        if max_pages and page_number > max_pages:
            break
        page_data = _request_enterprise_api_page(
            endpoint,
            page_number,
            page_size,
            timeout,
            request_headers,
        )
        page_records = page_data["list"]
        page_signature = _enterprise_page_signature(page_records)
        total_pages = _safe_int(page_data.get("totalPage"))
        total_count = _safe_int(page_data.get("totalCount")) or len(records) + len(page_records)
        if total_pages is None:
            total_pages = max(1, (total_count + page_size - 1) // page_size)

        reported_page = _safe_int(page_data.get("currPage"))
        repeated_page = bool(page_signature and page_signature in seen_page_signatures)
        page_not_advanced = page_number > 1 and reported_page is not None and reported_page != page_number
        if repeated_page or page_not_advanced:
            if total_count > pagination_fallback_limit:
                raise RuntimeError(
                    f"企业接口分页未推进，且总记录数 {total_count} 超过兼容回退上限 {pagination_fallback_limit}。"
                )
            fallback_data = _request_enterprise_api_page(
                endpoint,
                1,
                total_count,
                timeout,
                request_headers,
            )
            fallback_records = fallback_data["list"]
            if len(fallback_records) < total_count:
                raise RuntimeError(
                    f"企业接口分页未推进，单次回退仅返回 {len(fallback_records)}/{total_count} 条。"
                )
            records = fallback_records
            if progress_callback:
                progress_callback(
                    f"检测到企业接口分页未推进，已按 totalCount 单次回退: 返回={len(records)}"
                )
            break

        records.extend(page_records)
        if page_signature:
            seen_page_signatures.add(page_signature)
        if progress_callback:
            progress_callback(
                f"企业接口分页完成: page={page_number}/{total_pages}, 当前={len(page_records)}, 累计={len(records)}"
            )
        if not page_records:
            break
        page_number += 1

    return _normalize_enterprise_records(records, endpoint)
