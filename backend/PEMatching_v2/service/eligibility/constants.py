# -*- coding: utf-8 -*-
"""资格评分兼容常量与稳定枚举。"""

DEFAULT_MISSING_SCORE = 0.1
PRESENT_DATA_BASE_SCORE = 0.3
RECOMMENDATION_SCORE_THRESHOLD = 0.6
SPECIAL_PASS_MIN_SCORE = 0.9

PROFILE_SCHEMA = "pematching-v2-eligibility-profile-v1"
MANIFEST_SCHEMA = "pematching-v2-eligibility-manifest-v1"
RESULT_SCHEMA = "pematching-v2-eligibility-result-v1"
FIELD_MAP_SCHEMA = "pematching-v2-eligibility-field-map-v1"

GROUP_KEYS = ("hardRuleGroups", "softRuleGroups", "specialPassRuleGroups")
VALID_OPERATORS = {"gt", "gte", "lt", "lte", "eq", "equals", "is_true", "is_false", "has_value", "llm_judge"}
VALID_RULE_KINDS = {"", "direct", "derived", "reference", "proxy", "supporting"}
EXCLUDED_STATUSES = {"N_not_evaluable", "S_skipped"}
PENDING_EVIDENCE = {"B_supporting", "U_missing", "R_rejected", "N_not_evaluable", "S_skipped"}
