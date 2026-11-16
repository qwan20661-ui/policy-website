from __future__ import annotations

import html
import json
import mimetypes
import os
import re
import shutil
import ssl
import subprocess
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


OUT_ROOT = Path(__file__).resolve().parent / "output" / "批量政策爬取_20260717_round3"
USER_AGENT = os.environ.get(
    "CRAWLER_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
)
FETCH_TIMEOUT = int(os.environ.get("CRAWLER_FETCH_TIMEOUT", "25"))
CONNECT_TIMEOUT = int(os.environ.get("CRAWLER_CONNECT_TIMEOUT", "8"))
FETCH_RETRIES = int(os.environ.get("CRAWLER_FETCH_RETRIES", "0"))
ATTACHMENT_FETCH_TIMEOUT = int(os.environ.get("CRAWLER_ATTACHMENT_FETCH_TIMEOUT", "30"))
XMSME_RESOLVE_IP = os.environ.get("XMSME_RESOLVE_IP", "112.48.134.4").strip()
ATTACHMENT_SUFFIXES = {"pdf", "doc", "docx", "xls", "xlsx", "zip", "rar", "wps"}


def safe_name(value: str, max_len: int = 90) -> str:
    value = re.sub(r'[<>:"/\\|?*\r\n\t]', "_", value or "").strip()
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value[:max_len] or "policy")


def public_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def fetch_bytes(url: str, timeout: int | None = None) -> tuple[bytes, str | None, str]:
    request_timeout = timeout or FETCH_TIMEOUT
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    curl_cmd = shutil.which("curl.exe") or shutil.which("curl")
    errors: list[str] = []
    if curl_cmd:
        parsed = urllib.parse.urlparse(url)
        curl_args = [
            curl_cmd,
            "-sS",
            "-L",
            "--compressed",
            "--connect-timeout",
            str(CONNECT_TIMEOUT),
            "--max-time",
            str(request_timeout),
            "--retry",
            str(FETCH_RETRIES),
            "--retry-delay",
            "2",
            "--retry-connrefused",
            "-A",
            USER_AGENT,
            "-H",
            "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "-H",
            "Accept-Language: zh-CN,zh;q=0.9",
            url,
        ]
        if parsed.hostname == "www.xmsme.cn" and XMSME_RESOLVE_IP:
            curl_args[1:1] = ["--resolve", f"www.xmsme.cn:443:{XMSME_RESOLVE_IP}"]
        if os.environ.get("CRAWLER_FORCE_IPV4", "1") != "0":
            curl_args.insert(1, "-4")
        try:
            completed = subprocess.run(
                curl_args,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=request_timeout + CONNECT_TIMEOUT + 10,
            )
            return completed.stdout, None, mimetypes.types_map.get(suffix, "text/html")
        except Exception as exc:
            stderr = getattr(exc, "stderr", b"") or b""
            errors.append(f"curl failed: {stderr.decode('utf-8', 'ignore').strip() or exc}")

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )
    context = ssl._create_unverified_context()
    for attempt in range(FETCH_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=request_timeout, context=context) as resp:
                data = resp.read()
                charset = resp.headers.get_content_charset()
                content_type = resp.headers.get("Content-Type", "")
                return data, charset, content_type
        except Exception as exc:
            errors.append(f"urllib attempt {attempt + 1} failed: {exc}")

    raise RuntimeError(f"下载 URL 超时或失败：{url}；" + "；".join(errors[-3:]))


def decode_html(data: bytes, charset: str | None = None) -> str:
    encodings: list[str] = []
    if charset:
        encodings.append(charset)
    head = data[:4096].decode("ascii", "ignore")
    match = re.search(r"charset=['\"]?([\w\-]+)", head, re.I)
    if match:
        encodings.append(match.group(1))
    encodings.extend(["utf-8", "gb18030", "gbk"])
    for encoding in encodings:
        try:
            return data.decode(encoding, "ignore")
        except Exception:
            continue
    return data.decode("utf-8", "ignore")


def download(url: str, raw_path: Path) -> None:
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    data, _, _ = fetch_bytes(url)
    raw_path.write_bytes(data)


