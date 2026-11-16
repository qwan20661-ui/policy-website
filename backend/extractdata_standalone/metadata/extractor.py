from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from config import MetadataSettings
from llm_client import MetadataLLMClient

# 全局缓存：数据库关键词库（从 policy_keyword_library 表加载）
_db_keyword_cache: set[str] = set()
_db_keyword_loaded: bool = False


def get_attachment_list(file_path: Path) -> str:
    """读取政策文件夹下 attachments 文件夹中的附件文件名列表"""
    # file_path 可能是政策文件夹下的 .md 文件，attachments 在同级目录
    attachment_dir = file_path.parent / "attachments"
    if not attachment_dir.is_dir():
        return "无"
    files = [f.name for f in attachment_dir.iterdir() if f.is_file()]
    if not files:
        return "无"
    return "、".join(files)


def read_md(file_path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        try:
            return file_path.read_text(encoding=encoding)
        except Exception:
            continue
    raise ValueError(f"无法识别文件编码: {file_path}")


def read_file_content(file_path: Path) -> str:
    extension = file_path.suffix.lower()
    if extension == ".md":
        return read_md(file_path)
    raise ValueError(f"暂不支持的文件类型: {extension}")


def clean_text(text: str) -> str:
    if not text:
        return ""

    cleaned = text.replace("\r", "\n")

    # 关键元数据关键词，保留包含这些关键词的行
    KEY_META_KEYWORDS = [
        "申报量", "申报对象", "申报时间", "政策文号", "政策级别",
        "发文部门", "发布日期", "发布机关", "文号", "有效期",
    ]

    # 先提取包含关键元数据的行
    meta_lines = []
    lines = cleaned.split("\n")
    for line in lines:
        if any(kw in line for kw in KEY_META_KEYWORDS):
            # 去掉开头的 -/*/+ 等符号但保留内容
            meta_line = re.sub(r"^\s*[-*+]\s*", "", line).strip()
            if meta_line:
                meta_lines.append(meta_line)

    cleaned = re.sub(r"!\[.*?\]\(.*?\)", "", cleaned)
    cleaned = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", cleaned)
    cleaned = re.sub(r"```.*?```", "", cleaned, flags=re.S)
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = re.sub(r"^\s{0,3}#{1,6}\s*", "", cleaned, flags=re.M)
    cleaned = re.sub(r"^\s*>\s?", "", cleaned, flags=re.M)
    cleaned = re.sub(r"^\s*[-*+]\s+", "", cleaned, flags=re.M)
    cleaned = re.sub(r"^\s*\d+\.\s+", "", cleaned, flags=re.M)
    cleaned = re.sub(r"\n\s*\n+", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)

    # 在正文末尾追加关键元数据
    if meta_lines:
        cleaned = cleaned.strip() + "\n\n【关键元数据】\n" + "\n".join(meta_lines)

    return cleaned.strip()


# 申报条件聚类完整模板
CLUSTER_TEMPLATE = {
    "工商基础信息": {
        "基础信息": {
            "上市情况": "",
            "企业性质": "",
            "企业类型": {"value": [], "source_text": ""},
            "单位名称": "",
            "实缴资本": {"value": 0, "operator": "", "unit": "", "source_text": ""},
            "成立年限": {"value": 0, "operator": "", "unit": "", "source_text": ""},
            "机构性质": "",
            "法定代表人": "",
            "注册日期": "",
            "注册资本": {"value": 0, "operator": "", "unit": "", "source_text": ""},
            "统一社会信用代码": ""
        },
        "注册信息": {
            "实际经营地址": "",
            "属地信息": "",
            "注册地址": "",
            "注册日期": "",
            "纳税属地": ""
        },
        "经营信息": {
            "主体资格": "",
            "单位类型": "",
            "经营状态": "",
            "经营范围": ""
        }
    },
    "主要技术": {
        "国家战略新兴产业分类": "",
        "所属行业领域": "",
        "核心技术领域": ""
    },
    "主要产品": {
        "产品信息": {
            "主营产品": "",
            "主营服务": "",
            "核心产品": "",
            "产品阶段": ""
        }
    },
    "核心团队": {
        "人才条件": {
            "健康状况": "",
            "国籍要求": "",
            "年龄要求": {"value": 0, "operator": "", "unit": "岁", "source_text": ""},
            "语言能力": ""
        },
        "基础信息": {
            "企业职工总数": {"value": 0, "operator": "", "unit": "人", "source_text": ""},
            "团队人数": "",
            "研发人员数量": {"value": 0, "operator": "", "unit": "人", "source_text": ""},
            "科技人员占比": {"value": 0, "operator": "", "unit": "%", "source_text": ""},
            "科技人员数量": {"value": 0, "operator": "", "unit": "人", "source_text": ""},
            "项目负责人身份": ""
        },
        "学历结构": {
            "博士人数": {"value": 0, "operator": "", "unit": "人", "source_text": ""},
            "本科人数": {"value": 0, "operator": "", "unit": "人", "source_text": ""},
            "硕士人数": {"value": 0, "operator": "", "unit": "人", "source_text": ""},
            "科技奖励": {"value": [], "source_text": ""},
            "职称要求": "",
            "项目经历": ""
        }
    },
    "创新投入": {
        "研发基础设施": {
            "设施情况": ""
        },
        "研发管理": {
            "研发制度": ""
        },
        "研发设备": {
            "研发设备原值": {"value": 0, "operator": "", "unit": "万元", "source_text": ""}
        },
        "研发费用": {
            "年研发投入": {"value": 0, "operator": "", "unit": "万元", "source_text": ""},
            "研发投入强度": {"value": 0, "operator": "", "unit": "%", "source_text": ""}
        },
        "项目实施条件": {
            "实施地点": "",
            "执行期限": "",
            "项目经费": {"value": 0, "operator": "", "unit": "万元", "source_text": ""}
        }
    },
    "创新产出": {
        "技术交易": {
            "成果类型": "",
            "技术交易额": {"value": 0, "operator": "", "unit": "万元", "source_text": ""},
            "技术成果转化": ""
        },
        "知识产权": {
            "专利": "",
            "成果": "",
            "标准": {"value": [], "source_text": ""},
            "论文或文献": {"value": 0, "operator": "", "unit": "篇", "source_text": ""},
            "软著或商标": {"value": 0, "operator": "", "unit": "件", "source_text": ""},
            "知识产权约定": ""
        }
    },
    "创新发展": {
        "企业发展规模": {
            "人员规模": {"value": 0, "operator": "", "unit": "人", "source_text": ""},
            "市场规模": {"value": 0, "operator": "", "unit": "亿元", "source_text": ""},
            "税收规模": {"value": 0, "operator": "", "unit": "万元", "source_text": ""},
            "营收规模": {"value": 0, "operator": "", "unit": "亿元", "source_text": ""},
            "资产规模": {"value": 0, "operator": "", "unit": "亿元", "source_text": ""}
        },
        "企业成长性": {
            "净利润增长率": {"value": 0, "operator": "", "unit": "%", "source_text": ""},
            "净资产增长率": {"value": 0, "operator": "", "unit": "%", "source_text": ""},
            "研发增长率": {"value": 0, "operator": "", "unit": "%", "source_text": ""},
            "营收增长率": {"value": 0, "operator": "", "unit": "%", "source_text": ""}
        }
    },
    "创新影响": {
        "产学研合作": {
            "合作协议": "",
            "合作基础": "",
            "国际合作基础": "",
            "科研院所合作": "",
            "高校合作": ""
        },
        "信用合规": {
            "产品合规": "",
            "企业信用": {
                "经营异常名录": False,
                "source_text": ""
            },
            "伦理合规": "",
            "安全生产": {
                "重大安全事故": False,
                "source_text": ""
            },
            "工商信用": {
                "严重违法失信企业名单": False,
                "source_text": ""
            },
            "年报公式": "",
            "政府人员限制": "",
            "法律合规": "",
            "科研信用": {
                "科研严重失信": False,
                "source_text": ""
            },
            "税务信用": "",
            "行政处罚": "",
            "质量合规": {
                "重大质量事故": False,
                "source_text": ""
            },
            "重复申报限制": ""
        },
        "融资情况": {
            "投资单位": "",
            "融资金额": {"value": 0, "operator": "", "unit": "万元", "source_text": ""},
            "融资阶段": ""
        },
        "资质荣誉": {
            "人才资质": {"value": [], "source_text": ""},
            "企业资质": {"value": [], "source_text": ""},
            "平台资质": {"value": [], "source_text": ""}
        },
        "项目合作": {
            "共建单位数量": {"value": 0, "operator": "", "unit": "家", "source_text": ""},
            "共建单位类型": "",
            "联合申报": ""
        }
    }
}


def build_empty_result(source_name: str) -> dict:
    return {
        "文件名": source_name,
        "元数据": {
            "场景用途": "",
            "政策属性": "",
            "发文部门": "",
            "政策标题": "",
            "政策原标题": "",
            "政策文号": "",
            "政策关键词": [],
            "有效日期": "",
            "指导政策": [],
            "实施细则": [],
            "通知公告": [],
            "适用范围": "",
            "行业领域": "",
        },
        "摘要": "",
        "申报条件": {
            "适用对象": "",
            "申报要求": "",
            "申报时间": "",
            "公告日期": "",
        },
        "申报条件聚类": copy.deepcopy(CLUSTER_TEMPLATE),
        "支持方式": {
            "扶持类型": "",
            "扶持内容描述": "",
            "扶持金额": "",
            "最高扶持金额": "",
        },
        "申报材料及方式": {
            "申报材料": "",
            "申报方式": "",
            "申报链接": "",
            "附件": "",
        },
        "联系方式": "",
        "申报量": "",
        "原文地址": "",
    }


def normalize_match_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def sanitize_array_by_source(items: object, source_text: str) -> list[str]:
    if not isinstance(items, list):
        return []

    normalized_source = normalize_match_text(source_text)
    cleaned: list[str] = []
    seen: set[str] = set()

    for item in items:
        if not isinstance(item, str):
            continue

        value = item.strip().strip("\"'")
        if not value:
            continue

        # 去掉金额部分后检查（支持"研发补助 最高100万元"格式）
        # 提取关键词主体部分（去掉"最高X万元"等金额描述）
        amount_patterns = [
            r'\s*最高\s*\d+[\d.]*\s*[万元亿/]+.*$',
            r'\s*\d+[\d.]*\s*[万元亿/]+.*$',
        ]
        keyword_base = value
        for pattern in amount_patterns:
            keyword_base = re.sub(pattern, '', keyword_base, flags=re.IGNORECASE)
        keyword_base = keyword_base.strip()

        # 检查主体部分是否在原文中
        normalized_base = normalize_match_text(keyword_base) if keyword_base else ""
        normalized_value = normalize_match_text(value)

        # 如果主体不在原文但完整关键词不在，也跳过
        if normalized_base and normalized_base not in normalized_source:
            if not normalized_value or normalized_value not in normalized_source:
                continue
        elif normalized_value and normalized_value not in normalized_source:
            continue

        if value not in seen:
            seen.add(value)
            cleaned.append(value)

    return cleaned


def extract_plan_cycle_keywords(source_text: str) -> list[str]:
    matches = re.findall(
        r"(?<![A-Za-z0-9])[一二三四五六七八九十百零〇两]{2,4}五(?![A-Za-z0-9])",
        source_text,
    )
    ordered: list[str] = []
    seen: set[str] = set()

    for match in matches:
        value = match.strip()
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)

    return ordered


