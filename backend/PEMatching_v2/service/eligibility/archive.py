# -*- coding: utf-8 -*-
"""资格运行归档。完整证据只落盘，不扩散到运行日志。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from service.paths import ELIGIBILITY_ARCHIVE_DIR, ensure_dir


def archiveable_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """移除仅供运行时字段适配使用、不可写入 JSON 的内部对象。"""
    return {key: value for key, value in profile.items() if key != "_availability"}


def archive_run(run_id: str, payload: Dict[str, Any]) -> Path:
    destination = ensure_dir(ELIGIBILITY_ARCHIVE_DIR) / f"{run_id}.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, destination)
    return destination
