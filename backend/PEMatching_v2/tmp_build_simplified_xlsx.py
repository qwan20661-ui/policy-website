# -*- coding: utf-8 -*-
"""从 Top20推荐链路证据.xlsx 生成面向客户展示的简化版：
   00_阅读说明 / 01_推荐总览 / 02_命中规则链路 / 03_企业规则证据 / 04_政策标签证据
   核心目标：把「推荐政策 → 命中规则 → 规则命中依据(企业侧) → 政策标签(政策侧)」的链路连起来。
"""
import argparse
import glob
import json
import os
import re
from pathlib import Path
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

def parse_args():
    parser = argparse.ArgumentParser(description='从详细链路证据 Excel 生成简版报告')
    parser.add_argument('--input-xlsx', required=True, type=Path,
                        help='export_top20_evidence.py 生成的详细证据 Excel')
    parser.add_argument('--output-xlsx', required=True, type=Path,
                        help='简版报告 Excel 输出路径')
    parser.add_argument('--match-dir', type=Path, default=None,
                        help='对应的逐企业匹配 JSON 批次目录；用于读取企业标签证据')
    return parser.parse_args()


args = parse_args()
SRC = str(args.input_xlsx)
OUT = str(args.output_xlsx)
if not os.path.isfile(SRC):
    raise SystemExit(f'详细证据 Excel 不存在：{SRC}')

wb = openpyxl.load_workbook(SRC, data_only=True)


def rows(ws):
    hd = [c.value for c in ws[1]]
    out = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        if all(v is None for v in r):
            continue
        out.append(dict(zip(hd, r)))
    return out


recs = rows(wb['推荐证据'])
rule_rows = rows(wb['企业规则证据'])
tag_rows = rows(wb['政策标签证据'])
base_rows = rows(wb['基础评分证据'])
excl_rows = rows(wb['排除项证据'])
bonus_rows = rows(wb['附加分证据'])

# ---- 索引 ----
matched_by_ent = {}
stats_by_ent = {}
for d in rule_rows:
    ent = str(d.get('企业名称') or '')
    st = str(d.get('状态') or '').strip()
    stats = stats_by_ent.setdefault(ent, {'matched': 0, 'unmatched': 0, 'skipped': 0, 'notrun': 0})
    if st == 'matched':
        stats['matched'] += 1
        matched_by_ent.setdefault(ent, []).append(d)
    elif st == 'unmatched':
        stats['unmatched'] += 1
    elif st == 'skipped':
        stats['skipped'] += 1
    elif '未执行' in st:
        stats['notrun'] += 1
for ent in matched_by_ent:
    matched_by_ent[ent].sort(key=lambda d: str(d.get('规则ID')))

tags_by_pid = {}
for d in tag_rows:
    pid = str(d.get('政策ID') or '').strip()
    tags_by_pid.setdefault(pid, []).append(d)

recs.sort(key=lambda d: (str(d.get('源文件')), int(d.get('排名') or 0)))
# 过滤：只保留有命中规则的政策（matchedRules 非空），去掉仅凭通用评分推荐的
recs_kept = [d for d in recs if str(d.get('命中规则') or '').strip()]
rec_pids = sorted({str(d['政策ID']) for d in recs_kept}, key=int)

# ---- 企业画像索引：直接从 04_全量匹配批次 的原始 JSON 读取企业数据 ----
_batch_dir = os.path.dirname(SRC)
# 批次文件夹形如 "20260804_125016_测试企业名称1-5"，时间戳是前两段（日期_时刻）
_ts = '_'.join(os.path.basename(_batch_dir).split('_')[:2])
_json_dir = str(args.match_dir) if args.match_dir else os.path.join(
    os.path.dirname(os.path.dirname(_batch_dir)), '04_全量匹配批次', _ts
)
if not os.path.isdir(_json_dir):
    raise SystemExit(f'匹配结果目录不存在：{_json_dir}；请使用 --match-dir 指定逐企业 JSON 批次目录')