def merge_keywords_with_source_hints(keywords: list[str], source_text: str) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()

    for keyword in keywords + extract_plan_cycle_keywords(source_text):
        if keyword and keyword not in seen:
            seen.add(keyword)
            merged.append(keyword)

    return merged


def validate_and_extract_keywords(data: dict) -> list[str]:
    """
    严格模式：从指定5个字段强制提取关键词，确保100%来自规则允许的来源

    允许的关键词来源（按优先级）：
    1. 场景用途 - 从"元数据.场景用途"提取，拆分"-"和"、"连接的项目
    2. 政策标题 - 直接使用"元数据.政策标题"
    3. 行业领域 - 从"元数据.行业领域"提取，排除"通用行业"/"通用行业领域"
    4. 扶持类型 - 从"支持方式.扶持类型"提取，拆分"、"分隔的项目
    5. 最高扶持金额 - 从"支持方式.最高扶持金额"提取，仅当有具体数值时

    严格禁止：
    - 数字、年份、文号（如"二十五""2024年""第1号"）
    - 常见动词（如"认定""申报""办理""申请""审批"）
    - 概括性词语（如"政策""措施""办法""规定"）
    - 政策类型词（如"实施细则""指导政策""通知公告"）
    - 行业通用词（如"科技""创新""产业"，除非出现在指定字段）
    """
    keywords: list[str] = []
    seen: set[str] = set()

    # 禁止词列表（绝对不能作为关键词）
    FORBIDDEN_PATTERNS = [
        r'^\d+$',  # 纯数字
        r'^\d{4}年$',  # 年份如"2024年"
        r'^\d+\.\d+$',  # 小数如"3.14"
        r'^\[\d+\]$',  # 序号如"[1]"
        r'第[一二三四五六七八九十百零〇]+[号条项]',  # 文号如"第二十五条"
    ]
    FORBIDDEN_WORDS = {
        "认定", "申报", "办理", "申请", "审批", "政策", "措施", "办法", "规定",
        "实施细则", "指导政策", "通知公告", "公告", "通知", "科技", "创新", "产业",
        "企业", "单位", "个人", "自然人", "法人", "二十五", "三十五", "十五",
    }

    def is_forbidden(word: str) -> bool:
        """检查词是否在禁止列表中"""
        if not word:
            return True
        # 检查是否全为数字
        if word.isdigit():
            return True
        # 检查禁止词
        if word in FORBIDDEN_WORDS:
            return True
        # 检查正则模式
        for pattern in FORBIDDEN_PATTERNS:
            if re.match(pattern, word):
                return True
        return False

    def add_keyword(word: str) -> None:
        """添加关键词，去重并过滤禁止词"""
        if not word or is_forbidden(word):
            return
        word = word.strip()
        if word and word not in seen:
            seen.add(word)
            keywords.append(word)

    # 1. 从场景用途提取（支持"资质认定-高企"和"资质认定、高企"两种格式）
    scene_usage = data.get("元数据", {}).get("场景用途", "")
    if scene_usage:
        # 先按"、"拆分（多选情况）
        scene_parts = scene_usage.replace("；", "、").split("、")
        for part in scene_parts:
            part = part.strip()
            if not part:
                continue
            # 按"-"拆分（两级格式如"资质认定-高企"）
            if "-" in part:
                sub_parts = part.split("-")
                for sub in sub_parts:
                    add_keyword(sub.strip())
            else:
                add_keyword(part)

    # 2. 政策标题 - 直接添加
    policy_title = data.get("元数据", {}).get("政策标题", "")
    if policy_title:
        add_keyword(policy_title.strip())

    # 3. 行业领域 - 排除"通用行业"/"通用行业领域"
    industry = data.get("元数据", {}).get("行业领域", "")
    if industry:
        # 按"、"拆分多选
        industry_parts = industry.replace("；", "、").split("、")
        for part in industry_parts:
            part = part.strip()
            # 排除通用行业
            if part and part not in ("通用行业", "通用行业领域"):
                add_keyword(part)

    # 4. 扶持类型 - 拆分"、"分隔的每一项
    support_type = data.get("支持方式", {}).get("扶持类型", "")
    if support_type:
        support_parts = support_type.replace("；", "、").split("、")
        for part in support_parts:
            add_keyword(part.strip())

    # 5. 最高扶持金额 - 仅当有具体数值时添加
    max_amount = data.get("支持方式", {}).get("最高扶持金额", "")
    if max_amount and isinstance(max_amount, str):
        max_amount = max_amount.strip()
        # 检查是否有具体的金额数字（如"100万元"、"200万元"）
        if re.search(r'\d+\s*万元', max_amount):
            add_keyword(max_amount)

    return keywords


