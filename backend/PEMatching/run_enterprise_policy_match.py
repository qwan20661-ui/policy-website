"""直接运行企业匹配政策的命令行脚本。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PROJECT_ROOT.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from service.enterprise_loader import (
    DEFAULT_ENTERPRISE_API_BASE_URL,
    DEFAULT_ENTERPRISE_API_PATH,
    load_enterprises,
    load_enterprises_from_api,
)
from service.matching_service import run_matching
from service.policy_loader import load_structure_policies
from service.rule_profile_service import DEFAULT_TECH_SME_RULE_PROFILE_PATH, DEFAULT_TEST_ENTERPRISE_JSON_PATH, build_rule_profile_policy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="直接运行企业匹配政策")
    parser.add_argument(
        "--enterprise-source",
        choices=("api", "json"),
        default=os.getenv("PEMATCHING_ENTERPRISE_SOURCE", "api").strip().lower() or "api",
        help="企业数据源，默认读取政策推荐企业接口；传 --enterprise-json 时自动改用 json。",
    )
    parser.add_argument(
        "--enterprise-json",
        default="",
        help="企业 JSON 文件路径；用于离线回放或临时覆盖企业接口。",
    )
    parser.add_argument(
        "--enterprise-api-base-url",
        default=os.getenv("PEMATCHING_ENTERPRISE_API_BASE_URL", DEFAULT_ENTERPRISE_API_BASE_URL),
        help="政策推荐企业接口基地址。",
    )
    parser.add_argument(
        "--enterprise-api-path",
        default=os.getenv("PEMATCHING_ENTERPRISE_API_PATH", DEFAULT_ENTERPRISE_API_PATH),
        help="政策推荐企业分页接口路径。",
    )
    parser.add_argument(
        "--enterprise-api-page-size",
        type=int,
        default=int(os.getenv("PEMATCHING_ENTERPRISE_API_PAGE_SIZE", "100")),
        help="企业接口每页条数，范围 1-100。",
    )
    parser.add_argument(
        "--enterprise-api-max-pages",
        type=int,
        default=int(os.getenv("PEMATCHING_ENTERPRISE_API_MAX_PAGES", "0")),
        help="最多读取企业接口页数，0 表示读取全部。",
    )
    parser.add_argument(
        "--enterprise-api-timeout",
        type=float,
        default=float(os.getenv("PEMATCHING_ENTERPRISE_API_TIMEOUT", "15")),
        help="企业接口单次请求超时秒数。",
    )
    parser.add_argument(
        "--enterprise-name",
        default="",
        help="企业名称。若 JSON 中包含多家企业，建议显式指定。",
    )
    parser.add_argument(
        "--all-enterprises",
        action="store_true",
        help="允许当前 JSON 中的所有企业全部参与匹配。",
    )
    parser.add_argument(
        "--rule-profile",
        default="",
        help="规则表 JSON 路径。不传时默认走 backend/PEMatching/企业测试/科技型中小企业_企业数据直接匹配规则表_20260722.json。",
    )
    parser.add_argument(
        "--rule-policy-title",
        default="",
        help="规则表单政策模式下用于展示的政策标题。不传时根据规则表文件名自动推断。",
    )
    parser.add_argument(
        "--use-structure-policies",
        action="store_true",
        help="显式切回 structure API 主政策链路；默认走当前测试规则表单政策链路。",
    )
    parser.add_argument(
        "--policy-keyword",
        default="",
        help="按政策标题过滤，例如：新型研发机构。不传表示不过滤。",
    )
    parser.add_argument(
        "--policy-limit",
        type=int,
        default=0,
        help="最多取多少条政策。传 0 表示不限制，默认 0。",
    )
    parser.add_argument(
        "--all-policies",
        action="store_true",
        help="显式匹配全部主政策。开启后会忽略 policy-limit 的截断。",
    )
    parser.add_argument(
        "--policy-id",
        action="append",
        default=[],
        help="指定结构化政策 ID，可重复传入多次。",
    )
    parser.add_argument(
        "--include-document-content",
        action="store_true",
        help="匹配前额外拉取部分关联正文，信息更全但更慢。",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只验证企业和政策是否加载成功，不调用 LLM。",
    )
    parser.add_argument(
        "--output",
        default="",
        help="输出 JSON 文件路径。不传时自动写到 output 目录。",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=int(os.getenv("MAX_WORKERS", "3")),
        help="并发数，默认读取 .env 里的 MAX_WORKERS。",
    )
    parser.add_argument(
        "--structure-api-base-url",
        default=os.getenv("STRUCTURE_API_BASE_URL", "http://127.0.0.1:8080"),
        help="structure 接口基地址。",
    )
    return parser.parse_args()


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def default_enterprise_json_path() -> Path:
    env_value = os.getenv("PEMATCHING_CHAIN_ENTERPRISE_JSON_PATH", "").strip()
    if env_value:
        return resolve_path(env_value)
    return DEFAULT_TEST_ENTERPRISE_JSON_PATH.resolve()


def default_rule_profile_path() -> Path:
    env_value = os.getenv("PEMATCHING_CHAIN_RULE_PROFILE_PATH", "").strip() or os.getenv("RULE_PROFILE_PATH", "").strip()
    if env_value:
        return resolve_path(env_value)
    return DEFAULT_TECH_SME_RULE_PROFILE_PATH.resolve()


def filter_enterprises(
    enterprises: List[Dict[str, Any]],
    enterprise_name: str,
    allow_all: bool,
) -> List[Dict[str, Any]]:
    if enterprise_name.strip():
        matched = [item for item in enterprises if item.get("企业名称") == enterprise_name.strip()]
        if not matched:
            raise ValueError(f"未找到企业：{enterprise_name}")
        return matched

    if len(enterprises) == 1:
        return enterprises

    if not allow_all:
        raise ValueError(
            f"当前企业文件包含 {len(enterprises)} 家企业。请传 --enterprise-name 指定一家，或显式加 --all-enterprises。"
        )

    return enterprises


def filter_policies(
    policies: List[Dict[str, Any]],
    policy_ids: List[str],
    policy_keyword: str,
    policy_limit: int,
    all_policies: bool,
) -> List[Dict[str, Any]]:
    filtered = policies

    if policy_ids:
        policy_id_set = {item.strip() for item in policy_ids if item and item.strip()}
        filtered = [item for item in filtered if item.get("id") in policy_id_set]

    if policy_keyword.strip():
        filtered = [item for item in filtered if policy_keyword.strip() in item.get("title", "")]

    if not all_policies and policy_limit > 0:
        filtered = filtered[:policy_limit]

    return filtered


def default_output_path(enterprise_source_name: str) -> Path:
    output_dir = PROJECT_ROOT / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_stem = Path(enterprise_source_name).stem
    return output_dir / f"{file_stem}_match_result_{timestamp}.json"


def default_log_path(enterprise_source_name: str) -> Path:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_stem = Path(enterprise_source_name).stem
    return log_dir / f"{file_stem}_run_{timestamp}.log"


def setup_logger(log_path: Path) -> logging.Logger:
    logger_name = f"pematching-runner-{log_path.stem}"
    logger = logging.getLogger(logger_name)
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


def print_preview(enterprises: List[Dict[str, Any]], policies: List[Dict[str, Any]]) -> None:
    print("=" * 72)
    print("试跑预览")
    print("=" * 72)
    print(f"企业数量: {len(enterprises)}")
    print(f"政策数量: {len(policies)}")
    print("-" * 72)

    for item in enterprises[:5]:
        print(f"企业: {item.get('企业名称', '')} | 注册地: {item.get('注册地', '')} | 一级行业: {item.get('一级行业', '')}")

    if len(enterprises) > 5:
        print(f"... 其余 {len(enterprises) - 5} 家企业未展开")

    print("-" * 72)

    for item in policies[:5]:
        if item.get("rule_profile_path"):
            summary = item.get("condition_profile_summary") or {}
            condition_count = int(summary.get("conditionFieldItemCount") or 0)
        else:
            condition_count = len(item.get("structured_conditions", []) or [])
        print(f"政策: {item.get('title', '')} | 政策ID: {item.get('id', '')} | 条件数: {condition_count}")

    if len(policies) > 5:
        print(f"... 其余 {len(policies) - 5} 条政策未展开")


def log_run_context(
    logger: logging.Logger,
    args: argparse.Namespace,
    enterprise_source_label: str,
    effective_rule_profile_path: Path | None,
    enterprises: List[Dict[str, Any]],
    policies: List[Dict[str, Any]],
) -> None:
    logger.info("开始本次企业政策匹配试跑")
    logger.info("脚本参数: %s", json.dumps(vars(args), ensure_ascii=False, default=str))
    logger.info("企业数据源: %s", enterprise_source_label)
    if effective_rule_profile_path:
        logger.info("当前匹配链路: 企业测试规则表+企业统一画像+逐项模型判定")
        logger.info("规则表文件: %s", effective_rule_profile_path)
    else:
        logger.info("当前匹配链路: structure API 主政策+企业统一画像")
    logger.info("企业数量: %s", len(enterprises))
    for index, enterprise in enumerate(enterprises, start=1):
        logger.info(
            "企业[%s]: 名称=%s 注册地=%s 一级行业=%s 二级行业=%s 荣誉资质=%s 科技服务领域=%s",
            index,
            enterprise.get("企业名称", ""),
            enterprise.get("注册地", ""),
            enterprise.get("一级行业", ""),
            enterprise.get("二级行业", ""),
            "；".join(enterprise.get("荣誉资质", []) or []),
            "；".join(enterprise.get("科技服务领域", []) or []),
        )
    logger.info("本次参与匹配的主政策数量: %s", len(policies))
    for index, policy in enumerate(policies, start=1):
        if policy.get("rule_profile_path"):
            summary = policy.get("condition_profile_summary") or {}
            logger.info(
                "匹配政策[%s]: id=%s title=%s 来源=政策条件文件 名称=%s 版本=%s 硬性组=%s 软性组=%s 条件项总数=%s",
                index,
                policy.get("id", ""),
                policy.get("title", ""),
                policy.get("condition_profile_name", ""),
                policy.get("condition_profile_version", ""),
                summary.get("hardRuleGroupCount", ""),
                summary.get("softRuleGroupCount", ""),
                summary.get("conditionFieldItemCount", ""),
            )
            continue

        logger.info(
            "主政策[%s]: id=%s title=%s sourcePolicyId=%s 条件数=%s 文档数=%s",
            index,
            policy.get("id", ""),
            policy.get("title", ""),
            policy.get("sourcePolicyId", ""),
            len(policy.get("structured_conditions", []) or []),
            len(policy.get("documents", []) or []),
        )


def log_match_summary(logger: logging.Logger, results: List[Dict[str, Any]]) -> None:
    logger.info("开始写入本次匹配汇总")
    for enterprise_result in results:
        enterprise_name = enterprise_result.get("企业名称", "")
        logger.info("企业匹配汇总: %s", enterprise_name)
        for policy_result in enterprise_result.get("政策匹配结果", []):
            if "错误" in policy_result:
                logger.info(
                    "  政策=%s 失败=%s",
                    policy_result.get("政策标题", ""),
                    policy_result.get("错误", ""),
                )
                continue
            logger.info(
                "  政策=%s 匹配率=%s 星级=%s 是否推荐=%s 条件数=%s",
                policy_result.get("政策标题", ""),
                policy_result.get("匹配率", 0),
                policy_result.get("星级推荐", ""),
                policy_result.get("是否推荐", ""),
                len(policy_result.get("条件匹配详情", []) or []),
            )


def main() -> int:
    args = parse_args()

    enterprise_json_input = args.enterprise_json.strip()
    enterprise_source = "json" if enterprise_json_input else args.enterprise_source
    if enterprise_source == "json":
        if enterprise_json_input:
            enterprise_json_path = resolve_path(enterprise_json_input)
        else:
            enterprise_json_path = default_enterprise_json_path()
        enterprise_source_label = str(enterprise_json_path)
        if not enterprise_json_path.exists():
            print(f"企业 JSON 不存在: {enterprise_json_path}")
            return 1
    else:
        enterprise_json_path = None
        enterprise_source_label = (
            f"{args.enterprise_api_base_url.rstrip('/')}/{args.enterprise_api_path.lstrip('/')}"
        )

    source_name = enterprise_json_path.name if enterprise_json_path else "enterprise_api"
    log_path = default_log_path(source_name)
    logger = setup_logger(log_path)
    logger.info("日志文件已创建: %s", log_path)

    if enterprise_source == "json":
        enterprises = load_enterprises(enterprise_json_path)
    else:
        enterprises = load_enterprises_from_api(
            base_url=args.enterprise_api_base_url,
            api_path=args.enterprise_api_path,
            page_size=args.enterprise_api_page_size,
            max_pages=args.enterprise_api_max_pages,
            timeout=args.enterprise_api_timeout,
            progress_callback=logger.info,
        )
    enterprises = filter_enterprises(
        enterprises=enterprises,
        enterprise_name=args.enterprise_name,
        allow_all=args.all_enterprises,
    )

    effective_rule_profile_path: Path | None = None
    if not args.use_structure_policies:
        if args.rule_profile.strip():
            effective_rule_profile_path = resolve_path(args.rule_profile)
        else:
            effective_rule_profile_path = default_rule_profile_path()

    if effective_rule_profile_path:
        rule_profile_path = effective_rule_profile_path
        if not rule_profile_path.exists():
            print(f"规则表 JSON 不存在: {rule_profile_path}")
            logger.error("规则表 JSON 不存在: %s", rule_profile_path)
            return 1
        policy = build_rule_profile_policy(rule_profile_path, args.rule_policy_title)
        policies = [policy]
        logger.info(
            "已启用当前测试链路：企业数据=%s；规则表=%s；当前仅加载该条件文件对应的单条政策，逐条件判定强制走 LLM。",
            enterprise_source_label,
            rule_profile_path,
        )
    else:
        fetch_count = None if args.all_policies or args.policy_limit <= 0 else max(args.policy_limit, len(args.policy_id) or 1)
        policies = load_structure_policies(
            base_url=args.structure_api_base_url,
            max_policies=fetch_count,
            detail_workers=max(1, min(args.max_workers, 20)),
            include_document_content=args.include_document_content,
            progress_callback=logger.info,
        )
        policies = filter_policies(
            policies=policies,
            policy_ids=args.policy_id,
            policy_keyword=args.policy_keyword,
            policy_limit=args.policy_limit,
            all_policies=args.all_policies or args.policy_limit <= 0,
        )

    if not policies:
        print("没有筛选到任何政策，请调整 --policy-keyword / --policy-id，或直接传 --rule-profile。")
        logger.error("没有筛选到任何政策，请调整筛选参数。")
        return 1

    log_run_context(logger, args, enterprise_source_label, effective_rule_profile_path, enterprises, policies)
    print_preview(enterprises, policies)

    if args.validate_only:
        print("\n已完成加载验证，未调用 LLM。")
        logger.info("本次仅做加载验证，未调用 LLM。")
        logger.info("日志结束")
        return 0

    output_path = resolve_path(args.output) if args.output.strip() else default_output_path(source_name)
    results = run_matching(
        enterprises=enterprises,
        policies=policies,
        max_workers=max(1, args.max_workers),
        output_file=output_path,
        progress_callback=logger.info,
    )

    print("\n" + "=" * 72)
    print("匹配完成")
    print("=" * 72)
    print(f"输出文件: {output_path}")
    print(f"日志文件: {log_path}")
    print(f"结果企业数: {len(results)}")
    logger.info("输出文件: %s", output_path)
    logger.info("日志文件: %s", log_path)
    log_match_summary(logger, results)

    for item in results:
        print(f"\n企业: {item.get('企业名称', '')}")
        for policy_result in item.get("政策匹配结果", [])[:5]:
            if "错误" in policy_result:
                print(f"  - {policy_result.get('政策标题', '')}: 失败 - {policy_result.get('错误', '')}")
            else:
                print(
                    f"  - {policy_result.get('政策标题', '')}: "
                    f"匹配率 {policy_result.get('匹配率', 0)} | "
                    f"{policy_result.get('星级推荐', '')}"
                )

    logger.info("日志结束")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
