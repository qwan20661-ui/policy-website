from __future__ import annotations

import csv
import re
import uuid
from datetime import datetime
from pathlib import Path


ROOT = Path(r"E:\demo\docs1\补充")
EXPORT_DIR = Path(r"E:\demo\exports")
POLICY_TSV = EXPORT_DIR / "supplement_policy_import_20260717.tsv"
ATTACH_TSV = EXPORT_DIR / "supplement_policy_attachment_import_20260717.tsv"
TITLES_TXT = EXPORT_DIR / "supplement_policy_titles_20260717.txt"
EMPTY_CONTENT = EXPORT_DIR / "empty_policy_content_20260717.txt"


def maybe_fix_mojibake(text: str) -> str:
    """Repair UTF-8 text that was decoded as GBK/CP936, when possible."""
    sample_markers = ("鍘", "绂", "鏀", "鏈", "闂", "甯", "銆", "锛")
    if not any(marker in text for marker in sample_markers):
        return text
    try:
        fixed = text.encode("cp936", errors="strict").decode("utf-8", errors="strict")
    except UnicodeError:
        return text
    return fixed if count_cjk(fixed) > count_cjk(text) else text


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
        pattern = re.compile(rf"^\s*[-*]?\s*{re.escape(label)}\s*[:：]\s*(.+?)\s*$", re.M)
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return ""


def extract_date(text: str, fallback_file: Path) -> str:
    value = extract_value(text, ("发布时间", "发布日期", "发布日间", "发布日期间"))
    match = re.search(r"(20\d{2})[-年./](\d{1,2})[-月./](\d{1,2})", value or text)
    if match:
        y, m, d = match.groups()
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    return datetime.fromtimestamp(fallback_file.stat().st_mtime).strftime("%Y-%m-%d")


def extract_policy_code(text: str) -> str:
    value = extract_value(text, ("政策文号", "文号"))
    if value and value not in ("无", "无。", "暂无"):
        return value.strip()
    match = re.search(r"[\u4e00-\u9fff]{1,8}(?:府|科|工信|人社|财|政办|科规|府办|规)[^，。\n]{0,20}[〔\[]20\d{2}[〕\]][^，。\n]{0,20}号", text)
    return match.group(0).strip() if match else ""


def extract_department(text: str, title: str) -> str:
    value = extract_value(text, ("发文部门", "发布机构", "来源网站", "来源"))
    if value:
        return value.strip()
    if title.startswith("福建省"):
        return title.split("关于", 1)[0].strip()
    if title.startswith("厦门市"):
        return title.split("关于", 1)[0].strip()
    if title.startswith("中共厦门市委"):
        return title.split("关于", 1)[0].strip()
    return ""


def classify_attribute(title: str, department: str) -> str:
    joined = title + department
    if "国家" in joined or "工业和信息化部" in joined or "财政部" in joined:
        return "国家级"
    if "福建省" in joined:
        return "省级"
    if "厦门" in joined:
        return "市级"
    return "补充"


def rel_path(path: Path) -> str:
    try:
        return path.relative_to(Path(r"E:\demo")).as_posix()
    except ValueError:
        return str(path).replace("\\", "/")


def main() -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    EMPTY_CONTENT.write_text("", encoding="utf-8")

    policy_rows: list[dict[str, str]] = []
    attach_rows: list[dict[str, str]] = []

    for policy_dir in sorted([p for p in ROOT.iterdir() if p.is_dir()], key=lambda p: p.name):
        md_files = sorted(policy_dir.glob("*.md"))
        if not md_files:
            continue
        md_file = md_files[0]
        title = policy_dir.name.strip()
        text = read_text(md_file)
        page_title = ""
        first_heading = re.search(r"^\s*#\s+(.+?)\s*$", text, re.M)
        if first_heading:
            page_title = first_heading.group(1).strip()
        url = extract_value(text, ("原文链接", "链接", "政策链接"))
        if url and not re.match(r"^https?://", url):
            url = ""
        department = extract_department(text, title)
        publish_date = extract_date(text, md_file)
        code = extract_policy_code(text)
        attr = classify_attribute(title, department)

        policy_rows.append(
            {
                "REQUESTED_TITLE": title,
                "PAGE_TITLE": page_title or title,
                "URL": url,
                "POLICY_CODE": code,
                "PUBLISH_DATE": publish_date,
                "ISSUE_DEPARTMENT": department,
                "POLICY_ATTRIBUTE": attr,
                "FILE_PATH": rel_path(md_file),
                "CONTENT_FILE": str(EMPTY_CONTENT),
            }
        )

        for attachment in sorted(policy_dir.rglob("*")):
            if not attachment.is_file() or attachment.suffix.lower() == ".md":
                continue
            suffix = attachment.suffix.lower().lstrip(".")
            attach_rows.append(
                {
                    "REQUESTED_TITLE": title,
                    "ATTACH_ID": str(uuid.uuid4()),
                    "ATTACH_NAME": attachment.name,
                    "ATTACH_SHOW_NAME": attachment.name,
                    "FILE_PATH": rel_path(attachment),
                    "FILE_SUFFIX": suffix,
                    "ATTACH_TYPE": suffix,
                    "ATTACH_REVIEW_STATUS": "未通过",
                }
            )

    with POLICY_TSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(policy_rows[0].keys()), delimiter="\t")
        writer.writeheader()
        writer.writerows(policy_rows)

    with ATTACH_TSV.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "REQUESTED_TITLE",
            "ATTACH_ID",
            "ATTACH_NAME",
            "ATTACH_SHOW_NAME",
            "FILE_PATH",
            "FILE_SUFFIX",
            "ATTACH_TYPE",
            "ATTACH_REVIEW_STATUS",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(attach_rows)

    TITLES_TXT.write_text("\n".join(row["REQUESTED_TITLE"] for row in policy_rows) + "\n", encoding="utf-8")
    print(f"policies={len(policy_rows)} attachments={len(attach_rows)}")
    print(POLICY_TSV)
    print(ATTACH_TSV)
    print(TITLES_TXT)


if __name__ == "__main__":
    main()
