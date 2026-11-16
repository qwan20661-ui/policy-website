from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymysql


@dataclass
class MarkdownPrompt:
    system: str
    user_template: str


@dataclass
class RuleOptions:
    """规则选项数据"""
    scope_options: str = ""  # 适用范围选项（用于提示词）
    scope_map: dict[str, int] = field(default_factory=dict)  # {option_value: id}
    applicant_options: str = ""  # 申报对象选项
    applicant_map: dict[str, int] = field(default_factory=dict)
    support_options: str = ""  # 扶持类型选项
    support_map: dict[str, int] = field(default_factory=dict)
    usage_options: str = ""  # 场景用途选项
    usage_map: dict[str, int] = field(default_factory=dict)
    industry_options: str = ""  # 行业领域选项
    industry_map: dict[str, int] = field(default_factory=dict)
    keyword_options: str = ""  # 关键词选项
    profile_category_options: str = ""  # 条件聚类选项（企业画像分类）


def load_markdown_prompt(
    path: Path,
    required_placeholders: tuple[str, ...] = (),
) -> MarkdownPrompt:
    content = path.read_text(encoding="utf-8")
    system = _extract_section(content, "system", path)
    user_template = _extract_section(content, "user", path)

    for placeholder in required_placeholders:
        token = "{" + placeholder + "}"
        if token not in user_template:
            raise ValueError(f"提示词文件缺少占位符 {token}: {path}")

    return MarkdownPrompt(system=system, user_template=user_template)


def _extract_section(content: str, section_name: str, path: Path) -> str:
    pattern = rf"^##\s+{re.escape(section_name)}\s*$([\s\S]*?)(?=^##\s+|\Z)"
    match = re.search(pattern, content, flags=re.M)
    if not match:
        raise ValueError(f"提示词文件缺少 ## {section_name} 段落: {path}")
    return match.group(1).strip()


def load_rule_options_from_db(connection: Any) -> RuleOptions:
    """从数据库加载规则选项"""
    options = RuleOptions()

    with connection.cursor(pymysql.cursors.DictCursor) as cursor:
        # 加载适用范围 (scope_level)
        cursor.execute(
            "SELECT id, level1_name, level2_name, option_value FROM dim_rule_option "
            "WHERE rule_type = 'scope_level' AND status = 1 ORDER BY sort_order"
        )
        scope_rows = cursor.fetchall()
        scope_list = []
        for row in scope_rows:
            if row.get("level2_name"):
                text = f"{row['level1_name']}-{row['level2_name']}"
            else:
                text = row["level1_name"]
            scope_list.append(text)
            options.scope_map[text] = row["id"]
        options.scope_options = "；".join(scope_list)

        # 加载申报对象 (applicant_type)
        cursor.execute(
            "SELECT id, level1_name, level2_name, option_value FROM dim_rule_option "
            "WHERE rule_type = 'applicant_type' AND status = 1 ORDER BY sort_order"
        )
        applicant_rows = cursor.fetchall()
        applicant_list = []
        for row in applicant_rows:
            text = row["option_value"]
            applicant_list.append(text)
            options.applicant_map[text] = row["id"]
        options.applicant_options = "；".join(applicant_list)

        # 加载扶持类型 (support_type) - 只取二级分类
        cursor.execute(
            "SELECT id, level1_name, level2_name, option_value FROM dim_rule_option "
            "WHERE rule_type = 'support_type' AND status = 1 AND level2_name IS NOT NULL ORDER BY sort_order"
        )
        support_rows = cursor.fetchall()
        support_list = []
        for row in support_rows:
            text = row["level2_name"]
            support_list.append(text)
            options.support_map[text] = row["id"]
        options.support_options = "；".join(support_list)

        # 加载场景用途 (usage_scenario)
        cursor.execute(
            "SELECT id, level1_name, level2_name, option_value FROM dim_rule_option "
            "WHERE rule_type = 'usage_scenario' AND status = 1 ORDER BY sort_order"
        )
        usage_rows = cursor.fetchall()
        usage_list = []
        for row in usage_rows:
            text = row["option_value"]
            usage_list.append(text)
            options.usage_map[text] = row["id"]
        options.usage_options = "；".join(usage_list)

        # 加载条件聚类选项 (category_field_info 表)
        cursor.execute(
            "SELECT level1_category, level2_category, level3_category, core_fields "
            "FROM category_field_info "
            "ORDER BY level1_category, level2_category, level3_category"
        )
        rows = cursor.fetchall()

        profile_parts = []
        current_l1 = None
        current_l2_items = []

        for row in rows:
            l1 = row.get("level1_category", "")
            l2 = row.get("level2_category", "")
            l3 = row.get("level3_category", "")
            core = row.get("core_fields", "") or ""

            # 一级分类变化时，输出前一个分类
            if l1 != current_l1:
                if current_l2_items:
                    profile_parts.append(f"**{current_l1}**\n" + "\n".join(current_l2_items))
                current_l1 = l1
                current_l2_items = []

            # 格式化：- 三级分类：核心字段
            if core:
                line = f"- {l3}：{core}"
            else:
                line = f"- {l3}"
            current_l2_items.append(line)

        # 输出最后一个分类
        if current_l2_items and current_l1:
            profile_parts.append(f"**{current_l1}**\n" + "\n".join(current_l2_items))

        # 转义花括号，防止破坏 Python str.format()
        profile_text = "\n\n".join(profile_parts)
        profile_text = profile_text.replace("{", "{{").replace("}", "}}")
        options.profile_category_options = profile_text

    return options


