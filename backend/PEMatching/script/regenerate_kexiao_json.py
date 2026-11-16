"""
从科小数据样例.xlsx重新提取数据到科小数据样例.json
正确处理2023/2024/2025三年的不同列结构
"""
import openpyxl
import json
from pathlib import Path

# ============================
# 2025/2024 列映射（双层表头，合并单元格结构）
# ============================
COL_2025 = {
    '企业名称': 2, '统一社会信用代码': 3, '注册日期': 4,
    '注册地': 5, '地址': 6, '一级行业': 7, '二级行业': 8,
    '填报年份': 9,
    '指标1_注册地': 10, '指标2_企业规模': 11, '指标3_产品及服务范围': 12,
    '指标4_企业信用': 13, '指标5_高新技术企业': 14, '指标6_研发机构': 15,
    '指标7_科技奖励': 16, '指标8_制定标准': 17,
    '是否享受加计扣除': 18, '享受加计扣除金额(元)': 19,
    '总得分': 20,
    '科技人员占企业职工总数比例': 21,
    '研发费用占销售收入总额比例': 22,
    '研发费用占成本费用支出总额比例': 23,
    'I类知识产权数量': 24, 'II类知识产权数量': 25,
    '资产总额(万元)': 26, '净资产(万元)': 27, '成本费用总额(万元)': 28,
    '销售收入(万元)': 29, '主营业务收入(万元)': 30,
    '研发费用总额(万元)': 31, '利润总额(万元)': 32, '纳税总额(万元)': 33,
    '职工总数': 34, '在职职工人数': 35, '兼职职工人数': 36, '临时职工人数': 37,
    '科技人员总数': 38, '科技人员在职人数': 39, '科技人员兼职人数': 40, '科技人员临时人数': 41,
    '本年新增人数': 42, '吸纳高校毕业生人数': 43,
    '博士人数': 44, '硕士人数': 45, '本科人数': 46, '大专及以下人数': 47,
    '高级职称人数': 48, '中级职称人数': 49, '初级职称人数': 50, '高级技工人数': 51,
}

# ============================
# 2023 列映射（单层表头，无合并单元格，比2025多一个"企业注册地"列在col4）
# ============================
COL_2023 = {
    '序号': 1, '企业名称': 2, '统一社会信用代码': 3,
    '企业注册地': 4, '地址': 5, '注册日期': 6,
    '一级行业': 7, '二级行业': 8,
    '填报年份': 9, '入库日期': 10, '总得分': 11,
    '科技人员占企业职工总数比例': 12,
    '研发费用占销售收入总额比例': 13,
    '研发费用占成本费用支出总额比例': 14,
    'I类知识产权数量': 15, 'II类知识产权数量': 16,
    '指标1_注册地': 17, '指标2_企业规模': 18, '指标3_产品及服务范围': 19,
    '指标4_企业信用': 20, '指标5_高新技术企业': 21, '指标6_研发机构': 22,
    '指标7_科技奖励': 23, '指标8_制定标准': 24,
    '资产总额(万元)': 25, '净资产(万元)': 26, '成本费用总额(万元)': 27,
    '销售收入(万元)': 28, '主营业务收入(万元)': 29,
    '研发费用总额(万元)': 30, '利润总额(万元)': 31, '纳税总额(万元)': 32,
    '企业职工总数': 33, '企业在职人数': 34, '企业兼职人数': 35, '企业临时人数': 36,
    '企业科技人员总数': 37, '企业科技人员中在职人数': 38, '企业科技人员中兼职人数': 39, '企业科技人员中临时人数': 40,
    '本年新增人数': 41, '吸纳高校毕业生人数': 42,
    '企业博士人数': 43, '企业硕士人数': 44, '企业本科人数': 45, '企业大专及以下人数': 46,
    '企业中高级职称人数': 47, '企业中中级职称人': 48, '企业中初级职称人数': 49, '企业中高级技工人数': 50,
    '是否享受加计扣除': 51, '享受加计扣除金额(元)': 52,
}


