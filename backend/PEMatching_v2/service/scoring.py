# -*- coding: utf-8 -*-
"""评分器（确定性）。总分 = 基础分（≤100） + 附加分（≤50），满分 150。

基础分五项：领域匹配度 30 / 发展阶段 20 / 创新活跃度 25 / 规模适配 15 / 信用 10。
关键机制是**分母剔除后归一化**：企业侧字段为 unknown 的计分项从分母剔除，
`基础分 = Σ实际得分 / Σ有效项权重 × 100`。这样字段缺失多的企业不被系统性
压低，与阈值口径保持可比——不是给缺失项记 0 分。

权重、分档、阈值全部取自 config/tag_taxonomy.json，调参不改代码。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from service import fields as F
from service import taxonomy as tx

BAND_DIRECT = "direct_push"
BAND_SUPPLEMENT = "needs_supplement"
BAND_NOT_RECOMMENDED = "not_recommended"
BAND_NOT_EVALUABLE = "not_evaluable"

TIMELINESS_NOT_STARTED = "not_started"
TIMELINESS_IN_PROGRESS = "in_progress"
TIMELINESS_PAST_YEAR = "past_year"
TIMELINESS_UNKNOWN = "unknown"

# 资质 → 政策标题关键词。企业已具备某资质时，标题命中对应关键词的政策直接排除
# （避免"已是科小还推科小、已是高企还推高企"）。
# 注意：key 是 qualifications 里的字段名，keywords 命中任一即排除该政策。
QUALIFICATION_POLICY_KEYWORDS = {
    "isTechSme": ["科技型中小企业", "科技中小企业"],
    "isHighTechEnterprise": ["高新技术企业", "高企"],
    "isSpecializedSme": ["专精特新中小企业", "专精特新"],
    "isSpecializedLittleGiant": ["专精特新小巨人", "小巨人"],
}

# 资质升级链（规则3）。企业当前具备的资质决定可推的政策层级：
# 已具备某级资质 → 不再推"该级及以下"的认定政策，只推更高一级。
# 顺序从低到高：科小/创新型 → 高企 → 专精特新 → 小巨人 → 上市后备 → 上市。
# 每项：qualifications 字段名 + 对应政策的标题关键词。
QUALIFICATION_CHAIN = [
    {"field": "isTechSme", "label": "科技型/创新型中小企业", "keywords": ["科技型中小企业", "科技中小企业", "创新型中小企业"]},
    {"field": "isHighTechEnterprise", "label": "国家高新技术企业", "keywords": ["高新技术企业", "高企"]},
    {"field": "isSpecializedSme", "label": "专精特新中小企业", "keywords": ["专精特新中小企业", "专精特新"]},
    {"field": "isSpecializedLittleGiant", "label": "专精特新小巨人", "keywords": ["专精特新小巨人", "小巨人"]},
]

# 研发补助类政策标题关键词：无专利（ipTotal=0 或缺失）时不推。
RD_SUBSIDY_KEYWORDS = ["研发补助", "研发费用补助", "研发投入补助", "研发专项补助"]


def filter_policy_by_qualification(
    title: str,
    qualifications: Dict[str, Any],
    metrics: Optional[Dict[str, Any]] = None,
    audience: Optional[str] = None,
) -> Optional[str]:
    """按企业已具备资质过滤对应认定政策（规则2/3）。

    规则2：已具备某资质 → 排除标题含对应关键词的认定政策。
    规则3：资质升级链硬性排除——已具备某级，则不推该级及以下（含同级认定）。
    研发补助类：企业无专利（ipTotal 为 0 或缺失）时不推。
    受众过滤：面向个人的政策（人才认定/专家库/职称）不推给企业。

    返回排除原因，否则 None。
    """
    title_lower = F.text(title).lower()

    # 受众为个人的政策（人才认定/专家库/职称/选拔）不推给企业
    if F.text(audience) == "个人":
        return "政策面向个人申报（人才/专家/职称类），不推给企业"

    # 规则3：升级链——企业当前处于第 N 级资质时，不推"第 N 级及以下"的认定政策，
    # 保留第 N+1 级及以上的政策（那些是企业升级的目标，应当推）。
    # 例：已科小 → 不推科小认定，但推高企认定（升级目标）。
    for index, stage in enumerate(QUALIFICATION_CHAIN):
        if qualifications.get(stage["field"]) is not True:
            continue
        for lower_stage in QUALIFICATION_CHAIN[: index + 1]:
            for kw in lower_stage["keywords"]:
                if kw.lower() in title_lower:
                    return f"企业已具备{stage['label']}，不推{lower_stage['label']}认定政策"

    # 规则2：单资质关键词排除（兜底，覆盖升级链未列出的）
    for qual_key, keywords in QUALIFICATION_POLICY_KEYWORDS.items():
        if qualifications.get(qual_key) is not True:
            continue
        for kw in keywords:
            if kw.lower() in title_lower:
                return f"企业已具备{kw}资质，不再推送对应认定政策"

    # 研发补助类：无专利不推
    metrics = metrics or {}
    ip_total = metrics.get("ipTotal")
    if ip_total in (None, 0):
        for kw in RD_SUBSIDY_KEYWORDS:
            if kw.lower() in title_lower:
                return f"研发补助类政策需企业有专利（当前无专利记录）"

    return None


def is_ancient_policy(window: Optional[Dict[str, Any]], today: date) -> bool:
    """政策申报期是否已在两年前（前年以前），是则建议过滤不推。

    规则：今年正在申报 > 当年未启动 > 往年政策（去年出现）> 前年以前不推。
    """
    if not isinstance(window, dict) or window.get("isRolling"):
        return False
    end = _parse_iso_date(window.get("endDate"))
    if end is None:
        return False
    return end.year < today.year - 1

_CONFIDENCE_SCORES = {"high": 3, "medium": 2, "low": 1}


def _parse_iso_date(value: Any) -> Optional[date]:
    raw = F.text(value)
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _policy_tag_values(policy_tags: Dict[str, Any], dimension: str) -> List[str]:
    entry = ((policy_tags.get("tags") or {}).get(dimension)) or {}
    if dimension == "A":
        values = [entry.get("primary")] + list(entry.get("secondary") or [])
    elif dimension in ("B", "C"):
        values = list(entry.get("values") or [])
    else:
        values = [entry.get("value")]
    return [str(v) for v in values if v]


def _policy_sub_tags(policy_tags: Dict[str, Any]) -> List[str]:
    entry = ((policy_tags.get("tags") or {}).get("C")) or {}
    return [str(v) for v in (entry.get("subValues") or []) if v]


# --------------------------------------------------------------------------
# 基础分五项
# --------------------------------------------------------------------------


def _score_industry(profile: Dict[str, Any], policy_tags: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """领域匹配度。返回 None 表示剔除分母。

    五档（docx 原为三档，两处调整见 config/tag_taxonomy.json 的 note）：
      强子类交集 30  — 企业业务描述文本命中子类，证据强
      弱子类交集 25  — 企业仅行业名命中子类，证据弱
      大类精确交集 25 — 不依赖子类的区分档
       政策仅 C9     0（保留召回，不把“全行业通用”当作领域匹配证据）
      无交集        0
    """
    scores = tx.scoring_config()["industryMatchScores"]
    availability = profile.get("fieldAvailability") or {}
    if availability.get("cTags") != F.AVAILABLE:
        return None

    tags = profile.get("tags") or {}
    enterprise_major = set(tags.get("C") or [])
    strong_sub = set(tags.get("cSubStrong") or [])
    weak_sub = set(tags.get("cSubWeak") or [])

    policy_major = set(_policy_tag_values(policy_tags, "C"))
    policy_sub = set(_policy_sub_tags(policy_tags))
    generic = tx.load_taxonomy()["dimensions"]["C"]["genericTag"]

    strong_hit = sorted(strong_sub & policy_sub)
    if strong_hit:
        return {
            "score": scores["strongSubTagIntersect"],
            "basis": f"业务描述命中政策子类 {'、'.join(strong_hit)}",
        }

    weak_hit = sorted(weak_sub & policy_sub)
    if weak_hit:
        return {
            "score": scores["weakSubTagIntersect"],
            "basis": f"行业名命中政策子类 {'、'.join(weak_hit)}（无业务描述佐证）",
        }

    major_hit = sorted((enterprise_major & policy_major) - {generic})
    if major_hit:
        return {
            "score": scores["majorTagExactIntersect"],
            "basis": f"领域大类精确匹配 {'、'.join(major_hit)}",
        }

    if generic in policy_major:
        return {
            "score": scores["noIntersect"],
            "basis": "政策为全行业通用（C9），仅作为召回兜底，不计领域匹配分",
        }

    return {
        "score": scores["noIntersect"],
        "basis": f"企业领域 {sorted(enterprise_major) or '无'} 与政策领域 {sorted(policy_major) or '无'} 无交集",
    }


def _score_stage(profile: Dict[str, Any], policy_tags: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """发展阶段匹配。返回 None 表示剔除分母。

    docx 原为「有交集 20 / 相邻 10」。实测 B2 命中率 100%（样本来自科技型
    中小企业名单），仅靠是否命中无区分度，故改为按企业在该阶段命中的信号数
    计分：3 个信号 20 分、2 个 15 分、1 个 10 分。标签归属仍是 docx 的
    多归属「或」逻辑，只改计分粒度。
    """
    config = tx.scoring_config()["stageMatchScores"]
    availability = profile.get("fieldAvailability") or {}
    if availability.get("bTags") != F.AVAILABLE:
        return None

    enterprise_stages = set((profile.get("tags") or {}).get("B") or [])
    policy_stages = set(_policy_tag_values(policy_tags, "B"))
    if not policy_stages:
        # 政策未限定阶段，视为适用所有阶段
        policy_stages = enterprise_stages

    signal_counts = {
        item["stage"]: item.get("signalCount", 1)
        for item in ((profile.get("tagBasis") or {}).get("B") or [])
    }

    overlap = sorted(enterprise_stages & policy_stages)
    if overlap:
        best_stage = max(overlap, key=lambda s: signal_counts.get(s, 1))
        count = signal_counts.get(best_stage, 1)
        by_count = config.get("bySignalCount") or {}
        score = by_count.get(str(min(count, 3)), config["intersect"])
        return {
            "score": score,
            "basis": f"阶段 {best_stage} 匹配，企业在该阶段命中 {count} 个信号",
        }

    adjacency = tx.load_taxonomy()["dimensions"]["B"]["adjacency"]
    for stage in enterprise_stages:
        if set(adjacency.get(stage) or []) & policy_stages:
            return {
                "score": config["adjacent"],
                "basis": f"企业阶段 {stage} 与政策阶段 {sorted(policy_stages)} 相邻",
            }

    return {
        "score": config["noIntersect"],
        "basis": f"企业阶段 {sorted(enterprise_stages)} 与政策阶段 {sorted(policy_stages)} 无交集且不相邻",
    }


def _score_innovation(profile: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """创新活跃度。返回 None 表示剔除分母。

    注意 0 件与缺失的区别：ipTotal=0 是已核验的真实 0，记 0 分参与计分；
    ipTotal=None 是不知道，剔除分母。
    """
    availability = profile.get("fieldAvailability") or {}
    if availability.get("ipTotal") != F.AVAILABLE:
        return None

    total = (profile.get("metrics") or {}).get("ipTotal")
    if total is None:
        return None

    source = (profile.get("metrics") or {}).get("ipSource") or ""
    for tier in tx.scoring_config()["innovationTiers"]:
        if total >= tier["minCount"]:
            return {
                "score": tier["score"],
                "basis": f"知识产权 {total} 件（来源 {source}），落 ≥{tier['minCount']} 件档",
            }
    return {"score": 0, "basis": f"知识产权 {total} 件"}


def _score_scale(profile: Dict[str, Any], policy_tags: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """规模适配。返回 None 表示剔除分母。

    只依赖 employeeCount 与 revenueWan——registerCapital 实测 0.03% 可用、
    enterpriseScale 实测 0%，均不作依据。

    政策未给规模门槛时按「无限定即适配」记满分，并在 basis 里说明，
    避免把「政策没限制」误判成「企业不达标」。
    """
    config = tx.scoring_config()["scaleMatchScores"]
    availability = profile.get("fieldAvailability") or {}
    if not any(availability.get(f) == F.AVAILABLE for f in ("employeeCount", "revenueWan")):
        return None

    metrics = profile.get("metrics") or {}
    employees = metrics.get("employeeCount")
    revenue = metrics.get("revenueWan")

    thresholds = policy_tags.get("scaleThresholds") or {}
    confidence = F.text(thresholds.get("confidence"))
    if confidence and confidence != "high":
        return {
            "score": config["inRange"],
            "basis": "政策规模门槛置信度不足，未作为确定性评分依据",
        }
    emp_max = F.to_float(thresholds.get("employeeMax"))
    emp_min = F.to_float(thresholds.get("employeeMin"))
    rev_max = F.to_float(thresholds.get("revenueMaxWan"))
    rev_min = F.to_float(thresholds.get("revenueMinWan"))

    if not any(v is not None for v in (emp_max, emp_min, rev_max, rev_min)):
        return {"score": config["inRange"], "basis": "政策未限定规模门槛，视为适配"}

    boundary_ratio = 0.1
    in_range = True
    at_boundary = False
    reasons: List[str] = []

    for value, low, high, label in (
        (employees, emp_min, emp_max, "人数"),
        (revenue, rev_min, rev_max, "营收"),
    ):
        if value is None:
            continue
        if low is not None:
            if value < low * (1 - boundary_ratio):
                in_range = False
                reasons.append(f"{label} {value} 低于门槛 {low}")
            elif value < low:
                at_boundary = True
                reasons.append(f"{label} {value} 处于门槛 {low} 边界")
        if high is not None:
            if value > high * (1 + boundary_ratio):
                in_range = False
                reasons.append(f"{label} {value} 超出上限 {high}")
            elif value > high:
                at_boundary = True
                reasons.append(f"{label} {value} 处于上限 {high} 边界")

    if not in_range:
        return {"score": config["outOfRange"], "basis": "；".join(reasons)}
    if at_boundary:
        return {"score": config["boundary"], "basis": "；".join(reasons)}
    return {"score": config["inRange"], "basis": "规模落在政策门槛区间内"}


def _score_credit(profile: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """信用记录。返回 None 表示剔除分母。

    enterpriseCredit 实测 100% 为空。缺失时剔除分母而非白送 10 分——
    「不知道有没有失信」不等于「确认信用良好」。
    """
    config = tx.scoring_config()["creditScores"]
    credit = profile.get("credit") or {}
    if not credit.get("checked"):
        return None
    if credit.get("negative"):
        return {"score": config["negative"], "basis": "存在信用负面记录"}
    return {"score": config["clean"], "basis": "信用记录无负面"}


# --------------------------------------------------------------------------
# 附加分
# --------------------------------------------------------------------------


def resolve_timeliness(
    window: Optional[Dict[str, Any]], today: date
) -> Dict[str, Any]:
    """时效性判定。三档互斥，窗口缺失记 unknown 得 0 分。"""
    bonus = tx.scoring_config()["bonus"]
    if not isinstance(window, dict):
        return {"band": TIMELINESS_UNKNOWN, "score": 0, "basis": "政策未标注申报期"}

    if window.get("isRolling"):
        return {
            "band": TIMELINESS_IN_PROGRESS,
            "score": bonus["timelinessInProgress"],
            "basis": "常年受理",
        }

    start = _parse_iso_date(window.get("startDate"))
    end = _parse_iso_date(window.get("endDate"))
    if start is None and end is None:
        return {"band": TIMELINESS_UNKNOWN, "score": 0, "basis": "政策未标注申报期"}

    if start is not None and today < start:
        if start.year == today.year:
            return {
                "band": TIMELINESS_NOT_STARTED,
                "score": bonus["timelinessNotStarted"],
                "basis": f"当年申报未启动（{start.isoformat()} 开始）",
            }
        return {
            "band": TIMELINESS_NOT_STARTED,
            "score": bonus["timelinessInProgress"],
            "basis": f"申报期在 {start.year} 年，尚未启动",
        }

    if end is not None and today > end:
        # docx 写「2025 年政策按 +8 分倾斜」，已改为相对逻辑（上一年度），
        # 避免年份写死随时间失效。
        if end.year == today.year - 1:
            return {
                "band": TIMELINESS_PAST_YEAR,
                "score": bonus["timelinessPreviousYear"],
                "basis": f"上一年度政策（{end.isoformat()} 截止），仍具参考价值",
            }
        return {
            "band": TIMELINESS_PAST_YEAR,
            "score": bonus["timelinessPastYear"],
            "basis": f"往年政策（{end.isoformat()} 截止）",
        }

    return {
        "band": TIMELINESS_IN_PROGRESS,
        "score": bonus["timelinessInProgress"],
        "basis": "申报期进行中",
    }


def _score_keyword(
    keyword: str, policy_tags: Dict[str, Any], policy: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """关键词命中。四级取最高一级，不叠加（docx 的「优先级」语义）。

    未传 keyword 时返回 None，该项不计分。
    """
    word = F.text(keyword)
    if not word:
        return None

    bonus = tx.scoring_config()["bonus"]
    index = policy_tags.get("keywordIndex") or {}
    policy = policy or {}

    title = F.text(policy.get("title") or (policy.get("policyInfo") or {}).get("policyTitle"))
    if not title:
        title = " ".join(str(t) for t in (index.get("title") or []))
    if word in title:
        return {"score": bonus["keywordTitle"], "basis": f"关键词命中政策标题：{word}"}

    a_words = [str(t) for t in (index.get("aKeywords") or [])]
    if any(word in t or t in word for t in a_words):
        return {"score": bonus["keywordATag"], "basis": f"关键词命中支持方式标签：{word}"}

    c_words = [str(t) for t in (index.get("cKeywords") or [])]
    if any(word in t or t in word for t in c_words):
        return {"score": bonus["keywordCTag"], "basis": f"关键词命中行业标签：{word}"}

    content = " ".join(
        F.text(v) for v in (
            (policy.get("policyInfo") or {}).get("summary"),
            policy.get("notes"),
        )
    )
    if word in content:
        return {"score": bonus["keywordContent"], "basis": f"关键词命中政策正文：{word}"}

    return {"score": 0, "basis": f"关键词「{word}」未命中"}


def _score_chain(
    policy_tags: Dict[str, Any], chain_stages: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """链路加成。企业正处于该政策培育链路的上一环节时加分。"""
    bonus = tx.scoring_config()["bonus"]
    chain = policy_tags.get("growthChain") or {}
    prerequisite = F.text(chain.get("prerequisite"))
    if not prerequisite or not chain_stages:
        return None

    for item in chain_stages:
        stage = F.text(item.get("stage"))
        if stage and (prerequisite in stage or stage in prerequisite):
            return {
                "score": bonus["chainBonus"],
                "basis": f"企业处于培育链路「{stage}」，本政策为其下一环节",
            }
    return None


def build_secondary_sort_key(
    policy_tags: Dict[str, Any], today: date
) -> List[float]:
    """生成可序列化的同分次级排序键；数值越小越优先。"""
    config = tx.scoring_config().get("secondarySort") or {}
    if not config.get("enabled", False):
        return []

    window = policy_tags.get("applicationWindow") or {}
    end = _parse_iso_date(window.get("endDate"))
    days_to_deadline: Optional[int] = None
    if end is not None and end >= today:
        days_to_deadline = (end - today).days
    elif window.get("isRolling"):
        # 常年受理有明确时效信息，但紧迫度低于有截止期的政策。
        days_to_deadline = 1000000

    tags = policy_tags.get("tags") or {}
    support = F.text((tags.get("E") or {}).get("value"))
    threshold = F.text((tags.get("D") or {}).get("value"))
    support_level = int(support[1:]) if support.startswith("E") and support[1:].isdigit() else None
    threshold_level = int(threshold[1:]) if threshold.startswith("D") and threshold[1:].isdigit() else None

    confidences = [
        _CONFIDENCE_SCORES.get(F.text((tags.get(dim) or {}).get("confidence")), 0)
        for dim in "ABCDEF"
    ]
    confidence_score = sum(confidences) / len(confidences) if confidences else None
    values = {
        "daysToDeadline": days_to_deadline,
        "supportLevel": support_level,
        "thresholdLevel": threshold_level,
        "confidenceScore": confidence_score,
    }

    result: List[float] = []
    for item in config.get("keys") or []:
        field = F.text(item.get("field"))
        value = values.get(field)
        # 每个维度先放缺失标记，确保无论升降序，缺失值始终靠后。
        result.append(1.0 if value is None else 0.0)
        if value is None:
            result.append(0.0)
        elif F.text(item.get("order")) == "desc":
            result.append(-float(value))
        else:
            result.append(float(value))
    return result


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------


def score_policy(
    profile: Dict[str, Any],
    policy_tags: Dict[str, Any],
    keyword: str = "",
    today: Optional[date] = None,
    chain_stages: Optional[List[Dict[str, Any]]] = None,
    policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """对单条政策评分。

    today 可注入，便于测试时效性（不用 date.today() 硬编码）。
    """
    reference_date = today or date.today()
    config = tx.scoring_config()
    weights = config["baseWeights"]

    # ---------- 基础分 ----------
    computed: List[Tuple[str, str, Optional[Dict[str, Any]]]] = [
        ("领域匹配度", "industryMatch", _score_industry(profile, policy_tags)),
        ("发展阶段匹配", "stageMatch", _score_stage(profile, policy_tags)),
        ("创新活跃度", "innovationActivity", _score_innovation(profile)),
        ("规模适配", "scaleMatch", _score_scale(profile, policy_tags)),
        ("信用记录", "creditRecord", _score_credit(profile)),
    ]

    items: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    raw_sum = 0.0
    effective_weight = 0

    for label, key, result in computed:
        weight = weights[key]
        if result is None:
            excluded.append({
                "name": label,
                "weight": weight,
                "reason": _exclusion_reason(key, profile),
            })
            continue
        items.append({
            "name": label,
            "weight": weight,
            "score": result["score"],
            "basis": result["basis"],
        })
        raw_sum += result["score"]
        effective_weight += weight

    if effective_weight == 0:
        # 五项全部剔除：不返回 0 分（会被误读为「匹配度极差」），
        # 明确标为不可评估。
        return {
            "totalScore": None,
            "baseScore": None,
            "bonusScore": 0,
            "band": BAND_NOT_EVALUABLE,
            "baseDetail": {
                "items": [], "excludedItems": excluded,
                "effectiveWeight": 0, "rawSum": 0.0, "normalized": None,
            },
            "bonusDetail": {"items": [], "capped": False, "total": 0},
            "reason": "企业侧全部计分字段均无值，无法评估匹配度",
        }

    base_score = round(raw_sum / effective_weight * 100, 2)

    # ---------- 附加分 ----------
    bonus_items: List[Dict[str, Any]] = []
    keyword_result = _score_keyword(keyword, policy_tags, policy)
    if keyword_result and keyword_result["score"] > 0:
        bonus_items.append({"name": "关键词命中", **keyword_result})

    timeliness = resolve_timeliness(policy_tags.get("applicationWindow"), reference_date)
    if timeliness["score"] > 0:
        bonus_items.append({
            "name": "时效性", "score": timeliness["score"],
            "basis": timeliness["basis"], "band": timeliness["band"],
        })

    chain_result = _score_chain(policy_tags, chain_stages or [])
    if chain_result:
        bonus_items.append({"name": "链路加成", **chain_result})

    bonus_raw = sum(item["score"] for item in bonus_items)
    cap = config["bonus"]["cap"]
    bonus_score = min(bonus_raw, cap)

    total = round(base_score + bonus_score, 2)
    thresholds = config["thresholds"]
    if total >= thresholds["directPush"]:
        band = BAND_DIRECT
    elif total >= thresholds["needsSupplement"]:
        band = BAND_SUPPLEMENT
    else:
        band = BAND_NOT_RECOMMENDED

    return {
        "totalScore": total,
        "baseScore": base_score,
        "bonusScore": bonus_score,
        "band": band,
        "baseDetail": {
            "items": items,
            "excludedItems": excluded,
            "effectiveWeight": effective_weight,
            "rawSum": round(raw_sum, 2),
            "normalized": base_score,
        },
        "bonusDetail": {
            "items": bonus_items,
            "capped": bonus_raw > cap,
            "rawTotal": bonus_raw,
            "total": bonus_score,
        },
        "timeliness": timeliness,
        "secondarySortKey": build_secondary_sort_key(policy_tags, reference_date),
    }


def _exclusion_reason(key: str, profile: Dict[str, Any]) -> str:
    """说明该计分项为何被剔除分母，使分数可解释。"""
    availability = profile.get("fieldAvailability") or {}
    if key == "industryMatch":
        return "企业行业字段（industryLevel1/2）无值，C 维度为 unknown"
    if key == "stageMatch":
        return "成立年限、营收、资质三信号均无值，B 维度为 unknown"
    if key == "innovationActivity":
        return "知识产权字段全部无值（区别于确认的 0 件）"
    if key == "scaleMatch":
        return "employeeCount 与 operatingIncome 均无值"
    if key == "creditRecord":
        return "enterpriseCredit 无值，未执行信用校验"
    return f"字段无值（{availability.get(key, F.UNKNOWN)}）"
