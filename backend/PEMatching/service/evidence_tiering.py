"""运行时证据分层。

规则表只提供政策规则。本模块仅根据运行时企业事实、证据映射和校验结果
确定证据质量，不读取规则表的 ruleKind，也不计算业务条件分。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


ASSUMED_PASS_SOURCES = {"default_missing_assumed", "tech_sme_evaluation_partial_assumed"}
REJECTED_ERROR_CODES = {"semantic_evidence_incompatible", "semantic_evidence_path_invalid"}
SUPPORTING_MAPPING_METHODS = {"proxy", "reference", "supporting"}
SUPPORTING_PASS_SOURCES = {"reference_hit", "supporting_hit"}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


@dataclass(frozen=True)
class EvidenceTierResult:
    tier: str
    bucket: str
    reason: str
    evidence_backed: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "evidenceTier": self.tier,
            "scoreBucket": self.bucket,
            "bucketReason": self.reason,
            "evidenceBacked": self.evidence_backed,
        }


def classify_runtime_evidence(evaluated: Dict[str, Any]) -> EvidenceTierResult:
    """根据已执行条件的运行时事实确定证据层级。"""
    eval_status = _text(evaluated.get("status")).lower()
    pass_source = _text(evaluated.get("pass_source")).lower()
    fact = evaluated.get("semantic_fact") if isinstance(evaluated.get("semantic_fact"), dict) else {}
    fact_status = _text(fact.get("status")).lower()
    mapping_method = _text(fact.get("mappingMethod")).lower()
    error_code = _text(fact.get("errorCode")).lower()

    if eval_status == "skipped":
        return EvidenceTierResult("S_skipped", "excluded", "条件分支不适用于当前企业。", False)
    if eval_status == "not_evaluable":
        return EvidenceTierResult("N_not_evaluable", "excluded", "规则目标、单位或结构不可可靠计算。", False)
    if error_code in REJECTED_ERROR_CODES:
        has_independent_local_value = bool(evaluated.get("resolved_values"))
        if has_independent_local_value:
            return EvidenceTierResult(
                "A1_exact",
                "verified",
                "模型证据被拒绝，但本地标准企业字段可独立完成确定性比较。",
                True,
            )
        return EvidenceTierResult("R_rejected", "unverified", f"运行时证据校验拒绝：{error_code}。", False)
    if pass_source in ASSUMED_PASS_SOURCES or fact_status == "missing":
        local_values = evaluated.get("resolved_values") or []
        if not local_values or pass_source in ASSUMED_PASS_SOURCES:
            return EvidenceTierResult("U_missing", "unverified", "企业缺少可核验事实，按缺失条件处理。", False)
    if mapping_method in SUPPORTING_MAPPING_METHODS or pass_source in SUPPORTING_PASS_SOURCES:
        return EvidenceTierResult("B_supporting", "supporting", "存在运行时辅助证据，但不能作为等价事实证明。", True)
    if fact.get("localDerived") or mapping_method == "composite" or pass_source.startswith("tech_sme_evaluation"):
        return EvidenceTierResult("A2_derived", "verified", "由本地确定性公式或完整组合字段推导。", True)

    has_runtime_value = bool(evaluated.get("resolved_values")) or evaluated.get("enterprise_value") is not None
    has_fact_evidence = bool(fact.get("mappedFields")) or bool(fact.get("evidence"))
    if has_runtime_value or has_fact_evidence or fact_status in {"supported", "contradicted"}:
        return EvidenceTierResult("A1_exact", "verified", "运行时企业事实与政策条件可直接比较。", True)

    return EvidenceTierResult("U_missing", "unverified", "未形成可核验的运行时企业事实。", False)