def safe_num(v):
    try:
        return float(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def read_sheet_2025(ws):
    companies = []
    for row in range(3, ws.max_row + 1):
        name = ws.cell(row, COL_2025['企业名称']).value
        if not name:
            continue
        year_val = ws.cell(row, COL_2025['填报年份']).value
        if year_val:
            year = str(int(year_val)) if isinstance(year_val, (int, float)) else str(year_val)
        else:
            continue

        def g(key):
            return ws.cell(row, COL_2025[key]).value

        company = {
            '企业名称': name,
            '统一社会信用代码': str(g('统一社会信用代码') or ''),
            '注册日期': str(g('注册日期') or ''),
            '注册地': str(g('注册地') or ''),
            '地址': str(g('地址') or ''),
            '一级行业': str(g('一级行业') or ''),
            '二级行业': str(g('二级行业') or ''),
            '填报年份': int(year),
            '指标1_注册地': str(g('指标1_注册地') or ''),
            '指标2_企业规模': str(g('指标2_企业规模') or ''),
            '指标3_产品及服务范围': str(g('指标3_产品及服务范围') or ''),
            '指标4_企业信用': str(g('指标4_企业信用') or ''),
            '指标5_高新技术企业': str(g('指标5_高新技术企业') or ''),
            '指标6_研发机构': str(g('指标6_研发机构') or ''),
            '指标7_科技奖励': str(g('指标7_科技奖励') or ''),
            '指标8_制定标准': str(g('指标8_制定标准') or ''),
            '是否享受加计扣除': str(g('是否享受加计扣除') or ''),
            '享受加计扣除金额(元)': safe_num(g('享受加计扣除金额(元)')),
            '总得分': safe_num(g('总得分')),
            '科技人员占企业职工总数比例': safe_num(g('科技人员占企业职工总数比例')),
            '研发费用占销售收入总额比例': safe_num(g('研发费用占销售收入总额比例')),
            '研发费用占成本费用支出总额比例': safe_num(g('研发费用占成本费用支出总额比例')),
            'I类知识产权数量': int(safe_num(g('I类知识产权数量'))),
            'II类知识产权数量': int(safe_num(g('II类知识产权数量'))),
            '资产总额(万元)': safe_num(g('资产总额(万元)')),
            '净资产(万元)': safe_num(g('净资产(万元)')),
            '成本费用总额(万元)': safe_num(g('成本费用总额(万元)')),
            '销售收入(万元)': safe_num(g('销售收入(万元)')),
            '主营业务收入(万元)': safe_num(g('主营业务收入(万元)')),
            '研发费用总额(万元)': safe_num(g('研发费用总额(万元)')),
            '利润总额(万元)': safe_num(g('利润总额(万元)')),
            '纳税总额(万元)': safe_num(g('纳税总额(万元)')),
            '职工总数': int(safe_num(g('职工总数'))),
            '在职职工人数': int(safe_num(g('在职职工人数'))),
            '兼职职工人数': int(safe_num(g('兼职职工人数'))),
            '临时职工人数': int(safe_num(g('临时职工人数'))),
            '科技人员总数': int(safe_num(g('科技人员总数'))),
            '科技人员在职人数': int(safe_num(g('科技人员在职人数'))),
            '科技人员兼职人数': int(safe_num(g('科技人员兼职人数'))),
            '科技人员临时人数': int(safe_num(g('科技人员临时人数'))),
            '本年新增人数': int(safe_num(g('本年新增人数'))),
            '吸纳高校毕业生人数': int(safe_num(g('吸纳高校毕业生人数'))),
            '博士人数': int(safe_num(g('博士人数'))),
            '硕士人数': int(safe_num(g('硕士人数'))),
            '本科人数': int(safe_num(g('本科人数'))),
            '大专及以下人数': int(safe_num(g('大专及以下人数'))),
            '高级职称人数': int(safe_num(g('高级职称人数'))),
            '中级职称人数': int(safe_num(g('中级职称人数'))),
            '初级职称人数': int(safe_num(g('初级职称人数'))),
            '高级技工人数': int(safe_num(g('高级技工人数'))),
        }
        companies.append((year, company))
    return companies


def read_sheet_2023(ws):
    companies = []
    for row in range(2, ws.max_row + 1):
        name = ws.cell(row, COL_2023['企业名称']).value
        if not name:
            continue
        year_val = ws.cell(row, COL_2023['填报年份']).value
        if year_val:
            year = str(int(year_val)) if isinstance(year_val, (int, float)) else str(year_val)
        else:
            continue

        def g(key):
            return ws.cell(row, COL_2023[key]).value

        company = {
            '企业名称': name,
            '统一社会信用代码': str(g('统一社会信用代码') or ''),
            '注册日期': str(g('注册日期') or ''),
            '注册地': str(g('企业注册地') or ''),
            '地址': str(g('地址') or ''),
            '一级行业': str(g('一级行业') or ''),
            '二级行业': str(g('二级行业') or ''),
            '填报年份': int(year),
            '指标1_注册地': str(g('指标1_注册地') or ''),
            '指标2_企业规模': str(g('指标2_企业规模') or ''),
            '指标3_产品及服务范围': str(g('指标3_产品及服务范围') or ''),
            '指标4_企业信用': str(g('指标4_企业信用') or ''),
            '指标5_高新技术企业': str(g('指标5_高新技术企业') or ''),
            '指标6_研发机构': str(g('指标6_研发机构') or ''),
            '指标7_科技奖励': str(g('指标7_科技奖励') or ''),
            '指标8_制定标准': str(g('指标8_制定标准') or ''),
            '是否享受加计扣除': str(g('是否享受加计扣除') or ''),
            '享受加计扣除金额(元)': safe_num(g('享受加计扣除金额(元)')),
            '总得分': safe_num(g('总得分')),
            '科技人员占企业职工总数比例': safe_num(g('科技人员占企业职工总数比例')),
            '研发费用占销售收入总额比例': safe_num(g('研发费用占销售收入总额比例')),
            '研发费用占成本费用支出总额比例': safe_num(g('研发费用占成本费用支出总额比例')),
            'I类知识产权数量': int(safe_num(g('I类知识产权数量'))),
            'II类知识产权数量': int(safe_num(g('II类知识产权数量'))),
            '资产总额(万元)': safe_num(g('资产总额(万元)')),
            '净资产(万元)': safe_num(g('净资产(万元)')),
            '成本费用总额(万元)': safe_num(g('成本费用总额(万元)')),
            '销售收入(万元)': safe_num(g('销售收入(万元)')),
            '主营业务收入(万元)': safe_num(g('主营业务收入(万元)')),
            '研发费用总额(万元)': safe_num(g('研发费用总额(万元)')),
            '利润总额(万元)': safe_num(g('利润总额(万元)')),
            '纳税总额(万元)': safe_num(g('纳税总额(万元)')),
            '职工总数': int(safe_num(g('企业职工总数'))),
            '在职职工人数': int(safe_num(g('企业在职人数'))),
            '兼职职工人数': int(safe_num(g('企业兼职人数'))),
            '临时职工人数': int(safe_num(g('企业临时人数'))),
            '科技人员总数': int(safe_num(g('企业科技人员总数'))),
            '科技人员在职人数': int(safe_num(g('企业科技人员中在职人数'))),
            '科技人员兼职人数': int(safe_num(g('企业科技人员中兼职人数'))),
            '科技人员临时人数': int(safe_num(g('企业科技人员中临时人数'))),
            '本年新增人数': int(safe_num(g('本年新增人数'))),
            '吸纳高校毕业生人数': int(safe_num(g('吸纳高校毕业生人数'))),
            '博士人数': int(safe_num(g('企业博士人数'))),
            '硕士人数': int(safe_num(g('企业硕士人数'))),
            '本科人数': int(safe_num(g('企业本科人数'))),
            '大专及以下人数': int(safe_num(g('企业大专及以下人数'))),
            '高级职称人数': int(safe_num(g('企业中高级职称人数'))),
            '中级职称人数': int(safe_num(g('企业中中级职称人'))),
            '初级职称人数': int(safe_num(g('企业中初级职称人数'))),
            '高级技工人数': int(safe_num(g('企业中高级技工人数'))),
        }
        companies.append((year, company))
    return companies


def main():
    wb = openpyxl.load_workbook('企业测试/科小数据样例.xlsx', data_only=True)

    all_by_year = {}

    for year in ['2025', '2024']:
        if year in wb.sheetnames:
            records = read_sheet_2025(wb[year])
            print(f'{year}: {len(records)} companies')
            all_by_year[year] = {r[1]['企业名称']: r[1] for r in records}

    if '2023' in wb.sheetnames:
        records = read_sheet_2023(wb['2023'])
        print(f'2023: {len(records)} companies')
        all_by_year['2023'] = {r[1]['企业名称']: r[1] for r in records}

    output = {}
    for year, companies in sorted(all_by_year.items()):
        output[year] = dict(sorted(companies.items()))

    output_path = Path('企业测试/科小数据样例.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f'\nSaved to: {output_path}')

    # Verify
    for year, companies in sorted(all_by_year.items()):
        names = list(companies.keys())
        if not names:
            continue
        name = names[0]
        c = companies[name]
        staff = c['职工总数']
        sci = c['科技人员总数']
        ip1 = c['I类知识产权数量']
        ip2 = c['II类知识产权数量']
        rd = c['研发费用总额(万元)']
        rev = c['销售收入(万元)']
        reg = c['注册地']
        hi = c['指标5_高新技术企业']
        print(f'\n{year} - {name}:')
        print(f'  staff={staff}, sci={sci}, I={ip1}, II={ip2}')
        print(f'  rd={rd}w, rev={rev}w')
        print(f'  reg={reg}, hi={hi}')


if __name__ == '__main__':
    main()