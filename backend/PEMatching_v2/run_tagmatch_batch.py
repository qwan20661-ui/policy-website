# -*- coding: utf-8 -*-
"""批量企业政策匹配 CLI。

支持断点续跑——大批量 agent 模式耗时长（单企业约 50 秒），中断后可续。
批次完成后会在同一结果目录自动生成 `summary.xlsx`、详细链路证据与简易报告。

用法：
    # deterministic 快速回归（100 家约 3 秒）
    python backend/PEMatching_v2/run_tagmatch_batch.py --mode deterministic --limit 100

    # agent 模式，指定并发
    python backend/PEMatching_v2/run_tagmatch_batch.py --mode agent --limit 20 --workers 4

    # 续跑上次中断的批次
    python backend/PEMatching_v2/run_tagmatch_batch.py --resume <runId>

    # 单文件
    python backend/PEMatching_v2/run_tagmatch_batch.py --file path/to/one.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from service import fields as F
from service.agent import MODE_AGENT, MODE_DETERMINISTIC, run_match
from service.event_log import RequestEventLog
from service.paths import OUTPUT_DIR, atomic_write_json, ensure_dir, env_int, sample_enterprise_dir
from service.report_export import export_evidence_reports, export_evidence_viewer

BATCH_ROOT = OUTPUT_DIR / "tagmatch"
PROGRESS_NAME = "_progress.json"


def _safe_name(text: str, limit: int = 40) -> str:
    cleaned = "".join(
        ch if ch.isalnum() or ch in "（）()-_·" else "_"
        for ch in F.text(text)
    )
    return cleaned[:limit] or "unnamed"


def _load_records(
    directory: Optional[Path], single_file: Optional[Path], limit: Optional[int]
) -> List[Tuple[str, Dict[str, Any]]]:
    """返回 [(源文件名, 企业记录)]。"""
    records: List[Tuple[str, Dict[str, Any]]] = []

    if single_file:
        record = json.loads(single_file.read_text(encoding="utf-8"))
        if isinstance(record, list):
            for index, item in enumerate(record):
                if isinstance(item, dict) and F.text(item.get("enterpriseName")):
                    records.append((f"{single_file.stem}#{index}", item))
        elif isinstance(record, dict) and F.text(record.get("enterpriseName")):
            records.append((single_file.name, record))
        return records[:limit] if limit else records

    for path in sorted((directory or sample_enterprise_dir()).glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(record, dict) or "industryLevel1" not in record:
            continue
        if not F.text(record.get("enterpriseName")):
            continue
        records.append((path.name, record))
        if limit and len(records) >= limit:
            break
    return records


def _load_progress(run_dir: Path) -> Dict[str, Any]:
    path = run_dir / PROGRESS_NAME
    if not path.exists():
        return {"done": [], "failed": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"done": [], "failed": []}
    return {
        "done": list(payload.get("done") or []),
        "failed": list(payload.get("failed") or []),
        "options": payload.get("options") or {},
    }


def _save_progress(run_dir: Path, progress: Dict[str, Any]) -> None:
    atomic_write_json(run_dir / PROGRESS_NAME, {
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
        **progress,
    })


def _summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    bands: Counter = Counter()
    degraded = 0
    truncated = 0
    gate_blocked = 0
    no_recommendation = 0
    not_evaluable_dropped = 0
    elapsed: List[int] = []
    turns: List[int] = []
    top_scores: List[float] = []
    completeness: List[int] = []
    eligibility_statuses: Counter = Counter()
    eligibility_evaluated = 0
    eligibility_failed = 0

    for item in results:
        if not item.get("success"):
            continue
        data = item["data"]
        meta = item["meta"]
        if meta.get("degraded"):
            degraded += 1
        if meta.get("truncated"):
            truncated += 1
        if not (data.get("gateResult") or {}).get("passed", True):
            gate_blocked += 1
        if meta.get("elapsedMs") is not None:
            elapsed.append(meta["elapsedMs"])
        if meta.get("turns") is not None:
            turns.append(meta["turns"])
        available = (meta.get("dataCompleteness") or {}).get("availableFields")
        if available is not None:
            completeness.append(available)

        recommendations = data.get("recommendations") or []
        if not recommendations:
            no_recommendation += 1
        else:
            top_score = recommendations[0].get("conditionMatchScore")
            if top_score is not None:
                top_scores.append(float(top_score))
        for recommendation in recommendations:
            bands[F.text(recommendation.get("gateStatus"))] += 1

        evals = ((data.get("eligibilityResult") or {}).get("eligibilityEvaluations")) or []
        if evals:
            eligibility_evaluated += 1
        else:
            eligibility_failed += 1
        for evaluation in evals:
            if isinstance(evaluation, dict) and F.text(evaluation.get("gateStatus")):
                eligibility_statuses[F.text(evaluation.get("gateStatus"))] += 1
        # 被静默丢弃的不可评估政策数：已评估总数 - 出现在 eligibilityResult 里的
        not_evaluable_dropped += max(0, (data.get("scoredCount") or 0) - len(evals))

    def percentile(values: List[Any], ratio: float) -> Optional[float]:
        if not values:
            return None
        ordered = sorted(values)
        index = min(int(ratio * (len(ordered) - 1)), len(ordered) - 1)
        return round(ordered[index], 2)

    return {
        "bands": dict(bands.most_common()),
        "degraded": degraded,
        "truncated": truncated,
        "gateBlocked": gate_blocked,
        "noRecommendation": no_recommendation,
        "notEvaluableDropped": not_evaluable_dropped,
        "elapsedMs": {
            "p50": percentile(elapsed, 0.50),
            "p95": percentile(elapsed, 0.95),
            "p99": percentile(elapsed, 0.99),
            "max": max(elapsed) if elapsed else None,
        },
        "turns": {"p50": percentile(turns, 0.50), "max": max(turns) if turns else None},
        "topScore": {
            "p10": percentile(top_scores, 0.10),
            "p50": percentile(top_scores, 0.50),
            "p90": percentile(top_scores, 0.90),
        },
        "availableFields": {
            "p50": percentile(completeness, 0.50),
            "min": min(completeness) if completeness else None,
            "max": max(completeness) if completeness else None,
        },
        "eligibility": {
            "evaluatedEnterprises": eligibility_evaluated,
            "failedEnterprises": eligibility_failed,
            "statuses": dict(eligibility_statuses.most_common()),
        },
    }


def export_excel(run_dir: Path, results: List[Dict[str, Any]], summary: Dict[str, Any]) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    workbook = Workbook()

    sheet = workbook.active
    sheet.title = "企业汇总"
    headers = [
        "序号", "源文件", "企业名称", "成功", "模式", "降级", "降级原因", "超轮次", "轮次",
        "耗时ms", "可用字段数", "缺失字段数", "G0通过", "信用已校验",
        "命中规则数", "跳过规则数", "候选政策", "评分政策", "推荐条数",
        "Top1政策", "Top1符合度", "Top1档位", "直推", "需补充",
        "资格已评估", "资格直推", "资格待补充", "资格不推荐", "requestId", "错误",
    ]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    fail_fill = PatternFill(start_color="FFD9D9", end_color="FFD9D9", fill_type="solid")
    degrade_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")

    for index, item in enumerate(results, start=1):
        if not item.get("success"):
            failure_row = [index, item.get("source"), item.get("enterpriseName"), "否", item.get("mode")]
            failure_row.extend([""] * (len(headers) - len(failure_row) - 2))
            failure_row.extend([item.get("requestId"), F.text(item.get("error"))[:300]])
            sheet.append(failure_row)
            for cell in sheet[sheet.max_row]:
                cell.fill = fail_fill
            continue

        data, meta = item["data"], item["meta"]
        gate = data.get("gateResult") or {}
        recommendations = data.get("recommendations") or []
        bands: Counter = Counter(F.text(r.get("gateStatus")) for r in recommendations)
        channels = data.get("channelResults") or {}
        matched = sum(len(c.get("matched") or []) for c in channels.values())
        skipped = sum(len(c.get("skipped") or []) for c in channels.values())
        top = recommendations[0] if recommendations else {}
        completeness = meta.get("dataCompleteness") or {}
        evals = ((data.get("eligibilityResult") or {}).get("eligibilityEvaluations")) or []
        eligibility_statuses = Counter(
            F.text(entry.get("gateStatus"))
            for entry in evals
            if isinstance(entry, dict) and F.text(entry.get("gateStatus"))
        )

        sheet.append([
            index, item.get("source"), data["enterprise"].get("enterpriseName"), "是",
            meta.get("mode"),
            "是" if meta.get("degraded") else "", F.text(meta.get("degradedReason")),
            "是" if meta.get("truncated") else "", meta.get("turns"),
            meta.get("elapsedMs"),
            completeness.get("availableFields"), completeness.get("unknownFields"),
            "是" if gate.get("passed") else "否",
            "是" if gate.get("creditChecked") else "否",
            matched, skipped,
            data.get("candidateCount"), data.get("scoredCount"), len(recommendations),
            F.text(top.get("policyTitle"))[:40], top.get("conditionMatchScore"), F.text(top.get("gateStatus")),
            bands.get("direct_push", 0), bands.get("needs_supplement", 0),
            "是" if evals else "否",
            eligibility_statuses.get("direct_push", 0),
            eligibility_statuses.get("needs_supplement", 0),
            eligibility_statuses.get("not_recommended", 0),
            meta.get("requestId"), "",
        ])
        if meta.get("degraded") or meta.get("truncated"):
            for cell in sheet[sheet.max_row]:
                cell.fill = degrade_fill

    for column, width in zip(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZABCD",
        (6, 30, 26, 6, 14, 6, 18, 8, 6, 10, 12, 12, 8, 12, 12, 12, 10, 10, 10, 42, 10, 18, 6, 6, 10, 10, 10, 12, 14, 40),
    ):
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = "D2"

    # 推荐明细页
    detail = workbook.create_sheet("推荐明细")
    detail_headers = ["企业名称", "排名", "政策ID", "政策标题", "符合度", "档位",
                      "资格原因", "命中规则", "理由来源", "理由"]
    detail.append(detail_headers)
    for cell in detail[1]:
        cell.font = Font(bold=True)
    for item in results:
        if not item.get("success"):
            continue
        name = item["data"]["enterprise"].get("enterpriseName")
        for recommendation in item["data"].get("recommendations") or []:
            detail.append([
                name, recommendation["rank"], recommendation["fusionPolicyId"],
                F.text(recommendation.get("policyTitle"))[:60],
                recommendation.get("conditionMatchScore"), F.text(recommendation.get("gateStatus")),
                "、".join(recommendation.get("gateReasons") or []),
                "、".join(recommendation.get("matchedRules") or []),
                F.text(recommendation.get("reasonSource")),
                F.text(recommendation.get("reason"))[:400],
            ])
    for column, width in zip("ABCDEFGHIJ", (26, 6, 10, 46, 10, 18, 32, 22, 10, 70)):
        detail.column_dimensions[column].width = width
    detail.freeze_panes = "B2"

    # 汇总统计页
    stats = workbook.create_sheet("批次统计")
    stats.append(["指标", "值"])
    stats["A1"].font = Font(bold=True)
    stats["B1"].font = Font(bold=True)
    for key, value in (
        ("企业总数", len(results)),
        ("成功", sum(1 for item in results if item.get("success"))),
        ("失败", sum(1 for item in results if not item.get("success"))),
        ("降级", summary["degraded"]),
        ("超轮次", summary["truncated"]),
        ("G0 拦截", summary["gateBlocked"]),
        ("无推荐", summary["noRecommendation"]),
        ("耗时 P50 ms", summary["elapsedMs"]["p50"]),
        ("耗时 P95 ms", summary["elapsedMs"]["p95"]),
        ("耗时 P99 ms", summary["elapsedMs"]["p99"]),
        ("耗时最大 ms", summary["elapsedMs"]["max"]),
        ("轮次 P50", summary["turns"]["p50"]),
        ("轮次最大", summary["turns"]["max"]),
        ("Top1 符合度 P10", summary["topScore"]["p10"]),
        ("Top1 符合度 P50", summary["topScore"]["p50"]),
        ("Top1 符合度 P90", summary["topScore"]["p90"]),
        ("不可评估丢弃", summary.get("notEvaluableDropped", 0)),
        ("可用字段数 P50", summary["availableFields"]["p50"]),
        ("资格已评估企业", summary["eligibility"]["evaluatedEnterprises"]),
        ("资格评估失败企业", summary["eligibility"]["failedEnterprises"]),
    ):
        stats.append([key, value])
    stats.append([])
    stats.append(["档位分布（按推荐条目计）", ""])
    for band, count in summary["bands"].items():
        stats.append([band, count])
    stats.append([])
    stats.append(["资格门禁状态（按政策计）", ""])
    for status, count in summary["eligibility"]["statuses"].items():
        stats.append([status, count])
    stats.column_dimensions["A"].width = 28
    stats.column_dimensions["B"].width = 16

    target = run_dir / "summary.xlsx"
    workbook.save(target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="批量企业政策匹配")
    parser.add_argument("--mode", default=MODE_DETERMINISTIC,
                        choices=[MODE_AGENT, MODE_DETERMINISTIC])
    parser.add_argument("--dir", default=None, help="企业样本目录")
    parser.add_argument("--file", default=None, help="单个 JSON 文件（可含企业数组）")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 家")
    parser.add_argument("--keyword", default="", help="查询关键词")
    parser.add_argument("--stage2", action="store_true", help="启用 G4 第二轮")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--today", default=None, help="基准日期 YYYY-MM-DD")
    parser.add_argument("--resume", default=None, help="续跑指定 runId")
    parser.add_argument("--eligibility-llm", dest="eligibility_llm", action="store_true",
                        help="强制启用逐条件模型抽取企业事实")
    parser.add_argument("--no-eligibility-llm", dest="eligibility_llm", action="store_false",
                        help="强制关闭逐条件模型抽取（默认跟随环境 PEMATCHING_V2_ELIGIBILITY_LLM_ENABLED，当前 .env 为开启）")
    parser.set_defaults(eligibility_llm=None)
    args = parser.parse_args()

    today = date.fromisoformat(args.today) if args.today else None
    records = _load_records(
        Path(args.dir) if args.dir else None,
        Path(args.file) if args.file else None,
        args.limit,
    )
    if not records:
        print("没有可用企业样本")
        return 1

    run_id = args.resume or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = ensure_dir(BATCH_ROOT / run_id)
    progress = _load_progress(run_dir)
    done = set(progress["done"])

    pending = [(name, record) for name, record in records if name not in done]
    print(f"批次 {run_id}　模式 {args.mode}　企业 {len(records)} 家"
          + (f"，续跑跳过 {len(done)} 家" if done else ""))
    if not pending:
        print("全部已完成。")
    workers = args.workers or (env_int("V2_MAX_WORKERS", 4) if args.mode == MODE_AGENT else 8)

    results: List[Dict[str, Any]] = []
    # 续跑时先读回已完成的结果，保证汇总完整
    for name, _ in records:
        if name not in done:
            continue
        path = run_dir / f"{_safe_name(name)}.json"
        if path.exists():
            try:
                results.append(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass

    started = time.time()
    completed = 0

    def work(item: Tuple[str, Dict[str, Any]]) -> Dict[str, Any]:
        name, record = item
        log = RequestEventLog()
        payload: Dict[str, Any] = {
            "source": name,
            "enterpriseName": F.text(record.get("enterpriseName")),
            "mode": args.mode,
            "requestId": log.request_id,
        }
        try:
            outcome = run_match(
                record, mode=args.mode, keyword=args.keyword,
                enable_stage2=args.stage2, top_n=args.top_n, today=today, event_log=log,
                use_llm_evidence=args.eligibility_llm,
            )
            payload.update({"success": True, "data": outcome["data"], "meta": outcome["meta"]})
        except Exception as exc:
            payload.update({
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
            })
        return payload

    executor = ThreadPoolExecutor(max_workers=workers)
    futures = {executor.submit(work, item): item for item in pending}
    try:
        for future in as_completed(futures):
            name, record = futures[future]
            completed += 1
            payload = future.result()
            atomic_write_json(run_dir / f"{_safe_name(name)}.json", payload)
            results.append(payload)

            if payload["success"]:
                progress["done"].append(name)
                meta = payload["meta"]
                recommendations = payload["data"].get("recommendations") or []
                flag = "⚠" if meta.get("degraded") or meta.get("truncated") else "✓"
                top = f"{recommendations[0]['conditionMatchScore']}" if recommendations else "无推荐"
                print(f"[{completed}/{len(pending)}] {flag} {payload['enterpriseName'][:20]:<22}"
                      f" 推荐{len(recommendations):>3}条 Top1={top:>7}"
                      f" {meta.get('elapsedMs')}ms"
                      + (f" 降级({meta.get('degradedReason')})" if meta.get("degraded") else ""))
            else:
                progress["failed"].append({"source": name, "error": payload["error"][:200]})
                print(f"[{completed}/{len(pending)}] ✗ {payload['enterpriseName'][:20]} "
                      f"{payload['error'][:60]}")

            if completed % 10 == 0:
                _save_progress(run_dir, progress)
    except KeyboardInterrupt:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        _save_progress(run_dir, progress)
        print("\n收到中断（Ctrl+C），已完成结果与进度已保存。")
        print(f"已完成 {completed}/{len(pending)}　结果目录：{run_dir}")
        print(f"续跑命令：python backend/PEMatching_v2/run_tagmatch_batch.py --resume {run_id}")
        return 130
    executor.shutdown(wait=True)

    _save_progress(run_dir, progress)
    elapsed = time.time() - started

    summary = _summarize(results)
    succeeded = sum(1 for item in results if item.get("success"))

    print()
    print(f"完成：成功 {succeeded}　失败 {len(results) - succeeded}　耗时 {elapsed:.0f} 秒")
    print(f"降级 {summary['degraded']}　超轮次 {summary['truncated']}　"
          f"G0 拦截 {summary['gateBlocked']}　无推荐 {summary['noRecommendation']}")
    if succeeded:
        rate = summary["degraded"] * 100.0 / succeeded
        print(f"降级率 {rate:.1f}%" + ("　⚠ 超过 10%，需排查 prompt 或模型配合" if rate > 10 else ""))
    print(f"耗时 P50={summary['elapsedMs']['p50']}ms　P95={summary['elapsedMs']['p95']}ms　"
          f"P99={summary['elapsedMs']['p99']}ms")
    print(f"档位分布：{summary['bands']}")
    print(f"资格门禁：已评估企业 {summary['eligibility']['evaluatedEnterprises']}　"
          f"失败企业 {summary['eligibility']['failedEnterprises']}　"
          f"状态分布 {summary['eligibility']['statuses']}　"
          f"不可评估丢弃 {summary.get('notEvaluableDropped', 0)}")
    print(f"Top1 符合度：P10={summary['topScore']['p10']}　P50={summary['topScore']['p50']}　"
          f"P90={summary['topScore']['p90']}")

    inputs_by_source = dict(records)
    for item in results:
        if item.get("success"):
            item["enterpriseInput"] = inputs_by_source.get(item.get("source")) or {}
    target = export_excel(run_dir, results, summary)
    detailed_report, simplified_report = export_evidence_reports(run_dir, results, summary)
    evidence_viewer = export_evidence_viewer(run_dir, results, summary)
    atomic_write_json(run_dir / "_summary.json", {
        "runId": run_id, "mode": args.mode,
        "total": len(results), "succeeded": succeeded,
        "elapsedSeconds": round(elapsed, 1),
        "options": {
            "keyword": args.keyword, "stage2": args.stage2,
            "topN": args.top_n, "workers": workers,
            "today": today.isoformat() if today else None,
            "eligibilityLlm": args.eligibility_llm,
        },
        "reports": {
            "summary": target.name,
            "detailedEvidence": detailed_report.name,
            "simplified": simplified_report.name,
            "viewer": evidence_viewer.name,
        },
        "summary": summary,
    })
    print(f"\n结果目录：{run_dir}")
    print(f"汇总表：{target}")
    print(f"详细链路证据：{detailed_report}")
    print(f"简易报告：{simplified_report}")
    print(f"离线查看页：{evidence_viewer}")
    print(f"续跑命令：python backend/PEMatching_v2/run_tagmatch_batch.py --resume {run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
