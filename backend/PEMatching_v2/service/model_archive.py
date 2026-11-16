# -*- coding: utf-8 -*-
"""可选的单政策模型归档，默认关闭并在写入前脱敏。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from service.paths import OUTPUT_DIR, atomic_write_json, ensure_dir, env_bool


MODEL_ARCHIVE_DIR = OUTPUT_DIR / "condition_match_model_archive"
_SENSITIVE_KEYS = {
    "enterpriseName", "creditCode", "registerAddress", "contactInfo", "contactPhone",
    "phone", "mobile", "email", "idCard", "identityNo",
}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if key in _SENSITIVE_KEYS:
                result[key] = "***"
            elif key == "raw" and isinstance(child, str):
                result[key] = "***"
            else:
                result[key] = _redact(child)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return deepcopy(value)


def archive_model_exchange(payload: Dict[str, Any]) -> bool:
    """在开关开启时写入脱敏请求和响应；归档故障不应影响匹配结果。"""
    if not env_bool("PEMATCHING_V2_MODEL_ARCHIVE_ENABLED", False):
        return False
    try:
        target = ensure_dir(MODEL_ARCHIVE_DIR) / (
            f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid4().hex[:8]}.json"
        )
        atomic_write_json(target, _redact(payload))
        return True
    except OSError:
        return False
