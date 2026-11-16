# -*- coding: utf-8 -*-
"""LLM 客户端测试。全部 mock，不发真实请求。"""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from service import llm_client as lc


def _response(payload: dict) -> MagicMock:
    handle = MagicMock()
    handle.read.return_value = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handle.__enter__ = lambda self: self
    handle.__exit__ = lambda *args: None
    return handle


def _message_payload(content=None, tool_calls=None) -> dict:
    message: dict = {}
    if content is not None:
        message["content"] = content
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def _http_error(code: int, body: str = "{}") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="http://x", code=code, msg="err", hdrs=None, fp=BytesIO(body.encode("utf-8"))
    )


# --------------------------------------------------------------------------
# chat
# --------------------------------------------------------------------------


def test_正常响应返回message():
    with patch.object(lc.urllib.request, "urlopen") as mocked:
        mocked.return_value = _response(_message_payload(content="结果"))
        message = lc.chat([{"role": "user", "content": "hi"}])

    assert message["content"] == "结果"


def test_tool_calls被正确返回():
    calls = [{"id": "c1", "type": "function",
              "function": {"name": "get_profile", "arguments": '{"a": 1}'}}]
    with patch.object(lc.urllib.request, "urlopen") as mocked:
        mocked.return_value = _response(_message_payload(tool_calls=calls))
        message = lc.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])

    assert message["tool_calls"][0]["function"]["name"] == "get_profile"


def test_tools为None时请求体不含tools字段():
    """传 tools: null 部分网关会报错。"""
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _response(_message_payload(content="ok"))

    with patch.object(lc.urllib.request, "urlopen", side_effect=fake_urlopen):
        lc.chat([{"role": "user", "content": "hi"}])

    assert "tools" not in captured["body"]
    assert "tool_choice" not in captured["body"]


def test_传tools时请求体含tool_choice():
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _response(_message_payload(content="ok"))

    with patch.object(lc.urllib.request, "urlopen", side_effect=fake_urlopen):
        lc.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])

    assert captured["body"]["tool_choice"] == "auto"


def test_响应无choices时抛LLMError():
    with patch.object(lc.urllib.request, "urlopen") as mocked:
        mocked.return_value = _response({"choices": []})
        with pytest.raises(lc.LLMError) as exc:
            lc.chat([{"role": "user", "content": "hi"}])

    assert "无 choices" in str(exc.value)


def test_响应不是合法json时抛LLMError():
    handle = MagicMock()
    handle.read.return_value = b"{not json"
    handle.__enter__ = lambda self: self
    handle.__exit__ = lambda *args: None

    with patch.object(lc.urllib.request, "urlopen", return_value=handle):
        with pytest.raises(lc.LLMError) as exc:
            lc.chat([{"role": "user", "content": "hi"}])

    assert "合法 JSON" in str(exc.value)


def test_http错误信息含状态码与响应体():
    with patch.object(lc.urllib.request, "urlopen", side_effect=_http_error(401, '{"error":"Unauthorized"}')):
        with pytest.raises(lc.LLMError) as exc:
            lc.chat([{"role": "user", "content": "hi"}])

    assert "401" in str(exc.value)
    assert "Unauthorized" in str(exc.value)


def test_网络不可达时抛LLMError():
    with patch.object(lc.urllib.request, "urlopen",
                      side_effect=urllib.error.URLError("connection refused")):
        with pytest.raises(lc.LLMError) as exc:
            lc.chat([{"role": "user", "content": "hi"}])

    assert "网络不可达" in str(exc.value)


def test_event_log记录调用明细():
    log = MagicMock()
    calls = [{"function": {"name": "f", "arguments": "{}"}}]
    with patch.object(lc.urllib.request, "urlopen") as mocked:
        mocked.return_value = _response(_message_payload(tool_calls=calls))
        lc.chat([{"role": "user", "content": "hi"}], event_log=log)

    log.write.assert_called_once()
    kind, detail = log.write.call_args[0]
    assert kind == "llm_call"
    assert detail["toolCalls"] == ["f"]
    assert detail["promptTokens"] == 10


