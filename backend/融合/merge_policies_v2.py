#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
政策文件批量融合脚本 v2（LLM版）
适配扁平目录结构：每个一级子文件夹内直接放.md文件
输出七大板块结构：基本信息、申报条件、适用对象、支持方式、申报材料、申报方式、联系方式、附件列表
"""

import os
import sys
import re
import time
import argparse
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

try:
    import requests
except ImportError:
    print("请先安装 requests：pip install requests")
    sys.exit(1)

MAX_CONTENT_CHARS = 60000
MAX_RETRY = 3
RETRY_DELAY = 5


def read_file_safe(path: str, max_chars: int = MAX_CONTENT_CHARS) -> str:
    encodings = ["utf-8", "gbk", "gb2312", "utf-16", "latin-1"]
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    return f"[无法读取文件：{path}]"


def _load_settings():
    try:
        from config import load_settings
        s = load_settings()
        return s.api_url, s.api_key, s.model_name
    except Exception as e:
        print(f"⚠️  读取 config.py 失败（{e}）")
        return None, None, None


def call_llm(api_url, system_prompt, user_prompt, model, api_key,
             max_tokens=16384, temperature=0.3):
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
            resp = requests.post(api_url, headers=headers, json=payload, timeout=600)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"    ⚠️  第{attempt}次调用失败：{e}")
            if attempt < MAX_RETRY:
                time.sleep(RETRY_DELAY)
            else:
                return f"[LLM 调用失败：{e}]"


def clean_output(text: str) -> str:
    """强力清洗 LLM 输出，确保以 ## 基本信息 开头。"""
    text = text.strip()
    # 去 markdown 代码块包裹
    for prefix in ["```markdown", "```md", "```"]:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    if text.endswith("```"):
        text = text[:-3].strip()

    # 查找 ## 基本信息（不缩进的，在行首）
    match = re.search(r'^##\s+基本信息', text, re.MULTILINE)
    if match:
        text = text[match.start():]
    else:
        # 退而求其次：找任何位置的 ## 基本信息
        match = re.search(r'##\s+基本信息', text)
        if match:
            text = text[match.start():]

    return text.strip()


def remove_compressed_duplicates(text: str) -> str:
    """删除源数据中的压缩重复段落。"""
    lines = text.split('\n')
    result = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if len(stripped) <= 1000:
            result.append(line)
            continue
        if stripped.startswith(('http', '[', '- [')):
            result.append(line)
            continue
        sample = stripped[:300]
        sents = re.split(r'[。；;，,\s]', sample)
        sents = [s.strip() for s in sents if len(s.strip()) >= 6]
        if len(sents) < 3:
            result.append(line)
            continue
        remaining = '\n'.join(lines[i+1:])
        matches = sum(1 for s in sents if s in remaining)
        if matches / len(sents) >= 0.5:
            continue
        result.append(line)
    return '\n'.join(result)


def discover_groups(data_dir: str):
    """扫描扁平目录结构：一级子文件夹内直接放.md文件。"""
    groups = []
    data_path = Path(data_dir)
    if not data_path.exists():
        print(f"❌ 数据目录不存在：{data_dir}")
        return groups
    for topic_dir in sorted(data_path.iterdir()):
        if not topic_dir.is_dir():
            continue
        md_files = sorted([str(f) for f in topic_dir.glob("*.md") if f.is_file()])
        if not md_files:
            print(f"⚠️  跳过 {topic_dir.name}：无 .md 文件")
            continue
        groups.append({
            "name": topic_dir.name,
            "topic_path": str(topic_dir),
            "md_files": md_files,
        })
    return groups


def build_user_prompt(files_contents: list, topic_name: str) -> str:
    parts = []
    parts.append(f"请对政策分组「{topic_name}」下的 {len(files_contents)} 个MD文件进行七大板块结构化融合。\n")
    parts.append("=" * 60)
    for i, (path, content) in enumerate(files_contents, 1):
        parts.append(f"\n--- 文件 {i}：{os.path.basename(path)} ---")
        parts.append(content)
    parts.append("\n" + "=" * 60)
    parts.append("请严格按七大板块规则输出完整Markdown文档。首行必须是 ## 基本信息，禁止包含任何思考过程。")
    return "\n".join(parts)


def process_one_group(group, system_prompt, api_url, api_key, model):
    topic_name = group["name"]
    print(f"\n📂 {topic_name}")
    print(f"   文件数：{len(group['md_files'])}")
    for f in group['md_files']:
        print(f"      📄 {os.path.basename(f)}")

    # 读取所有文件并去重
    files_contents = []
    for p in group['md_files']:
        content = read_file_safe(p)
        content = remove_compressed_duplicates(content)
        files_contents.append((p, content))

    user_prompt = build_user_prompt(files_contents, topic_name)
    print(f"   📤 调用 LLM（{model}），提示词 {len(user_prompt)} 字符...")

    merged = call_llm(api_url, system_prompt, user_prompt, model, api_key)
    merged = clean_output(merged)

    # 写入到子文件夹内，文件名为子文件夹名
    out_path = os.path.join(group["topic_path"], f"{topic_name}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(merged)

    print(f"   ✅ → {out_path} ({len(merged)} 字符)")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="政策文件批量融合 v2（七大板块）")
    parser.add_argument("--data_dir", default=r"D:\kejiju\metadata\重点政策融合数据")
    parser.add_argument("--prompt", default=r"D:\kejiju\metadata\prompts\ronghe.md")
    parser.add_argument("--api_url", default=None)
    parser.add_argument("--api_key", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    cfg_url, cfg_key, cfg_model = _load_settings()
    api_url = args.api_url or cfg_url
    api_key = args.api_key or cfg_key
    model = args.model or cfg_model or "qwen-plus"

    if not api_url:
        print("❌ 未配置 API 地址")
        sys.exit(1)
    print(f"🔗 API：{api_url}")
    print(f"🤖 模型：{model}")
    print(f"🔑 Key：{'已配置' if api_key else '未配置'}")

    with open(args.prompt, "r", encoding="utf-8") as f:
        system_prompt = f.read().strip()
    print(f"✅ 提示词：{args.prompt}（{len(system_prompt)} 字符）")

    groups = discover_groups(args.data_dir)
    if not groups:
        print("❌ 无有效分组")
        sys.exit(1)
    print(f"\n🔍 共 {len(groups)} 个分组：")
    for g in groups:
        print(f"   • {g['name']}（{len(g['md_files'])} 个文件）")

    results = []
    t0 = time.time()
    for idx, group in enumerate(groups, 1):
        print(f"\n[{idx}/{len(groups)}]", end="")
        out = process_one_group(group, system_prompt, api_url, api_key, model)
        results.append({"group": group["name"], "output": out})

    print(f"\n{'═' * 60}")
    print(f"🎉 完成！耗时 {time.time()-t0:.1f}s")
    for r in results:
        print(f"   ✅ {r['group']} → {r['output']}")


if __name__ == "__main__":
    main()