_src_by_ent = {}
for d in recs:
    _src_by_ent.setdefault(d.get('企业名称'), str(d.get('源文件')))
json_paths = {os.path.basename(p): p for p in glob.glob(os.path.join(_json_dir, '*.json'))}


def load_enterprise(ent):
    path = json_paths.get(_src_by_ent.get(ent))
    if not path:
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            obj = json.load(f)
        return (obj.get('data') or {}).get('enterprise') or {}
    except Exception:
        return {}


ent_names = sorted(_src_by_ent.keys())

# ---- 原始录入企业数据（PEMatching/企业测试目录） ----
_raw_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'PEMatching', '企业测试目录')


def load_raw_enterprise(ent):
    src = _src_by_ent.get(ent) or ''
    raw_name = src.replace('_json.json', '.json') if src else ''
    path = os.path.join(_raw_dir, raw_name)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def latest_operation(raw):
    ops = raw.get('operationInfoList') or []
    ops = sorted(ops, key=lambda r: str(r.get('year', '')), reverse=True)
    return ops[0] if ops else {}


def honor_row(raw):
    h = (raw.get('honorQualificationList') or [{}])
    return h[0] if h else {}


def tag_row(pid, dim):
    for d in tags_by_pid.get(pid, []):
        if str(d.get('维度') or '').strip() == dim:
            return d
    return None


def split_tags(text):
    if text is None:
        return set()
    if isinstance(text, (list, tuple, set)):
        return {str(s).strip() for s in text if str(s).strip()}
    return {s.strip() for s in str(text).replace('，', '、').replace(',', '、').split('、') if s.strip()}


def parse_signals(sig):
    obj = {}
    if isinstance(sig, str):
        try:
            obj = json.loads(sig) if sig.strip() else {}
        except Exception:
            obj = {}
    elif isinstance(sig, dict):
        obj = sig
    a = split_tags(obj.get('aTags'))
    c = split_tags(obj.get('cTags'))
    kws = obj.get('keywords') or []
    return a, c, kws


CSUB_NAME = {'C1.1': '新型显示', 'C1.2': '集成电路', 'C1.3': '软件开发', 'C10.1': '人工智能'}
B_NAME = {'B1': '初创期', 'B2': '成长期', 'B3': '成熟期'}
C_NAME = {'C1': '电子信息', 'C2': '生物与新医药', 'C3': '航空航天', 'C4': '新材料',
          'C5': '高技术服务', 'C6': '新能源与节能', 'C7': '资源与环境',
          'C8': '先进制造与自动化', 'C9': '通用行业', 'C10': '未来产业', 'C11': '数字经济'}


def fmt_tags(tag_str, name_map):
    """把 ['B2','B3'] 或 'B2、B3' 之类标签翻译成 'B2 成长期、B3 成熟期'。"""
    if tag_str is None:
        return '—'
    if isinstance(tag_str, (list, tuple)):
        items = [str(t).strip() for t in tag_str if str(t).strip()]
    else:
        items = [t.strip() for t in str(tag_str).replace('，', '、').split('、') if t.strip()]
    parts = []
    for t in items:
        if t in ('None', ''):
            continue
        parts.append(f'{t} {name_map.get(t, "")}'.strip())
    return '、'.join(parts) if parts else '—'


def fmt_list(v):
    if v is None:
        return '—'
    if isinstance(v, (list, tuple)):
        return '、'.join(str(x) for x in v) or '—'
    return str(v) if str(v).strip() not in ('', 'None') else '—'


def yes_no(v):
    if v is None:
        return '未知'
    s = str(v).strip()
    if v is True or s in ('True', '是', '1', 'yes', 'YES', 'true'):
        return '是'
    if v is False or s in ('False', '否', '0', 'no', 'NO', 'false'):
        return '否'
    return '未知'


