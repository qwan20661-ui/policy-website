"""
从 API 获取 isTechSme="是" 的科技型中小企业，保存完整的企业数据到企业测试目录。
"""
import requests
import json
import os
import time
from datetime import datetime


API_URL = "http://112.48.19.225:8500/cms/tb-base-business-info/web/page"
PAGE_SIZE = 100
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "企业测试目录")


def fetch_page(page_number):
    """获取一页数据。"""
    params = {"pageNumber": page_number, "pageSize": PAGE_SIZE}
    try:
        response = requests.get(API_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("data", {})
    except Exception as error:
        print(f"  [错误] 第{page_number}页请求失败: {error}")
        return None


def is_tech_sme(enterprise):
    """判断企业是否是科技型中小企业（isTechSme="是"）。"""
    qualification_list = enterprise.get("honorQualificationList")
    if not qualification_list:
        return False
    for qualification in qualification_list:
        if qualification.get("isTechSme") == "是":
            return True
    return False


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    first_page = fetch_page(1)
    if not first_page:
        print("无法获取第一页数据，退出")
        return

    total_count = first_page.get("totalCount", 0)
    total_pages = (total_count + PAGE_SIZE - 1) // PAGE_SIZE
    print(f"总记录数: {total_count}, 总页数: {total_pages}")

    all_matched = []
    for enterprise in first_page.get("list", []):
        if is_tech_sme(enterprise):
            all_matched.append(enterprise)

    for page in range(2, total_pages + 1):
        print(f"正在获取第 {page}/{total_pages} 页...")
        page_data = fetch_page(page)
        if page_data is None:
            continue
        for enterprise in page_data.get("list", []):
            if is_tech_sme(enterprise):
                all_matched.append(enterprise)
        time.sleep(0.3)

    output_file = os.path.join(OUTPUT_DIR, "tech_sme_enterprises.json")
    result = {
        "totalCount": len(all_matched),
        "fetchTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "enterprises": all_matched,
    }
    with open(output_file, "w", encoding="utf-8") as output_handle:
        json.dump(result, output_handle, ensure_ascii=False, indent=2)

    print(f"\n完成！共找到 {len(all_matched)} 家科技型中小企业")
    print(f"结果已保存到: {output_file}")

    for index, enterprise in enumerate(all_matched, 1):
        name = enterprise.get("enterpriseName", f"unknown_{index}")
        safe_name = "".join(
            character if character.isalnum() or character in " _-" else "_"
            for character in name
        )
        enterprise_file = os.path.join(OUTPUT_DIR, f"{index:04d}_{safe_name}.json")
        with open(enterprise_file, "w", encoding="utf-8") as output_handle:
            json.dump(enterprise, output_handle, ensure_ascii=False, indent=2)

    print(f"已生成 {len(all_matched)} 个单独的企业 JSON 文件到: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
