# -*- coding: utf-8 -*-
"""路径与环境变量集中定义。

其余模块统一从这里取路径与配置，避免各处重复推算 PROJECT_ROOT。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

# service/paths.py → PEMatching_v2/
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
# PEMatching_v2/ → backend/ → 仓库根
REPO_ROOT = PACKAGE_ROOT.parent.parent

CONFIG_DIR = PACKAGE_ROOT / "config"
PROMPTS_DIR = PACKAGE_ROOT / "prompts"
LOGS_DIR = PACKAGE_ROOT / "logs"
OUTPUT_DIR = PACKAGE_ROOT / "output"
ELIGIBILITY_CONFIG_DIR = CONFIG_DIR / "eligibility"
ELIGIBILITY_MANIFEST_PATH = ELIGIBILITY_CONFIG_DIR / "manifest.json"
ELIGIBILITY_PROFILES_PATH = ELIGIBILITY_CONFIG_DIR / "policy_profiles.jsonl"
ELIGIBILITY_FIELD_MAPPING_PATH = ELIGIBILITY_CONFIG_DIR / "field_mapping.json"
ELIGIBILITY_UNIT_MAPPING_PATH = ELIGIBILITY_CONFIG_DIR / "unit_mapping.json"
ELIGIBILITY_ARCHIVE_DIR = OUTPUT_DIR / "eligibility_runs"

# 人工维护的源文件（提交到 git）
TAXONOMY_PATH = CONFIG_DIR / "tag_taxonomy.json"
INDUSTRY_MAP_PATH = CONFIG_DIR / "industry_to_c_tag.json"
KEYWORD_LIBRARY_PATH = CONFIG_DIR / "keyword_library.json"
RULES_PATH = CONFIG_DIR / "rules.json"
# 新匹配链路使用的资质成长图。它是人工维护的业务配置，不由模型生成。
QUALIFICATION_GROWTH_CHAIN_PATH = CONFIG_DIR / "qualification_growth_chain.json"
# 条件匹配评分规则与提示词均为新链路的显式版本输入，参与缓存键。
MATCH_RULES_PATH = CONFIG_DIR / "match_rules.json"
POLICY_CONDITION_MATCH_PROMPT_PATH = PROMPTS_DIR / "policy_condition_match_prompt.md"

# 生成物（不提交）
POLICY_SNAPSHOT_PATH = CONFIG_DIR / "policy_snapshot.json"
POLICY_CONDITION_SNAPSHOT_PATH = CONFIG_DIR / "policy_condition_snapshot.json"
POLICY_TAGS_PATH = CONFIG_DIR / "policy_tags.json"
POLICY_TAGS_OVERRIDES_PATH = CONFIG_DIR / "policy_tags_overrides.json"
# 政策原文条件到企业画像字段的可执行映射。它独立于标签，不能由打标模型覆盖。
POLICY_ELIGIBILITY_RULES_PATH = CONFIG_DIR / "policy_eligibility_rules.json"


def _load_dotenv() -> None:
    """加载 PEMatching_v2/.env。缺失时不报错，交由各配置项的默认值兜底。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = PACKAGE_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)


_load_dotenv()


def env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value.strip() if isinstance(value, str) and value.strip() else default


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


# 独立政策库目录：设置后快照/标签/复核覆盖都从该目录读取。
# 用于“不与已审核主政策混在一起”的第二套政策库（如 SYSDBA 导出的 Excel 转换库）。
# 生效方式：启动进程前设置 V2_POLICY_LIBRARY_DIR=<目录>（目录内需含 snapshot.json / tags.json）。
# 注意：带此变量时不要运行 run_sync_policies.py——会把 API 拉取的主政策覆盖进独立库快照。
POLICY_LIBRARY_DIR = env_str("V2_POLICY_LIBRARY_DIR", "")
if POLICY_LIBRARY_DIR:
    _library = Path(POLICY_LIBRARY_DIR)
    POLICY_SNAPSHOT_PATH = _library / "snapshot.json"
    POLICY_CONDITION_SNAPSHOT_PATH = _library / "condition_snapshot.json"
    POLICY_TAGS_PATH = _library / "tags.json"
    POLICY_TAGS_OVERRIDES_PATH = _library / "tags_overrides.json"


@lru_cache(maxsize=None)
def sample_enterprise_dir() -> Path:
    """一期统计与验收用的企业样本目录。只读引用，不修改其内容。"""
    configured = env_str("V2_SAMPLE_ENTERPRISE_DIR", "backend/PEMatching/企业测试目录")
    path = Path(configured)
    return path if path.is_absolute() else REPO_ROOT / path


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write_json(path: Path, payload: Any) -> None:
    """原子写 JSON：先写 .tmp 再 os.replace，避免中断留下半份文件。"""
    import json

    ensure_dir(path.parent)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
