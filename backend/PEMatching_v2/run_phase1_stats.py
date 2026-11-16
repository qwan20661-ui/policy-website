# -*- coding: utf-8 -*-
"""一期统计：跑全量样本，产出字段填充率、规则三态分布、标签分布、分数分布。

这份统计是四期阈值校准的输入，不是可选项。最关键的一张表是「分组分布」——
对比有行业值与无行业值两组的基础分分布，验证归一化是否真的拉平了信息量差异。
若两组中位数差异过大，说明归一化不足，需在四期引入信息量惩罚或调权重。

用法：
    python backend/PEMatching_v2/run_phase1_stats.py --limit 50   # 小样本验证流程
    python backend/PEMatching_v2/run_phase1_stats.py              # 全量
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from service import fields as F
from service import taxonomy as tx
from service.paths import OUTPUT_DIR, ensure_dir, sample_enterprise_dir
from service.profile import build_profile
from service.rules import evaluate_rules, load_rules
from service.scoring import BAND_NOT_EVALUABLE, score_policy

# 一期没有真实政策标签（二期才由 LLM 产出），用三条覆盖不同口径的样例政策算分，
# 目的是看分数分布形态而非绝对值。
SAMPLE_POLICIES = [
    {
        "label": "通用普惠类（C9 / D1）",
        "tags": {
            "A": {"primary": "A1", "confidence": "high", "basis": "资质认定"},
            "B": {"values": ["B1", "B2"], "confidence": "high", "basis": "初创成长期"},
            "C": {"values": ["C9"], "subValues": [], "confidence": "high", "basis": "通用行业"},
            "D": {"value": "D1", "confidence": "high", "basis": "入库即享"},
        },
        "applicationWindow": {"startDate": "2026-09-01", "endDate": "2026-11-30", "isRolling": False},
        "keywordIndex": {"title": [], "aKeywords": [], "cKeywords": []},
    },
    {
        "label": "电子信息专项（C1 + C1.3 子类 / D2）",
        "tags": {
            "A": {"primary": "A3", "confidence": "high", "basis": "资金奖补"},
            "B": {"values": ["B2", "B3"], "confidence": "high", "basis": "成长成熟期"},
            "C": {"values": ["C1"], "subValues": ["C1.3"], "confidence": "high", "basis": "软件"},
            "D": {"value": "D2", "confidence": "high", "basis": "需资质"},
        },
        "applicationWindow": {"startDate": "2026-07-01", "endDate": "2026-09-30", "isRolling": False},
        "keywordIndex": {"title": [], "aKeywords": [], "cKeywords": []},
    },
    {
        "label": "生物医药专项（C2 / D3 / 有规模门槛）",
        "tags": {
            "A": {"primary": "A3", "confidence": "high", "basis": "资金奖补"},
            "B": {"values": ["B3"], "confidence": "high", "basis": "成熟期"},
            "C": {"values": ["C2"], "subValues": [], "confidence": "high", "basis": "生物医药"},
            "D": {"value": "D3", "confidence": "high", "basis": "竞争立项"},
        },
        "scaleThresholds": {"employeeMin": 50, "revenueMinWan": 2000},
        "applicationWindow": {"startDate": "2025-03-01", "endDate": "2025-05-31", "isRolling": False},
        "keywordIndex": {"title": [], "aKeywords": [], "cKeywords": []},
    },
]

TRACKED_FIELDS = [
    "registerDate", "registerAddress", "registerType", "registerDistrict",
    "nationalHighTechZone", "registerCapital", "industryLevel1", "industryLevel2",
    "industryField", "highTechField", "brief", "productServiceIntro", "enterpriseScale",
    "creditCode", "ipClass1Count", "ipClass2Count", "ipCount", "inventionPatentCount",
    "ipTotal", "employeeCount", "operatingIncome", "revenueWan", "rdExpense",
    "techContractAmount", "enterpriseCredit", "doctorCount", "masterCount",
    "newRecruitCount", "collegeGraduateCount", "netAssets", "totalAssets",
    "hasRndAccount", "hasRndInstitution", "industryUniversityCoop",
    "isTechSme", "isInnovativeSme", "isHighTechEnterprise", "isSpecializedSme",
    "isSpecializedLittleGiant", "isTechLeaderEnterprise", "isInnovativeLeader",
    "cTags", "bTags", "regions", "cSubTagTextSource",
]


def _percentiles(values: List[float]) -> Dict[str, Optional[float]]:
    if not values:
        return {k: None for k in ("count", "min", "p10", "p25", "p50", "p75", "p90", "max", "mean")}
    ordered = sorted(values)

    def pick(ratio: float) -> float:
        index = min(int(ratio * (len(ordered) - 1)), len(ordered) - 1)
        return round(ordered[index], 2)

    return {
        "count": len(ordered),
        "min": round(ordered[0], 2),
        "p10": pick(0.10),
        "p25": pick(0.25),
        "p50": pick(0.50),
        "p75": pick(0.75),
        "p90": pick(0.90),
        "max": round(ordered[-1], 2),
        "mean": round(statistics.fmean(ordered), 2),
    }


def collect(sample_dir: Path, limit: Optional[int], today: date) -> Dict[str, Any]:
    field_available: Counter = Counter()
    rule_states: Dict[str, Counter] = defaultdict(Counter)
    skip_reasons: Dict[str, Counter] = defaultdict(Counter)
    c_major: Counter = Counter()
    c_strong: Counter = Counter()
    c_weak: Counter = Counter()
    b_tags: Counter = Counter()
    b_signals: Counter = Counter()
    regions: Counter = Counter()
    bands: Dict[str, Counter] = defaultdict(Counter)
    effective_weights: Counter = Counter()
    scores_by_policy: Dict[str, List[float]] = defaultdict(list)
    scores_with_industry: Dict[str, List[float]] = defaultdict(list)
    scores_without_industry: Dict[str, List[float]] = defaultdict(list)
    blocked = 0
    total = 0

    paths = sorted(sample_dir.glob("*.json"))
    if limit:
        paths = paths[:limit]

    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(record, dict) or "industryLevel1" not in record:
            continue
        if not F.text(record.get("enterpriseName")):
            continue

        profile = build_profile(record, today=today)
        total += 1

        availability = profile["fieldAvailability"]
        for field in TRACKED_FIELDS:
            if availability.get(field) == F.AVAILABLE:
                field_available[field] += 1

        tags = profile["tags"]
        for tag in tags["C"]:
            c_major[tag] += 1
        for tag in tags["cSubStrong"]:
            c_strong[tag] += 1
        for tag in tags["cSubWeak"]:
            c_weak[tag] += 1
        for tag in tags["B"]:
            b_tags[tag] += 1
        for tag in tags["regions"]:
            regions[tag] += 1
        for item in profile["tagBasis"]["B"]:
            b_signals[f"{item['stage']}×{item['signalCount']}信号"] += 1

        rule_result = evaluate_rules(profile, enable_stage2=True)
        if not rule_result["gateResult"]["passed"]:
            blocked += 1
            rule_states["R00"]["matched"] += 1
            continue
        rule_states["R00"]["skipped" if not rule_result["gateResult"]["creditChecked"] else "unmatched"] += 1

        for channel_data in rule_result["channels"].values():
            if not channel_data.get("executed"):
                continue
            for item in channel_data["matched"]:
                rule_states[item["ruleId"]]["matched"] += 1
            for item in channel_data["unmatched"]:
                rule_states[item["ruleId"]]["unmatched"] += 1
            for item in channel_data["skipped"]:
                rule_states[item["ruleId"]]["skipped"] += 1
                for field in item["missingFields"]:
                    skip_reasons[item["ruleId"]][field] += 1

        has_industry = availability.get("cTags") == F.AVAILABLE
        for policy in SAMPLE_POLICIES:
            label = policy["label"]
            result = score_policy(
                profile, policy, today=today, chain_stages=rule_result["chainStages"]
            )
            bands[label][result["band"]] += 1
            if result["band"] == BAND_NOT_EVALUABLE:
                continue
            effective_weights[result["baseDetail"]["effectiveWeight"]] += 1
            scores_by_policy[label].append(result["baseScore"])
            bucket = scores_with_industry if has_industry else scores_without_industry
            bucket[label].append(result["baseScore"])

    return {
        "total": total,
        "blocked": blocked,
        "fieldAvailable": field_available,
        "ruleStates": rule_states,
        "skipReasons": skip_reasons,
        "cMajor": c_major,
        "cStrong": c_strong,
        "cWeak": c_weak,
        "bTags": b_tags,
        "bSignals": b_signals,
        "regions": regions,
        "bands": bands,
        "effectiveWeights": effective_weights,
        "scoresByPolicy": scores_by_policy,
        "scoresWithIndustry": scores_with_industry,
        "scoresWithoutIndustry": scores_without_industry,
    }


def _write_sheet(workbook, title: str, headers: List[str], rows: List[List[Any]]) -> None:
    from openpyxl.styles import Font

    sheet = workbook.create_sheet(title)
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(row)
    for index, header in enumerate(headers, start=1):
        width = max(12, min(52, len(str(header)) * 2 + 8))
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
    sheet.freeze_panes = "A2"


def export(stats: Dict[str, Any]) -> Path:
    from openpyxl import Workbook

    total = stats["total"] or 1
    workbook = Workbook()
    workbook.remove(workbook.active)

    # 1. 字段填充率
    _write_sheet(
        workbook, "1-字段填充率", ["字段", "有值数", "填充率%", "备注"],
        [
            [
                field, stats["fieldAvailable"][field],
                round(stats["fieldAvailable"][field] * 100.0 / total, 1),
                "0% 表示依赖该字段的规则必然 skip" if stats["fieldAvailable"][field] == 0 else "",
            ]
            for field in TRACKED_FIELDS
        ],
    )

    # 2. 规则三态分布
    rule_rows = []
    for rule in load_rules():
        rule_id = rule["ruleId"]
        counter = stats["ruleStates"][rule_id]
        reasons = stats["skipReasons"].get(rule_id) or Counter()
        rule_rows.append([
            rule_id, rule["channel"], rule["priority"], rule["name"],
            counter["matched"], counter["unmatched"], counter["skipped"],
            round(counter["matched"] * 100.0 / total, 1),
            round(counter["skipped"] * 100.0 / total, 1),
            "、".join(f"{k}({v})" for k, v in reasons.most_common(3)),
            "永久跳过" if rule.get("permanentlySkipped") else "",
        ])
    _write_sheet(
        workbook, "2-规则三态分布",
        ["规则", "通道", "优先级", "名称", "命中", "未命中", "跳过",
         "命中率%", "跳过率%", "主要缺失字段", "标记"],
        rule_rows,
    )

    # 3. 标签分布
    tag_rows = []
    c_names = tx.load_taxonomy()["dimensions"]["C"]["values"]
    for tag, count in stats["cMajor"].most_common():
        tag_rows.append(["C 大类", tag, c_names.get(tag, ""), count, round(count * 100.0 / total, 1)])
    for tag, count in stats["cStrong"].most_common():
        tag_rows.append(["C 强子类(30分档)", tag, "", count, round(count * 100.0 / total, 1)])
    for tag, count in stats["cWeak"].most_common():
        tag_rows.append(["C 弱子类(25分档)", tag, "", count, round(count * 100.0 / total, 1)])
    for tag, count in stats["bTags"].most_common():
        tag_rows.append(["B 阶段", tag, "", count, round(count * 100.0 / total, 1)])
    for tag, count in sorted(stats["bSignals"].items()):
        tag_rows.append(["B 信号数", tag, "", count, round(count * 100.0 / total, 1)])
    for tag, count in stats["regions"].most_common():
        tag_rows.append(["属地", tag, "", count, round(count * 100.0 / total, 1)])
    _write_sheet(workbook, "3-标签分布", ["维度", "标签", "名称", "企业数", "占比%"], tag_rows)

    # 4. 分数分布（分有无行业值两组）
    score_rows = []
    for label in [p["label"] for p in SAMPLE_POLICIES]:
        for group, data in (
            ("全部", stats["scoresByPolicy"]),
            ("有行业值", stats["scoresWithIndustry"]),
            ("无行业值", stats["scoresWithoutIndustry"]),
        ):
            p = _percentiles(data.get(label) or [])
            score_rows.append([
                label, group, p["count"], p["min"], p["p10"], p["p25"],
                p["p50"], p["p75"], p["p90"], p["max"], p["mean"],
            ])
    _write_sheet(
        workbook, "4-基础分分布",
        ["样例政策", "分组", "样本数", "最小", "P10", "P25", "P50", "P75", "P90", "最大", "均值"],
        score_rows,
    )

    # 5. effectiveWeight 分布
    _write_sheet(
        workbook, "5-有效权重分布",
        ["有效权重", "评分次数", "占比%", "说明"],
        [
            [
                weight, count, round(count * 100.0 / max(sum(stats["effectiveWeights"].values()), 1), 1),
                f"五项中有 {weight}/100 权重参与计分",
            ]
            for weight, count in sorted(stats["effectiveWeights"].items(), reverse=True)
        ],
    )

    # 6. 档位分布
    band_rows = []
    for label in [p["label"] for p in SAMPLE_POLICIES]:
        counter = stats["bands"][label]
        subtotal = sum(counter.values()) or 1
        for band in ("direct_push", "needs_supplement", "not_recommended", "not_evaluable"):
            band_rows.append([
                label, band, counter[band], round(counter[band] * 100.0 / subtotal, 1),
            ])
    _write_sheet(workbook, "6-档位分布", ["样例政策", "档位", "企业数", "占比%"], band_rows)

    ensure_dir(OUTPUT_DIR)
    target = OUTPUT_DIR / "phase1_stats.xlsx"
    workbook.save(target)
    return target


def print_summary(stats: Dict[str, Any]) -> None:
    total = stats["total"] or 1
    print(f"\n样本 {stats['total']} 份，G0 拦截 {stats['blocked']} 份")

    zero_fields = [f for f in TRACKED_FIELDS if stats["fieldAvailable"][f] == 0]
    print(f"\n[填充率 0% 的字段 {len(zero_fields)} 个] 依赖它们的规则必然 skip")
    for field in zero_fields:
        print(f"  {field}")

    always_skipped = [
        rule["ruleId"] for rule in load_rules()
        if stats["ruleStates"][rule["ruleId"]]["skipped"] >= total
    ]
    print(f"\n[100% 跳过的规则 {len(always_skipped)} 条] {'、'.join(always_skipped)}")

    always_matched = [
        rule["ruleId"] for rule in load_rules()
        if stats["ruleStates"][rule["ruleId"]]["matched"] >= total
    ]
    if always_matched:
        print(f"[100% 命中的规则 {len(always_matched)} 条] {'、'.join(always_matched)}"
              f"  ← 无区分度，四期需评估")

    print("\n[基础分分布：有行业值 vs 无行业值]")
    print("  归一化若有效，两组中位数差异应较小；差异 >10 分说明归一化不足，需四期处理")
    for policy in SAMPLE_POLICIES:
        label = policy["label"]
        with_p = _percentiles(stats["scoresWithIndustry"].get(label) or [])
        without_p = _percentiles(stats["scoresWithoutIndustry"].get(label) or [])
        if with_p["p50"] is None or without_p["p50"] is None:
            continue
        gap = round(abs(with_p["p50"] - without_p["p50"]), 2)
        flag = "  ← 差异偏大" if gap > 10 else ""
        print(f"  {label}")
        print(f"    有行业值 n={with_p['count']:>5} P50={with_p['p50']:>6}  "
              f"无行业值 n={without_p['count']:>5} P50={without_p['p50']:>6}  差 {gap}{flag}")

    print("\n[档位分布]")
    for policy in SAMPLE_POLICIES:
        label = policy["label"]
        counter = stats["bands"][label]
        subtotal = sum(counter.values()) or 1
        parts = " ".join(
            f"{band}={counter[band] * 100 // subtotal}%"
            for band in ("direct_push", "needs_supplement", "not_recommended", "not_evaluable")
        )
        print(f"  {label}: {parts}")


def main() -> int:
    parser = argparse.ArgumentParser(description="一期统计")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 份，验证流程用")
    parser.add_argument("--dir", default=None, help="样本目录")
    parser.add_argument("--today", default="2026-08-03", help="基准日期 YYYY-MM-DD")
    args = parser.parse_args()

    sample_dir = Path(args.dir) if args.dir else sample_enterprise_dir()
    if not sample_dir.exists():
        print(f"样本目录不存在：{sample_dir}")
        return 1

    today = date.fromisoformat(args.today)
    print(f"样本目录：{sample_dir}")
    print(f"基准日期：{today}（成立年限与时效性以此计算）")

    stats = collect(sample_dir, args.limit, today)
    if stats["total"] == 0:
        print("没有可用样本")
        return 1

    print_summary(stats)
    target = export(stats)
    print(f"\n统计表：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