def zh_rule_basis(rid, detail):
    """把规则命中的技术判断详情翻译成中文（面向客户）。"""
    detail = detail or ''
    rg = lambda pat, d=detail: (re.search(pat, d).group(1) if re.search(pat, d) else '')
    _n = lambda s: re.sub(r'\.0+$', '', s or '')   # 108.0 → 108；2399.52 保留小数
    if rid == 'R05':
        return '企业处于初创期（B1）'
    if rid == 'R06':
        return '企业处于成长期（B2）'
    if rid == 'R07':
        cs = rg(r"交集=\[([^\]]*)\]").replace("'", '')
        ip = _n(rg(r"ipTotal=([\d.]+)"))
        return f'企业行业属于高企八大领域（{cs}），且知识产权 {ip} 件（≥5件）'
    if rid == 'R09':
        val = rg(r"cSub=\[['\"]?([^'\]]+)") or 'C1.3'
        nm = CSUB_NAME.get(val, '')
        return f'企业经营范围属软件开发行业（{val}{nm and f"·{nm}"}）'
    if rid == 'R13':
        r = rg(r"含\s*([^\]']+)").strip()
        return f'企业注册地位于 {r}'
    if rid == 'R14':
        r = rg(r"交集=\[([^\]]*)\]").replace("'", '')
        return f'企业注册地属行政区（{r}），适用区级配套政策'
    if rid == 'R15':
        emp = _n(rg(r"employeeCount=([\d.]+)"))
        return f'员工人数 {emp} 人（≤300人）'
    if rid == 'R21':
        parts = []
        if 'isTechSme=True' in detail:
            parts.append('已是市高企培育库企业（科小）')
        if 'isHighTechEnterprise=False' in detail:
            parts.append('尚未认定为国家级高企')
        return '；'.join(parts) or '已入库市高企培育库、未认定国高'
    if rid == 'R22':
        return '已是国家级高新技术企业'
    if rid == 'R30':
        emp = _n(rg(r"employeeCount=([\d.]+)"))
        rev = _n(rg(r"revenueWan=([\d.]+)"))
        return f'员工 {emp} 人（≤300人），营收 {rev} 万元（≤3亿元）'
    if rid == 'R31':
        emp = _n(rg(r"employeeCount=([\d.]+)"))
        return f'员工人数 {emp} 人（≤1000人）'
    if rid == 'R32':
        return '已是科技型中小企业（科小）'
    if rid == 'R33':
        n = _n(rg(r"newRecruitCount=([\d.]+)"))
        return f'当年新增参保人员 {n} 人'
    if rid == 'R34':
        emp = _n(rg(r"employeeCount=([\d.]+)"))
        return f'有参保职工 {emp} 人'
    return detail


# ---- 样式 ----
HDR_FILL = PatternFill(fill_type='solid', fgColor='4472C4')
HDR_FONT = Font(bold=True, color='FFFFFF', size=10)
THIN = Side(style='thin', color='BFBFBF')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(vertical='top', wrap_text=True)
CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)
WARN_FILL = PatternFill(fill_type='solid', fgColor='FDE9E9')


def style_header(ws, row, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.alignment = CENTER
        cell.border = BORDER


def write_table(ws, start_row, headers, rows_, widths, center_cols=(), band_fill=None):
    for j, h in enumerate(headers, 1):
        ws.cell(row=start_row, column=j, value=h)
    style_header(ws, start_row, len(headers))
    r = start_row + 1
    for i, row_ in enumerate(rows_):
        for j, v in enumerate(row_, 1):
            cell = ws.cell(row=r, column=j, value=v)
            cell.border = BORDER
            cell.alignment = CENTER if j in center_cols else WRAP
            if band_fill:
                _f = band_fill(i)
                if _f:
                    cell.fill = _f
        r += 1
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(j)].width = w
    return r


out = openpyxl.Workbook()

