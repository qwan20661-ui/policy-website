from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from docx import Document  # 支持 docx

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from config import DEFAULT_ENV_FILE, MetadataSettings, load_settings
from extractor import (
    build_empty_result,
    process_one_file,
    process_one_policy,
    save_json,
    set_db_keywords,
)
from llm_client import MetadataLLMClient
from prompt_loader import load_markdown_prompt, load_rule_options_from_db, load_industry_options_from_db, load_keyword_options_from_db
from dameng_jdbc_loader import load_rule_options_via_jdbc
import convert_keywords as keyword_postprocessor
from db_importer import JsonDatabaseImporter


SUMMARY_KEY = "\u7edf\u8ba1\u4fe1\u606f"
LIST_KEY = "\u653f\u7b56\u6570\u636e\u5217\u8868"
FILE_NAME_KEY = "\u6587\u4ef6\u540d"


class DamengDictConnection:
    """Adapt dmPython cursors to the dict-row interface used by prompt_loader."""

    def __init__(self, connection):
        self._connection = connection

    def cursor(self, *args, **kwargs):
        return DamengDictCursor(self._connection.cursor())

    def close(self):
        self._connection.close()


class DamengDictCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def execute(self, *args, **kwargs):
        return self._cursor.execute(*args, **kwargs)

    def fetchall(self):
        rows = self._cursor.fetchall()
        columns = [item[0].lower() for item in self._cursor.description or []]
        return [dict(zip(columns, row)) for row in rows]

    def close(self):
        self._cursor.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量提取 Markdown / Word 政策文件的结构化信息")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--max-workers", type=int)
    parser.add_argument("--file", type=str, help="只处理单个文件")
    parser.add_argument("--no-db-import", action="store_true", help="只输出 JSON，不写入数据库")
    parser.add_argument("--db-dry-run", action="store_true", help="执行入库链路但回滚数据库事务")
    return parser.parse_args()


# ===================== docx读取 =====================
def read_docx_text(file_path: Path) -> str:
    doc = Document(file_path)
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])


# ===================== 文件扫描 =====================
def discover_files(input_dirs: list[Path]) -> list[tuple[Path, Path]]:
    results = []
    seen = set()

    # 需要排除的目录名
    exclude_dirs = {"attachments", "attachment", "附", "附件", "附件1", "附件2"}

    for root_dir in input_dirs:
        if not root_dir.exists():
            print(f"警告：目录不存在 -> {root_dir}")
            continue

        for path in root_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.name.startswith(("~$", ".")):
                continue

            # 排除附件目录
            if path.parent.name.lower() in exclude_dirs:
                continue

            if path.suffix.lower() not in [".md", ".docx", ".docs"]:
                continue

            resolved = path.resolve()
            if resolved in seen:
                continue

            seen.add(resolved)
            results.append((root_dir, path))

    results.sort(key=lambda x: str(x[1]).lower())
    return results


# ===================== 按目录分组 =====================
def group_files_by_directory(file_items: list[tuple[Path, Path]]) -> dict[Path, list[Path]]:
    """将文件按父目录分组"""
    dir_groups = {}

    for root_dir, file_path in file_items:
        parent = file_path.parent
        if parent not in dir_groups:
            dir_groups[parent] = []
        dir_groups[parent].append(file_path)

    return dir_groups


# ===================== 附件扫描 =====================
def find_attachments(file_path: Path) -> list[str]:
    """查找与文件同级的附件目录，返回附件文件名列表"""
    attachment_names = []

    # 常见的附件目录名
    attachment_dirs = ["附件", "attachments", "attachment", "附", "附件1", "附件2"]

    parent = file_path.parent

    for att_dir in attachment_dirs:
        att_path = parent / att_dir
        if att_path.is_dir():
            for att_file in att_path.iterdir():
                if att_file.is_file() and not att_file.name.startswith("~$"):
                    attachment_names.append(att_file.name)

    return attachment_names


# ===================== 统一处理入口 =====================
def process_file_wrapper(file_path: Path, settings, client):
    if file_path.suffix.lower() in [".docx", ".docs"]:
        text = read_docx_text(file_path)

        temp_md = file_path.with_suffix(".tmp.md")
        temp_md.write_text(text, encoding="utf-8")

        result = process_one_file(temp_md, settings, client)

        temp_md.unlink(missing_ok=True)
    else:
        result = process_one_file(file_path, settings, client)

    # 查找附件目录
    attachments = find_attachments(file_path)
    if attachments:
        result["附件"] = attachments

    return result


