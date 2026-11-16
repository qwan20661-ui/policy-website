# -*- coding: utf-8 -*-
import json, io
cfg = json.load(io.open("config/申报条件管理.json", encoding="utf-8"))
print("top type:", type(cfg).__name__)
if isinstance(cfg, dict):
    for k, v in cfg.items():
        print("  %-24s %s" % (k, type(v).__name__ + ("[%d]"%len(v) if isinstance(v,(list,dict)) else "")))
    data = cfg.get("data") or cfg.get("fields") or cfg.get("list")
else:
    data = cfg
if isinstance(data, list):
    print("\n条目数:", len(data))
    print("首条 keys:", list(data[0]))
    print("\n=== 全部标准字段 ===")
    for i, e in enumerate(data, 1):
        print("  %2d. %-14s / %-16s / %-22s  coreField=%s" % (
            i, str(e.get("level1Category"))[:14], str(e.get("level2Category"))[:16],
            str(e.get("level3Category") or e.get("name"))[:22], str(e.get("coreField"))[:44]))
