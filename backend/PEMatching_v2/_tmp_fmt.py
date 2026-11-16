# -*- coding: utf-8 -*-
import json

s = open(r'E:\workspace\kejiju-frontend\backend\PEMatching\config\主政策申报条件.json', encoding='utf-8').read()
print("含 backslash-u 转义:", "\\u" in s)
print("含真实中文:", any('一' <= ch <= '鿿' for ch in s[:3000]))
print("含换行:", "\n" in s[:2000])
print("含2空格缩进:", '  "' in s[:2000])
print("文件长度:", len(s))
d = json.loads(s)
out_ascii = json.dumps(d, ensure_ascii=False, indent=2)
out_orig = json.dumps(d, ensure_ascii=True, indent=2)
print("ensure_ascii=False 长度:", len(out_ascii), "| True 长度:", len(out_orig))
# 推断当前用的 indent 参数：尝试不同 indent 重序列化比较长度
import re
for indent in (None, 2, 4, 0):
    out = json.dumps(d, ensure_ascii=True, indent=indent)
    if abs(len(out) - len(s)) < 2000:
        print("可能 indent =", indent, "长度差:", len(out) - len(s))
