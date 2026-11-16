from __future__ import annotations

import csv
import argparse
import html
import json
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import uuid
from pathlib import Path


ROOT = Path(r"E:\demo")
DEFAULT_SRC = ROOT / "exports" / "missing_gov_attachment_sources_utf8.tsv"
DEFAULT_OUT_TSV = ROOT / "exports" / "gov_attachment_import_20260716.tsv"
DEFAULT_OUT_JSON = ROOT / "exports" / "gov_attachment_crawl_status_20260716.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
FILE_EXTS = (".pdf", ".doc", ".docx", ".wps", ".xls", ".xlsx", ".zip", ".rar", ".7z")

STATUS_DOWNLOADED = "\u5df2\u4e0b\u8f7d"
STATUS_REJECT = "\u62d2\u7edd\u4fee\u6539"
ATTACH_REVIEW = "\u672a\u901a\u8fc7"
ATTACH_FOLDER = "\u9644\u4ef6"


def curl_bytes(url: str, timeout: int = 60) -> bytes:
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl.exe not found")
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
    try:
        cmd = [
            curl,
            "-L",
            "-k",
            "-sS",
            "-A",
            USER_AGENT,
            "--connect-timeout",
            "15",
            "--max-time",
            str(timeout),
            url,
            "-o",
            tmp_path,
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or "").strip() or f"curl failed: {completed.returncode}")
        return Path(tmp_path).read_bytes()
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass


def decode_html(data: bytes) -> str:
    head = data[:4096].decode("ascii", errors="ignore").lower()
    match = re.search(r"charset=[\"']?([a-z0-9_\-]+)", head)
    encodings: list[str] = []
    if match:
        encodings.append(match.group(1))
    encodings.extend(["utf-8", "gb18030"])
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def js_unescape(value: str) -> str:
    try:
        return json.loads('"' + value.replace('"', '\\"') + '"')
    except Exception:
        try:
            return value.encode("utf-8").decode("unicode_escape")
        except Exception:
            return value


def is_gov_url(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).netloc.lower().split(":", 1)[0]
    except Exception:
        return False
    return host.endswith(".gov.cn")


def extract_xmsme_gov_url(page_text: str) -> str:
    candidates: list[str] = []
    for pattern in [
        r'website:"((?:\\.|[^"\\])*)"',
        r'website\s*:\s*"((?:\\.|[^"\\])*)"',
    ]:
        for match in re.finditer(pattern, page_text):
            candidates.append(js_unescape(match.group(1)))
    for match in re.finditer(r"https?://[^\s\"<>]+\.gov\.cn/[^\s\"<>]+?\.htm", page_text):
        candidates.append(html.unescape(match.group(0)))
    for candidate in candidates:
        if is_gov_url(candidate):
            return candidate
    return ""


def parse_static_links(page_text: str, base_url: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for href, label in re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', page_text, re.I | re.S):
        clean_href = html.unescape(href).strip()
        parsed_path = urllib.parse.unquote(urllib.parse.urlparse(clean_href).path)
        if not parsed_path.lower().endswith(FILE_EXTS):
            continue
        text = re.sub(r"<[^>]+>", "", label)
        text = html.unescape(re.sub(r"\s+", " ", text)).strip()
        fallback = Path(parsed_path).name
        name = text or fallback
        if Path(name).suffix == "" and Path(fallback).suffix:
            name += Path(fallback).suffix
        links.append({"name": name, "url": urllib.parse.urljoin(base_url, clean_href)})

    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in links:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        unique.append(item)
    return unique


def sanitize_filename(name: str, fallback_stem: str, url: str) -> str:
    cleaned = html.unescape(name or "").strip()
    cleaned = urllib.parse.unquote(cleaned)
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    ext = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).suffix
    if Path(cleaned).suffix == "" and ext:
        cleaned += ext
    if not cleaned or len(cleaned.replace("_", "").replace("-", "").strip()) < 2:
        cleaned = fallback_stem + (ext or ".bin")
    if len(cleaned) > 180:
        cleaned = Path(cleaned).stem[:160].rstrip(" ._") + Path(cleaned).suffix[:20]
    return cleaned


def looks_like_html(data: bytes) -> bool:
    prefix = data[:512].lstrip().lower()
    return prefix.startswith(b"<html") or prefix.startswith(b"<!doctype") or prefix.startswith(b"<script") or b"<title>" in prefix


def bad_download_reason(data: bytes) -> str:
    if len(data) < 16:
        return "downloaded file is empty"
    if looks_like_html(data):
        text = decode_html(data[:4096])
        if "\u9a8c\u8bc1\u7801" in text or "captcha" in text.lower():
            return "\u9644\u4ef6\u94fe\u63a5\u8fd4\u56de\u9a8c\u8bc1\u7801\u9875\uff0c\u672a\u5165\u5e93"
        return "\u9644\u4ef6\u94fe\u63a5\u8fd4\u56deHTML\u9875\uff0c\u672a\u5165\u5e93"
    return ""


def attachment_type(policy_attribute: str) -> str:
    if "\u7ed3\u679c\u516c\u544a" in (policy_attribute or ""):
        return "\u7ed3\u679c\u516c\u544a\u7c7b"
    if "\u901a\u77e5\u516c\u544a" in (policy_attribute or ""):
        return "\u901a\u77e5\u516c\u544a\u7c7b"
    return "\u7533\u62a5\u6750\u6599\u7c7b"


