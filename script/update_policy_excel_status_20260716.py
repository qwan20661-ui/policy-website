from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import openpyxl


EXCEL = Path(r"E:\policy\rule\局重点政策汇总.xlsx")
SHEET_NAME = "2026.7.15政策归类"
HEADER = "附件处理情况(2026-07-16)"
NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

ET.register_namespace("", NS_MAIN)
ET.register_namespace("r", NS_REL)


STATUSES = {
    18: "已补附件并入库：补入2025年10月-2026年4月科技成果登记清单附件7个（POLICY_ID:11887,12016,12223,12327,12414,12583,12741）；POLICY_ID 11432政府原网页未发现附件链接。",
    24: "拒绝修改：政府原网页当前返回站点提示页，未发现可下载附件链接；未使用非政府转载来源入库。",
    25: "已核实：POLICY_ATTACHMENT已存在2个附件且本地文件存在，无需重复导入。",
    56: "已补附件并入库：从政府网站补入附件1个（POLICY_ID:12544）。",
    60: "未处理：备注为缺少指导政策/关联政策，本轮按要求不导入关联表；未发现需补入当前源政策的附件。",
    72: "拒绝修改：POLICY_ID 70政府原网页未发现可下载附件链接；关联政策未导入关联表。",
    74: "已核实：POLICY_ID 111已有2个附件且本地文件存在，无需重复导入；结果公示/关联政策不在本轮处理范围。",
    80: "已核实：POLICY_ID 72已有3个附件且本地文件存在，无需重复导入；关联政策不在本轮处理范围。",
    92: "未处理：备注为缺少关联政策，本轮按要求不导入关联表；未发现需补入当前源政策的附件。",
    94: "拒绝修改：POLICY_ID 10709政府原网页未发现可下载附件链接；关联政策未导入关联表。",
    98: "未处理：备注为缺少关联政策，本轮按要求不导入关联表；未发现需补入当前源政策的附件。",
    106: "未处理：备注为缺少关联政策，本轮按要求不导入关联表；未发现需补入当前源政策的附件。",
    108: "未处理：备注为缺少关联政策，本轮按要求不导入关联表；未发现需补入当前源政策的附件。",
    110: "已核实：POLICY_ID 115已有附件5且可展示；另补入省级协同创新平台附件1个（POLICY_ID:14776）。",
    116: "未处理：备注为缺少关联政策，本轮按要求不导入关联表；未发现需补入当前源政策的附件。",
    120: "已核实：POLICY_ID 13159已有3个工信部附件且本地文件存在；本轮从工信部原文复核后未重复保留入库。",
    131: "未处理：备注为缺少关联政策，本轮按要求不导入关联表；未发现需补入当前源政策的附件。",
    133: "未处理：备注为缺少关联政策，本轮按要求不导入关联表；未发现需补入当前源政策的附件。",
    134: "未处理：备注为缺少关联政策，本轮按要求不导入关联表；未发现需补入当前源政策的附件。",
    136: "未处理：备注为缺少结果公示/关联政策，本轮按要求不导入关联表；未发现需补入当前源政策的附件。",
    158: "拒绝修改：POLICY_ID 11576政府原网页未发现可下载附件链接；关联政策未导入关联表。",
    159: "未处理：备注为缺少关联政策，本轮按要求不导入关联表；未发现需补入当前源政策的附件。",
    191: "已补附件并入库：从政府网站补入附件1个（POLICY_ID:14158）。",
    195: "已补附件并入库：补入省级技术转移机构认定名单附件1个（POLICY_ID:14732）；关联政策未导入关联表。",
    196: "拒绝修改：POLICY_ID 14449政府原网页未发现可下载附件链接；指导政策未导入关联表。",
    197: "拒绝修改：POLICY_ID 14472政府原网页未发现可下载附件链接；关联政策未导入关联表。",
    199: "未处理：备注为缺少关联政策，本轮按要求不导入关联表；未发现需补入当前源政策的附件。",
    201: "未处理：备注为缺少关联政策，本轮按要求不导入关联表；未发现需补入当前源政策的附件。",
    204: "未处理：备注为缺少政策文件/通知要求，本轮未找到可直接补入当前源政策附件的政府附件。",
}


def col_to_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def cell_col_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref).group(0)
    value = 0
    for char in letters:
        value = value * 26 + ord(char) - 64
    return value


def load_sheet_info() -> tuple[int, int]:
    wb = openpyxl.load_workbook(EXCEL, read_only=True, data_only=True)
    ws = wb[SHEET_NAME]
    header_col = None
    for col in range(1, ws.max_column + 1):
        if str(ws.cell(1, col).value or "").strip() == HEADER:
            header_col = col
            break
    max_column = ws.max_column
    max_row = ws.max_row
    wb.close()
    return header_col or max_column + 1, max_row


def workbook_sheet_target(work_dir: Path) -> str:
    workbook = ET.parse(work_dir / "xl" / "workbook.xml")
    rels = ET.parse(work_dir / "xl" / "_rels" / "workbook.xml.rels")
    rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.getroot()}
    for sheet in workbook.getroot().find(f"{{{NS_MAIN}}}sheets"):
        if sheet.attrib["name"] == SHEET_NAME:
            rel_id = sheet.attrib[f"{{{NS_REL}}}id"]
            target = rel_map[rel_id].lstrip("/")
            return target if target.startswith("xl/") else "xl/" + target
    raise RuntimeError(f"sheet not found: {SHEET_NAME}")


