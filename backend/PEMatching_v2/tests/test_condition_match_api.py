# -*- coding: utf-8 -*-
"""T8 新条件匹配 API 适配层测试。"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

import api_server as api
from api_server import ApiError, _parse_condition_options, handle_condition_batch, handle_condition_enterprise


def _record(name="测试企业"):
    return {"enterpriseName": name}


def _result(name="测试企业"):
    return {
        "data": {"enterpriseName": name, "recommendations": []},
        "meta": {
            "engine": "condition_llm", "policyVersion": "a" * 64, "policyCount": 1,
            "candidateCount": 1, "excludedCount": 0, "modelCallCount": 0, "cacheHitCount": 0,
            "failedCount": 0, "modelRetryCount": 0, "modelQueueWaitMs": 0,
            "scoringConfigVersion": "test", "allRecommendationCount": 0, "returnedCount": 0,
            "elapsedMs": 0, "globalExclusion": None, "prefilterExclusions": [], "failures": [],
        },
    }


def test_新接口默认返回全部_只在显式topN时分页():
    assert _parse_condition_options({}) == {"top_n": None, "today": None}
    assert _parse_condition_options({"topN": "2", "today": "2026-08-07"}) == {
        "top_n": 2, "today": date(2026, 8, 7),
    }
    with pytest.raises(ApiError, match="topN"):
        _parse_condition_options({"topN": "0"})


def test_新单企业和批量接口分别委托统一编排器():
    options = {"top_n": None, "today": date(2026, 8, 7)}
    with patch.object(api, "run_condition_match", return_value=_result()) as single, \
         patch.object(api, "run_condition_match_batch", return_value={
             "data": {"results": []}, "meta": {"engine": "condition_llm", "total": 1, "succeeded": 1, "failed": 0},
         }) as batch:
        assert handle_condition_enterprise(_record(), options)["meta"]["engine"] == "condition_llm"
        assert handle_condition_batch([_record()], options)["meta"]["succeeded"] == 1

    single.assert_called_once_with(_record(), today=date(2026, 8, 7), top_n=None)
    batch.assert_called_once_with([_record()], today=date(2026, 8, 7), top_n=None)


def test_默认引擎和既有tagmatch接口均切到新条件链路():
    with patch.object(api, "env_str", side_effect=lambda _name, default: default):
        assert api._active_engine() == "condition_llm"

    options = {"top_n": 2, "top_n_explicit": True, "today": date(2026, 8, 7)}
    with patch.object(api, "_active_engine", return_value="condition_llm"), \
         patch.object(api, "run_condition_match", return_value=_result()) as matched, \
         patch.object(api, "run_match") as legacy:
        result = api.handle_enterprise(_record(), options)

    assert result["meta"]["engine"] == "condition_llm"
    matched.assert_called_once_with(_record(), today=date(2026, 8, 7), top_n=2)
    legacy.assert_not_called()
