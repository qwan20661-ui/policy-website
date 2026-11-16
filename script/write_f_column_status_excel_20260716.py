import csv
import re
import shutil
import zipfile
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

INPUT = Path("E:/policy/rule/局重点政策汇总.xlsx")
STATUS_TSV = Path("E:/demo/exports/f_column_policy_processing_status_20260716.tsv")
OUTPUT = Path("E:/policy/rule/局重点政策汇总_F列政策处理结果_20260716.xlsx")
SHEET_NAME = "2026.7.15政策归类"
HEADER = "F列政策处理情况(2026-07-16)"

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
ET.register_namespace("", NS_MAIN)
ET.register_namespace("r", NS_REL)


def col_to_num(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + ord(ch.upper()) - 64
    return n


def num_to_col(num: int) -> str:
    out = ""
    while num:
        num, rem = divmod(num - 1, 26)
        out = chr(65 + rem) + out
    return out


def split_cell(ref: str):
    m = re.match(r"([A-Z]+)(\d+)", ref)
    return m.group(1), int(m.group(2))


def q(tag: str) -> str:
    return f"{{{NS_MAIN}}}{tag}"


def load_statuses():
    grouped = defaultdict(list)
    with STATUS_TSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            excel_row = int(row["EXCEL_ROW"])
            title = row["MISSING_POLICY_TITLE"].strip()
            status = row["STATUS"].strip()
            detail = row["POLICY_ID_OR_REASON"].strip()
            grouped[excel_row].append(f"{title}：{status}（{detail}）")
    return {row: "\n".join(items) for row, items in grouped.items()}


def target_sheet_path(zf: zipfile.ZipFile) -> str:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rid = None
    for sheet in workbook.find(q("sheets")):
        if sheet.attrib.get("name") == SHEET_NAME:
            rid = sheet.attrib.get(f"{{{NS_REL}}}id")
            break
    if not rid:
        raise RuntimeError(f"sheet not found: {SHEET_NAME}")

    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    for rel in rels:
        if rel.attrib.get("Id") == rid:
            target = rel.attrib["Target"].lstrip("/")
            return "xl/" + target if not target.startswith("xl/") else target
    raise RuntimeError(f"relationship not found: {rid}")


def inline_cell(ref: str, text: str, style: str | None):
    c = ET.Element(q("c"), {"r": ref, "t": "inlineStr"})
    if style:
        c.set("s", style)
    is_node = ET.SubElement(c, q("is"))
    t = ET.SubElement(is_node, q("t"))
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text
    return c


def row_cell_map(row):
    out = {}
    for c in row.findall(q("c")):
        ref = c.attrib.get("r")
        if ref:
            out[split_cell(ref)[0]] = c
    return out


def upsert_text_cell(row, col: str, row_num: int, text: str):
    cells = row_cell_map(row)
    ref = f"{col}{row_num}"
    old = cells.get(col)
    style = None
    if old is not None:
        style = old.attrib.get("s")
        row.remove(old)
    elif "G" in cells:
        style = cells["G"].attrib.get("s")
    elif "F" in cells:
        style = cells["F"].attrib.get("s")

    new_cell = inline_cell(ref, text, style)
    inserted = False
    new_col_num = col_to_num(col)
    for idx, cell in enumerate(row.findall(q("c"))):
        cell_col, _ = split_cell(cell.attrib["r"])
        if col_to_num(cell_col) > new_col_num:
            row.insert(idx, new_cell)
            inserted = True
            break
    if not inserted:
        row.append(new_cell)


def update_dimension(root, new_col, max_row):
    dim = root.find(q("dimension"))
    if dim is not None:
        ref = dim.attrib.get("ref", "A1")
        start = ref.split(":")[0] if ":" in ref else "A1"
        dim.set("ref", f"{start}:{new_col}{max_row}")


def ensure_col_width(root, col_num):
    cols = root.find(q("cols"))
    if cols is None:
        sheet_data = root.find(q("sheetData"))
        cols = ET.Element(q("cols"))
        root.insert(list(root).index(sheet_data), cols)
    col = ET.Element(q("col"), {"min": str(col_num), "max": str(col_num), "width": "60", "customWidth": "1"})
    cols.append(col)


def main():
    statuses = load_statuses()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(INPUT, OUTPUT)

    with zipfile.ZipFile(INPUT, "r") as zin:
        sheet_path = target_sheet_path(zin)
        root = ET.fromstring(zin.read(sheet_path))
        sheet_data = root.find(q("sheetData"))
        rows = {int(row.attrib["r"]): row for row in sheet_data.findall(q("row"))}
        max_row = max(rows)

        # Append after the current used range. The source sheet currently ends at G.
        new_col = "H"
        new_col_num = col_to_num(new_col)
        upsert_text_cell(rows[1], new_col, 1, HEADER)
        for row_num, text in statuses.items():
            if row_num in rows:
                upsert_text_cell(rows[row_num], new_col, row_num, text)
        update_dimension(root, new_col, max_row)
        ensure_col_width(root, new_col_num)
        new_sheet_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)

        with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == sheet_path:
                    data = new_sheet_xml
                zout.writestr(item, data)

    print(OUTPUT)
    print(f"sheet={SHEET_NAME} column={new_col} rows_written={len(statuses) + 1}")


if __name__ == "__main__":
    main()