def set_db_keywords(keywords: list[str]) -> None:
    """设置数据库关键词库（从 policy_keyword_library 表加载）"""
    global _db_keyword_cache, _db_keyword_loaded
    _db_keyword_cache = set(keywords)
    _db_keyword_loaded = True
    print(f"  [关键词库] 已加载 {len(_db_keyword_cache)} 个关键词")


def validate_and_extract_keywords(data: dict) -> list[str]:
    """
    严格模式：从指定5个字段提取关键词，然后从数据库关键词库中匹配

    流程：
    1. 从5个指定字段提取候选关键词
    2. 只保留在数据库关键词库中存在的关键词（精确匹配或包含匹配）
    3. 如果数据库关键词库未加载，则使用原有的严格模式（不限制来源）

    允许的关键词来源（按优先级）：
    1. 场景用途 - 从"元数据.场景用途"提取，拆分"-"和"、"连接的项目
    2. 政策标题 - 直接使用"元数据.政策标题"
    3. 行业领域 - 从"元数据.行业领域"提取，排除"通用行业"/"通用行业领域"
    4. 扶持类型 - 从"支持方式.扶持类型"提取，拆分"、"分隔的每一项
    5. 最高扶持金额 - 从"支持方式.最高扶持金额"提取，仅当有具体数值时

    严格禁止：
    - 数字、年份、文号（如"二十五""2024年""第1号"）
    - 常见动词（如"认定""申报""办理""申请""审批"）
    - 概括性词语（如"政策""措施""办法""规定"）
    - 政策类型词（如"实施细则""指导政策""通知公告"）
    - 行业通用词（如"科技""创新""产业"，除非出现在指定字段）
    """
    global _db_keyword_cache, _db_keyword_loaded

    candidate_keywords: list[str] = []
    seen: set[str] = set()

    # 禁止词列表（绝对不能作为关键词）
    FORBIDDEN_PATTERNS = [
        r'^\d+$',  # 纯数字
        r'^\d{4}年$',  # 年份如"2024年"
        r'^\d+\.\d+$',  # 小数如"3.14"
        r'^\[\d+\]$',  # 序号如"[1]"
        r'第[一二三四五六七八九十百零〇]+[号条项]',  # 文号如"第二十五条"
    ]
    FORBIDDEN_WORDS = {
        "认定", "申报", "办理", "申请", "审批", "政策", "措施", "办法", "规定",
        "实施细则", "指导政策", "通知公告", "公告", "通知", "科技", "创新", "产业",
        "企业", "单位", "个人", "自然人", "法人", "二十五", "三十五", "十五",
    }

    def is_forbidden(word: str) -> bool:
        """检查词是否在禁止列表中"""
        if not word:
            return True
        if word.isdigit():
            return True
        if word in FORBIDDEN_WORDS:
            return True
        for pattern in FORBIDDEN_PATTERNS:
            if re.match(pattern, word):
                return True
        return False

    def add_candidate(word: str) -> None:
        """添加候选关键词，去重并过滤禁止词"""
        if not word or is_forbidden(word):
            return
        word = word.strip()
        if word and word not in seen:
            seen.add(word)
            candidate_keywords.append(word)

    # 1. 从场景用途提取
    scene_usage = data.get("元数据", {}).get("场景用途", "")
    if scene_usage:
        scene_parts = scene_usage.replace("；", "、").split("、")
        for part in scene_parts:
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                sub_parts = part.split("-")
                for sub in sub_parts:
                    add_candidate(sub.strip())
            else:
                add_candidate(part)

    # 2. 行业领域 - 排除"通用行业"/"通用行业领域"
    industry = data.get("元数据", {}).get("行业领域", "")
    if industry:
        industry_parts = industry.replace("；", "、").split("、")
        for part in industry_parts:
            part = part.strip()
            if part and part not in ("通用行业", "通用行业领域"):
                add_candidate(part)

    # 3. 扶持类型 - 拆分"、"分隔的每一项
    support_type = data.get("支持方式", {}).get("扶持类型", "")
    if support_type:
        support_parts = support_type.replace("；", "、").split("、")
        for part in support_parts:
            add_candidate(part.strip())

    # 注意：不提取政策标题和最高扶持金额作为关键词

    # 如果数据库关键词库已加载，只保留在数据库中存在的关键词（严格精确匹配）
    if _db_keyword_loaded and _db_keyword_cache:
        matched_keywords = []
        for kw in candidate_keywords:
            # 严格精确匹配：只有完全相等才保留
            if kw in _db_keyword_cache:
                matched_keywords.append(kw)
            # 如果没有精确匹配，不添加该关键词
        return matched_keywords

    # 如果数据库关键词库未加载，返回候选关键词（兼容模式）
    return candidate_keywords