def load_industry_options_from_db(connection: Any) -> tuple[str, dict[str, int]]:
    """从数据库加载行业领域选项 (sys_territory_data_new 表)"""
    industry_map: dict[str, int] = {}
    industry_list = []

    with connection.cursor(pymysql.cursors.DictCursor) as cursor:
        # 加载行业领域数据
        cursor.execute(
            "SELECT id, territory_name, territory_two_name FROM sys_territory_data_new "
            "WHERE del_flag = 0 AND territory_name IS NOT NULL "
            "ORDER BY territory_name, territory_two_name"
        )
        rows = cursor.fetchall()

        # 按一级分类整理
        current_level1 = None
        level1_items = []

        for row in rows:
            level1 = row.get("territory_name", "")
            level2 = row.get("territory_two_name") or ""
            row_id = row.get("id")

            if level1 != current_level1:
                if level1_items:
                    industry_list.append("；".join(level1_items))
                current_level1 = level1
                level1_items = []

            if level2:
                text = f"{level1}-{level2}"
                level1_items.append(text)
            else:
                text = level1
                level1_items.append(text)

            industry_map[text] = row_id

        if level1_items:
            industry_list.append("；".join(level1_items))

    industry_options = "；".join(industry_list)
    return industry_options, industry_map


def load_keyword_options_from_db(connection: Any) -> str:
    """从数据库加载关键词选项 (policy_keyword_library 表)"""
    keyword_list = []

    with connection.cursor(pymysql.cursors.DictCursor) as cursor:
        cursor.execute(
            "SELECT keyword_text FROM policy_keyword_library "
            "WHERE status = 1 ORDER BY keyword_category, keyword_text"
        )
        rows = cursor.fetchall()
        for row in rows:
            keyword_text = row.get("keyword_text", "")
            if keyword_text:
                keyword_list.append(keyword_text)

    return "；".join(keyword_list)


def load_profile_category_options_from_db(connection: Any) -> str:
    """从数据库加载条件聚类选项 (category_field_info 表)"""
    options_list = []

    with connection.cursor(pymysql.cursors.DictCursor) as cursor:
        cursor.execute(
            "SELECT level1_category, level2_category, level3_category, core_fields "
            "FROM category_field_info "
            "ORDER BY level1_category, level2_category, level3_category"
        )
        rows = cursor.fetchall()

        current_l1 = None
        current_l2_items = []

        for row in rows:
            l1 = row.get("level1_category", "")
            l2 = row.get("level2_category", "")
            l3 = row.get("level3_category", "")
            core = row.get("core_fields", "") or ""

            # 一级分类变化时，输出前一个分类
            if l1 != current_l1:
                if current_l2_items:
                    options_list.append("\n".join(current_l2_items))
                current_l1 = l1
                current_l2_items = []

            # 格式化：二级分类 - 三级分类：核心字段
            if core:
                line = f"- {l2} - {l3}：{core}"
            else:
                line = f"- {l2} - {l3}"
            current_l2_items.append(line)

        # 输出最后一个分类
        if current_l2_items:
            options_list.append("\n".join(current_l2_items))

    return "\n\n".join(options_list)
