import json
from pathlib import Path

# 直接加载政策文件
policy_file = Path('policydata/厦门市科技型中小企业评价.json')
with open(policy_file, 'r', encoding='utf-8') as f:
    policy = json.load(f)

profile = policy.get('申报条件聚类', {})

def flatten_policy_profile(profile):
    """
    递归遍历政策画像，提取申报条件
    - 有 source_text 且不为空的条件
    - 有 value 值且为非空字符串的条件
    """
    conditions = []
    
    def traverse(data, current_path=()):
        if isinstance(data, dict):
            for key, value in data.items():
                new_path = current_path + (key,)
                
                if isinstance(value, dict):
                    source_text = value.get("source_text", "")
                    val = value.get("value", "")
                    
                    # 有 source_text，使用 source_text 作为政策原文
                    if source_text and str(source_text).strip():
                        conditions.append({
                            "name": f"{'.'.join(new_path[1:])}",
                            "text": str(source_text).strip(),
                            "value": str(val).strip() if isinstance(val, str) else str(val),
                            "unit": str(value.get("unit", ""))
                        })
                    else:
                        # 没有 source_text，检查 value 是否为非空字符串
                        if isinstance(val, str) and val.strip():
                            conditions.append({
                                "name": f"{'.'.join(new_path[1:])}",
                                "text": str(val).strip(),
                                "value": str(val).strip(),
                                "unit": str(value.get("unit", ""))
                            })
                        else:
                            # 继续递归遍历子节点
                            traverse(value, new_path)
                elif isinstance(value, str) and value.strip():
                    # 字符串值，直接作为申报条件
                    conditions.append({
                        "name": f"{'.'.join(new_path[1:])}",
                        "text": str(value).strip(),
                        "value": str(value).strip(),
                        "unit": ""
                    })
    
    traverse(profile)
    return conditions

conds = flatten_policy_profile(profile)
print(f'提取到 {len(conds)} 个申报条件:\n')
for i, c in enumerate(conds, 1):
    print(f"{i}. {c['name']}")
    print(f"   政策原文: {c['text'][:80]}")
    print()
