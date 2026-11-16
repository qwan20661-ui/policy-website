from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
XLSX = Path(r"D:/app/慧企云申报项目_POLICY_TITLE匹配结果.xlsx")
POLICY_CATALOG = ROOT / "tmp" / "huiqiyun_policy_catalog.tsv"
EXISTING_FUSION = ROOT / "tmp" / "huiqiyun_existing_fusion.tsv"
EXISTING_DOCS = ROOT / "tmp" / "huiqiyun_existing_documents.tsv"
OUT_JSON = ROOT / "tmp" / "huiqiyun_import_precheck.json"
OUT_MD = ROOT / "tmp" / "huiqiyun_import_precheck.md"


def text(value) -> str:
    return "" if value is None else str(value).strip()


def norm_title(value: str) -> str:
    value = text(value)
    value = value.replace("（", "(").replace("）", ")")
    value = value.replace("《", "").replace("》", "")
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[，,。；;：:、·“”\"'‘’\[\]【】<>＜＞]", "", value)
    return value.lower()


def strip_suffix_filename(value: str) -> str:
    value = text(value)
    value = re.sub(r"^\d{4}[-_/年]\d{1,2}[-_/月]\d{1,2}日?[_ -]*", "", value)
    value = re.sub(r"\.(md|txt|pdf|docx?|wps)$", "", value, flags=re.I)
    return value


def strip_tail_parenthetical(value: str) -> str:
    value = text(value).replace("（", "(").replace("）", ")")
    return re.sub(r"\([^()]{1,30}\)$", "", value).strip()


def clean_huiqiyun_noise(value: str) -> str:
    value = text(value)
    value = re.sub(r"\s*[—–-]\s*慧企云\s*$", "", value)
    value = re.sub(r"\s+慧企云\s*$", "", value)
    return value.strip()


def basis_variants(value: str) -> list[str]:
    cleaned = clean_huiqiyun_noise(value)
    variants = [cleaned]
    book_titles = re.findall(r"《([^》]+)》", cleaned)
    variants.extend(book_titles)
    no_year = re.sub(r"^\d{4}年(?:度)?", "", cleaned).strip()
    variants.append(no_year)
    variants.append(strip_tail_parenthetical(cleaned))
    variants.append(strip_tail_parenthetical(no_year))
    if "—" in cleaned:
        variants.append(cleaned.split("—", 1)[0].strip())
    if "--" in cleaned:
        variants.append(cleaned.split("--", 1)[0].strip())
    result = []
    seen = set()
    for item in variants:
        item = text(item)
        key = norm_title(item)
        if item and key and key not in seen:
            result.append(item)
            seen.add(key)
    return result


def bigrams(value: str) -> set[str]:
    value = norm_title(value)
    if len(value) < 2:
        return {value} if value else set()
    return {value[i : i + 2] for i in range(len(value) - 1)}


def classify_attribute(title: str) -> str:
    title = text(title)
    if re.search(r"公示|名单|结果|公布|认定名单|兑现名单|拟.*名单", title):
        return "结果公告"
    if re.search(r"申报|申请|开展|受理|征集|备案|兑付|兑现|补助项目|奖励项目", title):
        return "申报通知"
    if re.search(r"实施细则|申报指南|操作规程|管理办法|暂行办法|试行办法|工作指南|办事指南", title):
        return "实施细则"
    return "指导政策"


def load_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def load_excel_rows() -> list[dict[str, str]]:
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    ws = wb["匹配结果"]
    headers = [text(c.value) for c in next(ws.iter_rows(min_row=1, max_row=1))]
    rows = []
    for row_no, values in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        item = {headers[i]: text(values[i]) if i < len(values) else "" for i in range(len(headers))}
        item["_row"] = row_no
        rows.append(item)
    return rows


def build_policy_indexes(catalog: list[dict[str, str]]):
    by_id = {}
    norm_index = defaultdict(list)
    for row in catalog:
        pid = text(row.get("ID"))
        if not pid:
            continue
        by_id[pid] = row
        variants = {
            text(row.get("POLICY_TITLE")),
            strip_suffix_filename(row.get("FILE_NAME")),
        }
        row["_norm_title"] = norm_title(row.get("POLICY_TITLE"))
        row["_norm_file"] = norm_title(strip_suffix_filename(row.get("FILE_NAME")))
        row["_search_norm"] = row["_norm_title"] + "|" + row["_norm_file"]
        row["_bigrams"] = bigrams(row.get("POLICY_TITLE")) | bigrams(strip_suffix_filename(row.get("FILE_NAME")))
        for variant in variants:
            n = norm_title(variant)
            if n:
                norm_index[n].append(row)
    return by_id, norm_index


