# -*- coding: utf-8 -*-
"""新申报条件匹配链路批处理命令。

该命令不调用旧标签索引或 Agent。输出 JSON 含逐企业审计元数据，可用于性能统计、
新旧差异比对和人工抽查。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List

from service import fields as F
from service.match_orchestrator import run_condition_match_batch
from service.paths import OUTPUT_DIR, atomic_write_json, ensure_dir, sample_enterprise_dir
from service.report_export import export_condition_match_viewer


def _load_records(path: Path, limit: int | None) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    paths = [path] if path.is_file() else sorted(path.glob("*.json"))
    for source in paths:
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict) and F.text(item.get("enterpriseName")):
                records.append(item)
                if limit and len(records) >= limit:
                    return records
    return records


def _percentile(values: List[int], ratio: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(int((len(ordered) - 1) * ratio), len(ordered) - 1)]


def _summary(result: Dict[str, Any]) -> Dict[str, Any]:
    successful = [item for item in result["data"]["results"] if item.get("success")]
    elapsed = [item["meta"].get("elapsedMs") for item in successful if item.get("meta", {}).get("elapsedMs") is not None]
    model_calls = [item["meta"].get("modelCallCount", 0) for item in successful]
    cache_hits = [item["meta"].get("cacheHitCount", 0) for item in successful]
    return {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "enterpriseCount": len(result["data"]["results"]),
        "succeeded": result["meta"]["succeeded"],
        "failed": result["meta"]["failed"],
        "elapsedMs": {"p50": _percentile(elapsed, 0.5), "p95": _percentile(elapsed, 0.95)},
        "modelCallCount": sum(model_calls),
        "cacheHitCount": sum(cache_hits),
        "recommendationCount": sum(
            len(item.get("data", {}).get("recommendations") or []) for item in successful
        ),
    }


def _seconds(milliseconds: Any) -> str:
    value = F.to_float(milliseconds)
    return f"{(value or 0) / 1000:.1f}s"


def _progress_printer():
    """生成线程安全的 CLI 进度输出器，保留 stdout 给最终 JSON 汇总。"""
    lock = Lock()
    outcomes = {
        "recommended": "可推荐",
        "direct_exclusion": "直接排除",
        "no_effective_match": "无有效匹配",
        "failed": "失败",
    }

    def report(event: Dict[str, Any]) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        enterprise = f"企业 {event.get('enterpriseIndex', '?')}/{event.get('enterpriseTotal', '?')}"
        kind = event.get("event")
        if kind == "enterprise_started":
            message = (
                f"[{timestamp}] [{enterprise}] 开始 {event.get('enterpriseName')} | "
                f"政策 {event.get('policyCount')}，候选 {event.get('candidateCount')}，"
                f"预过滤 {event.get('prefilterExcludedCount')}，政策并发 {event.get('policyWorkers')}"
            )
        elif kind == "policy_completed":
            policy = f"{event.get('fusionPolicyId')} {F.text(event.get('policyTitle'))[:40]}".strip()
            status = outcomes.get(F.text(event.get("outcome")), F.text(event.get("outcome")))
            if event.get("outcome") == "failed":
                detail = f"错误={event.get('error')}"
            else:
                cache = "，缓存命中" if event.get("cacheHit") else ""
                score = "" if event.get("finalScore") is None else f"，得分={event.get('finalScore')}"
                detail = (
                    f"模型={_seconds(event.get('modelElapsedMs'))}，"
                    f"排队={_seconds(event.get('queueWaitMs'))}，重试={event.get('retryCount', 0)}"
                    f"{cache}{score}"
                )
            message = (
                f"[{timestamp}] [{enterprise}] [政策 {event.get('completed')}/{event.get('total')}] "
                f"{policy} | {status} | {detail}"
            )
        elif kind == "enterprise_completed":
            message = (
                f"[{timestamp}] [{enterprise}] 完成 {event.get('enterpriseName')} | "
                f"耗时={_seconds(event.get('elapsedMs'))}，推荐={event.get('recommendationCount', 0)}，"
                f"返回={event.get('returnedCount', 0)}，模型调用={event.get('modelCallCount', 0)}，"
                f"缓存命中={event.get('cacheHitCount', 0)}，失败={event.get('failedCount', 0)}"
            )
        elif kind == "enterprise_failed":
            message = (
                f"[{timestamp}] [{enterprise}] 企业失败 {event.get('enterpriseName')} | "
                f"错误={event.get('error')}"
            )
        else:
            return
        with lock:
            print(message, file=sys.stderr, flush=True)

    return report


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="批量执行企业政策申报条件匹配")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--file", type=Path, help="单个企业 JSON 或企业数组 JSON")
    source.add_argument("--dir", type=Path, help="企业 JSON 目录")
    parser.add_argument("--limit", type=int, help="最多读取企业数")
    parser.add_argument("--workers", type=int, help="企业并发数")
    parser.add_argument("--top-n", type=int, help="每家企业返回的最终政策条数，默认全部")
    parser.add_argument("--today", help="固定日期 YYYY-MM-DD，便于回归测试")
    parser.add_argument("--output", type=Path, help="结果目录，默认 output/condition_match/<时间>")
    parser.add_argument("--quiet", action="store_true", help="关闭逐企业、逐政策进度日志")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 必须是正整数")
    if args.workers is not None and args.workers <= 0:
        parser.error("--workers 必须是正整数")
    if args.top_n is not None and args.top_n <= 0:
        parser.error("--top-n 必须是正整数")
    try:
        today = date.fromisoformat(args.today) if args.today else None
    except ValueError:
        parser.error("--today 必须是 YYYY-MM-DD")
    source_path = args.file or args.dir or sample_enterprise_dir()
    records = _load_records(source_path, args.limit)
    if not records:
        print(f"未从 {source_path} 读取到含 enterpriseName 的企业 JSON")
        return 2
    progress_callback = None if args.quiet else _progress_printer()
    if not args.quiet:
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] 已读取 {len(records)} 家企业，开始条件匹配",
            file=sys.stderr,
            flush=True,
        )
    result = run_condition_match_batch(
        records, today=today, top_n=args.top_n, workers=args.workers,
        progress_callback=progress_callback,
    )
    # 结果项按 records 下标关联，供 HTML 报告展示企业原始数据（对齐旧链路 item["enterpriseInput"]）。
    for item in result["data"]["results"]:
        if item.get("success") and isinstance(item.get("index"), int):
            item["enterpriseInput"] = records[item["index"]]
    output = args.output or OUTPUT_DIR / "condition_match" / datetime.now().strftime("%Y%m%d_%H%M%S")
    ensure_dir(output)
    atomic_write_json(output / "results.json", result)
    summary = _summary(result)
    atomic_write_json(output / "summary.json", summary)
    viewer = export_condition_match_viewer(output, result["data"]["results"], summary)
    print(json.dumps({"output": str(output), "viewer": str(viewer), **summary}, ensure_ascii=False, indent=2))
    return 0 if result["meta"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
