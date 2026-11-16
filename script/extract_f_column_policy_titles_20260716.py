import re
from pathlib import Path

import pandas as pd

INPUT = "E:\\policy\\rule\\局重点政策汇总.xlsx"
SHEET = "2026.7.15政策归类"
OUTPUT = Path("E:/demo/exports/f_column_policy_titles_20260716.tsv")


def normalize_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    title = title.replace("（2025-2029 年）", "（2025-2029年）")
    title = title.replace("闽科规〔2022〕５号", "闽科规〔2022〕5号")
    return title.strip(" ：:；;，,。》")


def extract_titles(note: str):
    note = str(note).strip()
    titles = []
    for value in re.findall(r"《([^》]+)》", note):
        titles.append(normalize_title(value))
    malformed = re.search(r"缺少\s+([^《》]+?通知)》", note)
    if malformed:
        titles.insert(0, normalize_title(malformed.group(1)))
    if titles:
        return titles

    patterns = [
        r"^(.*?通知)\s*等.*?(?:附件|漏)",
        r"^(.*?通知).*?附件缺失",
        r"缺少\s+(.+?通知)》",
        r"缺少(?:指导政策|关联政策)[：:]?(.+?)(?:（|\(|；|;|$)",
        r"缺少\s+(.+?)(?:（|\(|《|要求|；|;|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, note)
        if match:
            value = normalize_title(match.group(1))
            if value and value not in ("附件缺失", "原文不完整"):
                return [value]
    return []


def main():
    df = pd.read_excel(INPUT, sheet_name=SHEET, header=0)
    rows = []
    seen = set()
    for idx, row in df.iterrows():
        note = row.iloc[5]
        if pd.isna(note) or not str(note).strip():
            continue
        for title in extract_titles(str(note)):
            key = title
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "EXCEL_ROW": str(idx + 2),
                "SOURCE_POLICY_ID": "" if pd.isna(row.iloc[1]) else str(int(row.iloc[1])),
                "MAIN_POLICY": "" if pd.isna(row.iloc[3]) else str(row.iloc[3]).strip(),
                "MISSING_POLICY_TITLE": title,
                "F_NOTE": str(note).replace("\r", " ").replace("\n", " "),
            })
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as f:
        f.write("EXCEL_ROW\tSOURCE_POLICY_ID\tMAIN_POLICY\tMISSING_POLICY_TITLE\tF_NOTE\n")
        for row in rows:
            f.write("\t".join(row[col] for col in ["EXCEL_ROW", "SOURCE_POLICY_ID", "MAIN_POLICY", "MISSING_POLICY_TITLE", "F_NOTE"]) + "\n")
    print(f"written={OUTPUT} count={len(rows)}")
    for row in rows:
        print(f"{row['EXCEL_ROW']}\t{row['MISSING_POLICY_TITLE']}")


if __name__ == "__main__":
    main()
