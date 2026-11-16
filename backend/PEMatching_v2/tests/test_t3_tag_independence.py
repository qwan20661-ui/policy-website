# -*- coding: utf-8 -*-
"""T3 新政策源、资质成长图与旧链路隔离测试。"""

from __future__ import annotations

import inspect
import json
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

import api_server as api
from service import match_policy_source as source
from service.match_policy_source import direct_policy_fields, load_match_policy_packages
from service.policy_condition_package import build_condition_package_snapshot
from service.qualification_growth import (
    QualificationGrowthConfigError,
    load_qualification_growth_config,
    qualification_growth_stats,
    successor_target_ids,
    validate_qualification_growth_config,
)


def _policy():
    return {
        "fusionPolicyId": "T3-P1",
        "sourcePolicyId": "S1",
        "title": "测试企业资质申报",
        "policyInfo": {
            "policyTitle": "测试企业资质申报",
            "industryField": "软件和信息技术服务业",
            "industryFieldPath": "电子信息-软件",
            "applicableScope": "厦门市思明区",
            "applicableScopePath": "市级-厦门市-思明区",
            "effectiveStartDate": "2026-01-01",
            "effectiveEndDate": "2027-12-31",
        },
        "conditions": [{
            "id": "C1",
            "applicableTarget": "企业",
            "applicableTargetPath": "企业-科技企业",
            "applicationInstruction": "通过系统申报",
            "applicationRequirement": "在思明区依法登记的软件企业",
            "applicationStartTime": "2026-08-01",
            "applicationEndTime": "2026-08-31",
            "announcementDate": "2026-07-20",
        }],
        "ruleTableJson": {},
        "supportMethods": [{
            "id": "M1",
            "supportType": "一次性奖励补助",
            "supportTypePath": "资金补助-一次性奖励补助",
            "contentDesc": "按实际投入给予补助",
            "amount": "10万元",
            "maxAmount": "50万元",
        }],
        "keywords": [{"id": "K1", "text": "资质认定", "pathText": "资质认定"}],
        "documents": [],
        "relatedPolicies": {},
    }


def _write_condition_snapshot(tmp_path):
    snapshot = build_condition_package_snapshot({
        "schemaVersion": "pematching-v2-policy-snapshot-v1",
        "versionHash": "a" * 64,
        "count": 1,
        "data": [_policy()],
    })
    path = tmp_path / "policy_condition_snapshot.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    return path


def test_新政策源模块没有旧打标与旧索引依赖():
    module_source = inspect.getsource(source)

    assert "service.tagging" not in module_source
    assert "policy_index" not in module_source
    assert "policy_tags" not in module_source


def test_标签文件删除或清空不影响新政策源(tmp_path):
    condition_path = _write_condition_snapshot(tmp_path)
    legacy_path = tmp_path / "policy_tags.json"

    assert not legacy_path.exists()
    first = load_match_policy_packages(condition_path)
    legacy_path.write_text("{}", encoding="utf-8")
    second = load_match_policy_packages(condition_path)

    assert list(first) == ["T3-P1"]
    assert second == first


def test_匹配事实直接取自条件包字段(tmp_path):
    package = load_match_policy_packages(_write_condition_snapshot(tmp_path))["T3-P1"]

    facts = direct_policy_fields(package)

    assert facts["industry"] == {
        "value": "软件和信息技术服务业",
        "path": "电子信息-软件",
    }
    assert facts["location"]["value"] == "厦门市思明区"
    assert facts["applicantTargets"][0]["applicableTarget"] == "企业"
    assert facts["supportMethods"][0]["amount"] == "10万元"
    assert facts["supportMethods"][0]["maxAmount"] == "50万元"
    assert facts["applicationWindows"][0]["applicationStartTime"] == "2026-08-01"
    assert facts["applicationWindows"][0]["applicationEndTime"] == "2026-08-31"
    assert facts["effectivePeriod"] == {"start": "2026-01-01", "end": "2027-12-31"}


def test_资质成长图只返回直接后继且保留业务分支():
    stats = qualification_growth_stats()

    assert successor_target_ids("no_technology_qualification") == [
        "innovative_sme", "tech_sme"
    ]
    assert successor_target_ids("innovative_sme") == ["specialized_sme"]
    assert successor_target_ids("tech_sme") == ["high_tech_enterprise"]
    assert successor_target_ids("high_tech_enterprise") == [
        "new_research_institution", "future_industry_backbone"
    ]
    assert stats["branchStateCount"] >= 3


def test_资质成长图拒绝悬空后继引用():
    config = deepcopy(load_qualification_growth_config())
    config["states"][0]["nextTargetIds"].append("missing_target")

    with pytest.raises(QualificationGrowthConfigError, match="不存在的后继目标"):
        validate_qualification_growth_config(config)


def _run_main_with_legacy_switch(enabled):
    server = MagicMock()
    server.serve_forever.side_effect = KeyboardInterrupt

    def fake_env_bool(name, default=False):
        if name == "PEMATCHING_V2_LEGACY_TAGGING_ON_STARTUP":
            return enabled
        return default

    with patch.object(api, "env_bool", side_effect=fake_env_bool), \
         patch.object(api, "_startup_sync_policies", return_value=True), \
         patch.object(api, "_startup_build_policy_condition_packages", return_value=True), \
         patch.object(api, "match_policy_source_stats", return_value={
             "count": 1, "versionHash": "a" * 64,
         }), \
         patch.object(api.pi, "index_stats") as legacy_index, \
         patch.object(api, "_startup_sync_eligibility"), \
         patch.object(api, "_startup_sync_legacy_policy_tags") as legacy_sync, \
         patch.object(api, "ThreadingHTTPServer", return_value=server):
        return_code = api.main()

    legacy_index.assert_not_called()
    return return_code, legacy_sync


def test_新链路默认启动不触发旧打标任务():
    return_code, legacy_sync = _run_main_with_legacy_switch(False)

    assert return_code == 0
    legacy_sync.assert_not_called()


def test_旧链路显式开关保留后台打标回滚入口():
    return_code, legacy_sync = _run_main_with_legacy_switch(True)

    assert return_code == 0
    legacy_sync.assert_called_once_with()


def test_旧打标函数关闭时不创建后台线程():
    with patch.object(api, "env_bool", return_value=False), \
         patch.object(api.threading, "Thread") as thread:
        api._startup_sync_legacy_policy_tags()

    thread.assert_not_called()
