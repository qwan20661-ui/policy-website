import os
import argparse
import csv
import io
import subprocess
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


ROOT_DIR = Path(__file__).resolve().parents[1]
JAVA_HELPER = ROOT_DIR / "script" / "ExportPolicyExcelRows.java"
DM_JDBC_JAR = ROOT_DIR / "lib" / "jdbc" / "DmJdbcDriver8.jar"

DEFAULT_HOST = os.environ.get("KEJIJU_DB_HOST", "127.0.0.1")
DEFAULT_PORT = "5237"
DEFAULT_USER = "SYSDBA"
DEFAULT_PASSWORD = os.environ.get("KEJIJU_DB_PASSWORD", "")
DEFAULT_OUTPUT = ROOT_DIR / "script" / "policy_attachment_department_export.xlsx"
STATUS_CHOICES = ("已标注", "标注中", "未标注")
HIDDEN_POLICY_ATTRIBUTES = {"主政策", "主属性"}

BASE_HEADERS = [
    ("policy_id", "政策ID"),
    ("policy_title", "政策标题"),
    ("policy_attribute", "政策属性"),
    ("issuing_department", "发文部门"),
    ("has_attachment", "是否有附件"),
    ("attachment_count", "附件数量"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="导出政策标题、是否有附件、发文部门到 Excel。"
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="达梦数据库地址")
    parser.add_argument("--port", default=DEFAULT_PORT, help="达梦数据库端口")
    parser.add_argument("--user", default=DEFAULT_USER, help="达梦数据库用户名")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="达梦数据库密码")
    parser.add_argument(
        "--status",
        choices=STATUS_CHOICES,
        help="按 merge 页状态筛选，可选：已标注、标注中、未标注",
    )
    parser.add_argument(
        "--hidden-only",
        action="store_true",
        help="只导出政策属性为主政策/主属性的记录",
    )
    parser.add_argument(
        "--exclude-hidden",
        action="store_true",
        help="排除政策属性为主政策/主属性的记录，默认导出 merge 页可见列表时建议开启",
    )
    parser.add_argument(
        "--hide-policy-attribute-column",
        action="store_true",
        help="Excel 中不展示政策属性列",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="输出 Excel 路径，默认写到 script/policy_attachment_department_export.xlsx",
    )
    return parser.parse_args()


def fetch_rows(args: argparse.Namespace) -> list[dict]:
    if not JAVA_HELPER.exists():
        raise FileNotFoundError(f"未找到 Java 查询脚本: {JAVA_HELPER}")
    if not DM_JDBC_JAR.exists():
        raise FileNotFoundError(f"未找到达梦 JDBC 驱动: {DM_JDBC_JAR}")

    cmd = [
        "java",
        "--class-path",
        str(DM_JDBC_JAR),
        str(JAVA_HELPER),
        args.host,
        args.port,
        args.user,
        args.password,
    ]
    if args.status:
        cmd.append(args.status)
    result = subprocess.run(
        cmd,
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or "无错误输出"
        raise RuntimeError(f"Java 查询失败:\n{stderr}")

    content = result.stdout.lstrip("\ufeff").strip()
    if not content:
        return []

    reader = csv.DictReader(io.StringIO(content))
    return list(reader)


def filter_rows(rows: list[dict], args: argparse.Namespace) -> list[dict]:
    if args.hidden_only:
        rows = [row for row in rows if (row.get("policy_attribute") or "").strip() in HIDDEN_POLICY_ATTRIBUTES]
    elif args.exclude_hidden:
        rows = [row for row in rows if (row.get("policy_attribute") or "").strip() not in HIDDEN_POLICY_ATTRIBUTES]
    return rows


def resolve_headers(args: argparse.Namespace) -> list[tuple[str, str]]:
    if args.hide_policy_attribute_column:
        return [item for item in BASE_HEADERS if item[0] != "policy_attribute"]
    return BASE_HEADERS


def write_excel(rows: list[dict], output_path: Path, headers: list[tuple[str, str]]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "政策导出"
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(len(rows) + 1, 2)}"

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    header_alignment = Alignment(horizontal="center", vertical="center")

    for col_idx, (_, title) in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=title)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_alignment

    for row_idx, row in enumerate(rows, start=2):
        for col_idx, (key, _) in enumerate(headers, start=1):
            value = row.get(key, "")
            if key == "attachment_count":
                try:
                    value = int(value or 0)
                except ValueError:
                    value = value or 0
            ws.cell(row=row_idx, column=col_idx, value=value)

    for column_cells in ws.columns:
        max_length = 0
        column_index = column_cells[0].column
        for cell in column_cells:
            text = "" if cell.value is None else str(cell.value)
            if len(text) > max_length:
                max_length = len(text)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        ws.column_dimensions[get_column_letter(column_index)].width = min(max(max_length + 2, 12), 60)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)


def main() -> int:
    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()

    try:
        rows = fetch_rows(args)
        rows = filter_rows(rows, args)
        headers = resolve_headers(args)
        write_excel(rows, output_path, headers)
    except Exception as exc:
        print(f"导出失败: {exc}", file=sys.stderr)
        return 1

    print(f"导出完成，共 {len(rows)} 条，文件位置: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
