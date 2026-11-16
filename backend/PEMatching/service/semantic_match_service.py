"""企业原始数据与政策规则之间的模型语义事实提取服务。"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, Iterable, List, Optional

import requests
from dotenv import load_dotenv

from service.enterprise_field_mapping import (
    candidates_with_chinese_field_names,
    chinese_field_name,
    is_mapped_enterprise_field,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

SEMANTIC_SYSTEM_PROMPT_FILE = PROJECT_ROOT / "prompts" / "semantic_policy_match_system_prompt.md"
SEMANTIC_LLM_API_URL = (os.getenv("API_URL", "") or "").strip()
SEMANTIC_LLM_API_KEY = (os.getenv("API_KEY", "") or "").strip()
SEMANTIC_LLM_MODEL_NAME = (os.getenv("MODEL_NAME", "") or "").strip()
SEMANTIC_LLM_TIMEOUT = int(os.getenv("SEMANTIC_MATCH_LLM_TIMEOUT", os.getenv("RULE_LLM_TIMEOUT", "90")))
SEMANTIC_LLM_RETRIES = max(0, int(os.getenv("SEMANTIC_MATCH_LLM_RETRIES", "2")))
SEMANTIC_LLM_RETRY_BACKOFF = max(0.0, float(os.getenv("SEMANTIC_MATCH_LLM_RETRY_BACKOFF", "0.5")))
SEMANTIC_LLM_ENABLED = (os.getenv("SEMANTIC_MATCH_LLM_ENABLED", "true") or "true").strip().lower() not in {"0", "false", "no"}
SEMANTIC_MATCH_MAX_WORKERS = max(1, int(os.getenv("MAX_WORKERS", "3")))
# 默认保持逐条件请求。微批必须完成逐 semanticItemKey 的 A/B 审计后才提高该值。
SEMANTIC_MATCH_BATCH_SIZE = min(10, max(1, int(os.getenv("SEMANTIC_MATCH_BATCH_SIZE", "1"))))

_SEMANTIC_EXECUTOR: Optional[ThreadPoolExecutor] = None
_SEMANTIC_EXECUTOR_LOCK = Lock()

TECH_SME_EVALUATION_RULE = {
    "适用条件": "科技型中小企业评价指标",
    "科技人员": "科技人员占职工比例：>=30%=20，>=25%=16，>=20%=12，>=15%=8，>=10%=4，<10%=0。",
    "研发投入": "两种口径择高：研发费用/销售收入：>=6%=50，>=5%=40，>=4%=30，>=3%=20，>=2%=10，<2%=0；研发费用/成本费用：>=30%=50，>=25%=40，>=20%=30，>=15%=20，>=10%=10，<10%=0。",
    "科技成果": "有效且无争议的相关知识产权：I类>=1项=30；否则II类>=4项=24、3项=18、2项=12、1项=6、0项=0。",
    "模型职责": "仅提取各项原始比例或数量及证据；不得自行计算分数或总分。所有比例统一为0到1的小数。",
}


class SemanticMatchRetryableError(RuntimeError):
    def __init__(self, message: str, *, error_type: str, attempts: int) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.attempts = attempts


class SemanticMatchProtocolError(SemanticMatchRetryableError):
    """模型返回无法与本次批次一一关联时触发拆分回退。"""


class _FallbackBudget:
    def __init__(self, initial_batch_count: int) -> None:
        self._limit = max(1, initial_batch_count) * 2
        self._used = 0
        self._lock = Lock()

    def reserve(self) -> int:
        with self._lock:
            if self._used >= self._limit:
                raise SemanticMatchRetryableError(
                    f"模型批次回退请求已达到额外预算 {self._limit} 次。",
                    error_type="semantic_model_fallback_exhausted",
                    attempts=self._used,
                )
            self._used += 1
            return self._used

    @property
    def used(self) -> int:
        with self._lock:
            return self._used


def _policy_identifier(policy: Optional[Dict[str, Any]], profile: Dict[str, Any]) -> str:
    source = policy or {}
    return _safe_text(
        source.get("fusionPolicyId")
        or source.get("policyId")
        or source.get("id")
        or profile.get("fusionPolicyId")
        or profile.get("policyId")
        or source.get("title")
        or profile.get("name")
        or "unknown-policy"
    )


def semantic_item_key(policy_id: str, group_type: str, group_ordinal: int, item_ordinal: int) -> str:
    """生成只用于程序关联的稳定规则项键，保留原始 itemId 供展示和追溯。"""
    return f"{policy_id}:{group_type}:{group_ordinal}:{item_ordinal}"


def _semantic_executor() -> ThreadPoolExecutor:
    global _SEMANTIC_EXECUTOR
    with _SEMANTIC_EXECUTOR_LOCK:
        if _SEMANTIC_EXECUTOR is None:
            _SEMANTIC_EXECUTOR = ThreadPoolExecutor(
                max_workers=SEMANTIC_MATCH_MAX_WORKERS,
                thread_name_prefix="pematching-semantic",
            )
        return _SEMANTIC_EXECUTOR


def _post_semantic_request(headers: Dict[str, str], payload: Dict[str, Any]) -> requests.Response:
    max_attempts = SEMANTIC_LLM_RETRIES + 1
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(
                SEMANTIC_LLM_API_URL,
                headers=headers,
                json=payload,
                timeout=SEMANTIC_LLM_TIMEOUT,
            )
            response.raise_for_status()
            return response
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt >= max_attempts:
                error_type = "semantic_model_timeout" if isinstance(exc, requests.Timeout) else "semantic_model_connection_error"
                raise SemanticMatchRetryableError(
                    f"语义模型调用失败，已尝试 {attempt} 次：{str(exc)[:160]}",
                    error_type=error_type,
                    attempts=attempt,
                ) from exc
            if SEMANTIC_LLM_RETRY_BACKOFF:
                time.sleep(SEMANTIC_LLM_RETRY_BACKOFF * attempt)

    raise RuntimeError("语义模型请求重试流程异常结束")


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return "；".join(_safe_text(item) for item in value if _safe_text(item))
    return str(value).strip()


def _explanation_indicates_missing(raw: Dict[str, Any]) -> bool:
    explanation = f"{_safe_text(raw.get('reason'))} {_safe_text(raw.get('branchReason'))}"
    markers = (
        "无法判断",
        "无法确认",
        "不能证明",
        "不足以证明",
        "缺少对应",
        "缺少指定",
        "未提供相关",
        "未提供该",
        "没有对应",
        "没有指定",
        "没有202",
    )
    return any(marker in explanation for marker in markers)


def _sanitize_json(content: str) -> Dict[str, Any]:
    text = _safe_text(content)
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError("模型返回中未找到 JSON 对象")
    candidate = text[start:end]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        left = max(0, exc.pos - 160)
        right = min(len(candidate), exc.pos + 240)
        excerpt = candidate[left:right].replace("\n", "\\n")
        raise ValueError(
            f"模型返回的 JSON 格式无效：{exc.msg}（位置 {exc.pos}，附近内容：{excerpt}）"
        ) from exc


def _flatten_json(value: Any, path: str = "$") -> Iterable[Dict[str, Any]]:
    """将任意接口 JSON 展平成可供模型选择的字段候选，保留 JSONPath。"""
    if isinstance(value, dict):
        for key, child in value.items():
            # 用无嵌套引号的路径标签，避免模型回传 $["字段"] 时漏转义内部双引号而破坏 JSON。
            child_path = f"{path}.{key}" if key.replace("_", "").isalnum() else f"{path}[{key}]"
            yield from _flatten_json(child, child_path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value[:30]):
            yield from _flatten_json(child, f"{path}[{index}]")
        return
    if value is None or isinstance(value, (dict, list)):
        return
    text = _safe_text(value)
    if text:
        yield {"sourcePath": path, "sourceValue": value}


def _model_input_candidates(enterprise: Dict[str, Any]) -> Dict[str, Any]:
    """仅构造映射文件声明的原始接口字段快照。"""
    raw_record = enterprise.get("原始数据") if isinstance(enterprise.get("原始数据"), dict) else enterprise
    all_candidates: Dict[str, Any] = {}
    for entry in _flatten_json(raw_record):
        path = entry["sourcePath"]
        if path in all_candidates or not is_mapped_enterprise_field(path):
            continue
        all_candidates[path] = entry["sourceValue"]

    # 数组字段会在构造模型提示词时合并为单条摘要，因此这里不再截断原始映射字段。
    return all_candidates


_MODEL_NOISE_FIELD_NAMES = {
    "企业唯一标识ID", "企业ID", "所属企业ID", "记录ID", "租户ID", "创建时间", "更新时间", "最后更新时间",
    "企业联系电话", "企业邮箱", "企业官网地址", "法定代表人姓名", "法定代表人联系电话", "企业名称", "企业全称", "统一社会信用代码",
}

_ARRAY_FIELD_NAMES = (
    "operationInfoList", "mainTechnologyList", "mainProductList", "honorQualificationList",
    "investmentFinancingList", "internationalRankingList", "nationalRankingList", "provincialRankingList",
    "ipStatisticsList",
)


def _is_array_leaf_path(path: str) -> bool:
    return any(path.startswith(f"$.{array_name}[") for array_name in _ARRAY_FIELD_NAMES)


def _compact_array_value(array_name: str, records: Any) -> str:
    if not isinstance(records, list):
        return ""
    compact_records: List[str] = []
    for record_index, record in enumerate(records[:30], start=1):
        if not isinstance(record, dict):
            continue
        fields: List[str] = []
        for field_key, value in record.items():
            field_path = f"$.{array_name}[{record_index - 1}].{field_key}"
            field_name = chinese_field_name(field_path)
            if not field_name or field_name in _MODEL_NOISE_FIELD_NAMES or not _safe_text(value):
                continue
            fields.append(f"{field_name}={_safe_text(value)}")
        if fields:
            compact_records.append(f"记录{record_index}：" + "；".join(fields))
    text = " | ".join(compact_records)
    return text[:4000]


def _prompt_candidate_snapshot(
    enterprise: Dict[str, Any], items: List[Dict[str, Any]], candidates: Dict[str, Any],
) -> Dict[str, Any]:
    """保留全量映射业务字段，压缩数组并剔除重复的技术元数据。"""
    raw_record = enterprise.get("原始数据") if isinstance(enterprise.get("原始数据"), dict) else enterprise
    selected: List[tuple[str, Any]] = []
    seen_values = set()
    for path, value in candidates.items():
        if _is_array_leaf_path(path):
            continue
        field_name = chinese_field_name(path)
        if not field_name or field_name in _MODEL_NOISE_FIELD_NAMES:
            continue
        dedup_key = (field_name, _safe_text(value))
        if dedup_key in seen_values:
            continue
        seen_values.add(dedup_key)
        selected.append((path, value))
    for array_name in _ARRAY_FIELD_NAMES:
        value = _compact_array_value(array_name, raw_record.get(array_name))
        path = f"$.{array_name}"
        if value and chinese_field_name(path):
            selected.append((path, value))
    return dict(selected)


def _internal_evidence_candidates(enterprise: Dict[str, Any], model_candidates: Dict[str, Any]) -> Dict[str, Any]:
    """内部派生指标可保留本地证据，不进入模型提示词。"""
    candidates = dict(model_candidates)
    for key, value in enterprise.items():
        if key.startswith("_") or key in {"原始数据", "_source_records"} or isinstance(value, (dict, list)):
            continue
        path = f"$normalized.{key}"
        if _safe_text(value):
            candidates[path] = value
    return candidates


def _expected_unit(item: Dict[str, Any]) -> str:
    declared_unit = _safe_text((item.get("matchRule") or {}).get("unit"))
    if declared_unit:
        return declared_unit
    name = _safe_text(item.get("itemName"))
    fields = "；".join(item.get("enterpriseFields", []) or [])
    text = f"{name}；{fields}"
    if any(word in text for word in ["营收", "销售收入", "资产规模", "资产总额"]):
        return "亿元"
    if any(word in text for word in ["职工", "人员", "人数"]):
        return "人"
    if "得分" in text:
        return "分"
    if "比例" in text or "占比" in text:
        return "小数"
    return _safe_text((item.get("matchRule") or {}).get("unit"))


def _policy_items(profile: Dict[str, Any], policy: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    policy_id = _policy_identifier(policy, profile)
    for group_type in ("hardRuleGroups", "softRuleGroups", "specialPassRuleGroups"):
        for group_ordinal, group in enumerate(profile.get(group_type, []) or [], start=1):
            group_items = list(group.get("conditionFieldItems", []) or [])
            sibling_branches = [
                {
                    "itemId": _safe_text(sibling.get("itemId")),
                    "itemName": _safe_text(sibling.get("itemName")),
                    "policyText": _safe_text(sibling.get("policyFragment")),
                    "conditionBranchText": _safe_text(sibling.get("conditionBranchText")),
                }
                for sibling in group_items
            ]
            for item_ordinal, item in enumerate(group_items, start=1):
                rule = item.get("matchRule") or {}
                condition_operator = _safe_text(group.get("conditionOperator") or "AND")
                explicit_branch_text = (
                    _safe_text(item.get("conditionBranchText"))
                    or _safe_text(group.get("conditionBranchText"))
                )
                if explicit_branch_text:
                    branch_explanation = explicit_branch_text
                elif condition_operator.upper() in {"ANY", "OR"} and len(group_items) > 1:
                    branch_explanation = "本组存在可选或互斥分支；必须先根据企业证据确定当前规则项对应分支是否适用。"
                elif len(group_items) > 1:
                    branch_explanation = "本规则项属于同一条件组；判断时必须结合组原文和同组其他规则项，不得脱离上下文单独解释。"
                else:
                    branch_explanation = "该规则项没有单独标注条件分支，按组原文和本项政策原文共同判断。"
                items.append(
                    {
                        "semanticItemKey": semantic_item_key(policy_id, group_type, group_ordinal, item_ordinal),
                        "itemId": _safe_text(item.get("itemId")),
                        "conditionNo": group.get("conditionNo"),
                        "groupType": group_type,
                        "evaluationRole": _safe_text(group.get("evaluationRole")),
                        "conditionContext": _safe_text(group.get("conditionContext")),
                        "groupRawText": _safe_text(group.get("rawText")),
                        "conditionOperator": condition_operator,
                        "conditionBranchText": explicit_branch_text,
                        "branchExplanation": branch_explanation,
                        "siblingBranches": sibling_branches,
                        "itemName": _safe_text(item.get("itemName")),
                        "policyText": _safe_text(item.get("policyFragment")) or _safe_text(group.get("rawText")),
                        "enterpriseFields": list(item.get("enterpriseFields", []) or []),
                        "fieldType": _safe_text(rule.get("fieldType")),
                        "operator": _safe_text(rule.get("operator")),
                        "targetValue": rule.get("targetValue"),
                        "expectedUnit": _expected_unit(item),
                    }
                )
    return items


def _build_prompt(
    enterprise: Dict[str, Any],
    policy: Dict[str, Any],
    profile: Dict[str, Any],
    items: Optional[List[Dict[str, Any]]] = None,
    candidates: Optional[Dict[str, Any]] = None,
) -> str:
    prompt_items = items if items is not None else _policy_items(profile, policy)
    candidate_snapshot = candidates if candidates is not None else _prompt_candidate_snapshot(
        enterprise, prompt_items, _model_input_candidates(enterprise),
    )
    raw_record = enterprise.get("原始数据") if isinstance(enterprise.get("原始数据"), dict) else enterprise
    payload = {
        "企业": {
            "企业名称": enterprise.get("企业名称") or raw_record.get("enterpriseName", ""),
            "统一社会信用代码": enterprise.get("统一社会信用代码") or raw_record.get("creditCode", ""),
        },
        "政策": {"政策标题": policy.get("title", ""), "规则项": prompt_items},
        "企业字段候选": candidates_with_chinese_field_names(candidate_snapshot),
    }
    # 政策推荐企业的科技型中小企业指标已有标准字段，本地只计算一次并复用。
    if enterprise.get("_source_type") != "policy_recommendation_api_record":
        payload["科技型中小企业评价指标计分规则"] = TECH_SME_EVALUATION_RULE
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def _path_evidence(raw_evidence: Any, candidates: Dict[str, Any]) -> List[Dict[str, str]]:
    """按模型回传的中文字段名反查本次输入快照，并回填不可伪造的原始文本。"""
    evidence: List[Dict[str, str]] = []
    seen = set()
    paths_by_name: Dict[str, List[str]] = {}
    for candidate_path in candidates:
        field_name = chinese_field_name(candidate_path)
        if field_name:
            paths_by_name.setdefault(field_name, []).append(candidate_path)
    for entry in raw_evidence if isinstance(raw_evidence, list) else []:
        field_name = _safe_text(entry.get("fieldName")) if isinstance(entry, dict) else ""
        matching_paths = paths_by_name.get(field_name, [])
        path = matching_paths[0] if len(matching_paths) == 1 else ""
        # 兼容已经发出的旧版请求：旧模型响应仍可携带 sourcePath，但新请求不会暴露该字段。
        if not path and isinstance(entry, dict):
            path = _safe_text(entry.get("sourcePath"))
        if not path or path in seen or path not in candidates:
            continue
        seen.add(path)
        evidence.append({"sourcePath": path, "sourceText": _safe_text(candidates[path])})
        if len(evidence) == 5:
            break
    return evidence


def _display_value(value: Any, unit: str) -> str:
    if value is None or value == "":
        return ""
    return f"{value}{unit}" if unit else _safe_text(value)


def _validate_assessment(
    payload: Dict[str, Any],
    expected_items: List[Dict[str, Any]],
    candidates: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """校验模型协议，不把未返回项目伪装成业务 missing。"""
    raw_facts = payload.get("facts")
    if not isinstance(raw_facts, list):
        raise ValueError("模型返回缺少 facts 数组")
    candidate_values = candidates or {}
    expected_by_key = {_safe_text(item.get("semanticItemKey")): item for item in expected_items}
    facts: Dict[str, Dict[str, Any]] = {}
    warnings: List[str] = []
    for raw in raw_facts:
        if not isinstance(raw, dict):
            continue
        semantic_key = _safe_text(raw.get("semanticItemKey"))
        if semantic_key not in expected_by_key:
            warnings.append(f"忽略未知 semanticItemKey={semantic_key or '<empty>'}")
            continue
        if semantic_key in facts:
            raise ValueError(f"模型重复返回 semanticItemKey={semantic_key}")
        item = expected_by_key[semantic_key]
        status = _safe_text(raw.get("status")).lower()
        if status not in {"supported", "contradicted", "missing"}:
            continue
        if status == "supported" and _explanation_indicates_missing(raw):
            status = "missing"
        mapping_method = _safe_text(raw.get("mappingMethod")).lower()
        if mapping_method not in {"direct", "semantic", "composite", "none"}:
            mapping_method = "none" if status == "missing" else "semantic"
        confidence = raw.get("confidence")
        try:
            confidence = max(0.0, min(float(confidence), 1.0))
        except (TypeError, ValueError):
            confidence = 0.0
        branch_applicability = _safe_text(raw.get("branchApplicability")).lower()
        if branch_applicability not in {"applicable", "not_applicable", "unknown", "not_branch"}:
            branch_applicability = "not_branch"
        if status == "missing" and branch_applicability == "applicable":
            branch_applicability = "unknown"
        evidence = _path_evidence(raw.get("evidence"), candidate_values)
        normalized_value = raw.get("normalizedValue")
        normalized_unit = _safe_text(raw.get("normalizedUnit"))
        if status == "missing":
            mapping_method = "none"
            normalized_value = None
            normalized_unit = ""
            evidence = []
        facts[semantic_key] = {
            "semanticItemKey": semantic_key,
            "itemId": _safe_text(item.get("itemId")),
            "status": status,
            "mappingMethod": mapping_method,
            "mappedFields": [entry["sourcePath"] for entry in evidence],
            "normalizedValue": normalized_value,
            "normalizedUnit": normalized_unit,
            "displayValue": _display_value(normalized_value, normalized_unit),
            "confidence": confidence,
            "branchApplicability": branch_applicability,
            "branchReason": _safe_text(raw.get("branchReason")),
            "evidence": evidence,
            "reason": _safe_text(raw.get("reason")),
        }
    omitted = [key for key in expected_by_key if key not in facts]
    return {"facts": facts, "omittedSemanticItemKeys": omitted, "protocolWarnings": warnings}


def _validate_tech_sme_evaluation(payload: Dict[str, Any], candidates: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw_evaluation = payload.get("techSmeEvaluation") if isinstance(payload.get("techSmeEvaluation"), dict) else {}
    metrics: Dict[str, Dict[str, Any]] = {}
    for metric_name in ("techPersonnelRatio", "rdExpenseRevenueRatio", "rdExpenseCostRatio", "classICount", "classIICount"):
        raw_metric = raw_evaluation.get(metric_name) if isinstance(raw_evaluation.get(metric_name), dict) else {}
        value = raw_metric.get("value")
        try:
            value = float(value)
            if metric_name.endswith("Ratio") and not 0 <= value <= 1:
                raise ValueError
            if metric_name.endswith("Count") and value < 0:
                raise ValueError
        except (TypeError, ValueError):
            value = None
        metrics[metric_name] = {
            "value": value,
            "evidence": _path_evidence(raw_metric.get("evidence"), candidates),
            "reason": _safe_text(raw_metric.get("reason")),
        }
    return metrics


def _local_tech_sme_evaluation(enterprise: Dict[str, Any], candidates: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    field_map = {
        "techPersonnelRatio": "科技人员占企业职工总数比例",
        "rdExpenseRevenueRatio": "研发费用占销售收入总额比例",
        "rdExpenseCostRatio": "研发费用占成本费用支出总额比例",
        "classICount": "I类知识产权数量",
        "classIICount": "II类知识产权数量",
    }
    metrics: Dict[str, Dict[str, Any]] = {}
    for metric_name, field_name in field_map.items():
        value = enterprise.get(field_name)
        path = f"$normalized.{field_name}"
        metrics[metric_name] = {
            "value": value,
            "evidence": [{"sourcePath": path, "sourceText": _safe_text(candidates[path])}] if path in candidates and value is not None else [],
            "reason": "政策推荐企业标准字段本地一次性生成。" if value is not None else "企业标准字段未提供该指标。",
        }
    return metrics


def _response_content(response: requests.Response) -> tuple[str, Dict[str, Any]]:
    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"模型响应缺少 choices[0].message.content：{str(exc)[:160]}") from exc
    return _safe_text(content), body if isinstance(body, dict) else {}


def _assess_policy_batch(
    enterprise: Dict[str, Any],
    policy: Dict[str, Any],
    profile: Dict[str, Any],
    items: List[Dict[str, Any]],
    batch_index: int,
    batch_total: int,
    submitted_at: float,
    system_prompt: str,
    headers: Dict[str, str],
    candidates: Dict[str, Any],
    progress_callback: Optional[Callable[[str], None]],
) -> Dict[str, Any]:
    queue_wait_ms = round((time.monotonic() - submitted_at) * 1000)
    user_prompt = _build_prompt(enterprise, policy, profile, items, candidates)
    candidate_chars = len(json.dumps(candidates, ensure_ascii=False, separators=(",", ":"), default=str))
    if progress_callback:
        progress_callback(
            f"模型条件批次开始请求：政策={policy.get('title', '')} 批次={batch_index}/{batch_total} "
            f"batchSize={len(items)} queueWaitMs={queue_wait_ms} promptChars={len(user_prompt)} "
            f"candidateCount={len(candidates)} candidateChars={candidate_chars} workerLimit={SEMANTIC_MATCH_MAX_WORKERS}"
        )
    request_started_at = time.monotonic()
    response = _post_semantic_request(
        headers,
        {
            "model": SEMANTIC_LLM_MODEL_NAME,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False},
        },
    )
    request_ms = round((time.monotonic() - request_started_at) * 1000)
    parse_started_at = time.monotonic()
    content, response_body = _response_content(response)
    model_payload = _sanitize_json(content)
    assessment = _validate_assessment(model_payload, items, candidates)
    parse_ms = round((time.monotonic() - parse_started_at) * 1000)
    usage = response_body.get("usage") if isinstance(response_body.get("usage"), dict) else {}
    choices = response_body.get("choices")
    first_choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    assessment["techSmeEvaluation"] = _validate_tech_sme_evaluation(model_payload, candidates)
    assessment["metrics"] = {
        "queueWaitMs": queue_wait_ms,
        "requestMs": request_ms,
        "parseMs": parse_ms,
        "promptChars": len(user_prompt),
        "responseChars": len(content),
        "promptTokens": usage.get("prompt_tokens"),
        "completionTokens": usage.get("completion_tokens"),
        "totalTokens": usage.get("total_tokens"),
        "finishReason": _safe_text(first_choice.get("finish_reason")),
    }
    if progress_callback:
        progress_callback(
            f"模型条件批次完成：政策={policy.get('title', '')} 批次={batch_index}/{batch_total} "
            f"batchSize={len(items)} requestMs={request_ms} parseMs={parse_ms} responseChars={len(content)} "
            f"promptTokens={usage.get('prompt_tokens')} completionTokens={usage.get('completion_tokens')} "
            f"totalTokens={usage.get('total_tokens')} finishReason={assessment['metrics']['finishReason']} "
            f"returnedCount={len(assessment['facts'])} missingCount={len(assessment['omittedSemanticItemKeys'])}"
        )
    return assessment


def _assess_with_fallback(
    enterprise: Dict[str, Any], policy: Dict[str, Any], profile: Dict[str, Any], items: List[Dict[str, Any]],
    batch_index: int, batch_total: int, system_prompt: str, headers: Dict[str, str], candidates: Dict[str, Any],
    progress_callback: Optional[Callable[[str], None]], budget: _FallbackBudget, is_fallback: bool = False,
) -> Dict[str, Any]:
    if is_fallback:
        budget.reserve()
    try:
        assessment = _assess_policy_batch(
            enterprise, policy, profile, items, batch_index, batch_total, time.monotonic(), system_prompt, headers, candidates, progress_callback,
        )
        omitted = assessment["omittedSemanticItemKeys"]
        if not omitted:
            return assessment
        if len(items) == 1:
            raise SemanticMatchProtocolError(
                f"模型未返回规则项 semanticItemKey={items[0]['semanticItemKey']}。",
                error_type="semantic_model_protocol_error", attempts=1,
            )
        missing_items = [item for item in items if item["semanticItemKey"] in omitted]
        if progress_callback:
            progress_callback(
                f"模型条件批次回退：reason=omitted_items originalSize={len(items)} childSizes="
                f"{','.join('1' for _ in missing_items)} fallbackDepth=single"
            )
        for item in missing_items:
            child = _assess_with_fallback(
                enterprise, policy, profile, [item], batch_index, batch_total, system_prompt, headers, candidates,
                progress_callback, budget, True,
            )
            assessment["facts"].update(child["facts"])
        assessment["omittedSemanticItemKeys"] = []
        return assessment
    except (SemanticMatchRetryableError, ValueError) as exc:
        if len(items) == 1:
            if isinstance(exc, SemanticMatchRetryableError):
                raise
            raise SemanticMatchProtocolError(str(exc), error_type="semantic_model_protocol_error", attempts=1) from exc
        midpoint = len(items) // 2
        children = [items[:midpoint], items[midpoint:]]
        if progress_callback:
            progress_callback(
                f"模型条件批次回退：reason={getattr(exc, 'error_type', 'semantic_model_protocol_error')} "
                f"originalSize={len(items)} childSizes={len(children[0])},{len(children[1])} fallbackDepth=binary"
            )
        merged = {"facts": {}, "techSmeEvaluation": {}, "metrics": {}}
        for child_items in children:
            child = _assess_with_fallback(
                enterprise, policy, profile, child_items, batch_index, batch_total, system_prompt, headers, candidates,
                progress_callback, budget, True,
            )
            merged["facts"].update(child["facts"])
            merged["techSmeEvaluation"].update(child.get("techSmeEvaluation", {}))
        return merged


def assess_enterprise_policy(
    enterprise: Dict[str, Any], policy: Dict[str, Any], profile: Dict[str, Any],
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """按政策微批提取事实；默认批次为 1，模型请求全局受 MAX_WORKERS 限制。"""
    if not SEMANTIC_LLM_ENABLED:
        return {"enabled": False, "model": "", "facts": {}, "error": "模型语义判定已禁用。"}
    if not SEMANTIC_LLM_API_URL or not SEMANTIC_LLM_MODEL_NAME:
        raise RuntimeError("模型语义判定配置缺失，请检查 API_URL / MODEL_NAME")
    system_prompt = SEMANTIC_SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
    expected_items = _policy_items(profile, policy)
    if not expected_items:
        return {"enabled": True, "model": SEMANTIC_LLM_MODEL_NAME, "facts": {}, "techSmeEvaluation": {}}
    source_candidates = _model_input_candidates(enterprise)
    headers = {"Content-Type": "application/json"}
    if SEMANTIC_LLM_API_KEY:
        headers["Authorization"] = f"Bearer {SEMANTIC_LLM_API_KEY}"
    batches = [expected_items[index:index + SEMANTIC_MATCH_BATCH_SIZE] for index in range(0, len(expected_items), SEMANTIC_MATCH_BATCH_SIZE)]
    budget = _FallbackBudget(len(batches))
    futures: List[Future] = [
        _semantic_executor().submit(
            _assess_with_fallback, enterprise, policy, profile, batch, batch_index, len(batches), system_prompt, headers,
            _prompt_candidate_snapshot(enterprise, batch, source_candidates), progress_callback, budget,
        )
        for batch_index, batch in enumerate(batches, start=1)
    ]
    facts: Dict[str, Dict[str, Any]] = {}
    internal_evidence_candidates = _internal_evidence_candidates(enterprise, source_candidates)
    tech_sme_evaluation = _local_tech_sme_evaluation(enterprise, internal_evidence_candidates) if enterprise.get("_source_type") == "policy_recommendation_api_record" else {}
    for future in as_completed(futures):
        assessment = future.result()
        facts.update(assessment["facts"])
        if not tech_sme_evaluation:
            for metric_name, metric in assessment.get("techSmeEvaluation", {}).items():
                if metric.get("value") is not None or metric.get("evidence") or metric.get("reason"):
                    tech_sme_evaluation[metric_name] = metric
    if len(facts) != len(expected_items):
        raise SemanticMatchRetryableError(
            f"语义事实覆盖不完整：已返回 {len(facts)}/{len(expected_items)} 项。",
            error_type="semantic_model_incomplete_facts", attempts=budget.used,
        )
    if progress_callback:
        progress_callback(
            f"企业匹配模型汇总：initialBatchCount={len(batches)} totalRequestCount={len(batches) + budget.used} "
            f"fallbackCount={budget.used} batchSize={SEMANTIC_MATCH_BATCH_SIZE} semanticWorkers={SEMANTIC_MATCH_MAX_WORKERS}"
        )
    return {
        "enabled": True, "model": SEMANTIC_LLM_MODEL_NAME, "facts": facts,
        "techSmeEvaluation": tech_sme_evaluation,
        "semanticBatchSize": SEMANTIC_MATCH_BATCH_SIZE,
        "initialBatchCount": len(batches), "totalRequestCount": len(batches) + budget.used, "fallbackCount": budget.used,
    }
