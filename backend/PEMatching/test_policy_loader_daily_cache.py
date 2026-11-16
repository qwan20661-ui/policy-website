"""主政策申报条件每日快照缓存回归测试。"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime

from service import policy_loader


def test_load_structure_policies_reuses_today_cache(monkeypatch, tmp_path) -> None:
    cached_policies = [
        {
            "id": "6617",
            "title": "缓存政策",
            "structured_conditions": [{"条件名称": "企业资质"}],
        }
    ]
    cache_path = tmp_path / "主政策申报条件.json"
    cache_path.write_text(
        json.dumps({"source": {"exportedAt": datetime.now().isoformat()}, "data": cached_policies}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(policy_loader, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(policy_loader, "DAILY_POLICY_CACHE_ENABLED", True)
    monkeypatch.setattr(
        policy_loader,
        "_load_structure_policies_from_api",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("不应调用 API")),
    )

    policies = policy_loader.load_structure_policies(max_policies=None)

    assert policies == cached_policies


def test_load_structure_policies_creates_today_cache_from_full_api(monkeypatch, tmp_path) -> None:
    api_policies = [{"id": "6617", "title": "API 政策", "structured_conditions": []}]
    monkeypatch.setattr(policy_loader, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(policy_loader, "DAILY_POLICY_CACHE_ENABLED", True)
    captured = {}

    def fake_load_from_api(**kwargs):
        captured.update(kwargs)
        return api_policies

    monkeypatch.setattr(policy_loader, "_load_structure_policies_from_api", fake_load_from_api)

    policies = policy_loader.load_structure_policies(max_policies=1)

    assert policies == api_policies
    assert captured["max_policies"] is None
    cache_path = tmp_path / "主政策申报条件.json"
    assert json.loads(cache_path.read_text(encoding="utf-8"))["data"] == api_policies


def test_sparse_policy_document_excerpt_is_bounded_and_does_not_mutate_input(monkeypatch) -> None:
    policies = [
        {
            "id": "rich",
            "policy_info": {"summary": "已有摘要", "industryField": "软件"},
            "documents": [{"id": "rich-doc", "policyAttribute": "主政策"}],
        },
        {
            "id": "sparse",
            "policy_info": {},
            "documents": [
                {"id": "attachment", "policyAttribute": "附件"},
                {"id": "notice", "policyAttribute": "申报通知"},
                {"id": "rules", "policyAttribute": "实施细则"},
            ],
        },
    ]
    original = deepcopy(policies)
    requested_ids = []

    class FakeClient:
        def __init__(self, base_url):
            assert base_url == "http://structure.test"

        def fetch_document_content(self, document_id):
            requested_ids.append(document_id)
            return {"content": "<p>政策&nbsp;正文</p>\n" + ("甲" * 4000)}

    monkeypatch.setattr(policy_loader, "StructurePolicyClient", FakeClient)

    enriched = policy_loader._attach_sparse_policy_document_excerpts(
        policies,
        base_url="http://structure.test",
        max_workers=2,
        progress_callback=None,
    )

    assert requested_ids == ["notice"]
    assert "association_document_excerpt" not in enriched[0]
    assert enriched[1]["association_document_excerpt"].startswith("政策 正文")
    assert "<p>" not in enriched[1]["association_document_excerpt"]
    assert "&nbsp;" not in enriched[1]["association_document_excerpt"]
    assert len(enriched[1]["association_document_excerpt"]) == 3000
    assert policies == original


def test_disabled_sparse_document_path_skips_fetch(monkeypatch) -> None:
    policies = [{
        "id": "sparse",
        "policy_info": {},
        "documents": [{"id": "notice", "policyAttribute": "申报通知"}],
    }]
    helper_calls = []

    monkeypatch.setattr(policy_loader, "_load_daily_policy_cache", lambda: policies)
    monkeypatch.setattr(
        policy_loader,
        "_attach_sparse_policy_document_excerpts",
        lambda *args, **kwargs: helper_calls.append((args, kwargs)) or args[0],
    )

    assert policy_loader.load_structure_policies(include_sparse_document_content=False) == policies
    assert helper_calls == []


def test_sparse_policy_document_failure_keeps_policy(monkeypatch) -> None:
    policies = [{
        "id": "sparse",
        "policy_info": {},
        "documents": [{"id": "notice", "policyAttribute": "申报通知"}],
    }]

    class FailingClient:
        def __init__(self, base_url):
            pass

        def fetch_document_content(self, document_id):
            raise RuntimeError("正文接口暂不可用")

    monkeypatch.setattr(policy_loader, "StructurePolicyClient", FailingClient)
    enriched = policy_loader._attach_sparse_policy_document_excerpts(
        policies,
        base_url="http://structure.test",
        max_workers=1,
        progress_callback=None,
    )
    assert enriched == policies
    assert enriched is not policies
