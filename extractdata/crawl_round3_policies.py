from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import crawl_more_policies as crawler


DEFAULT_URLS = [
    "https://www.xmsme.cn/policy/market/document202305301624074225029",
    "https://www.xmsme.cn/policy/market/50102K3jH3AaOBK",
]


def crawl_urls(urls: list[str], out_root: Path) -> list[dict]:
    crawler.OUT_ROOT = out_root
    crawler.OUT_ROOT.mkdir(parents=True, exist_ok=True)

    manifest = []
    with tempfile.TemporaryDirectory(prefix="policy_round3_") as temp_dir:
        temp_root = Path(temp_dir)
        for index, url in enumerate(urls, start=1):
            raw_path = temp_root / f"page{index}.html"
            crawler.download(url, raw_path)
            page_html = crawler.read_html(raw_path)
            result = crawler.extract_xmsme(page_html, url) if "xmsme.cn" in url else crawler.extract_gov_page(page_html, url)
            manifest.append(crawler.write_result(index, result, raw_path))
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量政策爬取 round3")
    parser.add_argument("--url", action="append", default=[], help="政策详情 URL，可重复传入")
    parser.add_argument("--urls-json", default="", help="JSON 数组格式的 URL 列表")
    parser.add_argument(
        "--out-root",
        default=str(Path(__file__).resolve().parent / "output" / "批量政策爬取_20260717_round3"),
        help="输出根目录",
    )
    parser.add_argument("--write-manifest", action="store_true", help="额外写 manifest.json")
    return parser.parse_args()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()

    urls = list(args.url)
    if args.urls_json:
        urls.extend(json.loads(args.urls_json))
    if not urls:
        urls = DEFAULT_URLS

    out_root = Path(args.out_root)
    manifest = crawl_urls(urls, out_root)
    if args.write_manifest:
        (out_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"success": True, "data": manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