def attachment_dir_for(file_path: str) -> Path:
    parent = (ROOT / file_path).parent
    normalized = file_path.replace("\\", "/")
    if normalized.startswith("importantplus/"):
        return parent / "attachments"
    return parent / ATTACH_FOLDER


def read_sources(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def write_import_rows(path: Path, rows: list[dict[str, str]]) -> None:
    columns = [
        "ATTACH_ID",
        "POLICY_ID",
        "ATTACH_NAME",
        "ATTACH_SHOW_NAME",
        "FILE_PATH",
        "FILE_SUFFIX",
        "ATTACH_TYPE",
        "ATTACH_REVIEW_STATUS",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--out-tsv", type=Path, default=DEFAULT_OUT_TSV)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    args = parser.parse_args()

    statuses: list[dict[str, object]] = []
    import_rows: list[dict[str, str]] = []
    for row in read_sources(args.src):
        policy_id = str(row["POLICY_ID"]).strip()
        status: dict[str, object] = {
            "policy_id": policy_id,
            "title": row["POLICY_TITLE"],
            "status": "",
            "reason": "",
            "original_url": row["POLICY_URL"],
            "source_url": "",
            "files": [],
        }
        try:
            page_url = row["POLICY_URL"].strip()
            page_data = curl_bytes(page_url)
            page_text = decode_html(page_data)
            if "xmsme.cn/policy/market/" in page_url:
                gov_url = extract_xmsme_gov_url(page_text)
                if not gov_url:
                    status["status"] = STATUS_REJECT
                    status["reason"] = "\u6167\u4f01\u4e91\u9875\u672a\u627e\u5230\u653f\u5e9c\u7f51\u539f\u6587\u94fe\u63a5"
                    statuses.append(status)
                    continue
                page_url = gov_url
                page_data = curl_bytes(page_url)
                page_text = decode_html(page_data)
            status["source_url"] = page_url
            if not is_gov_url(page_url):
                status["status"] = STATUS_REJECT
                status["reason"] = "\u539f\u6587\u4e0d\u662f\u653f\u5e9c\u7ad9\u70b9\uff0c\u6309\u8981\u6c42\u672a\u6293\u53d6"
                statuses.append(status)
                continue

            links = parse_static_links(page_text, page_url)
            if not links:
                status["status"] = STATUS_REJECT
                status["reason"] = "\u653f\u5e9c\u539f\u7f51\u9875\u672a\u53d1\u73b0\u53ef\u4e0b\u8f7d\u9644\u4ef6\u94fe\u63a5"
                statuses.append(status)
                continue

            target_dir = attachment_dir_for(row["FILE_PATH"])
            target_dir.mkdir(parents=True, exist_ok=True)
            files: list[str] = []
            bad_reasons: list[str] = []
            used_names: set[str] = set()
            for index, link in enumerate(links, 1):
                filename = sanitize_filename(link["name"], f"{policy_id}_{index}", link["url"])
                base = Path(filename).stem
                suffix = Path(filename).suffix
                candidate = filename
                counter = 2
                while candidate.lower() in used_names:
                    candidate = f"{base}_{counter}{suffix}"
                    counter += 1
                used_names.add(candidate.lower())

                data = curl_bytes(link["url"], timeout=90)
                reason = bad_download_reason(data)
                if reason:
                    bad_reasons.append(f"{candidate}: {reason}")
                    continue

                target = target_dir / candidate
                if not target.exists() or target.stat().st_size != len(data):
                    target.write_bytes(data)
                rel_path = target.relative_to(ROOT).as_posix()
                files.append(rel_path)
                import_rows.append(
                    {
                        "ATTACH_ID": str(uuid.uuid4()),
                        "POLICY_ID": policy_id,
                        "ATTACH_NAME": target.name,
                        "ATTACH_SHOW_NAME": target.name,
                        "FILE_PATH": rel_path,
                        "FILE_SUFFIX": target.suffix.lower().lstrip("."),
                        "ATTACH_TYPE": attachment_type(row.get("POLICY_ATTRIBUTE", "")),
                        "ATTACH_REVIEW_STATUS": ATTACH_REVIEW,
                    }
                )

            status["files"] = files
            if files:
                status["status"] = STATUS_DOWNLOADED
                status["reason"] = f"\u4ece\u653f\u5e9c\u7f51\u7ad9\u4e0b\u8f7d\u9644\u4ef6 {len(files)} \u4e2a"
                if bad_reasons:
                    status["bad_links"] = bad_reasons
                    status["reason"] += f"\uff1b{len(bad_reasons)} \u4e2a\u94fe\u63a5\u672a\u4e0b\u8f7d"
            else:
                status["status"] = STATUS_REJECT
                status["reason"] = "; ".join(bad_reasons) if bad_reasons else "\u672a\u6210\u529f\u4e0b\u8f7d\u4efb\u4f55\u771f\u5b9e\u9644\u4ef6"
        except Exception as exc:
            status["status"] = STATUS_REJECT
            status["reason"] = str(exc)
        statuses.append(status)

    write_import_rows(args.out_tsv, import_rows)
    args.out_json.write_text(json.dumps(statuses, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"source_rows={len(statuses)}")
    print(f"downloaded_policies={sum(1 for item in statuses if item['status'] == STATUS_DOWNLOADED)}")
    print(f"rejected_policies={sum(1 for item in statuses if item['status'] == STATUS_REJECT)}")
    print(f"import_rows={len(import_rows)}")
    print(f"tsv={args.out_tsv}")
    print(f"status_json={args.out_json}")
    for item in statuses:
        print(item["policy_id"], item["status"], len(item.get("files") or []), item["reason"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
