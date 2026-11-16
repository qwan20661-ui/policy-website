# -*- coding: utf-8 -*-
"""Agent 循环控制与 deterministic 模式。

两条路径产出同一结构的响应：

  agent 模式        — LLM 多轮 tool-calling 编排，可换标签组合重试检索
  deterministic 模式 — 固定顺序执行，不调模型

**deterministic 是一等公民**（开发计划决策 3）：可由调用方直接指定、是回归测试
的主要手段、是「双模式推荐集合重合度 ≥90%」这一验收指标的基准侧。

分数、档位、命中规则一律从 SessionState 取，不信模型转述——模型只出
reason / note / summary 三个文案字段。这是防越权的结构性保障。
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from service import agent_tools as at
from service import fields as F
from service import policy_index as pi
from service.eligibility.gate_service import evaluate_policies
from service.event_log import NullEventLog, RequestEventLog
from service.llm_client import (
    LLMError,
    chat_with_retry,
    extract_json_content,
    parse_tool_arguments,
)
from service.paths import PROMPTS_DIR, env_int
from service.policy_evidence import rule_evidence_for_policy
from service.profile import build_profile
from service.rules import evaluate_rules
from service.scoring import build_secondary_sort_key, filter_policy_by_qualification, is_ancient_policy

PROMPT_PATH = PROMPTS_DIR / "agent_system_prompt.md"

MODE_AGENT = "agent"
MODE_DETERMINISTIC = "deterministic"

DEGRADE_MODEL_IDLE = "model_idle"
DEGRADE_UNPARSEABLE = "unparseable_output"
DEGRADE_LLM_UNAVAILABLE = "llm_unavailable"
DEGRADE_TRUNCATED = "truncated"

# 每组检索的候选数上限。
# 实测教训：原为 max(top_n*3, 30)，但检索侧对无行业值企业会返回近百条同权重政策，
# 排序退化为按 fusionPolicyId 稳定排序，30 条截断会切掉 ID 偏后的高分政策
# （agent 换标签检索能捞到、deterministic 捞不到，双模式重合度因此掉到 70%）。
# 政策库当前 110 条，评分是确定性计算且 25000 条/秒，多算几十条成本可忽略，
# 故放宽到覆盖全库。政策库到千级时改为按标签建倒排索引（开发计划 11.4）。
CANDIDATE_MULTIPLIER = 6
CANDIDATE_FLOOR = 120

# 上下文压缩：保留最近几轮完整往返，更早的工具结果压成摘要。
# 实测不压缩时 10 轮 prompt 涨到 19812 token，模型生成变慢直至 240 秒超时。
KEEP_RECENT_TURNS = 2
TOOL_RESULT_KEEP = 400

# 这些状态由资格门禁判定：not_evaluable 静默丢弃；not_recommended（硬性不通过/符合度不足）单列排除。


@lru_cache(maxsize=1)
def load_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"Agent prompt 不存在：{PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 响应组装：分数只从 state 取
# --------------------------------------------------------------------------


def _assemble(
    state: at.SessionState,
    narrative: Optional[Dict[str, Any]],
    mode: str,
    meta_extra: Dict[str, Any],
    event_log: Any,
) -> Dict[str, Any]:
    """组装最终响应。

    narrative 是模型产出的文案（可为 None）。分数、档位、命中规则全部来自
    state.scores 中的资格门禁评估与 state.rule_result——模型转述的数值一律不采用。
    """
    profile = state.profile or {}
    rule_result = state.rule_result or {}
    gate = rule_result.get("gateResult") or {}

    rule_evidence_by_policy = _rules_by_policy(state)
    narrative = narrative if isinstance(narrative, dict) else {}
    reasons = {
        F.text(item.get("fusionPolicyId")): F.text(item.get("reason"))
        for item in (narrative.get("recommendations") or [])
        if isinstance(item, dict)
    }
    tags_index = pi.load_policy_tags()

    # ---- 按资格门禁分桶：not_evaluable 静默丢弃，not_recommended 单列排除 ----
    recommendable: List[Dict[str, Any]] = []
    excluded_by_gate: List[Dict[str, Any]] = []
    for item in state.scores.values():
        eligibility = item.get("eligibility") or {}
        status = F.text(eligibility.get("gateStatus"))
        if status == "not_evaluable":
            continue  # 无申报条件档案或不可评估，不展示
        if status == "not_recommended":
            excluded_by_gate.append(item)
            continue
        # direct_push / needs_supplement 进入可推荐集合
        recommendable.append({
            **item,
            "policyEvidence": rule_evidence_by_policy.get(item["fusionPolicyId"], []),
        })

    # ---- 业务规则过滤（调整后的推荐规则）----
    # 规则1：前年以前（≥2年前截止）的政策直接过滤不推。
    # 规则2/3：企业已具备某资质（科小/高企/专精特新/小巨人）时，
    #          按标题关键词排除对应认定政策（硬性排除）。
    # 研发补助类：企业无专利时不推。
    qualifications = (profile or {}).get("qualifications") or {}
    metrics = (profile or {}).get("metrics") or {}
    excluded_by_policy_rule: List[Dict[str, Any]] = []
    kept_by_policy_rule: List[Dict[str, Any]] = []
    for item in recommendable:
        reason = None
        tags_rec = item.get("policy_tags") or tags_index.get(item["fusionPolicyId"]) or {}
        window = tags_rec.get("applicationWindow")
        if is_ancient_policy(window, state.today):
            reason = f"政策申报期在前年以前（{window.get('endDate')} 截止），按规则不推"
        else:
            title = F.text(tags_rec.get("title") or item.get("title"))
            audience = (tags_rec.get("applicability") or {}).get("audience")
            reason = filter_policy_by_qualification(title, qualifications, metrics, audience)
        if reason:
            item["_excludedReason"] = reason
            excluded_by_policy_rule.append(item)
        else:
            kept_by_policy_rule.append(item)
    if excluded_by_policy_rule:
        event_log.write("policy_rule_filter", {
            "excludedCount": len(excluded_by_policy_rule),
            "excludedPolicyIds": [
                {"policyId": it["fusionPolicyId"], "reason": it["_excludedReason"]}
                for it in excluded_by_policy_rule
            ],
        })

    # ---- 排序：按申报要求符合度降序，同分用次级键 ----
    def deterministic_sort_key(item: Dict[str, Any]) -> tuple:
        eligibility = item.get("eligibility") or {}
        score = eligibility.get("conditionMatchScore")
        policy_tags = item.get("policy_tags") or {}
        return (
            0.0 if score is None else -float(score),
            *build_secondary_sort_key(policy_tags, state.today),
            F.text(item.get("fusionPolicyId")),
        )

    kept_by_policy_rule.sort(key=deterministic_sort_key)
    excluded_by_gate.sort(key=deterministic_sort_key)

    recommendations: List[Dict[str, Any]] = []
    for rank, item in enumerate(kept_by_policy_rule[: state.top_n], start=1):
        policy_id = item["fusionPolicyId"]
        eligibility = item.get("eligibility") or {}
        summary = next(
            (s for s in state.searched if s["fusionPolicyId"] == policy_id), {}
        )
        policy_tags = item.get("policy_tags") or {}
        recommendations.append({
            "fusionPolicyId": policy_id,
            "rank": rank,
            "policyTitle": item.get("title") or policy_tags.get("title"),
            "tags": summary.get("tags") or {},
            "applicationWindow": summary.get("applicationWindow") or {},
            "conditionMatchScore": eligibility.get("conditionMatchScore"),
            "gateStatus": eligibility.get("gateStatus"),
            "directPushAllowed": eligibility.get("directPushAllowed"),
            "gateReasons": eligibility.get("gateReasons") or [],
            "conditionDetails": eligibility.get("conditionDetails") or [],
            "matchedRules": [entry["ruleId"] for entry in item.get("policyEvidence") or []],
            "policyEvidence": item.get("policyEvidence") or [],
            # 模型常只给部分政策写理由，其余用确定性模板兜底——空白理由对调用方无用
            "reason": reasons.get(policy_id) or _template_reason(eligibility),
            "reasonSource": "model" if reasons.get(policy_id) else "template",
        })

    supplement = _supplement_suggestions(state, narrative)
    eligibility_exclusions = [
        {
            "fusionPolicyId": it["fusionPolicyId"],
            "policyTitle": it.get("title") or (it.get("policy_tags") or {}).get("title"),
            "gateStatus": F.text((it.get("eligibility") or {}).get("gateStatus")),
            "gateReasons": (it.get("eligibility") or {}).get("gateReasons") or [],
            "action": it.get("_excludedReason") or "硬性条件不满足或符合度不足，不推荐申报",
            "reasons": [it.get("_excludedReason")] if it.get("_excludedReason")
                       else (it.get("eligibility") or {}).get("gateReasons") or [],
        }
        for it in excluded_by_gate + excluded_by_policy_rule
    ]

    # eligibilityResult：不含 not_evaluable，供批跑/导出/接口消费
    eligibility_meta = getattr(state, "eligibility_meta", None) or {}
    eligibility_evaluations = [
        item.get("eligibility")
        for item in state.scores.values()
        if F.text((item.get("eligibility") or {}).get("gateStatus"))
        in ("direct_push", "needs_supplement", "not_recommended")
    ]
    eligibility_result = {
        "eligibilityEvaluations": eligibility_evaluations,
        "eligibilitySchemaVersion": eligibility_meta.get("eligibilitySchemaVersion"),
        "ruleManifestHash": eligibility_meta.get("ruleManifestHash"),
    }

    data = {
        "enterprise": {
            "enterpriseName": profile.get("enterpriseName"),
            "creditCode": profile.get("creditCode"),
            "tags": profile.get("tags") or {},
            "tagBasis": profile.get("tagBasis") or {},
            "metrics": profile.get("metrics") or {},
            "qualifications": profile.get("qualifications") or {},
            "eligibilityFacts": profile.get("eligibilityFacts") or {},
            "credit": profile.get("credit") or {},
            "fieldAvailability": profile.get("fieldAvailability") or {},
            "dataCompleteness": profile.get("dataCompleteness") or {},
        },
        "gateResult": gate,
        "channelResults": rule_result.get("channels") or {},
        "aggregatedSignals": rule_result.get("aggregatedSignals") or {},
        "recommendations": recommendations,
        "eligibilityExclusions": eligibility_exclusions,
        "supplementSuggestions": supplement,
        "unmatchedSignals": at.collect_unmatched_policy_signals(state),
        "summary": F.text(narrative.get("summary")),
        "candidateCount": len(state.searched),
        "scoredCount": len(state.scores),
        "recommendableCount": len(kept_by_policy_rule),
        "eligibilityResult": eligibility_result,
    }

    completeness = profile.get("dataCompleteness") or {}
    meta = {
        "mode": mode,
        "requestId": getattr(event_log, "request_id", None),
        "elapsedMs": event_log.elapsed_ms() if event_log else None,
        "toolCallCount": len(state.tool_calls),
        "dataCompleteness": {
            "availableFields": completeness.get("available"),
            "unknownFields": completeness.get("unknown"),
            "totalFields": completeness.get("total"),
        },
        **meta_extra,
    }
    return {"data": data, "meta": meta}


def _template_reason(eligibility: Dict[str, Any]) -> str:
    """确定性模板理由。

    模型没给理由时用它兜底：把资格门禁里满足/未达标/缺数据的条件串起来，
    再补上符合度与档位。全部来自确定性计算，不含推测。
    """
    status = F.text(eligibility.get("gateStatus"))
    score = eligibility.get("conditionMatchScore")
    parts: List[str] = []
    for detail in (eligibility.get("conditionDetails") or [])[:3]:
        name = F.text(detail.get("itemName"))
        if not name:
            continue
        eval_status = F.text(detail.get("evalStatus"))
        if eval_status == "pass":
            parts.append(f"{name}满足")
        elif eval_status == "fail":
            parts.append(f"{name}未达标")
        else:
            parts.append(f"{name}缺数据待核实")

    gate_reasons = [F.text(r) for r in (eligibility.get("gateReasons") or []) if F.text(r)]
    if not parts:
        parts.extend(gate_reasons)

    tail = f"申报要求符合度 {score}，档位 {status}" if score is not None else f"档位 {status}"
    if not parts:
        return f"{tail}。"
    return "；".join(parts) + "。" + tail + "。"


def _rules_by_policy(state: at.SessionState) -> Dict[str, List[Dict[str, Any]]]:
    """政策 → 有关键词证据的命中规则，拒绝 A/C 粗标签假背书。"""
    if not state.rule_result:
        return {}
    matched: List[Dict[str, Any]] = []
    for channel in (state.rule_result.get("channels") or {}).values():
        matched.extend(channel.get("matched") or [])

    mapping: Dict[str, List[Dict[str, Any]]] = {}
    tags_index = pi.load_policy_tags()
    for policy_id in state.scores:
        evidence = rule_evidence_for_policy(matched, tags_index.get(policy_id) or {})
        if evidence:
            mapping[policy_id] = evidence
    return mapping


def _supplement_suggestions(
    state: at.SessionState, narrative: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """补数建议。字段与解锁规则由系统从 skipped 规则算出，note 取模型文案。"""
    if not state.rule_result:
        return []

    notes = {
        F.text(item.get("field")): F.text(item.get("note"))
        for item in (narrative.get("supplementSuggestions") or [])
        if isinstance(item, dict)
    }

    by_field: Dict[str, Dict[str, Any]] = {}
    for channel in (state.rule_result.get("channels") or {}).values():
        for item in channel.get("skipped") or []:
            if item.get("permanent"):
                continue  # 接口契约就没这个字段，补不了
            for field in item.get("missingFields") or []:
                entry = by_field.setdefault(field, {
                    "field": field, "unlocksRules": [], "note": notes.get(field, ""),
                })
                if item["ruleId"] not in entry["unlocksRules"]:
                    entry["unlocksRules"].append(item["ruleId"])

    suggestions = sorted(
        by_field.values(), key=lambda item: (-len(item["unlocksRules"]), item["field"])
    )
    for suggestion in suggestions:
        suggestion["unlocksRules"].sort()
    return suggestions


# --------------------------------------------------------------------------
# deterministic 模式
# --------------------------------------------------------------------------


def run_deterministic(
    record: Dict[str, Any],
    keyword: str = "",
    enable_stage2: bool = False,
    top_n: int = 20,
    today: Optional[date] = None,
    event_log: Any = None,
    explain_with_llm: bool = False,
    use_llm_evidence: bool | None = None,
) -> Dict[str, Any]:
    """固定顺序执行，不调模型（explain_with_llm=True 时仅调一次写文案）。

    顺序：build_profile → evaluate_rules → search_policies(用 aggregatedSignals)
          → 逐候选按申报要求评估（硬性先行，硬性不过不推荐）→ 按符合度排序取 top_n。
    """
    log = event_log or NullEventLog()
    state = at.SessionState(record, keyword, enable_stage2, today, top_n)
    state.use_llm_evidence = use_llm_evidence
    log.write("mode", {"mode": MODE_DETERMINISTIC, "keyword": keyword,
                       "enableStage2": enable_stage2, "topN": top_n})

    state.profile = build_profile(record, today=state.today)
    log.write("profile", {"tags": state.profile["tags"],
                          "dataCompleteness": state.profile["dataCompleteness"]})

    state.rule_result = evaluate_rules(state.profile, enable_stage2=enable_stage2)
    log.write("rules", {
        "matched": state.rule_result["matchedRuleIds"],
        "skipped": state.rule_result["skippedRuleIds"],
        "gate": state.rule_result["gateResult"],
    })

    if not state.rule_result["gateResult"]["passed"]:
        log.write("gate_blocked", state.rule_result["gateResult"])
        return _assemble(state, None, MODE_DETERMINISTIC,
                         {"degraded": False, "truncated": False, "turns": 0}, log)

    candidates = _search_candidates(state, log)
    state.searched = list(candidates)
    if at.is_enabled(use_llm_evidence):
        state.llm_window_ids = at.llm_window_ids(candidates, state.top_n)
    _score_candidates(state, candidates, use_llm_evidence=use_llm_evidence, event_log=log)
    log.write("scored", {"count": len(state.scores)})

    narrative = None
    if explain_with_llm:
        narrative = _explain_only(state, log)

    return _assemble(state, narrative, MODE_DETERMINISTIC,
                     {"degraded": False, "truncated": False, "turns": 0}, log)


def _score_candidates(
    state: at.SessionState,
    candidates: List[Dict[str, Any]],
    *,
    use_llm_evidence: bool | None = None,
    event_log: Any = None,
) -> None:
    """对候选政策统一走资格门禁评分，写入 state.scores。

    deterministic 与 agent 补齐路径共用，保证两模式看到同一评分口径。
    LLM 开启时只评估精评窗口（state.llm_window_ids）内的候选，窗口外静默丢弃；
    窗口内逐政策并发评估，控制模型调用耗时。
    """
    if not candidates:
        return
    use_llm_evidence = at.is_enabled(use_llm_evidence)
    if use_llm_evidence:
        window_ids = state.llm_window_ids
        if window_ids is not None:
            candidates = [
                candidate for candidate in candidates
                if candidate["fusionPolicyId"] in window_ids
            ]
    if not candidates:
        return
    policy_ids = [candidate["fusionPolicyId"] for candidate in candidates]
    dropped = len(state.searched) - len(candidates) if use_llm_evidence else 0
    eligibility_result = at.evaluate_policy_ids(
        state.profile, policy_ids, use_llm_evidence=use_llm_evidence,
    )
    evals = {
        F.text(item.get("fusionPolicyId")): item
        for item in (eligibility_result.get("eligibilityEvaluations") or [])
        if isinstance(item, dict)
    }
    tags_index = pi.load_policy_tags()
    for candidate in candidates:
        policy_id = candidate["fusionPolicyId"]
        evaluation = evals.get(policy_id)
        if evaluation is None:
            continue
        state.scores[policy_id] = {
            "fusionPolicyId": policy_id,
            "title": candidate["title"],
            "policy_tags": tags_index.get(policy_id) or {},
            "eligibility": evaluation,
        }
    state.eligibility_meta = {
        "eligibilitySchemaVersion": eligibility_result.get("eligibilitySchemaVersion"),
        "ruleManifestHash": eligibility_result.get("ruleManifestHash"),
    }
    if event_log is not None:
        statuses = {
            F.text(item.get("gateStatus")): 0 for item in evals.values()
        }
        for item in evals.values():
            statuses[F.text(item.get("gateStatus"))] = statuses.get(F.text(item.get("gateStatus")), 0) + 1
        event_log.write("eligibility_scored", {
            "policyCount": len(policy_ids),
            "statuses": statuses,
            "useLlmEvidence": bool(use_llm_evidence),
            "windowDropped": dropped,
        })


def _search_candidates(state: at.SessionState, log: Any) -> List[Dict[str, Any]]:
    """多组标签检索并合并去重。

    单组检索会漏政策：实测 agent 模式换标签多次检索能找到比单次检索**更高分**的
    政策（如 98.33 分的技术先进型服务企业认定），说明单组口径覆盖不足。
    这里用四组互补口径覆盖：

      1. 全标签精确组   — 领域 + 阶段 + 属地 + 支持方式，最贴合企业
      2. 领域组         — 只按 C 大类与子类，放宽阶段与属地
      3. 阶段属地组     — 只按 B 与属地，捞跨领域的普惠政策
      4. 规则信号组     — 用规则聚合的 A 标签与关键词，捞规则命中方向的政策

    这是 deterministic 与 agent 共用的基准口径，也是双模式重合度能对齐的前提。
    """
    profile = state.profile or {}
    rule_result = state.rule_result or {}
    tags = profile.get("tags") or {}
    signals = rule_result.get("aggregatedSignals") or {}
    limit = max(state.top_n * CANDIDATE_MULTIPLIER, CANDIDATE_FLOOR)

    groups: List[Tuple[str, Dict[str, Any]]] = [
        ("全标签", {
            "a_tags": signals.get("aTags") or None,
            "c_tags": tags.get("C") or None,
            "c_sub_tags": tags.get("cSub") or None,
            "b_tags": tags.get("B") or None,
            "region_tags": tags.get("regions") or None,
        }),
        ("领域", {
            "c_tags": tags.get("C") or None,
            "c_sub_tags": tags.get("cSub") or None,
        }),
        ("阶段属地", {
            "b_tags": tags.get("B") or None,
            "region_tags": tags.get("regions") or None,
        }),
        ("规则信号", {
            "a_tags": signals.get("aTags") or None,
            "region_tags": tags.get("regions") or None,
        }),
    ]

    merged: Dict[str, Dict[str, Any]] = {}
    per_group: Dict[str, int] = {}
    for name, criteria in groups:
        if not any(criteria.values()):
            continue
        found = pi.search_policies(keyword=state.keyword, limit=limit, **criteria)
        per_group[name] = len(found)
        for item in found:
            merged.setdefault(item["fusionPolicyId"], item)

    candidates = sorted(merged.values(), key=lambda item: item["fusionPolicyId"])
    log.write("search", {
        "perGroup": per_group,
        "merged": len(candidates),
        "policyIds": [item["fusionPolicyId"] for item in candidates],
    })
    return candidates


def _explain_only(state: at.SessionState, log: Any) -> Optional[Dict[str, Any]]:
    """降级后仍尝试让模型写解释（单轮，不带 tools）。失败返回 None，用模板兜底。"""
    def score_key(item: Dict[str, Any]) -> float:
        return -float((item.get("eligibility") or {}).get("conditionMatchScore") or 0.0)

    top = sorted(state.scores.values(), key=score_key)[: state.top_n]
    if not top:
        return None

    brief = [
        {
            "fusionPolicyId": item["fusionPolicyId"],
            "title": item["title"],
            "conditionMatchScore": (item.get("eligibility") or {}).get("conditionMatchScore"),
            "gateStatus": (item.get("eligibility") or {}).get("gateStatus"),
            "conditionDetails": [
                {
                    "name": d.get("itemName"),
                    "evalStatus": d.get("evalStatus"),
                    "score": d.get("score"),
                }
                for d in ((item.get("eligibility") or {}).get("conditionDetails") or [])[:10]
            ],
        }
        for item in top
    ]
    profile = state.profile or {}
    user = json.dumps({
        "enterprise": {
            "name": profile.get("enterpriseName"),
            "tags": profile.get("tags"),
            "unknownFields": profile.get("unknownFields"),
        },
        "policies": brief,
    }, ensure_ascii=False)

    try:
        message = chat_with_retry(
            [
                {"role": "system", "content": load_prompt()},
                {"role": "user", "content":
                    "以下是已算好的匹配结果，请只输出第五节要求的 JSON 文案"
                    "（reason / note / summary），不要修改任何分数：\n" + user},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            event_log=log,
        )
    except LLMError as exc:
        log.write("explain_failed", {"error": str(exc)[:300]})
        return None

    parsed = extract_json_content(message)
    return parsed if isinstance(parsed, dict) else None


# --------------------------------------------------------------------------
# agent 模式
# --------------------------------------------------------------------------


def _initial_messages(record: Dict[str, Any], state: at.SessionState) -> List[Dict[str, str]]:
    hints = {
        "enterpriseName": F.text(record.get("enterpriseName")),
        "keyword": state.keyword or "（未提供查询关键词）",
        "enableStage2": state.enable_stage2,
        "topN": state.top_n,
        "today": state.today.isoformat(),
    }
    return [
        {"role": "system", "content": load_prompt()},
        {
            "role": "user",
            "content": "请为以下企业匹配可申报的科技政策。企业数据已在会话中，"
                       "调用 build_enterprise_profile 读取。\n"
                       + json.dumps(hints, ensure_ascii=False),
        },
    ]


def run_agent(
    record: Dict[str, Any],
    keyword: str = "",
    enable_stage2: bool = False,
    top_n: int = 20,
    today: Optional[date] = None,
    event_log: Any = None,
    max_turns: Optional[int] = None,
    use_llm_evidence: bool | None = None,
) -> Dict[str, Any]:
    """多轮 tool-calling 编排。

    四种情况降级到 deterministic：模型连续多轮不调工具、输出连续不可解析、
    超轮次、LLM 彻底失败。降级后仍返回 200 与完整分数。
    """
    log = event_log or NullEventLog()
    turns_limit = max_turns or env_int("V2_AGENT_MAX_TURNS", 12)
    idle_limit = env_int("V2_AGENT_IDLE_LIMIT", 2)

    state = at.SessionState(record, keyword, enable_stage2, today, top_n)
    state.use_llm_evidence = use_llm_evidence
    log.write("mode", {"mode": MODE_AGENT, "keyword": keyword,
                       "enableStage2": enable_stage2, "topN": top_n,
                       "maxTurns": turns_limit})

    messages = _initial_messages(record, state)
    narrative: Optional[Dict[str, Any]] = None
    idle_count = 0
    parse_failures = 0
    turns = 0
    degraded_reason: Optional[str] = None

    while turns < turns_limit:
        turns += 1
        try:
            message = chat_with_retry(
                messages, tools=at.TOOL_SCHEMAS, tool_choice="auto",
                temperature=0.0, event_log=log,
            )
        except LLMError as exc:
            log.write("llm_unavailable", {"turn": turns, "error": str(exc)[:300]})
            degraded_reason = DEGRADE_LLM_UNAVAILABLE
            break

        tool_calls = message.get("tool_calls") or []
        content = F.text(message.get("content"))

        if tool_calls:
            idle_count = 0
            messages = messages + [{
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            }]
            for call in tool_calls:
                name = F.text((call.get("function") or {}).get("name"))
                arguments = parse_tool_arguments(call)
                result = at.execute_tool(state, name, arguments, event_log=log)
                messages.append({
                    "role": "tool",
                    "tool_call_id": F.text(call.get("id")) or name,
                    "content": json.dumps(result, ensure_ascii=False),
                })
            # 上下文压缩：实测不压缩时 10 轮涨到 19812 token，模型生成变慢直至超时。
            # 旧的工具结果对后续决策已无用（状态在 SessionState 里），只留摘要。
            messages = _compact_messages(messages, log)
            continue

        if content:
            parsed = extract_json_content(message)
            if isinstance(parsed, dict):
                narrative = parsed
                log.write("final_output", {
                    "turn": turns,
                    "recommendationCount": len(parsed.get("recommendations") or []),
                })
                break
            parse_failures += 1
            log.write("unparseable", {"turn": turns, "content": content[:400]})
            if parse_failures >= 2:
                degraded_reason = DEGRADE_UNPARSEABLE
                break
            messages = messages + [
                {"role": "assistant", "content": content},
                {"role": "user", "content":
                    "上一次输出不是合法 JSON。请只输出 system prompt 第五节要求的 JSON，"
                    "不要代码块、不要解释文字。"},
            ]
            continue

        idle_count += 1
        log.write("model_idle", {"turn": turns, "idleCount": idle_count})
        if idle_count >= idle_limit:
            degraded_reason = DEGRADE_MODEL_IDLE
            break
        messages = messages + [
            {"role": "assistant", "content": ""},
            {"role": "user", "content":
                "请调用工具继续，或输出最终 JSON 结果。"},
        ]

    truncated = narrative is None and degraded_reason is None and turns >= turns_limit
    if truncated:
        log.write("truncated", {"turns": turns})

    # 降级或超轮次：用 deterministic 补齐缺失步骤，保证响应完整
    if degraded_reason or truncated:
        _fill_missing(state, log, use_llm_evidence=use_llm_evidence)
        if narrative is None:
            narrative = _explain_only(state, log)
        return _assemble(state, narrative, MODE_AGENT, {
            "degraded": True,
            "degradedReason": degraded_reason or DEGRADE_TRUNCATED,
            "truncated": truncated,
            "turns": turns,
        }, log)

    # 模型收尾了但可能漏了步骤（例如没评分就输出），同样补齐
    _fill_missing(state, log, use_llm_evidence=use_llm_evidence)
    return _assemble(state, narrative, MODE_AGENT, {
        "degraded": False, "truncated": False, "turns": turns,
    }, log)


def _compact_messages(
    messages: List[Dict[str, Any]], log: Any
) -> List[Dict[str, Any]]:
    """压缩历史工具结果，控制上下文长度。

    保留 system + 首条 user + 最近 KEEP_RECENT_TURNS 轮的完整往返；
    更早的工具结果替换为一行摘要（"已调用 X，返回 N 项"）。

    这么做安全的前提是**状态不在消息历史里**——profile、检索结果、评分结果
    都存在 SessionState，模型不需要从历史里翻。它只需要知道「这一步做过了」。
    """
    if len(messages) <= 2 + KEEP_RECENT_TURNS * 3:
        return messages

    head = messages[:2]
    tail_start = _find_turn_boundary(messages, KEEP_RECENT_TURNS)
    middle, tail = messages[2:tail_start], messages[tail_start:]

    compacted: List[Dict[str, Any]] = []
    saved_chars = 0
    for item in middle:
        if item.get("role") == "tool":
            original = item.get("content") or ""
            if len(original) <= TOOL_RESULT_KEEP:
                compacted.append(item)
                continue
            saved_chars += len(original) - TOOL_RESULT_KEEP
            compacted.append({
                **item,
                "content": _summarize_tool_result(original),
            })
        else:
            compacted.append(item)

    if saved_chars > 0:
        log.write("context_compacted", {
            "savedChars": saved_chars,
            "messageCount": len(messages),
        })
    return head + compacted + tail


def _find_turn_boundary(messages: List[Dict[str, Any]], keep_turns: int) -> int:
    """找到最近 keep_turns 个 assistant 消息的起始下标。"""
    indexes = [
        index for index, item in enumerate(messages)
        if item.get("role") == "assistant"
    ]
    if len(indexes) <= keep_turns:
        return 2
    return max(indexes[-keep_turns], 2)


def _summarize_tool_result(content: str) -> str:
    """把工具结果压成一行摘要。"""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content[:TOOL_RESULT_KEEP] + "…（已省略）"
    if not isinstance(payload, dict):
        return content[:TOOL_RESULT_KEEP] + "…（已省略）"

    if not payload.get("ok"):
        return json.dumps(payload, ensure_ascii=False)[:TOOL_RESULT_KEEP]

    parts: List[str] = ["ok"]
    if "policies" in payload:
        ids = [F.text(p.get("fusionPolicyId")) for p in payload.get("policies") or []]
        parts.append(f"检索到 {len(ids)} 条：{'、'.join(ids[:15])}")
    if "scored" in payload:
        ids = [
            f"{F.text(s.get('fusionPolicyId'))}={s.get('conditionMatchScore')}"
            for s in payload.get("scored") or []
        ]
        parts.append(f"已评分 {len(ids)} 条：{'、'.join(ids[:15])}")
    if "profile" in payload:
        tags = (payload.get("profile") or {}).get("tags") or {}
        parts.append(f"画像已构建，标签 {json.dumps(tags, ensure_ascii=False)}")
    if "ruleResult" in payload:
        signals = (payload.get("ruleResult") or {}).get("aggregatedSignals") or {}
        parts.append(f"规则已执行，聚合信号 {json.dumps(signals, ensure_ascii=False)[:300]}")
    if "policy" in payload:
        policy = payload.get("policy") or {}
        parts.append(f"已读政策 {F.text(policy.get('fusionPolicyId'))} "
                     f"{F.text(policy.get('title'))[:30]} 详情")

    return "（历史结果已压缩）" + "；".join(parts)


def _fill_missing(
    state: at.SessionState, log: Any, use_llm_evidence: bool | None = None
) -> None:
    """补齐模型未完成的步骤，让响应始终完整。

    这是降级路径与「模型提前收尾」共用的兜底。
    """
    if state.profile is None:
        state.profile = build_profile(state.record, today=state.today)
        log.write("fill_profile", {"tags": state.profile["tags"]})

    if state.rule_result is None:
        state.rule_result = evaluate_rules(state.profile, enable_stage2=state.enable_stage2)
        log.write("fill_rules", {"matched": state.rule_result["matchedRuleIds"]})

    if not state.rule_result["gateResult"]["passed"]:
        state.scores.clear()
        return

    # 用与 deterministic 完全相同的多组口径补齐候选池。
    # 两模式看到同一候选集合，是「双模式推荐集合重合度 ≥90%」能达成的前提——
    # 实测单组检索会让 deterministic 漏掉更高分的政策。
    baseline = _search_candidates(state, log)
    known = {item["fusionPolicyId"] for item in state.searched}
    added = [item for item in baseline if item["fusionPolicyId"] not in known]
    if added:
        state.searched.extend(added)
        log.write("fill_search", {
            "modelFound": len(known), "added": len(added),
            "total": len(state.searched),
        })

    # LLM 精评窗口必须从 deterministic 同一基准候选集计算，保证两模式窗口一致。
    if at.is_enabled(use_llm_evidence):
        state.llm_window_ids = at.llm_window_ids(baseline, state.top_n)

    # 给所有检索到但未评分的候选补分（走与 deterministic 相同的资格门禁评分）。
    # 模型常只挑几条评分，漏掉的高分政策会拉低双模式重合度。
    unscored = [
        candidate for candidate in state.searched
        if candidate["fusionPolicyId"] not in state.scores
    ]
    if unscored:
        _score_candidates(state, unscored, use_llm_evidence=use_llm_evidence, event_log=log)
        log.write("fill_scored", {
            "filled": len(unscored), "total": len(state.scores),
        })


def run_match(
    record: Dict[str, Any],
    mode: str = MODE_AGENT,
    keyword: str = "",
    enable_stage2: bool = False,
    top_n: int = 20,
    today: Optional[date] = None,
    event_log: Any = None,
    use_llm_evidence: bool | None = None,
) -> Dict[str, Any]:
    """统一入口。mode 决定走哪条路径。

    use_llm_evidence：None 表示跟随环境变量 PEMATCHING_V2_ELIGIBILITY_LLM_ENABLED
    （默认开启逐条件模型抽取企业事实）；显式传 False 可强制关闭。
    """
    if mode == MODE_DETERMINISTIC:
        return run_deterministic(
            record, keyword=keyword, enable_stage2=enable_stage2, top_n=top_n,
            today=today, event_log=event_log, use_llm_evidence=use_llm_evidence,
        )
    if mode != MODE_AGENT:
        raise ValueError(f"mode 只能是 {MODE_AGENT} 或 {MODE_DETERMINISTIC}，实际 {mode!r}")
    return run_agent(
        record, keyword=keyword, enable_stage2=enable_stage2, top_n=top_n,
        today=today, event_log=event_log, use_llm_evidence=use_llm_evidence,
    )
