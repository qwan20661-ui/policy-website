#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
政策融合 LLM 服务 - FastAPI 应用
提供 REST API，供前端 /pages/structure.html 的"融合"按钮调用
"""
from __future__ import annotations

import sys
import io
from pathlib import Path

# ── 修复 Windows 控制台编码 ──
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import os
import re
import time
import json
import logging
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ── 配置 ──
BASE_DIR = Path(__file__).resolve().parent
DOTENV_PATH = BASE_DIR / ".env"
PROMPTS_DIR = BASE_DIR / "prompts"
FUSION_DATA_DIR = Path(os.getenv("FUSION_DATA_DIR", "D:\\kejiju\\metadata\\重点政策融合数据"))

# JDBC 中间层服务地址（融合编排时通过该服务获取文档原文）
JDBC_SERVICE_URL = os.getenv("JDBC_SERVICE_URL", "http://localhost:8080")

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fusion")

app = FastAPI(
    title="政策融合 LLM 服务",
    description="提供基于 LLM 的政策文件智能融合 API",
    version="1.0.0",
)

# CORS - 允许前端跨域调用
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════

class FusionRequest(BaseModel):
    """融合请求参数（基于目录路径）"""
    directoryPath: str = Field(
        ..., description="政策文件目录路径（绝对路径），如 D:\\kejiju\\metadata\\重点政策融合数据\\国家高新技术企业认定"
    )
    policyTitle: Optional[str] = Field(
        None, description="政策标题，为空则取目录名称"
    )
    promptName: str = Field(
        "ronghe_api.md", description="提示词文件名，位于 prompts/ 目录下"
    )


class DocumentInput(BaseModel):
    """输入文档"""
    title: str = Field(..., description="文档标题/名称")
    content: str = Field(..., description="文档完整正文 Markdown 内容")


class FusionByContentRequest(BaseModel):
    """融合请求参数（基于文档内容注入）"""
    documents: list[DocumentInput] = Field(
        ..., description="待融合的文档列表（标题+正文）"
    )
    policyTitle: str = Field(
        ..., description="政策标题，用于输出文档的一级标题"
    )
    promptName: str = Field(
        "ronghe_api.md", description="提示词文件名，位于 prompts/ 目录下"
    )


class FusionByPolicyRequest(BaseModel):
    """融合请求参数（基于 fusionPolicyId，后台自动从 JDBC 获取原文）"""
    fusionPolicyId: str = Field(
        ..., description="融合政策ID，对应数据库 X1_FUSION_POLICY 表的主键"
    )
    promptName: str = Field(
        "ronghe_api.md", description="提示词文件名，位于 prompts/ 目录下"
    )


class FusionResponse(BaseModel):
    """融合响应"""
    success: bool
    data: Optional[dict] = None
    message: Optional[str] = None


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str
    version: str
    model: Optional[str] = None


# ═══════════════════════════════════════════════
# 配置加载（复用 merge_policies_llm.py 逻辑）
# ═══════════════════════════════════════════════

def load_env_settings() -> dict:
    """从 .env 加载 LLM 配置"""
    settings = {
        "api_url": os.getenv("METADATA_API_URL", "http://127.0.0.1:8000/v1/chat/completions"),
        "api_key": os.getenv("METADATA_API_KEY", ""),
        "model_name": os.getenv("METADATA_MODEL_NAME", "qwen-32B"),
        "request_timeout": int(os.getenv("METADATA_REQUEST_TIMEOUT", "300")),
        "max_retries": int(os.getenv("METADATA_MAX_RETRIES", "2")),
        "temperature": float(os.getenv("METADATA_TEMPERATURE", "0.7")),
    }

    # 尝试从 .env 文件加载
    env_file = DOTENV_PATH
    if env_file.exists():
        try:
            from dotenv import dotenv_values
            env_vals = dotenv_values(env_file)
            for k, v in env_vals.items():
                if v is not None and k in os.environ:
                    pass  # 环境变量优先级更高
                elif v is not None:
                    os.environ[k] = v
            # 重新读取
            settings["api_url"] = os.getenv("METADATA_API_URL", settings["api_url"])
            settings["api_key"] = os.getenv("METADATA_API_KEY", settings["api_key"])
            settings["model_name"] = os.getenv("METADATA_MODEL_NAME", settings["model_name"])
        except ImportError:
            # dotenv 未安装，手动解析
            _parse_env_file(env_file)

    return settings


def _parse_env_file(env_path: Path) -> None:
    """手动解析 .env 文件"""
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key not in os.environ:
            os.environ[key] = value


# ═══════════════════════════════════════════════
# JDBC 调用（从 Java 中间层获取文档内容）
# ═══════════════════════════════════════════════

def fetch_from_jdbc(path: str, method: str = "GET", body: dict | None = None) -> dict:
    """调用 Java JDBC 中间层服务"""
    url = f"{JDBC_SERVICE_URL}{path}"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    logger.info(f"[JDBC] {method} {url}")
    try:
        if method == "POST" and body:
            resp = requests.post(url, headers=headers, json=body, timeout=30)
        else:
            resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.Timeout:
        logger.error(f"[JDBC] 请求超时: {url}")
        return {"success": False, "message": f"JDBC 请求超时: {path}"}
    except requests.ConnectionError:
        logger.error(f"[JDBC] 连接失败: {url}")
        return {"success": False, "message": f"无法连接 JDBC 服务 ({JDBC_SERVICE_URL})"}
    except Exception as e:
        logger.error(f"[JDBC] 请求失败: {e}")
        return {"success": False, "message": str(e)}


# ═══════════════════════════════════════════════
# 提示词加载
# ═══════════════════════════════════════════════

def load_prompt(prompt_name: str) -> tuple[str, str]:
    """加载提示词文件，返回 (system_prompt, user_template)"""
    prompt_file = PROMPTS_DIR / prompt_name
    if not prompt_file.exists():
        # 兜底：尝试不带目录前缀
        prompt_file = BASE_DIR / prompt_name
    if not prompt_file.exists():
        raise FileNotFoundError(f"提示词文件不存在: {prompt_file}")

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
        stripped = line.strip()
        if stripped == "## system":
            has_section_markers = True
            _flush()
            current_section = "system"
            current_content = []
        elif stripped == "## user":
            has_section_markers = True
            _flush()
            current_section = "user"
            current_content = []
        else:
            current_content.append(line)

    _flush()

    if not has_section_markers:
        system_prompt = content.strip()
        user_template = ""

    return system_prompt, user_template


# ═══════════════════════════════════════════════
# 文件处理
# ═══════════════════════════════════════════════

def extract_basic_info(content: str) -> dict:
    """提取基本信息字段"""
    info = {
        "政策名称": "",
        "发布日期": "",
        "政策文号": "",
        "发文部门": "",
        "政策级别": "",
        "原文链接": "",
    }
    patterns = {
        "政策名称": r"\*\*政策名称\*\*[：:]\s*(.+)",
        "发布日期": r"\*\*发布[日曰]期\*\*[：:]\s*(.+)",
        "政策文号": r"\*\*政策文号\*\*[：:]\s*(.+)",
        "发文部门": r"\*\*发文部门\*\*[：:]\s*(.+)",
        "政策级别": r"\*\*政策级别\*\*[：:]\s*(.+)",
        "原文链接": r"\*\*原文链接\*\*[：:]\s*(.+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, content)
        if match:
            info[key] = match.group(1).strip()
    return info


def count_filled_fields(info: dict) -> int:
    """统计有内容的字段数量"""
    return sum(1 for v in info.values() if v and v.strip())


def read_file_safe(path: str) -> str:
    """安全读取文件，自动检测编码"""
    encodings = ["utf-8", "gbk", "gb2312", "utf-16", "latin-1"]
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    return f"[无法读取文件：{path}]"


# ═══════════════════════════════════════════════
# LLM 调用
# ═══════════════════════════════════════════════

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


def call_llm(
    settings: dict,
    system_prompt: str,
    user_prompt: str,
) -> str:
    """调用 LLM 进行融合"""
    payload = {
        "model": settings["model_name"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": settings["temperature"],
    }

    headers = {"Content-Type": "application/json"}
    if settings.get("api_key"):
        headers["Authorization"] = f"Bearer {settings['api_key']}"

    api_url = settings["api_url"]
    max_retries = settings["max_retries"]
    timeout = settings["request_timeout"]

    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"  第 {attempt} 次请求模型 {settings['model_name']} ...")
            response = requests.post(
                api_url,
                headers=headers,
                json=payload,
                timeout=(30, timeout),
            )
            logger.info(f"  模型响应状态码: {response.status_code}")
            if not response.ok:
                logger.error(f"  API 错误响应体: {response.text[:2000]}")
            response.raise_for_status()

            result = response.json()
            content = result["choices"][0]["message"]["content"]
            return content

        except Exception as exc:
            last_error = exc
            logger.warning(f"  第 {attempt} 次请求失败: {exc}")
            time.sleep(3)

    if last_error is None:
        raise RuntimeError("模型调用失败，但未捕获到具体异常")
    raise last_error


def extract_markdown_from_response(text: str) -> str:
    """从模型响应中提取 Markdown 内容，去除思考过程"""
    cleaned = text.strip()

    # 第1层：提取 ```markdown ... ``` 代码块
    match = re.search(r"```markdown\s*([\s\S]*?)\s*```", cleaned)
    if match:
        cleaned = match.group(1).strip()

    # 第2层：剥离开头整块思考前缀
    thinking_block_patterns = [
        r"(?s)^Here's\s+(?:a\s+)?thinking\s+process[:：].*?(?=\n\s*#+\s+\S)",
        r"(?s)^Thinking\s+[Pp]rocess[:：].*?(?=\n\s*#+\s+\S)",
        r"(?s)^思考过程[:：].*?(?=\n\s*#+\s+\S)",
        r"(?s)^让我[来]?思考.*?(?=\n\s*#+\s+\S)",
    ]
    for pat in thinking_block_patterns:
        m = re.search(pat, cleaned, re.IGNORECASE)
        if m:
            cleaned = cleaned[m.end():].strip()
            break

    # 第3层：扫描思考残留标记
    thinking_residue_patterns = [
        r"\[Output Generation\]",
        r"\[Done\]",
        r"^\s*Proceeds\.\s*$",
        r"^\s*</think>\s*$",
        r"\*\(Self-Correction",
        r"\*\(Note:",
        r"\*Revised Paragraph",
        r"^\s*Structure check:\s*$",
        r"^\s*Output matches the response\.\s*$",
        r"^\s*Final check of the prompt:",
    ]
    last_marker_end = 0
    for pat in thinking_residue_patterns:
        for m in re.finditer(pat, cleaned, re.MULTILINE | re.IGNORECASE):
            line_end = cleaned.find("\n", m.end())
            if line_end == -1:
                line_end = len(cleaned)
            else:
                line_end += 1
            if line_end > last_marker_end:
                last_marker_end = line_end
    if last_marker_end > 0:
        remaining = cleaned[last_marker_end:].lstrip("\n")
        if remaining:
            cleaned = remaining

    # 第4层：按 "## 基本信息" 定位
    pattern = r"(##?\s*基本信息\s*\n\s*(?:-\s*)?\*\*政策名称\*\*)"
    match = re.search(pattern, cleaned)
    if match:
        cleaned = cleaned[match.start():]
        return cleaned.strip()

    # 第5层：按 "**政策名称**" 字段往前回溯
    policy_name_pos = cleaned.find("**政策名称**")
    if policy_name_pos > 0:
        preceding = cleaned[:policy_name_pos]
        lines = preceding.split("\n")
        for i in range(len(lines) - 1, -1, -1):
            stripped = lines[i].strip()
            if stripped.startswith("#") and not lines[i].startswith((" ", "\t")):
                pos = sum(len(l) + 1 for l in lines[:i])
                cleaned = cleaned[pos:]
                return cleaned.strip()

    # 第6层：兜底
    lines = cleaned.split("\n")
    start_idx = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (stripped.startswith("#") or stripped.startswith("- **")) and not line.startswith((" ", "\t")):
            start_idx = i
            break
    if start_idx > 0:
        cleaned = "\n".join(lines[start_idx:])

    return cleaned.strip()


def validate_merged_content(content: str) -> tuple[bool, list[str]]:
    """校验融合成品是否符合规范"""
    errors: list[str] = []

    # 规则0：成品开头必须是 "#" 标题
    first_non_empty = ""
    for line in content.split("\n"):
        if line.strip():
            first_non_empty = line.strip()
            break
    if first_non_empty and not first_non_empty.startswith("#"):
        errors.append(
            f'成品开头不是标题行（"{first_non_empty[:40]}..."），可能包含思考过程'
        )

    # 规则1：只能存在1套基本信息
    basic_info_headers = re.findall(r"##?\s*基本信息", content)
    policy_name_fields = re.findall(r"\*\*政策名称\*\*[：:]", content)
    if len(basic_info_headers) > 1:
        errors.append(f"存在 {len(basic_info_headers)} 套基本信息块，应仅保留1套")
    elif len(policy_name_fields) > 1:
        errors.append(f'存在 {len(policy_name_fields)} 个"政策名称"字段，应仅保留1个')

    # 规则2：不应出现分文件/分附件的独立区块标记（排除合法的 ## 附件列表）
    split_markers = re.findall(
        r"(?:^|\n)\s*#+\s*(?:补充文件|文件\d|附表)\s*",
        content,
    )
    if split_markers:
        errors.append(f"正文中存在 {len(split_markers)} 处分段标记，说明未按语义内嵌融合")

    # 规则3：不应包含 markdown 代码块嵌套
    code_blocks = re.findall(r"```markdown", content)
    if code_blocks:
        errors.append(f"正文中残留 {len(code_blocks)} 个 markdown 代码块，存在原文堆砌")

    return (len(errors) == 0, errors)


# ═══════════════════════════════════════════════
# 核心融合逻辑
# ═══════════════════════════════════════════════

def do_fusion(
    directory_path: str,
    policy_title: str | None = None,
    prompt_name: str = "ronghe1.md",
) -> dict:
    """
    对指定目录中的 .md 文件执行 LLM 融合

    Args:
        directory_path: 政策文件目录绝对路径
        policy_title: 政策标题（用于输出文件名），为空则取目录名称
        prompt_name: 提示词文件名

    Returns:
        dict: {content, outputPath, title, filesCount}
    """
    settings = load_env_settings()
    dir_path = Path(directory_path)

    if not dir_path.exists() or not dir_path.is_dir():
        raise FileNotFoundError(f"目录不存在: {directory_path}")

    # 加载提示词
    try:
        system_prompt, user_template = load_prompt(prompt_name)
    except FileNotFoundError:
        # 兜底使用内置模板
        system_prompt = ""
        user_template = DEFAULT_USER_TEMPLATE
    template = user_template or DEFAULT_USER_TEMPLATE

    # 扫描 .md 文件（跳过已融合的文件）
    md_files = []
    for filename in os.listdir(dir_path):
        if filename.endswith(".md") and not filename.startswith("【融合版】"):
            filepath = dir_path / filename
            if filepath.is_file():
                content = filepath.read_text(encoding="utf-8")
                basic_info = extract_basic_info(content)
                md_files.append({
                    "path": filepath,
                    "content": content,
                    "basic_info": basic_info,
                    "filename": filename,
                })

    if len(md_files) < 2:
        if len(md_files) == 1:
            # 只有一个文件，直接返回该文件内容（无需融合）
            content = md_files[0]["content"]
            title = policy_title or dir_path.name
            out_filename = f"【融合版】{md_files[0]['filename']}"
            out_path = dir_path / out_filename
            out_path.write_text(content, encoding="utf-8")
            logger.info(f"单文件直接输出: {out_path}")
            return {
                "content": content,
                "outputPath": str(out_path),
                "title": title,
                "filesCount": 1,
                "warning": "目录下仅有一个MD文件，直接输出原文，未调用LLM",
            }
        raise ValueError(f"目录下MD文件不足（{len(md_files)}个），需要至少2个文件才能融合")

    logger.info(f"处理目录: {dir_path.name} ({len(md_files)} 个文件)")
    for f in md_files:
        filled = count_filled_fields(f["basic_info"])
        logger.info(f"  - {f['filename']}: {filled} 个字段有内容")

    # 选择主文件（基本信息最完整的）
    main_file = max(md_files, key=lambda x: count_filled_fields(x["basic_info"]))
    logger.info(f"  主文件: {main_file['filename']}")

    # 构建补充文件部分
    supplement_parts = []
    for f in md_files:
        if f["path"] != main_file["path"]:
            supplement_parts.append(
                f"### 补充文件：{f['filename']}\n\n```markdown\n{f['content']}\n```"
            )

    supplement_files_section = "\n\n".join(supplement_parts)

    # 构建用户提示词
    user_prompt = template.format(
        main_filename=main_file["filename"],
        main_content=main_file["content"][:20000],
        supplement_files_section=supplement_files_section[:40000],
    )

    # 调用 LLM（含校验返工循环）
    max_validation_retries = 2
    title = policy_title or dir_path.name
    out_filename = f"【融合版】{main_file['filename']}"
    out_path = dir_path / out_filename

    for attempt in range(1, max_validation_retries + 2):
        logger.info(f"  正在调用 LLM 进行融合（第 {attempt} 次）...")
        try:
            response = call_llm(settings, system_prompt, user_prompt)
            merged_content = extract_markdown_from_response(response)

            # 校验
            is_valid, errors = validate_merged_content(merged_content)
            if is_valid:
                out_path.write_text(merged_content, encoding="utf-8")
                logger.info(f"  输出: {out_filename} ({len(merged_content)} chars)")
                return {
                    "content": merged_content,
                    "outputPath": str(out_path),
                    "title": title,
                    "filesCount": len(md_files),
                    "model": settings["model_name"],
                }

            logger.warning(f"  校验未通过（第 {attempt} 次）: {errors}")
            if attempt > max_validation_retries:
                out_path.write_text(merged_content, encoding="utf-8")
                logger.info(f"  返工次数已用完，保存当前结果: {out_filename}")
                return {
                    "content": merged_content,
                    "outputPath": str(out_path),
                    "title": title,
                    "filesCount": len(md_files),
                    "model": settings["model_name"],
                    "warnings": errors,
                }

            user_prompt = (
                user_prompt
                + f"\n\n---\n"
                  f"上次输出存在以下问题，请重新输出完整融合结果：\n"
                  + "\n".join(f"- {e}" for e in errors)
                  + f"\n\n格式违规，存在上下文分段拼接，"
                    f"请完全按照语义内嵌融合示例重写全文正文，"
                    f"不准拆分段落堆放附件内容。"
                    f"成品中只能存在1套基本信息，不得出现多个基本信息块。"
                    f"直接输出最终成品，不要输出任何思考过程、推理步骤、分析记录，"
                    f"成品第一个字符必须是 `##`。"
            )

        except Exception as e:
            logger.error(f"  融合失败: {e}")
            raise RuntimeError(f"融合失败: {e}")

    raise RuntimeError("融合过程异常终止")


def do_fusion_by_content(
    documents: list[dict],
    policy_title: str,
    prompt_name: str = "ronghe_api.md",
) -> dict:
    """
    对传入的文档内容列表执行 LLM 融合（不依赖文件系统目录）

    Args:
        documents: [{"title": str, "content": str}, ...]
        policy_title: 政策名称
        prompt_name: 提示词文件名

    Returns:
        dict: {content, title, filesCount, model, warnings?}
    """
    settings = load_env_settings()

    # 加载提示词
    try:
        system_prompt, user_template = load_prompt(prompt_name)
    except FileNotFoundError:
        system_prompt = ""
        user_template = ""
    if not system_prompt:
        # 无提示词时使用通用要求
        system_prompt = "你是专业政策结构化融合解析专家。对多份政策文档进行结构化融合。"

    if len(documents) < 2:
        if len(documents) == 1:
            content = documents[0]["content"]
            logger.info(f"单文档直接输出，未调用 LLM: {documents[0]['title']}")
            return {
                "content": content,
                "title": policy_title,
                "filesCount": 1,
                "warning": "仅有一个文档，直接输出原文，未调用 LLM",
            }
        raise ValueError(f"文档数不足（{len(documents)}个），需要至少2个文档才能融合")

    logger.info(f"内容融合: {policy_title} ({len(documents)} 个文档)")
    for doc in documents:
        logger.info(f"  - {doc['title']}: {len(doc['content'])} chars")

    # 构建提示词：将文档内容注入
    content_parts = []
    for i, doc in enumerate(documents, 1):
        content_parts.append(
            f"## 文档 {i}：{doc['title']}\n\n"
            f"```markdown\n{doc['content'][:30000]}\n```"
        )

    documents_section = "\n\n".join(content_parts)

    # 替换提示词中的模板变量
    user_prompt = (
        f"以下共 {len(documents)} 份政策文档需要进行融合处理。\n\n"
        f"{documents_section}\n\n"
        f"请将以上 {len(documents)} 份文档融合为一篇完整的结构化政策解读文档。\n"
        f"政策名称统一为「{policy_title}」。\n"
        f"成品第一行必须是 `# {policy_title}（政策解读）`，"
        f"前面不得有任何文字。\n"
        f"严格按 system 中的板块规则融合输出，"
        f"禁止输出任何思考过程、推理步骤、分析记录。"
    )

    max_validation_retries = 2
    out_filename = f"【融合版】{policy_title}.md"

    for attempt in range(1, max_validation_retries + 2):
        logger.info(f"  正在调用 LLM 进行融合（第 {attempt} 次）...")
        try:
            response = call_llm(settings, system_prompt, user_prompt)
            merged_content = extract_markdown_from_response(response)

            is_valid, errors = validate_merged_content(merged_content)
            if is_valid:
                logger.info(f"  融合成功 ({len(merged_content)} chars)")
                return {
                    "content": merged_content,
                    "title": policy_title,
                    "filesCount": len(documents),
                    "model": settings["model_name"],
                }

            logger.warning(f"  校验未通过（第 {attempt} 次）: {errors}")
            if attempt > max_validation_retries:
                logger.info(f"  返工次数已用完，保存当前结果")
                return {
                    "content": merged_content,
                    "title": policy_title,
                    "filesCount": len(documents),
                    "model": settings["model_name"],
                    "warnings": errors,
                }

            user_prompt = (
                user_prompt
                + f"\n\n---\n"
                  f"上次输出存在以下问题，请重新输出完整融合结果：\n"
                  + "\n".join(f"- {e}" for e in errors)
                  + f"\n\n格式违规，存在上下文分段拼接，"
                    f"请完全按照语义内嵌融合示例重写全文正文，"
                    f"不准拆分段落堆放附件内容。"
                    f"成品中只能存在1套基本信息，不得出现多个基本信息块。"
                    f"直接输出最终成品，不要输出任何思考过程、推理步骤、分析记录，"
                    f"成品第一个字符必须是 `##`。"
            )

        except Exception as e:
            logger.error(f"  融合失败: {e}")
            raise RuntimeError(f"融合失败: {e}")

    raise RuntimeError("融合过程异常终止")


# ═══════════════════════════════════════════════
# API 路由
# ═══════════════════════════════════════════════

@app.get("/api/fusion/health", response_model=HealthResponse)
async def health():
    """健康检查"""
    settings = load_env_settings()
    return HealthResponse(
        status="ok",
        version="1.0.0",
        model=settings.get("model_name"),
    )


@app.post("/api/fusion/llm-fuse", response_model=FusionResponse)
async def llm_fuse(request: FusionRequest):
    """
    执行 LLM 政策融合

    接收前端传入的政策目录路径，扫描该目录下所有 .md 文件，
    调用 LLM 进行智能融合，输出融合后的 Markdown 内容。
    """
    try:
        logger.info(f"收到融合请求: directoryPath={request.directoryPath}, promptName={request.promptName}")

        if not os.path.exists(request.directoryPath):
            return FusionResponse(
                success=False,
                message=f"目录不存在: {request.directoryPath}",
            )

        result = do_fusion(
            directory_path=request.directoryPath,
            policy_title=request.policyTitle,
            prompt_name=request.promptName,
        )

        logger.info(f"融合完成: {result.get('outputPath')} ({result.get('filesCount')} files)")
        return FusionResponse(success=True, data=result)

    except FileNotFoundError as e:
        return FusionResponse(success=False, message=str(e))
    except ValueError as e:
        return FusionResponse(success=False, message=str(e))
    except RuntimeError as e:
        return FusionResponse(success=False, message=str(e))
    except Exception as e:
        logger.exception("融合请求异常")
        return FusionResponse(success=False, message=f"服务器内部错误: {e}")


@app.post("/api/fusion/llm-fuse-by-content", response_model=FusionResponse)
async def llm_fuse_by_content(request: FusionByContentRequest):
    """
    执行 LLM 政策融合（基于文档内容注入）

    接收前端传入的文档内容列表（标题+正文），
    调用 LLM 进行智能融合，返回融合后的 Markdown 内容。
    不依赖文件系统目录，适用于从数据库获取文档内容的场景。
    """
    try:
        logger.info(f"收到内容注入融合请求: policyTitle={request.policyTitle}, documents={len(request.documents)}")

        if not request.documents:
            return FusionResponse(
                success=False,
                message="文档列表为空，请提供至少一个文档",
            )

        docs_list = [
            {"title": doc.title, "content": doc.content}
            for doc in request.documents
        ]

        result = do_fusion_by_content(
            documents=docs_list,
            policy_title=request.policyTitle,
            prompt_name=request.promptName,
        )

        logger.info(f"内容融合完成: {request.policyTitle} ({result.get('filesCount')} docs)")
        return FusionResponse(success=True, data=result)

    except ValueError as e:
        return FusionResponse(success=False, message=str(e))
    except RuntimeError as e:
        return FusionResponse(success=False, message=str(e))
    except Exception as e:
        logger.exception("内容融合请求异常")
        return FusionResponse(success=False, message=f"服务器内部错误: {e}")


@app.post("/api/fusion/llm-fuse-by-policy", response_model=FusionResponse)
async def llm_fuse_by_policy(request: FusionByPolicyRequest):
    """
    执行 LLM 政策融合（基于 fusionPolicyId）

    通过 fusionPolicyId 自动从 JDBC 中间层获取关联文档列表及原文内容，
    再调用 LLM 进行融合，返回融合后的 Markdown。
    前端只需传入 fusionPolicyId，无需自行获取文档内容。
    """
    try:
        fid = request.fusionPolicyId
        logger.info(f"收到政策融合请求: fusionPolicyId={fid}")

        # 1) 获取融合政策详情（含关联文档列表）
        fuse_resp = fetch_from_jdbc("/api/fusion-classification/fuse", "POST", {"fusionPolicyId": fid})
        if not fuse_resp.get("success"):
            msg = fuse_resp.get("message", "获取融合政策详情失败")
            return FusionResponse(success=False, message=msg)

        policy_data = fuse_resp.get("data", {}) or {}
        docs = policy_data.get("documents") or []
        if not docs:
            return FusionResponse(success=False, message="该融合政策暂未关联任何子文档")

        policy_title = policy_data.get("directoryTitle") or "政策融合文档"

        # 2) 逐个获取文档正文
        documents = []
        for doc in docs:
            doc_id = doc.get("id", "")
            if not doc_id:
                continue
            try:
                content_resp = fetch_from_jdbc(f"/api/fusion-classification/documents/content?id={doc_id}")
                if content_resp.get("success") and content_resp.get("data", {}).get("content"):
                    documents.append({
                        "title": doc.get("policyTitle") or doc.get("fileName") or "未命名文档",
                        "content": content_resp["data"]["content"],
                    })
            except Exception as e:
                logger.warning(f"跳过文档 {doc.get('fileName')}: {e}")

        if len(documents) < 1:
            return FusionResponse(success=False, message="未能获取任何文档的有效正文内容")
        if len(documents) < 2:
            return FusionResponse(success=False, message=f"至少需要2份有效文档才能融合，当前仅获取到1份")

        # 3) 调用内容融合
        logger.info(f"获取到 {len(documents)} 份文档原文，开始 LLM 融合...")
        result = do_fusion_by_content(
            documents=documents,
            policy_title=policy_title,
            prompt_name=request.promptName,
        )

        logger.info(f"政策融合完成: {policy_title}")
        return FusionResponse(success=True, data=result)

    except ValueError as e:
        return FusionResponse(success=False, message=str(e))
    except RuntimeError as e:
        return FusionResponse(success=False, message=str(e))
    except Exception as e:
        logger.exception("政策融合请求异常")
        return FusionResponse(success=False, message=f"服务器内部错误: {e}")


@app.get("/api/fusion/info")
async def service_info():
    """服务信息"""
    settings = load_env_settings()
    return {
        "service": "政策融合 LLM 服务",
        "version": "1.0.0",
        "model": settings.get("model_name"),
        "api_url": settings.get("api_url"),
        "data_dir": str(FUSION_DATA_DIR),
        "prompts_available": [p.name for p in PROMPTS_DIR.glob("*.md")] if PROMPTS_DIR.exists() else [],
    }


# ═══════════════════════════════════════════════
# 启动入口
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    # 启动前加载 .env 文件（让 os.getenv 能读到 FUSION_SERVICE_PORT 等变量）
    try:
        from dotenv import load_dotenv
        load_dotenv(DOTENV_PATH)
    except Exception:
        _parse_env_file(DOTENV_PATH)

    port = int(os.getenv("FUSION_SERVICE_PORT", "8001"))
    host = os.getenv("FUSION_SERVICE_HOST", "0.0.0.0")

    logger.info("=" * 55)
    logger.info("🚀 政策融合 LLM 服务启动")
    logger.info(f"📡 监听: {host}:{port}")
    logger.info(f"📂 融合数据目录: {FUSION_DATA_DIR}")
    logger.info(f"📝 提示词目录: {PROMPTS_DIR}")
    logger.info("=" * 55)

    uvicorn.run(
        "app:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )
