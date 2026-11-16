from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import openpyxl


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXCEL_DIR = Path("E:/policy/rule")
DEFAULT_API_BASE = "http://localhost:8080"
USER_AGENT = "Mozilla/5.0"
FILE_EXTS = (".pdf", ".doc", ".docx", ".wps", ".xls", ".xlsx", ".zip", ".rar", ".7z")
XMSME_DOWNLOAD = "https://www.xmsme.cn/api/gxdp/common/download/resource/{id}"
NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ET.register_namespace("", NS_MAIN)
ET.register_namespace("r", NS_REL)


def find_excel() -> Path:
    matches = [
        p for p in DEFAULT_EXCEL_DIR.glob("*重点政策汇总*.xlsx")
        if not p.name.startswith("~$")
    ]
    if not matches:
        raise FileNotFoundError(f"Cannot find workbook under {DEFAULT_EXCEL_DIR}")
    return matches[0]


def curl_bytes(url: str, timeout: int = 45) -> bytes:
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
            "--max-time",
            str(timeout),
            url,
            "-o",
            tmp_path,
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or f"curl failed: {completed.returncode}")
        return Path(tmp_path).read_bytes()
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass


def download_file(url: str, target: Path, timeout: int = 90) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    data = curl_bytes(url, timeout=timeout)
    if len(data) < 16:
        raise RuntimeError("downloaded file is empty")
    target.write_bytes(data)


def api_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def query_policy(api_base: str, keyword: str) -> list[dict]:
    params = urllib.parse.urlencode(
        {
            "page": 1,
            "size": 10,
            "includeAttachments": "true",
            "keyword": keyword,
        }
    )
    data = api_get_json(f"{api_base}/api/classification/policies?{params}")
    return data.get("data") or []


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "").replace("“", '"').replace("”", '"').replace("（", "(").replace("）", ")")


def score_policy(row: dict, policy: dict) -> int:
    title = normalize_text(row["title"])
    policy_title = normalize_text(policy.get("title", ""))
    score = 0
    if title == policy_title:
        score += 100
    elif title in policy_title or policy_title in title:
        score += 80
    elif normalize_text(row["title"][:20]) in policy_title:
        score += 50
    department = row.get("department", "")
    policy_department = policy.get("department") or policy.get("issuingDepartment") or ""
    if department and department in policy_department:
        score += 25
    file_path = policy.get("filePath") or ""
    if "厦门" in department and "docs1/市级/" in file_path.replace("\\", "/"):
        score += 15
    if int(policy.get("attachmentCount") or 0) == 0:
        score += 3
    return score


def match_policy(api_base: str, row: dict) -> dict | None:
    terms = [row["title"], row["title"][:30], row["title"][:20]]
    found: dict[str, dict] = {}
    for term in terms:
        if not term:
            continue
        for policy in query_policy(api_base, term):
            found[str(policy.get("id"))] = policy
    if not found:
        return None
    scored = sorted(((score_policy(row, p), p) for p in found.values()), key=lambda item: item[0], reverse=True)
    return scored[0][1] if scored and scored[0][0] > 0 else None


def find_header_col(ws, header: str) -> int:
    for col_index in range(1, ws.max_column + 1):
        if str(ws.cell(1, col_index).value or "").strip() == header:
            return col_index
    raise ValueError(f"未找到表头列：{header}")


def load_pending_rows(
    excel_path: Path,
    sheet_prefix: str = "2026.7.10",
    trigger_header: str = "核对",
    trigger_value: str = "待添加",
    trigger_column: int | None = None,
) -> list[dict]:
    wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
    ws = next(sheet for sheet in wb.worksheets if sheet.title.startswith(sheet_prefix))
    check_col = trigger_column or find_header_col(ws, trigger_header)
    rows = []
    for row_index in range(2, ws.max_row + 1):
        if str(ws.cell(row_index, check_col).value or "").strip() != trigger_value:
            continue
        rows.append(
            {
                "excel_row": row_index,
                "excel_policy_id": ws.cell(row_index, 1).value,
                "title": str(ws.cell(row_index, 2).value or "").strip(),
                "department": str(ws.cell(row_index, 3).value or "").strip(),
                "remark": str(ws.cell(row_index, 4).value or "").strip(),
            }
        )
    wb.close()
    return rows


def extract_source_url(policy: dict) -> str:
    file_path = policy.get("filePath") or ""
    if not file_path:
        return ""
    local_path = ROOT / file_path
    if not local_path.is_file():
        return ""
    text = local_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"\*\*原文链接\*\*[:：]\s*(\S+)", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"https?://[^\s)）]+", text)
    return match.group(0).strip() if match else ""


def decode_js_string(value: str) -> str:
    try:
        return json.loads('"' + value.replace('"', '\\"') + '"')
    except Exception:
        return value.encode("utf-8").decode("unicode_escape", errors="replace")


def parse_xmsme_appendixes(page_text: str) -> list[dict]:
    match = re.search(r"appendixes:\[(.*?)\],videoFiles", page_text, re.S)
    if not match:
        return []
    block = match.group(1)
    items = []
    for item_match in re.finditer(r'\{id:"([^"]+)",name:"((?:\\.|[^"])*)"\}', block):
        file_id = item_match.group(1)
        name = decode_js_string(item_match.group(2))
        items.append({"id": file_id, "name": name, "url": XMSME_DOWNLOAD.format(id=file_id)})
    return items


def parse_static_links(page_text: str, base_url: str) -> list[dict]:
    links = []
    for href, label in re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', page_text, re.I | re.S):
        clean_href = html.unescape(href).strip()
        if not clean_href.lower().split("?", 1)[0].endswith(FILE_EXTS):
            continue
        text = re.sub(r"<[^>]+>", "", label)
        name = html.unescape(re.sub(r"\s+", " ", text)).strip() or Path(urllib.parse.urlparse(clean_href).path).name
        links.append({"id": "", "name": name, "url": urllib.parse.urljoin(base_url, clean_href)})
    unique = []
    seen = set()
    for item in links:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        unique.append(item)
    return unique


def fetch_attachments(source_url: str) -> list[dict]:
    page = curl_bytes(source_url).decode("utf-8", errors="replace")
    if "xmsme.cn/policy/market/" in source_url:
        items = parse_xmsme_appendixes(page)
        if items:
            return items
    return parse_static_links(page, source_url)


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "", name).strip()
    return cleaned[:180] or f"attachment-{uuid.uuid4().hex[:8]}"


def attachment_dir_for(policy: dict) -> Path:
    file_path = policy.get("filePath") or ""
    policy_file = ROOT / file_path
    folder = policy_file.parent
    normalized = file_path.replace("\\", "/")
    return folder / ("attachments" if normalized.startswith("importantplus/") else "附件")


def suffix_for(name: str) -> str:
    suffix = Path(name).suffix.lower().lstrip(".")
    return suffix[:20]


def default_attach_type(policy: dict) -> str:
    attr = (policy.get("policyAttribute") or "").split("、", 1)[0].strip()
    allowed = {"实施细则", "指导政策", "申报材料", "申报通知", "结果公告", "通知公告"}
    if attr in allowed:
        return attr + "类"
    return ""


def process_rows(rows: list[dict], api_base: str) -> tuple[list[dict], list[dict]]:
    statuses = []
    import_rows = []
    for row in rows:
        status = {
            "excel_row": row["excel_row"],
            "excel_policy_id": row["excel_policy_id"],
            "title": row["title"],
            "status": "",
            "reason": "",
            "policy_id": "",
            "source_url": "",
            "files": [],
        }
        try:
            policy = match_policy(api_base, row)
            if not policy:
                status["status"] = "拒绝修改"
                status["reason"] = "数据库未匹配到政策"
                statuses.append(status)
                continue
            status["policy_id"] = str(policy.get("id") or "")
            source_url = extract_source_url(policy)
            status["source_url"] = source_url
            if not source_url:
                status["status"] = "拒绝修改"
                status["reason"] = "本地政策文件未记录原文链接"
                statuses.append(status)
                continue
            attachments = fetch_attachments(source_url)
            if not attachments:
                status["status"] = "拒绝修改"
                status["reason"] = "原网页未发现附件链接"
                statuses.append(status)
                continue

            target_dir = attachment_dir_for(policy)
            added = 0
            for attachment in attachments:
                name = safe_filename(attachment["name"])
                if "." not in name and suffix_for(urllib.parse.urlparse(attachment["url"]).path):
                    name += "." + suffix_for(urllib.parse.urlparse(attachment["url"]).path)
                target = target_dir / name
                if not target.exists():
                    download_file(attachment["url"], target)
                rel_path = target.relative_to(ROOT).as_posix()
                suffix = suffix_for(target.name)
                status["files"].append(rel_path)
                import_rows.append(
                    {
                        "ATTACH_ID": str(uuid.uuid4()),
                        "POLICY_ID": status["policy_id"],
                        "ATTACH_NAME": target.name,
                        "ATTACH_SHOW_NAME": target.name,
                        "FILE_PATH": rel_path,
                        "FILE_SUFFIX": suffix,
                        "ATTACH_TYPE": default_attach_type(policy),
                        "ATTACH_REVIEW_STATUS": "未通过",
                    }
                )
                added += 1
            status["status"] = "已下载"
            status["reason"] = f"下载附件 {added} 个，待入库"
        except Exception as exc:
            status["status"] = "拒绝修改"
            status["reason"] = str(exc)
        statuses.append(status)
    return statuses, import_rows


def write_import_tsv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def col_to_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def cell_col_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref).group(0)
    value = 0
    for ch in letters:
        value = value * 26 + ord(ch) - 64
    return value


def get_sheet_path(work_dir: Path, sheet_name_prefix: str) -> Path:
    workbook = ET.parse(work_dir / "xl" / "workbook.xml")
    rels = ET.parse(work_dir / "xl" / "_rels" / "workbook.xml.rels")
    rel_map = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.getroot()
    }
    for sheet in workbook.getroot().find(f"{{{NS_MAIN}}}sheets"):
        if sheet.attrib.get("name", "").startswith(sheet_name_prefix):
            rid = sheet.attrib[f"{{{NS_REL}}}id"]
            target = rel_map[rid].lstrip("/")
            return work_dir / "xl" / target
    raise ValueError(f"sheet not found: {sheet_name_prefix}")


def load_shared_strings(work_dir: Path) -> tuple[ET.ElementTree, ET.Element]:
    path = work_dir / "xl" / "sharedStrings.xml"
    tree = ET.parse(path)
    return tree, tree.getroot()


def append_shared_string(sst: ET.Element, value: str) -> int:
    idx = len(sst.findall(f"{{{NS_MAIN}}}si"))
    si = ET.SubElement(sst, f"{{{NS_MAIN}}}si")
    t = ET.SubElement(si, f"{{{NS_MAIN}}}t")
    if value != value.strip():
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = value
    count = int(sst.attrib.get("count", idx))
    unique = int(sst.attrib.get("uniqueCount", idx))
    sst.attrib["count"] = str(count + 1)
    sst.attrib["uniqueCount"] = str(unique + 1)
    return idx


def row_cell(row_el: ET.Element, ref: str) -> ET.Element | None:
    for cell in row_el.findall(f"{{{NS_MAIN}}}c"):
        if cell.attrib.get("r") == ref:
            return cell
    return None


def set_text_cell(row_el: ET.Element, ref: str, shared_index: int, style: str | None = None) -> None:
    cell = row_cell(row_el, ref)
    if cell is None:
        cell = ET.Element(f"{{{NS_MAIN}}}c", {"r": ref, "t": "s"})
        if style:
            cell.set("s", style)
        inserted = False
        target_col = cell_col_index(ref)
        children = list(row_el.findall(f"{{{NS_MAIN}}}c"))
        for existing in children:
            if cell_col_index(existing.attrib["r"]) > target_col:
                row_el.insert(list(row_el).index(existing), cell)
                inserted = True
                break
        if not inserted:
            row_el.append(cell)
    cell.attrib["t"] = "s"
    v = cell.find(f"{{{NS_MAIN}}}v")
    if v is None:
        v = ET.SubElement(cell, f"{{{NS_MAIN}}}v")
    v.text = str(shared_index)


def update_dimension(sheet_root: ET.Element, col_name: str, max_row: int) -> None:
    dim = sheet_root.find(f"{{{NS_MAIN}}}dimension")
    if dim is not None:
        dim.attrib["ref"] = f"A1:{col_name}{max_row}"