# ---- 00 阅读说明 ----
ws = out.active
ws.title = '00_阅读说明'
guide = [
    ('本表说明：为什么推荐这些政策？', ''),
    ('推荐逻辑一句话：', '系统先给企业画像打标签 → 用「规则」判断企业符合哪些条件（规则命中）→ '
     '命中规则的信号标签与政策身上的标签做交集 → 按五项评分+时效分排序推荐。'),
    ('', ''),
    ('六个工作表怎么读：', ''),
    ('01 企业信息', '被匹配企业的基本信息，直接读取原始企业数据(JSON)：行业、发展阶段、区域、规模、知产、资质及判定依据。'),
    ('02 推荐总览', '每家企业推荐的前 20 条政策，含分数、档位和命中规则清单。'),
    ('过滤说明', '本表已过滤掉「无命中规则」的政策——即没有任何规则信号为它背书、仅凭通用评分进入推荐的政策，'
     '因为无法向客户解释它为什么被推荐。仅展示有明确命中规则、能讲清推荐理由的政策。'),
    ('03 命中规则链路（核心）', '逐条展示「政策 ← 命中规则 ← 规则命中依据(企业数据) ← 政策标签」的完整链路，'
     '说明这条政策为什么被推荐、因为命中什么规则。'),
    ('04 企业规则证据', '这家企业命中的全部规则及其判断依据（为什么满足条件）。'),
    ('05 政策标签证据', '被推荐政策自身的标签（支持方式/适用阶段/行业等）及打标依据。'),
    ('06 评分明细', '每条推荐的总分是怎么算出来的：五项基础分（领域/阶段/创新/规模/信用）逐项得分、'
     '被剔除项、有效权重、归一化基础分、附加分（时效性），以及总分。'),
    ('', ''),
    ('总分数怎么算：', ''),
    ('公式', '总分数 = 基础分 + 附加分。'),
    ('基础分', '五项加权：领域匹配度30 / 发展阶段20 / 创新活跃度25 / 规模适配15 / 信用记录10。'
     '基础分 = Σ实际得分 ÷ 有效权重 × 100；有效权重 = 100 − 字段缺失被剔除项的权重。'
     '例：10+15+25+15=65，剔除信用(10)后有效权重=90，基础分 = 65÷90×100 ≈ 72.22。'),
    ('创新活跃度分档', '知识产权 ≥10件 25分 / ≥5件 15分 / ≥1件 8分 / 确认0件 0分。'),
    ('附加分', '目前只有时效性：申报未开始 15分 / 申报进行中(含常年受理) 10分 / 上一年度 8分。'),
    ('档位', '总分≥85 → 直接推送(direct_push)；60~85 → 需补资料(needs_supplement)。'),
    ('', ''),
    ('常见符号：', ''),
    ('A1~A8', '政策支持方式：A1资质认定 / A2财税优惠 / A3资金奖补 / A4人才 / A5金融支持 / A6载体平台 / A7成果转化 / A8科技合作。'),
    ('B1~B3', '企业/政策发展阶段：B1初创 / B2成长期 / B3成熟期。'),
    ('C9', '通用行业（全行业适用）。'),
    ('命中交集', '规则信号标签 与 政策标签 的交集，是「规则→政策」挂钩的依据。'),
    ('评分明细·合计行', '加粗浅蓝底的一行，展示该项的总分 = 基础分 + 附加分的完整算式。'),
]
r = 1
for k, v in guide:
    ws.cell(row=r, column=1, value=k).font = Font(bold=True, size=11) if k and not k.startswith(('01', '02', '03', '04', '05', '06')) else Font(bold=True)
    ws.cell(row=r, column=2, value=v).alignment = WRAP
    r += 1
ws.column_dimensions['A'].width = 24
ws.column_dimensions['B'].width = 110

