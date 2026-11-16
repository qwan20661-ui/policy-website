from __future__ import annotations

import os
import sys
import json
from pathlib import Path
from threading import Lock
from typing import Any
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel, Field
    from starlette.concurrency import run_in_threadpool
except ModuleNotFoundError:
    FastAPI = None
    HTTPException = None
    BaseModel = object
    Field = None
    run_in_threadpool = None

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from extractdata import run_extraction


_extraction_lock = Lock()


def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象")

    cleaned: dict[str, Any] = {}
    for key in ("env_file", "file", "output_dir"):
        value = payload.get(key)
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} 必须是非空字符串")
            cleaned[key] = value

    input_dirs = payload.get("input_dirs")
    if input_dirs is not None:
        if not isinstance(input_dirs, list) or not input_dirs:
            raise ValueError("input_dirs 必须是非空字符串数组")
        if not all(isinstance(item, str) and item.strip() for item in input_dirs):
            raise ValueError("input_dirs 只能包含非空字符串")
        cleaned["input_dirs"] = input_dirs

    for key in ("batch_size", "max_workers"):
        value = payload.get(key)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{key} 必须是大于等于 1 的整数")
            cleaned[key] = value

    for key in ("import_to_db", "db_dry_run"):
        value = payload.get(key)
        if value is not None:
            if not isinstance(value, bool):
                raise ValueError(f"{key} 必须是布尔值")
            cleaned[key] = value

    return cleaned


def _run_extraction_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = _validate_payload(payload)
    # extractdata.py 内部有若干全局后处理缓存，接口层串行执行避免并发串数据。
    with _extraction_lock:
        return run_extraction(
            env_file=cleaned.get("env_file"),
            batch_size=cleaned.get("batch_size"),
            max_workers=cleaned.get("max_workers"),
            file=cleaned.get("file"),
            input_dirs=cleaned.get("input_dirs"),
            output_dir=cleaned.get("output_dir"),
            import_to_db=cleaned.get("import_to_db"),
            db_dry_run=cleaned.get("db_dry_run"),
        )


def _error_status(exc: Exception) -> int:
    if isinstance(exc, SystemExit):
        return 400
    if isinstance(exc, FileNotFoundError):
        return 404
    if isinstance(exc, ValueError):
        return 400
    return 500


def _error_detail(exc: Exception) -> str:
    if isinstance(exc, SystemExit):
        return str(exc) or "抽取任务已停止"
    if isinstance(exc, (FileNotFoundError, ValueError)):
        return str(exc)
    return f"抽取失败: {exc}"


if FastAPI is not None:
    app = FastAPI(
        title="Policy Metadata Extraction API",
        version="1.0.0",
        description="政策文件结构化抽取接口",
    )


    class ExtractRequest(BaseModel):
        env_file: str | None = Field(default=None, description="配置文件路径，默认使用 metadata/.env")
        file: str | None = Field(default=None, description="单个政策文件路径")
        input_dirs: list[str] | None = Field(default=None, description="批量处理目录列表；不传则使用配置文件")
        output_dir: str | None = Field(default=None, description="输出目录；不传则使用配置文件")
        batch_size: int | None = Field(default=None, ge=1, description="每批处理数量")
        max_workers: int | None = Field(default=None, ge=1, description="并发线程数")
        import_to_db: bool | None = Field(default=None, description="是否写入数据库；不传则使用配置文件")
        db_dry_run: bool | None = Field(default=None, description="是否走入库流程但回滚事务；不传则使用配置文件")


    class ExtractResponse(BaseModel):
        status: str
        result: dict[str, Any]


    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}


    @app.post("/extract", response_model=ExtractResponse)
    async def extract_policies(request: ExtractRequest) -> ExtractResponse:
        try:
            payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
            result = await run_in_threadpool(lambda: _run_extraction_payload(payload))
        except Exception as exc:
            raise HTTPException(status_code=_error_status(exc), detail=_error_detail(exc)) from exc

        return ExtractResponse(status="success", result=result)
else:
    app = None


class StdlibApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        self._send_json(404, {"detail": "接口不存在"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/extract":
            self._send_json(404, {"detail": "接口不存在"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw_body.decode("utf-8"))
            result = _run_extraction_payload(payload)
            self._send_json(200, {"status": "success", "result": result})
        except json.JSONDecodeError:
            self._send_json(400, {"detail": "请求体不是合法 JSON"})
        except Exception as exc:
            self._send_json(_error_status(exc), {"detail": _error_detail(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_stdlib_server() -> None:
    host = os.getenv("METADATA_API_HOST", "0.0.0.0")
    port = int(os.getenv("METADATA_API_PORT", "8000"))
    server = ThreadingHTTPServer((host, port), StdlibApiHandler)
    print(f"FastAPI 未安装，使用标准库 HTTP 服务: http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    if app is None:
        run_stdlib_server()
    else:
        import uvicorn

        uvicorn.run(
            app,
            host=os.getenv("METADATA_API_HOST", "0.0.0.0"),
            port=int(os.getenv("METADATA_API_PORT", "8000")),
            reload=False,
        )
