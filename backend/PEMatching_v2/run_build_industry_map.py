# -*- coding: utf-8 -*-
"""生成国标行业 → C1-C11 映射表（一次性离线任务）。

企业侧行业字段是国标行业分类（统计口径），C 维度是高企领域口径，两套编码不通用。
本脚本扫样本统计出实际出现的行业取值（封闭集合），交 LLM 按 tag_taxonomy.json
的 C 维度定义产出映射，人工复核后固化。运行时只读表，不调模型。

用法：
    python backend/PEMatching_v2/run_build_industry_map.py --dry-run   # 只统计不调模型
    python backend/PEMatching_v2/run_build_industry_map.py             # 生成映射表
    python backend/PEMatching_v2/run_build_industry_map.py --review    # 只导出复核表
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from service import taxonomy as tx
from service.llm_client import LLMError, chat_with_retry, extract_json_content
from service.paths import (
    INDUSTRY_MAP_PATH,
    OUTPUT_DIR,
    atomic_write_json,
    ensure_dir,
    sample_enterprise_dir,
)


def collect_industry_values(sample_dir: Path) -> Tuple[Counter, Counter, int, int]:
    """扫样本目录，统计 industryLevel1 / industryLevel2 的取值分布。

    返回 (level1 计数, level2 计数, 扫描文件数, 有行业值的文件数)。
    """
    level1: Counter = Counter()
    level2: Counter = Counter()
    scanned = 0
    with_industry = 0

    for path in sorted(sample_dir.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as handle:
                record = json.load(handle)
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(record, dict) or "industryLevel1" not in record:
            continue
        scanned += 1
        first = str(record.get("industryLevel1") or "").strip()
        second = str(record.get("industryLevel2") or "").strip()
        if first or second:
            with_industry += 1
        if first:
            level1[first] += 1
        if second:
            level2[second] += 1

    return level1, level2, scanned, with_industry


def _build_prompt(level1: Counter, level2: Counter) -> List[Dict[str, str]]:
    dimension = tx.load_taxonomy()["dimensions"]["C"]
    majors = "\n".join(f"  {code}：{name}" for code, name in dimension["values"].items())
    subs = "\n".join(
        f"  {code}：{spec['name']}（属 {spec['parent']}）"
        for code, spec in dimension["subValues"].items()
    )
    l1_lines = "\n".join(f"  {value}（{count} 家）" for value, count in level1.most_common())
    l2_lines = "\n".join(f"  {value}（{count} 家）" for value, count in level2.most_common())

    system = (
        "你是科技政策匹配系统的数据标注专家，负责把国民经济行业分类（国标）映射到"
        "《高新技术企业认定管理办法》国家重点支持的高新技术领域编号。\n\n"
        "硬性要求：\n"
        "1. cTags 只能填下面给出的 C 编号，禁止输出行业名称、禁止自造编号。\n"
        "2. 一个行业可对应多个领域，按相关性排序，最多 3 个。\n"
        "3. 若该行业整体不属于高企八大领域（如传统建筑、传统纺织、餐饮零售、"
        "普通教育），cTags 填空数组 []，表示落 C9 通用兜底。**不要硬凑一个领域。**\n"
        "4. confidence 三档：high 表示该行业绝大多数企业都属该领域；"
        "medium 表示多数属但有例外；low 表示只有部分属、需靠企业文本进一步判断。\n"
        "5. reason 用一句中文说明依据，必填。\n"
        "6. 口径过宽无法归类的（如「其他制造业」「专业技术服务业」），"
        "cTags 填 [] 且 confidence 填 low。\n"
    )

    user = (
        f"C 维度大类编号：\n{majors}\n\n"
        f"C 维度子类编号（仅供理解粒度，本次映射只需产出大类）：\n{subs}\n\n"
        f"需要映射的一级行业（industryLevel1）取值：\n{l1_lines}\n\n"
        f"需要映射的二级行业（industryLevel2）取值：\n{l2_lines}\n\n"
        "注意：源数据存在把一级行业值误填进二级字段的情况（如二级出现"
        "「M 科学研究和技术服务业」），这类按其一级口径映射。\n\n"
        "输出 JSON，结构如下，不要输出任何解释文字：\n"
        '{"byLevel1": {"行业取值": {"cTags": ["C1"], "confidence": "high", "reason": "…"}}, '
        '"byLevel2": {"行业取值": {"cTags": ["C2"], "confidence": "high", "reason": "…"}}}'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _validate_mapping(mapping: Dict[str, Any], level1: Counter, level2: Counter) -> List[str]:
    errors: List[str] = []
    for key, expected in (("byLevel1", level1), ("byLevel2", level2)):
        table = mapping.get(key)
        if not isinstance(table, dict):
            errors.append(f"缺少 {key} 段")
            continue
        missing = set(expected) - set(table)
        if missing:
            errors.append(f"{key} 漏了 {len(missing)} 个取值：{sorted(missing)[:5]}")
        for value, entry in table.items():
            if not isinstance(entry, dict):
                errors.append(f"{key}.{value} 不是对象")
                continue
            tags = entry.get("cTags")
            if not isinstance(tags, list):
                errors.append(f"{key}.{value}.cTags 必须是数组")
                continue
            for tag in tags:
                if not tx.is_valid_tag("C", str(tag)):
                    errors.append(f"{key}.{value}.cTags 含非法编号：{tag!r}")
            if entry.get("confidence") not in ("high", "medium", "low"):
                errors.append(f"{key}.{value}.confidence 非法：{entry.get('confidence')!r}")
            if not str(entry.get("reason") or "").strip():
                errors.append(f"{key}.{value}.reason 不能为空")
    return errors


def generate_mapping(level1: Counter, level2: Counter, max_retries: int = 2) -> Dict[str, Any]:
    """调 LLM 产出映射，带校验与重试。"""
    messages = _build_prompt(level1, level2)

    for attempt in range(max_retries + 1):
        message = chat_with_retry(
            messages,
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        parsed = extract_json_content(message)
        if parsed is None:
            errors = ["响应不是合法 JSON"]
        elif not isinstance(parsed, dict):
            errors = ["响应根节点必须是对象"]
        else:
            errors = _validate_mapping(parsed, level1, level2)

        if not errors:
            return parsed

        print(f"[第 {attempt + 1} 次] 校验未通过：")
        for error in errors[:10]:
            print(f"  - {error}")
        if attempt >= max_retries:
            raise LLMError(f"重试 {max_retries} 次后仍未产出合法映射：{errors[:3]}")

        # 把校验错误回传给模型，比原样重试有效
        messages = messages + [
            {"role": "assistant", "content": json.dumps(parsed or {}, ensure_ascii=False)},
            {
                "role": "user",
                "content": "上一次输出有以下问题，请修正后重新输出完整 JSON：\n"
                + "\n".join(f"- {e}" for e in errors[:20]),
            },
        ]

    raise LLMError("未能产出合法映射")


def export_review_excel(mapping: Dict[str, Any], level1: Counter, level2: Counter) -> Path:
    """导出复核表。这张表决定领域匹配度 30 分的判定，映射错了分数就错。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "行业映射复核"
    headers = ["层级", "国标行业取值", "样本企业数", "C 标签", "领域名称", "置信度", "映射依据", "复核意见"]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    names = tx.load_taxonomy()["dimensions"]["C"]["values"]
    low_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
    generic_fill = PatternFill(start_color="EFEFEF", end_color="EFEFEF", fill_type="solid")

    for level_name, key, counter in (("二级", "byLevel2", level2), ("一级", "byLevel1", level1)):
        table = mapping.get(key) or {}
        for value, count in counter.most_common():
            entry = table.get(value) or {}
            tags = entry.get("cTags") or []
            row = [
                level_name,
                value,
                count,
                "、".join(tags) if tags else "（落 C9 通用）",
                "、".join(names.get(t, t) for t in tags) if tags else "通用",
                entry.get("confidence") or "",
                entry.get("reason") or "",
                "",
            ]
            sheet.append(row)
            if entry.get("confidence") == "low":
                for cell in sheet[sheet.max_row]:
                    cell.fill = low_fill
            elif not tags:
                for cell in sheet[sheet.max_row]:
                    cell.fill = generic_fill

    for column, width in zip("ABCDEFGH", (8, 40, 12, 14, 22, 10, 60, 20)):
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = "A2"

    ensure_dir(OUTPUT_DIR)
    target = OUTPUT_DIR / "industry_map_review.xlsx"
    workbook.save(target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="生成国标行业 → C1-C11 映射表")
    parser.add_argument("--dry-run", action="store_true", help="只统计行业取值，不调模型")
    parser.add_argument("--review", action="store_true", help="只用现有映射表导出复核 Excel")
    parser.add_argument("--dir", default=None, help="企业样本目录，默认取 V2_SAMPLE_ENTERPRISE_DIR")
    args = parser.parse_args()

    sample_dir = Path(args.dir) if args.dir else sample_enterprise_dir()
    if not sample_dir.exists():
        print(f"样本目录不存在：{sample_dir}")
        return 1

    level1, level2, scanned, with_industry = collect_industry_values(sample_dir)
    ratio = with_industry * 100.0 / scanned if scanned else 0.0
    print(f"样本目录：{sample_dir}")
    print(f"扫描 {scanned} 份，其中 {with_industry} 份有行业值（{ratio:.1f}%）")
    print(f"industryLevel1 取值 {len(level1)} 种，industryLevel2 取值 {len(level2)} 种")

    if args.dry_run:
        print("\n=== industryLevel1 ===")
        for value, count in level1.most_common():
            print(f"  {count:>5}  {value}")
        print("\n=== industryLevel2 ===")
        for value, count in level2.most_common():
            print(f"  {count:>5}  {value}")
        return 0

    if args.review:
        mapping = tx.load_industry_map()
    else:
        print("\n调用 LLM 生成映射…")
        mapping = generate_mapping(level1, level2)
        existing_review = {}
        if INDUSTRY_MAP_PATH.exists():
            try:
                existing_review = json.loads(INDUSTRY_MAP_PATH.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing_review = {}
        payload = {
            "schemaVersion": tx.INDUSTRY_MAP_SCHEMA,
            "note": existing_review.get(
                "note",
                "国标行业分类（统计口径）→ C1-C11 高企领域口径。两套编码不通用，必须过表转换。",
            ),
            "generatedBy": f"llm:{__import__('os').getenv('V2_MODEL_NAME', 'minimax2.7')}",
            "reviewStatus": "待业务复核",
            "sampleSize": scanned,
            "byLevel1": mapping.get("byLevel1") or {},
            "byLevel2": mapping.get("byLevel2") or {},
        }
        atomic_write_json(INDUSTRY_MAP_PATH, payload)
        tx.reset_caches()
        mapping = payload
        print(f"已写入 {INDUSTRY_MAP_PATH}")

    target = export_review_excel(mapping, level1, level2)
    print(f"复核表：{target}")

    generic_count = sum(
        1
        for table in (mapping.get("byLevel1") or {}, mapping.get("byLevel2") or {})
        for entry in table.values()
        if not (entry.get("cTags") or [])
    )
    print(f"\n落 C9 通用的行业取值：{generic_count} 个（这部分企业领域匹配度只能得 10 分）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
