import os
import re
import json
import requests
from pathlib import Path

# =========================
# LLM API配置
# =========================
LLM_API_URL = os.environ.get("LLM_API_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = "minimax2.7"

# =========================
# 路径配置
# =========================
INPUT_DIR = Path(r"e:/PEMatching/Enterprisedata")
OUTPUT_DIR = Path(r"e:/PEMatching/Enterprisedata")
OUTPUT_DIR.mkdir(exist_ok=True)


# =========================
# 调用LLM提取结构化信息
# =========================
def extract_enterprise_info(md_text, source_file):
    """
    使用LLM提取企业信息
    """

    result_template = {
        "企业名称": "",
        "填表日期": "",
        "注册地址": "",
        "经营地址": "",
        "联系人": "",
        "联系电话": "",
        "法定代表人": "",
        "所属领域": "",

        "知识产权": {
            "发明专利": 0,
            "实用新型专利": 0,
            "软件著作权": 0,
            "外观专利": 0
        },

        "人员情况": {
            "员工总数": 0,
            "研发人员数": 0,
            "高级职称": 0,
            "博士": 0,
            "硕士": 0,
            "本科": 0,
            "大专及以下": 0
        },

        "近3年研发投入(万元)": {
            "2021年": 0,
            "2022年": 0,
            "2023年": 0
        },

        "近3年营业收入(万元)": {
            "2021年": 0,
            "2022年": 0,
            "2023年": 0
        },

        "主要研发项目": [],

        "企业介绍": "",

        "源文件": source_file
    }

    prompt = f"""你是一个专业的企业信息抽取助手。

请从HTML表格文本中提取企业信息，并严格输出JSON。

要求：
1. 只能输出JSON
2. 不允许输出markdown
3. 不允许解释
4. 不允许补充不存在的信息
5. 缺失字段用空字符串或0
6. 主要研发项目输出数组
7. 数值字段只保留数字
8. 日期保持原格式

输出格式如下：
{json.dumps(result_template, ensure_ascii=False, indent=2)}

HTML表格文本：
{md_text}
"""

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "你是一个严谨的企业信息抽取助手，只输出合法JSON。"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0
    }

    response = requests.post(
        LLM_API_URL,
        headers=headers,
        json=payload,
        timeout=300
    )

    response.raise_for_status()

    result = response.json()

    content = result["choices"][0]["message"]["content"]

    # 去除markdown代码块
    content = re.sub(r"```json\s*", "", content)
    content = re.sub(r"```\s*", "", content).strip()

    # 提取JSON部分（从第一个{到最后一个}）
    json_start = content.find('{')
    json_end = content.rfind('}') + 1
    if json_start >= 0 and json_end > json_start:
        content = content[json_start:json_end]

    # 处理换行符
    content = content.replace('\n', ' ').replace('\r', ' ')

    # 替换各种引号
    content = content.replace('"', '"').replace('"', '"')  # 中文双引号
    content = content.replace(''', "'").replace(''', "'")  # 中文单引号
    content = content.replace('"', '"').replace('"', '"')  # 全角双引号
    content = content.replace(''', "'").replace(''', "'")  # 全角单引号

    try:
        parsed_json = json.loads(content)
        return parsed_json

    except Exception as e:
        print("JSON解析失败：", e)
        print(content)

        return {
            "error": "JSON解析失败",
            "raw_content": content
        }


# =========================
# 主流程
# =========================
def process_file(md_path):
    """
    处理单个MD文件
    """

    print(f"\n正在处理: {md_path.name}")

    try:
        # 读取MD文件
        with open(md_path, "r", encoding="utf-8") as f:
            md_text = f.read()

        print("文件读取完成")

        # 提取结构化信息
        result = extract_enterprise_info(
            md_text,
            md_path.name
        )

        # 保存JSON
        json_path = OUTPUT_DIR / f"{md_path.stem}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f"JSON已保存: {json_path}")

    except Exception as e:
        print(f"处理失败: {md_path.name}")
        print(e)


# =========================
# 批量处理
# =========================
def main():

    md_files = list(INPUT_DIR.glob("*.md"))

    print(f"共发现 {len(md_files)} 个MD文件")

    for md_path in md_files:
        process_file(md_path)

    print("\n全部处理完成")


# =========================
# 启动
# =========================
if __name__ == "__main__":
    main()