"""企业原始数据的中文字段事实视图。

V2 接口接收的是上游企业原始 JSON。本模块严格按 V2 自有的
``config/policy_recommendation_field_map.json`` 把其中的 JSONPath 映射为中文字段名，
并保留可审计的原始路径。模型侧读取完整的中文键名原始数据，避免把内部 JSONPath
当作业务语义，也避免画像压缩后遗漏政策条件需要的字段。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List

from service import fields as F


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIELD_MAP_PATH = PROJECT_ROOT / "config" / "policy_recommendation_field_map.json"
_SEGMENT_RE = re.compile(r"^(?P<key>[^\[\]]+)(?P<wildcard>\[\*\])?$")

# 联系方式与个人姓名不属于政策条件核验事实，不默认发送给模型。
class RawFieldMappingError(RuntimeError):
    """字段映射配置不合法或不可读取。"""


@lru_cache(maxsize=1)
def load_field_map() -> Dict[str, str]:
    """读取 V2 自有字段映射；不依赖或导入 V1 运行时代码。"""
    try:
        payload = json.loads(FIELD_MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RawFieldMappingError(f"企业字段映射不可读取：{exc}") from exc
    if not isinstance(payload, dict) or not payload:
        raise RawFieldMappingError("企业字段映射必须是非空对象")

    result: Dict[str, str] = {}
    for source_path, field_name in payload.items():
        path = F.text(source_path)
        name = F.text(field_name)
        if not path.startswith("$.") or not name:
            raise RawFieldMappingError(f"企业字段映射项非法：{source_path!r}")
        result[path] = name
    return result


def reset_cache() -> None:
    """测试或映射文件更新后显式清理缓存。"""
    load_field_map.cache_clear()


def _is_scalar_present(value: Any) -> bool:
    """0、False 都是有效企业事实；容器字段由其叶子字段表示。"""
    if value is None or isinstance(value, (dict, list)):
        return False
    return not (isinstance(value, str) and not value.strip())


def _extract_path(record: Dict[str, Any], source_path: str) -> Iterable[tuple[str, Any]]:
    """解析本映射使用的 JSONPath 子集：对象路径与 ``[*]`` 数组展开。"""
    segments = source_path.removeprefix("$.").split(".")

    def visit(current: Any, index: int, resolved: str) -> Iterable[tuple[str, Any]]:
        if index >= len(segments):
            yield resolved, current
            return
        match = _SEGMENT_RE.match(segments[index])
        if not match or not isinstance(current, dict):
            return
        key = match.group("key")
        if key not in current:
            return
        value = current[key]
        next_resolved = f"{resolved}.{key}"
        if not match.group("wildcard"):
            yield from visit(value, index + 1, next_resolved)
            return
        if not isinstance(value, list):
            return
        for item_index, item in enumerate(value):
            yield from visit(item, index + 1, f"{next_resolved}[{item_index}]")

    yield from visit(record, 0, "$")


def build_raw_facts(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把所有可用叶子事实映射为中文名，并保存原始 JSONPath 供服务端审计。"""
    if not isinstance(record, dict):
        raise ValueError("企业原始数据必须是对象")

    facts: List[Dict[str, Any]] = []
    for source_pattern, field_name in load_field_map().items():
        for source_path, value in _extract_path(record, source_pattern):
            if not _is_scalar_present(value):
                continue
            facts.append({
                "fieldName": field_name,
                "value": value,
                "sourcePath": source_path,
            })
    return facts


def _path_pattern(path: str) -> str:
    """把实路径的数组下标还原为映射文件使用的 ``[*]``。"""
    return re.sub(r"\[\d+\]", "[*]", path)


def map_record_to_chinese(record: Dict[str, Any]) -> Dict[str, Any]:
    """按映射文件递归转换完整原始记录，保留数组、年度和子表的原有关系。

    不能把所有叶子事实拍平成列表：例如两年经营信息里的「统计年份」必须与同一条
    「营业收入」保留在一个对象里，模型才可以正确理解政策要求的年度口径。
    未列入映射的字段不输出，确保模型看到的每个键都有稳定的中文业务语义。
    """
    if not isinstance(record, dict):
        raise ValueError("企业原始数据必须是对象")
    field_map = load_field_map()

    def has_mapping_at_or_below(path: str) -> bool:
        pattern = _path_pattern(path)
        return any(
            candidate == pattern
            or candidate.startswith(pattern + ".")
            or candidate.startswith(pattern + "[*]")
            for candidate in field_map
        )

    def convert(value: Any, source_path: str) -> Any:
        if isinstance(value, dict):
            converted: Dict[str, Any] = {}
            for key, child in value.items():
                child_path = f"{source_path}.{key}"
                if not has_mapping_at_or_below(child_path):
                    continue
                pattern = _path_pattern(child_path)
                display_name = field_map.get(pattern)
                # 映射配置覆盖本项目所有可递归容器；若未来出现仅有子路径的容器，
                # 不伪造英文键，而以其唯一中文后代继续表示。
                if not display_name:
                    continue
                converted[display_name] = convert(child, child_path)
            return converted
        if isinstance(value, list):
            return [convert(item, f"{source_path}[{index}]") for index, item in enumerate(value)]
        return value

    return convert(record, "$")


def build_fact_index(record: Dict[str, Any]) -> Dict[str, Any]:
    """构建请求内只读事实索引，供 Agent 的按字段查询工具使用。"""
    facts = build_raw_facts(record)
    names = sorted({F.text(fact["fieldName"]) for fact in facts if F.text(fact["fieldName"])})
    return {
        "mappingFile": FIELD_MAP_PATH.name,
        "mappingCount": len(load_field_map()),
        "availableFieldNames": names,
        "facts": facts,
        "mappedRecord": map_record_to_chinese(record),
    }


def select_model_facts(
    fact_index: Dict[str, Any], field_names: Iterable[Any], *, max_facts: int = 60,
) -> List[Dict[str, Any]]:
    """按中文字段名返回模型可读事实，不输出内部路径。"""
    wanted = {F.text(name) for name in field_names if F.text(name)}
    if not wanted:
        return []

    result: List[Dict[str, Any]] = []
    for fact in fact_index.get("facts") or []:
        name = F.text(fact.get("fieldName"))
        if name not in wanted:
            continue
        result.append({"fieldName": name, "value": fact.get("value")})
        if len(result) >= max_facts:
            break
    return result
