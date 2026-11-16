from __future__ import annotations

import base64
import subprocess
from pathlib import Path
from typing import Any

from prompt_loader import RuleOptions


CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
EXPORTER_SOURCE = PROJECT_ROOT / "scripts" / "DamengRuleOptionExporter.java"
EXPORTER_CLASS = PROJECT_ROOT / "scripts" / "DamengRuleOptionExporter.class"
DRIVER_JAR = PROJECT_ROOT / "demo" / "lib" / "jdbc" / "DmJdbcDriver8.jar"


def load_rule_options_via_jdbc(settings: Any) -> RuleOptions:
    if not DRIVER_JAR.exists():
        raise FileNotFoundError(f"Dameng JDBC driver not found: {DRIVER_JAR}")
    if not EXPORTER_SOURCE.exists():
        raise FileNotFoundError(f"Dameng JDBC exporter source not found: {EXPORTER_SOURCE}")
    if not all([settings.db_host, settings.db_user, settings.db_password]):
        raise ValueError("Dameng database settings are incomplete")

    _compile_exporter_if_needed()
    sections = _run_exporter(settings)
    return _build_rule_options(sections)


def _compile_exporter_if_needed() -> None:
    if EXPORTER_CLASS.exists() and EXPORTER_CLASS.stat().st_mtime >= EXPORTER_SOURCE.stat().st_mtime:
        return

    subprocess.run(
        [
            "javac",
            "-encoding",
            "UTF-8",
            "-cp",
            str(DRIVER_JAR),
            str(EXPORTER_SOURCE),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def _run_exporter(settings: Any) -> dict[str, list[list[str | None]]]:
    classpath = f"{DRIVER_JAR};{EXPORTER_SOURCE.parent}"
    completed = subprocess.run(
        [
            "java",
            "-cp",
            classpath,
            "DamengRuleOptionExporter",
            str(settings.db_host),
            str(settings.db_port),
            str(settings.db_user),
            str(settings.db_password),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    sections: dict[str, list[list[str | None]]] = {}
    current: str | None = None
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("##"):
            current = line[2:]
            sections[current] = []
            continue
        if current is None:
            continue
        sections[current].append([_decode_cell(cell) for cell in line.split("\t")])
    return sections


def _decode_cell(value: str) -> str | None:
    if value == r"\N":
        return None
    return base64.b64decode(value).decode("utf-8")


def _build_rule_options(sections: dict[str, list[list[str | None]]]) -> RuleOptions:
    options = RuleOptions()

    scope_list: list[str] = []
    applicant_list: list[str] = []
    support_list: list[str] = []
    usage_list: list[str] = []

    for row in sections.get("RULE_OPTIONS", []):
        row_id, rule_type, level1, level2, option_value, _sort_order = _pad(row, 6)
        if rule_type == "scope_level":
            text = f"{level1}-{level2}" if level2 else (level1 or "")
            if text:
                scope_list.append(text)
                options.scope_map[text] = row_id
        elif rule_type == "applicant_type":
            text = option_value or ""
            if text:
                applicant_list.append(text)
                options.applicant_map[text] = row_id
        elif rule_type == "support_type" and level2:
            support_list.append(level2)
            options.support_map[level2] = row_id
        elif rule_type == "usage_scenario":
            text = option_value or ""
            if text:
                usage_list.append(text)
                options.usage_map[text] = row_id

    options.scope_options = "；".join(scope_list)
    options.applicant_options = "；".join(applicant_list)
    options.support_options = "；".join(support_list)
    options.usage_options = "；".join(usage_list)
    options.profile_category_options = _build_profile_category_options(sections.get("CATEGORY_FIELD_INFO", []))

    industry_options, industry_map = _build_industry_options(sections.get("INDUSTRY", []))
    options.industry_options = industry_options
    options.industry_map = industry_map
    options.keyword_options = "；".join(
        keyword
        for row in sections.get("KEYWORDS", [])
        for keyword in [_pad(row, 1)[0]]
        if keyword
    )
    return options


def _build_profile_category_options(rows: list[list[str | None]]) -> str:
    profile_parts: list[str] = []
    current_l1: str | None = None
    current_l2_items: list[str] = []

    for row in rows:
        l1, _l2, l3, core = _pad(row, 4)
        l1 = l1 or ""
        l3 = l3 or ""
        core = core or ""

        if l1 != current_l1:
            if current_l2_items:
                profile_parts.append(f"**{current_l1}**\n" + "\n".join(current_l2_items))
            current_l1 = l1
            current_l2_items = []

        current_l2_items.append(f"- {l3}：{core}" if core else f"- {l3}")

    if current_l2_items and current_l1:
        profile_parts.append(f"**{current_l1}**\n" + "\n".join(current_l2_items))

    return "\n\n".join(profile_parts).replace("{", "{{").replace("}", "}}")


def _build_industry_options(rows: list[list[str | None]]) -> tuple[str, dict[str, str | None]]:
    industry_map: dict[str, str | None] = {}
    industry_list: list[str] = []
    current_level1: str | None = None
    level1_items: list[str] = []

    for row in rows:
        row_id, level1, level2 = _pad(row, 3)
        level1 = level1 or ""
        level2 = level2 or ""

        if level1 != current_level1:
            if level1_items:
                industry_list.append("；".join(level1_items))
            current_level1 = level1
            level1_items = []

        text = f"{level1}-{level2}" if level2 else level1
        if text:
            level1_items.append(text)
            industry_map[text] = row_id

    if level1_items:
        industry_list.append("；".join(level1_items))
    return "；".join(industry_list), industry_map


def _pad(row: list[str | None], size: int) -> list[str | None]:
    return row + [None] * max(0, size - len(row))
