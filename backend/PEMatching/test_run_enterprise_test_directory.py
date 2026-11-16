"""企业测试目录接口批跑器的断点状态回归测试。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from run_enterprise_test_directory import (
    STATE_FILE_NAME,
    atomic_write_json,
    build_api_url,
    discover_enterprise_files,
    is_completed_unchanged,
    load_or_create_manifest,
    output_paths,
    request_enterprise_match,
    load_enterprise_request,
    sha256_file,
)


class _Args:
    api_base_url = "http://127.0.0.1:3000"
    api_path = "/api/pematching/match/enterprise"
    policy_id = ["6617", "6652"]
    policy_keyword = "创新中心"
    policy_limit = 2
    include_document_content = True


def test_discover_enterprise_files_excludes_aggregate_json(tmp_path) -> None:
    (tmp_path / "0002_企业B.json").write_text("{}", encoding="utf-8")
    (tmp_path / "0001_企业A.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tech_sme_enterprises.json").write_text("[]", encoding="utf-8")

    files = discover_enterprise_files(tmp_path, r"^\d{4}_.*\.json$")

    assert [path.name for path in files] == ["0001_企业A.json", "0002_企业B.json"]


def test_api_url_keeps_policy_filters() -> None:
    api_url = build_api_url(_Args())

    assert api_url == (
        "http://127.0.0.1:3000/api/pematching/match/enterprise?"
        "policyId=6617&policyId=6652&policyKeyword=%E5%88%9B%E6%96%B0%E4%B8%AD%E5%BF%83&"
        "policyLimit=2&includeDocumentContent=true"
    )


def test_request_enterprise_match_posts_original_enterprise_json() -> None:
    http_response = MagicMock()
    http_response.status = 200
    http_response.read.return_value = '{"success": true, "meta": {"runId": "run-1"}}'.encode("utf-8")
    urlopen_context = MagicMock()
    urlopen_context.__enter__.return_value = http_response

    with patch("run_enterprise_test_directory.urlopen", return_value=urlopen_context) as mocked_urlopen:
        response = request_enterprise_match(
            "http://127.0.0.1:3000/api/pematching/match/enterprise?policyLimit=0",
            {"id": "1001", "enterpriseName": "企业A", "creditCode": "913502000000000001"},
            timeout=600,
        )

    request = mocked_urlopen.call_args.args[0]
    assert request.get_method() == "POST"
    assert json.loads(request.data.decode("utf-8"))["enterpriseName"] == "企业A"
    assert mocked_urlopen.call_args.kwargs["timeout"] == 600
    assert response.status_code == 200
    assert response.payload["meta"]["runId"] == "run-1"


def test_load_enterprise_request_rejects_aggregate_json(tmp_path) -> None:
    aggregate_file = tmp_path / "tech_sme_enterprises.json"
    aggregate_file.write_text(
        json.dumps({"totalCount": 1, "enterprises": [{"id": "1001", "enterpriseName": "企业A"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    try:
        load_enterprise_request(aggregate_file)
        raise AssertionError("聚合企业 JSON 不应作为单企业请求提交")
    except ValueError as exc:
        assert "enterprises[] 聚合文件" in str(exc)


def test_load_enterprise_request_requires_enterprise_id(tmp_path) -> None:
    source_file = tmp_path / "0001_企业A.json"
    source_file.write_text(json.dumps({"enterpriseName": "企业A"}, ensure_ascii=False), encoding="utf-8")

    try:
        load_enterprise_request(source_file)
        raise AssertionError("缺少企业 ID 时应在本地校验失败")
    except ValueError as exc:
        assert "id 或 enterpriseId" in str(exc)


def test_completed_record_requires_unchanged_source_and_response(tmp_path) -> None:
    source_file = tmp_path / "0001_企业A.json"
    source_file.write_text(json.dumps({"enterpriseName": "企业A"}, ensure_ascii=False), encoding="utf-8")
    response_file = tmp_path / "响应.json"
    response_file.write_text("{}", encoding="utf-8")
    source_hash = sha256_file(source_file)
    record = {"status": "completed", "sourceSha256": source_hash, "responseJson": str(response_file)}

    assert is_completed_unchanged(record, source_hash) is True
    source_file.write_text(json.dumps({"enterpriseName": "企业A-已更新"}, ensure_ascii=False), encoding="utf-8")
    assert is_completed_unchanged(record, sha256_file(source_file)) is False
    response_file.unlink()
    assert is_completed_unchanged(record, source_hash) is False


def test_manifest_can_be_loaded_for_resume(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    api_url = "http://127.0.0.1:8010/api/pematching/match/enterprise?policyLimit=0"

    manifest = load_or_create_manifest(run_dir, input_dir, api_url, 600, r"^\d{4}_.*\.json$", resume=False)
    atomic_write_json(run_dir / STATE_FILE_NAME, manifest)
    resumed = load_or_create_manifest(run_dir, input_dir, api_url, 600, r"^\d{4}_.*\.json$", resume=True)

    assert resumed["runId"] == "run"


def test_output_paths_keep_one_response_per_source_file(tmp_path) -> None:
    response_json, failure_json = output_paths(tmp_path, tmp_path / "0001_企业A.json")

    assert response_json.name == "0001_企业A_响应.json"
    assert failure_json.name == "0001_企业A_失败.json"
