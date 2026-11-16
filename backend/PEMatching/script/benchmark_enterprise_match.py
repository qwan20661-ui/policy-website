"""比较两个单企业匹配 runId 归档，输出性能与业务结果 A/B 差异。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


FACT_FIELDS = (
    "status",
    "normalizedValue",
    "normalizedUnit",
    "branchApplicability",
    "branchReason",
)
RESULT_FIELDS = ("是否推荐", "hard_pass", "final_score", "score_A", "score_B", "coeff_C")


def _load_archive(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict) or not isinstance(payload.get("matchResult"), dict):
        raise ValueError(f"不是单企业匹配归档：{path}")
    return payload


def _policy_results(archive: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    results = archive["matchResult"].get("政策匹配结果", [])
    return {
        str(item.get("fusionPolicyId") or item.get("政策标题") or index): item
        for index, item in enumerate(results)
        if isinstance(item, dict)
    }


def _semantic_facts(policy_result: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    facts = policy_result.get("模型语义事实", {})
    if not isinstance(facts, dict):
        return {}
    # 同时兼容新键和唯一 itemId 展示别名；有 semanticItemKey 的对象才参与严格对照。
    return {
        key: value
        for key, value in facts.items()
        if isinstance(value, dict) and str(value.get("semanticItemKey") or key) == key
    }


def _evidence_paths(fact: Dict[str, Any]) -> List[str]:
    return [
        str(entry.get("sourcePath"))
        for entry in fact.get("evidence", [])
        if isinstance(entry, dict) and entry.get("sourcePath")
    ]


def _diff_fact(baseline: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    diff = {
        field: {"baseline": baseline.get(field), "candidate": candidate.get(field)}
        for field in FACT_FIELDS
        if baseline.get(field) != candidate.get(field)
    }
    paths_before = _evidence_paths(baseline)
    paths_after = _evidence_paths(candidate)
    if paths_before != paths_after:
        diff["evidencePaths"] = {"baseline": paths_before, "candidate": paths_after}
    return diff


def compare_archives(baseline_archive: Dict[str, Any], candidate_archive: Dict[str, Any]) -> Dict[str, Any]:
    baseline_policies = _policy_results(baseline_archive)
    candidate_policies = _policy_results(candidate_archive)
    policy_diffs: List[Dict[str, Any]] = []
    for policy_key in sorted(set(baseline_policies) | set(candidate_policies)):
        baseline = baseline_policies.get(policy_key)
        candidate = candidate_policies.get(policy_key)
        if baseline is None or candidate is None:
            policy_diffs.append({"policy": policy_key, "difference": "policy_missing", "baselinePresent": baseline is not None, "candidatePresent": candidate is not None})
            continue
        result_diff = {
            field: {"baseline": baseline.get(field), "candidate": candidate.get(field)}
            for field in RESULT_FIELDS
            if baseline.get(field) != candidate.get(field)
        }
        facts_before = _semantic_facts(baseline)
        facts_after = _semantic_facts(candidate)
        fact_diffs: Dict[str, Any] = {}
        for semantic_key in sorted(set(facts_before) | set(facts_after)):
            before = facts_before.get(semantic_key)
            after = facts_after.get(semantic_key)
            if before is None or after is None:
                fact_diffs[semantic_key] = {"difference": "fact_missing", "baselinePresent": before is not None, "candidatePresent": after is not None}
                continue
            diff = _diff_fact(before, after)
            if diff:
                fact_diffs[semantic_key] = diff
        if result_diff or fact_diffs:
            policy_diffs.append({"policy": policy_key, "resultDiff": result_diff, "semanticFactDiff": fact_diffs})
    return {
        "baselineRunId": baseline_archive.get("runId"),
        "candidateRunId": candidate_archive.get("runId"),
        "baselinePolicyCount": len(baseline_policies),
        "candidatePolicyCount": len(candidate_policies),
        "differentPolicyCount": len(policy_diffs),
        "policyDiffs": policy_diffs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="对照两份企业匹配 runId 归档的业务结果")
    parser.add_argument("--baseline", required=True, type=Path, help="优化前完整归档 JSON")
    parser.add_argument("--candidate", required=True, type=Path, help="优化后完整归档 JSON")
    parser.add_argument("--output", type=Path, help="可选：写入差异报告 JSON")
    arguments = parser.parse_args()
    report = compare_archives(_load_archive(arguments.baseline), _load_archive(arguments.candidate))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.output:
        arguments.output.write_text(text, encoding="utf-8")
    print(text)
    return 0 if report["differentPolicyCount"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
