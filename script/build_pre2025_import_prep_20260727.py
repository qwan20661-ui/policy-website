from __future__ import annotations

import csv
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
ROOT = WORKSPACE_ROOT / "docs1" / "2025.1前"
EXPORT_DIR = WORKSPACE_ROOT / "exports" / "pre2025_import_prep_20260727"
POLICY_TSV = EXPORT_DIR / "pre2025_policy_import_draft.tsv"
ATTACH_TSV = EXPORT_DIR / "pre2025_attachment_import_draft.tsv"
REPORT_CSV = EXPORT_DIR / "pre2025_policy_inventory_report.csv"
SUMMARY_JSON = EXPORT_DIR / "pre2025_summary.json"
TITLES_TXT = EXPORT_DIR / "pre2025_titles.txt"
EMPTY_CONTENT = EXPORT_DIR / "empty_policy_content.txt"
LOCAL_API_BASE = "http://127.0.0.1:8080"


def maybe_fix_mojibake(text: str) -> str:
    sample_markers = ("閸", "缁", "闂", "鐢", "鍙", "鍏", "鏀", "绗")
    if not any(marker in text for marker in sample_markers):
        return text
    try:
        fixed = text.encode("cp936", errors="strict").decode("utf-8", errors="strict")
    except UnicodeError:
        return text
    return fixed if count_cjk(fixed) >= count_cjk(text) else text


def count_cjk(text: str) -> int:
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


def read_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return maybe_fix_mojibake(path.read_text(encoding=encoding))
        except UnicodeDecodeError:
            continue
    return maybe_fix_mojibake(path.read_text(encoding="utf-8", errors="replace"))


def extract_value(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        pattern = re.compile(rf"^\s*[-*]?\s*\*{{0,2}}{re.escape(label)}\*{{0,2}}\s*[:：]\s*(.+?)\s*$", re.M)
        match = pattern.search(text)
        if match:
            return clean_text(match.group(1))
    return ""


def clean_text(value: str) -> str:
    value = maybe_fix_mojibake(value or "")
    value = re.sub(r"\s+", " ", value.replace("\u00a0", " ").replace("\u2002", " ").replace("\u3000", " "))
    return value.strip()


def clean_folder_title(name: str) -> str:
    name = re.sub(r"_(document\d+|[0-9a-f]{16,})$", "", name, flags=re.I)
    return clean_text(name)


def normalize_title_for_match(title: str) -> str:
    title = clean_text(title)
    title = re.sub(r"[“”\"'‘’《》<>（）()【】\[\]—\-_:：,，。！？、\s]", "", title)
    return title.lower()


def extract_heading_title(text: str) -> str:
    match = re.search(r"^\s*#\s+(.+?)\s*$", text, re.M)
    return clean_text(match.group(1)) if match else ""


def extract_date(text: str, fallback_file: Path) -> str:
    value = extract_value(text, ("发布日期", "发布时间", "发布日期间", "发布时间间", "发文日期"))
    match = re.search(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})", value or text)
    if match:
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    return datetime.fromtimestamp(fallback_file.stat().st_mtime).strftime("%Y-%m-%d")


def extract_policy_code(text: str) -> str:
    value = extract_value(text, ("政策文号", "文号"))
    if value and value not in {"-", "--", "无", "暂无"}:
        return value
    match = re.search(r"([^\s，。,；;]{1,30}[〔\[]20\d{2}[〕\]][^\s，。,；;]{0,20}号)", text)
    return clean_text(match.group(1)) if match else ""


def extract_department(text: str, title: str) -> str:
    value = extract_value(text, ("发文部门", "发布机构", "来源网站", "来源"))
    if value:
        return value
    if "关于" in title:
        prefix = title.split("关于", 1)[0].strip()
        if 1 <= len(prefix) <= 40:
            return prefix
    return ""


def extract_file_type(text: str) -> str:
    return extract_value(text, ("文件类型",))


