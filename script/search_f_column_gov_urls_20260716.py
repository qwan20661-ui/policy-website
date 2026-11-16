import csv
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

INPUT = Path("E:/demo/exports/f_column_policy_titles_20260716.tsv")
OUTPUT = Path("E:/demo/exports/f_column_gov_url_candidates_20260716.tsv")

SITE_CODES = ["3502000001", "3500000001"]


def post_search(title: str, site_code: str):
    data = urllib.parse.urlencode({
        "qt": title,
        "siteCode": site_code,
        "tab": "all",
        "page": "1",
        "pageSize": "10",
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.so-gov.cn/query/s",
        data=data,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.xm.gov.cn",
            "Referer": "https://www.xm.gov.cn/so/s",
        },
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def strip_html(value):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value or "")).strip()


def score(title, doc):
    data = doc.get("data") or {}
    title_o = strip_html(data.get("titleO") or data.get("title") or "")
    url = data.get("url") or ""
    my = data.get("myValues") or {}
    quick = strip_html(my.get("QUICKDESCRIPTION") or data.get("summary") or "")
    s = 0
    if "POLICY" in (data.get("dataType") or []):
        s += 100
    if title == title_o:
        s += 80
    if title in title_o:
        s += 60
    if title in quick:
        s += 30
    if "政策解读" in title_o or "视频解读" in title_o or "意见征集" in title_o:
        s -= 80
    if ".gov.cn" in url or "xm.gov.cn" in url or "fujian.gov.cn" in url:
        s += 10
    return s, title_o


def attachments(doc):
    data = doc.get("data") or {}
    my = data.get("myValues") or {}
    att = my.get("ATTACHMENTS")
    if isinstance(att, dict):
        return json.dumps(att, ensure_ascii=False)
    return ""


def main():
    rows = list(csv.DictReader(INPUT.open("r", encoding="utf-8-sig"), delimiter="\t"))
    output = []
    fields = None
    out_file = OUTPUT.open("w", encoding="utf-8", newline="")
    writer = None
    for row in rows:
        title = row["MISSING_POLICY_TITLE"].strip()
        best = None
        for site_code in SITE_CODES:
            try:
                result = post_search(title, site_code)
            except Exception as exc:
                # Try the next site code; record only when every site code fails.
                continue
            docs = list(result.get("resultDocs") or [])
            for docs_key in ("suggests",):
                suggests = result.get(docs_key) or {}
                for values in suggests.values():
                    if isinstance(values, list):
                        docs.extend({"data": v} for v in values)
            for doc in docs:
                s, found_title = score(title, doc)
                data = doc.get("data") or {}
                cand = {
                    **row,
                    "STATUS": "FOUND" if s >= 50 else "LOW_SCORE",
                    "SITE_CODE": site_code,
                    "FOUND_TITLE": found_title,
                    "URL": data.get("url") or "",
                    "SCORE": str(s),
                    "ATTACHMENTS_JSON": attachments(doc),
                    "ERROR": "",
                }
                if best is None or s > int(best["SCORE"]):
                    best = cand
            if best and int(best["SCORE"]) >= 80:
                break
            time.sleep(0.2)
        if best:
            out_row = best
        else:
            out_row = {**row, "STATUS": "NOT_FOUND", "SITE_CODE": "", "FOUND_TITLE": "", "URL": "", "SCORE": "0", "ATTACHMENTS_JSON": "", "ERROR": ""}
        output.append(out_row)
        if writer is None:
            fields = list(out_row.keys())
            writer = csv.DictWriter(out_file, fields, delimiter="\t")
            writer.writeheader()
        writer.writerow(out_row)
        out_file.flush()
        print(f"{out_row['STATUS']}\t{out_row['SCORE']}\t{out_row['MISSING_POLICY_TITLE']}\t{out_row['FOUND_TITLE']}\t{out_row['URL']}", flush=True)
        time.sleep(0.3)
    out_file.close()


if __name__ == "__main__":
    main()
