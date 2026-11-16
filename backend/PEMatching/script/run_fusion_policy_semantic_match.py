"""用实时融合政策规则运行企业-政策模型语义匹配与确定性评分。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from service.enterprise_loader import load_enterprises
from service.matching_service import _enterprise_summary, _hard_blocker_summary, _local_explanation, _write_excel_output
from service.rule_profile_service import build_rule_profile_policy
from service.scoring_service import score_policy_against_enterprise
from service.semantic_match_service import assess_enterprise_policy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行实时融合政策的模型语义匹配。")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080", help="融合政策接口基地址。")
    parser.add_argument("--policy-id", default="6548", help="融合政策 ID。")
    parser.add_argument("--enterprise-file", type=Path, required=True, help="企业 JSON 文件。")
    parser.add_argument("--output-file", type=Path, required=True, help="匹配结果 JSON 输出文件。")
    parser.add_argument("--log-file", type=Path, default=None, help="运行日志文件；不传时自动写入 backend/PEMatching/logs。")
    parser.add_argument("--max-workers", type=int, default=1, help="企业级并发数，默认 1；建议先从 2 开始，根据模型服务容量逐步提高。")
    parser.add_argument("--limit", type=int, default=0, help="仅运行前 N 家企业，0 表示全部。")
    parser.add_argument("--restart", action="store_true", help="忽略已有检查点，从头重新运行。")
    parser.add_argument(
        "--rescore-existing",
        action="store_true",
        help="复用输出文件中的模型语义事实，仅按当前本地规则重新评分，不调用模型。",
    )
    return parser.parse_args()


def fetch_policy(base_url: str, policy_id: str) -> Dict[str, Any]:
    response = requests.get(
        f"{base_url.rstrip('/')}/api/fusion-classification/fusion-detail",
        params={"fusionPolicyId": policy_id},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    detail = payload.get("data") if payload.get("success") else None
    rule_table = detail.get("ruleTableJson") if isinstance(detail, dict) else None
    if not isinstance(rule_table, dict) or not rule_table:
        raise RuntimeError("接口未返回可执行的 ruleTableJson。")
    return {
        "id": detail.get("id", policy_id),
        "title": detail.get("mainFileTitle") or detail.get("directoryTitle") or f"政策{policy_id}",
        "ruleTableJson": rule_table,
    }


def default_log_path(policy_id: str, enterprise_file: Path) -> Path:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return log_dir / f"fusion_policy_{policy_id}_{enterprise_file.stem}_{timestamp}.log"


def setup_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger(f"pematching-fusion-semantic-{log_path.stem}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


def build_summary(results: List[Dict[str, Any]], policy: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    mapping_counter: Counter[str] = Counter()
    fact_status_counter: Counter[str] = Counter()
    default_qualitative_count = 0
    model_error_count = 0
    for enterprise_result in results:
        match = (enterprise_result.get("政策匹配结果") or [{}])[0]
        if match.get("错误"):
            model_error_count += 1
            continue
        details = match.get("条件匹配详情", []) or []
        for detail in details:
            mapping_counter[str(detail.get("匹配方式") or "未记录")] += 1
            fact_status_counter[str(detail.get("模型事实状态") or "未记录")] += 1
            if detail.get("passSource") == "default_qualitative_after_semantic":
                default_qualitative_count += 1
        rows.append(
            {
                "企业ID": (enterprise_result.get("企业信息") or {}).get("企业ID", ""),
                "企业名称": enterprise_result.get("企业名称", ""),
                "最终分": match.get("final_score", 0),
                "硬性通过": bool(match.get("hard_pass")),
                "星级推荐": match.get("星级推荐", ""),
                "直通车命中": bool(match.get("special_pass_hit")),
                "模型": match.get("规则判定模型", ""),
                "模型调用错误": match.get("模型语义判定错误", ""),
                "硬性阻断": match.get("hard_fail_items", []),
            }
        )
    rows.sort(key=lambda item: str(item["企业ID"]))
    scores = [float(item["最终分"] or 0) for item in rows]
    return {
        "测试时间": datetime.now().isoformat(timespec="seconds"),
        "政策接口": f"{args.base_url.rstrip('/')}/api/fusion-classification/fusion-detail?fusionPolicyId={policy['id']}",
        "政策名称": policy["title"],
        "规则表版本": policy["ruleTableJson"].get("schemaVersion"),
        "企业数据文件": str(args.enterprise_file),
        "模型语义匹配": "每个企业与政策调用一次模型；模型仅提取字段映射与证据，最终评分由本地规则执行。",
        "企业数": len(results),
        "成功匹配数": len(rows),
        "失败数": model_error_count,
        "硬性通过数": sum(1 for item in rows if item["硬性通过"]),
        "硬性失败数": sum(1 for item in rows if not item["硬性通过"]),
        "直通车命中数": sum(1 for item in rows if item["直通车命中"]),
        "平均分": round(sum(scores) / len(scores), 2) if scores else 0,
        "最低分": min(scores) if scores else 0,
        "最高分": max(scores) if scores else 0,
        "星级分布": dict(Counter(item["星级推荐"] for item in rows)),
        "字段映射方式分布": dict(mapping_counter),
        "模型事实状态分布": dict(fact_status_counter),
        "定性默认通过次数": default_qualitative_count,
        "测试明细": rows,
    }


def progress_file_for(output_file: Path) -> Path:
    return output_file.with_name(f"{output_file.stem}.progress.json")


def load_progress(path: Path, restart: bool) -> List[Dict[str, Any]]:
    if restart or not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"检查点格式错误：{path}")
    return payload


def write_progress(path: Path, results: List[Dict[str, Any]]) -> None:
    temporary_path = path.with_name(f"{path.stem}.tmp{path.suffix}")
    temporary_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(path)


def _source_evidence(enterprise: Dict[str, Any], field_name: str) -> List[Dict[str, str]]:
    raw_record = enterprise.get("原始数据") if isinstance(enterprise.get("原始数据"), dict) else enterprise
    if not isinstance(raw_record, dict) or raw_record.get(field_name) is None:
        return []
    return [{"sourcePath": f"$.{field_name}", "sourceText": str(raw_record[field_name])}]


def saved_semantic_assessment(match: Dict[str, Any], enterprise: Dict[str, Any]) -> Dict[str, Any]:
    """从既有结果还原评分所需的模型事实，供确定性规则变更后的本地重评分使用。"""
    evaluation = match.get("科技型中小企业评价得分")
    tech_sme_evaluation: Dict[str, Dict[str, Any]] = {}
    if isinstance(evaluation, dict):
        metric_specs = {
            "techPersonnelRatio": ("科技人员占比", "科技人员占企业职工总数比例"),
            "rdExpenseRevenueRatio": ("研发费用占销售收入比例", "研发费用占销售收入总额比例"),
            "rdExpenseCostRatio": ("研发费用占成本费用比例", "研发费用占成本费用支出总额比例"),
            "classICount": ("I类知识产权数量", "I类知识产权数量"),
            "classIICount": ("II类知识产权数量", "II类知识产权数量"),
        }
        tech_sme_evaluation = {
            metric_name: {
                "value": evaluation.get(evaluation_field),
                # 历史产物可能没有保留模型提取的指标 evidence；可由当前企业原始字段无损重建。
                "evidence": _source_evidence(enterprise, source_field),
            }
            for metric_name, (evaluation_field, source_field) in metric_specs.items()
        }
    return {
        "enabled": bool(match.get("规则判定模型已启用")),
        "model": str(match.get("规则判定模型") or ""),
        "facts": match.get("模型语义事实") if isinstance(match.get("模型语义事实"), dict) else {},
        "techSmeEvaluation": tech_sme_evaluation,
        "error": str(match.get("模型语义判定错误") or ""),
    }


def rescore_existing_results(
    existing_results: List[Dict[str, Any]],
    enterprises: List[Dict[str, Any]],
    policy: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """只重跑本地确定性评分，保持原有模型字段映射与证据不变。"""
    current_schema = str(policy["ruleTableJson"].get("schemaVersion") or "")
    if not current_schema:
        raise ValueError("当前政策规则表缺少 schemaVersion，拒绝执行仅重评分。")
    enterprises_by_code = {
        str(enterprise.get("统一社会信用代码") or ""): enterprise
        for enterprise in enterprises
        if str(enterprise.get("统一社会信用代码") or "")
    }
    enterprises_by_id = {
        str(enterprise.get("企业ID") or ""): enterprise
        for enterprise in enterprises
        if str(enterprise.get("企业ID") or "")
    }
    rescored: List[Dict[str, Any]] = []
    for entry in existing_results:
        if not isinstance(entry, dict):
            continue
        enterprise_info = entry.get("企业信息") if isinstance(entry.get("企业信息"), dict) else {}
        enterprise = (
            enterprises_by_code.get(str(enterprise_info.get("统一社会信用代码") or ""))
            or enterprises_by_id.get(str(enterprise_info.get("企业ID") or ""))
        )
        if enterprise is None:
            raise ValueError(f"历史结果中的企业未在本次企业文件找到：{entry.get('企业名称', '')}")
        old_matches = entry.get("政策匹配结果") if isinstance(entry.get("政策匹配结果"), list) else []
        old_match = next((item for item in old_matches if isinstance(item, dict) and item.get("政策标题") == policy["title"]), None)
        if not isinstance(old_match, dict):
            raise ValueError(
                f"历史结果不存在目标政策“{policy['title']}”：{entry.get('企业名称', '')}；拒绝使用其他政策结果重评分。"
            )
        old_policy_id = str(old_match.get("政策ID") or "")
        if old_policy_id and old_policy_id != str(policy["id"]):
            raise ValueError(
                f"历史结果政策 ID 不一致：企业={entry.get('企业名称', '')}，历史={old_policy_id}，当前={policy['id']}"
            )
        old_schema = str(old_match.get("条件文件版本") or "")
        if not old_schema:
            raise ValueError(
                f"历史结果缺少规则表版本：企业={entry.get('企业名称', '')}；拒绝执行无法追溯的重评分。"
            )
        if old_schema != current_schema:
            raise ValueError(
                f"历史结果规则表版本不一致：企业={entry.get('企业名称', '')}，历史={old_schema}，当前={current_schema}"
            )
        if old_match.get("错误"):
            rescored.append(entry)
            continue
        semantic_assessment = saved_semantic_assessment(old_match, enterprise)
        score_result = score_policy_against_enterprise(enterprise, policy, semantic_assessment=semantic_assessment)
        score_result.update(_local_explanation(score_result))
        score_result.update(_hard_blocker_summary(score_result))
        score_result["LLM解释已启用"] = bool(semantic_assessment.get("enabled"))
        score_result["解释模型跳过原因"] = "复用历史模型语义事实，仅按当前本地规则重新评分。"
        score_result["模型语义判定错误"] = semantic_assessment.get("error", "")
        score_result["政策ID"] = policy["id"]
        score_result["政策标题"] = policy["title"]
        rescored.append(
            {
                "企业名称": enterprise.get("企业名称", entry.get("企业名称", "")),
                "企业信息": _enterprise_summary(enterprise),
                "政策匹配结果": [score_result],
            }
        )
    rescored.sort(key=lambda item: str((item.get("企业信息") or {}).get("企业ID", "")))
    return rescored


def match_one(enterprise: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
    profile = policy["ruleTableJson"]
    semantic_assessment = assess_enterprise_policy(enterprise, policy, profile)
    score_result = score_policy_against_enterprise(enterprise, policy, semantic_assessment=semantic_assessment)
    score_result.update(_local_explanation(score_result))
    score_result.update(_hard_blocker_summary(score_result))
    score_result["LLM解释已启用"] = bool(semantic_assessment.get("enabled"))
    score_result["解释模型跳过原因"] = "模型仅负责语义字段映射和证据提取；硬性判断及评分由本地规则引擎执行。"
    score_result["模型语义判定错误"] = semantic_assessment.get("error", "")
    score_result["政策ID"] = policy["id"]
    score_result["政策标题"] = policy["title"]
    return {
        "企业名称": enterprise.get("企业名称", ""),
        "企业信息": _enterprise_summary(enterprise),
        "政策匹配结果": [score_result],
    }


def match_one_safely(enterprise: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
    enterprise_name = enterprise.get("企业名称", "")
    try:
        return match_one(enterprise, policy)
    except Exception as exc:  # noqa: BLE001
        return {
            "企业名称": enterprise_name,
            "企业信息": _enterprise_summary(enterprise),
            "政策匹配结果": [{
                "政策ID": policy["id"],
                "政策标题": policy["title"],
                "条件文件版本": policy["ruleTableJson"].get("schemaVersion", ""),
                "错误": str(exc),
            }],
        }


def validate_existing_result_metadata(output_file: Path, policy: Dict[str, Any]) -> None:
    """仅重评分前验证历史摘要与当前 live 政策完全对应，避免跨政策覆盖产物。"""
    summary_file = output_file.with_name(f"{output_file.stem}_summary.json")
    if not summary_file.exists():
        raise FileNotFoundError(f"仅重评分需要历史摘要以校验政策 ID：{summary_file}")
    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    policy_url = str(summary.get("政策接口") or "")
    saved_policy_id = (parse_qs(urlparse(policy_url).query).get("fusionPolicyId") or [""])[0]
    if str(saved_policy_id) != str(policy["id"]):
        raise ValueError(f"历史摘要政策 ID 不一致：历史={saved_policy_id or '缺失'}，当前={policy['id']}")
    if str(summary.get("政策名称") or "") != str(policy["title"]):
        raise ValueError(f"历史摘要政策标题不一致：历史={summary.get('政策名称', '')}，当前={policy['title']}")
    saved_schema = str(summary.get("规则表版本") or "")
    current_schema = str(policy["ruleTableJson"].get("schemaVersion") or "")
    if not saved_schema:
        raise ValueError("历史摘要缺少规则表版本，拒绝执行无法追溯的重评分。")
    if not current_schema:
        raise ValueError("当前政策规则表缺少 schemaVersion，拒绝执行仅重评分。")
    if saved_schema != current_schema:
        raise ValueError(f"历史摘要规则表版本不一致：历史={saved_schema}，当前={current_schema}")


def main() -> None:
    args = parse_args()
    args.enterprise_file = args.enterprise_file.resolve()
    args.output_file = args.output_file.resolve()
    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    log_path = (args.log_file.resolve() if args.log_file else default_log_path(args.policy_id, args.enterprise_file))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(log_path)
    logger.info("日志文件已创建: %s", log_path)
    logger.info("运行参数: %s", json.dumps(vars(args), ensure_ascii=False, default=str))
    logger.info("开始读取政策接口: %s/api/fusion-classification/fusion-detail?fusionPolicyId=%s", args.base_url.rstrip("/"), args.policy_id)
    policy = fetch_policy(args.base_url, args.policy_id)
    logger.info(
        "政策读取完成: id=%s 标题=%s 规则表版本=%s",
        policy["id"], policy["title"], policy["ruleTableJson"].get("schemaVersion", ""),
    )
    enterprises = load_enterprises(args.enterprise_file)
    if args.limit > 0:
        enterprises = enterprises[: args.limit]
    checkpoint_file = progress_file_for(args.output_file)
    if args.rescore_existing:
        if not args.output_file.exists():
            raise FileNotFoundError(f"仅重评分需要已有结果文件：{args.output_file}")
        existing_results = json.loads(args.output_file.read_text(encoding="utf-8"))
        if not isinstance(existing_results, list):
            raise ValueError(f"历史结果文件格式错误：{args.output_file}")
        validate_existing_result_metadata(args.output_file, policy)
        logger.info("开始仅重评分：企业数=%s；不会调用模型服务。", len(existing_results))
        results = rescore_existing_results(existing_results, enterprises, policy)
        args.output_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        write_progress(checkpoint_file, results)
        _write_excel_output(results, args.output_file.with_suffix(".xlsx"))
        summary = build_summary(results, policy, args)
        summary_file = args.output_file.with_name(f"{args.output_file.stem}_summary.json")
        summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(
            "仅重评分完成：结果 JSON=%s，检查点=%s，结果 Excel=%s，汇总 JSON=%s",
            args.output_file, checkpoint_file, args.output_file.with_suffix(".xlsx"), summary_file,
        )
        return
    results = load_progress(checkpoint_file, args.restart)
    completed_codes = {
        str((entry.get("企业信息") or {}).get("统一社会信用代码", ""))
        for entry in results
        if isinstance(entry, dict)
    }
    pending = [item for item in enterprises if str(item.get("统一社会信用代码", "")) not in completed_codes]
    worker_count = max(1, args.max_workers)
    logger.info(
        "企业总数=%s，已完成=%s，待运行=%s，并发企业数=%s，检查点=%s",
        len(enterprises), len(results), len(pending), worker_count, checkpoint_file,
    )
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="policy-match") as executor:
        future_to_enterprise = {
            executor.submit(match_one_safely, enterprise, policy): enterprise for enterprise in pending
        }
        for future in as_completed(future_to_enterprise):
            enterprise = future_to_enterprise[future]
            enterprise_name = enterprise.get("企业名称", "")
            result = future.result()
            results.append(result)
            # 只有主线程写检查点，避免并发任务相互覆盖进度文件。
            write_progress(checkpoint_file, results)
            match = result["政策匹配结果"][0]
            logger.info(
                "进度 %s/%s：企业=%s 最终分=%s 硬性通过=%s 错误=%s；检查点已写入=%s",
                len(results), len(enterprises), enterprise_name, match.get("final_score", ""),
                match.get("hard_pass", ""), match.get("错误", ""), checkpoint_file,
            )

    results.sort(key=lambda item: str((item.get("企业信息") or {}).get("企业ID", "")))
    args.output_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_excel_output(results, args.output_file.with_suffix(".xlsx"))
    summary = build_summary(results, policy, args)
    summary_file = args.output_file.with_name(f"{args.output_file.stem}_summary.json")
    summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_text = json.dumps(
        {key: summary[key] for key in ["企业数", "成功匹配数", "失败数", "硬性通过数", "硬性失败数", "平均分", "星级分布", "字段映射方式分布"]},
        ensure_ascii=False,
    )
    logger.info("匹配汇总: %s", summary_text)
    logger.info("结果 JSON: %s", args.output_file)
    logger.info("结果 Excel: %s", args.output_file.with_suffix(".xlsx"))
    logger.info("汇总 JSON: %s", summary_file)
    logger.info("日志结束")


if __name__ == "__main__":
    main()
