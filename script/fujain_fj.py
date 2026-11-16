import requests
from bs4 import BeautifulSoup
import json
import re
import os
import sys
from urllib.parse import urljoin

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)
os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)

METADATA_API_URL = os.environ.get("METADATA_API_URL", "")
METADATA_API_KEY = os.environ.get("METADATA_API_KEY", "")
METADATA_MODEL_NAME = "minimax2.7"

urls = [
    "https://sti.xm.gov.cn/xxgk/tzgg/202504/t20250424_2929438.htm",
]

headers = {"User-Agent": "Mozilla/5.0"}

for url in urls:
    print(f"\n正在处理: {url}")

    session = requests.Session()
    session.trust_env = False
    response = session.get(url, headers=headers, timeout=30, verify=False)
    response.encoding = "utf-8"
    html = response.text

    soup = BeautifulSoup(html, "html.parser")

    title = ""
    title_selectors = [
        ("h1", None), ("div", "article-title"), ("div", "title"),
        ("div", "rules_tit"), ("div", "article_title_group"),
        ("span", "article-title"), ("strong", None), ("title", None),
    ]
    for tag, cls in title_selectors:
        title_tag = soup.find(tag, class_=cls) if cls else soup.find(tag)
        if title_tag:
            t = title_tag.get_text(strip=True)
            if len(t) > 5 and len(t) < 200:
                title = t
                break
    if "_" in title:
        title = title.split("_")[0].strip()
    if not title or len(title) > 150:
        title = "政策文件"
    print(f"标题: {title}")

    content_div = None
    selectors = [("div", "TRS_Editor"), ("div", "pages_content"),
                 ("div", "article"), ("div", "content"), ("div", "page_con")]
    for tag, cls in selectors:
        content_div = soup.find(tag, class_=cls)
        if content_div:
            break

    content = ""
    if content_div:
        for tag in content_div(["script", "style", "img", "video", "audio"]):
            tag.decompose()

        table_markdown_parts = []
        for table in content_div.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue
            grid = []
            for row_idx, tr in enumerate(rows):
                while len(grid) <= row_idx:
                    grid.append([])
                cells = tr.find_all(["td", "th"])
                col_idx = 0
                for cell in cells:
                    while col_idx < len(grid[row_idx]) and grid[row_idx][col_idx] is not None:
                        col_idx += 1
                    text = re.sub(r"\s+", " ", cell.get_text(" ", strip=True))
                    rowspan = int(cell.get("rowspan", 1))
                    colspan = int(cell.get("colspan", 1))
                    for r in range(rowspan):
                        for c in range(colspan):
                            target_row = row_idx + r
                            target_col = col_idx + c
                            while len(grid) <= target_row:
                                grid.append([])
                            while len(grid[target_row]) <= target_col:
                                grid[target_row].append(None)
                            grid[target_row][target_col] = text
                    col_idx += colspan
            if grid:
                max_cols = max(len(row) for row in grid)
                for row in grid:
                    while len(row) < max_cols:
                        row.append("")
                md_lines = []
                md_lines.append("|" + "|".join(grid[0]) + "|")
                md_lines.append("|" + "|".join(["---"] * max_cols) + "|")
                for row in grid[1:]:
                    md_lines.append("|" + "|".join(row) + "|")
                table_md = "\n".join(md_lines)
                table_markdown_parts.append(table_md)
                table.replace_with(f"\n[TABLE_{len(table_markdown_parts)}]\n")

        texts = []
        for p_tag in content_div.find_all("p", recursive=True):
            if p_tag.find_parent("table"):
                continue
            text = re.sub(r"\s+", " ", p_tag.get_text(" ", strip=True))
            if len(text) < 2:
                continue
            text = text.replace("​", "")
            texts.append(text)

        content_text = "\n\n".join(texts)
        for idx, table_md in enumerate(table_markdown_parts, 1):
            content_text = content_text.replace(f"[TABLE_{idx}]", table_md)
        content = content_text

    print(f"正文长度: {len(content)}")

    publish_date = ""
    date_span = soup.find(class_=re.compile(r"article_time|pub-date|pubtime|publish-time|date"))
    if date_span:
        date_text = date_span.get_text(strip=True)
        m = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", date_text)
        if m:
            publish_date = m.group(1)
    if not publish_date:
        for pattern in [r"(\d{4}-\d{2}-\d{2})", r"(\d{4}年\d{1,2}月\d{1,2}日)", r"(\d{4}/\d{1,2}/\d{1,2})"]:
            m = re.search(pattern, html)
            if m:
                publish_date = m.group(1)
                break
    if publish_date:
        m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", publish_date)
        if m:
            publish_date = f"{m.group(1)}年{m.group(2)}月{m.group(3)}日"
        m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", publish_date)
        if m:
            publish_date = f"{m.group(1)}年{m.group(2)}月{m.group(3)}日"
    print(f"发布日期: {publish_date}")

    policy_number = ""
    patterns = [
        r"闽科资〔\d{4}〕\d+号", r"国科发火〔\d{4}〕\d+号", r"国发〔\d{4}〕\d+号",
        r"财税〔\d{4}〕\d+号", r"工信部〔\d{4}〕\d+号", r"科技部〔\d{4}〕\d+号",
        r"财政部〔\d{4}〕\d+号", r"教育部〔\d{4}〕\d+号", r"商务部〔\d{4}〕\d+号",
        r"发改委〔\d{4}〕\d+号", r"厦门市〔\d{4}〕\d+号", r"福建省〔\d{4}〕\d+号",
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            policy_number = m.group()
            break
    print(f"政策文号: {policy_number}")

    attachments = []
    file_exts = [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar"]
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if not href:
            continue
        if any(href.lower().endswith(ext) for ext in file_exts):
            full_url = urljoin(url, href)
            ext = href.split(".")[-1]
            attachments.append({"附件名称": text, "附件链接": full_url, "附件类型": ext})
    unique_attachments = []
    seen = set()
    for item in attachments:
        if item["附件链接"] not in seen:
            seen.add(item["附件链接"])
            unique_attachments.append(item)
    attachments = unique_attachments
    print(f"附件数量: {len(attachments)}")

    safe_title = re.sub(r'[\\/:*?"<>|\n\r\t]', '', title).strip()[:80]
    title_dir = os.path.join("output", safe_title)
    os.makedirs(title_dir, exist_ok=True)

    attachment_dir = os.path.join(title_dir, "attachments")
    os.makedirs(attachment_dir, exist_ok=True)
    for item in attachments:
        try:
            file_url = item["附件链接"]
            file_name = re.sub(r'[\\/:*?"<>|]', '', item["附件名称"])
            if "." not in file_name:
                file_name += "." + item["附件类型"]
            save_path = os.path.join(attachment_dir, file_name)
            r = requests.get(file_url, headers=headers, timeout=60, verify=False)
            with open(save_path, "wb") as f:
                f.write(r.content)
            item["本地路径"] = save_path
            print(f"附件下载成功: {file_name}")
        except Exception as e:
            print(f"附件下载失败: {e}")

    prompt = f"""请从以下政策文本中提取：
1. 发文部门
2. 政策级别
要求：只返回JSON，不要解释，政策级别只能从以下选择：国家级、省级、市级、区县级
输出格式：{{"发文部门": "", "政策级别": ""}}
政策标题：{title}
政策正文：{content[:12000]}"""

    payload = {"model": METADATA_MODEL_NAME, "messages": [{"role": "user", "content": prompt}], "temperature": 0}
    api_headers = {"Content-Type": "application/json", "Authorization": f"Bearer {METADATA_API_KEY}"}
    llm_result = {}
    try:
        llm_response = requests.post(METADATA_API_URL, headers=api_headers, json=payload, timeout=120)
        llm_response.raise_for_status()
        result = llm_response.json()
        content_text = result["choices"][0]["message"]["content"].strip()
        if "```" in content_text:
            content_text = content_text.split("```")[-1].strip()
        json_match = re.search(r"\{[\s\S]*\}", content_text)
        if json_match:
            llm_result = json.loads(json_match.group())
    except Exception as e:
        print(f"LLM调用失败: {e}")

    final_result = {
        "政策标题": title,
        "发布日期": publish_date,
        "政策文号": policy_number,
        "发文部门": llm_result.get("发文部门", ""),
        "政策级别": llm_result.get("政策级别", ""),
        "原文链接": url,
        "正文内容": content,
        "附件列表": attachments
    }

    md_content = f"""# {final_result['政策标题']}

## 基本信息

- 发布日期：{final_result['发布日期']}
- 政策文号：{final_result['政策文号']}
- 发文部门：{final_result['发文部门']}
- 政策级别：{final_result['政策级别']}
- 原文链接：{final_result['原文链接']}

---

# 政策正文

{final_result['正文内容']}

---

# 附件列表

"""
    if attachments:
        for item in attachments:
            md_content += f"- [{item['附件名称']}]({item['附件链接']})\n"
    else:
        md_content += "无附件\n"

    output_md = os.path.join(title_dir, f"{safe_title[:40]}.md")
    with open(output_md, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"Markdown 已保存: {output_md}")

    output_json = os.path.join(title_dir, f"{safe_title[:40]}.json")
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(final_result, f, ensure_ascii=False, indent=4)
    print(f"JSON 已保存: {output_json}")

print(f"\n处理完成，共处理 {len(urls)} 个链接")