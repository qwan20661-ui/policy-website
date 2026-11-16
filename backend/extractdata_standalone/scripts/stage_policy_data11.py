from __future__ import annotations

import argparse
import base64
import csv
import json
from pathlib import Path
from typing import Any


K = {
    "list": "\u653f\u7b56\u6570\u636e\u5217\u8868",
    "file_name": "\u6587\u4ef6\u540d",
    "metadata": "\u5143\u6570\u636e",
    "summary": "\u6458\u8981",
    "condition": "\u7533\u62a5\u6761\u4ef6",
    "cluster": "\u7533\u62a5\u6761\u4ef6\u805a\u7c7b",
    "support": "\u652f\u6301\u65b9\u5f0f",
    "material": "\u7533\u62a5\u6750\u6599\u53ca\u65b9\u5f0f",
    "contact": "\u8054\u7cfb\u65b9\u5f0f",
    "volume": "\u7533\u62a5\u91cf",
    "scene": "\u573a\u666f\u7528\u9014",
    "attribute": "\u653f\u7b56\u5c5e\u6027",
    "department": "\u53d1\u6587\u90e8\u95e8",
    "title": "\u653f\u7b56\u6807\u9898",
    "original_title": "\u653f\u7b56\u539f\u6807\u9898",
    "keywords": "\u653f\u7b56\u5173\u952e\u8bcd",
    "effective_date": "\u6709\u6548\u65e5\u671f",
    "scope": "\u9002\u7528\u8303\u56f4",
    "industry": "\u884c\u4e1a\u9886\u57df",
    "target": "\u9002\u7528\u5bf9\u8c61",
    "requirement": "\u7533\u62a5\u8981\u6c42",
    "application_time": "\u7533\u62a5\u65f6\u95f4",
    "announcement_date": "\u516c\u544a\u65e5\u671f",
    "support_desc": "\u6276\u6301\u5185\u5bb9\u63cf\u8ff0",
    "support_type": "\u6276\u6301\u7c7b\u578b",
    "support_amount": "\u6276\u6301\u91d1\u989d",
    "max_support_amount": "\u6700\u9ad8\u6276\u6301\u91d1\u989d",
    "materials": "\u7533\u62a5\u6750\u6599",
    "method": "\u7533\u62a5\u65b9\u5f0f",
    "url": "\u7533\u62a5\u94fe\u63a5",
    "attachments": "\u9644\u4ef6",
}


P = {
    "biz": "\u5de5\u5546\u57fa\u7840\u4fe1\u606f",
    "basic": "\u57fa\u7840\u4fe1\u606f",
    "reg": "\u6ce8\u518c\u4fe1\u606f",
    "bizinfo": "\u7ecf\u8425\u4fe1\u606f",
    "tech": "\u4e3b\u8981\u6280\u672f",
    "product": "\u4e3b\u8981\u4ea7\u54c1",
    "product_info": "\u4ea7\u54c1\u4fe1\u606f",
    "team": "\u6838\u5fc3\u56e2\u961f",
    "talent": "\u4eba\u624d\u6761\u4ef6",
    "edu": "\u5b66\u5386\u7ed3\u6784",
    "investment": "\u521b\u65b0\u6295\u5165",
    "infra": "\u7814\u53d1\u57fa\u7840\u8bbe\u65bd",
    "mgmt": "\u7814\u53d1\u7ba1\u7406",
    "equip": "\u7814\u53d1\u8bbe\u5907",
    "cost": "\u7814\u53d1\u8d39\u7528",
    "proj_cond": "\u9879\u76ee\u5b9e\u65bd\u6761\u4ef6",
    "output": "\u521b\u65b0\u4ea7\u51fa",
    "trade": "\u6280\u672f\u4ea4\u6613",
    "ip": "\u77e5\u8bc6\u4ea7\u6743",
    "development": "\u521b\u65b0\u53d1\u5c55",
    "scale": "\u4f01\u4e1a\u53d1\u5c55\u89c4\u6a21",
    "growth": "\u4f01\u4e1a\u6210\u957f\u6027",
    "impact": "\u521b\u65b0\u5f71\u54cd",
    "collab": "\u4ea7\u5b66\u7814\u5408\u4f5c",
    "credit": "\u4fe1\u7528\u5408\u89c4",
    "finance": "\u878d\u8d44\u60c5\u51b5",
    "qual": "\u8d44\u8d28\u8363\u8a89",
    "proj": "\u9879\u76ee\u5408\u4f5c",
}