def build_output_json_path(output_root: Path, root_dir: Path, file_path: Path) -> Path:
    relative = file_path.relative_to(root_dir)
    return output_root / relative.with_suffix(".json")


def _build_final_output(total_dirs: int, total_files: int, success_count: int, start_time: float, items: list[dict]) -> dict:
    return {
        SUMMARY_KEY: {
            "\u603b\u76ee\u5f55\u6570": total_dirs,
            "\u603b\u6587\u4ef6\u6570": total_files,
            "\u6210\u529f\u6570": success_count,
            "\u8017\u65f6\u79d2": round(time.time() - start_time, 2),
        },
        LIST_KEY: items,
    }


def _save_running_summary(settings, total_dirs: int, total_files: int, success_count: int, start_time: float, items: list[dict]) -> Path:
    final_json_path = settings.output_dir / "all_policy_data.json"
    save_json(_build_final_output(total_dirs, total_files, success_count, start_time, items), final_json_path)
    return final_json_path


def _save_and_import_result(
    *,
    result: dict,
    out_path: Path,
    settings,
    importer: JsonDatabaseImporter | None,
    item_index: int,
    total_dirs: int,
    total_files: int,
    success_count: int,
    start_time: float,
    all_policy_full_data: list[dict],
) -> dict | None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(result, out_path)
    _save_running_summary(settings, total_dirs, total_files, success_count, start_time, all_policy_full_data)

    if importer is None:
        return None

    try:
        db_result = importer.import_item(result, item_index)
    except Exception as exc:
        raise RuntimeError(f"DB_IMPORT_FAILED: {exc}") from exc
    if db_result.get("enabled"):
        summary = db_result.get("summary", {})
        inserted = summary.get("insertedPolicies", 0)
        skipped = summary.get("skippedPolicies", 0)
        print(f"数据库导入完成: insertedPolicies={inserted}, skippedPolicies={skipped}")
    else:
        print("数据库导入已跳过")
    return db_result


def _load_keyword_postprocess_rules() -> None:
    attribute_dir = CURRENT_DIR / "Attribute"
    keyword_postprocessor._IMPLEMENT_RULE_TITLES = keyword_postprocessor.load_implementation_rules(
        attribute_dir / "实施细则.json"
    )
    keyword_postprocessor._GUIDING_RULE_TITLES = keyword_postprocessor.load_guiding_rules(
        attribute_dir / "指导政策.json"
    )
    print(
        "已加载关键词后处理规则："
        f"实施细则({len(keyword_postprocessor._IMPLEMENT_RULE_TITLES)}条)、"
        f"指导政策({len(keyword_postprocessor._GUIDING_RULE_TITLES)}条)"
    )


def _register_current_policy_relation_candidate(result: dict) -> None:
    metadata = result.get("元数据", {})
    if not isinstance(metadata, dict):
        return

    title = metadata.get("政策标题", "")
    attribute = metadata.get("政策属性", "")
    if not title or not attribute:
        return

    if "实施细则" in attribute and title not in keyword_postprocessor._IMPLEMENT_RULE_TITLES:
        keyword_postprocessor._IMPLEMENT_RULE_TITLES.append(title)
    if "指导政策" in attribute and title not in keyword_postprocessor._GUIDING_RULE_TITLES:
        keyword_postprocessor._GUIDING_RULE_TITLES.append(title)


def _postprocess_result(result: dict, source_dir: Path | None = None) -> dict:
    _register_current_policy_relation_candidate(result)
    try:
        keyword_postprocessor.process_policy(result, source_dir)
    except Exception as exc:
        print(f"警告：关键词后处理失败，保留原抽取结果: {exc}")

    metadata = result.setdefault("元数据", {})
    if isinstance(metadata, dict):
        metadata["政策属性"] = "主政策"
    return result


# ===================== 批处理核心 =====================
def is_policy_directory(dir_path: Path) -> bool:
    """判断目录是否为政策目录（包含实施细则/通知公告/指导政策子目录）"""
    subdirs = {"实施细则", "通知公告", "指导政策"}
    return any((dir_path / subdir).is_dir() for subdir in subdirs)


