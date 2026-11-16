"""通过单企业匹配 HTTP 接口批跑企业测试目录，并支持断点续跑。"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PROJECT_ROOT.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

STATE_FILE_NAME = "运行状态.json"
MANIFEST_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ApiResponse:
    """单次 HTTP 调用的完整响应，HTTP 错误也会保留响应体。"""

    status_code: int
    payload: Any
    raw_body: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="通过 /api/pematching/match/enterprise 批跑企业测试目录")
    parser.add_argument(
        "--input-dir",
        default="backend/PEMatching/企业测试目录",
        help="每个 JSON 对应一家企业的输入目录。",
    )
    parser.add_argument(
        "--file-name-pattern",
        default=r"^\d{4}_.*\.json$",
        help="纳入批跑的文件名正则，默认只匹配四位序号企业文件。",
    )
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("PEMATCHING_API_BASE_URL", "http://127.0.0.1:3000"),
        help="根目录公开 HTTP 服务地址，默认通过 3000 转发到 PEMatching。",
    )
    parser.add_argument(
        "--api-path",
        default="/api/pematching/match/enterprise",
        help="单企业匹配 API 路径。",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=float(os.getenv("PEMATCHING_BATCH_REQUEST_TIMEOUT", "600")),
        help="单家企业 HTTP 请求超时秒数，默认 600。",
    )
    parser.add_argument("--policy-id", action="append", default=[], help="API 的 policyId 查询参数，可重复传入。")
    parser.add_argument("--policy-keyword", default="", help="API 的 policyKeyword 查询参数。")
    parser.add_argument("--policy-limit", type=int, default=0, help="API 的 policyLimit 查询参数，0 表示不限制。")
    parser.add_argument(
        "--include-document-content",
        action="store_true",
        help="传递 includeDocumentContent=true，信息更全但每家企业请求更慢。",
    )
    parser.add_argument(
        "--run-dir",
        default="",
        help="本次运行归档目录；断点续跑时必须传入原目录。",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="从 --run-dir 的运行状态继续，跳过源文件未变化且已有完整 HTTP 响应的企业。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="本次最多处理多少家企业，0 表示不限制，适合先小批量验证。",
    )
    return parser.parse_args()


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    return path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for block in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(path)


def setup_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger(f"pematching-api-directory-runner-{log_path.parent.name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


def discover_enterprise_files(input_dir: Path, file_name_pattern: str) -> List[Path]:
    try:
        matcher = re.compile(file_name_pattern)
    except re.error as error:
        raise ValueError(f"文件名正则无效: {error}") from error
    return sorted((path for path in input_dir.glob("*.json") if matcher.search(path.name)), key=lambda path: path.name)


def build_api_url(args: argparse.Namespace) -> str:
    if args.policy_limit < 0:
        raise ValueError("--policy-limit 不能小于 0")
    base_url = args.api_base_url.rstrip("/")
    api_path = "/" + args.api_path.lstrip("/")
    query_items: List[tuple[str, str]] = [("policyId", policy_id.strip()) for policy_id in args.policy_id if policy_id.strip()]
    if args.policy_keyword.strip():
        query_items.append(("policyKeyword", args.policy_keyword.strip()))
    query_items.append(("policyLimit", str(args.policy_limit)))
    if args.include_document_content:
        query_items.append(("includeDocumentContent", "true"))
    return f"{base_url}{api_path}?{urlencode(query_items)}"


def load_enterprise_request(source_file: Path) -> Dict[str, Any]:
    with source_file.open("r", encoding="utf-8") as file_handle:
        payload = json.load(file_handle)
    if not isinstance(payload, dict):
        raise ValueError("企业文件必须是单个 JSON 对象，不能是数组或 RECORDS 聚合文件。")
    if isinstance(payload.get("enterprises"), list):
        raise ValueError("企业文件是 enterprises[] 聚合文件，请使用四位序号的单企业 JSON 文件。")
    enterprise = payload.get("enterprise") if isinstance(payload.get("enterprise"), dict) else payload
    enterprise_id = str(enterprise.get("id") or enterprise.get("enterpriseId") or "").strip()
    if not enterprise_id:
        raise ValueError("企业文件缺少必填字段 id 或 enterpriseId。")
    if not str(enterprise.get("enterpriseName", "")).strip():
        raise ValueError("企业文件缺少必填字段 enterpriseName。")
    return payload


def request_enterprise_match(api_url: str, enterprise_payload: Dict[str, Any], timeout: float) -> ApiResponse:
    request = Request(
        api_url,
        data=json.dumps(enterprise_payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = response.status
            raw_body = response.read().decode("utf-8", errors="replace")
    except HTTPError as error:
        status_code = error.code
        raw_body = error.read().decode("utf-8", errors="replace")
    except URLError as error:
        raise RuntimeError(f"调用 PEMatching 接口失败: {error.reason}") from error
    except TimeoutError as error:
        raise RuntimeError(f"调用 PEMatching 接口超时（{timeout} 秒）") from error

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        payload = {"_nonJsonResponse": raw_body}
    return ApiResponse(status_code=status_code, payload=payload, raw_body=raw_body)


def create_manifest(run_dir: Path, input_dir: Path, api_url: str, timeout: float, file_name_pattern: str) -> Dict[str, Any]:
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "runId": run_dir.name,
        "createdAt": now_text(),
        "updatedAt": now_text(),
        "configuration": {
            "inputDir": str(input_dir),
            "apiUrl": api_url,
            "requestTimeoutSeconds": timeout,
            "fileNamePattern": file_name_pattern,
        },
        "records": {},
    }


def load_or_create_manifest(
    run_dir: Path,
    input_dir: Path,
    api_url: str,
    timeout: float,
    file_name_pattern: str,
    resume: bool,
) -> Dict[str, Any]:
    state_path = run_dir / STATE_FILE_NAME
    if resume:
        if not state_path.exists():
            raise FileNotFoundError(f"未找到可续跑的状态文件: {state_path}")
        with state_path.open("r", encoding="utf-8") as file_handle:
            manifest = json.load(file_handle)
        if manifest.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
            raise ValueError(f"不支持的状态文件版本: {manifest.get('schemaVersion')}")
        configuration = manifest.get("configuration", {})
        if Path(str(configuration.get("inputDir", ""))).resolve() != input_dir:
            raise ValueError("续跑目录的输入目录与本次 --input-dir 不一致，拒绝混用状态。")
        if configuration.get("apiUrl") != api_url:
            raise ValueError("续跑目录的 API 地址或政策筛选参数与本次不一致，拒绝混用状态。")
        return manifest

    if state_path.exists():
        raise FileExistsError(f"运行目录已存在状态文件: {state_path}。如需继续请加 --resume。")
    return create_manifest(run_dir, input_dir, api_url, timeout, file_name_pattern)


def update_manifest(run_dir: Path, manifest: Dict[str, Any]) -> None:
    manifest["updatedAt"] = now_text()
    atomic_write_json(run_dir / STATE_FILE_NAME, manifest)


def output_paths(run_dir: Path, source_file: Path) -> tuple[Path, Path]:
    stem = source_file.stem
    return run_dir / "响应结果" / f"{stem}_响应.json", run_dir / "失败记录" / f"{stem}_失败.json"


def is_completed_unchanged(record: Dict[str, Any] | None, source_sha256: str) -> bool:
    if not record or record.get("status") != "completed" or record.get("sourceSha256") != source_sha256:
        return False
    response_path = Path(str(record.get("responseJson", "")))
    return response_path.is_file() and response_path.stat().st_size > 0


def response_run_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    meta = payload.get("meta")
    return str(meta.get("runId", "")) if isinstance(meta, dict) else ""


def process_file(
    source_file: Path,
    source_key: str,
    source_sha256: str,
    run_dir: Path,
    manifest: Dict[str, Any],
    api_url: str,
    timeout: float,
    logger: logging.Logger,
) -> str:
    response_json, failure_json = output_paths(run_dir, source_file)
    previous_record = manifest["records"].get(source_key, {})
    attempt_count = int(previous_record.get("attemptCount", 0)) + 1
    manifest["records"][source_key] = {
        "sourceFile": str(source_file),
        "sourceSha256": source_sha256,
        "status": "running",
        "attemptCount": attempt_count,
        "startedAt": now_text(),
        "responseJson": str(response_json),
    }
    update_manifest(run_dir, manifest)

    try:
        enterprise_payload = load_enterprise_request(source_file)
        enterprise = enterprise_payload.get("enterprise") if isinstance(enterprise_payload.get("enterprise"), dict) else enterprise_payload
        enterprise_name = str(enterprise.get("enterpriseName", ""))
        logger.info("开始请求 [%s]：%s", source_file.name, enterprise_name)
        api_response = request_enterprise_match(api_url, enterprise_payload, timeout)
        atomic_write_json(response_json, api_response.payload)
        succeeded = 200 <= api_response.status_code < 300 and isinstance(api_response.payload, dict) and api_response.payload.get("success") is True
        record = {
            "sourceFile": str(source_file),
            "sourceSha256": source_sha256,
            "enterpriseName": enterprise_name,
            "status": "completed" if succeeded else "failed",
            "attemptCount": attempt_count,
            "completedAt": now_text(),
            "responseJson": str(response_json),
            "httpStatus": api_response.status_code,
            "apiRunId": response_run_id(api_response.payload),
        }
        manifest["records"][source_key] = record
        if succeeded:
            update_manifest(run_dir, manifest)
            logger.info("企业完成 [%s]：HTTP %s，runId=%s", enterprise_name, api_response.status_code, record["apiRunId"] or "无")
            return "completed"

        failure = {
            "sourceFile": str(source_file),
            "failedAt": now_text(),
            "httpStatus": api_response.status_code,
            "responseJson": str(response_json),
            "message": api_response.payload.get("message", "接口未返回 success=true") if isinstance(api_response.payload, dict) else "接口响应不是 JSON 对象",
        }
        atomic_write_json(failure_json, failure)
        record["failureJson"] = str(failure_json)
        record["error"] = failure["message"]
        update_manifest(run_dir, manifest)
        logger.error("企业接口失败 [%s]：HTTP %s，响应已归档到 %s", enterprise_name, api_response.status_code, response_json)
        return "failed"
    except Exception as error:
        failure = {
            "sourceFile": str(source_file),
            "sourceSha256": source_sha256,
            "failedAt": now_text(),
            "errorType": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
        }
        atomic_write_json(failure_json, failure)
        manifest["records"][source_key] = {
            "sourceFile": str(source_file),
            "sourceSha256": source_sha256,
            "status": "failed",
            "attemptCount": attempt_count,
            "failedAt": now_text(),
            "responseJson": str(response_json),
            "failureJson": str(failure_json),
            "errorType": type(error).__name__,
            "error": str(error),
        }
        update_manifest(run_dir, manifest)
        logger.exception("企业请求异常 [%s]", source_file.name)
        return "failed"


def default_run_dir() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return PROJECT_ROOT / "output" / "企业测试目录接口批跑" / timestamp


def main() -> int:
    args = parse_args()
    if args.resume and not args.run_dir.strip():
        print("断点续跑必须同时传入 --run-dir。")
        return 2
    if args.limit < 0 or args.request_timeout <= 0:
        print("--limit 不能小于 0，--request-timeout 必须大于 0。")
        return 2

    input_dir = resolve_path(args.input_dir)
    if not input_dir.is_dir():
        print(f"企业输入目录不存在: {input_dir}")
        return 1
    run_dir = resolve_path(args.run_dir) if args.run_dir.strip() else default_run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(run_dir / "批跑日志.log")

    try:
        api_url = build_api_url(args)
        manifest = load_or_create_manifest(run_dir, input_dir, api_url, args.request_timeout, args.file_name_pattern, args.resume)
        update_manifest(run_dir, manifest)
        files = discover_enterprise_files(input_dir, args.file_name_pattern)
        if not files:
            raise ValueError(f"没有找到匹配文件名规则的企业 JSON: {input_dir}")
        if args.limit > 0:
            files = files[:args.limit]
        logger.info("批跑开始：企业文件=%s，接口=%s，运行目录=%s", len(files), api_url, run_dir)
    except Exception as error:
        logger.exception("批跑初始化失败：%s", error)
        return 1

    completed_count = 0
    failed_count = 0
    skipped_count = 0
    for index, source_file in enumerate(files, start=1):
        source_key = source_file.relative_to(input_dir).as_posix()
        source_sha256 = sha256_file(source_file)
        previous_record = manifest["records"].get(source_key)
        if args.resume and is_completed_unchanged(previous_record, source_sha256):
            skipped_count += 1
            logger.info("跳过已完成企业 [%s/%s]：%s", index, len(files), source_file.name)
            continue

        status = process_file(source_file, source_key, source_sha256, run_dir, manifest, api_url, args.request_timeout, logger)
        if status == "completed":
            completed_count += 1
        else:
            failed_count += 1

    logger.info("批跑结束：本次完成=%s，失败=%s，跳过=%s，状态文件=%s", completed_count, failed_count, skipped_count, run_dir / STATE_FILE_NAME)
    print(f"运行目录: {run_dir}")
    print(f"本次完成: {completed_count}，失败: {failed_count}，跳过: {skipped_count}")
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
