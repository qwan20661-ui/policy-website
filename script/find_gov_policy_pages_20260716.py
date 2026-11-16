import html
import re
import time
import urllib.parse
import urllib.request
from collections import deque

TITLES = [
    "厦门市关于进一步推动企业加大研发投入的若干措施",
    "福建省生物医药产业应用基础平台建设奖补办法",
    "厦门市市级科技计划管理办法",
    "福建省技术转移机构管理办法（修订）",
    "厦门经济特区鼓励留学人员来厦创业工作规定",
    "厦门市促进人口高质量发展若干措施",
    "思明区鼓励科技创新发展办法（修订版）",
    "工业和信息化部科技型企业孵化器管理办法",
    "厦门市重点实验室建设与运行管理办法",
    "厦门市众创空间认定办法",
    "厦门市众创空间管理办法",
    "关于调整国家级科技企业孵化器和国家备案众创空间信息变更管理流程的通知",
    "厦门市促进科技企业孵化器高质量发展实施办法",
    "福建省科技创新领军人才遴选和支持办法（2025-2029年）",
    "福建省科技创新领军人才遴选和支持办法（2025-2029 年）",
    "厦门市关于深入推进科技特派员制度服务乡村振兴的工作方案",
    "厦门市集美区人民政府关于印发集美区产业高质量发展若干措施（暂行）的通知",
    "厦门市集美区人民政府关于修订印发集美区产业高质量发展若干措施（暂行）的通知",
    "福建省人民政府关于加快推进科技创新发展的通知",
    "福建省青年科技人员育成项目支持办法（试行）",
    "关于深化人才发展体制机制改革加快推进人才强市战略的意见",
    "厦门市新引进人才生活补贴实施办法",
    "厦门市优质中小企业梯度培育管理实施细则",
    "厦门市工业和信息化局关于组织开展2026年创新型中小企业评价工作的通知",
    "厦门市科技创新引领工程实施方案",
    "厦门市统筹推动现代产业体系实施方案",
    "中华人民共和国企业所得税法",
]

SEEDS = [
    "https://sti.xm.gov.cn/xxgk/zcfg/",
    "https://sti.xm.gov.cn/xxgk/tzgg/",
    "https://www.xm.gov.cn/zfxxgk/xxgkznml/szfgz/sgfxwj/",
    "https://www.xm.gov.cn/zwgk/flfg/sfwj/",
    "https://www.xm.gov.cn/zwgk/zfxxgk/zfxxgkml/szfwj/",
    "https://kjt.fujian.gov.cn/xxgk/zfxxgkzl/zfxxgkzc/gfxwj/",
    "https://kjt.fujian.gov.cn/xxgk/tzgg/",
    "https://www.fujian.gov.cn/zwgk/zxwj/szfwj/",
    "https://gxt.xm.gov.cn/xwzx/tzgg/",
    "https://gxt.xm.gov.cn/xxgk/zfxxgk/xxgkml/gfxwj/",
    "https://www.siming.gov.cn/zfxxgkzl/gbmxxgk/kjhxxhj/zfxxgkml/gfxwj/",
    "https://www.siming.gov.cn/xxgk/tzgg/",
    "https://www.jimei.gov.cn/zfxxgkzl/gbmxxgk/kjhxxhj/zfxxgkml/gfxwj/",
    "https://www.jimei.gov.cn/xxgk/zdxxgk/zdjsxm/",
    "https://www.miit.gov.cn/zwgk/zcwj/wjfb/tz/",
    "https://www.gov.cn/zhengce/zhengceku/",
]

ALLOWED_HOSTS = (
    "sti.xm.gov.cn",
    "www.xm.gov.cn",
    "kjt.fujian.gov.cn",
    "www.fujian.gov.cn",
    "gxt.xm.gov.cn",
    "www.siming.gov.cn",
    "www.jimei.gov.cn",
    "www.miit.gov.cn",
    "www.gov.cn",
)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
    ctype = resp.headers.get_content_charset() or "utf-8"
    return data.decode(ctype, "ignore")


def clean_text(text):
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text))


def links(url, text):
    for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\']', text, re.I):
        href = urllib.parse.urljoin(url, html.unescape(match.group(1)))
        parsed = urllib.parse.urlparse(href)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc not in ALLOWED_HOSTS:
            continue
        if any(href.lower().endswith(ext) for ext in (".jpg", ".png", ".gif", ".css", ".js", ".mp4")):
            continue
        yield urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def index_pages(seed):
    yield seed
    for i in range(1, 12):
        yield urllib.parse.urljoin(seed, f"index_{i}.htm")


def main():
    title_hits = {title: [] for title in TITLES}
    queue = deque()
    for seed in SEEDS:
        for page in index_pages(seed):
            queue.append((page, 0))
    seen = set()
    while queue and len(seen) < 700:
        url, depth = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        try:
            text = fetch(url)
        except Exception:
            continue
        body = clean_text(text)
        for title in TITLES:
            title_norm = title.replace("（2025-2029 年）", "（2025-2029年）")
            body_norm = body.replace("（2025-2029 年）", "（2025-2029年）")
            if title in body or title_norm in body_norm:
                title_hits[title].append(url)
                print(f"HIT\t{title}\t{url}", flush=True)
        if depth < 1:
            for href in links(url, text):
                path = urllib.parse.urlparse(href).path
                if re.search(r"/t20\d{6}_|/art/20\d{4}/|/20\d{4}/", path):
                    queue.append((href, depth + 1))
        time.sleep(0.05)
    print("SUMMARY")
    for title, urls in title_hits.items():
        if not urls:
            print(f"MISS\t{title}")


if __name__ == "__main__":
    main()
