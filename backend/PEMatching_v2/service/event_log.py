# -*- coding: utf-8 -*-
"""按 requestId 落盘的审计日志。

Agent 每轮调了什么工具、参数是什么、返回什么，全部写进 logs/<requestId>.log。
出 badcase 时能定位到是规则错了、检索漏了还是模型选错了——这是 tool-calling
方案可审计性的基础（开发计划 7.3）。

日志是 JSON Lines，一行一事件，便于 grep 与程序读取。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from service.paths import LOGS_DIR, ensure_dir


def new_request_id() -> str:
    """短 ID，够用且便于人工引用。"""
    return uuid.uuid4().hex[:12]


class RequestEventLog:
    """单请求的事件日志。

    线程安全（批跑时多线程共用一个 log 实例的场景）。
    实例化时不建文件，第一次 write 才建——避免 deterministic 快路径产生空文件。
    """

    def __init__(self, request_id: Optional[str] = None, enabled: bool = True) -> None:
        self.request_id = request_id or new_request_id()
        self.enabled = enabled
        self.started_at = time.time()
        self._lock = threading.Lock()
        self._events: List[Dict[str, Any]] = []
        self._path: Optional[Path] = None

    @property
    def path(self) -> Path:
        if self._path is None:
            self._path = ensure_dir(LOGS_DIR) / f"{self.request_id}.log"
        return self._path

    def write(self, kind: str, detail: Any = None) -> None:
        """记一条事件。kind 是事件类型，detail 是任意可 JSON 序列化的内容。"""
        event = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "elapsedMs": int((time.time() - self.started_at) * 1000),
            "kind": kind,
            "detail": _safe(detail),
        }
        with self._lock:
            self._events.append(event)
            if not self.enabled:
                return
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            except OSError:
                # 日志写失败不该影响主流程
                pass

    def events(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def elapsed_ms(self) -> int:
        return int((time.time() - self.started_at) * 1000)

    def summary(self) -> Dict[str, Any]:
        """事件类型计数，供响应 meta 用。"""
        counts: Dict[str, int] = {}
        for event in self.events():
            counts[event["kind"]] = counts.get(event["kind"], 0) + 1
        return {
            "requestId": self.request_id,
            "elapsedMs": self.elapsed_ms(),
            "eventCounts": counts,
            "logPath": str(self._path) if self._path else None,
        }


def _safe(value: Any, depth: int = 0) -> Any:
    """裁剪成可序列化且体积可控的结构。

    日志要能读，不能因为塞进整个 profile 就变成几 MB。
    """
    if depth > 6:
        return "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 1500 else value[:1500] + f"…(共{len(value)}字符)"
    if isinstance(value, dict):
        return {str(k): _safe(v, depth + 1) for k, v in list(value.items())[:60]}
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        trimmed = [_safe(v, depth + 1) for v in items[:60]]
        if len(items) > 60:
            trimmed.append(f"…(共{len(items)}项)")
        return trimmed
    return str(value)[:500]


class NullEventLog:
    """不落盘的空实现。测试与不需要审计的场景用。"""

    def __init__(self, request_id: Optional[str] = None) -> None:
        self.request_id = request_id or new_request_id()
        self.started_at = time.time()

    def write(self, kind: str, detail: Any = None) -> None:  # noqa: D401
        return None

    def events(self) -> List[Dict[str, Any]]:
        return []

    def elapsed_ms(self) -> int:
        return int((time.time() - self.started_at) * 1000)

    def summary(self) -> Dict[str, Any]:
        return {"requestId": self.request_id, "elapsedMs": self.elapsed_ms(), "eventCounts": {}}
