from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import dotenv_values
except ImportError:
    dotenv_values = None


METADATA_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = METADATA_DIR.parent
DEFAULT_ENV_FILE = METADATA_DIR / ".env"


@dataclass
class MetadataSettings:
    api_url: str
    api_key: str | None
    model_name: str
    input_dirs: list[Path]
    output_dir: Path
    request_timeout: float
    connect_timeout: float
    max_text_length: int
    batch_size: int
    max_workers: int
    max_retries: int
    temperature: float
    prompt_file: Path
    db_host: str | None
    db_port: int
    db_name: str | None
    db_user: str | None
    db_password: str | None
    db_charset: str
    db_import_enabled: bool
    db_import_dry_run: bool
    db_import_stage_dir: Path
    db_import_force_insert_policies: bool


def load_settings(
    env_file: str | os.PathLike[str] | None = None,
    batch_size_override: int | None = None,
    max_workers_override: int | None = None,
) -> MetadataSettings:
    env_path = Path(env_file).resolve() if env_file else DEFAULT_ENV_FILE
    file_values = _read_env_file(env_path)

    def get_value(name: str, default: str) -> str:
        return os.getenv(name, file_values.get(name, default))

    settings = MetadataSettings(
        api_url=_require_text(
            "METADATA_API_URL",
            get_value("METADATA_API_URL", "http://127.0.0.1:8889/v1/chat/completions"),
        ),
        api_key=_optional_text(
            get_value("METADATA_API_KEY", ""),
        ),
        model_name=_require_text(
            "METADATA_MODEL_NAME",
            get_value("METADATA_MODEL_NAME", "/model"),
        ),
        input_dirs=_parse_input_dirs(
            input_dirs_value=get_value("METADATA_INPUT_DIRS", ""),
            input_dir_value=get_value("METADATA_INPUT_DIR", "docs/test"),
            base_dir=PROJECT_ROOT,
        ),
        output_dir=_resolve_path(
            get_value("METADATA_OUTPUT_DIR", "outputs/test/test"),
            base_dir=PROJECT_ROOT,
        ),
        request_timeout=_parse_float(
            "METADATA_REQUEST_TIMEOUT",
            get_value("METADATA_REQUEST_TIMEOUT", "150"),
        ),
        connect_timeout=_parse_float(
            "METADATA_CONNECT_TIMEOUT",
            get_value("METADATA_CONNECT_TIMEOUT", "10"),
        ),
        max_text_length=_parse_int(
            "METADATA_MAX_TEXT_LENGTH",
            get_value("METADATA_MAX_TEXT_LENGTH", "12000"),
        ),
        batch_size=batch_size_override
        if batch_size_override is not None
        else _parse_int(
            "METADATA_BATCH_SIZE",
            get_value("METADATA_BATCH_SIZE", "10"),
        ),
        max_workers=max_workers_override
        if max_workers_override is not None
        else _parse_int(
            "METADATA_MAX_WORKERS",
            get_value("METADATA_MAX_WORKERS", "5"),
        ),
        max_retries=_parse_int(
            "METADATA_MAX_RETRIES",
            get_value("METADATA_MAX_RETRIES", "2"),
        ),
        temperature=_parse_float(
            "METADATA_TEMPERATURE",
            get_value("METADATA_TEMPERATURE", "0.0"),
        ),
        prompt_file=_resolve_path(
            get_value("METADATA_PROMPT_FILE", "metadata/prompts/policy_extract.md"),
            base_dir=PROJECT_ROOT,
        ),
        db_host=_optional_text(get_value("METADATA_DB_HOST", "")),
        db_port=_parse_int(
            "METADATA_DB_PORT",
            get_value("METADATA_DB_PORT", "3306"),
        ),
        db_name=_optional_text(get_value("METADATA_DB_NAME", "")),
        db_user=_optional_text(get_value("METADATA_DB_USER", "")),
        db_password=_optional_text(get_value("METADATA_DB_PASSWORD", "")),
        db_charset=_require_text(
            "METADATA_DB_CHARSET",
            get_value("METADATA_DB_CHARSET", "utf8mb4"),
        ),
        db_import_enabled=_parse_bool(
            "METADATA_DB_IMPORT_ENABLED",
            get_value("METADATA_DB_IMPORT_ENABLED", "true"),
        ),
        db_import_dry_run=_parse_bool(
            "METADATA_DB_IMPORT_DRY_RUN",
            get_value("METADATA_DB_IMPORT_DRY_RUN", "false"),
        ),
        db_import_stage_dir=_resolve_path(
            get_value("METADATA_DB_IMPORT_STAGE_DIR", "metadata/output/_db_stage"),
            base_dir=PROJECT_ROOT,
        ),
        db_import_force_insert_policies=_parse_bool(
            "METADATA_DB_IMPORT_FORCE_INSERT_POLICIES",
            get_value("METADATA_DB_IMPORT_FORCE_INSERT_POLICIES", "false"),
        ),
    )

    _validate_settings(settings)
    return settings