def validate_and_update_keywords(data: dict) -> dict:
    """
    使用严格模式验证并更新关键词字段
    """
    validated_keywords = validate_and_extract_keywords(data)
    data["元数据"]["政策关键词"] = validated_keywords
    return data


def ensure_output_schema(data: dict, source_name: str, source_text: str = "") -> dict:
    template = build_empty_result(source_name)

    if not isinstance(data, dict):
        return template

    for key, value in template.items():
        data.setdefault(key, value)

    if not isinstance(data.get("元数据"), dict):
        data["元数据"] = template["元数据"].copy()
    else:
        # 白名单过滤：只保留模板中定义的字段，移除 LLM 多余生成的字段（如重复的"附件"）
        allowed_keys = set(template["元数据"].keys())
        extra_keys = [k for k in data["元数据"] if k not in allowed_keys]
        for k in extra_keys:
            del data["元数据"][k]
        for key, value in template["元数据"].items():
            data["元数据"].setdefault(key, value)

    if not isinstance(data.get("申报条件"), dict):
        data["申报条件"] = template["申报条件"].copy()
    else:
        for key, value in template["申报条件"].items():
            data["申报条件"].setdefault(key, value)

    # 确保申报条件聚类字段存在
    if not isinstance(data.get("申报条件聚类"), dict):
        data["申报条件聚类"] = copy.deepcopy(template["申报条件聚类"])

    if not isinstance(data.get("申报材料及方式"), dict):
        data["申报材料及方式"] = template["申报材料及方式"].copy()
    else:
        for key, value in template["申报材料及方式"].items():
            data["申报材料及方式"].setdefault(key, value)

    data["文件名"] = source_name

    # 兼容处理：如果顶层或申报条件有"附件"字段，移动到"申报材料及方式.附件"
    attachment_value = None
    if "附件" in data:
        attachment_value = data.pop("附件")
    if not attachment_value and "申报条件" in data:
        attachment_value = data["申报条件"].pop("附件", None)
    if attachment_value and "申报材料及方式" in data:
        data["申报材料及方式"]["附件"] = attachment_value

    if not isinstance(data.get("原文地址"), str):
        data["原文地址"] = ""

    # 关键词已在上方的 validate_and_update_keywords 中处理（严格模式：100%来自指定字段）
    # 不再使用旧的 sanitize_array_by_source 和 merge_keywords_with_source_hints 处理关键词

    data["元数据"]["指导政策"] = sanitize_array_by_source(
        data["元数据"].get("指导政策", []),
        source_text,
    )
    data["元数据"]["实施细则"] = sanitize_array_by_source(
        data["元数据"].get("实施细则", []),
        source_text,
    )
    data["元数据"]["通知公告"] = sanitize_array_by_source(
        data["元数据"].get("通知公告", []),
        source_text,
    )

    # 有效日期：只有当政策有效期有值时才设置，否则留空
    # (已在 convert_keywords.py 中将政策有效期同步到有效日期)

    # 确保支持方式是对象结构
    if not isinstance(data.get("支持方式"), dict):
        data["支持方式"] = {
            "扶持类型": "",
            "扶持内容描述": "",
            "扶持金额": "",
            "最高扶持金额": "",
        }
    else:
        data["支持方式"].setdefault("扶持类型", "")
        data["支持方式"].setdefault("扶持内容描述", "")
        data["支持方式"].setdefault("扶持金额", "")
        data["支持方式"].setdefault("最高扶持金额", "")

    # 使用严格模式验证并更新关键词（确保100%来自指定字段）
    data = validate_and_update_keywords(data)

    return data


