# -*- coding: utf-8 -*-
"""Agent 工具注册层：把一期二期的确定性函数包装成 tool schema。

三条设计要点：

1. **结果裁剪**（开发计划 7.1）。回填给模型的是裁剪版，完整结果写进 logs 与
   最终响应的 data。审计信息不丢，上下文也不炸。

2. **状态不经模型手**（三期 3.3）。profile 存在 SessionState 里，工具 schema 不
   接受 profile 参数。这既省上下文，也堵住「模型伪造 profile 参数操纵分数」。

3. **重复调用检测**。同工具同参数连续调用多次时返回提示而非重复执行，
   防模型刷轮次。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Set

from service import fields as F
from service import policy_index as pi
from service.eligibility.constants import RESULT_SCHEMA
from service.eligibility.gate_service import evaluate_policies, evaluate_policy
from service.eligibility.model_evidence import is_enabled
from service.eligibility.profile_loader import load_snapshot
from service.paths import env_int
from service.profile import build_profile
from service.raw_enterprise_mapping import select_model_facts
from service.rules import collect_unmatched_signals, evaluate_rules

# 同一工具同一参数连续调用超过这个次数就返回提示，不再重复执行
REPEAT_LIMIT = 3
# 单次 search_policies 回填给模型的最大条数
SEARCH_LIMIT_CAP = 30
# get_policy_detail 调用次数上限。实测模型倾向逐条读全文写理由，
# 一家企业调 6 次就耗尽 12 轮预算导致强制收尾（降级率 16.7%）。
DETAIL_LIMIT = 2

# LLM 抽取成本高，只对检索相关性最高的前 N 条候选做 LLM 精评；其余候选不进入精评窗口。
# 窗口大小 = top_n + 余量，下限 LLM_WINDOW_FLOOR，上限为候选总数。
LLM_WINDOW_SLACK = 10
LLM_WINDOW_FLOOR = 24


class SessionState:
    """单次请求的会话状态。模型不能直接读写，只能通过工具间接影响。"""

    def __init__(
        self,
        record: Dict[str, Any],
        keyword: str = "",
        enable_stage2: bool = False,
        today: Optional[date] = None,
        top_n: int = 20,
    ) -> None:
        self.record = record
        self.keyword = F.text(keyword)
        self.enable_stage2 = bool(enable_stage2)
        self.today = today or date.today()
        self.top_n = top_n

        self.profile: Optional[Dict[str, Any]] = None
        self.rule_result: Optional[Dict[str, Any]] = None
        # {fusionPolicyId: 完整资格评估结果}，最终响应从这里取分数，不信模型转述
        self.scores: Dict[str, Dict[str, Any]] = {}
        self.searched: List[Dict[str, Any]] = []
        self.call_counts: Dict[str, int] = {}
        self.tool_calls: List[Dict[str, Any]] = []
        # 资格评估相关元信息与是否启用 llm 逐条件抽取证据
        self.eligibility_meta: Dict[str, Any] = {}
        self.use_llm_evidence: bool | None = None
        # LLM 精评窗口：LLM 开启时只评估这些候选，窗口外静默丢弃
        self.llm_window_ids: Optional[Set[str]] = None

    def signature(self, name: str, arguments: Dict[str, Any]) -> str:
        return name + "|" + json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True)

    def note_call(self, name: str, arguments: Dict[str, Any]) -> int:
        key = self.signature(name, arguments)
        self.call_counts[key] = self.call_counts.get(key, 0) + 1
        return self.call_counts[key]


# --------------------------------------------------------------------------
# 工具 schema（给模型看的部分）
# --------------------------------------------------------------------------

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "build_enterprise_profile",
            "description": (
                "构建企业工商画像，产出 B 发展阶段 / C 行业领域（含强弱子类）/ 属地标签、"
                "规模与知识产权指标、资质状态、每个字段的可用性，以及严格映射成中文键名的完整企业原始数据。"
                "企业数据已在会话中，无需传参。必须先调用本工具才能执行后续步骤。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_channel_rules",
            "description": (
                "执行 docx 定义的 34 条匹配规则（G0 前置拦截 / G2 第一轮直推 / "
                "G3 链路推荐 / G4 第二轮精推），返回每条规则的命中、未命中、跳过三态，"
                "以及聚合后的标签信号供检索使用。需先调用 build_enterprise_profile。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_policies",
            "description": (
                "按标签检索候选政策，返回摘要列表（不含全文）。"
                "可反复调用并更换标签组合——首轮结果不理想时换组合再试是本 Agent 的主要价值。"
                "不可申报政策（专家征集、报告报送等）已自动排除。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cTags": {
                        "type": "array", "items": {"type": "string"},
                        "description": "C 行业领域大类，如 [\"C1\",\"C5\"]。政策为 C9 通用时对任意查询都命中",
                    },
                    "cSubTags": {
                        "type": "array", "items": {"type": "string"},
                        "description": "C 子类，仅 C1.1 新型显示 / C1.2 集成电路 / C1.3 软件开发 / C10.1 人工智能",
                    },
                    "aTags": {
                        "type": "array", "items": {"type": "string"},
                        "description": "A 支持方式 A1-A8，用于按企业需求方向筛选",
                    },
                    "bTags": {
                        "type": "array", "items": {"type": "string"},
                        "description": "B 发展阶段 B1 初创 / B2 成长 / B3 成熟",
                    },
                    "dLevels": {
                        "type": "array", "items": {"type": "string"},
                        "description": "D 申报门槛 D1 低 / D2 中 / D3 高",
                    },
                    "regionTags": {
                        "type": "array", "items": {"type": "string"},
                        "description": "属地，如 [\"思明区\",\"火炬高新区\"]。区级政策只推给本区企业",
                    },
                    "keyword": {
                        "type": "string",
                        "description": "查询关键词，只影响排序不做过滤",
                    },
                    "limit": {
                        "type": "integer",
                        "description": f"返回条数，默认 20，上限 {SEARCH_LIMIT_CAP}",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "score_policy",
            "description": (
                "对指定政策按申报要求逐条件评估，产出 0-100 申报要求符合度。"
                "先判硬性条件，硬性不通过直接不推荐（不再判软性）；硬性通过后硬性与软性条件一起算分。"
                "分数由确定性函数算出，你不得自行计算或修改。"
                "只能对 search_policies 返回过的政策评分。需先调用 build_enterprise_profile。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fusionPolicyIds": {
                        "type": "array", "items": {"type": "string"},
                        "description": "政策ID列表，可一次传多个批量评估",
                    },
                },
                "required": ["fusionPolicyIds"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_enterprise_raw_facts",
            "description": (
                "按中文字段名读取企业原始数据事实。先读取政策申报条件，再传入条件实际涉及的中文字段名，"
                "例如 [\"营业收入\", \"员工总人数\", \"高新技术企业\"]。"
                "返回值严格来自企业原始 JSON 的字段映射；未返回即表示该字段没有可用事实，不能自行推测。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fieldNames": {
                        "type": "array", "items": {"type": "string"},
                        "description": "需要读取的中文企业字段名，最多 30 个",
                    },
                },
                "required": ["fieldNames"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_policy_detail",
            "description": (
                "读取单条政策的申报条件、支持方式、申报材料全文。"
                f"**最多调用 {DETAIL_LIMIT} 次**——每次占一轮往返，调多了会耗尽轮次预算。"
                "score_policy 返回的 items[].basis 已足够写推荐理由，"
                "只在确实需要核对具体申报门槛时才调本工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fusionPolicyId": {"type": "string", "description": "政策ID"},
                },
                "required": ["fusionPolicyId"],
            },
        },
    },
]

TOOL_NAMES = [schema["function"]["name"] for schema in TOOL_SCHEMAS]


# --------------------------------------------------------------------------
# 结果裁剪：回填给模型的精简版
# --------------------------------------------------------------------------


def _trim_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """画像裁剪。fieldAvailability 只回 unknown 的字段名——
    模型需要知道哪些信息缺失（以免推测填补），但不需要全量键值对。"""
    metrics = profile.get("metrics") or {}
    return {
        "enterpriseName": profile.get("enterpriseName"),
        "tags": profile.get("tags"),
        "tagBasis": profile.get("tagBasis"),
        "metrics": {
            key: metrics.get(key)
            for key in ("companyAgeYears", "employeeCount", "revenueWan", "ipTotal",
                        "ipSource", "rdExpenseWan", "metricsYear")
        },
        "qualifications": {
            key: value for key, value in (profile.get("qualifications") or {}).items()
            if value is not None
        },
        "credit": profile.get("credit"),
        "dataCompleteness": profile.get("dataCompleteness"),
        "unknownFields": profile.get("unknownFields"),
        "availableRawFields": (profile.get("_rawFactIndex") or {}).get("availableFieldNames") or [],
        "originalEnterpriseData": (profile.get("_rawFactIndex") or {}).get("mappedRecord") or {},
    }


def _trim_rules(rule_result: Dict[str, Any]) -> Dict[str, Any]:
    """规则裁剪。命中的回 ID+signals，跳过的回 ID+原因，未命中的只回 ID 列表。"""
    channels: Dict[str, Any] = {}
    for name, data in (rule_result.get("channels") or {}).items():
        if not data.get("executed"):
            channels[name] = {"executed": False, "reason": data.get("reason")}
            continue
        channels[name] = {
            "executed": True,
            "matched": [
                {
                    "ruleId": item["ruleId"], "name": item["name"],
                    "priority": item["priority"],
                    "policyPackDesc": item.get("policyPackDesc"),
                }
                for item in data.get("matched") or []
            ],
            "unmatchedIds": [item["ruleId"] for item in data.get("unmatched") or []],
            "skipped": [
                {"ruleId": item["ruleId"], "skipReason": item["skipReason"]}
                for item in data.get("skipped") or []
            ],
        }
    gate = rule_result.get("gateResult") or {}
    return {
        "gateResult": {
            "passed": gate.get("passed"),
            "creditChecked": gate.get("creditChecked"),
            "reason": gate.get("reason"),
            "blockedBy": gate.get("blockedBy"),
        },
        "channels": channels,
        "aggregatedSignals": rule_result.get("aggregatedSignals"),
        "chainStages": rule_result.get("chainStages"),
    }


def _trim_search(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "fusionPolicyId": item["fusionPolicyId"],
            "title": item["title"],
            "summary": item.get("summary"),
            "tags": {
                "A": (item["tags"].get("A") or {}).get("primary"),
                "C": (item["tags"].get("C") or {}).get("values"),
                "cSub": (item["tags"].get("C") or {}).get("subValues"),
                "B": (item["tags"].get("B") or {}).get("values"),
                "D": (item["tags"].get("D") or {}).get("value"),
            },
            "applicationWindow": {
                "startDate": (item.get("applicationWindow") or {}).get("startDate"),
                "endDate": (item.get("applicationWindow") or {}).get("endDate"),
                "isRolling": (item.get("applicationWindow") or {}).get("isRolling"),
            },
            "matchedDimensions": item.get("matchedDimensions"),
        }
        for item in items
    ]


def _trim_score(policy_id: str, title: str, eligibility: Dict[str, Any]) -> Dict[str, Any]:
    """资格评估裁剪。符合度、档位、原因、条件明细回填给模型。"""
    return {
        "fusionPolicyId": policy_id,
        "title": title,
        "conditionMatchScore": eligibility.get("conditionMatchScore"),
        "gateStatus": eligibility.get("gateStatus"),
        "directPushAllowed": eligibility.get("directPushAllowed"),
        "gateReasons": eligibility.get("gateReasons") or [],
        "scoredConditionCount": eligibility.get("scoredConditionCount"),
        "pendingConditionCount": eligibility.get("pendingConditionCount"),
        "conditionDetails": [
            {
                "itemName": d.get("itemName"),
                "evalStatus": d.get("evalStatus"),
                "score": d.get("score"),
                "message": d.get("message"),
            }
            for d in (eligibility.get("conditionDetails") or [])[:10]
        ],
    }


def _trim_detail(detail: Dict[str, Any]) -> Dict[str, Any]:
    """政策详情裁剪。去掉 relatedPolicies 与 documents（三期 3.2）。"""
    return {
        "fusionPolicyId": detail.get("fusionPolicyId"),
        "title": detail.get("title"),
        "summary": detail.get("summary"),
        "industryField": detail.get("industryField"),
        "applicableScopePath": detail.get("applicableScopePath"),
        "issuingDepartment": detail.get("issuingDepartment"),
        "supportMethods": [
            {
                "supportType": F.text(method.get("supportType"))[:200],
                "amount": F.text(method.get("amount"))[:100],
                "maxAmount": F.text(method.get("maxAmount"))[:100],
                "contentDesc": F.text(method.get("contentDesc"))[:400],
            }
            for method in (detail.get("supportMethods") or [])[:6]
            if isinstance(method, dict)
        ],
        "structuredConditions": [
            {
                "applicableTarget": F.text(condition.get("applicableTarget"))[:300],
                "applicationRequirement": F.text(condition.get("applicationRequirement"))[:1200],
            }
            for condition in (detail.get("structuredConditions") or [])[:8]
            if isinstance(condition, dict)
        ],
        "materials": [
            F.text(material.get("materials"))[:300]
            for material in (detail.get("materials") or [])[:6]
            if isinstance(material, dict)
        ],
        "tags": detail.get("tags"),
        "applicationWindow": detail.get("applicationWindow"),
    }


# --------------------------------------------------------------------------
# handlers
# --------------------------------------------------------------------------


def _handle_build_profile(state: SessionState, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if state.profile is None:
        state.profile = build_profile(state.record, today=state.today)
    return {"ok": True, "profile": _trim_profile(state.profile)}


def _handle_run_rules(state: SessionState, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if state.profile is None:
        return {
            "ok": False,
            "error": "尚未构建企业画像，请先调用 build_enterprise_profile",
        }
    if state.rule_result is None:
        state.rule_result = evaluate_rules(state.profile, enable_stage2=state.enable_stage2)
    return {"ok": True, "ruleResult": _trim_rules(state.rule_result)}


def _handle_search(state: SessionState, arguments: Dict[str, Any]) -> Dict[str, Any]:
    def as_list(key: str) -> Optional[List[str]]:
        value = arguments.get(key)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return None
        cleaned = [F.text(v) for v in value if F.text(v)]
        return cleaned or None

    limit = arguments.get("limit")
    limit = min(int(limit), SEARCH_LIMIT_CAP) if isinstance(limit, (int, float)) else 20

    items = pi.search_policies(
        a_tags=as_list("aTags"),
        c_tags=as_list("cTags"),
        c_sub_tags=as_list("cSubTags"),
        b_tags=as_list("bTags"),
        d_levels=as_list("dLevels"),
        region_tags=as_list("regionTags"),
        keyword=F.text(arguments.get("keyword")) or state.keyword,
        limit=limit,
    )

    known = {item["fusionPolicyId"] for item in state.searched}
    for item in items:
        if item["fusionPolicyId"] not in known:
            state.searched.append(item)

    return {"ok": True, "count": len(items), "policies": _trim_search(items)}


def _handle_score(state: SessionState, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if state.profile is None:
        return {
            "ok": False,
            "error": "尚未构建企业画像，请先调用 build_enterprise_profile",
        }

    ids = arguments.get("fusionPolicyIds")
    if isinstance(ids, str):
        ids = [ids]
    if not isinstance(ids, list) or not ids:
        return {"ok": False, "error": "fusionPolicyIds 必须是非空数组"}

    searched_ids = {item["fusionPolicyId"] for item in state.searched}
    tags_index = pi.load_policy_tags()
    llm_on = is_enabled(state.use_llm_evidence)

    valid: List[str] = []
    rejected: List[Dict[str, str]] = []
    for raw_id in ids[:40]:
        policy_id = F.text(raw_id)
        if not policy_id:
            continue
        # 只允许对检索返回过的政策评估——防模型凭空造政策 ID
        if searched_ids and policy_id not in searched_ids:
            rejected.append({
                "fusionPolicyId": policy_id,
                "reason": "该政策未出现在 search_policies 结果中，不可评估",
            })
            continue
        if tags_index.get(policy_id) is None:
            rejected.append({"fusionPolicyId": policy_id, "reason": "政策不存在或未打标"})
            continue
        # LLM 精评窗口：窗口外政策不做模型抽取，避免成本失控
        if llm_on and state.llm_window_ids is not None and policy_id not in state.llm_window_ids:
            rejected.append({
                "fusionPolicyId": policy_id,
                "reason": "该政策不在 LLM 精评窗口内，不可评估",
            })
            continue
        if policy_id not in state.scores:
            valid.append(policy_id)

    if valid:
        eligibility_result = evaluate_policy_ids(
            state.profile, valid, use_llm_evidence=state.use_llm_evidence,
        )
        evals = {
            F.text(item.get("fusionPolicyId")): item
            for item in (eligibility_result.get("eligibilityEvaluations") or [])
            if isinstance(item, dict)
        }
        for policy_id in valid:
            evaluation = evals.get(policy_id)
            if evaluation is None:
                continue
            summary = next(
                (item for item in state.searched if item["fusionPolicyId"] == policy_id), {}
            )
            state.scores[policy_id] = {
                "fusionPolicyId": policy_id,
                "title": summary.get("title") or (tags_index.get(policy_id) or {}).get("title"),
                "policy_tags": tags_index.get(policy_id) or {},
                "eligibility": evaluation,
            }
        state.eligibility_meta = {
            "eligibilitySchemaVersion": eligibility_result.get("eligibilitySchemaVersion"),
            "ruleManifestHash": eligibility_result.get("ruleManifestHash"),
        }

    scored = [
        _trim_score(policy_id, state.scores[policy_id]["title"], state.scores[policy_id]["eligibility"])
        for policy_id in ids[:40]
        if policy_id in state.scores
    ]
    scored.sort(key=lambda item: -(item.get("conditionMatchScore") or 0.0))
    payload: Dict[str, Any] = {"ok": True, "scored": scored}
    if rejected:
        payload["rejected"] = rejected
    return payload


def _handle_raw_facts(state: SessionState, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if state.profile is None:
        return {
            "ok": False,
            "error": "尚未构建企业画像，请先调用 build_enterprise_profile",
        }
    names = arguments.get("fieldNames")
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list) or not names:
        return {"ok": False, "error": "fieldNames 必须是非空中文字段名数组"}
    cleaned = [F.text(name) for name in names if F.text(name)][:30]
    if not cleaned:
        return {"ok": False, "error": "fieldNames 不能全部为空"}
    facts = select_model_facts(state.profile.get("_rawFactIndex") or {}, cleaned)
    return {
        "ok": True,
        "requestedFieldNames": cleaned,
        "facts": facts,
        "missingFieldNames": sorted(set(cleaned) - {fact["fieldName"] for fact in facts}),
    }


def _handle_detail(state: SessionState, arguments: Dict[str, Any]) -> Dict[str, Any]:
    policy_id = F.text(arguments.get("fusionPolicyId"))
    if not policy_id:
        return {"ok": False, "error": "fusionPolicyId 不能为空"}

    # 硬限次数：光靠 prompt 约束不住，实测模型会逐条读全文耗尽轮次预算
    detail_calls = sum(1 for call in state.tool_calls if call["name"] == "get_policy_detail")
    if detail_calls >= DETAIL_LIMIT:
        return {
            "ok": False,
            "error": f"get_policy_detail 已达 {DETAIL_LIMIT} 次上限。"
                     "score_policy 返回的 items[].basis 已足够写推荐理由，请直接输出最终 JSON。",
        }
    try:
        detail = pi.get_policy_detail(policy_id)
    except pi.PolicyIndexError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "policy": _trim_detail(detail)}


HANDLERS: Dict[str, Callable[[SessionState, Dict[str, Any]], Dict[str, Any]]] = {
    "build_enterprise_profile": _handle_build_profile,
    "run_channel_rules": _handle_run_rules,
    "search_policies": _handle_search,
    "score_policy": _handle_score,
    "get_enterprise_raw_facts": _handle_raw_facts,
    "get_policy_detail": _handle_detail,
}


def execute_tool(
    state: SessionState,
    name: str,
    arguments: Optional[Dict[str, Any]],
    event_log: Any = None,
) -> Dict[str, Any]:
    """执行工具。参数解析失败、工具不存在、重复调用都返回结构化错误而非抛异常，
    让模型能自我纠正。"""
    import time

    if name not in HANDLERS:
        return {"ok": False, "error": f"未知工具 {name}，可用工具：{'、'.join(TOOL_NAMES)}"}
    if arguments is None:
        return {"ok": False, "error": "工具参数不是合法 JSON，请重新输出"}

    repeat = state.note_call(name, arguments)
    if repeat > REPEAT_LIMIT:
        return {
            "ok": False,
            "error": f"{name} 已用相同参数调用 {repeat - 1} 次，结果同上。"
                     "请改变参数或进入下一步。",
        }

    started = time.time()
    try:
        result = HANDLERS[name](state, arguments)
    except Exception as exc:  # 工具异常不该中断整个 Agent
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    elapsed_ms = int((time.time() - started) * 1000)
    state.tool_calls.append({
        "name": name, "arguments": arguments,
        "ok": bool(result.get("ok")), "elapsedMs": elapsed_ms,
    })
    if event_log is not None:
        event_log.write("tool_call", {
            "name": name, "arguments": arguments,
            "ok": bool(result.get("ok")), "elapsedMs": elapsed_ms,
            "error": result.get("error"),
            "resultKeys": sorted(result.keys()),
        })
    return result


def collect_unmatched_policy_signals(state: SessionState) -> List[Dict[str, Any]]:
    """命中规则但库内无对应政策的清单，作为补政策库的输入。"""
    if state.rule_result is None:
        return []
    signals = collect_unmatched_signals(state.rule_result)
    scored_titles = " ".join(
        F.text(item.get("title")) for item in state.scores.values()
    )
    unmatched: List[Dict[str, Any]] = []
    for signal in signals:
        keywords = signal.get("keywords") or []
        if keywords and any(F.text(k) and F.text(k) in scored_titles for k in keywords):
            continue
        unmatched.append({
            "ruleId": signal["ruleId"],
            "priority": signal["priority"],
            "policyPackDesc": signal["policyPackDesc"],
            "note": "库内暂无对应政策",
        })
    return unmatched


# --------------------------------------------------------------------------
# LLM 精评窗口与并发评估
# --------------------------------------------------------------------------


def llm_window_ids(candidates: List[Dict[str, Any]], top_n: int) -> Set[str]:
    """LLM 开启时，只对检索相关性（matchScore）最高的前 N 条候选做精评。

    窗口大小 = top_n + 余量，下限 LLM_WINDOW_FLOOR，上限为候选总数。
    窗口外的候选不进入精评、不进推荐（静默丢弃）。
    """
    if not candidates:
        return set()
    size = min(len(candidates), max(top_n + LLM_WINDOW_SLACK, LLM_WINDOW_FLOOR))
    ranked = sorted(candidates, key=lambda item: -(float(item.get("matchScore") or 0.0)))
    return {F.text(item["fusionPolicyId"]) for item in ranked[:size]}


def evaluate_policy_ids(
    profile: Dict[str, Any],
    policy_ids: List[str],
    use_llm_evidence: bool | None = None,
) -> Dict[str, Any]:
    """对一组政策做资格评估。

    LLM 开启：并发逐政策评估（每政策一次模型事实抽取），降低串行耗时。
    静态（LLM 关）：一次性串行评估全部，几乎零成本。
    """
    if not policy_ids:
        return {
            "eligibilityEvaluations": [],
            "eligibilitySchemaVersion": None,
            "ruleManifestHash": "",
        }
    if not is_enabled(use_llm_evidence):
        return evaluate_policies(
            profile, policy_ids,
            include_condition_details=True,
            use_llm_evidence=False,
        )

    workers = max(1, env_int("V2_ELIGIBILITY_LLM_WORKERS", 6))
    evaluations: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                evaluate_policy, profile, policy_id,
                include_condition_details=True, use_llm_evidence=True,
            ): policy_id
            for policy_id in policy_ids
        }
        for future in as_completed(futures):
            policy_id = futures[future]
            try:
                evaluations.append(future.result())
            except Exception as exc:  # 单条失败不中断其余政策
                evaluations.append({
                    "fusionPolicyId": policy_id,
                    "gateStatus": "not_evaluable",
                    "directPushAllowed": False,
                    "conditionMatchScore": None,
                    "gateReasons": ["POLICY_EVALUATION_FAILED", f"{type(exc).__name__}: {exc}"],
                })
    manifest, _ = load_snapshot()
    return {
        "eligibilityEvaluations": evaluations,
        "eligibilitySchemaVersion": RESULT_SCHEMA,
        "ruleManifestHash": manifest.get("contentSha256", ""),
    }