def suggest_policy_attribute(title: str, file_type: str, department: str, text: str) -> tuple[str, str, str]:
    joined = f"{title} {file_type} {department}"
    body_head = clean_text(text[:1000])

    result_keywords = ("公示", "名单", "结果", "批复", "拟", "兑现", "通过名单", "入选")
    notice_keywords = ("通知", "通告", "公告", "征集", "征求", "申报", "推荐", "组织开展", "受理")
    guidance_keywords = ("解读", "若干措施", "意见", "规划", "方案")
    detailed_keywords = ("办法", "细则", "规程", "工作指引", "实施细则", "管理规定")

    if "解读" in joined:
        return "指导政策", "high", "标题包含“解读”"
    if any(word in joined for word in result_keywords) and "申报" not in joined:
        return "结果公告", "medium", "标题/文件类型更像公示或结果发布"
    if "申报指南" in joined:
        return "申报通知", "medium", "标题包含“申报指南”"
    if any(word in joined for word in notice_keywords):
        return "申报通知", "medium", "标题/文件类型包含通知公告类关键词"
    if any(word in joined for word in guidance_keywords):
        return "指导政策", "medium", "标题包含政策导向类关键词"
    if any(word in joined for word in detailed_keywords):
        return "实施细则", "medium", "标题包含办法细则类关键词"
    if "根据" in body_head and "通知如下" in body_head:
        return "申报通知", "low", "正文开头接近通知体"
    return "实施细则", "low", "未命中强规则，默认归入实施细则待人工复核"


def suggest_attachment_type(name: str, suffix: str) -> tuple[str, str]:
    joined = clean_text(name)
    suffix = suffix.lower()
    if any(word in joined for word in ("解读", "政策解读")):
        return "指导政策类", "名称包含解读"
    if any(word in joined for word in ("办法", "细则", "规程", "指引", "实施方案")):
        return "实施细则类", "名称包含办法细则类关键词"
    if any(word in joined for word in ("申请表", "申报书", "汇总表", "承诺书", "模板", "材料", "清单", "附件表")):
        return "申报材料类", "名称更像申报表单或材料"
    if any(word in joined for word in ("公示", "名单", "结果", "批复")):
        return "结果公告类", "名称更像结果公示"
    if any(word in joined for word in ("通知", "公告", "通告", "指南")):
        return "申报通知类", "名称更像通知公告"
    if suffix in {"doc", "docx", "xls", "xlsx", "pdf"}:
        return "申报材料类", "常见可填写/附件文档，先按申报材料归类"
    return "其它", "未命中规则"


def rel_path(path: Path) -> str:
    return path.relative_to(WORKSPACE_ROOT).as_posix()


def fetch_existing_policy_index() -> tuple[dict[str, list[dict[str, str]]], dict[str, list[dict[str, str]]], dict[str, object]]:
    by_title: dict[str, list[dict[str, str]]] = defaultdict(list)
    by_normalized_title: dict[str, list[dict[str, str]]] = defaultdict(list)
    meta: dict[str, object] = {"available": False, "source_total": 0, "error": "", "pagesFetched": 0}

    page = 1
    size = 1000
    total = None
    while True:
        query = urllib.parse.urlencode(
            {
                "page": page,
                "size": size,
                "includeAttachments": "false",
            }
        )
        url = f"{LOCAL_API_BASE}/api/classification/policies?{query}"
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            meta["error"] = str(exc)
            return by_title, by_normalized_title, meta

        items = payload.get("data") or []
        pagination = payload.get("pagination") or {}
        if total is None:
            total = int(pagination.get("total") or len(items))
            meta["source_total"] = total

        meta["available"] = True
        meta["pagesFetched"] = page
        for item in items:
            title = clean_text(str(item.get("title") or ""))
            if not title:
                continue
            record = {
                "policyId": str(item.get("policyId") or ""),
                "title": title,
                "publishDate": str(item.get("publishDate") or ""),
                "department": str(item.get("department") or item.get("issuingDepartment") or ""),
                "filePath": str(item.get("filePath") or ""),
            }
            by_title[title].append(record)
            by_normalized_title[normalize_title_for_match(title)].append(record)

        fetched = page * size
        if not items or (total is not None and fetched >= total):
            break
        page += 1
    return by_title, by_normalized_title, meta


