from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from config import MetadataSettings


CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
STAGE_SCRIPT = SCRIPTS_DIR / "stage_policy_data11.py"
IMPORTER_SOURCE = SCRIPTS_DIR / "DmPolicyData11Importer.java"
IMPORTER_CLASS = SCRIPTS_DIR / "DmPolicyData11Importer.class"
DRIVER_JAR = PROJECT_ROOT / "demo" / "lib" / "jdbc" / "DmJdbcDriver8.jar"


class JsonDatabaseImporter:
    def __init__(self, settings: MetadataSettings):
        self.settings = settings
        self.enabled = settings.db_import_enabled
        self.dry_run = settings.db_import_dry_run
        self.stage_dir = settings.db_import_stage_dir
        self.force_insert_policies = settings.db_import_force_insert_policies
        self._stage_module = None

    def import_item(self, item: dict[str, Any], index: int) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "status": "skipped"}

        self._validate_runtime()
        stage_item_dir = self.stage_dir / f"item_{index:06d}"
        self._write_stage_files(stage_item_dir, item, str(index))
        output = self._run_java_importer(stage_item_dir)
        return {
            "enabled": True,
            "status": "dry_run" if self.dry_run else "imported",
            "stage_dir": str(stage_item_dir),
            "stdout": output,
            "summary": self._parse_import_summary(output),
        }

    def _validate_runtime(self) -> None:
        if not DRIVER_JAR.exists():
            raise FileNotFoundError(f"Dameng JDBC driver not found: {DRIVER_JAR}")
        if not STAGE_SCRIPT.exists():
            raise FileNotFoundError(f"Stage script not found: {STAGE_SCRIPT}")
        if not IMPORTER_SOURCE.exists():
            raise FileNotFoundError(f"Importer source not found: {IMPORTER_SOURCE}")
        if not self.settings.db_host or not self.settings.db_user or self.settings.db_password is None:
            raise ValueError("Database import requires METADATA_DB_HOST, METADATA_DB_USER and METADATA_DB_PASSWORD")
        if shutil.which("java") is None:
            raise RuntimeError("java not found in PATH")
        if shutil.which("javac") is None:
            raise RuntimeError("javac not found in PATH")

    def _write_stage_files(self, stage_item_dir: Path, item: dict[str, Any], row_key: str) -> None:
        stage = self._load_stage_module()
        if stage_item_dir.exists():
            shutil.rmtree(stage_item_dir)
        stage_item_dir.mkdir(parents=True, exist_ok=True)

        rows = stage.build_rows(item, row_key)
        for table, table_rows in rows.items():
            stage.write_table(stage_item_dir, table, table_rows)

    def _run_java_importer(self, stage_item_dir: Path) -> str:
        self._compile_importer_if_needed()
        classpath = f"{DRIVER_JAR};{SCRIPTS_DIR}"
        completed = subprocess.run(
            [
                "java",
                "-cp",
                classpath,
                "DmPolicyData11Importer",
                str(stage_item_dir),
                str(self.settings.db_host),
                str(self.settings.db_port),
                str(self.settings.db_user),
                str(self.settings.db_password),
                str(self.dry_run).lower(),
                str(self.force_insert_policies).lower(),
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return completed.stdout.strip()

    def _compile_importer_if_needed(self) -> None:
        if IMPORTER_CLASS.exists() and IMPORTER_CLASS.stat().st_mtime >= IMPORTER_SOURCE.stat().st_mtime:
            return

        subprocess.run(
            [
                "javac",
                "-encoding",
                "UTF-8",
                "-cp",
                str(DRIVER_JAR),
                str(IMPORTER_SOURCE),
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _load_stage_module(self):
        if self._stage_module is not None:
            return self._stage_module

        spec = importlib.util.spec_from_file_location("stage_policy_data11", STAGE_SCRIPT)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load stage script: {STAGE_SCRIPT}")
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("stage_policy_data11", module)
        spec.loader.exec_module(module)
        self._stage_module = module
        return module

    @staticmethod
    def _parse_import_summary(output: str) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        match = re.search(r"insertedPolicies=(\d+), skippedPolicies=(\d+)", output)
        if match:
            summary["insertedPolicies"] = int(match.group(1))
            summary["skippedPolicies"] = int(match.group(2))

        table_stats: dict[str, dict[str, int]] = {}
        for table, inserted, skipped in re.findall(r"([A-Z_]+): inserted=(\d+), skipped=(\d+)", output):
            table_stats[table] = {"inserted": int(inserted), "skipped": int(skipped)}
        if table_stats:
            summary["tables"] = table_stats
        return summary
