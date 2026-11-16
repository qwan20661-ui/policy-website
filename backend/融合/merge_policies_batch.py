#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
政策文件批量融合脚本（LLM版）
遍历指定数据目录下所有子目录，对每个政策分组中的「通知公告」与「实施细则」进行融合处理。

目录结构要求：
    数据根目录/
    ├── 政策主题A/
    │   ├── 通知公告/
    │   │   └── xxx.md
    │   └── 实施细则/
    │       ├── yyy.md
    │       └── zzz.md  （可能多个）
    ├── 政策主题B/
    │   └── ...
    └── ...

使用方法：
    # 默认读取 .env 中的 METADATA_API_URL / METADATA_API_KEY / METADATA_MODEL_NAME
    python merge_policies_batch.py \
        --data_dir D:\kejiju\metadata\重点政策融合数据 \
        --prompt   D:\kejiju\metadata\prompts\ronghe.md

    # 手动指定 API 配置
    python merge_policies_batch.py \
        --data_dir D:\kejiju\metadata\重点政策融合数据 \
        --prompt   D:\kejiju\metadata\prompts\ronghe.md \
        --output   D:\kejiju\metadata\重点政策融合数据_融合结果 \
        --api_url  http://xxx/v1/chat/completions \
        --api_key  sk-xxx \
        --model    qwen-plus
