# -*- coding: utf-8 -*-
"""独立资格接口：顺序、开关和 V2 隔离。"""

from __future__ import annotations

from copy import deepcopy

import pytest

import api_server as api


def test_资格接口默认关闭():
    with pytest.raises(api.ApiError) as exc:
        api.handle_eligibility({"enterprise": {"enterpriseName": "甲"}, "fusionPolicyIds": ["P1"]})
    assert exc.value.status == 404


def test_资格接口保持政策输入顺序且不归档(monkeypatch):
    def enabled(name, default=False):
        return name == "PEMATCHING_V2_ELIGIBILITY_ENABLED"

    monkeypatch.setattr(api, "env_bool", enabled)
    monkeypatch.setattr(api, "evaluate_policies", lambda profile, ids, **kwargs: {"eligibilityEvaluations": [{"fusionPolicyId": item} for item in ids], "eligibilitySchemaVersion": "v1", "ruleManifestHash": "hash"})

    data, meta = api.handle_eligibility({"enterprise": {"enterpriseName": "甲", "metrics": {}}, "fusionPolicyIds": ["P2", "P1"]})

    assert [item["fusionPolicyId"] for item in data["eligibilityEvaluations"]] == ["P2", "P1"]
    assert meta["archiveEnabled"] is False
    assert meta["runId"]


def test_响应隐藏明细也不影响完整归档(monkeypatch):
    monkeypatch.setattr(api, "env_bool", lambda name, default=False: name in {"PEMATCHING_V2_ELIGIBILITY_ENABLED", "PEMATCHING_V2_ELIGIBILITY_ARCHIVE_ENABLED"})
    full_result = {"eligibilityEvaluations": [{"fusionPolicyId": "P1", "conditionDetails": [{"秘密": "证据"}], "ruleGroupDetails": [{"items": [1]}]}], "eligibilitySchemaVersion": "v1", "ruleManifestHash": "hash"}
    monkeypatch.setattr(api, "evaluate_policies", lambda *args, **kwargs: full_result)
    archived = {}
    monkeypatch.setattr(api, "archive_run", lambda run_id, payload: archived.update(payload))

    data, _ = api.handle_eligibility({"enterprise": {"enterpriseName": "甲", "metrics": {}}, "fusionPolicyIds": ["P1"], "includeConditionDetails": False})

    assert data["eligibilityEvaluations"][0]["conditionDetails"] == []
    assert archived["result"]["eligibilityEvaluations"][0]["conditionDetails"] == [{"秘密": "证据"}]


def test_资格调用不触碰既有推荐对象(monkeypatch):
    """接口只构造独立结果，传入的 V2 推荐结果不在其读写范围内。"""
    recommendation = {"recommendations": [{"fusionPolicyId": "P1", "rank": 1, "totalScore": 88.0, "band": "direct_push"}]}
    before = deepcopy(recommendation)
    monkeypatch.setattr(api, "env_bool", lambda name, default=False: name == "PEMATCHING_V2_ELIGIBILITY_ENABLED")
    monkeypatch.setattr(api, "evaluate_policies", lambda *args, **kwargs: {"eligibilityEvaluations": [], "eligibilitySchemaVersion": "v1", "ruleManifestHash": "hash"})

    api.handle_eligibility({"enterprise": {"enterpriseName": "甲", "metrics": {}}, "fusionPolicyIds": ["P1"]})

    assert recommendation == before
