#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
政策文件融合脚本 - 使用LLM进行智能融合
读取 .env 配置，调用模型进行政策文件融合
"""

from __future__ import annotations

import os
import re
import time
import requests
from pathlib import Path
from dataclasses import dataclass

try:
    from dotenv import dotenv_values
except ImportError:
    dotenv_values = None


# ==================== 配置加载 ====================

METADATA_DIR = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = METADATA_DIR / ".env"


@dataclass
class MergeSettings:
    api_url: str
    api_key: str | None
    model_name: str
    request_timeout: float
    connect_timeout: float
    max_retries: int
    temperature: float
    max_text_length: int


def load_settings(env_file: Path | None = None) -> MergeSettings:
    """加载配置"""
    env_path = env_file or DEFAULT_ENV_FILE
    file_values = _read_env_file(env_path)

    def get_value(name: str, default: str) -> str:
        return os.getenv(name, file_values.get(name, default))

    return MergeSettings(
        api_url=get_value("METADATA_API_URL", "http://127.0.0.1:8000/v1/chat/completions"),
        api_key=get_value("METADATA_API_KEY", "") or None,
        model_name=get_value("METADATA_MODEL_NAME", "qwen-32B"),
        request_timeout=float(get_value("METADATA_REQUEST_TIMEOUT", "300")),
        connect_timeout=float(get_value("METADATA_CONNECT_TIMEOUT", "30")),
        max_retries=int(get_value("METADATA_MAX_RETRIES", "2")),
        temperature=float(get_value("METADATA_TEMPERATURE", "0.7")),
        max_text_length=int(get_value("METADATA_MAX_TEXT_LENGTH", "12000")),
    )


def _read_env_file(env_path: Path) -> dict[str, str]:
    """读取 .env 文件"""
    if not env_path.exists():
        return {}

    if dotenv_values is not None:
        return {
            key: value
            for key, value in dotenv_values(env_path).items()
            if key and value is not None
        }

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


# ==================== 提示词加载 ====================

def load_prompt(prompt_file: Path) -> tuple[str, str]:
    """加载提示词文件，返回 (system_prompt, user_template)

    支持两种格式：
    1. 使用 ``## system`` / ``## user`` 显式分段
    2. 无分段标记时，整个文件视为 system_prompt（user_template 为空）
    """
    content = prompt_file.read_text(encoding="utf-8")

    system_prompt = ""
    user_template = ""

    current_section = None
    current_content: list[str] = []
    has_section_markers = False

    def _flush() -> None:
        nonlocal system_prompt, user_template
        if not current_section or not current_content:
            return
        text = "\n".join(current_content).strip()
        if current_section == "system":
            system_prompt = text
        elif current_section == "user":
            user_template = text

    for line in content.split("\n"):
        if line.strip() == "## system":
            has_section_markers = True
            _flush()
            current_section = "system"
            current_content = []
        elif line.strip() == "## user":
            has_section_markers = True
            _flush()
            current_section = "user"
            current_content = []
        else:
            current_content.append(line)

    # 处理最后一个 section
    _flush()

    # 无显式分段标记时，将整个文件作为 system_prompt
    if not has_section_markers:
        system_prompt = content.strip()
        user_template = ""

    return system_prompt, user_template


# 默认用户提示词模板（当提示词文件中未包含 ## user 段时使用）
DEFAULT_USER_TEMPLATE = (
    "请将以下主政策文件与补充文件进行语义内嵌融合。\n\n"
    "**重要：直接输出最终成品文档，不要输出任何思考过程、推理步骤、分析记录。**\n"
    "成品第一个字符必须是 `##`（即 `## 基本信息` 标题行），前面不得有任何文字。\n\n"
    "## 主文件：{main_filename}\n\n"
    "```markdown\n"
    "{main_content}\n"
    "```\n\n"
    "{supplement_files_section}\n\n"
    "融合要求：\n"
    "1. 保留主文件的基本信息块（政策名称、发布日期等），仅保留1套基本信息，不得出现多套\n"
    "2. 以主文件段落顺序为骨架，将补充文件内容语义内嵌到对应段落中，禁止分段堆砌拼接\n"
    "3. 补充文件所有内容必须100%融入，无遗漏\n"
    "4. 输出仅包含基本信息块 + 融合后连贯正文，无额外注释、无来源标注"
)

# 校验失败时发送给模型的精简返工指令（与提示词末尾"使用小技巧"对应）
VALIDATION_RETRY_INSTRUCTION = (
    "格式违规，存在上下文分段拼接，"
    "请完全按照语义内嵌融合示例重写全文正文，"
    "不准拆分段落堆放附件内容。"
    "成品中只能存在1套基本信息，不得出现多个基本信息块。"
    "直接输出最终成品，不要输出任何思考过程、推理步骤、分析记录，"
    "成品第一个字符必须是 `##`。"
)


# ==================== 基本信息提取 ====================

def extract_basic_info(content: str) -> dict:
    """提取基本信息字段"""
    info = {
        '政策名称': '',
        '发布日期': '',
        '政策文号': '',
        '发文部门': '',
        '政策级别': '',
        '原文链接': '',
    }
    patterns = {
        '政策名称': r'\*\*政策名称\*\*[：:]\s*(.+)',
        '发布日期': r'\*\*发布[日曰]期\*\*[：:]\s*(.+)',
        '政策文号': r'\*\*政策文号\*\*[：:]\s*(.+)',
        '发文部门': r'\*\*发文部门\*\*[：:]\s*(.+)',
        '政策级别': r'\*\*政策级别\*\*[：:]\s*(.+)',
        '原文链接': r'\*\*原文链接\*\*[：:]\s*(.+)',
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, content)
        if match:
            info[key] = match.group(1).strip()
    return info


def count_filled_fields(info: dict) -> int:
    """统计有内容的字段数量"""
    return sum(1 for v in info.values() if v and v.strip())


# ==================== LLM 调用 ====================

def call_llm(
    settings: MergeSettings,
    system_prompt: str,
    user_prompt: str,
) -> str:
    """调用LLM进行融合"""
    payload = {
        "model": settings.model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": settings.temperature,
        "max_tokens": 32768,  # 增加输出token限制
    }

    headers = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    last_error: Exception | None = None

    for attempt in range(1, settings.max_retries + 1):
        try:
            print(f"  第 {attempt} 次请求模型...")
            response = requests.post(
                settings.api_url,
                headers=headers,
                json=payload,
                timeout=(settings.connect_timeout, settings.request_timeout),
            )
            print(f"  模型响应状态码: {response.status_code}")
            response.raise_for_status()

            result = response.json()
            content = result["choices"][0]["message"]["content"]
            return content

        except Exception as exc:
            last_error = exc
            print(f"  第 {attempt} 次请求失败: {exc}")
            time.sleep(3)

    if last_error is None:
        raise RuntimeError("模型调用失败，但未捕获到具体异常")
    raise last_error


def extract_markdown_from_response(text: str) -> str:
    """从模型响应中提取Markdown内容，去除思考过程

    防御层级：
    1. 提取 ```markdown 代码块
    2. 按已知思考前缀关键词截断（整块剥离）
    3. 扫描全文中的思考残留标记（[Output Generation]、[Done]、Proceeds. 等），
       从最后一个标记之后开始截断（处理思考过程散布在伪草稿中的情况）
    4. 按 ``## 基本信息`` / ``**政策名称**`` 定位正文起点
    5. 兜底：找第一个非缩进的 ``#`` 标题行
    """
    cleaned = text.strip()

    # 第1层：提取 ```markdown ... ``` 代码块
    match = re.search(r"```markdown\s*([\s\S]*?)\s*```", cleaned)
    if match:
        cleaned = match.group(1).strip()

    # 第2层：剥离开头整块思考前缀（匹配到第一个真正的标题之前）
    thinking_block_patterns = [
        r'(?s)^Here\'s\s+(?:a\s+)?thinking\s+process[:：].*?(?=\n\s*#+\s+\S)',
        r'(?s)^Thinking\s+[Pp]rocess[:：].*?(?=\n\s*#+\s+\S)',
        r'(?s)^思考过程[:：].*?(?=\n\s*#+\s+\S)',
        r'(?s)^让我[来]?思考.*?(?=\n\s*#+\s+\S)',
    ]
    for pat in thinking_block_patterns:
        m = re.search(pat, cleaned, re.IGNORECASE)
        if m:
            cleaned = cleaned[m.end():].strip()
            break

    # 第3层：扫描思考残留标记，从最后一个标记后截断
    # 这些标记会散布在伪草稿/内部对话中
    thinking_residue_patterns = [
        r'\[Output Generation\]',
        r'\[Done\]',
        r'^\s*Proceeds\.\s*$',
        r'^\s*</think>\s*$',
        r'\*\(Self-Correction',
        r'\*\(Note:',
        r'\*Revised Paragraph',
        r'^\s*Structure check:\s*$',
        r'^\s*Output matches the response\.\s*$',
        r'^\s*Final check of the prompt:',
    ]
    last_marker_end = 0
    for pat in thinking_residue_patterns:
        for m in re.finditer(pat, cleaned, re.MULTILINE | re.IGNORECASE):
            # 记录该行结束位置（包括换行符）
            line_end = cleaned.find('\n', m.end())
            if line_end == -1:
                line_end = len(cleaned)
            else:
                line_end += 1
            if line_end > last_marker_end:
                last_marker_end = line_end

    if last_marker_end > 0:
        # 跳过标记后的空行
        remaining = cleaned[last_marker_end:].lstrip('\n')
        if remaining:
            cleaned = remaining

    # 第4层：按 "## 基本信息" 格式定位（兼容 "# 基本信息"）
    pattern = r'(##?\s*基本信息\s*\n\s*(?:-\s*)?\*\*政策名称\*\*)'
    match = re.search(pattern, cleaned)
    if match:
        cleaned = cleaned[match.start():]
        return cleaned.strip()

    # 第5层：按 "**政策名称**" 字段往前回溯到最近的 "#" 标题
    policy_name_pos = cleaned.find("**政策名称**")
    if policy_name_pos > 0:
        preceding = cleaned[:policy_name_pos]
        lines = preceding.split('\n')
        for i in range(len(lines) - 1, -1, -1):
            stripped = lines[i].strip()
            # 只匹配非缩进的标题行（排除思考草稿中带缩进的伪标题）
            if stripped.startswith('#') and not lines[i].startswith((' ', '\t')):
                pos = sum(len(l) + 1 for l in lines[:i])
                cleaned = cleaned[pos:]
                return cleaned.strip()

    # 第6层：兜底——找第一个非缩进的 # 标题行或 - ** 开头的行
    lines = cleaned.split('\n')
    start_idx = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (stripped.startswith('#') or stripped.startswith('- **')) and not line.startswith((' ', '\t')):
            start_idx = i
            break
    if start_idx > 0:
        cleaned = '\n'.join(lines[start_idx:])

    return cleaned.strip()


def validate_merged_content(content: str) -> tuple[bool, list[str]]:
    """校验融合成品是否符合提示词规范（对应提示词第四节"校验返工机制"）

    Returns:
        (is_valid, errors)  errors 为问题描述列表
    """
    errors: list[str] = []

    # 规则0：成品开头必须是 "##" 或 "# 基本信息"，不允许有思考过程前缀
    first_non_empty = ""
    for line in content.split("\n"):
        if line.strip():
            first_non_empty = line.strip()
            break
    thinking_markers = [
        "here's a thinking", "thinking process", "[output generation]",
        "思考过程", "让我思考", "推理过程", "analysis:", "step 1",
    ]
    if first_non_empty and not first_non_empty.startswith("#"):
        errors.append(f'成品开头不是标题行（"{first_non_empty[:40]}..."），'
                       '前面可能包含思考过程，必须删除')
    else:
        lower_start = content[:500].lower()
        for marker in thinking_markers:
            if lower_start.startswith(marker):
                errors.append(f'成品开头包含思考过程标记 "{marker}"，必须删除')
                break

    # 规则1：成品中只能存在1套基本信息（通过"## 基本信息"或"**政策名称**"计数）
    basic_info_headers = re.findall(r'##?\s*基本信息', content)
    policy_name_fields = re.findall(r'\*\*政策名称\*\*[：:]', content)
    if len(basic_info_headers) > 1:
        errors.append(f"存在 {len(basic_info_headers)} 套基本信息块，应仅保留1套")
    elif len(policy_name_fields) > 1:
        errors.append(f'存在 {len(policy_name_fields)} 个"政策名称"字段，应仅保留1个')

    # 规则2：正文不应出现分文件/分附件的独立区块标记
    split_markers = re.findall(
        r'(?:^|\n)\s*#+\s*(?:附件|补充文件|文件\d|附表)\s*',
        content,
    )
    if split_markers:
        errors.append(f"正文中存在 {len(split_markers)} 处附件/补充文件分段标记，"
                       "说明未按语义内嵌融合")

    # 规则3：正文不应包含 ```markdown 代码块嵌套（说明原文被完整粘贴）
    code_blocks = re.findall(r'```markdown', content)
    if code_blocks:
        errors.append(f"正文中残留 {len(code_blocks)} 个 markdown 代码块，"
                       "说明存在原文堆砌拼接")

    return (len(errors) == 0, errors)


