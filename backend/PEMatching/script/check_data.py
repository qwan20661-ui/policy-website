import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

with (PROJECT_ROOT / '企业测试' / '科小数据样例.json').open('r', encoding='utf-8') as f:
    d = json.load(f)
print('Years:', list(d.keys()))
for y, companies in d.items():
    print(f'{y}: {len(companies)} companies')
    for name in list(companies.keys())[:3]:
        print(f'  - {name}')