TABLE_COLUMNS = {
    "POLICY": ["ROW_KEY", "FILE_NAME", "ABSTRACT", "CONTACT_INFO", "APPLICATION_VOLUME", "POLICY_TITLE"],
    "POLICY_METADATA": [
        "ROW_KEY",
        "SCENE_USAGE",
        "POLICY_ATTRIBUTE",
        "ISSUING_DEPARTMENT",
        "POLICY_TITLE_MAIN",
        "POLICY_ORIGINAL_TITLE",
        "EFFECTIVE_DATE",
        "APPLICABLE_SCOPE",
        "INDUSTRY_FIELD",
        "REMARK",
    ],
    "POLICY_KEYWORD": ["ROW_KEY", "KEYWORD"],
    "APPLICATION_CONDITION": [
        "ROW_KEY",
        "APPLICABLE_TARGET",
        "APPLICATION_REQUIREMENT",
        "APPLICATION_TIME",
        "ANNOUNCEMENT_DATE",
    ],
    "SUPPORT_METHOD": [
        "ROW_KEY",
        "SUPPORT_CONTENT_DESC",
        "SUPPORT_TYPE",
        "SUPPORT_AMOUNT",
        "MAX_SUPPORT_AMOUNT",
    ],
    "APPLICATION_MATERIAL": ["ROW_KEY", "APPLICATION_MATERIALS", "APPLICATION_METHOD", "APPLICATION_URL"],
    "APPLICATION_ATTACHMENT": ["ROW_KEY", "ATTACHMENT_NAME"],
    "CONDITION_CLUSTER": [
        "ROW_KEY",
        "BASIC_UNIT_NAME",
        "BASIC_CREDIT_CODE",
        "BASIC_REGISTER_DATE",
        "BASIC_ENTERPRISE_NATURE",
        "BASIC_ENTERPRISE_TYPE_VALUE",
        "BASIC_ENTERPRISE_TYPE_SOURCE",
        "BASIC_LEGAL_REPRESENTATIVE",
        "BASIC_INSTITUTION_NATURE",
        "REG_ADDRESS_VALUE",
        "REG_ADDRESS_SOURCE",
        "REG_REGISTER_DATE",
        "REG_ACTUAL_ADDRESS",
        "REG_TERRITORIAL_INFO",
        "REG_TAX_TERRITORY",
        "BIZ_SUBJECT_QUALIFICATION",
        "BIZ_UNIT_TYPE",
        "BIZ_OPERATION_STATUS",
        "BIZ_SCOPE_VALUE",
        "BIZ_SCOPE_SOURCE",
    ],
    "MAIN_TECHNOLOGY": [
        "ROW_KEY",
        "NATIONAL_STRATEGIC_INDUSTRY_CATEGORY",
        "INDUSTRY_FIELD",
        "CORE_TECHNOLOGY_FIELD",
    ],
    "MAIN_PRODUCT": ["ROW_KEY", "MAIN_PRODUCT_NAME", "MAIN_SERVICE", "CORE_PRODUCT", "PRODUCT_STAGE"],
    "CORE_TEAM": [
        "ROW_KEY",
        "TALENT_HEALTH_STATUS",
        "TALENT_NATIONALITY_REQ",
        "TALENT_AGE_REQ",
        "TALENT_LANGUAGE_ABILITY",
        "TEAM_EMPLOYEE_COUNT_VALUE",
        "TEAM_EMPLOYEE_COUNT_OPERATOR",
        "TEAM_EMPLOYEE_COUNT_UNIT",
        "TEAM_EMPLOYEE_COUNT_SOURCE",
        "TEAM_SIZE",
        "TEAM_RD_PERSONNEL_COUNT",
        "TEAM_TECH_PERSONNEL_RATIO",
        "TEAM_TECH_PERSONNEL_COUNT",
        "TEAM_PROJECT_LEADER_ROLE",
        "EDU_PHD_COUNT",
        "EDU_BACHELOR_COUNT",
        "EDU_MASTER_COUNT",
        "EDU_TECH_AWARD_VALUE",
        "EDU_TECH_AWARD_SOURCE",
        "EDU_TITLE_REQ",
        "EDU_PROJECT_EXPERIENCE",
    ],
    "INNOVATION_INVESTMENT": [
        "ROW_KEY",
        "INFRA_FACILITY_STATUS",
        "MGMT_RD_SYSTEM_VALUE",
        "MGMT_RD_SYSTEM_SOURCE",
        "EQUIP_RD_EQUIPMENT_ORIGINAL",
        "COST_ANNUAL_RD_INVESTMENT",
        "COST_RD_INVESTMENT_INTENSITY",
        "PROJ_IMPLEMENTATION_LOCATION",
        "PROJ_EXECUTION_PERIOD",
        "PROJ_PROJECT_FUNDING",
    ],
    "INNOVATION_OUTPUT": [
        "ROW_KEY",
        "TRADE_ACHIEVEMENT_TYPE",
        "TRADE_TECH_TRADE_AMOUNT",
        "TRADE_TECH_ACHIEVEMENT_TRANSFER",
        "IP_PATENT",
        "IP_ACHIEVEMENT",
        "IP_STANDARD_VALUE",
        "IP_STANDARD_SOURCE",
        "IP_PAPER_LITERATURE",
        "IP_SOFTCOPY_TRADEMARK",
        "IP_IP_AGREEMENT",
    ],
    "INNOVATION_DEVELOPMENT": [
        "ROW_KEY",
        "SCALE_PERSONNEL_VALUE",
        "SCALE_PERSONNEL_OPERATOR",
        "SCALE_PERSONNEL_UNIT",
        "SCALE_PERSONNEL_SOURCE",
        "SCALE_MARKET_VALUE",
        "SCALE_MARKET_OPERATOR",
        "SCALE_MARKET_UNIT",
        "SCALE_MARKET_SOURCE",
        "SCALE_TAX_SCALE",
        "SCALE_REVENUE_VALUE",
        "SCALE_REVENUE_OPERATOR",
        "SCALE_REVENUE_UNIT",
        "SCALE_REVENUE_SOURCE",
        "SCALE_ASSET_VALUE",
        "SCALE_ASSET_OPERATOR",
        "SCALE_ASSET_UNIT",
        "SCALE_ASSET_SOURCE",
        "GROWTH_NET_PROFIT_GROWTH_RATE",
        "GROWTH_NET_ASSET_GROWTH_RATE",
        "GROWTH_RD_GROWTH_RATE",
        "GROWTH_REVENUE_GROWTH_RATE",
    ],
    "INNOVATION_IMPACT": [
        "ROW_KEY",
        "COLLAB_AGREEMENT",
        "COLLAB_FOUNDATION",
        "COLLAB_INTERNATIONAL_FOUNDATION",
        "COLLAB_RESEARCH_INSTITUTE",
        "COLLAB_UNIVERSITY",
        "CREDIT_PRODUCT_COMPLIANCE",
        "CREDIT_ENTERPRISE_CREDIT",
        "CREDIT_ETHICS_COMPLIANCE",
        "CREDIT_SAFETY_MAJOR_ACCIDENT",
        "CREDIT_SAFETY_SOURCE",
        "CREDIT_BUSINESS_ABNORMAL_LIST",
        "CREDIT_BUSINESS_SERIOUS_DISHONESTY_LIST",
        "CREDIT_BUSINESS_SOURCE",
        "CREDIT_ANNUAL_REPORT_FORMULA",
        "CREDIT_GOVERNMENT_PERSONNEL_RESTRICTION",
        "CREDIT_LEGAL_COMPLIANCE_VALUE",
        "CREDIT_LEGAL_COMPLIANCE_SOURCE",
        "CREDIT_RESEARCH_CREDIT_VALUE",
        "CREDIT_RESEARCH_CREDIT_SOURCE",
        "CREDIT_TAX_CREDIT",
        "CREDIT_ADMIN_PENALTY_VALUE",
        "CREDIT_ADMIN_PENALTY_SOURCE",
        "CREDIT_QUALITY_MAJOR_ACCIDENT",
        "CREDIT_QUALITY_SOURCE",
        "CREDIT_REPEAT_APPLICATION_RESTRICTION",
        "FIN_INVESTOR",
        "FIN_FINANCING_AMOUNT",
        "FIN_FINANCING_STAGE",
        "QUAL_TALENT_QUALIFICATION",
        "QUAL_ENTERPRISE_QUALIFICATION_VALUE",
        "QUAL_ENTERPRISE_QUALIFICATION_SOURCE",
        "QUAL_PLATFORM_QUALIFICATION_VALUE",
        "QUAL_PLATFORM_QUALIFICATION_SOURCE",
        "PROJ_COOP_UNIT_COUNT",
        "PROJ_COOP_UNIT_TYPE",
        "PROJ_JOINT_APPLICATION",
    ],
}