def _read_env_file(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        return {}

    if dotenv_values is not None:
        return {
            key: value
            for key, value in dotenv_values(env_path).items()
            if key and value is not None
        }

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _resolve_path(value: str, base_dir: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _parse_input_dirs(input_dirs_value: str, input_dir_value: str, base_dir: Path) -> list[Path]:
    """
    优先读取 METADATA_INPUT_DIRS
    多个目录使用英文分号 ; 分隔

    同时兼容旧配置 METADATA_INPUT_DIR
    """
    cleaned_dirs_value = input_dirs_value.strip()
    if cleaned_dirs_value:
        dir_list = [
            _resolve_path(item.strip(), base_dir)
            for item in cleaned_dirs_value.split(";")
            if item.strip()
        ]
        if not dir_list:
            raise ValueError("METADATA_INPUT_DIRS 不能为空")
        return dir_list

    cleaned_single_value = input_dir_value.strip()
    if not cleaned_single_value:
        raise ValueError("METADATA_INPUT_DIR 或 METADATA_INPUT_DIRS 不能为空")

    return [_resolve_path(cleaned_single_value, base_dir)]


def _require_text(name: str, value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} 不能为空")
    return cleaned


def _optional_text(value: str) -> str | None:
    cleaned = value.strip()
    return cleaned or None


def _parse_int(name: str, value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数，当前值为: {value}") from exc


def _parse_float(name: str, value: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是数字，当前值为: {value}") from exc


def _parse_bool(name: str, value: str) -> bool:
    cleaned = value.strip().lower()
    if cleaned in {"1", "true", "yes", "y", "on"}:
        return True
    if cleaned in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value, got: {value}")


def _validate_settings(settings: MetadataSettings) -> None:
    if not settings.input_dirs:
        raise ValueError("至少需要配置一个输入目录")

    if settings.request_timeout <= 0:
        raise ValueError("METADATA_REQUEST_TIMEOUT 必须大于 0")
    if settings.connect_timeout <= 0:
        raise ValueError("METADATA_CONNECT_TIMEOUT 必须大于 0")
    if settings.max_text_length <= 0:
        raise ValueError("METADATA_MAX_TEXT_LENGTH 必须大于 0")
    if settings.batch_size <= 0:
        raise ValueError("METADATA_BATCH_SIZE 或 --batch-size 必须大于 0")
    if settings.max_workers <= 0:
        raise ValueError("METADATA_MAX_WORKERS 或 --max-workers 必须大于 0")
    if settings.max_retries < 0:
        raise ValueError("METADATA_MAX_RETRIES 不能小于 0")
    if settings.db_import_enabled:
        missing = []
        if not settings.db_host:
            missing.append("METADATA_DB_HOST")
        if not settings.db_user:
            missing.append("METADATA_DB_USER")
        if settings.db_password is None:
            missing.append("METADATA_DB_PASSWORD")
        if missing:
            raise ValueError(f"DB import enabled but missing settings: {', '.join(missing)}")
    if not settings.prompt_file.exists():
        raise ValueError(f"提示词文件不存在: {settings.prompt_file}")