# ---- 01 企业信息（原始录入数据 + 系统标签，中文展示） ----
def tag_basis_zh(e):
    tb = e.get('tagBasis') or {}
    parts = []
    for st in tb.get('B') or []:
        sigs = '、'.join(str(s).replace('isTechSme', '科小').replace('isHighTechEnterprise', '国高')
                         for s in (st.get('signals') or []))
        parts.append(f"{st.get('stage', '')}阶段：{sigs}")
    c = tb.get('C')
    if c:
        if isinstance(c, list):
            c = '、'.join(str(x) for x in c)
        parts.append(f"行业：{c}")
    return '；'.join(parts) if parts else '—'


def fmt_num(v):
    if v is None:
        return '—'
    if isinstance(v, (int, float)):
        return re.sub(r'\.0+$', '', str(v))
    s = str(v)
    return s if s.strip() not in ('', 'None') else '—'


def wan(v):
    """原始经营数据单位是元，转成万元展示。"""
    if v is None:
        return '—'
    try:
        return re.sub(r'\.0+$', '', str(round(float(v) / 10000, 2)))
    except Exception:
        return '—'


def fmt_date(v):
    return str(v)[:10] if v else '—'


ws = out.create_sheet('01_企业信息')
headers = ['企业名称', '行业（原始录入）', '行业标签（系统判定）', '阶段标签（系统判定）',
           '注册地址', '注册区域', '注册类型', '成立时间',
           '数据年度', '员工人数', '研发人员数', '年营收(万元)', '研发投入(万元)', '净资产(万元)',
           '新增参保(人)', '一类知产(件)', '二类知产(件)',
           '科技型中小企业', '国家级高企', '创新型中小企业', '专精特新', '小巨人', '科技领军(瞪羚)',
           '标签判定说明', '数据完整度']
widths = [16, 26, 20, 20, 30, 14, 20, 12, 10, 10, 10, 12, 12, 12, 12, 12, 12,
          14, 14, 14, 14, 12, 16, 72, 12]
ent_rows = []
for ent in ent_names:
    e = load_enterprise(ent)          # 批次结果 JSON 的 data.enterprise（含标签）
    raw = load_raw_enterprise(ent)    # 原始录入 JSON
    op = latest_operation(raw)
    hon = honor_row(raw)
    tags = e.get('tags') or {}
    dc = e.get('dataCompleteness') or {}
    ent_rows.append([
        raw.get('enterpriseName') or e.get('enterpriseName') or ent,
        ' / '.join(str(x) for x in (raw.get('industryLevel1'), raw.get('industryLevel2')) if x) or '—',
        fmt_tags(tags.get('C'), C_NAME),
        fmt_tags(tags.get('B'), B_NAME),
        raw.get('registerAddress') or '—',
        raw.get('registerDistrict') or fmt_list(tags.get('regions')),
        raw.get('registerType') or '—',
        fmt_date(raw.get('registerDate')),
        op.get('year') or '—',
        fmt_num(op.get('employeeCount')),
        fmt_num(op.get('rdPersonnelCount')),
        wan(op.get('operatingIncome')),
        wan(op.get('rdExpense')),
        wan(op.get('netAssets')),
        fmt_num(op.get('newRecruitCount')),
        fmt_num(raw.get('ipClass1Count')),
        fmt_num(raw.get('ipClass2Count')),
        yes_no(hon.get('isTechSme')),
        yes_no(hon.get('isHighTechEnterprise')),
        yes_no(hon.get('isInnovativeSme')),
        yes_no(hon.get('isSpecializedSme')),
        yes_no(hon.get('isSpecializedLittleGiant')),
        yes_no(hon.get('isTechLeaderEnterprise')),
        tag_basis_zh(e),
        f"{dc.get('available', 0)}/{dc.get('total', 0)}",
    ])
write_table(ws, 1, headers, ent_rows, widths, center_cols=(9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 25))
ws.freeze_panes = 'A2'