def read_html(raw_path: Path) -> str:
    return decode_html(raw_path.read_bytes())


def clean_text(fragment: str) -> str:
    fragment = re.sub(r"<!--.*?-->", " ", fragment or "", flags=re.S)
    fragment = re.sub(r"<script.*?</script>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<style.*?</style>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"</p>|<br\s*/?>|</div>|</li>|</h\d>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<li[^>]*>", "\n- ", fragment, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def table_to_markdown(table_html: str) -> str:
    rows: list[list[str]] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.I | re.S):
        cells = [clean_text(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.I | re.S)]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    output = ["|" + "|".join(rows[0]) + "|", "|" + "|".join(["---"] * width) + "|"]
    output.extend("|" + "|".join(row) + "|" for row in rows[1:])
    return "\n".join(output)


def html_to_markdown(fragment: str) -> str:
    tables: list[str] = []

    def replace_table(match: re.Match[str]) -> str:
        md = table_to_markdown(match.group(0))
        if not md:
            return " "
        tables.append(md)
        return f"\n[TABLE_{len(tables)}]\n"

    text = re.sub(r"<table[\s\S]*?</table>", replace_table, fragment or "", flags=re.I)
    text = clean_text(text)
    for index, table in enumerate(tables, start=1):
        text = text.replace(f"[TABLE_{index}]", table)
    return text.strip()


def normalize_date(value: str) -> str:
    value = value or ""
    match = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", value)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    match = re.search(r"(\d{4}-\d{2}-\d{2})", value)
    return match.group(1) if match else ""


