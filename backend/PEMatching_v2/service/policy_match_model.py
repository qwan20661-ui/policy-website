# -*- coding: utf-8 -*-
"""T5 单政策模型判断与严格输出契约校验。"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional

from service import fields as F
from service.condition_scoring import condition_catalog_for_model, evidence_catalog, load_scoring_rules
from service.llm_client import LLMError, chat, extract_json_content
from service.model_archive import archive_model_exchange
from service.paths import (
    POLICY_CONDITION_MATCH_PROMPT_PATH,
    env_float,
    env_int,
    env_str,
)


PROMPT_VERSION = "policy-condition-match-prompt-v3"
INPUT_SCHEMA_VERSION = "policy-condition-match-input-v2"
OUTPUT_SCHEMA_VERSION = "policy-condition-match-output-v2"
_OUTPUT_KEYS = {
    "schemaVersion", "sourceConflicts", "conditionResults", "discoveredConditions",
}
_RULE_CONDITION_KEYS = {
    "conditionKey", "status", "failureEffect", "score", "evidencePaths", "reason", "confidence",
}
_DISCOVERED_CONDITION_KEYS = _RULE_CONDITION_KEYS | {
    "sourceText", "groupKey", "groupType", "groupOperator",
}
_MODEL_PROFILE_PREFIXES = (
    "$.metrics", "$.registerInfo", "$.industryInfo", "$.rndConditions",
    "$.qualifications", "$.qualificationCounts", "$.eligibilityFacts",
)
_RAW_FACT_EXCLUDED_NAMES = {
    "记录ID", "企业唯一标识ID", "所属企业ID", "企业名称", "企业全称",
    "租户ID", "创建时间", "更新时间", "最后更新时间",
}
_RAW_FACT_NORMALIZED_PATHS = {
    "注册日期": "$.metrics.registerDate",
    "注册地址": "$.registerInfo.registerAddress",
    "注册类型": "$.registerInfo.registerType",
    "注册地（行政区）": "$.registerInfo.registerDistrict",
    "所属国家级高新区": "$.registerInfo.nationalHighTechZone",
    "所属国民经济行业分类一级": "$.industryInfo.level1",
    "所属国民经济行业分类二级": "$.industryInfo.level2",
    "Ⅰ类知识产权数量": "$.metrics.ipClass1Count",
    "Ⅱ类知识产权数量": "$.metrics.ipClass2Count",
    "统计年份": "$.metrics.metricsYear",
    "研发费用": "$.metrics.rdExpenseWan",
    "研发费用占营业收入比例": "$.metrics.rdExpenseRatioPct",
    "营业收入": "$.metrics.revenueWan",
    "主营业务收入": "$.metrics.mainBusinessIncomeWan",
    "员工总人数": "$.metrics.employeeCount",
    "研发人员数量": "$.metrics.rdPersonnelCount",
    "技术合同成交额": "$.metrics.techContractWan",
    "净资产": "$.metrics.netAssetsWan",
    "资产总额": "$.metrics.totalAssetsWan",
    "本年新增人数": "$.metrics.newRecruitCount",
    "吸纳高校毕业生人数": "$.metrics.collegeGraduateCount",
    "企业博士人数": "$.metrics.doctorCount",
    "企业硕士人数": "$.metrics.masterCount",
    "科技型中小企业": "$.qualifications.isTechSme",
    "高新技术企业": "$.qualifications.isHighTechEnterprise",
    "研发费用专账": "$.rndConditions.hasRndAccount",
    "产学研合作": "$.rndConditions.industryUniversityCoop",
}
_POLICY_INFO_FIELDS = (
    "summary", "policyAttribute", "sceneUsage", "applicableScope", "industryField",
)
_POLICY_CONDITION_FIELDS = (
    "applicableTarget", "applicationRequirement", "applicationStartTime", "applicationEndTime",
)


class PolicyMatchModelError(RuntimeError):
    """模型不可用、JSON 非法或违反单政策输出契约。"""


def _canonical_hash(value: Any) -> str:
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(content.encode("utf-8")).hexdigest()


@lru_cache(maxsize=2)
def _load_prompt(path_text: str) -> str:
    path = Path(path_text)
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PolicyMatchModelError(f"单政策模型提示词不可读取：{path}（{exc}）") from exc
    if not content:
        raise PolicyMatchModelError("单政策模型提示词不能为空")
    return content


def reset_prompt_cache() -> None:
    _load_prompt.cache_clear()


def model_raw_facts(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    """生成单一精简事实表，去掉内部状态、旧标签、技术元数据和重复映射。"""
    facts: List[Dict[str, Any]] = []
    catalog = evidence_catalog(profile)
    for path, item in sorted(catalog.items()):
        if not any(path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "[") for prefix in _MODEL_PROFILE_PREFIXES):
            continue
        facts.append({"path": path, "value": item["value"], "label": item["text"]})

    limit = max(env_int("PEMATCHING_V2_POLICY_LLM_MAX_FACTS", 120), 1)
    if len(facts) >= limit:
        # 标准画像不可因保护阈值而缺字段；阈值只限制后续兜底原始事实。
        return facts

    for fact in ((profile.get("_rawFactIndex") or {}).get("facts") or []):
        if not isinstance(fact, dict):
            continue
        name = F.text(fact.get("fieldName"))
        path = F.text(fact.get("sourcePath"))
        normalized_path = _RAW_FACT_NORMALIZED_PATHS.get(name)
        if (
            not name or not path or name in _RAW_FACT_EXCLUDED_NAMES
            or (normalized_path is not None and normalized_path in catalog)
            or F.is_missing(fact.get("value"))
        ):
            continue
        facts.append({"path": path, "value": fact.get("value"), "label": name})
        if len(facts) >= limit:
            break

    return facts


def _selected_fields(source: Any, fields: tuple[str, ...]) -> Dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    return {
        key: deepcopy(source[key])
        for key in fields
        if key in source and not F.is_missing(source.get(key))
    }


def policy_for_model(package: Dict[str, Any]) -> Dict[str, Any]:
    """只保留判断申报资格所需政策内容，完整条件原文和原始规则表不裁剪。"""
    keywords = [
        F.text(item.get("text"))
        for item in package.get("keywords") or []
        if isinstance(item, dict) and F.text(item.get("text"))
    ]
    return {
        "fusionPolicyId": F.text(package.get("fusionPolicyId")),
        "title": F.text(package.get("title")),
        "policyInfo": _selected_fields(package.get("policyInfo"), _POLICY_INFO_FIELDS),
        "conditions": [
            _selected_fields(item, _POLICY_CONDITION_FIELDS)
            for item in package.get("conditions") or []
            if isinstance(item, dict)
        ],
        "ruleTableJson": deepcopy(package.get("ruleTableJson")),
        "keywords": keywords,
    }


def build_model_input(
    package: Dict[str, Any], profile: Dict[str, Any], rules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    effective_rules = rules or load_scoring_rules()
    return {
        "schemaVersion": INPUT_SCHEMA_VERSION,
        "policy": policy_for_model(package),
        "enterprise": {"facts": model_raw_facts(profile)},
        "evaluationRules": {
            "missingScore": effective_rules["missingScore"],
            "minimumThresholdFormula": "actual/target*100",
            "conditionKeys": condition_catalog_for_model(package),
        },
    }


def _require_exact_keys(value: Any, required: set[str], label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyMatchModelError(f"{label} 必须是对象")
    keys = set(value)
    if keys != required:
        missing, extra = sorted(required - keys), sorted(keys - required)
        raise PolicyMatchModelError(f"{label} 字段不符合契约：缺少 {missing}，额外 {extra}")
    return value


def _enum_text(value: Any) -> str:
    """模型契约的 `none` 是有效枚举，不能复用业务字段的缺失值归一化。"""
    return value.strip() if isinstance(value, str) else "" if value is None else str(value).strip()


def _validate_evidence_paths(entries: Any, label: str) -> None:
    if not isinstance(entries, list):
        raise PolicyMatchModelError(f"{label}.evidencePaths 必须是数组")
    for index, path in enumerate(entries):
        if not isinstance(path, str) or not path.strip().startswith("$"):
            raise PolicyMatchModelError(f"{label}.evidencePaths[{index}] 必须是可回读路径")
    if len(entries) != len(set(entries)):
        raise PolicyMatchModelError(f"{label}.evidencePaths 不允许重复")


def _validate_condition(raw: Any, descriptor: Optional[Dict[str, Any]], label: str) -> None:
    required = _RULE_CONDITION_KEYS if descriptor else _DISCOVERED_CONDITION_KEYS
    item = _require_exact_keys(raw, required, label)
    if F.text(item.get("status")) not in {"satisfied", "partially_satisfied", "not_satisfied", "missing"}:
        raise PolicyMatchModelError(f"{label}.status 非法")
    if _enum_text(item.get("failureEffect")) not in {
        "none", "out_of_scope", "irreversible", "growable", "ordinary_gap", "missing_data",
    }:
        raise PolicyMatchModelError(f"{label}.failureEffect 非法")
    score = F.to_float(item.get("score"))
    if score is None or not 0 <= score <= 100:
        raise PolicyMatchModelError(f"{label}.score 必须在 0 到 100 之间")
    if not F.text(item.get("conditionKey")):
        raise PolicyMatchModelError(f"{label} 缺少条件标识")
    if not F.text(item.get("reason")) or F.text(item.get("confidence")) not in {"high", "medium", "low"}:
        raise PolicyMatchModelError(f"{label} 缺少判断理由或置信度非法")
    _validate_evidence_paths(item.get("evidencePaths"), label)
    if item["status"] == "missing" and item["evidencePaths"]:
        raise PolicyMatchModelError(f"{label}.status 为 missing 时 evidencePaths 必须为空")
    if item["status"] != "missing" and not item["evidencePaths"]:
        raise PolicyMatchModelError(f"{label} 非缺失条件必须提供 evidencePaths")
    if descriptor:
        if item["conditionKey"] != descriptor["conditionKey"]:
            raise PolicyMatchModelError(f"{label}.conditionKey 必须回填条件目录原值")
        return
    if not F.text(item.get("sourceText")) or not F.text(item.get("groupKey")):
        raise PolicyMatchModelError(f"{label} 缺少原文或分组标识")
    if F.text(item.get("groupType")) not in {"hard", "soft", "special_pass"}:
        raise PolicyMatchModelError(f"{label}.groupType 非法")
    if F.text(item.get("groupOperator")) not in {"AND", "OR"}:
        raise PolicyMatchModelError(f"{label}.groupOperator 非法")


def validate_model_output(
    output: Any, package: Dict[str, Any], *, allowed_evidence_paths: Optional[set[str]] = None,
) -> Dict[str, Any]:
    """校验精简输出结构和输入证据路径，事实值仍由 T6 回读。"""
    payload = _require_exact_keys(output, _OUTPUT_KEYS, "模型输出")
    if payload.get("schemaVersion") != OUTPUT_SCHEMA_VERSION:
        raise PolicyMatchModelError("模型输出 schemaVersion 不匹配")
    if not isinstance(payload.get("sourceConflicts"), list):
        raise PolicyMatchModelError("sourceConflicts 必须是数组")
    for index, item in enumerate(payload["sourceConflicts"]):
        row = _require_exact_keys(item, {"applicationRequirementText", "ruleTableText", "description"}, f"sourceConflicts[{index}]")
        if not F.text(row.get("applicationRequirementText")) or not F.text(row.get("description")):
            raise PolicyMatchModelError(f"sourceConflicts[{index}] 缺少原文或说明")

    expected = set(condition_catalog_for_model(package))
    results = payload.get("conditionResults")
    if not isinstance(results, list) or len(results) != len(expected):
        raise PolicyMatchModelError("conditionResults 必须完整覆盖规则表条件目录")
    seen = set()
    for index, item in enumerate(results):
        key = F.text(item.get("conditionKey")) if isinstance(item, dict) else ""
        if key in seen or key not in expected:
            raise PolicyMatchModelError("conditionResults 包含重复或未知条件键")
        seen.add(key)
        _validate_condition(item, {"conditionKey": key}, f"conditionResults[{index}]")
    if seen != expected:
        raise PolicyMatchModelError("conditionResults 未覆盖全部规则表条件")
    discovered = payload.get("discoveredConditions")
    if not isinstance(discovered, list):
        raise PolicyMatchModelError("discoveredConditions 必须是数组")
    discovered_keys = set(expected)
    for index, item in enumerate(discovered):
        key = F.text(item.get("conditionKey")) if isinstance(item, dict) else ""
        if not key or key in discovered_keys:
            raise PolicyMatchModelError("discoveredConditions 条件键为空或重复")
        discovered_keys.add(key)
        _validate_condition(item, None, f"discoveredConditions[{index}]")
    if allowed_evidence_paths is not None:
        for field in ("conditionResults", "discoveredConditions"):
            for index, item in enumerate(payload[field]):
                unknown = sorted(set(item["evidencePaths"]) - allowed_evidence_paths)
                if unknown:
                    raise PolicyMatchModelError(f"{field}[{index}] 引用了输入中不存在的证据路径：{unknown}")
    return payload


def judge_policy(
    package: Dict[str, Any], profile: Dict[str, Any], *, rules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """一次主调用完成单政策判断，输出非法时最多额外重试一次。"""
    model_input = build_model_input(package, profile, rules)
    prompt = _load_prompt(str(POLICY_CONDITION_MATCH_PROMPT_PATH.resolve()))
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(model_input, ensure_ascii=False)},
    ]
    retries = 1
    started = time.monotonic()
    errors: List[str] = []
    for attempt in range(retries + 1):
        try:
            message = chat(
                messages,
                temperature=0.0,
                timeout=env_int("PEMATCHING_V2_POLICY_LLM_TIMEOUT", env_int("V2_LLM_TIMEOUT", 120)),
                model=env_str("V2_MODEL_NAME", "minimax2.7"),
            )
            parsed = extract_json_content(message)
            output = validate_model_output(
                parsed,
                package,
                allowed_evidence_paths={item["path"] for item in model_input["enterprise"]["facts"]},
            )
            archive_enabled = archive_model_exchange({
                "createdAt": int(time.time() * 1000),
                "fusionPolicyId": F.text(package.get("fusionPolicyId")),
                "input": model_input,
                "output": output,
            })
            return {
                "output": output,
                "modelName": env_str("V2_MODEL_NAME", "minimax2.7"),
                "promptVersion": PROMPT_VERSION,
                "inputHash": _canonical_hash(model_input),
                "retryCount": attempt,
                "elapsedMs": int((time.monotonic() - started) * 1000),
                "archiveEnabled": archive_enabled,
            }
        except (LLMError, PolicyMatchModelError) as exc:
            errors.append(str(exc))
            if attempt >= retries:
                break
            messages.append({"role": "user", "content": "上次输出不符合 JSON 契约。请重新只返回完整合法 JSON。"})
            time.sleep(env_float("PEMATCHING_V2_POLICY_LLM_RETRY_BACKOFF", 0.5))
    raise PolicyMatchModelError(
        f"政策 {F.text(package.get('fusionPolicyId'))} 模型判断失败：{errors[-1] if errors else '未知错误'}"
    )
