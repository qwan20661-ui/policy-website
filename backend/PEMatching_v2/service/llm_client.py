# -*- coding: utf-8 -*-
"""LLM 客户端：标准库实现，支持 tool-calling 与 JSON 模式。

网关现状（2026-08-03 实测）：只有 minimax2.7 支持原生 tool-calling。
qwen3-8b / qwen3.6-35b 所在的 vLLM 未开 --enable-auto-tool-choice，
传 tools 参数会返回 400："auto" tool choice requires --enable-auto-tool-choice。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from service.paths import env_float, env_int, env_str


class LLMError(RuntimeError):
    """LLM 调用失败。"""


def _endpoint() -> str:
    url = env_str("V2_API_URL")
    if not url:
        raise LLMError("未配置 V2_API_URL，请检查 backend/PEMatching_v2/.env")
    return url


def _headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = env_str("V2_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def chat(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: str = "auto",
    response_format: Optional[Dict[str, Any]] = None,
    temperature: float = 0.0,
    timeout: Optional[int] = None,
    model: Optional[str] = None,
    event_log: Any = None,
) -> Dict[str, Any]:
    """单次调用，返回 choices[0].message 原始 dict（含 content 与 tool_calls）。"""
    body: Dict[str, Any] = {
        "model": model or env_str("V2_MODEL_NAME", "minimax2.7"),
        "messages": messages,
        "temperature": temperature,
    }
    # tools 为 None 时不能传该字段，部分网关会因 tools: null 报错
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice
    if response_format:
        body["response_format"] = response_format

    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(_endpoint(), data=payload, headers=_headers())
    effective_timeout = timeout if timeout is not None else env_int("V2_LLM_TIMEOUT", 180)

    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=effective_timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:500]
        raise LLMError(f"HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        # URLError.reason 可能是 socket.timeout，此时按超时归类便于上层重试
        if isinstance(exc.reason, (TimeoutError, OSError)) and "timed out" in str(exc.reason):
            raise LLMError(f"请求超时（{effective_timeout} 秒）") from exc
        raise LLMError(f"网络不可达：{exc.reason}") from exc
    except TimeoutError as exc:
        # socket 读超时直接抛 TimeoutError（OSError 子类），不经过 URLError
        raise LLMError(f"请求超时（{effective_timeout} 秒）") from exc
    except json.JSONDecodeError as exc:
        raise LLMError(f"响应不是合法 JSON：{exc}") from exc
    except OSError as exc:
        raise LLMError(f"网络错误：{type(exc).__name__}: {exc}") from exc

    elapsed_ms = int((time.time() - started) * 1000)
    choices = raw.get("choices") or []
    if not choices:
        raise LLMError(f"响应无 choices：{json.dumps(raw, ensure_ascii=False)[:300]}")
    message = choices[0].get("message") or {}

    if event_log is not None:
        usage = raw.get("usage") or {}
        event_log.write(
            "llm_call",
            {
                "model": body["model"],
                "elapsedMs": elapsed_ms,
                "promptTokens": usage.get("prompt_tokens"),
                "completionTokens": usage.get("completion_tokens"),
                "toolCalls": [
                    (c.get("function") or {}).get("name")
                    for c in (message.get("tool_calls") or [])
                ],
                "hasContent": bool(message.get("content")),
            },
        )
    return message


def chat_with_retry(
    messages: List[Dict[str, Any]],
    max_retries: Optional[int] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """带指数退避的重试。

    只重试网络错误与 5xx——4xx 是请求本身有问题，重试无意义。
    """
    retries = max_retries if max_retries is not None else env_int("V2_LLM_RETRY", 2)
    backoff = env_float("V2_LLM_BACKOFF", 2.0)
    last_error: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            return chat(messages, **kwargs)
        except LLMError as exc:
            message = str(exc)
            is_client_error = message.startswith("HTTP 4")
            if is_client_error or attempt >= retries:
                raise
            last_error = exc
            time.sleep(backoff * (2 ** attempt))

    raise LLMError(f"重试 {retries} 次后仍失败：{last_error}")


def parse_tool_arguments(tool_call: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """解析 tool_call 的 arguments（模型返回的是 JSON 字符串）。

    非法 JSON 返回 None，由调用方决定降级，不抛异常。
    """
    raw = ((tool_call or {}).get("function") or {}).get("arguments")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def extract_json_content(message: Dict[str, Any]) -> Optional[Any]:
    """从 message.content 里取 JSON。

    容忍模型用 ```json 代码块包裹，以及思考模型的 <think> 前缀。
    非法 JSON 返回 None。
    """
    content = (message or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    text = content.strip()

    if "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 退而求其次：截取第一个 { 到最后一个 } 之间的内容
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None