"""

import os
import sys
import time
import argparse
import io
from pathlib import Path

# ── 修复 Windows 控制台中文/emoji 编码问题 ──
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ─── 让脚本可以 import 同目录的 config ───
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import requests
except ImportError:
    print("请先安装 requests：pip install requests")
    sys.exit(1)

# ─── 常量 ───
MAX_CONTENT_CHARS = 60000   # 单文件最大保留字符数（防止超 token）
MAX_RETRY = 3
RETRY_DELAY = 5             # 秒


# ═══════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════

def read_file_safe(path: str, max_chars: int = MAX_CONTENT_CHARS) -> str:
    """安全读取文件，超长时截断并提示。"""
    encodings = ["utf-8", "gbk", "gb2312", "utf-16", "latin-1"]
    content = None
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc) as f:
                content = f.read()
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if content is None:
        return f"[无法读取文件，编码识别失败：{path}]"
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n\n[... 文件过长，已截断，原始长度 {len(content)} 字符 ...]"
    return content


def _load_settings_from_config():
    """复用项目 config.py 的配置，返回 (api_url, api_key, model_name)。"""
    try:
        from config import load_settings
        settings = load_settings()
        return settings.api_url, settings.api_key, settings.model_name
    except Exception as e:
        print(f"⚠️  读取 config.py 失败（{e}），将回退到命令行参数。")
        return None, None, None


def call_llm(api_url: str, system_prompt: str, user_prompt: str,
             model: str, api_key: str | None,
             max_tokens: int = 8192, temperature: float = 0.3) -> str:
    """调用 OpenAI 兼容接口，带重试。"""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    for attempt in range(1, MAX_RETRY + 1):
        try:
            resp = requests.post(api_url, headers=headers, json=payload, timeout=300)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"    ⚠️  第{attempt}次调用失败：{e}")
            if attempt < MAX_RETRY:
                print(f"    {RETRY_DELAY}秒后重试...")
                time.sleep(RETRY_DELAY)
            else:
                return f"[LLM 调用失败，已重试{MAX_RETRY}次：{e}]"


def clean_output(text: str) -> str:
    """
    清洗 LLM 输出：
    1. 去除 markdown 代码块包裹
    2. 去除 ## 基本信息 之前的所有思考过程/推理步骤
    """
    import re
    text = text.strip()

    # 去除 markdown 代码块包裹
    if text.startswith("```markdown"):
        text = text[len("```markdown"):].strip()
    elif text.startswith("```md"):
        text = text[len("```md"):].strip()
    elif text.startswith("```"):
        text = text[3:].strip()
    if text.endswith("```"):
        text = text[:-3].strip()

    # 去除 ## 基本信息 之前的所有内容（思考过程、分析、校验记录等）
    match = re.search(r'^##\s+基本信息', text, re.MULTILINE)
    if match:
        text = text[match.start():]

    return text.strip()


# ═══════════════════════════════════════════════════════════════════
# 核心：发现政策分组 & 融合
# ═══════════════════════════════════════════════════════════════════

def discover_groups(data_dir: str):
    """
    扫描数据目录，返回所有政策分组：
    [
        {
            "name": "政策主题名",
            "notice_files": [...],
            "rules_files":  [...],
        },
        ...
    ]
    """
    groups = []
    data_path = Path(data_dir)
    if not data_path.exists():
        print(f"❌ 数据目录不存在：{data_dir}")
        return groups

    for topic_dir in sorted(data_path.iterdir()):
        if not topic_dir.is_dir():
            continue
        notice_dir = topic_dir / "通知公告"
        rules_dir  = topic_dir / "实施细则"
        if not notice_dir.exists():
            print(f"⚠️  跳过 {topic_dir.name}：缺少「通知公告」子目录")
            continue

        notice_files = sorted([str(f) for f in notice_dir.glob("*.md") if f.is_file()])
        rules_files  = sorted([str(f) for f in rules_dir.glob("*.md") if f.is_file()]) if rules_dir.exists() else []

        if not notice_files:
            print(f"⚠️  跳过 {topic_dir.name}：「通知公告」下无 .md 文件")
            continue

        groups.append({
            "name":         topic_dir.name,
            "topic_path":   str(topic_dir),
            "notice_dir":   str(notice_dir),
            "rules_dir":    str(rules_dir) if rules_dir.exists() else None,
            "notice_files": notice_files,
            "rules_files":  rules_files,
        })
    return groups


def build_user_prompt(notice_contents: list, rules_contents: list) -> str:
    """构建用户提示词，将全部文件内容注入。"""
    parts = []
    parts.append("请按 system 中的融合规则，对以下政策文件进行融合处理。\n")

    parts.append("=" * 60)
    parts.append("【通知公告文件】（主文件，基本信息与正文均不可修改）")
    parts.append("=" * 60)
    for i, (path, content) in enumerate(notice_contents, 1):
        parts.append(f"\n--- 通知公告文件 {i}：{os.path.basename(path)} ---")
        parts.append(content)

    if rules_contents:
        parts.append("\n" + "=" * 60)
        parts.append("【实施细则文件】（补充文件，内容需全部增补到通知公告正文中）")
        parts.append("=" * 60)
        for i, (path, content) in enumerate(rules_contents, 1):
            parts.append(f"\n--- 实施细则文件 {i}：{os.path.basename(path)} ---")
            parts.append(content)
    else:
        parts.append("\n（本分组无实施细则文件，请直接输出通知公告原文作为成品。）")

    parts.append("\n" + "=" * 60)
    parts.append("请严格按规则输出融合后的完整 Markdown 文档，开头必须是 ## 基本信息，禁止包含任何思考过程。")
    return "\n".join(parts)


def process_one_group(group: dict, system_prompt: str,
                      api_url: str, api_key: str | None, model: str,
                      output_dir: str) -> str:
    """处理单个政策分组，返回输出文件路径。"""
    topic_name = group["name"]
    print(f"\n{'─' * 60}")
    print(f"📂 处理政策分组：{topic_name}")
    print(f"   通知公告文件：{len(group['notice_files'])} 个")
    for f in group['notice_files']:
        print(f"      📄 {os.path.basename(f)}")
    print(f"   实施细则文件：{len(group['rules_files'])} 个")
    for f in group['rules_files']:
        print(f"      📄 {os.path.basename(f)}")

    # 读取所有文件
    notice_contents = [(p, read_file_safe(p)) for p in group["notice_files"]]
    rules_contents  = [(p, read_file_safe(p)) for p in group["rules_files"]]

    # 构建提示词并调用 LLM
    user_prompt = build_user_prompt(notice_contents, rules_contents)
    print(f"   📤 正在调用 LLM（{model}）...")
    print(f"   📊 用户提示词长度：{len(user_prompt)} 字符")

    merged = call_llm(api_url, system_prompt, user_prompt, model, api_key)
    merged = clean_output(merged)

    # 写入输出文件
    os.makedirs(output_dir, exist_ok=True)
    out_filename = f"{topic_name}_融合.md"
    out_path = os.path.join(output_dir, out_filename)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(merged)

    print(f"   ✅ 融合完成 → {out_path}")
    print(f"   📝 成品长度：{len(merged)} 字符")
    return out_path


# ═══════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="政策文件批量融合（LLM版）")
    parser.add_argument("--data_dir", required=True,
                        help="政策数据根目录（含多个政策主题子目录）")
    parser.add_argument("--prompt", required=True,
                        help="融合提示词文件路径（ronghe.md）")
    parser.add_argument("--output", default=None,
                        help="融合结果输出目录（默认：数据根目录_融合结果）")
    parser.add_argument("--api_url", default=None,
                        help="LLM API 地址（默认从 .env 读取 METADATA_API_URL）")
    parser.add_argument("--api_key", default=None,
                        help="LLM API Key（默认从 .env 读取 METADATA_API_KEY）")
    parser.add_argument("--model", default=None,
                        help="LLM 模型名称（默认从 .env 读取 METADATA_MODEL_NAME）")
    args = parser.parse_args()

    # ── 配置优先级：命令行参数 > .env 文件 ──
    cfg_url, cfg_key, cfg_model = _load_settings_from_config()
    api_url = args.api_url or cfg_url
    api_key = args.api_key or cfg_key
    model   = args.model   or cfg_model or "qwen-plus"

    if not api_url:
        print("❌ 未配置 API 地址。请通过 --api_url 参数或在 .env 中配置 METADATA_API_URL。")
        sys.exit(1)

    print(f"🔗 API 地址：{api_url}")
    print(f"🤖 模型名称：{model}")
    print(f"🔑 API Key：{'已配置' if api_key else '未配置（将以无认证方式调用）'}")

    # ── 读取 system prompt ──
    if not os.path.exists(args.prompt):
        print(f"❌ 提示词文件不存在：{args.prompt}")
        sys.exit(1)
    with open(args.prompt, "r", encoding="utf-8") as f:
        system_prompt = f.read().strip()
    print(f"✅ 已加载提示词：{args.prompt}（{len(system_prompt)} 字符）")

    # ── 输出目录 ──
    output_dir = args.output or (args.data_dir.rstrip("/\\") + "_融合结果")
    os.makedirs(output_dir, exist_ok=True)
    print(f"📁 输出目录：{output_dir}")

    # ── 发现政策分组 ──
    groups = discover_groups(args.data_dir)
    if not groups:
        print("❌ 未发现任何有效的政策分组，请检查目录结构。")
        sys.exit(1)
    print(f"\n🔍 共发现 {len(groups)} 个政策分组：")
    for g in groups:
        print(f"   • {g['name']}  （通知公告 {len(g['notice_files'])} / 实施细则 {len(g['rules_files'])}）")

    # ── 逐个处理 ──
    results = []
    start_time = time.time()
    for idx, group in enumerate(groups, 1):
        print(f"\n[{idx}/{len(groups)}]", end="")
        out_path = process_one_group(
            group, system_prompt, api_url, api_key, model, output_dir
        )
        results.append({"group": group["name"], "output": out_path})

    elapsed = time.time() - start_time

    # ── 汇总 ──
    print(f"\n{'═' * 60}")
    print(f"🎉 全部完成！共处理 {len(results)} 个政策分组，耗时 {elapsed:.1f} 秒。")
    print(f"📁 输出目录：{output_dir}")
    for r in results:
        print(f"   ✅ {r['group']} → {r['output']}")


if __name__ == "__main__":
    main()
