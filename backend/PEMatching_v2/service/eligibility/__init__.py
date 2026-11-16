# -*- coding: utf-8 -*-
"""第五期独立资格门禁链路。

本包不能依赖 V1，也不能被 V2 推荐评分器反向依赖。
"""

from service.eligibility.gate_service import evaluate_policies

__all__ = ["evaluate_policies"]