def process_one_file(
    file_path: Path | str,
    settings: MetadataSettings,
    client: MetadataLLMClient,
) -> dict:
    source_path = Path(file_path)
    source_name = source_path.name

    # 加载行业分类数据（使用类变量缓存）
    if not hasattr(process_one_file, "_industry_data"):
        json_path = Path(__file__).parent / "json2" / "merged_output.json"
        process_one_file._industry_data = load_industry_keywords(json_path)

    try:
        text = read_file_content(source_path)
        text = clean_text(text)

        if len(text) > settings.max_text_length:
            text = text[: settings.max_text_length]

        if not text:
            return build_empty_result(source_name)

        # 读取 attachments 文件夹中的附件文件名列表
        attachment_list = get_attachment_list(source_path)

        # 行业领域预匹配
        matched = match_industry_categories(text, process_one_file._industry_data)
        industry_context = build_industry_context(matched)

        # 将匹配上下文插入到政策文本前面
        full_text = industry_context + "\n\n" + text if industry_context else text

        data = client.extract(full_text, source_name, str(source_path), attachment_list)
        return ensure_output_schema(data, source_name, text)

    except Exception as exc:
        print(f"处理异常，返回空模板: {source_name} -> {exc}")
        return build_empty_result(source_name)


def process_one_policy(
    policy_name: str,
    policy_dir: Path,
    settings: MetadataSettings,
    client: MetadataLLMClient,
) -> dict:
    """
    处理一个政策目录，该目录包含 实施细则、通知公告、指导政策 等子目录

    策略：分离有效期提取和主内容提取
    - 第一步：从指导政策文件夹中专门提取政策有效期
    - 第二步：从实施细则目录提取主内容（其他字段），结合提前提取的有效期
    """
    # 子目录名称映射
    subdir_map = {
        "实施细则": "implement",
        "通知公告": "notice",
        "指导政策": "guide",
    }

    # 收集各目录下的文件内容
    contents = {k: [] for k in subdir_map.values()}

    for subdir_name, key in subdir_map.items():
        subdir_path = policy_dir / subdir_name
        if subdir_path.is_dir():
            for md_file in subdir_path.glob("*.md"):
                try:
                    text = read_file_content(md_file)
                    text = clean_text(text)
                    if text:
                        contents[key].append(text)
                except Exception as e:
                    print(f"  读取文件失败: {md_file} -> {e}")

    # 主内容：只用实施细则
    main_text = "\n\n".join(contents["implement"])

    if not main_text:
        return build_empty_result(policy_name)

    # ========== 第一步：从指导政策文件夹中专门提取政策有效期 ==========
    guide_text = "\n\n".join(contents["guide"])
    pre_extracted_validity = ""  # 提前提取的政策有效期

    if guide_text:
        # 只取指导政策内容的末尾部分（有效期信息通常在末尾）
        # 保留开头和末尾，但更多保留末尾
        max_guide_length = settings.max_text_length * 2  # 24000 chars
        if len(guide_text) > max_guide_length:
            keep_start = max_guide_length // 4
            keep_end = max_guide_length - keep_start
            guide_for_validity = guide_text[:keep_start] + "\n\n[...]" + guide_text[-keep_end:]
        else:
            guide_for_validity = guide_text

        # 调用专门的提示词提取有效期
        try:
            pre_extracted_validity = client.extract_validity(guide_for_validity, policy_name)
            print(f"  [有效期提取] {policy_name} -> {pre_extracted_validity or '未找到'}")
        except Exception as e:
            print(f"  [有效期提取失败] {policy_name} -> {e}")
            pre_extracted_validity = ""

    # ========== 第二步：收集通知公告和指导政策内容用于申报量提取 ==========
    # 申报量通常出现在通知公告的结果公告中
    amount_text = "\n\n".join(contents["notice"] + contents["guide"])
    if amount_text and len(amount_text) > settings.max_text_length:
        amount_text = amount_text[:settings.max_text_length]

    # ========== 第三步：提取主内容（其他字段） ==========

    # 截断处理（增大长度限制，确保包含完整的支持方式等信息）
    max_main_text = settings.max_text_length * 2  # 24000 chars
    if len(main_text) > max_main_text:
        # 保留开头和末尾（支持方式等信息可能在文件末尾）
        keep_start = max_main_text // 3
        keep_end = max_main_text - keep_start
        main_text = main_text[:keep_start] + "\n\n[...]" + main_text[-keep_end:]

    # 行业领域预匹配（基于主内容）
    if not hasattr(process_one_policy, "_industry_data"):
        json_path = Path(__file__).parent / "json2" / "merged_output.json"
        process_one_policy._industry_data = load_industry_keywords(json_path)

    matched = match_industry_categories(main_text, process_one_policy._industry_data)
    industry_context = build_industry_context(matched)
    full_text = industry_context + "\n\n" + main_text if industry_context else main_text

    # 获取附件列表（从实施细则目录）
    attachment_list = "无"
    implement_dir = policy_dir / "实施细则"
    if implement_dir.is_dir():
        attachments = []
        for att_dir_name in ["附件", "attachments", "attachment", "附"]:
            att_dir = implement_dir / att_dir_name
            if att_dir.is_dir():
                for f in att_dir.iterdir():
                    if f.is_file() and not f.name.startswith("~$"):
                        attachments.append(f.name)
        if attachments:
            attachment_list = "、".join(attachments)

    # 收集指导政策目录下的文件名（去掉.md后缀），用于填充指导政策字段
    guiding_policy_names = []
    guide_dir = policy_dir / "指导政策"
    if guide_dir.is_dir():
        for md_file in guide_dir.glob("*.md"):
            guiding_policy_names.append(md_file.stem)  # 去掉.md后缀

    # 收集实施细则目录下的文件名（去掉.md后缀），用于填充关联文件字段
    implement_policy_names = []
    if implement_dir.is_dir():
        for md_file in implement_dir.glob("*.md"):
            implement_policy_names.append(md_file.stem)  # 去掉.md后缀

    source_path = str(policy_dir)

    # 调用主提取，传入提前提取的有效期和申报量补充内容
    data = client.extract(
        full_text, policy_name, source_path, attachment_list,
        pre_extracted_validity=pre_extracted_validity,
        policy_amount_text=amount_text
    )

    # 先调用 ensure_output_schema 处理 LLM 返回的数据
    data = ensure_output_schema(data, policy_name, main_text)

    # 政策标题直接使用文件名
    data["元数据"]["政策标题"] = policy_name

    # 摘要：根据政策标题生成简要定义和价值说明
    if not data.get("摘要") or data.get("摘要") == "":
        # 获取扶持类型作为补充信息
        support_type = data.get("支持方式", {}).get("扶持类型", "")
        support_desc = data.get("支持方式", {}).get("扶持内容描述", "")[:200] if data.get("支持方式", {}).get("扶持内容描述") else ""

        summary_parts = [f"{policy_name}是"]
        if support_type:
            summary_parts.append(f"针对{support_type}的政策")
        summary_parts.append("旨在支持企业创新发展")
        if support_desc:
            summary_parts.append(f"。{support_desc}")

        data["摘要"] = "".join(summary_parts)

    # 如果LLM没有提取到有效期，使用提前提取的有效期
    current_validity = data["元数据"].get("有效日期", "")
    if not current_validity and pre_extracted_validity:
        data["元数据"]["有效日期"] = pre_extracted_validity
        print(f"  [使用预提取有效期] {policy_name} -> {pre_extracted_validity}")

    # 将指导政策目录下的文件名添加到元数据的指导政策字段
    # （在 ensure_output_schema 之后添加，避免被 sanitize_array_by_source 过滤掉）
    if guiding_policy_names:
        existing_guiding = data["元数据"].get("指导政策", [])
        if not isinstance(existing_guiding, list):
            existing_guiding = []
        # 合并去重
        for name in guiding_policy_names:
            if name not in existing_guiding:
                existing_guiding.append(name)
        data["元数据"]["指导政策"] = existing_guiding

    # 将实施细则目录下的文件名添加到元数据的实施细则字段
    # （在 ensure_output_schema 之后添加，避免被 sanitize_array_by_source 过滤掉）
    if implement_policy_names:
        existing_related = data["元数据"].get("实施细则", [])
        if not isinstance(existing_related, list):
            existing_related = []
        # 合并去重
        for name in implement_policy_names:
            if name not in existing_related:
                existing_related.append(name)
        data["元数据"]["实施细则"] = existing_related

    return data


