"""根据测试企业和实时规则表生成可核对的模型请求示例。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from service.semantic_match_service import (
    SEMANTIC_LLM_MODEL_NAME,
    SEMANTIC_SYSTEM_PROMPT_FILE,
    _build_prompt,
    _model_input_candidates,
    _policy_items,
    _prompt_candidate_snapshot,
)


DOCS_DIR = PROJECT_ROOT / "docs"
POLICY_ID = "6501"
ITEM_ID = "3-1"


def main() -> None:
    enterprise = json.loads((DOCS_DIR / "测试企业数据.json").read_text(encoding="utf-8"))
    response = requests.get(
        f"http://127.0.0.1:8080/api/fusion-classification/fusion-detail?fusionPolicyId={POLICY_ID}",
        timeout=10,
    )
    response.raise_for_status()
    detail = response.json()["data"]
    profile = detail["ruleTableJson"]
    policy = {
        "id": detail.get("id", POLICY_ID),
        "title": (detail.get("policyInfo") or {}).get("policyTitle") or detail.get("directoryTitle", ""),
    }
    selected_items = [item for item in _policy_items(profile, policy) if item["itemId"] == ITEM_ID]
    if len(selected_items) != 1:
        raise RuntimeError(f"未找到唯一规则项 {ITEM_ID}，实际数量：{len(selected_items)}")

    candidates = _prompt_candidate_snapshot(enterprise, selected_items, _model_input_candidates(enterprise))
    user_payload = json.loads(_build_prompt(enterprise, policy, profile, items=selected_items, candidates=candidates))
    request_payload = {
        "model": SEMANTIC_LLM_MODEL_NAME,
        "messages": [
            {"role": "system", "content": SEMANTIC_SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":"))},
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    (DOCS_DIR / f"模型提示词请求数据_{POLICY_ID}_{ITEM_ID}.json").write_text(
        json.dumps(user_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (DOCS_DIR / f"模型完整请求数据_{POLICY_ID}_{ITEM_ID}.json").write_text(
        json.dumps(request_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