def summarize_risks(row: dict[str, str]) -> str:
    risks: list[str] = []
    if row["duplicate_folder_title_count"] != "1":
        risks.append("目录标题重复")
    if row["db_exact_match_count"] != "0":
        risks.append("数据库已存在同名政策")
    if row["db_normalized_match_count"] != "0" and row["db_exact_match_count"] == "0":
        risks.append("数据库存在近似标题")
    if row["attachment_count"] == "0":
        risks.append("无附件")
    if row["publish_date_source"] != "metadata":
        risks.append("发布日期不是直接从元数据提取")
    if row["department"] == "":
        risks.append("发文部门缺失")
    if row["policy_attribute_confidence"] == "low":
        risks.append("政策属性需人工复核")
    return "；".join(risks)


def main() -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    EMPTY_CONTENT.write_text("", encoding="utf-8")

    db_by_title, db_by_normalized_title, db_meta = fetch_existing_policy_index()

    policy_dirs = sorted([path for path in ROOT.iterdir() if path.is_dir()], key=lambda item: item.name)
    folder_title_counter = Counter(clean_folder_title(path.name) for path in policy_dirs)

    policy_rows: list[dict[str, str]] = []
    attach_rows: list[dict[str, str]] = []
    report_rows: list[dict[str, str]] = []
    skipped_missing_md: list[str] = []

    for policy_dir in policy_dirs:
        md_files = sorted(policy_dir.glob("*.md"))
        if not md_files:
            skipped_missing_md.append(rel_path(policy_dir))
            continue

        md_file = md_files[0]
        text = read_text(md_file)
        heading_title = extract_heading_title(text)
        folder_title = clean_folder_title(policy_dir.name)
        title = heading_title or folder_title
        file_type = extract_file_type(text)
        publish_date = extract_date(text, md_file)
        publish_date_source = "metadata" if extract_value(text, ("发布日期", "发布时间", "发布日期间", "发布时间间", "发文日期")) else "fallback_mtime"
        department = extract_department(text, title)
        policy_code = extract_policy_code(text)
        original_url = extract_value(text, ("原文链接", "链接", "政策链接"))
        if original_url and not re.match(r"^https?://", original_url):
            original_url = ""

        suggested_attribute, confidence, attribute_reason = suggest_policy_attribute(title, file_type, department, text)
        exact_db_matches = db_by_title.get(title, [])
        normalized_db_matches = db_by_normalized_title.get(normalize_title_for_match(title), [])

        policy_rows.append(
            {
                "REQUESTED_TITLE": title,
                "PAGE_TITLE": heading_title or title,
                "URL": original_url,
                "POLICY_CODE": policy_code,
                "PUBLISH_DATE": publish_date,
                "ISSUE_DEPARTMENT": department,
                "POLICY_ATTRIBUTE": suggested_attribute,
                "FILE_PATH": rel_path(md_file),
                "CONTENT_FILE": str(md_file),
            }
        )

        attachments = []
        for attachment in sorted(policy_dir.rglob("*")):
            if not attachment.is_file() or attachment.suffix.lower() == ".md":
                continue
            attachments.append(attachment)
            suffix = attachment.suffix.lower().lstrip(".")
            suggested_attach_type, attach_reason = suggest_attachment_type(attachment.name, suffix)
            attach_rows.append(
                {
                    "REQUESTED_TITLE": title,
                    "ATTACH_ID": str(uuid.uuid4()),
                    "ATTACH_NAME": attachment.name,
                    "ATTACH_SHOW_NAME": attachment.name,
                    "FILE_PATH": rel_path(attachment),
                    "FILE_SUFFIX": suffix,
                    "ATTACH_TYPE": suggested_attach_type,
                    "ATTACH_REVIEW_STATUS": "未通过",
                    "ATTACH_TYPE_REASON": attach_reason,
                }
            )

        report_row = {
            "folder_name": policy_dir.name,
            "folder_path": rel_path(policy_dir),
            "title": title,
            "heading_title": heading_title,
            "folder_title": folder_title,
            "md_file": rel_path(md_file),
            "publish_date": publish_date,
            "publish_date_source": publish_date_source,
            "department": department,
            "policy_code": policy_code,
            "original_url": original_url,
            "file_type_meta": file_type,
            "suggested_policy_attribute": suggested_attribute,
            "policy_attribute_confidence": confidence,
            "policy_attribute_reason": attribute_reason,
            "attachment_count": str(len(attachments)),
            "duplicate_folder_title_count": str(folder_title_counter[folder_title]),
            "db_exact_match_count": str(len(exact_db_matches)),
            "db_normalized_match_count": str(len(normalized_db_matches)),
            "db_exact_match_policy_ids": "|".join(match["policyId"] for match in exact_db_matches[:10]),
            "db_exact_match_publish_dates": "|".join(match["publishDate"] for match in exact_db_matches[:10]),
            "db_exact_match_paths": "|".join(match["filePath"] for match in exact_db_matches[:5]),
            "db_normalized_match_titles": "|".join(match["title"] for match in normalized_db_matches[:5]),
        }
        report_row["risk_flags"] = summarize_risks(report_row)
        report_rows.append(report_row)

    report_rows.sort(key=lambda item: (item["db_exact_match_count"] != "0", item["duplicate_folder_title_count"] != "1", item["title"]))

    with POLICY_TSV.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "REQUESTED_TITLE",
            "PAGE_TITLE",
            "URL",
            "POLICY_CODE",
            "PUBLISH_DATE",
            "ISSUE_DEPARTMENT",
            "POLICY_ATTRIBUTE",
            "FILE_PATH",
            "CONTENT_FILE",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(policy_rows)

    with ATTACH_TSV.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "REQUESTED_TITLE",
            "ATTACH_ID",
            "ATTACH_NAME",
            "ATTACH_SHOW_NAME",
            "FILE_PATH",
            "FILE_SUFFIX",
            "ATTACH_TYPE",
            "ATTACH_REVIEW_STATUS",
            "ATTACH_TYPE_REASON",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(attach_rows)

    with REPORT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = list(report_rows[0].keys()) if report_rows else []
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(report_rows)

    TITLES_TXT.write_text("".join(f"{row['REQUESTED_TITLE']}\n" for row in policy_rows), encoding="utf-8")

    summary = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "root": str(ROOT),
        "policyDirCount": len(policy_dirs),
        "policyCount": len(policy_rows),
        "attachmentCount": len(attach_rows),
        "missingMdDirCount": len(skipped_missing_md),
        "missingMdDirsSample": skipped_missing_md[:20],
        "dbIndex": db_meta,
        "dbExactMatchCount": sum(1 for row in report_rows if row["db_exact_match_count"] != "0"),
        "dbNormalizedMatchCount": sum(1 for row in report_rows if row["db_normalized_match_count"] != "0"),
        "duplicateFolderTitlePolicyCount": sum(1 for row in report_rows if row["duplicate_folder_title_count"] != "1"),
        "policyAttributeStats": dict(Counter(row["suggested_policy_attribute"] for row in report_rows)),
        "attachmentTypeStats": dict(Counter(row["ATTACH_TYPE"] for row in attach_rows)),
        "riskStats": {
            "noAttachment": sum(1 for row in report_rows if row["attachment_count"] == "0"),
            "missingDepartment": sum(1 for row in report_rows if not row["department"]),
            "lowConfidenceAttribute": sum(1 for row in report_rows if row["policy_attribute_confidence"] == "low"),
        },
        "outputs": {
            "policyTsv": str(POLICY_TSV),
            "attachmentTsv": str(ATTACH_TSV),
            "reportCsv": str(REPORT_CSV),
            "titlesTxt": str(TITLES_TXT),
        },
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
