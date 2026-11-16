import csv
import html
import mimetypes
import re
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

INPUT = Path("E:/demo/exports/f_column_confirmed_policy_pages_20260716.tsv")
POLICY_OUT = Path("E:/demo/exports/f_column_policy_import_20260716.tsv")
ATTACH_OUT = Path("E:/demo/exports/f_column_policy_attachment_import_20260716.tsv")
ROOT = Path("E:/demo/docs1")


def safe_name(value: str, max_len=70) -> str:
    value = re.sub(r'[<>:"/\\|?*\r\n\t]', "_", value).strip()
    value = re.sub(r"\s+", "", value)
    return value[:max_len] or "policy"


def fetch_bytes(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=18) as resp:
            data = resp.read()
            ctype = resp.headers.get_content_charset()
            content_type = resp.headers.get("Content-Type", "")
        return data, ctype, content_type
    except Exception:
        completed = subprocess.run(
            ["curl.exe", "-L", "--max-time", "30", "-A", "Mozilla/5.0", url],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
        content_type = mimetypes.types_map.get(suffix, "text/html")
        return completed.stdout, None, content_type


def decode_html(data: bytes, charset=None) -> str:
    if charset:
        try:
            return data.decode(charset, "ignore")
        except LookupError:
            pass
    head = data[:4096].decode("ascii", "ignore")
    match = re.search(r"charset=['\"]?([\w\-]+)", head, re.I)
    encodings = [match.group(1)] if match else []
    encodings.extend(["utf-8", "gb18030", "gbk"])
    for enc in encodings:
        try:
            return data.decode(enc, "ignore")
        except Exception:
            continue
    return data.decode("utf-8", "ignore")


def clean_text(fragment: str) -> str:
    fragment = re.sub(r"<!--.*?-->", " ", fragment, flags=re.S)
    fragment = re.sub(r"<script.*?</script>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<style.*?</style>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"</p>|<br\s*/?>|</div>|</h\d>", "\n", fragment, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text)
    text = re.sub(r"[ \\t\\r\\f\\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_title(text: str, fallback: str) -> str:
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    patterns = [
        r'<meta[^>]+name=["\']ArticleTitle["\'][^>]+content=["\']([^"\']+)',
        r'<div[^>]+class=["\'][^"\']*article_title[^"\']*["\'][^>]*>(.*?)</div>',
        r'<h4[^>]+id=["\']title["\'][^>]*>(.*?)</h4>',
        r"<title>(.*?)</title>",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            title = clean_text(match.group(1))
            if title and "File Not Found" not in title and "返回首页" not in title:
                return title
    return fallback


def extract_date(text: str):
    for pattern in [
        r'DOCRELTIME\s*=\s*["\'](\d{4}-\d{2}-\d{2})',
        r'<meta[^>]+name=["\']PubDate["\'][^>]+content=["\'](\d{4})[.\-](\d{1,2})[.\-](\d{1,2})',
        r"(\d{4})年(\d{1,2})月(\d{1,2})日",
        r"(\d{4}-\d{2}-\d{2})",
    ]:
        match = re.search(pattern, text)
        if not match:
            continue
        if len(match.groups()) == 3:
            return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        return match.group(1)
    return "2026-07-16"


def extract_code(text: str):
    match = re.search(r"([\u4e00-\u9fa5]{1,8}(?:科|府|委|工信|人社|规|财)[\u4e00-\u9fa5]*〔\d{4}〕\d+号)", clean_text(text))
    return match.group(1) if match else ""


def extract_attachments(base_url: str, text: str):
    out = []
    for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text, re.I | re.S):
        href = html.unescape(match.group(1))
        url = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlparse(url)
        suffix = Path(parsed.path).suffix.lower().lstrip(".")
        if suffix not in {"pdf", "doc", "docx", "xls", "xlsx", "zip", "rar", "wps"}:
            continue
        name = clean_text(match.group(2)) or Path(parsed.path).name
        out.append((url, name, suffix))
    return out


def main():
    policies = []
    attachments = []
    rows = list(csv.DictReader(INPUT.open("r", encoding="utf-8-sig"), delimiter="\t"))
    for row in rows:
        requested_title = row["MISSING_POLICY_TITLE"].strip()
        try:
            data, charset, content_type = fetch_bytes(row["URL"])
        except Exception as exc:
            print(f"SKIP fetch failed: {requested_title} {row['URL']} {exc}")
            continue
        if "html" not in content_type.lower() and not row["URL"].lower().endswith((".htm", ".html", "/5992", "/5125", "/6284")):
            continue
        text = decode_html(data, charset)
        page_text = clean_text(text)
        if len(page_text) < 200 or "File Not Found" in page_text or "返回首页" in page_text[:500]:
            print(f"SKIP invalid page: {requested_title} {row['URL']}")
            continue
        title = extract_title(text, requested_title)
        pub_date = extract_date(text)
        folder = ROOT / row["LEVEL"] / f"{pub_date}_{safe_name(requested_title)}"
        folder.mkdir(parents=True, exist_ok=True)
        md_name = f"{pub_date}_{safe_name(requested_title)}.md"
        md_path = folder / md_name
        rel_md = md_path.relative_to(Path("E:/demo")).as_posix()
        md = f"# {requested_title}\n\n原网页标题：{title}\n\n来源链接：{row['URL']}\n\n发布日期：{pub_date}\n\n发布部门：{row['ISSUE_DEPARTMENT']}\n\n---\n\n{page_text}\n"
        md_path.write_text(md, encoding="utf-8")
        policies.append({
            "REQUESTED_TITLE": requested_title,
            "PAGE_TITLE": title,
            "URL": row["URL"],
            "POLICY_CODE": extract_code(text),
            "PUBLISH_DATE": pub_date,
            "ISSUE_DEPARTMENT": row["ISSUE_DEPARTMENT"],
            "POLICY_ATTRIBUTE": row["POLICY_ATTRIBUTE"],
            "FILE_PATH": rel_md,
            "CONTENT_FILE": str(md_path),
        })
        attach_dir = folder / "附件"
        for attach_url, attach_name, suffix in extract_attachments(row["URL"], text):
            attach_dir.mkdir(exist_ok=True)
            file_name = safe_name(attach_name, 90)
            if not file_name.lower().endswith("." + suffix):
                file_name += "." + suffix
            target = attach_dir / file_name
            try:
                attach_data, _, _ = fetch_bytes(attach_url)
                target.write_bytes(attach_data)
            except Exception as exc:
                print(f"ATTACH_SKIP {attach_url} {exc}")
                continue
            attachments.append({
                "REQUESTED_TITLE": requested_title,
                "ATTACH_ID": str(uuid.uuid4()),
                "ATTACH_NAME": file_name,
                "ATTACH_SHOW_NAME": attach_name,
                "FILE_PATH": target.relative_to(Path("E:/demo")).as_posix(),
                "FILE_SUFFIX": suffix,
                "ATTACH_TYPE": row["POLICY_ATTRIBUTE"] + "类",
                "ATTACH_REVIEW_STATUS": "未通过",
            })
        print(f"CRAWLED {requested_title} -> {rel_md} attachments={sum(1 for a in attachments if a['REQUESTED_TITLE']==requested_title)}")
        time.sleep(0.2)
    with POLICY_OUT.open("w", encoding="utf-8", newline="") as f:
        fields = ["REQUESTED_TITLE", "PAGE_TITLE", "URL", "POLICY_CODE", "PUBLISH_DATE", "ISSUE_DEPARTMENT", "POLICY_ATTRIBUTE", "FILE_PATH", "CONTENT_FILE"]
        writer = csv.DictWriter(f, fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(policies)
    with ATTACH_OUT.open("w", encoding="utf-8", newline="") as f:
        fields = ["REQUESTED_TITLE", "ATTACH_ID", "ATTACH_NAME", "ATTACH_SHOW_NAME", "FILE_PATH", "FILE_SUFFIX", "ATTACH_TYPE", "ATTACH_REVIEW_STATUS"]
        writer = csv.DictWriter(f, fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(attachments)
    print(f"policy_rows={len(policies)} attachment_rows={len(attachments)}")


if __name__ == "__main__":
    main()