# ---- 02 推荐总览 ----
ws = out.create_sheet('02_推荐总览')
headers = ['企业名称', '排名', '政策标题', '政策ID', '总分', '档位', '命中规则', '申报开始', '申报截止', '时效档位', '时效分', '推荐理由']
widths = [14, 6, 34, 9, 8, 14, 26, 12, 12, 12, 8, 70]
rows_ = []
for d in recs_kept:
    rows_.append([
        d.get('企业名称'), d.get('排名'), d.get('政策标题'), d.get('政策ID'), d.get('总分'),
        d.get('档位'), d.get('命中规则'), d.get('申报开始'), d.get('申报截止'),
        d.get('时效档位'), d.get('时效分'), d.get('推荐理由'),
    ])
write_table(ws, 1, headers, rows_, widths, center_cols=(2, 4, 5, 9, 10, 11))
ws.freeze_panes = 'A2'

# ---- 03 命中规则链路 ----
ws = out.create_sheet('03_命中规则链路')
headers = ['企业名称', '排名', '政策标题', '总分', '规则ID', '规则名称',
           '规则命中依据（中文）', '规则信号', '命中交集（信号↔政策标签）',
           '政策标签A(支持方式)', '政策标签B(阶段)', '政策标签C(行业)', '政策标签依据（简要）', '链路阶段']
widths = [14, 6, 30, 8, 8, 22, 46, 34, 26, 22, 20, 12, 60, 16]
rows_ = []
warn_idx = []


def basis_short(pid, dim):
    tr = tag_row(pid, dim)
    if not tr:
        return '—'
    vals = tr.get('标签值') or ''
    conf = tr.get('置信度') or ''
    b = str(tr.get('打标依据') or '')
    return f"{vals}（{conf}）{b[:70]}{'…' if len(b) > 70 else ''}"


def rule_basis_short(pid):
    a = basis_short(pid, 'A')
    c = basis_short(pid, 'C')
    b = basis_short(pid, 'B')
    return f"A:{a}；B:{b}；C:{c}"


for d in recs_kept:
    ent = d.get('企业名称')
    pid = str(d.get('政策ID'))
    rank = d.get('排名')
    title = d.get('政策标题')
    score = d.get('总分')
    rule_ids = [s.strip() for s in str(d.get('命中规则') or '').replace('、', ',').split(',') if s.strip()]
    # 政策 A/C 标签
    pa = split_tags((tag_row(pid, 'A') or {}).get('标签值'))
    pc = split_tags((tag_row(pid, 'C') or {}).get('标签值'))
    tagA = str((tag_row(pid, 'A') or {}).get('标签值') or '—') + '（' + str((tag_row(pid, 'A') or {}).get('置信度') or '') + '）'
    tagB = str((tag_row(pid, 'B') or {}).get('标签值') or '—') + '（' + str((tag_row(pid, 'B') or {}).get('置信度') or '') + '）'
    tagC = str((tag_row(pid, 'C') or {}).get('标签值') or '—') + '（' + str((tag_row(pid, 'C') or {}).get('置信度') or '') + '）'
    base_short = rule_basis_short(pid)

    if not rule_ids:
        rows_.append([ent, rank, title, score, '—', '无命中规则（通用评分推荐）',
                      'matchedRules=[]：该政策未与任何命中规则的信号标签产生交集，仅凭通用四项评分进入推荐',
                      '—', '—', tagA, tagB, tagC, base_short, '—'])
        warn_idx.append(len(rows_) - 1)
        continue
    for rid in rule_ids:
        m = next((x for x in matched_by_ent.get(ent, []) if str(x.get('规则ID')) == rid), None)
        if not m:
            rows_.append([ent, rank, title, score, rid, '（规则证据未找到）', '—', '—', '—', tagA, tagB, tagC, base_short, '—'])
            continue
        rule_a, rule_c, kws = parse_signals(m.get('聚合信号'))
        inter_a = sorted(rule_a & pa)
        inter_c = sorted(rule_c & pc)
        inter_txt = '；'.join([f"规则{a} ↔ 政策{a}" for a in inter_a] + [f"规则{c} ↔ 政策{c}" for c in inter_c]) or '—（无标签交集）'
        kw_txt = '、'.join(str(k) for k in (kws or [])[:8])
        sig_txt = 'A标签:' + (', '.join(sorted(rule_a)) or '—') + '；关键词:' + (kw_txt or '—')
        chain = str(m.get('链路阶段') or '')
        if str(m.get('链路加成')) == 'True':
            chain = chain or '（链路规则）'
        rows_.append([ent, rank, title, score, rid, m.get('规则名称'), zh_rule_basis(rid, m.get('判断详情')),
                      sig_txt, inter_txt, tagA, tagB, tagC, base_short, chain])