def unique_by_id(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    out = []
    for row in rows:
        pid = text(row.get("ID"))
        if pid and pid not in seen:
            out.append(row)
            seen.add(pid)
    return out


def public_candidate(row: dict[str, str], **extra) -> dict[str, str]:
    result = {
        "ID": text(row.get("ID")),
        "POLICY_TITLE": text(row.get("POLICY_TITLE")),
        "FILE_NAME": text(row.get("FILE_NAME")),
        "PUBLISH_DATE": text(row.get("PUBLISH_DATE")),
        "ISSUE_DEPARTMENT": text(row.get("ISSUE_DEPARTMENT")),
        "POLICY_CODE": text(row.get("POLICY_CODE")),
        "FILE_PATH": text(row.get("FILE_PATH")),
    }
    result.update(extra)
    return result


def score_candidate(query: str, candidate: dict[str, str], query_core: str) -> tuple[float, str]:
    variants = basis_variants(query)
    variants.append(query_core)
    q_values = [norm_title(x) for x in variants if norm_title(x)]
    titles = [
        text(candidate.get("POLICY_TITLE")),
        strip_suffix_filename(candidate.get("FILE_NAME")),
    ]
    best = (0.0, "")
    for raw in titles:
        c = norm_title(raw)
        if not c:
            continue
        for q in q_values:
            if c == q:
                return (1.0, "清洗后标题/文件名完全一致")
            if q and q in c:
                best = max(best, (0.93, "数据库标题包含B列清洗标题"))
            if c and c in q:
                best = max(best, (0.88, "B列清洗标题包含数据库标题"))
            ratio = SequenceMatcher(None, q, c).ratio()
            if ratio >= 0.82:
                best = max(best, (ratio, "标题相似度较高"))
    return best


def match_basis_title(title: str, catalog: list[dict[str, str]], norm_index) -> dict:
    title = text(title)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", title):
        return {"status": "missing", "method": "datetime_value", "candidates": [], "score": 0}
    variants = basis_variants(title)
    for variant in variants:
        exact = unique_by_id(norm_index.get(norm_title(variant), []))
        if len(exact) == 1:
            return {"status": "unique", "method": "clean_exact", "candidates": [public_candidate(exact[0])], "score": 1.0}
        if len(exact) > 1:
            return {"status": "ambiguous", "method": "clean_exact", "candidates": [public_candidate(x) for x in exact[:10]], "score": 1.0}

    q_values = [norm_title(x) for x in variants if norm_title(x)]
    q_bigrams = set()
    for variant in variants:
        q_bigrams |= bigrams(variant)
    quick = []
    for candidate in catalog:
        search_norm = candidate.get("_search_norm", "")
        if any(q and q in search_norm for q in q_values):
            quick.append((1.0, candidate))
            continue
        c_bigrams = candidate.get("_bigrams", set())
        if not q_bigrams or not c_bigrams:
            continue
        overlap = len(q_bigrams & c_bigrams)
        ratio = overlap / max(1, min(len(q_bigrams), len(c_bigrams)))
        if ratio >= 0.46:
            quick.append((ratio, candidate))
    quick.sort(key=lambda x: -x[0])

    scored = []
    for _, candidate in quick[:120]:
        score, reason = score_candidate(title, candidate, variants[0] if variants else title)
        if score >= 0.86:
            scored.append((score, reason, candidate))
    scored.sort(key=lambda x: (-x[0], int(x[2].get("ID") or 0)))
    dedup = []
    seen = set()
    for score, reason, candidate in scored:
        pid = text(candidate.get("ID"))
        if pid in seen:
            continue
        dedup.append((score, reason, candidate))
        seen.add(pid)
    if not dedup:
        return {"status": "missing", "method": "none", "candidates": [], "score": 0}
    top_score = dedup[0][0]
    top = [x for x in dedup if top_score - x[0] <= 0.015]
    if len(top) == 1 and top_score >= 0.86:
        return {"status": "unique", "method": top[0][1], "candidates": [public_candidate(top[0][2])], "score": top_score}
    return {
        "status": "ambiguous",
        "method": "similarity",
        "candidates": [public_candidate(x[2], _score=f"{x[0]:.4f}", _reason=x[1]) for x in dedup[:10]],
        "score": top_score,
    }


def main():
    rows = load_excel_rows()
    catalog = load_tsv(POLICY_CATALOG)
    existing_fusion = load_tsv(EXISTING_FUSION)
    existing_docs = load_tsv(EXISTING_DOCS)
    by_id, norm_index = build_policy_indexes(catalog)

    fusion_by_title = defaultdict(list)
    for f in existing_fusion:
        if text(f.get("DEL_FLAG")) == "1":
            continue
        for key in {norm_title(f.get("DIRECTORY_TITLE")), norm_title(f.get("MAIN_FILE_TITLE"))}:
            if key:
                fusion_by_title[key].append(f)

    existing_doc_keys = {
        (text(d.get("FUSION_POLICY_ID")), text(d.get("SOURCE_POLICY_ID")))
        for d in existing_docs
        if text(d.get("DEL_FLAG")) != "1" and text(d.get("FUSION_POLICY_ID")) and text(d.get("SOURCE_POLICY_ID"))
    }

    unique_b_titles = sorted({text(r.get("文件依据")) for r in rows if text(r.get("文件依据"))})
    b_matches = {title: match_basis_title(title, catalog, norm_index) for title in unique_b_titles}

    d_checks = []
    plan_rows = []
    main_seen = {}
    doc_seen = set()
    for r in rows:
        main_title = text(r.get("政策标题"))
        main_key = norm_title(main_title)
        if main_key not in main_seen:
            main_seen[main_key] = {
                "mainTitle": main_title,
                "excelRows": [],
                "existingFusionCandidates": fusion_by_title.get(main_key, []),
            }
        main_seen[main_key]["excelRows"].append(r["_row"])

        d_pid = text(r.get("匹配POLICY_ID"))
        d_title = text(r.get("匹配POLICY_TITLE全称"))
        d_policy = by_id.get(d_pid)
        d_ok = bool(d_policy)
        d_title_match = False
        d_reason = ""
        if d_policy:
            db_title = text(d_policy.get("POLICY_TITLE"))
            if norm_title(db_title) == norm_title(d_title):
                d_title_match = True
                d_reason = "D列标题与POLICY标题完全一致"
            elif norm_title(d_title) in norm_title(db_title) or norm_title(db_title) in norm_title(d_title):
                d_title_match = True
                d_reason = "D列标题与POLICY标题互相包含"
            else:
                sim = SequenceMatcher(None, norm_title(d_title), norm_title(db_title)).ratio()
                d_title_match = sim >= 0.86
                d_reason = "相似度%.4f" % sim
        d_checks.append({
            "excelRow": r["_row"],
            "mainTitle": main_title,
            "policyId": d_pid,
            "excelTitle": d_title,
            "exists": d_ok,
            "titleMatch": d_title_match,
            "reason": d_reason,
            "dbTitle": text(d_policy.get("POLICY_TITLE")) if d_policy else "",
            "confidence": text(r.get("匹配置信度")),
        })
        if d_ok and d_title_match:
            key = (main_key, d_pid)
            if key not in doc_seen:
                plan_rows.append({
                    "source": "D列",
                    "excelRow": r["_row"],
                    "mainTitle": main_title,
                    "policyId": d_pid,
                    "policyTitle": text(d_policy.get("POLICY_TITLE")),
                    "policyAttribute": classify_attribute(text(d_policy.get("POLICY_TITLE"))),
                    "confidence": text(r.get("匹配置信度")),
                    "matchMethod": "C列ID校验通过",
                })
                doc_seen.add(key)

        b_title = text(r.get("文件依据"))
        b_match = b_matches.get(b_title)
        if b_match and b_match["status"] == "unique":
            b_policy = b_match["candidates"][0]
            b_pid = text(b_policy.get("ID"))
            key = (main_key, b_pid)
            if key not in doc_seen:
                plan_rows.append({
                    "source": "B列",
                    "excelRow": r["_row"],
                    "mainTitle": main_title,
                    "policyId": b_pid,
                    "policyTitle": text(b_policy.get("POLICY_TITLE")),
                    "policyAttribute": classify_attribute(text(b_policy.get("POLICY_TITLE"))),
                    "confidence": "",
                    "matchMethod": b_match["method"],
                    "basisTitle": b_title,
                })
                doc_seen.add(key)

    b_status_counts = Counter(m["status"] for m in b_matches.values())
    d_missing = [x for x in d_checks if not x["exists"]]
    d_bad_title = [x for x in d_checks if x["exists"] and not x["titleMatch"]]
    existing_main_exact = {k: v for k, v in main_seen.items() if v["existingFusionCandidates"]}
    new_main_exact = {k: v for k, v in main_seen.items() if not v["existingFusionCandidates"]}

    result = {
        "sourceFile": str(XLSX),
        "stats": {
            "excelRows": len(rows),
            "uniqueMainTitles": len(main_seen),
            "duplicateMainTitleGroups": sum(1 for v in main_seen.values() if len(v["excelRows"]) > 1),
            "uniqueBasisTitles": len(unique_b_titles),
            "basisMatchUnique": b_status_counts.get("unique", 0),
            "basisMatchAmbiguous": b_status_counts.get("ambiguous", 0),
            "basisMatchMissing": b_status_counts.get("missing", 0),
            "dRows": len(d_checks),
            "dIdMissing": len(d_missing),
            "dTitleMismatch": len(d_bad_title),
            "existingMainTitleMatches": len(existing_main_exact),
            "newMainTitles": len(new_main_exact),
            "confirmedPlanRows": len(plan_rows),
            "confirmedPlanRowsBySource": dict(Counter(r["source"] for r in plan_rows)),
            "confirmedPlanRowsByAttribute": dict(Counter(r["policyAttribute"] for r in plan_rows)),
            "alreadyExistingDocumentKeysIfReuseExistingFusion": sum(
                1 for pr in plan_rows
                for f in fusion_by_title.get(norm_title(pr["mainTitle"]), [])
                if (text(f.get("ID")), pr["policyId"]) in existing_doc_keys
            ),
        },
        "basisMatches": b_matches,
        "dChecks": d_checks,
        "mainPolicies": list(main_seen.values()),
        "confirmedPlanRows": plan_rows,
    }

    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = []
    s = result["stats"]
    lines.append("# 慧企云申报项目入库前校验")
    lines.append("")
    lines.append("## 总体统计")
    for k, v in s.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## B列文件依据匹配概况")
    lines.append(f"- 唯一确认: {s['basisMatchUnique']} / {s['uniqueBasisTitles']}")
    lines.append(f"- 多候选: {s['basisMatchAmbiguous']} / {s['uniqueBasisTitles']}")
    lines.append(f"- 找不到: {s['basisMatchMissing']} / {s['uniqueBasisTitles']}")
    lines.append("")
    lines.append("## D列ID校验概况")
    lines.append(f"- D列行数: {s['dRows']}")
    lines.append(f"- ID不存在: {s['dIdMissing']}")
    lines.append(f"- ID存在但标题不匹配: {s['dTitleMismatch']}")
    lines.append("")
    lines.append("## B列多候选样例")
    for title, match in list((x for x in b_matches.items() if x[1]["status"] == "ambiguous"))[:30]:
        lines.append(f"### {title}")
        lines.append(f"- method: {match['method']}, score: {match['score']}")
        for cand in match["candidates"][:5]:
            lines.append(f"  - ID={cand.get('ID')} | {cand.get('POLICY_TITLE')} | {cand.get('PUBLISH_DATE')}")
    lines.append("")
    lines.append("## B列找不到样例")
    for title, match in list((x for x in b_matches.items() if x[1]["status"] == "missing"))[:80]:
        lines.append(f"- {title}")
    lines.append("")
    lines.append("## D列异常")
    for item in (d_missing + d_bad_title)[:80]:
        lines.append(f"- Excel行{item['excelRow']} POLICY_ID={item['policyId']} exists={item['exists']} titleMatch={item['titleMatch']} | {item['excelTitle']} | DB={item['dbTitle']} | {item['reason']}")
    lines.append("")
    lines.append("## 可确认入库关系样例")
    for item in plan_rows[:80]:
        lines.append(f"- [{item['source']}] {item['mainTitle']} -> {item['policyId']} {item['policyTitle']} ({item['policyAttribute']})")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(result["stats"], ensure_ascii=False, indent=2))
    print("json=" + str(OUT_JSON))
    print("md=" + str(OUT_MD))


if __name__ == "__main__":
    main()
