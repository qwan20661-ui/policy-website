import csv
import html
import json
import mimetypes
import re
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

import pandas as pd

EXCEL = Path("E:/policy/rule/局重点政策汇总_F列政策处理结果_20260716.xlsx")
SHEET = "2026.7.15政策归类"
ROOT = Path("E:/demo/docs1")
LINKS_OUT = Path("E:/demo/exports/i_column_links_20260716.tsv")
POLICY_OUT = Path("E:/demo/exports/i_column_policy_import_20260716.tsv")
ATTACH_OUT = Path("E:/demo/exports/i_column_policy_attachment_import_20260716.tsv")
STATUS_OUT = Path("E:/demo/exports/i_column_policy_processing_status_20260716.tsv")

GOV_HOST_MARKERS = (
    "gov.cn",
    "xm.gov.cn",
    "fujian.gov.cn",
    "chinatax.gov.cn",
    "miit.gov.cn",
    "siming.gov.cn",
    "jimei.gov.cn",
)


def safe_name(value: str, max_len=70) -> str:
    value = re.sub(r'[<>:"/\\|?*\r\n\t]', "_", value).strip()
    value = re.sub(r"\s+", "", value)
    return value[:max_len] or "policy"


def fetch_bytes(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
            ctype = resp.headers.get_content_charset()
            content_type = resp.headers.get("Content-Type", "")
        return data, ctype, content_type
    except Exception:
        completed = subprocess.run(
            ["curl.exe", "-L", "--max-time", "15", "-A", "Mozilla/5.0", url],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
        content_type = mimetypes.types_map.get(suffix, "text/html")
        return completed.stdout, None, content_type


def decode_html(data: bytes, charset=None) -> str:
    def text_score(decoded: str) -> int:
        cjk = len(re.findall(r"[\u4e00-\u9fff]", decoded))
        hangul = len(re.findall(r"[\uac00-\ud7af]", decoded))
        common = sum(decoded.count(ch) for ch in "的一是在有和人民政府通知关于厦门福建省市区工业信息科技管理办法措施")
        return common * 80 + cjk - hangul * 100

    if charset:
        try:
            decoded = data.decode(charset, "ignore")
            if text_score(decoded) > 500:
                return decoded
        except LookupError:
            pass
    head = data[:4096].decode("ascii", "ignore")
    match = re.search(r"charset=['\"]?([\w\-]+)", head, re.I)
    encodings = [match.group(1)] if match else []
    encodings.extend(["utf-8", "gb18030", "gbk", "gb2312"])
    candidates = []
    for enc in dict.fromkeys(encodings):
        try:
            decoded = data.decode(enc, "ignore")
            candidates.append((text_score(decoded), decoded))
        except Exception:
            pass
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    return data.decode("utf-8", "ignore")


def clean_text(fragment: str) -> str:
    fragment = re.sub(r"<!--.*?-->", " ", fragment, flags=re.S)
    fragment = re.sub(r"<script.*?</script>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<style.*?</style>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"</p>|<br\s*/?>|</div>|</h\d>|</li>", "\n", fragment, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text)
    text = re.sub(r"[\u200b\xa0]+", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_title(text: str, fallback: str) -> str:
    text_no_comments = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    patterns = [
        r'<meta[^>]+name=["\']ArticleTitle["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r'<div[^>]+class=["\'][^"\']*rules_tit[^"\']*["\'][^>]*>(.*?)</div>',
        r'<p[^>]+class=["\'][^"\']*xxgk_title[^"\']*["\'][^>]*>(.*?)</p>',
        r'<div[^>]+class=["\'][^"\']*article_title[^"\']*["\'][^>]*>(.*?)</div>',
        r'<h1[^>]*>(.*?)</h1>',
        r'<h4[^>]+id=["\']title["\'][^>]*>(.*?)</h4>',
        r"<title>(.*?)</title>",
    ]
    for pattern in patterns:
        match = re.search(pattern, text_no_comments, re.I | re.S)
        if match:
            title = clean_text(match.group(1))
            title = re.sub(r"[-_].*$", "", title).strip()
            if title and title not in {"File Not Found", "404错误页面不存在"} and "返回首页" not in title:
                return title
    return fallback


def extract_date(text: str):
    for pattern in [
        r'DOCRELTIME\s*=\s*["\'](\d{4}-\d{2}-\d{2})',
        r'"DOCOPENDATE"\s*:\s*"(\d{4}-\d{2}-\d{2})"',
        r'<meta[^>]+name=["\']PubDate["\'][^>]+content=["\'](\d{4})[.\-](\d{1,2})[.\-](\d{1,2})',
        r"(?:发布日期|发布时间|发布于|时间)[:：\s]*(\d{4})[.\-/年](\d{1,2})[.\-/月](\d{1,2})",
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
    match = re.search(r"([\u4e00-\u9fa5]{1,12}(?:科|府|委|工信|人社|规|财|税|政)[\u4e00-\u9fa5]*〔\d{4}〕\d+号)", clean_text(text))
    return match.group(1) if match else ""


def host_department(host: str) -> str:
    mapping = [
        ("chinatax.gov.cn", "国家税务总局"),
        ("miit.gov.cn", "工业和信息化部"),
        ("kjt.fujian.gov.cn", "福建省科学技术厅"),
        ("gxt.fujian.gov.cn", "福建省工业和信息化厅"),
        ("zfgb.fujian.gov.cn", "福建省人民政府"),
        ("gxj.xm.gov.cn", "厦门市工业和信息化局"),
        ("hrss.xm.gov.cn", "厦门市人力资源和社会保障局"),
        ("siming.gov.cn", "厦门市思明区人民政府"),
        ("jimei.gov.cn", "厦门市集美区人民政府"),
        ("xm.gov.cn", "厦门市人民政府"),
    ]
    for marker, department in mapping:
        if marker in host:
            return department
    return host


def level_for_host(host: str) -> str:
    if "miit.gov.cn" in host or "chinatax.gov.cn" in host:
        return "国家级"
    if "fujian.gov.cn" in host:
        return "省级"
    if "siming.gov.cn" in host:
        return "思明区"
    if "jimei.gov.cn" in host:
        return "集美区"
    return "市级"


def extract_attachments(base_url: str, text: str):
    out = []
    seen = set()
    for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text, re.I | re.S):
        href = html.unescape(match.group(1))
        url = urllib.parse.urljoin(base_url, href)
        suffix = Path(urllib.parse.urlparse(url).path).suffix.lower().lstrip(".")
        if suffix not in {"pdf", "doc", "docx", "xls", "xlsx", "zip", "rar", "wps"}:
            continue
        if url in seen:
            continue
        seen.add(url)
        name = clean_text(match.group(2)) or Path(urllib.parse.urlparse(url).path).name
        out.append((url, name, suffix))
    for match in re.finditer(r"desc\s*:\s*['\"]([^'\"]+)['\"][\s\S]{0,300}?url\s*:\s*['\"]([^'\"]+)['\"]", text, re.I):
        name = html.unescape(match.group(1)).strip()
        url = urllib.parse.urljoin(base_url, html.unescape(match.group(2)).strip())
        suffix = Path(urllib.parse.urlparse(url).path).suffix.lower().lstrip(".")
        if suffix in {"pdf", "doc", "docx", "xls", "xlsx", "zip", "rar", "wps"} and url not in seen:
            seen.add(url)
            out.append((url, name, suffix))
    return out


def parse_links(value: str):
    return re.findall(r"https?://[^\s，,；;\"<>]+", value or "")


def extract_excel_links():
    df = pd.read_excel(EXCEL, sheet_name=SHEET, header=0)
    rows = []
    seen = set()
    for idx, row in df.iterrows():
        cell = row.iloc[8] if len(row) > 8 else None
        if pd.isna(cell) or not str(cell).strip():
            continue
        for url in parse_links(str(cell)):
            if url in seen:
                continue
            seen.add(url)
            rows.append({
                "EXCEL_ROW": str(idx + 2),
                "MAIN_POLICY": "" if pd.isna(row.iloc[3]) else str(row.iloc[3]).strip(),
                "F_NOTE": "" if pd.isna(row.iloc[5]) else str(row.iloc[5]).replace("\r", " ").replace("\n", " ").strip(),
                "URL": url,
            })
    with LINKS_OUT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, ["EXCEL_ROW", "MAIN_POLICY", "F_NOTE", "URL"], delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    return rows


def is_probably_policy(title: str, body: str, url: str) -> tuple[bool, str]:
    host = urllib.parse.urlparse(url).netloc.lower()
    if not any(marker in host for marker in GOV_HOST_MARKERS):
        return False, "非政府/非官方来源链接，未导入"
    if len(body) < 300:
        return False, "正文过短或页面不可用"
    bad_words = ["404", "页面不存在", "File Not Found", "请输入验证码"]
    if any(word in body[:800] for word in bad_words):
        return False, "页面不存在或需要验证码"
    policy_words = ["通知", "办法", "规定", "措施", "实施细则", "方案", "法律", "管理办法", "申报", "公报"]
    if any(word in title for word in policy_words) or any(word in body[:1200] for word in policy_words):
        return True, ""
    return False, "未识别为政策原文"


def crawl(rows):
    policies = []
    attachments = []
    statuses = []
    for row in rows:
        url = row["URL"]
        host = urllib.parse.urlparse(url).netloc.lower()
        if not any(marker in host for marker in GOV_HOST_MARKERS):
            statuses.append({**row, "STATUS": "未导入", "TITLE": "", "REASON": "非政府/非官方来源链接，未导入"})
            print(f"SKIP non-gov row={row['EXCEL_ROW']} url={url}", flush=True)
            continue
        try:
            data, charset, content_type = fetch_bytes(url)
            if "html" not in content_type.lower() and not url.lower().endswith((".htm", ".html", "/10627", "/5992", "/5125", "/6284")):
                statuses.append({**row, "STATUS": "未导入", "TITLE": "", "REASON": f"非HTML政策页: {content_type}"})
                continue
            text = decode_html(data, charset)
            body = clean_text(text)
            title = extract_title(text, row["MAIN_POLICY"])
            ok, reason = is_probably_policy(title, body, url)
            if not ok:
                statuses.append({**row, "STATUS": "未导入", "TITLE": title, "REASON": reason})
                continue
            pub_date = extract_date(text)
            issue_department = host_department(host)
            level = level_for_host(host)
            folder = ROOT / level / f"{pub_date}_{safe_name(title)}"
            folder.mkdir(parents=True, exist_ok=True)
            md_path = folder / f"{pub_date}_{safe_name(title)}.md"
            rel_md = md_path.relative_to(Path("E:/demo")).as_posix()
            md = f"# {title}\n\n来源链接：{url}\n\n发布日期：{pub_date}\n\n发布部门：{issue_department}\n\nExcel行：{row['EXCEL_ROW']}\n\n---\n\n{body}\n"
            md_path.write_text(md, encoding="utf-8")
            policies.append({
                "REQUESTED_TITLE": title,
                "PAGE_TITLE": title,
                "URL": url,
                "POLICY_CODE": extract_code(text),
                "PUBLISH_DATE": pub_date,
                "ISSUE_DEPARTMENT": issue_department,
                "POLICY_ATTRIBUTE": "指导政策",
                "FILE_PATH": rel_md,
                "CONTENT_FILE": str(md_path),
            })
            attach_count = 0
            attach_dir = folder / "附件"
            for attach_url, attach_name, suffix in extract_attachments(url, text):
                attach_dir.mkdir(exist_ok=True)
                file_name = safe_name(attach_name, 90)
                if not file_name.lower().endswith("." + suffix):
                    file_name += "." + suffix
                target = attach_dir / file_name
                try:
                    attach_data, _, _ = fetch_bytes(attach_url)
                    target.write_bytes(attach_data)
                except Exception:
                    continue
                attachments.append({
                    "REQUESTED_TITLE": title,
                    "ATTACH_ID": str(uuid.uuid4()),
                    "ATTACH_NAME": file_name,
                    "ATTACH_SHOW_NAME": attach_name,
                    "FILE_PATH": target.relative_to(Path("E:/demo")).as_posix(),
                    "FILE_SUFFIX": suffix,
                    "ATTACH_TYPE": "指导政策类",
                    "ATTACH_REVIEW_STATUS": "未通过",
                })
                attach_count += 1
            statuses.append({**row, "STATUS": "已抓取", "TITLE": title, "REASON": f"附件{attach_count}个；文件={rel_md}"})
            print(f"CRAWLED row={row['EXCEL_ROW']} title={title} attachments={attach_count}", flush=True)
        except Exception as exc:
            statuses.append({**row, "STATUS": "未导入", "TITLE": "", "REASON": f"抓取失败: {exc}"})
            print(f"SKIP failed row={row['EXCEL_ROW']} url={url} {exc}", flush=True)
        time.sleep(0.2)
    return policies, attachments, statuses


def write_tsv(path: Path, rows, fields):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def main():
    rows = extract_excel_links()
    policies, attachments, statuses = crawl(rows)
    # Deduplicate by exact title + URL before import.
    dedup = {}
    for row in policies:
        dedup[(row["REQUESTED_TITLE"], row["URL"])] = row
    policies = list(dedup.values())
    write_tsv(POLICY_OUT, policies, ["REQUESTED_TITLE", "PAGE_TITLE", "URL", "POLICY_CODE", "PUBLISH_DATE", "ISSUE_DEPARTMENT", "POLICY_ATTRIBUTE", "FILE_PATH", "CONTENT_FILE"])
    write_tsv(ATTACH_OUT, attachments, ["REQUESTED_TITLE", "ATTACH_ID", "ATTACH_NAME", "ATTACH_SHOW_NAME", "FILE_PATH", "FILE_SUFFIX", "ATTACH_TYPE", "ATTACH_REVIEW_STATUS"])
    write_tsv(STATUS_OUT, statuses, ["EXCEL_ROW", "MAIN_POLICY", "F_NOTE", "URL", "STATUS", "TITLE", "REASON"])
    print(f"links={len(rows)} policies={len(policies)} attachments={len(attachments)} status={STATUS_OUT}")


if __name__ == "__main__":
    main()