end = write_table(ws, 1, headers, rows_, widths, center_cols=(2, 4, 5),
                  band_fill=(lambda i: WARN_FILL if i in set(warn_idx) else None))
# 高亮无命中规则行（标红字）
for i in warn_idx:
    r = 2 + i
    ws.cell(row=r, column=6).font = Font(bold=True, color='C00000')
# 合并政策块首列
i = 0
base = 2
while i < len(rows_):
    ent, rank, title = rows_[i][0], rows_[i][1], rows_[i][2]
    j = i + 1
    while j < len(rows_) and rows_[j][0] == ent and rows_[j][1] == rank and rows_[j][2] == title:
        j += 1
    if j - i > 1:
        for col in (1, 2, 3, 4):
            ws.merge_cells(start_row=base + i, start_column=col, end_row=base + j - 1, end_column=col)
    i = j
ws.freeze_panes = 'A2'

# ---- 04 企业规则证据（仅命中） ----
ws = out.create_sheet('04_企业规则证据')
headers = ['企业名称', '规则ID', '规则名称', '通道', '优先级', '规则命中依据（中文）', '聚合信号', '政策包描述', '链路加成', '链路阶段']
widths = [14, 8, 24, 8, 8, 50, 44, 40, 8, 18]
rows_ = []
for ent in sorted(matched_by_ent.keys()):
    for m in matched_by_ent[ent]:
        rows_.append([ent, m.get('规则ID'), m.get('规则名称'), m.get('通道'), m.get('优先级'),
                      zh_rule_basis(m.get('规则ID'), m.get('判断详情')), m.get('聚合信号'), m.get('政策包描述'),
                      '是' if str(m.get('链路加成')) == 'True' else '', m.get('链路阶段') or ''])
write_table(ws, 1, headers, rows_, widths, center_cols=(1, 2, 4, 5, 9))
ws.freeze_panes = 'A2'

# ---- 05 政策标签证据（仅被推荐政策） ----
ws = out.create_sheet('05_政策标签证据')
headers = ['政策ID', '政策标题', '维度', '标签值', '置信度', '打标依据']
widths = [10, 36, 10, 22, 10, 100]
rows_ = []
for pid in rec_pids:
    title = next((d.get('政策标题') for d in recs if str(d.get('政策ID')) == pid), '')
    for tr in tags_by_pid.get(pid, []):
        dim = str(tr.get('维度') or '')
        if dim in ('A', 'B', 'C', 'D', 'E', 'F'):
            rows_.append([pid, title, dim, tr.get('标签值'), tr.get('置信度'), tr.get('打标依据')])
write_table(ws, 1, headers, rows_, widths, center_cols=(1, 3, 5))
ws.freeze_panes = 'A2'

# ---- 06 评分明细 ----
ws = out.create_sheet('06_评分明细')
headers = ['企业名称', '排名', '政策标题', '总分', '计分项', '权重', '得分', '计分依据', '有效权重', '原始得分', '基础分', '附加分', '说明']
widths = [14, 6, 32, 8, 18, 8, 8, 62, 10, 10, 10, 10, 44]