def clean(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        value = "\uff1b".join(str(v) for v in value if v not in (None, ""))
    elif isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False)
    else:
        value = str(value)
    value = value.strip()
    return value or None


def at(obj: dict[str, Any], *path: str) -> Any:
    cur: Any = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def qval(obj: Any) -> str | None:
    if isinstance(obj, dict):
        value = obj.get("value")
        source = clean(obj.get("source_text"))
        operator = clean(obj.get("operator"))
        if value in (None, "", [], {}) or (value == 0 and not source and not operator):
            return None
        return clean(value)
    return clean(obj)


def qsrc(obj: Any) -> str | None:
    return clean(obj.get("source_text")) if isinstance(obj, dict) else None


def qop(obj: Any) -> str | None:
    return clean(obj.get("operator")) if isinstance(obj, dict) and qval(obj) is not None else None


def qunit(obj: Any) -> str | None:
    return clean(obj.get("unit")) if isinstance(obj, dict) and qval(obj) is not None else None


def qbool(obj: Any, key: str | None = None) -> str | None:
    if not isinstance(obj, dict):
        return None
    value = obj.get(key) if key else obj.get("value")
    source = clean(obj.get("source_text"))
    if value is None and not source:
        return None
    if value is True:
        return "1"
    if value is False:
        return "0" if source else None
    return clean(value)


