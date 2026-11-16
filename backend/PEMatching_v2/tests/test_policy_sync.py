# -*- coding: utf-8 -*-
"""政策快照测试。HTTP 全部 mock，不发真实请求。

对应 docs/二期_政策打标开发流程.md 第 2.6 节测试要求。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from service import policy_sync as ps


@pytest.fixture(autouse=True)
def _restore_sync_state():
    original = ps.get_sync_status()
    yield
    ps._set_sync_state(**original)


def _response(payload: dict) -> MagicMock:
    handle = MagicMock()
    handle.read.return_value = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handle.__enter__ = lambda self: self
    handle.__exit__ = lambda *args: None
    return handle


def _list_payload(items, total=None):
    return {
        "success": True,
        "data": items,
        "pagination": {"page": 1, "size": 100, "total": total if total is not None else len(items)},
    }


def _detail_payload(detail):
    return {"success": True, "data": detail}


def _summary(policy_id="6450", title="某某政策"):
    return {"id": policy_id, "policyTitle": title, "updatedAt": "2026-07-28 09:23:56"}


def _detail(policy_id="6450", **overrides):
    detail = {
        "id": policy_id,
        "sourcePolicyId": "24038",
        "directoryTitle": "某某政策",
        "status": "ACTIVE",
        "reviewStatus": "1",
        "structureReviewStatus": "1",
        "reviewedAt": "2026-07-24 16:25:57",
        "policyInfo": {"policyTitle": "某某政策", "summary": "摘要文本", "industryField": "通用行业"},
        "supportMethods": [{"supportType": "一次性奖励补助", "amount": "50万元"}],
        "conditions": [{"applicableTarget": "企业", "applicationRequirement": "需具备资质"}],
        "keywords": [{"text": "研发经费补助", "pathText": "研发创新-研发经费补助"}],
        "materials": [{"materials": "申报书"}],
        "documents": [{"publishDate": "2026-03-01", "policyLevel": "市级-厦门市",
                       "policyAttribute": "申报通知", "issuingDepartment": "科技局",
                       "content": "正文很长应被裁掉"}],
        "parentPolicy": {},
        "directParentPolicies": [],
        "ancestorPolicies": [],
        "childPolicies": [],
        "descendantPolicies": [],
    }
    detail.update(overrides)
    return detail


# --------------------------------------------------------------------------
# 快照结构
# --------------------------------------------------------------------------


def test_快照结构与schema版本(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", tmp_path / "snapshot.json")
    with patch.object(ps.urllib.request, "urlopen") as mocked:
        mocked.side_effect = [
            _response(_list_payload([_summary()])),
            _response(_detail_payload(_detail())),
        ]
        snapshot = ps.fetch_policies(force=True)

    assert snapshot["schemaVersion"] == ps.SNAPSHOT_SCHEMA
    assert snapshot["count"] == 1
    assert snapshot["fetchedAt"]
    assert snapshot["source"]
    assert len(snapshot["data"]) == 1


def test_单条政策保留打标所需字段(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", tmp_path / "snapshot.json")
    with patch.object(ps.urllib.request, "urlopen") as mocked:
        mocked.side_effect = [
            _response(_list_payload([_summary()])),
            _response(_detail_payload(_detail())),
        ]
        snapshot = ps.fetch_policies(force=True)

    record = snapshot["data"][0]
    for field in (
        "fusionPolicyId", "title", "updateTime", "policyInfo", "supportMethods",
        "conditions", "structuredConditions", "ruleTableJson", "keywords", "materials",
        "documents", "relatedPolicies", "policyContentHash", "structureReviewStatus",
    ):
        assert field in record, field


def test_文档裁掉正文只留时效性信息(tmp_path, monkeypatch):
    """不整份 raw_detail 落盘，正文对打标无用且体积大。"""
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", tmp_path / "snapshot.json")
    with patch.object(ps.urllib.request, "urlopen") as mocked:
        mocked.side_effect = [
            _response(_list_payload([_summary()])),
            _response(_detail_payload(_detail())),
        ]
        snapshot = ps.fetch_policies(force=True)

    doc = snapshot["data"][0]["documents"][0]
    assert doc["publishDate"] == "2026-03-01"
    assert "content" not in doc


def test_申报条件和规则表原样保留():
    conditions = [{"applicationRequirement": "条件原文", "applicationEndTime": "2026-09-30"}]
    rule_table = {"softRuleGroups": [{"conditionOperator": "OR"}]}

    record = ps._normalize(
        {"id": "X"},
        {"id": "X", "conditions": conditions, "ruleTableJson": rule_table},
    )

    assert record["conditions"] == conditions
    assert record["structuredConditions"] == conditions
    assert record["ruleTableJson"] == rule_table


# --------------------------------------------------------------------------
# 关联政策只展开一层
# --------------------------------------------------------------------------


def test_关联政策只展开一层(tmp_path, monkeypatch):
    """造三层嵌套，断言第三层被截断——递归展开会爆炸。"""
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", tmp_path / "snapshot.json")
    deep_parent = {
        "id": "P1",
        "policyTitle": "父政策",
        "policyInfo": {"summary": "父摘要"},
        "conditions": [{"applicableTarget": "父条件"}],
        "parentPolicy": {"id": "P0", "policyTitle": "祖父政策"},
        "childPolicies": [{"id": "P2", "policyTitle": "孙政策"}],
    }
    with patch.object(ps.urllib.request, "urlopen") as mocked:
        mocked.side_effect = [
            _response(_list_payload([_summary()])),
            _response(_detail_payload(_detail(parentPolicy=deep_parent))),
        ]
        snapshot = ps.fetch_policies(force=True)

    parent = snapshot["data"][0]["relatedPolicies"]["parent"][0]
    assert parent["fusionPolicyId"] == "P1"
    assert parent["title"] == "父政策"
    assert "parentPolicy" not in parent
    assert "childPolicies" not in parent


def test_关联政策申报条件最多留5条(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", tmp_path / "snapshot.json")
    parent = {
        "id": "P1",
        "policyTitle": "父政策",
        "policyInfo": {"summary": "父摘要"},
        "conditions": [{"applicableTarget": f"条件{i}"} for i in range(20)],
    }
    with patch.object(ps.urllib.request, "urlopen") as mocked:
        mocked.side_effect = [
            _response(_list_payload([_summary()])),
            _response(_detail_payload(_detail(parentPolicy=parent))),
        ]
        snapshot = ps.fetch_policies(force=True)

    conditions = snapshot["data"][0]["relatedPolicies"]["parent"][0]["conditions"]
    assert len(conditions) == ps.RELATED_CONDITION_LIMIT


def test_关联政策五组都存在(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", tmp_path / "snapshot.json")
    with patch.object(ps.urllib.request, "urlopen") as mocked:
        mocked.side_effect = [
            _response(_list_payload([_summary()])),
            _response(_detail_payload(_detail())),
        ]
        snapshot = ps.fetch_policies(force=True)

    related = snapshot["data"][0]["relatedPolicies"]
    for group in ("parent", "directParents", "ancestors", "children", "descendants"):
        assert group in related


# --------------------------------------------------------------------------
# 启动同步不复用当天缓存
# --------------------------------------------------------------------------


def test_即使当天已有快照也重新请求(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot.json"
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", snapshot_path)
    snapshot_path.write_text(
        json.dumps({
            "schemaVersion": ps.LEGACY_SNAPSHOT_SCHEMA,
            "fetchedAt": "2026-08-06T09:00:00",
            "count": 1, "data": [{"fusionPolicyId": "OLD"}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    with patch.object(ps.urllib.request, "urlopen") as mocked:
        mocked.side_effect = [
            _response(_list_payload([_summary("NEW")])),
            _response(_detail_payload(_detail("NEW"))),
        ]
        snapshot = ps.fetch_policies(force=False)

    assert snapshot["data"][0]["fusionPolicyId"] == "NEW"
    assert mocked.call_count == 2


def test_列表过滤固定且不受旧环境变量覆盖(monkeypatch):
    calls = []

    def fake_get(path, params):
        calls.append((path, dict(params)))
        return _list_payload([], total=0)

    monkeypatch.setenv("V2_POLICY_SYNC_REVIEW_STATUS", "all")
    monkeypatch.setenv("V2_POLICY_SYNC_STRUCTURE_REVIEW_STATUS", "all")
    monkeypatch.setattr(ps, "_get_json", fake_get)

    summaries, total = ps._fetch_summaries(100)

    assert summaries == []
    assert total == 0
    assert calls[0][1]["reviewStatus"] == "1"
    assert calls[0][1]["structureReviewStatus"] == "1"
    assert calls[0][1]["requireSourcePolicyId"] == 1


# --------------------------------------------------------------------------
# 容错
# --------------------------------------------------------------------------


def test_单条详情失败拒绝切换不完整快照(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot.json"
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", snapshot_path)
    original = {
        "schemaVersion": ps.LEGACY_SNAPSHOT_SCHEMA,
        "fetchedAt": "2026-08-05T00:00:00",
        "count": 1,
        "data": [{"fusionPolicyId": "ORIGINAL"}],
    }
    snapshot_path.write_text(json.dumps(original), encoding="utf-8")
    with patch.object(ps.urllib.request, "urlopen") as mocked, \
         patch.object(ps.time, "sleep"):
        mocked.side_effect = [
            _response(_list_payload([_summary("A"), _summary("B")])),
            _response(_detail_payload(_detail("A"))),
            ps.urllib.error.URLError("boom"),
            ps.urllib.error.URLError("boom"),
            ps.urllib.error.URLError("boom"),
        ]
        with pytest.raises(ps.PolicySyncError) as exc:
            ps.fetch_policies(force=True)

    assert "拒绝切换不完整快照" in str(exc.value)
    assert exc.value.failures[0]["fusionPolicyId"] == "B"
    assert json.loads(snapshot_path.read_text(encoding="utf-8")) == original


def test_列表接口success为false时抛错(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", tmp_path / "snapshot.json")
    with patch.object(ps.urllib.request, "urlopen") as mocked, \
         patch.object(ps.time, "sleep"):
        mocked.return_value = _response({"success": False, "data": []})
        with pytest.raises(ps.PolicySyncError):
            ps.fetch_policies(force=True)


def test_列表data不是数组时抛错(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", tmp_path / "snapshot.json")
    with patch.object(ps.urllib.request, "urlopen") as mocked:
        mocked.return_value = _response({"success": True, "data": {"not": "a list"}})
        with pytest.raises(ps.PolicySyncError) as exc:
            ps.fetch_policies(force=True)

    assert "不是数组" in str(exc.value)


def test_网络错误会重试(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", tmp_path / "snapshot.json")
    with patch.object(ps.urllib.request, "urlopen") as mocked, \
         patch.object(ps.time, "sleep"):
        mocked.side_effect = [
            ps.urllib.error.URLError("temporary"),
            _response(_list_payload([_summary()])),
            _response(_detail_payload(_detail())),
        ]
        snapshot = ps.fetch_policies(force=True)

    assert snapshot["count"] == 1


def test_缺id的列表项拒绝生成快照(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", tmp_path / "snapshot.json")
    with patch.object(ps.urllib.request, "urlopen") as mocked:
        mocked.side_effect = [
            _response(_list_payload([{"policyTitle": "无 id"}, _summary("B")])),
            _response(_detail_payload(_detail("B"))),
        ]
        with pytest.raises(ps.PolicySyncError, match="缺少 id"):
            ps.fetch_policies(force=True)


def test_分页缺少total时拒绝生成快照(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", tmp_path / "snapshot.json")
    with patch.object(ps.urllib.request, "urlopen") as mocked:
        mocked.return_value = _response({"success": True, "data": []})
        with pytest.raises(ps.PolicySyncError, match="pagination.total"):
            ps.fetch_policies()


def test_分页提前结束时拒绝生成快照(monkeypatch):
    responses = [
        _list_payload([_summary("A")], total=2),
        _list_payload([], total=2),
    ]
    monkeypatch.setattr(ps, "_get_json", lambda path, params: responses.pop(0))

    with pytest.raises(ps.PolicySyncError, match="分页提前结束"):
        ps._fetch_summaries(1)


def test_详情审核状态变化时拒绝纳入快照(monkeypatch):
    monkeypatch.setattr(
        ps,
        "_get_json",
        lambda path, params: _detail_payload(_detail("A", structureReviewStatus="0")),
    )

    with pytest.raises(ps.PolicySyncError, match="结构审核状态不是 1"):
        ps._fetch_detail(_summary("A"))


# --------------------------------------------------------------------------
# 原子写
# --------------------------------------------------------------------------


def test_原子写失败时原文件不被破坏(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot.json"
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", snapshot_path)
    original = json.dumps({
        "schemaVersion": ps.LEGACY_SNAPSHOT_SCHEMA,
        "fetchedAt": "2020-01-01T00:00:00",
        "count": 1, "data": [{"fusionPolicyId": "ORIGINAL"}],
    }, ensure_ascii=False)
    snapshot_path.write_text(original, encoding="utf-8")

    with patch.object(ps.urllib.request, "urlopen") as mocked, \
         patch("service.paths.os.replace", side_effect=OSError("disk full")):
        mocked.side_effect = [
            _response(_list_payload([_summary()])),
            _response(_detail_payload(_detail())),
        ]
        with pytest.raises(OSError):
            ps.fetch_policies(force=True)

    assert json.loads(snapshot_path.read_text(encoding="utf-8"))["data"][0]["fusionPolicyId"] == "ORIGINAL"


def test_新快照切换前保存上一版本(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot.json"
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", snapshot_path)
    original = {
        "schemaVersion": ps.LEGACY_SNAPSHOT_SCHEMA,
        "fetchedAt": "2026-08-05T00:00:00",
        "count": 1,
        "data": [{"fusionPolicyId": "ORIGINAL"}],
    }
    snapshot_path.write_text(json.dumps(original), encoding="utf-8")

    with patch.object(ps.urllib.request, "urlopen") as mocked:
        mocked.side_effect = [
            _response(_list_payload([_summary("NEW")])),
            _response(_detail_payload(_detail("NEW"))),
        ]
        ps.fetch_policies()

    assert ps.load_previous_snapshot() == original
    assert ps.load_snapshot()["data"][0]["fusionPolicyId"] == "NEW"


def test_相同内容重复同步产生相同版本哈希(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", tmp_path / "snapshot.json")
    with patch.object(ps.urllib.request, "urlopen") as mocked:
        mocked.side_effect = [
            _response(_list_payload([_summary()])),
            _response(_detail_payload(_detail())),
            _response(_list_payload([_summary()])),
            _response(_detail_payload(_detail())),
        ]
        first = ps.fetch_policies()
        second = ps.fetch_policies()

    assert first["versionHash"] == second["versionHash"]
    assert first["data"][0]["policyContentHash"] == second["data"][0]["policyContentHash"]


def test_详情拉取受全局线程池上限控制(monkeypatch):
    active = 0
    peak = 0
    lock = ps.threading.Lock()

    def fake_fetch(summary):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        ps.time.sleep(0.02)
        with lock:
            active -= 1
        return {"fusionPolicyId": summary["id"]}

    monkeypatch.setenv("PEMATCHING_V2_POLICY_SYNC_WORKERS", "2")
    monkeypatch.setattr(ps, "_fetch_detail", fake_fetch)

    records = ps._fetch_details([_summary("A"), _summary("B"), _summary("C")])

    assert len(records) == 3
    assert peak == 2


# --------------------------------------------------------------------------
# load_snapshot / iter_policies
# --------------------------------------------------------------------------


def test_快照不存在时提示先跑同步脚本(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", tmp_path / "missing.json")

    with pytest.raises(FileNotFoundError) as exc:
        ps.load_snapshot()

    assert "run_sync_policies" in str(exc.value)


def test_快照schema不匹配时抛错(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot.json"
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", snapshot_path)
    snapshot_path.write_text(json.dumps({"schemaVersion": "wrong"}), encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        ps.load_snapshot()

    assert "schemaVersion" in str(exc.value)


def test_快照非法json时抛错(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot.json"
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", snapshot_path)
    snapshot_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(ValueError):
        ps.load_snapshot()


def test_启动同步失败且有旧快照时标记degraded(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot.json"
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", snapshot_path)
    snapshot_path.write_text(json.dumps({
        "schemaVersion": ps.LEGACY_SNAPSHOT_SCHEMA,
        "fetchedAt": "2026-08-05T00:00:00",
        "source": "http://old",
        "count": 1,
        "data": [{"fusionPolicyId": "OLD"}],
    }), encoding="utf-8")
    monkeypatch.setattr(
        ps,
        "fetch_policies",
        lambda: (_ for _ in ()).throw(ps.PolicySyncError("接口不可用")),
    )

    state = ps.synchronize_on_startup()

    assert state["status"] == "degraded"
    assert state["ready"] is True
    assert state["policyCount"] == 1
    assert "接口不可用" in state["degradedReason"]


def test_首次启动无快照且同步失败时不可用(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(
        ps,
        "fetch_policies",
        lambda: (_ for _ in ()).throw(ps.PolicySyncError("接口不可用")),
    )

    state = ps.synchronize_on_startup()

    assert state["status"] == "unavailable"
    assert state["ready"] is False
    assert state["policyCount"] == 0


def test_当前快照缺失时可从上一版本恢复并降级启动(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot.json"
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", snapshot_path)
    previous = {
        "schemaVersion": ps.LEGACY_SNAPSHOT_SCHEMA,
        "fetchedAt": "2026-08-05T00:00:00",
        "source": "http://old",
        "count": 1,
        "data": [{"fusionPolicyId": "ROLLBACK"}],
    }
    ps.previous_snapshot_path().write_text(json.dumps(previous), encoding="utf-8")
    monkeypatch.setattr(
        ps,
        "fetch_policies",
        lambda: (_ for _ in ()).throw(ps.PolicySyncError("接口不可用")),
    )

    state = ps.synchronize_on_startup()

    assert state["status"] == "degraded"
    assert state["ready"] is True
    assert ps.load_snapshot()["data"][0]["fusionPolicyId"] == "ROLLBACK"


def test_iter_policies流式遍历(tmp_path, monkeypatch):
    """返回迭代器而非 list，万级规模时可换分片实现，调用方不改。"""
    snapshot_path = tmp_path / "snapshot.json"
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", snapshot_path)
    snapshot_path.write_text(
        json.dumps({
            "schemaVersion": ps.LEGACY_SNAPSHOT_SCHEMA,
            "fetchedAt": "2026-08-03T00:00:00",
            "count": 2,
            "data": [{"fusionPolicyId": "A"}, {"fusionPolicyId": "B"}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    ids = [p["fusionPolicyId"] for p in ps.iter_policies()]

    assert ids == ["A", "B"]


def test_iter_policies跳过非字典元素(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot.json"
    monkeypatch.setattr(ps, "POLICY_SNAPSHOT_PATH", snapshot_path)
    snapshot_path.write_text(
        json.dumps({
            "schemaVersion": ps.LEGACY_SNAPSHOT_SCHEMA,
            "fetchedAt": "2026-08-03T00:00:00",
            "count": 2, "data": ["坏数据", {"fusionPolicyId": "B"}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    assert len(list(ps.iter_policies())) == 1


# --------------------------------------------------------------------------
# 增量指纹用的 updateTime
# --------------------------------------------------------------------------


def test_updateTime优先取updatedAt():
    record = ps._normalize(
        {"id": "X", "updatedAt": "2026-07-28 09:23:56"},
        {"id": "X", "reviewedAt": "2020-01-01 00:00:00"},
    )

    assert record["updateTime"] == "2026-07-28 09:23:56"


def test_updateTime回退到reviewedAt():
    record = ps._normalize({"id": "X"}, {"id": "X", "reviewedAt": "2026-07-24 16:25:57"})

    assert record["updateTime"] == "2026-07-24 16:25:57"


def test_updateTime回退到文档最新发布日期():
    record = ps._normalize(
        {"id": "X"},
        {"id": "X", "documents": [
            {"publishDate": "2025-01-01"}, {"publishDate": "2026-03-01"},
        ]},
    )

    assert record["updateTime"] == "2026-03-01"


def test_updateTime全缺时为空串():
    record = ps._normalize({"id": "X"}, {"id": "X"})

    assert record["updateTime"] == ""


# --------------------------------------------------------------------------
# snapshot_stats
# --------------------------------------------------------------------------


def test_统计输出各字段非空率():
    snapshot = {
        "schemaVersion": ps.SNAPSHOT_SCHEMA,
        "fetchedAt": "2026-08-03T00:00:00",
        "source": "http://x",
        "failures": [],
        "data": [
            {
                "title": "有标题",
                "policyInfo": {"summary": "有摘要", "industryField": "通用行业"},
                "supportMethods": [{}], "structuredConditions": [{}],
                "keywords": [{}], "materials": [{}],
                "updateTime": "2026-01-01",
                "documents": [{"policyAttribute": "申报通知"}],
                "relatedPolicies": {"parent": [{}], "children": []},
            },
            {
                "title": "只有标题", "policyInfo": {}, "supportMethods": [],
                "structuredConditions": [], "keywords": [], "materials": [],
                "updateTime": "", "documents": [], "relatedPolicies": {},
            },
        ],
    }

    stats = ps.snapshot_stats(snapshot)

    assert stats["count"] == 2
    assert stats["hasTitle"].startswith("2/2")
    assert stats["hasSummary"].startswith("1/2")
    assert stats["hasKeywords"].startswith("1/2")
    assert stats["policyAttributes"]["申报通知"] == 1


def test_统计对空快照不除零():
    stats = ps.snapshot_stats({"data": [], "fetchedAt": "", "source": "", "failures": []})

    assert stats["count"] == 0
    assert stats["hasTitle"] == "0/0"


# --------------------------------------------------------------------------
# 真实快照（若已同步）
# --------------------------------------------------------------------------


def test_真实快照可加载且字段完整():
    from service.paths import POLICY_SNAPSHOT_PATH

    if not POLICY_SNAPSHOT_PATH.exists():
        pytest.skip("尚未同步政策快照")

    snapshot = ps.load_snapshot()

    assert snapshot["count"] > 0
    for record in snapshot["data"][:20]:
        assert record["fusionPolicyId"]
        assert record["title"]
        assert "policyInfo" in record
        assert "relatedPolicies" in record


def test_真实快照体积远小于老目录缓存():
    """裁剪有效性：只保留打标与检索需要的字段。"""
    from service.paths import POLICY_SNAPSHOT_PATH

    if not POLICY_SNAPSHOT_PATH.exists():
        pytest.skip("尚未同步政策快照")

    size_mb = POLICY_SNAPSHOT_PATH.stat().st_size / 1024 / 1024
    assert size_mb < 3, f"快照 {size_mb:.1f}MB 偏大，检查裁剪逻辑"