# --------------------------------------------------------------------------
# chat_with_retry
# --------------------------------------------------------------------------


def test_5xx错误会重试():
    with patch.object(lc, "chat") as mocked, patch.object(lc.time, "sleep"):
        mocked.side_effect = [lc.LLMError("HTTP 500：boom"), {"content": "ok"}]
        message = lc.chat_with_retry([{"role": "user", "content": "hi"}], max_retries=2)

    assert mocked.call_count == 2
    assert message["content"] == "ok"


def test_4xx错误立即抛出不重试():
    """4xx 是请求本身有问题，重试无意义。"""
    with patch.object(lc, "chat") as mocked, patch.object(lc.time, "sleep"):
        mocked.side_effect = lc.LLMError("HTTP 400：bad request")
        with pytest.raises(lc.LLMError):
            lc.chat_with_retry([{"role": "user", "content": "hi"}], max_retries=3)

    assert mocked.call_count == 1


def test_重试耗尽后抛出最后一次错误():
    with patch.object(lc, "chat") as mocked, patch.object(lc.time, "sleep"):
        mocked.side_effect = lc.LLMError("HTTP 503：unavailable")
        with pytest.raises(lc.LLMError):
            lc.chat_with_retry([{"role": "user", "content": "hi"}], max_retries=2)

    assert mocked.call_count == 3


def test_退避时间指数增长():
    sleeps = []
    with patch.object(lc, "chat") as mocked, \
         patch.object(lc.time, "sleep", side_effect=sleeps.append):
        mocked.side_effect = [
            lc.LLMError("HTTP 500：x"),
            lc.LLMError("HTTP 500：x"),
            {"content": "ok"},
        ]
        lc.chat_with_retry([{"role": "user", "content": "hi"}], max_retries=2)

    assert len(sleeps) == 2
    assert sleeps[1] == sleeps[0] * 2


# --------------------------------------------------------------------------
# parse_tool_arguments
# --------------------------------------------------------------------------


def test_解析合法arguments():
    call = {"function": {"name": "f", "arguments": '{"keyword": "研发补助"}'}}
    assert lc.parse_tool_arguments(call) == {"keyword": "研发补助"}


def test_arguments是非法json时返回None():
    """返回 None 而非抛异常，由上层决定降级。"""
    call = {"function": {"name": "f", "arguments": "{broken"}}
    assert lc.parse_tool_arguments(call) is None


def test_arguments为空字符串时返回空字典():
    call = {"function": {"name": "f", "arguments": ""}}
    assert lc.parse_tool_arguments(call) == {}


def test_arguments已经是字典时直接返回():
    call = {"function": {"name": "f", "arguments": {"a": 1}}}
    assert lc.parse_tool_arguments(call) == {"a": 1}


def test_arguments是json数组时返回None():
    call = {"function": {"name": "f", "arguments": "[1, 2]"}}
    assert lc.parse_tool_arguments(call) is None


# --------------------------------------------------------------------------
# extract_json_content
# --------------------------------------------------------------------------


def test_提取纯json内容():
    assert lc.extract_json_content({"content": '{"a": 1}'}) == {"a": 1}


def test_提取被代码块包裹的json():
    content = '```json\n{"a": 1}\n```'
    assert lc.extract_json_content({"content": content}) == {"a": 1}


def test_提取思考模型带think前缀的json():
    content = '<think>让我想想</think>\n{"a": 1}'
    assert lc.extract_json_content({"content": content}) == {"a": 1}


def test_从混杂文本里截取json():
    content = '好的，结果如下：{"a": 1} 以上。'
    assert lc.extract_json_content({"content": content}) == {"a": 1}


def test_完全不含json时返回None():
    assert lc.extract_json_content({"content": "抱歉我无法完成"}) is None


def test_content为空时返回None():
    assert lc.extract_json_content({"content": ""}) is None
    assert lc.extract_json_content({"content": None}) is None
    assert lc.extract_json_content({}) is None
