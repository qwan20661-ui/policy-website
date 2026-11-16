# -*- coding: utf-8 -*-
"""资格证据的安全分类。"""

from __future__ import annotations

from typing import Any, Dict

from service import fields as F


def reject_fact(fact: Dict[str, Any], reason: str) -> Dict[str, Any]:
    result = dict(fact)
    result.update({"evidenceTier": "R_rejected", "bucket": "unverified", "reason": reason, "value": None, "sourcePaths": []})
    return result


def is_verified(fact: Dict[str, Any]) -> bool:
    return F.text(fact.get("evidenceTier")) in {"A1_exact", "A2_derived"}
