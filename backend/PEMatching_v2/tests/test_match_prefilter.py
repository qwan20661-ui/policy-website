# -*- coding: utf-8 -*-
"""T4 确定性预过滤：正例、反例和缺失事实必须有不同结果。"""

from __future__ import annotations

from copy import deepcopy
from datetime import date

from service.match_prefilter import prefilter_policies, prefilter_policy


def _profile():
    return {
        "credit": {"checked": False, "negative": None, "raw": ""},
        "metrics": {
            "ipClass1Count": 2, "ipClass2Count": 0, "ipTotal": 2,
            "companyAgeYears": 3, "revenueWan": 100, "employeeCount": 10,
            "totalAssetsWan": 100, "netAssetsWan": 50,
        },
        "qualifications": {
            "isTechSme": None, "isInnovativeSme": None, "isHighTechEnterprise": None,
            "isSpecializedSme": None, "isSpecializedLittleGiant": None,
        },
        "eligibilityFacts": {},
        "registerInfo": {"registerDistrict": "厦门市思明区", "registerAddress": "厦门市思明区软件园"},
        "industryInfo": {"level1": "信息传输业", "level2": "软件和信息技术服务业", "industryField": ""},
    }


def _policy(**overrides):
    package = {
        "fusionPolicyId": "P1",
        "title": "测试通用政策",
        "policyInfo": {"policyTitle": "测试通用政策", "applicableScope": "通用", "industryField": "通用行业"},
        "conditions": [{
            "id": "C1", "applicableTarget": "企业", "applicableTargetPath": "企业",
            "applicationRequirement": "企业可申报。", "applicationStartTime": "2026-08-01",
            "applicationEndTime": "2026-08-31", "announcementDate": "2026-07-01",
        }],
        "ruleTableJson": {"hardRuleGroups": [], "softRuleGroups": [], "specialPassRuleGroups": []},
        "supportMethods": [], "keywords": [], "documents": [],
    }
    package.update(overrides)
    return package


def test_信用负面整家拦截且不留下候选():
    profile = _profile()
    profile["credit"] = {"checked": True, "negative": True, "raw": "列入经营异常名录"}

    result = prefilter_policies(profile, [_policy()], today=date(2026, 8, 7))

    assert result["globalExclusion"]["code"] == "NEGATIVE_CREDIT"
    assert result["candidates"] == []


def test_个人高校和前年政策明确排除():
    personal = _policy()
    personal["conditions"][0]["applicableTarget"] = "高校及科研院所"
    personal["conditions"][0]["applicableTargetPath"] = "高校-科研院所"
    old = _policy(fusionPolicyId="P2")
    old["conditions"][0]["applicationStartTime"] = "2024-01-01"

    assert prefilter_policy(_profile(), personal, date(2026, 8, 7))["code"] == "SUBJECT_NOT_ENTERPRISE"
    assert prefilter_policy(_profile(), old, date(2026, 8, 7))["code"] == "POLICY_TOO_OLD"


def test_属地行业明确不符才排除_缺失保持候选():
    other_city = _policy()
    other_city["policyInfo"]["applicableScope"] = "福州市"
    other_industry = _policy(fusionPolicyId="P2")
    other_industry["policyInfo"]["industryField"] = "农业"
    missing_enterprise_location = _profile()
    missing_enterprise_location["registerInfo"] = {"registerDistrict": "", "registerAddress": ""}

    assert prefilter_policy(_profile(), other_city, date(2026, 8, 7))["code"] == "LOCATION_MISMATCH"
    assert prefilter_policy(_profile(), other_industry, date(2026, 8, 7))["code"] == "INDUSTRY_MISMATCH"
    assert prefilter_policy(missing_enterprise_location, other_city, date(2026, 8, 7)) is None


def test_无资质只保留起点_已有高企只保留直接后继():
    start = _policy(title="科技型中小企业入库", fusionPolicyId="start")
    high = _policy(title="国家高新技术企业认定", fusionPolicyId="high")
    no_qualification = _profile()
    high_qualification = _profile()
    high_qualification["qualifications"]["isHighTechEnterprise"] = True

    assert prefilter_policy(no_qualification, start, date(2026, 8, 7)) is None
    assert prefilter_policy(high_qualification, high, date(2026, 8, 7))["code"] == "QUALIFICATION_NOT_DIRECT_SUCCESSOR"


def test_无专利研发补助排除_发明字段回退时保留():
    subsidy = _policy(title="企业研发费用补助")
    subsidy["supportMethods"] = [{"supportType": "研发经费补助"}]
    no_ip = _profile()
    no_ip["metrics"].update({"ipClass1Count": 0, "ipClass2Count": 0, "ipTotal": 0})
    fallback = _profile()
    fallback["metrics"].update({"ipClass1Count": None, "ipClass2Count": None, "ipTotal": 1})

    assert prefilter_policy(no_ip, subsidy, date(2026, 8, 7))["code"] == "IP_TOTAL_ZERO_FOR_RD_SUBSIDY"
    assert prefilter_policy(fallback, subsidy, date(2026, 8, 7)) is None


def test_不可逆上限排除_缺失值不排除():
    package = _policy()
    package["ruleTableJson"]["hardRuleGroups"] = [{
        "conditionNo": 1, "conditionOperator": "AND", "conditionFieldItems": [{
            "itemId": "1", "itemName": "成立年限", "enterpriseFields": ["成立年限"],
            "policyFragment": "成立不超过2年", "matchRule": {"ruleId": "H1", "operator": "lte", "targetValue": 2, "unit": "年"},
        }],
    }]
    missing = deepcopy(_profile())
    missing["metrics"]["companyAgeYears"] = None

    assert prefilter_policy(_profile(), package, date(2026, 8, 7))["code"] == "IRREVERSIBLE_UPPER_BOUND"
    assert prefilter_policy(missing, package, date(2026, 8, 7)) is None
