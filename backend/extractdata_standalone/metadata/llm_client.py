from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

from config import MetadataSettings
from prompt_loader import MarkdownPrompt, RuleOptions


def escape_unmatched_braces(text: str) -> str:
    """转义字符串中未匹配的花括号，防止破坏 str.format()"""
    result = []
    i = 0
    while i < len(text):
        # 跳过已转义的双花括号
        if text[i:i+2] in ['{{', '}}']:
            result.append(text[i:i+2])
            i += 2
            continue
        # 转义未配对的单花括号
        if text[i] == '{':
            result.append('{{')
            i += 1
        elif text[i] == '}':
            result.append('}}')
            i += 1
        else:
            result.append(text[i])
            i += 1
    return ''.join(result)


class MetadataLLMClient:
    def __init__(self, settings: MetadataSettings, prompt: MarkdownPrompt, rule_options: RuleOptions | None = None) -> None:
        self.settings = settings
        self.prompt = prompt
        self.rule_options = rule_options
        # 加载专用于有效期提取的提示词
        self.validity_prompt = self._load_validity_prompt()

    def _load_validity_prompt(self) -> dict:
        """加载有效期提取提示词"""
        validity_prompt_file = Path(__file__).parent / "prompts" / "extract_validity.md"
        if validity_prompt_file.exists():
            content = validity_prompt_file.read_text(encoding="utf-8")
            # 提取 system 和 user 部分
            # 使用更简单直接的正则表达式
            system_match = re.search(r"## system\s*\n([\s\S]*?)\n## user", content)
            user_match = re.search(r"## user\s*\n([\s\S]*?)$", content)
            system = system_match.group(1).strip() if system_match else ""
            user_template = user_match.group(1).strip() if user_match else ""
            return {"system": system, "user_template": user_template}
        return {"system": "", "user_template": ""}

    def extract(self, policy_text: str, source_name: str, source_path: str, attachment_list: str = "无", policy_validity_text: str = "", pre_extracted_validity: str = "", policy_amount_text: str = "") -> dict:
        # 构建格式化参数
        format_kwargs = {
            "source_name": source_name,
            "source_path": source_path,
            "attachment_list": attachment_list,
            "policy_text": policy_text,
            "policy_validity_text": policy_validity_text,
            "pre_extracted_validity": pre_extracted_validity,
            "policy_amount_text": policy_amount_text,
        }

        # 如果有规则选项，则注入到模板中
        if self.rule_options:
            format_kwargs["scope_options"] = getattr(self.rule_options, 'scope_options', '')
            format_kwargs["applicant_options"] = getattr(self.rule_options, 'applicant_options', '')
            format_kwargs["support_options"] = getattr(self.rule_options, 'support_options', '')
            format_kwargs["usage_options"] = getattr(self.rule_options, 'usage_options', '')
            format_kwargs["industry_options"] = getattr(self.rule_options, 'industry_options', '')
            format_kwargs["keyword_options"] = getattr(self.rule_options, 'keyword_options', '')
            format_kwargs["profile_category_options"] = getattr(self.rule_options, 'profile_category_options', '')
        else:
            # 数据库未连接时，使用空字符串
            format_kwargs["scope_options"] = ""
            format_kwargs["applicant_options"] = ""
            format_kwargs["support_options"] = ""
            format_kwargs["usage_options"] = ""
            format_kwargs["industry_options"] = ""
            format_kwargs["keyword_options"] = ""
            format_kwargs["profile_category_options"] = ""

        user_prompt = self.prompt.user_template.format(**format_kwargs)
        payload = {
            "model": self.settings.model_name,
            "messages": [
                {"role": "system", "content": self.prompt.system},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.settings.temperature,
            "max_tokens": 16384,
            "chat_template_kwargs": {"enable_thinking": False},
        }

        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        last_error: Exception | None = None

        for attempt in range(1, self.settings.max_retries + 2):
            try:
                print(f"第 {attempt} 次请求模型: {source_name}")
                response = requests.post(
                    self.settings.api_url,
                    headers=headers,
                    json=payload,
                    timeout=(self.settings.connect_timeout, self.settings.request_timeout),
                )
                print(f"模型响应状态码: {response.status_code} -> {source_name}")
                response.raise_for_status()

                result = response.json()
                content = result["choices"][0]["message"]["content"]
                return extract_json_from_text(content)

            except Exception as exc:
                last_error = exc
                print(f"第 {attempt} 次请求失败: {source_name} -> {exc}")
                time.sleep(3)

        if last_error is None:
            raise RuntimeError("模型调用失败，但未捕获到具体异常")
        raise last_error

    def extract_validity(self, guide_text: str, source_name: str) -> str:
        """
        专门从指导政策文本中提取政策有效期

        Args:
            guide_text: 指导政策文件夹中的文本内容
            source_name: 政策名称

        Returns:
            提取到的政策有效期字符串，如 "2026.03.05-2029.03.04" 或 "长期"
        """
        if not self.validity_prompt["system"]:
            print(f"  [警告] 有效期提示词未加载，返回空")
            return ""

        user_template = self.validity_prompt["user_template"]
        user_prompt = user_template.format(policy_validity_text=guide_text)

        payload = {
            "model": self.settings.model_name,
            "messages": [
                {"role": "system", "content": self.validity_prompt["system"]},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,  # 有效期提取使用确定性输出
            "max_tokens": 1024,
            "chat_template_kwargs": {"enable_thinking": False},
        }

        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"

        for attempt in range(1, self.settings.max_retries + 2):
            try:
                print(f"  [有效期] 第 {attempt} 次请求: {source_name}")
                response = requests.post(
                    self.settings.api_url,
                    headers=headers,
                    json=payload,
                    timeout=(self.settings.connect_timeout, self.settings.request_timeout),
                )
                response.raise_for_status()
                result = response.json()
                content = result["choices"][0]["message"]["content"]

                # 解析返回的JSON
                data = extract_json_from_text(content)
                validity = data.get("起止日期", "")
                return validity if validity else ""

            except Exception as exc:
                print(f"  [有效期] 第 {attempt} 次失败: {exc}")
                time.sleep(2)

        return ""


def extract_json_from_text(text: str) -> dict:
    cleaned = text.strip()

    # 1. 优先匹配 ```json 代码块
    code_block_match = re.search(r"```json\s*(.*?)\s*```", cleaned, re.S)
    if code_block_match:
        cleaned = code_block_match.group(1).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

    # 2. 尝试找最后一个 { 到最后一个 } 的完整 JSON
    last_brace_pos = cleaned.rfind("}")
    if last_brace_pos > 0:
        # 从后往前找对应的 {
        search_text = cleaned[:last_brace_pos + 1]
        first_brace_pos = search_text.rfind("{")
        if first_brace_pos >= 0:
            json_candidate = search_text[first_brace_pos:]
            try:
                return json.loads(json_candidate)
            except json.JSONDecodeError:
                pass

    # 3. 尝试匹配任何 {...} 结构
    brace_match = re.search(r"\{[\s\S]*\}", cleaned)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    # 4. 尝试修复被截断的 JSON（从后往前补全）
    if "{" in cleaned:
        fixed = try_fix_truncated_json(cleaned)
        if fixed and len(fixed) >= 5:  # 至少要有5个字段才接受
            return fixed

    raise ValueError("未能从模型返回内容中解析出合法 JSON")


def try_fix_truncated_json(text: str) -> dict | None:
    """尝试修复被截断的 JSON 字符串"""
    # 找到最后一个 {
    last_open_brace = text.rfind("{")
    if last_open_brace < 0:
        return None

    original = text[last_open_brace:]
    for i in range(len(original), -1, -1):
        test_text = original[:i] + "}"
        try:
            result = json.loads(test_text)
            print(f"  [修复] 原始截断长度 {len(original)} -> 修复后 {i+1}")
            return result
        except json.JSONDecodeError:
            continue
    return None