base_by_key, excl_by_key, bonus_by_key = {}, {}, {}
for d in base_rows:
    base_by_key.setdefault((d.get('企业名称'), str(d.get('政策ID'))), []).append(d)
for d in excl_rows:
    excl_by_key.setdefault((d.get('企业名称'), str(d.get('政策ID'))), []).append(d)
for d in bonus_rows:
    bonus_by_key.setdefault((d.get('企业名称'), str(d.get('政策ID'))), []).append(d)

score_rows = []
score_blocks = []  # (start_idx, end_idx)
for d in recs_kept:
    ent, pid, rank = d.get('企业名称'), str(d.get('政策ID')), d.get('排名')
    title, total = d.get('政策标题'), d.get('总分')
    items = base_by_key.get((ent, pid), [])
    excl = excl_by_key.get((ent, pid), [])
    bonus = bonus_by_key.get((ent, pid), [])
    eff = (items[0] or {}).get('有效权重') if items else None
    raw = (items[0] or {}).get('原始得分') if items else None
    base_norm = round(raw / eff * 100, 2) if raw is not None and eff else None
    bonus_total = (bonus[0] or {}).get('封顶后合计') if bonus else 0
    capped = '封顶' if any(str(b.get('是否封顶')) == 'True' for b in bonus) else ''
    start = len(score_rows)
    for it in items:
        score_rows.append([ent, rank, title, total, it.get('计分项'), it.get('权重'), it.get('得分'),
                           it.get('计分依据'), eff, raw, base_norm, bonus_total, ''])
    for it in excl:
        score_rows.append([ent, rank, title, total, it.get('计分项') + '（剔除）', it.get('权重'), '—',
                           it.get('计分依据'), eff, raw, base_norm, bonus_total, '字段缺失→剔除分母，不计入实际得分'])
    for b in bonus:
        score_rows.append([ent, rank, title, total, '附加·' + str(b.get('附加项')), '', b.get('得分'),
                           b.get('依据'), '', '', '', bonus_total, ''])
    note = f"总分数 = 基础分{base_norm} + 附加分{bonus_total}" + (f"（{capped}）" if capped else "")
    score_rows.append([ent, rank, title, total, '★ 合计', '', '', '', eff, raw, base_norm, bonus_total, note])
    end = len(score_rows) - 1
    if end > start:
        score_blocks.append((start, end))

write_table(ws, 1, headers, score_rows, widths, center_cols=(2, 4, 6, 7, 9, 10, 11, 12))
# 合计行加粗、浅灰底
for s, e in score_blocks:
    for r in range(2 + s, 2 + e + 1):
        if ws.cell(row=r, column=5).value == '★ 合计':
            for c in range(1, 14):
                ws.cell(row=r, column=c).font = Font(bold=True)
                ws.cell(row=r, column=c).fill = PatternFill(fill_type='solid', fgColor='E7EEF7')
    if e > s:
        for col in (1, 2, 3, 4):
            ws.merge_cells(start_row=2 + s, start_column=col, end_row=2 + e, end_column=col)
ws.freeze_panes = 'A2'

saved_path = None
out_parent = os.path.dirname(OUT)
if out_parent:
    os.makedirs(out_parent, exist_ok=True)
for cand in [OUT, OUT.replace('.xlsx', '_新.xlsx'), OUT.replace('.xlsx', '_新2.xlsx')]:
    try:
        out.save(cand)
        saved_path = cand
        break
    except PermissionError:
        continue
if saved_path:
    print('saved:', saved_path)
    if saved_path != OUT:
        print('主文件名被占用（可能已在 Excel 打开），已另存为:', saved_path)
else:
    print('!! 所有候选文件名均被占用，请关闭 Excel 中打开的旧文件后重试')
print('sheets:', out.sheetnames)
print('05_评分明细 数据行:', len(score_rows))