def find_policy_dirs(file_items: list[tuple[Path, Path]]) -> dict[Path, list[Path]]:
    """
    扫描所有文件，按政策目录分组
    政策目录 = 包含"实施细则"、"通知公告"、"指导政策"子目录的目录
    返回 {政策目录: [所有文件路径]}
    """
    # 先找出所有政策目录
    all_dirs = set()
    for root_dir, file_path in file_items:
        # 从文件路径向上查找，找到包含"实施细则/通知公告/指导政策"子目录的目录
        current = file_path.parent
        while current != current.parent:  # 一直向上到根目录
            if is_policy_directory(current):
                all_dirs.add(current)
                break
            current = current.parent

    # 将文件分配到对应的政策目录
    policy_groups = {d: [] for d in all_dirs}
    for root_dir, file_path in file_items:
        # 找到文件所属的政策目录
        current = file_path.parent
        while current != current.parent:
            if current in policy_groups:
                policy_groups[current].append(file_path)
                break
            current = current.parent

    return policy_groups


def process_files_in_batches(file_items, settings, client, importer: JsonDatabaseImporter | None = None):
    _load_keyword_postprocess_rules()

    # 查找政策目录并按政策目录分组
    policy_groups = find_policy_dirs(file_items)

    # 找出不属于任何政策目录的"游离"文件，按普通目录分组
    policy_file_set = set()
    for files in policy_groups.values():
        policy_file_set.update(files)

    normal_file_items = [(r, f) for r, f in file_items if f not in policy_file_set]
    normal_groups = group_files_by_directory(normal_file_items)

    # 政策目录列表（去重）
    policy_dirs = list(policy_groups.keys())

    total_dirs = len(policy_dirs) + len(normal_groups)
    total_files = sum(len(v) for v in normal_groups.values()) + len(policy_dirs)

    all_policy_full_data = []  # 🔥 汇总容器
    db_import_results = []
    success_count = 0

    print("\n=== 开始处理 ===")
    print(f"共 {total_dirs} 个目录（含 {len(policy_dirs)} 个政策目录，{len(normal_groups)} 个普通目录），{total_files} 个文件")
    start_time = time.time()

    # 处理政策目录（按新逻辑：实施细则主内容 + 全部目录用于政策有效期）
    dir_idx = 0
    for policy_dir in policy_dirs:
        dir_idx += 1
        policy_name = policy_dir.name
        try:
            result = process_one_policy(policy_name, policy_dir, settings, client)
            result = _postprocess_result(result, policy_dir)
            result.setdefault(FILE_NAME_KEY, f"{policy_name}.md")
            all_policy_full_data.append(result)
            success_count += 1

            # 输出到 政策目录名.json
            out_path = settings.output_dir / f"{policy_name}.json"
            db_result = _save_and_import_result(
                result=result,
                out_path=out_path,
                settings=settings,
                importer=importer,
                item_index=len(all_policy_full_data),
                total_dirs=total_dirs,
                total_files=total_files,
                success_count=success_count,
                start_time=start_time,
                all_policy_full_data=all_policy_full_data,
            )
            if db_result is not None:
                db_import_results.append(db_result)

            print(f"[{dir_idx}/{total_dirs}] 政策目录完成: {policy_name}")

        except Exception as e:
            if str(e).startswith("DB_IMPORT_FAILED:"):
                raise
            print(f"[{dir_idx}/{total_dirs}] 政策目录失败: {policy_name} -> {e}")
            empty = build_empty_result(policy_name)
            all_policy_full_data.append(empty)

            out_path = settings.output_dir / f"{policy_name}.json"
            save_json(empty, out_path)
            _save_running_summary(settings, total_dirs, total_files, success_count, start_time, all_policy_full_data)

    # 处理普通目录（逐文件处理，保持原有逻辑）
    for parent_dir, file_paths in normal_groups.items():
        for file_idx, file_path in enumerate(file_paths):
            dir_idx += 1
            try:
                result = process_file_wrapper(file_path, settings, client)
                result = _postprocess_result(result, file_path.parent)
                result.setdefault(FILE_NAME_KEY, file_path.name)
                all_policy_full_data.append(result)
                success_count += 1

                # 确定根目录
                root_dir = parent_dir.parent
                out_path = build_output_json_path(settings.output_dir, root_dir, file_path)
                db_result = _save_and_import_result(
                    result=result,
                    out_path=out_path,
                    settings=settings,
                    importer=importer,
                    item_index=len(all_policy_full_data),
                    total_dirs=total_dirs,
                    total_files=total_files,
                    success_count=success_count,
                    start_time=start_time,
                    all_policy_full_data=all_policy_full_data,
                )
                if db_result is not None:
                    db_import_results.append(db_result)

                print(f"[{dir_idx}/{total_dirs}] ({file_idx+1}/{len(file_paths)}) 完成: {file_path.name}")

            except Exception as e:
                if str(e).startswith("DB_IMPORT_FAILED:"):
                    raise
                print(f"[{dir_idx}/{total_dirs}] ({file_idx+1}/{len(file_paths)}) 失败: {file_path.name} -> {e}")
                empty = build_empty_result(file_path.stem)
                all_policy_full_data.append(empty)

                root_dir = parent_dir.parent
                out_path = build_output_json_path(settings.output_dir, root_dir, file_path)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                save_json(empty, out_path)
                _save_running_summary(settings, total_dirs, total_files, success_count, start_time, all_policy_full_data)

    # ===================== 🔥 汇总输出 =====================
    total_time = time.time() - start_time

    final_output = {
        "统计信息": {
            "总目录数": total_dirs,
            "总文件数": total_files,
            "成功数": success_count,
            "耗时秒": round(total_time, 2),
        },
        "政策数据列表": all_policy_full_data,
    }

    final_json_path = settings.output_dir / "all_policy_data.json"
    save_json(final_output, final_json_path)

    # ===================== 🔥 按政策属性分类输出到 Attribute 文件夹 =====================
    attribute_dir = CURRENT_DIR / "Attribute"
    attribute_dir.mkdir(parents=True, exist_ok=True)

    # 收集指导政策、实施细则和通知公告的标题
    guiding_titles = set()
    implement_titles = set()
    notice_titles = set()

    for policy_data in all_policy_full_data:
        policy_title = policy_data.get("元数据", {}).get("政策标题", "")
        policy_attribute = policy_data.get("元数据", {}).get("政策属性", "")

        if not policy_title:
            continue

        # 指导政策分类
        if "指导政策" in policy_attribute:
            guiding_titles.add(policy_title)

        # 实施细则分类
        if "实施细则" in policy_attribute:
            implement_titles.add(policy_title)

        # 通知公告分类
        if "通知公告" in policy_attribute:
            notice_titles.add(policy_title)

    # 去重后排序
    guiding_list = sorted(list(guiding_titles))
    implement_list = sorted(list(implement_titles))
    notice_list = sorted(list(notice_titles))

    # 输出指导政策.json
    guiding_output = {
        "指导政策数": len(guiding_list),
        "政策标题列表": guiding_list,
    }
    guiding_json_path = attribute_dir / "指导政策.json"
    save_json(guiding_output, guiding_json_path)
    print(f"\n指导政策汇总: {len(guiding_list)} 条 -> {guiding_json_path}")

    # 输出实施细则.json
    implement_output = {
        "实施细则数": len(implement_list),
        "政策标题列表": implement_list,
    }
    implement_json_path = attribute_dir / "实施细则.json"
    save_json(implement_output, implement_json_path)
    print(f"实施细则汇总: {len(implement_list)} 条 -> {implement_json_path}")

    # 输出通知公告.json
    notice_output = {
        "通知公告数": len(notice_list),
        "政策标题列表": notice_list,
    }
    notice_json_path = attribute_dir / "通知公告.json"
    save_json(notice_output, notice_json_path)
    print(f"通知公告汇总: {len(notice_list)} 条 -> {notice_json_path}")

    print("\n========== 处理完成 ==========")
    print(f"总目录数: {total_dirs}")
    print(f"总文件数: {total_files}")
    print(f"成功数: {success_count}")
    print(f"汇总文件: {final_json_path}")
    print(f"属性分类文件: {attribute_dir}")

    return {
        "total_dirs": total_dirs,
        "total_files": total_files,
        "success_count": success_count,
        "elapsed_seconds": round(total_time, 2),
        "output_file": str(final_json_path),
        "attribute_dir": str(attribute_dir),
        "db_import_enabled": bool(importer and importer.enabled),
        "db_import_count": len(db_import_results),
        "db_import_results": db_import_results,
    }


