import html
import re
import sys
import time
import urllib.parse
import urllib.request

QUERIES = [
    "厦门市关于进一步推动企业加大研发投入的若干措施",
    "福建省生物医药产业应用基础平台建设奖补办法",
    "福建省技术转移机构管理办法（修订） 闽科规〔2022〕5号",
    "厦门经济特区鼓励留学人员来厦创业工作规定",
    "厦门市促进人口高质量发展若干措施",
    "思明区鼓励科技创新发展办法（修订版）",
    "工业和信息化部科技型企业孵化器管理办法",
    "厦门市重点实验室建设与运行管理办法",
    "厦门市众创空间认定办法",
    "厦门市促进科技企业孵化器高质量发展实施办法",
    "福建省科技创新领军人才遴选和支持办法（2025-2029年）",
    "厦门市关于深入推进科技特派员制度服务乡村振兴的工作方案",
    "福建省人民政府关于加快推进科技创新发展的通知 闽政〔2023〕7号",
    "福建省青年科技人员育成项目支持办法（试行）",
    "厦门市新引进人才生活补贴实施办法 厦人社〔2024〕239号",
    "厦门市优质中小企业梯度培育管理实施细则 厦工信规〔2023〕2号",
    "厦门市工业和信息化局关于组织开展2026年创新型中小企业评价工作的通知",
]

GOV_MARKERS = (
    "gov.cn",
    "xm.gov.cn",
    "fujian.gov.cn",
    "miit.gov.cn",
    "siming.gov.cn",
    "jimei.gov.cn",
)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read()
    return data.decode("utf-8", "ignore")


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub("<.*?>", "", s))).strip()


def bing(query: str):
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
    text = fetch(url)
    seen = set()
    # Bing often wraps URLs as direct hrefs; keep this parser simple and inspect manually.
    for match in re.finditer(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', text, re.I | re.S):
        href = html.unescape(match.group(1))
        title = clean(match.group(2))
        if href in seen:
            continue
        seen.add(href)
        if any(marker in href for marker in GOV_MARKERS):
            print(f"{query}\t{title}\t{href}")


def main():
    for q in QUERIES:
        try:
            bing(q)
        except Exception as exc:
            print(f"ERR\t{q}\t{exc}", file=sys.stderr)
        time.sleep(0.8)


if __name__ == "__main__":
    main()