def update_workbook_status(
    excel_path: Path,
    statuses: list[dict],
    sheet_prefix: str = "2026.7.10",
    status_header: str = "修改情况",
) -> tuple[Path, Path | None]:
    skill_dir = Path(os.environ.get("XLSX_SKILL_DIR", r"C:\Users\联想\.codex\skills\minimax-xlsx"))
    unpack = skill_dir / "scripts" / "xlsx_unpack.py"
    pack = skill_dir / "scripts" / "xlsx_pack.py"
    python = sys.executable
    work_dir = Path(tempfile.mkdtemp(prefix="policy_xlsx_"))
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run([python, str(unpack), str(excel_path), str(work_dir)], check=True, env=env)

    sheet_path = get_sheet_path(work_dir, sheet_prefix)
    sheet_tree = ET.parse(sheet_path)
    sheet_root = sheet_tree.getroot()
    sheet_data = sheet_root.find(f"{{{NS_MAIN}}}sheetData")
    rows = {int(row.attrib["r"]): row for row in sheet_data.findall(f"{{{NS_MAIN}}}row")}
    max_col = 0
    max_row = 0
    for row_el in rows.values():
        max_row = max(max_row, int(row_el.attrib["r"]))
        for cell in row_el.findall(f"{{{NS_MAIN}}}c"):
            max_col = max(max_col, cell_col_index(cell.attrib["r"]))
    status_col = max_col + 1
    status_col_name = col_to_name(status_col)

    shared_tree, sst = load_shared_strings(work_dir)
    header_idx = append_shared_string(sst, status_header)
    header_row = rows[1]
    last_header = row_cell(header_row, col_to_name(max_col) + "1")
    set_text_cell(header_row, f"{status_col_name}1", header_idx, last_header.attrib.get("s") if last_header is not None else None)
    for status in statuses:
        row_number = int(status["excel_row"])
        row_el = rows.get(row_number)
        if row_el is None:
            continue
        text = status["reason"]
        if status["status"] == "已入库":
            text = status.get("reason") or f"已添加附件并入库：{len(status.get('files') or [])}个"
        elif status["status"] == "已下载":
            text = f"已下载附件待入库：{len(status.get('files') or [])}个"
        elif status["status"] == "拒绝修改":
            text = f"拒绝修改：{status['reason']}"
        idx = append_shared_string(sst, text)
        set_text_cell(row_el, f"{status_col_name}{row_number}", idx)
    update_dimension(sheet_root, status_col_name, max_row)
    shared_tree.write(work_dir / "xl" / "sharedStrings.xml", encoding="utf-8", xml_declaration=True)
    sheet_tree.write(sheet_path, encoding="utf-8", xml_declaration=True)

    backup = excel_path.with_suffix(".backup_before_attachment_fix.xlsx")
    if not backup.exists():
        shutil.copy2(excel_path, backup)
    output = excel_path.with_suffix(".updated.xlsx")
    subprocess.run([python, str(pack), str(work_dir), str(output)], check=True, env=env)
    try:
        shutil.copy2(output, excel_path)
        copied_output = None
        try:
            output.unlink()
        except OSError:
            copied_output = output
    except PermissionError:
        copied_output = output
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    return backup, copied_output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--excel", type=Path)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--out", type=Path, default=ROOT / "exports" / "missing_attachment_import.tsv")
    parser.add_argument("--status-json", type=Path, default=ROOT / "exports" / "missing_attachment_status.json")
    parser.add_argument("--sheet-prefix", default="2026.7.10")
    parser.add_argument("--trigger-header", default="核对")
    parser.add_argument("--trigger-value", default="待添加")
    parser.add_argument("--trigger-column", type=int)
    parser.add_argument("--status-header", default="修改情况")
    parser.add_argument("--update-excel", action="store_true")
    args = parser.parse_args()
    if args.excel is None:
        args.excel = find_excel()

    rows = load_pending_rows(
        args.excel,
        sheet_prefix=args.sheet_prefix,
        trigger_header=args.trigger_header,
        trigger_value=args.trigger_value,
        trigger_column=args.trigger_column,
    )
    statuses, import_rows = process_rows(rows, args.api_base)
    write_import_tsv(args.out, import_rows)
    args.status_json.write_text(json.dumps(statuses, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"pending_rows={len(rows)}")
    print(f"downloaded_rows={sum(1 for s in statuses if s['status'] == '已下载')}")
    print(f"rejected_rows={sum(1 for s in statuses if s['status'] == '拒绝修改')}")
    print(f"import_rows={len(import_rows)}")
    print(f"tsv={args.out}")
    print(f"status_json={args.status_json}")
    if args.update_excel:
        backup, copied_output = update_workbook_status(
            args.excel,
            statuses,
            sheet_prefix=args.sheet_prefix,
            status_header=args.status_header,
        )
        print(f"excel_updated={args.excel if copied_output is None else copied_output}")
        print(f"excel_backup={backup}")
        if copied_output is not None:
            print(f"excel_update_note=original workbook could not be overwritten; updated copy was kept")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