# ==================== 融合处理 ====================

def process_directory(
    dir_path: Path,
    settings: MergeSettings,
    system_prompt: str,
    user_template: str,
) -> None:
    """处理单个目录"""
    # 若提示词文件中未提供 user 模板，使用默认模板
    template = user_template or DEFAULT_USER_TEMPLATE

    md_files = []

    for filename in os.listdir(dir_path):
        if filename.endswith('.md') and not filename.startswith('【融合版】'):
            filepath = dir_path / filename
            content = filepath.read_text(encoding="utf-8")
            basic_info = extract_basic_info(content)
            md_files.append({
                'path': filepath,
                'content': content,
                'basic_info': basic_info,
                'filename': filename
            })

    if len(md_files) < 2:
        print(f"  跳过（文件数不足）: {dir_path.name}")
        return

    print(f"\n处理目录: {dir_path.name}")
    for f in md_files:
        filled = count_filled_fields(f['basic_info'])
        print(f"  - {f['filename']}: {filled} 个字段有内容")

    # 选择主文件（基本信息最完整的）
    main_file = max(md_files, key=lambda x: count_filled_fields(x['basic_info']))
    print(f"  主文件: {main_file['filename']}")

    # 构建补充文件部分
    supplement_parts = []
    for f in md_files:
        if f['path'] != main_file['path']:
            supplement_parts.append(
                f"### 补充文件：{f['filename']}\n\n```markdown\n{f['content']}\n```"
            )

    supplement_files_section = "\n\n".join(supplement_parts)

    # 构建初始用户提示词
    user_prompt = template.format(
        main_filename=main_file['filename'],
        main_content=main_file['content'][:20000],
        supplement_files_section=supplement_files_section[:40000],
    )

    # 调用LLM（含校验返工循环，对应提示词第四节）
    max_validation_retries = 2  # 最多额外返工 2 次
    output_filename = f"【融合版】{main_file['filename']}"
    output_path = dir_path / output_filename

    for attempt in range(1, max_validation_retries + 2):  # 1次正常 + N次返工
        print(f"  正在调用LLM进行融合（第 {attempt} 次）...")
        try:
            response = call_llm(settings, system_prompt, user_prompt)
            merged_content = extract_markdown_from_response(response)

            # 校验融合结果
            is_valid, errors = validate_merged_content(merged_content)
            if is_valid:
                output_path.write_text(merged_content, encoding="utf-8")
                print(f"  输出: {output_filename} ({len(merged_content)} chars)")
                return

            # 校验未通过
            print(f"  校验未通过（第 {attempt} 次）:")
            for err in errors:
                print(f"    - {err}")

            if attempt > max_validation_retries:
                # 返工次数已用完，保存当前结果并提示
                output_path.write_text(merged_content, encoding="utf-8")
                print(f"  返工次数已用完，保存当前结果: {output_filename}")
                return

            # 追加校验指令，要求模型返工
            user_prompt = (
                user_prompt
                + f"\n\n---\n"
                  f"上次输出存在以下问题，请重新输出完整融合结果：\n"
                  + "\n".join(f"- {e}" for e in errors)
                  + f"\n\n{VALIDATION_RETRY_INSTRUCTION}"
            )

        except Exception as e:
            print(f"  融合失败: {e}")
            return