def extract_policy_id(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    for key in ("id", "policyId", "policy_id"):
        if query.get(key):
            return query[key][0]
    parts = [part for part in parsed.path.split("/") if part]
    return parts[-1] if parts else ""


def extract_title(page_html: str, fallback: str = "政策文件") -> str:
    patterns = [
        r'<meta[^>]+name=["\']ArticleTitle["\'][^>]+content=["\']([^"\']+)',
        r'<h1[^>]*>(.*?)</h1>',
        r'<div[^>]+class=["\'][^"\']*(?:article[-_ ]?title|title)[^"\']*["\'][^>]*>(.*?)</div>',
        r"<title>(.*?)</title>",
    ]
    for pattern in patterns:
        match = re.search(pattern, page_html, flags=re.I | re.S)
        if not match:
            continue
        title = clean_text(match.group(1))
        title = re.sub(r"[_|-].*$", "", title).strip()
        if 4 <= len(title) <= 180 and "File Not Found" not in title:
            return title
    return fallback


def extract_date(page_html: str) -> str:
    for pattern in [
        r'DOCRELTIME\s*=\s*["\']([^"\']+)',
        r'<meta[^>]+name=["\']PubDate["\'][^>]+content=["\']([^"\']+)',
        r"(?:发布时间|发布日期|发文日期|时间)[:：\s]*(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2})",
        r"(\d{4}年\d{1,2}月\d{1,2}日)",
        r"(\d{4}-\d{1,2}-\d{1,2})",
    ]:
        match = re.search(pattern, page_html, flags=re.I)
        if match:
            date = normalize_date(match.group(1))
            if date:
                return date
    return ""


def extract_code(page_html: str) -> str:
    text = clean_text(page_html)
    patterns = [
        r"[\u4e00-\u9fa5]{1,12}〔\d{4}〕\d+号",
        r"[\u4e00-\u9fa5]{1,12}\[\d{4}\]\d+号",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return ""


def extract_department(page_html: str) -> str:
    text = clean_text(page_html)
    patterns = [
        r"(?:发布机构|发文机关|发布部门|来源)[:：]\s*([^\n]{2,80})",
        r"(厦门市[\u4e00-\u9fa5]{2,30}(?:局|委|厅|办公室|人民政府))",
        r"(福建省[\u4e00-\u9fa5]{2,30}(?:厅|局|委|办公室|人民政府))",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def js_string_value(value: str) -> str:
    try:
        return json.loads('"' + value + '"')
    except Exception:
        value = re.sub(
            r"\\u([0-9a-fA-F]{4})",
            lambda match: chr(int(match.group(1), 16)),
            value,
        )
        return (
            value
            .replace(r"\/", "/")
            .replace(r"\"", '"')
            .replace(r"\n", "\n")
            .replace(r"\r", "\r")
            .replace(r"\t", "\t")
            .replace(r"\\", "\\")
        )


def find_js_field(fragment: str, key: str) -> str:
    match = re.search(rf"{re.escape(key)}:\"((?:\\.|[^\"\\])*)\"", fragment)
    return js_string_value(match.group(1)) if match else ""


def extract_nuxt_arg_values(page_html: str) -> dict[str, str]:
    match = re.search(r"window\.__NUXT__=\(function\(([^)]*)\)\{[\s\S]*?\}\(([\s\S]*?)\)\);</script>", page_html)
    if not match:
        return {}
    names = [item.strip() for item in match.group(1).split(",")]
    values = []
    current = []
    in_string = False
    escaped = False
    for char in match.group(2):
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            continue
        if char == '"':
            current.append(char)
            in_string = not in_string
            continue
        if char == "," and not in_string:
            values.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current:
        values.append("".join(current).strip())

    resolved: dict[str, str] = {}
    for name, value in zip(names, values):
        if value.startswith('"') and value.endswith('"'):
            resolved[name] = js_string_value(value[1:-1])
        elif value == "null":
            resolved[name] = ""
        elif value == "false":
            resolved[name] = "false"
        elif value == "true":
            resolved[name] = "true"
        else:
            resolved[name] = value
    return resolved


def find_js_field_or_var(fragment: str, key: str, variables: dict[str, str]) -> str:
    value = find_js_field(fragment, key)
    if value:
        return value
    match = re.search(rf"{re.escape(key)}:([A-Za-z_$][\w$]*)", fragment)
    if match:
        return variables.get(match.group(1), "")
    return ""


def xmsme_resource_url(resource_id: str) -> str:
    return f"https://www.xmsme.cn/api/gxdp/common/download/resource/{urllib.parse.quote(resource_id)}"


def extract_xmsme_appendixes(fragment: str) -> list[dict[str, str]]:
    match = re.search(r"appendixes:\[([\s\S]*?)\],videoFiles", fragment)
    if not match:
        return []
    attachments: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in re.finditer(r"\{([\s\S]*?)\}", match.group(1)):
        item_fragment = item.group(1)
        attach_id = find_js_field(item_fragment, "id")
        name = find_js_field(item_fragment, "name")
        if not attach_id or attach_id in seen:
            continue
        seen.add(attach_id)
        attachments.append({
            "id": attach_id,
            "name": name or attach_id,
            "url": xmsme_resource_url(attach_id),
            "suffix": Path(name or "").suffix.lower().lstrip("."),
        })
    return attachments


def extract_xmsme_nuxt(page_html: str, source_url: str) -> dict[str, Any] | None:
    marker = "fileDetail:{"
    start = page_html.find(marker)
    if start < 0:
        return None
    fragment = page_html[start:start + 80000]
    variables = extract_nuxt_arg_values(page_html)
    title = find_js_field(fragment, "name")
    content = find_js_field(fragment, "content")
    if not title and not content:
        return None

    issue_fragment_match = re.search(r"issueOrgs:\[([\s\S]*?)\],implementOrgs", fragment)
    issue_fragment = issue_fragment_match.group(1) if issue_fragment_match else ""
    departments = [js_string_value(item) for item in re.findall(r"name:\"((?:\\.|[^\"\\])*)\"", issue_fragment)]
    departments.extend(
        variables.get(item, "")
        for item in re.findall(r"name:([A-Za-z_$][\w$]*)", issue_fragment)
        if variables.get(item)
    )

    publish_date = normalize_date(find_js_field(fragment, "writtenTime") or find_js_field(fragment, "issuedTime"))
    if not publish_date:
        publish_date = extract_date(page_html)
    attachments = extract_xmsme_appendixes(fragment)
    attachments.extend(extract_generic_attachments(source_url, content or page_html))

    return {
        "title": title or extract_title(page_html),
        "sourceUrl": source_url,
        "sourcePolicyId": extract_policy_id(source_url),
        "publishDate": publish_date,
        "policyCode": find_js_field(fragment, "number"),
        "department": "、".join(dict.fromkeys(departments)),
        "applicableScope": find_js_field_or_var(fragment, "level", variables),
        "policyAttribute": find_js_field_or_var(fragment, "type", variables) or "通知公告",
        "content": html_to_markdown(content or page_html),
        "attachments": attachments,
    }


def extract_generic_attachments(base_url: str, page_html: str) -> list[dict[str, str]]:
    attachments: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', page_html, re.I | re.S):
        href = html.unescape(match.group(1))
        full_url = urllib.parse.urljoin(base_url, href)
        suffix = Path(urllib.parse.urlparse(full_url).path).suffix.lower().lstrip(".")
        if suffix not in ATTACHMENT_SUFFIXES or full_url in seen:
            continue
        seen.add(full_url)
        name = clean_text(match.group(2)) or Path(urllib.parse.urlparse(full_url).path).name
        attachments.append({"name": name, "url": full_url, "suffix": suffix})
    return attachments


def xmsme_api_url(source_url: str) -> str:
    policy_id = extract_policy_id(source_url)
    return f"https://www.xmsme.cn/api/gxdp/policies/approved/{urllib.parse.quote(policy_id)}"


def extract_xmsme(page_html: str, source_url: str) -> dict[str, Any]:
    nuxt_result = extract_xmsme_nuxt(page_html, source_url)
    if nuxt_result:
        return nuxt_result
    try:
        api_data, _, _ = fetch_bytes(xmsme_api_url(source_url))
        payload = json.loads(api_data.decode("utf-8", "ignore"))
        data = payload.get("data") or payload
        title = data.get("name") or data.get("title") or extract_title(page_html)
        department = "、".join(item.get("name", "") for item in data.get("depts", []) if isinstance(item, dict) and item.get("name"))
        sections = [
            ("申报条件", data.get("declarationCondition", "")),
            ("支持内容", data.get("supportContent", "")),
            ("申报材料", data.get("declarationMaterial", "")),
            ("申报流程", data.get("declarationProcess", "")),
            ("咨询电话", data.get("consultationTel", "")),
        ]
        body_parts = []
        for heading, content in sections:
            md = html_to_markdown(content)
            if md:
                body_parts.append(f"## {heading}\n\n{md}")
        attachments = []
        for item in (data.get("attachments", []) or []) + (data.get("appendixes", []) or []):
            if not isinstance(item, dict):
                continue
            attach_id = str(item.get("id") or item.get("resourceId") or item.get("fileId") or "")
            if not attach_id:
                continue
            name = str(item.get("name") or attach_id)
            attachments.append({
                "id": attach_id,
                "name": name,
                "url": xmsme_resource_url(attach_id),
                "suffix": Path(name).suffix.lower().lstrip("."),
            })
        return {
            "title": title,
            "sourceUrl": source_url,
            "sourcePolicyId": extract_policy_id(source_url),
            "publishDate": normalize_date(str(data.get("approvedTime") or data.get("publishTime") or data.get("startTime") or "")),
            "policyCode": str(data.get("policyCode") or data.get("documentNo") or data.get("code") or ""),
            "department": department or str(data.get("deptName") or data.get("department") or ""),
            "applicableScope": str(data.get("level") or data.get("applicableScope") or data.get("scope") or ""),
            "policyAttribute": str(data.get("policyAttribute") or data.get("typeName") or "申报通知"),
            "content": "\n\n".join(body_parts).strip() or html_to_markdown(page_html),
            "attachments": attachments,
        }
    except Exception:
        return nuxt_result or extract_gov_page(page_html, source_url)


def extract_gov_page(page_html: str, source_url: str) -> dict[str, Any]:
    title = extract_title(page_html)
    main_fragment = page_html
    for pattern in [
        r'<div[^>]+class=["\'][^"\']*(?:TRS_Editor|pages_content|article|content|page_con)[^"\']*["\'][^>]*>([\s\S]*?)</div>',
        r'<article[^>]*>([\s\S]*?)</article>',
    ]:
        match = re.search(pattern, page_html, flags=re.I)
        if match:
            main_fragment = match.group(1)
            break
    return {
        "title": title,
        "sourceUrl": source_url,
        "sourcePolicyId": extract_policy_id(source_url),
        "publishDate": extract_date(page_html),
        "policyCode": extract_code(page_html),
        "department": extract_department(page_html),
        "applicableScope": "",
        "policyAttribute": "通知公告",
        "content": html_to_markdown(main_fragment),
        "attachments": extract_generic_attachments(source_url, page_html),
    }


def download_attachment(item: dict[str, Any], attachment_dir: Path) -> dict[str, Any]:
    url = str(item.get("url") or "")
    show_name = str(item.get("name") or Path(urllib.parse.urlparse(url).path).name or "附件")
    suffix = str(item.get("suffix") or Path(show_name).suffix.lower().lstrip(".") or "bin")
    file_name = safe_name(show_name, 120)
    if suffix and not file_name.lower().endswith("." + suffix.lower()):
        file_name += "." + suffix
    target = attachment_dir / file_name
    try:
        data, _, _ = fetch_bytes(url, timeout=ATTACHMENT_FETCH_TIMEOUT)
        target.write_bytes(data)
        return {
            "id": str(item.get("id") or uuid.uuid4()),
            "name": file_name,
            "showName": show_name,
            "suffix": suffix,
            "size": len(data),
            "filePath": public_path(target),
            "status": "downloaded",
        }
    except Exception as exc:
        return {
            "id": str(item.get("id") or uuid.uuid4()),
            "name": file_name,
            "showName": show_name,
            "suffix": suffix,
            "size": 0,
            "filePath": "",
            "status": "failed",
            "error": str(exc),
        }


def build_markdown(result: dict[str, Any], attachments: list[dict[str, Any]]) -> str:
    lines = [
        f"# {result.get('title') or '政策文件'}",
        "",
        "## 基本信息",
        "",
        f"- 来源URL：{result.get('sourceUrl') or ''}",
        f"- 发文部门：{result.get('department') or '-'}",
        f"- 适用范围：{result.get('applicableScope') or '-'}",
        f"- 发布日期：{result.get('publishDate') or '-'}",
        f"- 文号：{result.get('policyCode') or '-'}",
        "",
        "---",
        "",
        "# 政策正文",
        "",
        str(result.get("content") or ""),
        "",
        "---",
        "",
        "# 附件列表",
        "",
    ]
    if attachments:
        for item in attachments:
            if item.get("status") == "downloaded":
                lines.append(f"- [{item.get('showName') or item.get('name')}](attachments/{item.get('name')})")
            else:
                lines.append(f"- {item.get('showName') or item.get('name')}（下载失败）")
    else:
        lines.append("无")
    return "\n".join(lines).strip() + "\n"


def write_result(index: int, result: dict[str, Any], raw_path: Path) -> dict[str, Any]:
    title = str(result.get("title") or f"政策文件_{index}")
    safe_title = safe_name(title, 100)
    policy_dir = OUT_ROOT / safe_title
    attachment_dir = policy_dir / "attachments"
    policy_dir.mkdir(parents=True, exist_ok=True)
    attachment_dir.mkdir(parents=True, exist_ok=True)

    attachments = [download_attachment(item, attachment_dir) for item in result.get("attachments", [])]
    md_path = policy_dir / "原文.md"
    markdown = build_markdown(result, attachments)
    md_path.write_text(markdown, encoding="utf-8")

    manifest = {
        "index": index,
        "title": title,
        "safeTitle": safe_title,
        "sourceUrl": result.get("sourceUrl") or "",
        "sourcePolicyId": result.get("sourcePolicyId") or "",
        "policyCode": result.get("policyCode") or "",
        "publishDate": result.get("publishDate") or "",
        "department": result.get("department") or "",
        "applicableScope": result.get("applicableScope") or "",
        "policyAttribute": result.get("policyAttribute") or "",
        "markdown": markdown,
        "markdownPath": public_path(md_path),
        "policyDir": public_path(policy_dir),
        "attachmentDir": public_path(attachment_dir),
        "attachments": attachments,
    }
    return manifest