def b64(value: Any) -> str:
    value = clean(value)
    if value is None:
        return r"\N"
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def write_table(out_dir: Path, table: str, rows: list[dict[str, Any]]) -> None:
    path = out_dir / f"{table}.tsv"
    columns = TABLE_COLUMNS[table]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([b64(row.get(column)) for column in columns])


def build_rows(item: dict[str, Any], row_key: str) -> dict[str, list[dict[str, Any]]]:
    md = item.get(K["metadata"], {}) or {}
    cond = item.get(K["condition"], {}) or {}
    support = item.get(K["support"], {}) or {}
    material = item.get(K["material"], {}) or {}
    cl = item.get(K["cluster"], {}) or {}

    rows: dict[str, list[dict[str, Any]]] = {table: [] for table in TABLE_COLUMNS}
    title = md.get(K["title"]) or item.get(K["file_name"], "")

    rows["POLICY"].append({
        "ROW_KEY": row_key,
        "FILE_NAME": item.get(K["file_name"]),
        "ABSTRACT": item.get(K["summary"]),
        "CONTACT_INFO": item.get(K["contact"]),
        "APPLICATION_VOLUME": item.get(K["volume"]),
        "POLICY_TITLE": title,
    })
    rows["POLICY_METADATA"].append({
        "ROW_KEY": row_key,
        "SCENE_USAGE": md.get(K["scene"]),
        "POLICY_ATTRIBUTE": md.get(K["attribute"]),
        "ISSUING_DEPARTMENT": md.get(K["department"]),
        "POLICY_TITLE_MAIN": title,
        "POLICY_ORIGINAL_TITLE": md.get(K["original_title"]),
        "EFFECTIVE_DATE": md.get(K["effective_date"]),
        "APPLICABLE_SCOPE": md.get(K["scope"]),
        "INDUSTRY_FIELD": md.get(K["industry"]),
        "REMARK": clean(md.get("\u653f\u7b56\u6587\u53f7")),
    })
    for keyword in md.get(K["keywords"], []) or []:
        rows["POLICY_KEYWORD"].append({"ROW_KEY": row_key, "KEYWORD": keyword})
    rows["APPLICATION_CONDITION"].append({
        "ROW_KEY": row_key,
        "APPLICABLE_TARGET": cond.get(K["target"]),
        "APPLICATION_REQUIREMENT": cond.get(K["requirement"]),
        "APPLICATION_TIME": cond.get(K["application_time"]),
        "ANNOUNCEMENT_DATE": cond.get(K["announcement_date"]),
    })
    rows["SUPPORT_METHOD"].append({
        "ROW_KEY": row_key,
        "SUPPORT_CONTENT_DESC": support.get(K["support_desc"]),
        "SUPPORT_TYPE": support.get(K["support_type"]),
        "SUPPORT_AMOUNT": support.get(K["support_amount"]),
        "MAX_SUPPORT_AMOUNT": support.get(K["max_support_amount"]),
    })
    rows["APPLICATION_MATERIAL"].append({
        "ROW_KEY": row_key,
        "APPLICATION_MATERIALS": material.get(K["materials"]),
        "APPLICATION_METHOD": material.get(K["method"]),
        "APPLICATION_URL": material.get(K["url"]),
    })
    attachments = material.get(K["attachments"], []) or []
    if isinstance(attachments, str):
        attachments = [attachments]
    for attachment in attachments:
        if clean(attachment):
            rows["APPLICATION_ATTACHMENT"].append({"ROW_KEY": row_key, "ATTACHMENT_NAME": attachment})

    enterprise_type = at(cl, P["biz"], P["basic"], "\u4f01\u4e1a\u7c7b\u578b")
    reg_address = at(cl, P["biz"], P["reg"], "\u6ce8\u518c\u5730\u5740")
    biz_scope = at(cl, P["biz"], P["bizinfo"], "\u7ecf\u8425\u8303\u56f4")
    rows["CONDITION_CLUSTER"].append({
        "ROW_KEY": row_key,
        "BASIC_UNIT_NAME": at(cl, P["biz"], P["basic"], "\u5355\u4f4d\u540d\u79f0"),
        "BASIC_CREDIT_CODE": at(cl, P["biz"], P["basic"], "\u7edf\u4e00\u793e\u4f1a\u4fe1\u7528\u4ee3\u7801"),
        "BASIC_REGISTER_DATE": at(cl, P["biz"], P["basic"], "\u6ce8\u518c\u65e5\u671f"),
        "BASIC_ENTERPRISE_NATURE": at(cl, P["biz"], P["basic"], "\u4f01\u4e1a\u6027\u8d28"),
        "BASIC_ENTERPRISE_TYPE_VALUE": qval(enterprise_type),
        "BASIC_ENTERPRISE_TYPE_SOURCE": qsrc(enterprise_type),
        "BASIC_LEGAL_REPRESENTATIVE": at(cl, P["biz"], P["basic"], "\u6cd5\u5b9a\u4ee3\u8868\u4eba"),
        "BASIC_INSTITUTION_NATURE": at(cl, P["biz"], P["basic"], "\u673a\u6784\u6027\u8d28"),
        "REG_ADDRESS_VALUE": qval(reg_address),
        "REG_ADDRESS_SOURCE": qsrc(reg_address),
        "REG_REGISTER_DATE": at(cl, P["biz"], P["reg"], "\u6ce8\u518c\u65e5\u671f"),
        "REG_ACTUAL_ADDRESS": at(cl, P["biz"], P["reg"], "\u5b9e\u9645\u7ecf\u8425\u5730\u5740"),
        "REG_TERRITORIAL_INFO": at(cl, P["biz"], P["reg"], "\u5c5e\u5730\u4fe1\u606f"),
        "REG_TAX_TERRITORY": at(cl, P["biz"], P["reg"], "\u7eb3\u7a0e\u5c5e\u5730"),
        "BIZ_SUBJECT_QUALIFICATION": at(cl, P["biz"], P["bizinfo"], "\u4e3b\u4f53\u8d44\u683c"),
        "BIZ_UNIT_TYPE": at(cl, P["biz"], P["bizinfo"], "\u5355\u4f4d\u7c7b\u578b"),
        "BIZ_OPERATION_STATUS": at(cl, P["biz"], P["bizinfo"], "\u7ecf\u8425\u72b6\u6001"),
        "BIZ_SCOPE_VALUE": qval(biz_scope),
        "BIZ_SCOPE_SOURCE": qsrc(biz_scope),
    })

    rows["MAIN_TECHNOLOGY"].append({
        "ROW_KEY": row_key,
        "NATIONAL_STRATEGIC_INDUSTRY_CATEGORY": at(cl, P["tech"], "\u56fd\u5bb6\u6218\u7565\u65b0\u5174\u4ea7\u4e1a\u5206\u7c7b"),
        "INDUSTRY_FIELD": at(cl, P["tech"], "\u6240\u5c5e\u884c\u4e1a\u9886\u57df"),
        "CORE_TECHNOLOGY_FIELD": at(cl, P["tech"], "\u6838\u5fc3\u6280\u672f\u9886\u57df"),
    })
    rows["MAIN_PRODUCT"].append({
        "ROW_KEY": row_key,
        "MAIN_PRODUCT_NAME": at(cl, P["product"], P["product_info"], "\u4e3b\u8425\u4ea7\u54c1"),
        "MAIN_SERVICE": at(cl, P["product"], P["product_info"], "\u4e3b\u8425\u670d\u52a1"),
        "CORE_PRODUCT": at(cl, P["product"], P["product_info"], "\u6838\u5fc3\u4ea7\u54c1"),
        "PRODUCT_STAGE": at(cl, P["product"], P["product_info"], "\u4ea7\u54c1\u9636\u6bb5"),
    })

    employee_count = at(cl, P["team"], P["basic"], "\u4f01\u4e1a\u804c\u5de5\u603b\u6570")
    tech_award = at(cl, P["team"], P["edu"], "\u79d1\u6280\u5956\u52b1")
    rows["CORE_TEAM"].append({
        "ROW_KEY": row_key,
        "TALENT_HEALTH_STATUS": at(cl, P["team"], P["talent"], "\u5065\u5eb7\u72b6\u51b5"),
        "TALENT_NATIONALITY_REQ": at(cl, P["team"], P["talent"], "\u56fd\u7c4d\u8981\u6c42"),
        "TALENT_AGE_REQ": qval(at(cl, P["team"], P["talent"], "\u5e74\u9f84\u8981\u6c42")),
        "TALENT_LANGUAGE_ABILITY": at(cl, P["team"], P["talent"], "\u8bed\u8a00\u80fd\u529b"),
        "TEAM_EMPLOYEE_COUNT_VALUE": qval(employee_count),
        "TEAM_EMPLOYEE_COUNT_OPERATOR": qop(employee_count),
        "TEAM_EMPLOYEE_COUNT_UNIT": qunit(employee_count),
        "TEAM_EMPLOYEE_COUNT_SOURCE": qsrc(employee_count),
        "TEAM_SIZE": at(cl, P["team"], P["basic"], "\u56e2\u961f\u4eba\u6570"),
        "TEAM_RD_PERSONNEL_COUNT": qval(at(cl, P["team"], P["basic"], "\u7814\u53d1\u4eba\u5458\u6570\u91cf")),
        "TEAM_TECH_PERSONNEL_RATIO": qval(at(cl, P["team"], P["basic"], "\u79d1\u6280\u4eba\u5458\u5360\u6bd4")),
        "TEAM_TECH_PERSONNEL_COUNT": qval(at(cl, P["team"], P["basic"], "\u79d1\u6280\u4eba\u5458\u6570\u91cf")),
        "TEAM_PROJECT_LEADER_ROLE": at(cl, P["team"], P["basic"], "\u9879\u76ee\u8d1f\u8d23\u4eba\u8eab\u4efd"),
        "EDU_PHD_COUNT": qval(at(cl, P["team"], P["edu"], "\u535a\u58eb\u4eba\u6570")),
        "EDU_BACHELOR_COUNT": qval(at(cl, P["team"], P["edu"], "\u672c\u79d1\u4eba\u6570")),
        "EDU_MASTER_COUNT": qval(at(cl, P["team"], P["edu"], "\u7855\u58eb\u4eba\u6570")),
        "EDU_TECH_AWARD_VALUE": qval(tech_award),
        "EDU_TECH_AWARD_SOURCE": qsrc(tech_award),
        "EDU_TITLE_REQ": at(cl, P["team"], P["edu"], "\u804c\u79f0\u8981\u6c42"),
        "EDU_PROJECT_EXPERIENCE": at(cl, P["team"], P["edu"], "\u9879\u76ee\u7ecf\u5386"),
    })

    rows["INNOVATION_INVESTMENT"].append({
        "ROW_KEY": row_key,
        "INFRA_FACILITY_STATUS": at(cl, P["investment"], P["infra"], "\u8bbe\u65bd\u60c5\u51b5"),
        "MGMT_RD_SYSTEM_VALUE": qbool(at(cl, P["investment"], P["mgmt"], "\u7814\u53d1\u5236\u5ea6")),
        "MGMT_RD_SYSTEM_SOURCE": qsrc(at(cl, P["investment"], P["mgmt"], "\u7814\u53d1\u5236\u5ea6")) or clean(at(cl, P["investment"], P["mgmt"], "\u7814\u53d1\u5236\u5ea6")),
        "EQUIP_RD_EQUIPMENT_ORIGINAL": qval(at(cl, P["investment"], P["equip"], "\u7814\u53d1\u8bbe\u5907\u539f\u503c")),
        "COST_ANNUAL_RD_INVESTMENT": qval(at(cl, P["investment"], P["cost"], "\u5e74\u7814\u53d1\u6295\u5165")),
        "COST_RD_INVESTMENT_INTENSITY": qval(at(cl, P["investment"], P["cost"], "\u7814\u53d1\u6295\u5165\u5f3a\u5ea6")),
        "PROJ_IMPLEMENTATION_LOCATION": at(cl, P["investment"], P["proj_cond"], "\u5b9e\u65bd\u5730\u70b9"),
        "PROJ_EXECUTION_PERIOD": at(cl, P["investment"], P["proj_cond"], "\u6267\u884c\u671f\u9650"),
        "PROJ_PROJECT_FUNDING": qval(at(cl, P["investment"], P["proj_cond"], "\u9879\u76ee\u7ecf\u8d39")),
    })

    standard = at(cl, P["output"], P["ip"], "\u6807\u51c6")
    rows["INNOVATION_OUTPUT"].append({
        "ROW_KEY": row_key,
        "TRADE_ACHIEVEMENT_TYPE": at(cl, P["output"], P["trade"], "\u6210\u679c\u7c7b\u578b"),
        "TRADE_TECH_TRADE_AMOUNT": qval(at(cl, P["output"], P["trade"], "\u6280\u672f\u4ea4\u6613\u989d")),
        "TRADE_TECH_ACHIEVEMENT_TRANSFER": at(cl, P["output"], P["trade"], "\u6280\u672f\u6210\u679c\u8f6c\u5316"),
        "IP_PATENT": at(cl, P["output"], P["ip"], "\u4e13\u5229"),
        "IP_ACHIEVEMENT": at(cl, P["output"], P["ip"], "\u6210\u679c"),
        "IP_STANDARD_VALUE": qval(standard),
        "IP_STANDARD_SOURCE": qsrc(standard),
        "IP_PAPER_LITERATURE": qval(at(cl, P["output"], P["ip"], "\u8bba\u6587\u6216\u6587\u732e")),
        "IP_SOFTCOPY_TRADEMARK": qval(at(cl, P["output"], P["ip"], "\u8f6f\u8457\u6216\u5546\u6807")),
        "IP_IP_AGREEMENT": at(cl, P["output"], P["ip"], "\u77e5\u8bc6\u4ea7\u6743\u7ea6\u5b9a"),
    })

    def metric(prefix: str, name: str) -> Any:
        return at(cl, P["development"], prefix, name)

    personnel = metric(P["scale"], "\u4eba\u5458\u89c4\u6a21")
    market = metric(P["scale"], "\u5e02\u573a\u89c4\u6a21")
    revenue = metric(P["scale"], "\u8425\u6536\u89c4\u6a21")
    asset = metric(P["scale"], "\u8d44\u4ea7\u89c4\u6a21")
    rows["INNOVATION_DEVELOPMENT"].append({
        "ROW_KEY": row_key,
        "SCALE_PERSONNEL_VALUE": qval(personnel),
        "SCALE_PERSONNEL_OPERATOR": qop(personnel),
        "SCALE_PERSONNEL_UNIT": qunit(personnel),
        "SCALE_PERSONNEL_SOURCE": qsrc(personnel),
        "SCALE_MARKET_VALUE": qval(market),
        "SCALE_MARKET_OPERATOR": qop(market),
        "SCALE_MARKET_UNIT": qunit(market),
        "SCALE_MARKET_SOURCE": qsrc(market),
        "SCALE_TAX_SCALE": qval(metric(P["scale"], "\u7a0e\u6536\u89c4\u6a21")),
        "SCALE_REVENUE_VALUE": qval(revenue),
        "SCALE_REVENUE_OPERATOR": qop(revenue),
        "SCALE_REVENUE_UNIT": qunit(revenue),
        "SCALE_REVENUE_SOURCE": qsrc(revenue),
        "SCALE_ASSET_VALUE": qval(asset),
        "SCALE_ASSET_OPERATOR": qop(asset),
        "SCALE_ASSET_UNIT": qunit(asset),
        "SCALE_ASSET_SOURCE": qsrc(asset),
        "GROWTH_NET_PROFIT_GROWTH_RATE": qval(metric(P["growth"], "\u51c0\u5229\u6da6\u589e\u957f\u7387")),
        "GROWTH_NET_ASSET_GROWTH_RATE": qval(metric(P["growth"], "\u51c0\u8d44\u4ea7\u589e\u957f\u7387")),
        "GROWTH_RD_GROWTH_RATE": qval(metric(P["growth"], "\u7814\u53d1\u589e\u957f\u7387")),
        "GROWTH_REVENUE_GROWTH_RATE": qval(metric(P["growth"], "\u8425\u6536\u589e\u957f\u7387")),
    })

    enterprise_credit = at(cl, P["impact"], P["credit"], "\u4f01\u4e1a\u4fe1\u7528")
    safety = at(cl, P["impact"], P["credit"], "\u5b89\u5168\u751f\u4ea7")
    business = at(cl, P["impact"], P["credit"], "\u5de5\u5546\u4fe1\u7528")
    legal = at(cl, P["impact"], P["credit"], "\u6cd5\u5f8b\u5408\u89c4")
    research = at(cl, P["impact"], P["credit"], "\u79d1\u7814\u4fe1\u7528")
    quality = at(cl, P["impact"], P["credit"], "\u8d28\u91cf\u5408\u89c4")
    financing = at(cl, P["impact"], P["finance"], "\u878d\u8d44\u91d1\u989d")
    talent_qual = at(cl, P["impact"], P["qual"], "\u4eba\u624d\u8d44\u8d28")
    enterprise_qual = at(cl, P["impact"], P["qual"], "\u4f01\u4e1a\u8d44\u8d28")
    platform_qual = at(cl, P["impact"], P["qual"], "\u5e73\u53f0\u8d44\u8d28")
    rows["INNOVATION_IMPACT"].append({
        "ROW_KEY": row_key,
        "COLLAB_AGREEMENT": at(cl, P["impact"], P["collab"], "\u5408\u4f5c\u534f\u8bae"),
        "COLLAB_FOUNDATION": at(cl, P["impact"], P["collab"], "\u5408\u4f5c\u57fa\u7840"),
        "COLLAB_INTERNATIONAL_FOUNDATION": at(cl, P["impact"], P["collab"], "\u56fd\u9645\u5408\u4f5c\u57fa\u7840"),
        "COLLAB_RESEARCH_INSTITUTE": at(cl, P["impact"], P["collab"], "\u79d1\u7814\u9662\u6240\u5408\u4f5c"),
        "COLLAB_UNIVERSITY": at(cl, P["impact"], P["collab"], "\u9ad8\u6821\u5408\u4f5c"),
        "CREDIT_PRODUCT_COMPLIANCE": at(cl, P["impact"], P["credit"], "\u4ea7\u54c1\u5408\u89c4"),
        "CREDIT_ENTERPRISE_CREDIT": qsrc(enterprise_credit) or qval(enterprise_credit),
        "CREDIT_ETHICS_COMPLIANCE": at(cl, P["impact"], P["credit"], "\u4f26\u7406\u5408\u89c4"),
        "CREDIT_SAFETY_MAJOR_ACCIDENT": qbool(safety, "\u91cd\u5927\u5b89\u5168\u4e8b\u6545"),
        "CREDIT_SAFETY_SOURCE": qsrc(safety),
        "CREDIT_BUSINESS_ABNORMAL_LIST": qbool(enterprise_credit, "\u7ecf\u8425\u5f02\u5e38\u540d\u5f55"),
        "CREDIT_BUSINESS_SERIOUS_DISHONESTY_LIST": qbool(business, "\u4e25\u91cd\u8fdd\u6cd5\u5931\u4fe1\u4f01\u4e1a\u540d\u5355"),
        "CREDIT_BUSINESS_SOURCE": qsrc(business) or qsrc(enterprise_credit),
        "CREDIT_ANNUAL_REPORT_FORMULA": at(cl, P["impact"], P["credit"], "\u5e74\u62a5\u516c\u5f0f"),
        "CREDIT_GOVERNMENT_PERSONNEL_RESTRICTION": at(cl, P["impact"], P["credit"], "\u653f\u5e9c\u4eba\u5458\u9650\u5236"),
        "CREDIT_LEGAL_COMPLIANCE_VALUE": qbool(legal),
        "CREDIT_LEGAL_COMPLIANCE_SOURCE": qsrc(legal) or clean(legal),
        "CREDIT_RESEARCH_CREDIT_VALUE": qbool(research, "\u79d1\u7814\u4e25\u91cd\u5931\u4fe1"),
        "CREDIT_RESEARCH_CREDIT_SOURCE": qsrc(research),
        "CREDIT_TAX_CREDIT": at(cl, P["impact"], P["credit"], "\u7a0e\u52a1\u4fe1\u7528"),
        "CREDIT_ADMIN_PENALTY_VALUE": qbool(at(cl, P["impact"], P["credit"], "\u884c\u653f\u5904\u7f5a")),
        "CREDIT_ADMIN_PENALTY_SOURCE": qsrc(at(cl, P["impact"], P["credit"], "\u884c\u653f\u5904\u7f5a")) or clean(at(cl, P["impact"], P["credit"], "\u884c\u653f\u5904\u7f5a")),
        "CREDIT_QUALITY_MAJOR_ACCIDENT": qbool(quality, "\u91cd\u5927\u8d28\u91cf\u4e8b\u6545"),
        "CREDIT_QUALITY_SOURCE": qsrc(quality),
        "CREDIT_REPEAT_APPLICATION_RESTRICTION": at(cl, P["impact"], P["credit"], "\u91cd\u590d\u7533\u62a5\u9650\u5236"),
        "FIN_INVESTOR": at(cl, P["impact"], P["finance"], "\u6295\u8d44\u5355\u4f4d"),
        "FIN_FINANCING_AMOUNT": qval(financing),
        "FIN_FINANCING_STAGE": at(cl, P["impact"], P["finance"], "\u878d\u8d44\u9636\u6bb5"),
        "QUAL_TALENT_QUALIFICATION": qval(talent_qual),
        "QUAL_ENTERPRISE_QUALIFICATION_VALUE": qval(enterprise_qual),
        "QUAL_ENTERPRISE_QUALIFICATION_SOURCE": qsrc(enterprise_qual),
        "QUAL_PLATFORM_QUALIFICATION_VALUE": qval(platform_qual),
        "QUAL_PLATFORM_QUALIFICATION_SOURCE": qsrc(platform_qual),
        "PROJ_COOP_UNIT_COUNT": qval(at(cl, P["impact"], P["proj"], "\u5171\u5efa\u5355\u4f4d\u6570\u91cf")),
        "PROJ_COOP_UNIT_TYPE": at(cl, P["impact"], P["proj"], "\u5171\u5efa\u5355\u4f4d\u7c7b\u578b"),
        "PROJ_JOINT_APPLICATION": at(cl, P["impact"], P["proj"], "\u8054\u5408\u7533\u62a5"),
    })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-file", default="metadata/output_test_processed/all_policy_data11.json")
    parser.add_argument("--out-dir", default="tmp/policy_data11_stage")
    args = parser.parse_args()

    data = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
    items = data.get(K["list"], [])
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_rows: dict[str, list[dict[str, Any]]] = {table: [] for table in TABLE_COLUMNS}
    for index, item in enumerate(items, start=1):
        rows = build_rows(item, str(index))
        for table, table_rows in rows.items():
            all_rows[table].extend(table_rows)

    for table, rows in all_rows.items():
        write_table(out_dir, table, rows)
        print(f"{table}: {len(rows)}")


if __name__ == "__main__":
    main()
