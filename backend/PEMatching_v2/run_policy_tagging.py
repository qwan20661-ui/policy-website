# -*- coding: utf-8 -*-
"""批量政策打标：增量指纹 + 并发 + 复核表。

产出 config/policy_tags.json 与 output/policy_tags_review.xlsx。

指纹 = sha256(fusionPolicyId + updateTime + prompt文件mtime + taxonomy版本)
四要素的理由：政策变了要重打；prompt 改了打标口径变了要重打；
taxonomy 加了新标签也要重打。

用法：
    python backend/PEMatching_v2/run_policy_tagging.py                  # 增量
    python backend/PEMatching_v2/run_policy_tagging.py --force          # 全量重打
    python backend/PEMatching_v2/run_policy_tagging.py --ids 6548,6613  # 只打指定几条
    python backend/PEMatching_v2/run_policy_tagging.py --limit 5        # 只打前 5 条
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from service import fields as F
from service import taxonomy as tx
from service.paths import (
    OUTPUT_DIR,
    POLICY_TAGS_PATH,
    atomic_write_json,
    ensure_dir,
    env_bool,
    env_int,
)
from service.policy_sync import load_snapshot
from service.tagging import PROMPT_PATH, TaggingError, tag_policy

TAGS_SCHEMA = "pematching-v2-policy-tags-v1"
# 每 N 条落盘一次，中断可续（万级规模时这一条是必需的）
FLUSH_EVERY = 20


def _force_applicable() -> bool:
    """审核通过的政策是否一律视为可申报（忽略打标模型的 applicable:false）。

    打标只针对审核通过的主政策；这些政策本就面向企业申报或企业必须履行，
    不应因受众为个人/机构或"无资金奖补"被排除出推荐。可用
    PEMATCHING_V2_FORCE_APPLICABLE=false 关闭（保留模型原始判定）。
    """
    return env_bool("PEMATCHING_V2_FORCE_APPLICABLE", True)


def _fingerprint(policy: Dict[str, Any]) -> str:
    prompt_mtime = str(int(PROMPT_PATH.stat().st_mtime)) if PROMPT_PATH.exists() else "0"
    taxonomy_version = F.text(tx.load_taxonomy().get("schemaVersion"))
    raw = "|".join([
        F.text(policy.get("fusionPolicyId")),
        F.text(policy.get("updateTime")),
        prompt_mtime,
        taxonomy_version,
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _load_existing() -> Dict[str, Dict[str, Any]]:
    if not POLICY_TAGS_PATH.exists():
        return {}
    try:
        payload = json.loads(POLICY_TAGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if payload.get("schemaVersion") != TAGS_SCHEMA:
        return {}
    return {
        F.text(item.get("fusionPolicyId")): item
        for item in (payload.get("data") or [])
        if isinstance(item, dict) and F.text(item.get("fusionPolicyId"))
    }


def _write_tags(records: Dict[str, Dict[str, Any]], failures: List[Dict[str, Any]]) -> None:
    ordered = sorted(records.values(), key=lambda r: F.text(r.get("fusionPolicyId")))
    atomic_write_json(POLICY_TAGS_PATH, {
        "schemaVersion": TAGS_SCHEMA,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "count": len(ordered),
        "failureCount": len(failures),
        "data": ordered,
    })
    if failures:
        atomic_write_json(OUTPUT_DIR / "tagging_failures.json", {
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "count": len(failures),
            "data": failures,
        })


def _select_policies(
    policies: List[Dict[str, Any]],
    existing: Dict[str, Dict[str, Any]],
    force: bool,
    ids: Optional[List[str]],
    limit: Optional[int],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """返回 (待打标, 跳过的 ID)。"""
    if ids:
        wanted = set(ids)
        policies = [p for p in policies if F.text(p.get("fusionPolicyId")) in wanted]
    if limit:
        policies = policies[:limit]

    pending: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for policy in policies:
        policy_id = F.text(policy.get("fusionPolicyId"))
        if force:
            pending.append(policy)
            continue
        previous = existing.get(policy_id)
        if previous and F.text(previous.get("_fingerprint")) == _fingerprint(policy):
            skipped.append(policy_id)
            continue
        pending.append(policy)
    return pending, skipped


def _summarize(records: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    confidence: Dict[str, Counter] = {d: Counter() for d in "ABCDEF"}
    a_primary: Counter = Counter()
    c_major: Counter = Counter()
    c_sub: Counter = Counter()
    d_levels: Counter = Counter()
    window_sources: Counter = Counter()
    audiences: Counter = Counter()
    not_applicable: List[str] = []
    warnings = 0

    for record in records.values():
        applicability = record.get("applicability") or {}
        audiences[F.text(applicability.get("audience")) or "（缺）"] += 1
        if applicability.get("applicable") is False:
            not_applicable.append(
                f"{F.text(record.get('fusionPolicyId'))} {F.text(record.get('title'))[:26]}"
            )
        tags = record.get("tags") or {}
        for dimension in "ABCDEF":
            entry = tags.get(dimension) or {}
            confidence[dimension][F.text(entry.get("confidence")) or "（缺）"] += 1
        a_primary[F.text((tags.get("A") or {}).get("primary")) or "（缺）"] += 1
        for value in (tags.get("C") or {}).get("values") or []:
            c_major[str(value)] += 1
        for value in (tags.get("C") or {}).get("subValues") or []:
            c_sub[str(value)] += 1
        d_levels[F.text((tags.get("D") or {}).get("value")) or "（缺）"] += 1
        window_sources[F.text((record.get("applicationWindow") or {}).get("source")) or "（缺）"] += 1
        warnings += len(record.get("warnings") or [])

    return {
        "confidence": {d: dict(c) for d, c in confidence.items()},
        "aPrimary": dict(a_primary.most_common()),
        "cMajor": dict(c_major.most_common()),
        "cSub": dict(c_sub.most_common()),
        "dLevels": dict(d_levels.most_common()),
        "windowSources": dict(window_sources.most_common()),
        "audiences": dict(audiences.most_common()),
        "notApplicable": sorted(not_applicable),
        "warningCount": warnings,
    }


def export_review_excel(records: Dict[str, Dict[str, Any]]) -> Path:
    """复核表。置信度 low 标黄，申报期缺失标灰。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "政策标签复核"
    headers = [
        "政策ID", "标题", "可申报", "受众", "适用性依据",
        "A主", "A次", "A置信度", "A依据",
        "B值", "B置信度", "B依据",
        "C大类", "C子类", "C置信度", "C依据",
        "D", "D置信度", "D依据", "E", "F",
        "职工下限", "职工上限", "营收下限(万元)", "营收上限(万元)",
        "规模置信度", "规模依据",
        "申报开始", "申报结束", "长期受理", "申报期来源", "申报期依据",
        "链路前置", "链路后继", "标题关键词", "警告", "备注", "复核意见",
    ]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    low_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
    gray_fill = PatternFill(start_color="EFEFEF", end_color="EFEFEF", fill_type="solid")
    red_fill = PatternFill(start_color="FFD9D9", end_color="FFD9D9", fill_type="solid")

    for record in sorted(records.values(), key=lambda r: F.text(r.get("fusionPolicyId"))):
        tags = record.get("tags") or {}
        window = record.get("applicationWindow") or {}
        chain = record.get("growthChain") or {}
        keyword_index = record.get("keywordIndex") or {}
        scale = record.get("scaleThresholds") or {}

        def entry(dimension: str) -> Dict[str, Any]:
            return tags.get(dimension) or {}

        applicability = record.get("applicability") or {}
        sheet.append([
            F.text(record.get("fusionPolicyId")),
            F.text(record.get("title")),
            "否" if applicability.get("applicable") is False else "是",
            F.text(applicability.get("audience")),
            F.text(applicability.get("reason")),
            F.text(entry("A").get("primary")),
            "、".join(str(v) for v in (entry("A").get("secondary") or [])),
            F.text(entry("A").get("confidence")),
            F.text(entry("A").get("basis")),
            "、".join(str(v) for v in (entry("B").get("values") or [])),
            F.text(entry("B").get("confidence")),
            F.text(entry("B").get("basis")),
            "、".join(str(v) for v in (entry("C").get("values") or [])),
            "、".join(str(v) for v in (entry("C").get("subValues") or [])),
            F.text(entry("C").get("confidence")),
            F.text(entry("C").get("basis")),
            F.text(entry("D").get("value")),
            F.text(entry("D").get("confidence")),
            F.text(entry("D").get("basis")),
            F.text(entry("E").get("value")),
            F.text(entry("F").get("value")),
            scale.get("employeeMin"),
            scale.get("employeeMax"),
            scale.get("revenueMinWan"),
            scale.get("revenueMaxWan"),
            F.text(scale.get("confidence")),
            F.text(scale.get("basis")),
            F.text(window.get("startDate")),
            F.text(window.get("endDate")),
            "是" if window.get("isRolling") else "",
            F.text(window.get("source")),
            F.text(window.get("basis")),
            F.text(chain.get("prerequisite")),
            F.text(chain.get("nextStage")),
            "、".join(str(v) for v in (keyword_index.get("title") or [])),
            "；".join(record.get("warnings") or []),
            F.text(record.get("notes")),
            "",
        ])

        row = sheet.max_row
        # 不可申报的整行标红——这批会被检索排除，优先复核
        if applicability.get("applicable") is False:
            for cell in sheet[row]:
                cell.fill = red_fill
        elif any(F.text(entry(d).get("confidence")) == "low" for d in "ABCD"):
            for cell in sheet[row]:
                cell.fill = low_fill
        elif not (window.get("startDate") or window.get("endDate") or window.get("isRolling")):
            for cell in sheet[row]:
                cell.fill = gray_fill

    widths = [10, 34, 8, 10, 40, 8, 10, 10, 46, 12, 10, 46, 12, 12, 10, 46, 8, 10, 46,
              8, 8, 12, 12, 16, 16, 10, 46, 12, 12, 10, 16, 40, 16, 16, 28, 30, 24, 20]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
    sheet.freeze_panes = "C2"

    ensure_dir(OUTPUT_DIR)
    target = OUTPUT_DIR / "policy_tags_review.xlsx"
    workbook.save(target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="批量政策打标")
    parser.add_argument("--force", action="store_true", help="全量重打，忽略指纹")
    parser.add_argument("--ids", default=None, help="只打指定政策ID，逗号分隔")
    parser.add_argument("--limit", type=int, default=None, help="只打前 N 条")
    parser.add_argument("--workers", type=int, default=None, help="并发数，默认 V2_MAX_WORKERS")
    parser.add_argument("--review-only", action="store_true", help="只用现有产出导出复核表")
    args = parser.parse_args()

    try:
        snapshot = load_snapshot()
    except (FileNotFoundError, ValueError) as exc:
        print(exc)
        return 1

    policies = [p for p in (snapshot.get("data") or []) if isinstance(p, dict)]
    existing = _load_existing()

    if args.review_only:
        if not existing:
            print("尚无打标产出，无法导出复核表")
            return 1
        print(f"复核表：{export_review_excel(existing)}")
        return 0

    ids = [i.strip() for i in args.ids.split(",") if i.strip()] if args.ids else None
    pending, skipped = _select_policies(policies, existing, args.force, ids, args.limit)

    print(f"政策总数 {len(policies)}　待打标 {len(pending)}　跳过（指纹未变）{len(skipped)}")
    if not pending:
        print("无需打标。")
        if existing:
            print(f"复核表：{export_review_excel(existing)}")
        return 0

    workers = args.workers or env_int("V2_MAX_WORKERS", 20)
    records = dict(existing)
    failures: List[Dict[str, Any]] = []
    done = 0
    started = time.time()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(tag_policy, policy): policy for policy in pending}
        for future in as_completed(futures):
            policy = futures[future]
            policy_id = F.text(policy.get("fusionPolicyId"))
            done += 1
            try:
                result = future.result()
            except TaggingError as exc:
                # 单条失败不中断整批；失败条目不写入，保留上一次结果（如果有）
                failures.append({
                    "fusionPolicyId": policy_id,
                    "title": F.text(policy.get("title")),
                    "error": str(exc)[:800],
                })
                print(f"[{done}/{len(pending)}] ✗ {policy_id} {F.text(policy.get('title'))[:24]} 失败")
                continue
            except Exception as exc:  # 防御：未预期异常也不中断整批
                failures.append({
                    "fusionPolicyId": policy_id,
                    "title": F.text(policy.get("title")),
                    "error": f"{type(exc).__name__}: {exc}"[:800],
                })
                print(f"[{done}/{len(pending)}] ✗ {policy_id} 异常 {type(exc).__name__}")
                continue

            result["_fingerprint"] = _fingerprint(policy)
            result["_taggedAt"] = datetime.now().isoformat(timespec="seconds")
            # 打标只针对审核通过的政策；审核通过即视为可申报，避免因受众/无奖补被误判排除。
            # 保留 audience 与 reason 供人工复核，但 applicable 强制为 true（可用环境变量关闭）。
            if _force_applicable():
                applicability = result.get("applicability")
                if isinstance(applicability, dict) and applicability.get("applicable") is False:
                    applicability["applicable"] = True
                    applicability["reason"] = (applicability.get("reason") or "") + "；审核通过的政策视为可申报"
            records[policy_id] = result
            flag = "⚠" if result.get("warnings") else "✓"
            print(f"[{done}/{len(pending)}] {flag} {policy_id} {F.text(result.get('title'))[:24]}"
                  f"  A={(result['tags'].get('A') or {}).get('primary')}"
                  f" C={(result['tags'].get('C') or {}).get('values')}"
                  f" D={(result['tags'].get('D') or {}).get('value')}")

            # 分批落盘，中断可续
            if done % FLUSH_EVERY == 0:
                _write_tags(records, failures)

    _write_tags(records, failures)
    elapsed = time.time() - started

    print()
    print(f"完成：成功 {len(pending) - len(failures)}　失败 {len(failures)}　"
          f"跳过 {len(skipped)}　耗时 {elapsed:.0f} 秒")
    print(f"产出：{POLICY_TAGS_PATH}")
    if failures:
        print(f"失败清单：{OUTPUT_DIR / 'tagging_failures.json'}")

    stats = _summarize(records)
    print()
    print("各维度置信度分布：")
    for dimension in "ABCDEF":
        parts = " ".join(f"{k}={v}" for k, v in sorted(stats["confidence"][dimension].items()))
        print(f"  {dimension}: {parts}")
    print(f"\nA 主标签分布：{stats['aPrimary']}")
    print(f"C 大类分布：{stats['cMajor']}")
    print(f"C 子类分布：{stats['cSub']}")
    print(f"D 门槛分布：{stats['dLevels']}")
    print(f"申报期来源：{stats['windowSources']}")
    print(f"受众分布：{stats['audiences']}")
    print(f"警告条目数：{stats['warningCount']}")
    if stats["notApplicable"]:
        print(f"\n判定为不可申报的政策 {len(stats['notApplicable'])} 条（检索时排除，复核表标红）：")
        for line in stats["notApplicable"]:
            print(f"  {line}")

    print(f"\n复核表：{export_review_excel(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