def main():
    # 加载配置
    settings = load_settings()
    print(f"模型: {settings.model_name}")
    print(f"API: {settings.api_url}")

    # 加载提示词
    prompt_file = METADATA_DIR / "prompts" / "ronghe.md"
    if not prompt_file.exists():
        print(f"提示词文件不存在: {prompt_file}")
        return

    system_prompt, user_template = load_prompt(prompt_file)
    print(f"提示词已加载: {prompt_file.name}")
    print(f"  system_prompt: {len(system_prompt)} chars")
    if user_template:
        print(f"  user_template: 来自提示词文件 ({len(user_template)} chars)")
    else:
        print(f"  user_template: 使用代码内置默认模板")

    # 找到融合数据目录
    fusion_dir = None
    for item in os.listdir(METADATA_DIR):
        item_path = METADATA_DIR / item
        if item_path.is_dir() and '融合' in item:
            fusion_dir = item_path
            break

    if not fusion_dir:
        print("未找到融合数据目录")
        return

    print(f"融合目录: {fusion_dir}")

    # 遍历所有子目录
    for sub in os.listdir(fusion_dir):
        subpath = fusion_dir / sub
        if subpath.is_dir():
            process_directory(subpath, settings, system_prompt, user_template)

    print("\n处理完成！")


if __name__ == '__main__':
    main()