def build_metadata_client(settings: MetadataSettings) -> MetadataLLMClient:
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    prompt = load_markdown_prompt(
        settings.prompt_file,
        required_placeholders=("source_name", "source_path", "policy_text", "policy_validity_text", "pre_extracted_validity"),
    )

    # 从数据库加载规则选项
    rule_options = None
    connection = None
    try:
        import dmPython

        if not all([settings.db_host, settings.db_user, settings.db_password]):
            raise ValueError("达梦数据库配置不完整，请检查 METADATA_DB_HOST、METADATA_DB_USER、METADATA_DB_PASSWORD")

        raw_connection = dmPython.connect(
            user=settings.db_user,
            password=settings.db_password,
            server=settings.db_host,
            port=settings.db_port,
        )
        connection = DamengDictConnection(raw_connection)
        rule_options = load_rule_options_from_db(connection)
        # 加载行业领域选项
        industry_options_str, industry_map = load_industry_options_from_db(connection)
        rule_options.industry_options = industry_options_str
        rule_options.industry_map = industry_map

        # 加载关键词选项
        keyword_options_str = load_keyword_options_from_db(connection)
        rule_options.keyword_options = keyword_options_str

        # 将数据库关键词库设置到 extractor 模块
        keyword_list = keyword_options_str.split("；") if keyword_options_str else []
        set_db_keywords(keyword_list)

        print(f"已从达梦数据库加载规则选项：适用范围({len(rule_options.scope_map)}个)、申报对象({len(rule_options.applicant_map)}个)、扶持类型({len(rule_options.support_map)}个)、场景用途({len(rule_options.usage_map)}个)、行业领域({len(rule_options.industry_map)}个)、关键词({len(keyword_options_str.split('；'))}个)")
    except Exception as e:
        print(f"警告：dmPython 加载规则选项失败，尝试使用 JDBC 加载: {e}")
        try:
            rule_options = load_rule_options_via_jdbc(settings)
            keyword_list = rule_options.keyword_options.split("；") if rule_options.keyword_options else []
            set_db_keywords(keyword_list)
            print(
                "已通过 JDBC 从达梦数据库加载规则选项："
                f"适用范围({len(rule_options.scope_map)}个)、"
                f"申报对象({len(rule_options.applicant_map)}个)、"
                f"扶持类型({len(rule_options.support_map)}个)、"
                f"场景用途({len(rule_options.usage_map)}个)、"
                f"行业领域({len(rule_options.industry_map)}个)、"
                f"关键词({len(keyword_list)}个)"
            )
        except Exception as jdbc_error:
            print(f"警告：JDBC 加载规则选项也失败，将使用提示词中的默认值: {jdbc_error}")
    finally:
        if connection is not None:
            connection.close()

    return MetadataLLMClient(settings=settings, prompt=prompt, rule_options=rule_options)