def append_shared_string(sst: ET.Element, text: str) -> int:
    existing = len(sst.findall(f"{{{NS_MAIN}}}si"))
    si = ET.SubElement(sst, f"{{{NS_MAIN}}}si")
    t = ET.SubElement(si, f"{{{NS_MAIN}}}t")
    t.text = text
    for attr in ("count", "uniqueCount"):
        if attr in sst.attrib:
            sst.attrib[attr] = str(int(sst.attrib[attr]) + 1)
    return existing


def get_or_create_shared_strings(work_dir: Path) -> tuple[ET.ElementTree, ET.Element]:
    path = work_dir / "xl" / "sharedStrings.xml"
    if path.exists():
        tree = ET.parse(path)
        return tree, tree.getroot()
    root = ET.Element(f"{{{NS_MAIN}}}sst", {"count": "0", "uniqueCount": "0"})
    return ET.ElementTree(root), root


def get_or_create_row(sheet_data: ET.Element, rows: dict[int, ET.Element], row_number: int) -> ET.Element:
    if row_number in rows:
        return rows[row_number]
    row = ET.Element(f"{{{NS_MAIN}}}row", {"r": str(row_number)})
    inserted = False
    for index, existing in enumerate(list(sheet_data)):
        if int(existing.attrib["r"]) > row_number:
            sheet_data.insert(index, row)
            inserted = True
            break
    if not inserted:
        sheet_data.append(row)
    rows[row_number] = row
    return row


def set_text_cell(row: ET.Element, cell_ref: str, string_index: int, style: str | None = None) -> None:
    cells = list(row.findall(f"{{{NS_MAIN}}}c"))
    target = None
    for cell in cells:
        if cell.attrib.get("r") == cell_ref:
            target = cell
            break
    if target is None:
        target = ET.Element(f"{{{NS_MAIN}}}c", {"r": cell_ref})
        target_col = cell_col_index(cell_ref)
        inserted = False
        for index, cell in enumerate(cells):
            if cell_col_index(cell.attrib["r"]) > target_col:
                row.insert(index, target)
                inserted = True
                break
        if not inserted:
            row.append(target)
    if style is not None:
        target.attrib["s"] = style
    target.attrib["t"] = "s"
    for child in list(target):
        target.remove(child)
    value = ET.SubElement(target, f"{{{NS_MAIN}}}v")
    value.text = str(string_index)


def style_from_cell(row: ET.Element, cell_ref: str) -> str | None:
    for cell in row.findall(f"{{{NS_MAIN}}}c"):
        if cell.attrib.get("r") == cell_ref:
            return cell.attrib.get("s")
    return None


def update_dimension(root: ET.Element, col_name: str, max_row: int) -> None:
    dimension = root.find(f"{{{NS_MAIN}}}dimension")
    if dimension is not None:
        dimension.attrib["ref"] = f"A1:{col_name}{max_row}"


def main() -> int:
    status_col, max_row = load_sheet_info()
    status_col_name = col_to_name(status_col)
    work_dir = Path(tempfile.mkdtemp(prefix="policy_excel_status_"))
    output = EXCEL.with_suffix(".updated.xlsx")
    backup = EXCEL.with_suffix(".backup_before_20260716_status.xlsx")
    try:
        with zipfile.ZipFile(EXCEL, "r") as archive:
            archive.extractall(work_dir)

        sheet_rel_path = workbook_sheet_target(work_dir)
        sheet_path = work_dir / sheet_rel_path
        sheet_tree = ET.parse(sheet_path)
        sheet_root = sheet_tree.getroot()
        sheet_data = sheet_root.find(f"{{{NS_MAIN}}}sheetData")
        if sheet_data is None:
            raise RuntimeError("sheetData not found")
        rows = {int(row.attrib["r"]): row for row in sheet_data.findall(f"{{{NS_MAIN}}}row")}

        shared_tree, sst = get_or_create_shared_strings(work_dir)
        header_row = get_or_create_row(sheet_data, rows, 1)
        header_style = style_from_cell(header_row, col_to_name(status_col - 1) + "1")
        set_text_cell(header_row, f"{status_col_name}1", append_shared_string(sst, HEADER), header_style)

        for row_number, text in STATUSES.items():
            row = get_or_create_row(sheet_data, rows, row_number)
            style = style_from_cell(row, col_to_name(status_col - 1) + str(row_number))
            set_text_cell(row, f"{status_col_name}{row_number}", append_shared_string(sst, text), style)

        update_dimension(sheet_root, status_col_name, max(max_row, max(STATUSES)))
        shared_tree.write(work_dir / "xl" / "sharedStrings.xml", encoding="utf-8", xml_declaration=True)
        sheet_tree.write(sheet_path, encoding="utf-8", xml_declaration=True)

        if not backup.exists():
            shutil.copy2(EXCEL, backup)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in work_dir.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(work_dir).as_posix())
        try:
            shutil.copy2(output, EXCEL)
            output.unlink(missing_ok=True)
            copied = ""
        except PermissionError:
            copied = str(output)
        print(f"status_column={status_col_name}")
        print(f"updated={EXCEL if not copied else copied}")
        print(f"backup={backup}")
        if copied:
            print("note=original workbook was locked; updated copy was kept")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    import re

    raise SystemExit(main())