def save_json(data: dict, out_path: Path | str) -> None:
    target_path = Path(out_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ===================== 行业领域预匹配 =====================
def load_industry_keywords(json_path: Path) -> list[dict]:
    """加载 merged_output.json 文件"""
    if not json_path.exists():
        return []
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def match_industry_categories(policy_text: str, industry_data: list[dict]) -> list[str]:
    """根据政策文本匹配行业分类，返回匹配到的 path 列表"""
    if not industry_data or not policy_text:
        return []

    matched_paths = []
    policy_lower = policy_text.lower()

    for item in industry_data:
        path = item.get("path", "")
        keywords = item.get("keywords", [])

        if not path or not keywords:
            continue

        for kw in keywords:
            if not kw or not isinstance(kw, str):
                continue
            kw_clean = kw.strip().lower()
            if kw_clean and len(kw_clean) >= 2 and kw_clean in policy_lower:
                path_formatted = path.replace("/", "-")
                if path_formatted not in matched_paths:
                    matched_paths.append(path_formatted)
                break

    return matched_paths


def build_industry_context(matched: list[str]) -> str:
    """构建行业领域匹配的上下文提示"""
    if not matched:
        return ""

    context = "\n".join([f"- {cat}" for cat in matched])
    return f"""
【行业领域预匹配结果】
参考以下从 json2/merged_output.json 匹配到的行业分类，优先从这些分类中选择：
{context}
如果上述匹配结果不适用于政策内容，请根据正文重新判断。
"""
