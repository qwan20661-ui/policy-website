# -*- coding: utf-8 -*-
"""让 tests 能直接 import service.xxx。

老链路依赖 pytest rootdir 恰好等于包目录，v2 显式把包目录加入 sys.path，
这样 `python -m pytest backend/PEMatching_v2/tests` 从仓库根跑也能通。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 测试保持确定性、不逐政策调模型：强制关闭资格评估的模型抽取与独立资格接口。
# 必须在任何 service 模块 import 前设置——service/paths 加载 .env 时不会覆盖已存在的环境变量。
os.environ["PEMATCHING_V2_ELIGIBILITY_LLM_ENABLED"] = "false"
os.environ["PEMATCHING_V2_ELIGIBILITY_ENABLED"] = "false"
os.environ["PEMATCHING_V2_POLICY_SYNC_WORKERS"] = "1"

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))