def run_extraction(
    *,
    env_file: str | Path | None = None,
    batch_size: int | None = None,
    max_workers: int | None = None,
    file: str | Path | None = None,
    input_dirs: list[str | Path] | None = None,
    output_dir: str | Path | None = None,
    import_to_db: bool | None = None,
    db_dry_run: bool | None = None,
) -> dict:
    settings = load_settings(
        env_file=env_file or DEFAULT_ENV_FILE,
        batch_size_override=batch_size,
        max_workers_override=max_workers,
    )

    if input_dirs:
        settings.input_dirs = [Path(item).resolve() for item in input_dirs]
    if output_dir:
        settings.output_dir = Path(output_dir).resolve()
    if import_to_db is not None:
        settings.db_import_enabled = import_to_db
    if db_dry_run is not None:
        settings.db_import_dry_run = db_dry_run

    client = build_metadata_client(settings)
    importer = JsonDatabaseImporter(settings) if settings.db_import_enabled else None

    # 单文件模式
    if file:
        file_path = Path(file)
        if not file_path.exists():
            raise SystemExit(f"文件不存在: {file_path}")

        root = settings.input_dirs[0]
        file_items = [(root, file_path)]

        print(f"单文件模式: {file_path}")

    else:
        file_items = discover_files(settings.input_dirs)

        print(f"共发现 {len(file_items)} 个文件")

        if not file_items:
            print("未找到文件")
            return {
                "total_dirs": 0,
                "total_files": 0,
                "success_count": 0,
                "elapsed_seconds": 0,
                "output_file": None,
                "attribute_dir": str(CURRENT_DIR / "Attribute"),
            }

    return process_files_in_batches(file_items, settings, client, importer=importer)


# ===================== 主函数 =====================
def main():
    args = parse_args()
    run_extraction(
        env_file=args.env_file,
        batch_size=args.batch_size,
        max_workers=args.max_workers,
        file=args.file,
        import_to_db=False if args.no_db_import else None,
        db_dry_run=True if args.db_dry_run else None,
    )


if __name__ == "__main__":
    main()
