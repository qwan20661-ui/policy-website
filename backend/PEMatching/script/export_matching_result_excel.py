"""将 PEMatching 完整结果 JSON 离线导出为 Excel。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from service.matching_service import _write_excel_output


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将 PEMatching 完整结果 JSON 离线导出为 Excel")
    parser.add_argument("--input", required=True, help="CLI 结果 JSON 或接口 enterprise_match_*.json 文件路径。")
    parser.add_argument("--output", default="", help="Excel 输出路径，默认与输入文件同名、扩展名改为 .xlsx。")
    return parser.parse_args(argv)


def load_matching_results(input_path: Path) -> List[dict[str, Any]]:
    """兼容 CLI 结果数组和单企业接口归档的 matchResult 包装结构。"""
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"结果 JSON 格式无效: {input_path}") from exc

    if isinstance(payload, list):
        results = payload
    elif isinstance(payload, dict) and isinstance(payload.get("matchResult"), dict):
        results = [payload["matchResult"]]
    else:
        raise ValueError(
            "结果 JSON 必须是 CLI 输出的结果数组，或包含 matchResult 对象的单企业接口归档文件。"
        )

    if not results:
        raise ValueError("结果 JSON 为空，无法导出 Excel。")
    if not all(isinstance(item, dict) for item in results):
        raise ValueError("结果 JSON 中每个企业匹配结果必须是对象。")
    return results


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.is_file():
        print(f"结果 JSON 不存在: {input_path}")
        return 1

    output_path = Path(args.output).expanduser().resolve() if args.output.strip() else input_path.with_suffix(".xlsx")
    try:
        results = load_matching_results(input_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_excel_output(results, output_path)
    except (OSError, ValueError) as exc:
        print(f"导出失败: {exc}")
        return 1

    policy_count = sum(len(item.get("政策匹配结果") or []) for item in results)
    print(f"导出成功: {output_path}")
    print(f"企业数: {len(results)}，政策匹配结果数: {policy_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
