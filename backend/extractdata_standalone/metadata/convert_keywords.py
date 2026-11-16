from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

# ---- 附件分类正则 ----
_INCLUDE_KW = re.compile(r'(?:指南|指引|办法|细则|规定|规程|规范|标准|条件|解答|问答)')
_NOTIFY_GUIDELINE = re.compile(
    r'(?:办法|细则|规定|规程|规范|指南|指引|措施|方案|意见|标准|条件|解答|问答).*通知'
    r'|通知.*(?:办法|细则|规定|规程|规范|指南|指引|措施|方案|意见|标准|条件|解答|问答)'
)
_EXCLUDE_KW = re.compile(r'(?:申请表|申报书|信息表|汇总表|登记表|承诺书|审查表|推荐表|名单|汇总|统计)')
_ARCHIVE_EXT = {'.zip', '.rar', '.7z'}
# 非指导性附件扩展名（表格/数据类文件格式）
_DATA_EXT = {'.xlsx', '.xls', '.csv'}

# 附件前缀匹配：附件/ 附件1：/ 附件：1./ 附件1-/ 附件1_/ 附件1-- 等
_ATT_PREFIX = re.compile(
    r'^附件'
    r'(?:\s*\d+\s*[：:_\-—.．]+\s*'      # 附件1：/ 附件1-/ 附件1.
    r'|\s*[：:_\-—.．]+\s*\d+\s*[.．]?\s*)' # 附件：1./ 附件：1 / 附件：1.
)


def clean_attachment_name(name: str) -> str:
    """去掉附件文件名的前缀（附件1：）和后缀（.doc），只保留中间名称。"""
    # 去掉扩展名
    stem = Path(name).stem
    # 去掉"附件N："类前缀
    cleaned = _ATT_PREFIX.sub('', stem)
    return cleaned.strip()


def classify_attachments(names: list[str]) -> tuple[list[str], list[str]]:
    """将附件文件名分为 指导性附件 / 其他附件 两类。

    规则：
      - INCLUDE：文件名含 指南/指引/办法/细则/规定/规程/规范
      - INCLUDE：文件名含 通知 且同时含 办法/细则/规定/规程/规范/指南/指引/措施/方案/意见
      - EXCLUDE 优先于 INCLUDE：申请表/申报书/信息表/汇总表/登记表/承诺书/名单/汇总/统计
      - .zip/.rar/.7z → 排除（通常是表格模板包）
    """
    guideline, other = [], []
    for name in names:
        stem = Path(name).stem
        ext = Path(name).suffix.lower()
        if ext in _ARCHIVE_EXT or ext in _DATA_EXT:
            other.append(name)
            continue
        if _EXCLUDE_KW.search(stem):
            other.append(name)
            continue
        if _INCLUDE_KW.search(stem) or _NOTIFY_GUIDELINE.search(stem):
            guideline.append(clean_attachment_name(name))
            continue
        other.append(name)
    return guideline, other


# 资金补助类别的具体项（需要放在关键词最前面）
FUNDING_ITEMS = {
    '一次性奖励', '研发补助', '项目资助', '设备购置补贴', '运营补贴', '展会补贴'
}

def extract_support_types(support_type_str):
    """从扶持类型字符串中提取具体项"""
    if not support_type_str:
        return []

    items = []
    # 按分号、顿号、分隔
    parts = re.split(r'[；;、]', support_type_str)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # 格式可能是 "资质荣誉-高企认定" 或 "高企认定"
        if '-' in part:
            # 取最后一级（具体项）
            last_item = part.split('-')[-1].strip()
            if last_item:
                items.append(last_item)
        else:
            # 没有分隔符，直接保留（如"高企认定"、"税收减免"等）
            items.append(part)

    return items

def is_pure_number(kw):
    """判断关键词是否为纯数字（中文数字、阿拉伯数字、罗马数字等）"""
    if not kw:
        return False
    # 中文数字：一/壹、二/贰、三/叁...二十五、一百等
    cn_num_chars = '零一二三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟'
    # 纯中文数字
    if all(c in cn_num_chars for c in kw):
        return True
    # 纯阿拉伯数字
    if re.match(r'^[\d.]+$', kw):
        return True
    # 带序号格式：第1条、（二）、(3)等
    if re.match(r'^[第（(]\s*[\d一二三四五六七八九十百千万]+\s*[条款项部编]?[）)]?$', kw):
        return True
    return False

def sort_keywords(keywords):
    """排序关键词：资金类在前，其他在后，行业领域最后"""
    funding = []
    other = []
    industry = []

    for kw in keywords:
        # 检查是否是资金补助类
        if kw in FUNDING_ITEMS:
            funding.append(kw)
        # 检查是否是行业领域
        elif '产业' in kw or '领域' in kw or '-' in kw:
            industry.append(kw)
        else:
            other.append(kw)

    return funding + other + industry

def process_file(input_path, output_path, source_dir=None):
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 处理单个文件（政策数据列表在根节点）
    if '政策数据列表' in data:
        for policy in data['政策数据列表']:
            process_policy(policy, source_dir)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return len(data['政策数据列表'])
    # 处理单个政策文件
    elif isinstance(data, dict) and '元数据' in data:
        process_policy(data, source_dir)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return 1
    else:
        return 0

def extract_policy_type_items(policy_type_str):
    """从政策类型字符串中提取最后一级的具体项

    示例：
      "资质认定-高企" -> ["高企"]
      "科技金融-奖励、补贴" -> ["奖励", "补贴"]
      "研发创新-研发补助、项目申报等" -> ["研发补助", "项目申报"]
    """
    if not policy_type_str:
        return []

    items = []
    # 按分号分隔多个一级类型
    type_parts = re.split(r'[；;]', policy_type_str)

    for part in type_parts:
        part = part.strip()
        if not part:
            continue

        # 格式可能是 "一级-二级、二级" 或 "一级-二级"
        if '-' in part:
            # 取最后一级（二级）
            last_level = part.split('-')[-1].strip()
            # 去掉末尾的"等"字（如"项目申报等"→"项目申报"）
            last_level = re.sub(r'等$', '', last_level).strip()
            # 二级可能用顿号分隔多个具体项
            sub_items = re.split(r'[、]', last_level)
            for sub in sub_items:
                sub = sub.strip()
                # 同样去掉每个子项末尾的"等"
                sub = re.sub(r'等$', '', sub).strip()
                if sub and sub not in items:
                    items.append(sub)
        else:
            # 没有分隔符，去掉末尾"等"后直接保留
            cleaned = re.sub(r'等$', '', part).strip()
            if cleaned and cleaned not in items:
                items.append(cleaned)

    return items


# 地区黑名单（常见的省级/市级/区级前缀）
_REGION_BLACKLIST = [
    # 省级
    '福建省', '广东省', '浙江省', '江苏省', '山东省', '河北省', '河南省', '湖北省',
    '湖南省', '四川省', '安徽省', '江西省', '辽宁省', '吉林省', '黑龙江省',
    # 市级
    '厦门市', '福州市', '泉州市', '漳州市', '莆田市', '宁德市', '龙岩市', '三明市', '南平市',
    '深圳市', '广州市', '佛山市', '东莞市', '珠海市', '中山市', '惠州市', '江门市', '肇庆市',
    '杭州市', '宁波市', '温州市', '嘉兴市', '湖州市', '绍兴市', '金华市', '衢州市', '舟山市', '台州市', '丽水市',
    '南京市', '苏州市', '无锡市', '常州市', '南通市', '扬州市', '镇江市', '泰州市', '徐州市', '连云港市', '淮安市', '盐城市', '宿迁市',
    # 区级/开发区
    '火炬区', '经开区', '高新区', '自贸区', '示范区', '产业园', '工业区','湖里区', '思明区', '翔安区', '集美区', '同安区',
    # 国家级
    '国家级', '国家', '中央',
]

def extract_title_keywords(title_str):
    """从政策标题中提取关键词（去除地区前缀，保留核心事项）

    规则：
      - 去掉地区前缀（省/市/区/国家级等）
      - 保留核心政策事项

    示例：
      "厦门市生物医药产业高质量发展若干措施" -> ["生物医药产业高质量发展若干措施"]
      "2025年国家级高新技术企业认定及重新认定" -> ["2025年国家级高新技术企业认定及重新认定"]
      "福建省高新技术企业认定" -> ["高新技术企业认定"]
    """
    if not title_str:
        return []

    cleaned = title_str

    # 去掉地区前缀（按长度降序排列，长的先匹配）
    _REGION_BLACKLIST_SORTED = sorted(_REGION_BLACKLIST, key=len, reverse=True)
    for region in _REGION_BLACKLIST_SORTED:
        if cleaned.startswith(region):
            cleaned = cleaned[len(region):]
            break

    # 去掉中间位置的地区词
    for region in _REGION_BLACKLIST_SORTED:
        cleaned = cleaned.replace(region, '')

    cleaned = cleaned.strip()

    if not cleaned:
        return []

    return [cleaned]

def extract_title_subject(title_str):
    """从政策标题中提取核心主语/事项名称

    规则：
      - 去掉年份、批次、申报/通知等通用词
      - 去掉地区前缀（省/市/区/国家级）
      - 提取核心事项名称
      - 遇到"和""及"时分拆为多个关键词
      - 过滤掉过于宽泛或无意义的词

    示例：
      "2025年度第一批技术交易奖励金申报" -> ["技术交易奖励金"]
      "2025年国家级高新技术企业认定及重新认定" -> ["高新技术企业认定", "重新认定"]
      "科创风投基金子基金和直接投资项目申报指南" -> ["科创风投基金子基金", "直接投资项目"]
      "福建省厦门市高新技术企业认定" -> ["高新技术企业认定"]
    """
    if not title_str:
        return []

    # ===== 第一步：清洗标题 =====
    cleaned = title_str

    # 去掉年份格式：2025年、2025年度、年度
    cleaned = re.sub(r'(?:20|19)[\d]{0,2}年?(?:度)?', '', cleaned)
    cleaned = re.sub(r'^\d{4}', '', cleaned)  # 开头的纯数字年份

    # 去掉批次格式：第一批、第N批、第N期
    cleaned = re.sub(r'^第[\d一二三四五六七八九十]+[批期]', '', cleaned)
    # 去掉中间位置的批次
    cleaned = re.sub(r'第[\d一二三四五六七八九十]+[批期]', '', cleaned)

    # 去掉通用前缀：网上填报、线下递交、线上申报等
    cleaned = re.sub(r'^(?:网上填报|线下递交|线上申报|线下申报|纸质申报)', '', cleaned)

    # 去掉地域前缀（按长度降序排列，长的先匹配）
    _REGION_BLACKLIST_SORTED = sorted(_REGION_BLACKLIST, key=len, reverse=True)
    for region in _REGION_BLACKLIST_SORTED:
        if cleaned.startswith(region):
            cleaned = cleaned[len(region):]
            break

    # 去掉中间位置的地区词（如"福建省厦门市"可能在中间）
    for region in _REGION_BLACKLIST_SORTED:
        cleaned = cleaned.replace(region, '')

    # 去掉通用后缀（按优先级排列，长的先去掉）
    _SUFFIX_BLACKLIST = [
        '申报指南', '工作指南', '办事指南', '操作指南',
        '申报工作', '工作通知', '申报通知',
        '申报材料', '申报表格',
        '若干措施', '实施细则', '管理办法', '若干政策', '实施意见', '实施办法',
        '的通知', '通知', '公示', '申报', '工作', '方案', '计划',
    ]
    for suffix in _SUFFIX_BLACKLIST:
        if cleaned.endswith(suffix):
            cleaned = cleaned[:-len(suffix)]
            break

    cleaned = cleaned.strip()

    if not cleaned or len(cleaned) < 2:
        return []

    # ===== 第二步：按"和""及"分拆 =====
    # 先替换"及"为"和"，统一处理
    cleaned = cleaned.replace('及', '和')
    parts = re.split(r'[和]', cleaned)
    parts = [p.strip() for p in parts if p.strip()]

    # ===== 第三步：过滤无效关键词 =====
    # 过滤规则：
    _FILTER_RULES = [
        # 1. 以"的"结尾的词（如"支持企业加强研发活动加大研发投入的"）
        lambda x: x.endswith('的'),
        # 2. 以"支持""加强""加大""推进""促进"开头的过于宽泛的词
        lambda x: x.startswith(('支持', '加强', '加大', '推进', '促进', '鼓励', '引导')),
        # 3. 纯动词或过于短的无意义词
        lambda x: len(x) < 4 and x in ('申报', '申请', '填报', '办理', '审核', '评审'),
        # 4. 纯数字或含数字的年份类词
        lambda x: re.match(r'^[\d一二三四五六七八九十年度第批期]+$', x),
        # 5. 通用动词短语（无具体事项）
        lambda x: x in ('开展申报', '组织申报', '申报工作', '填报材料', '提交材料'),
        # 6. 结尾带"活动""行动"但前面是通用词的
        lambda x: x.endswith('活动') and len(x) < 8,
    ]

    valid_parts = []
    for part in parts:
        # 检查是否被任一过滤规则命中
        is_filtered = False
        for rule in _FILTER_RULES:
            if rule(part):
                is_filtered = True
                break
        if not is_filtered and len(part) >= 2:
            valid_parts.append(part)

    # ===== 第四步：去重 =====
    seen = set()
    result = []
    for p in valid_parts:
        if p not in seen:
            seen.add(p)
            result.append(p)

    return result


def _parse_date(date_str: str):
    """解析 YYYY.MM.DD 格式的日期字符串，返回 (year, month, day) 或 None"""
    if not date_str:
        return None
    match = re.match(r'(\d{4})\.(\d{1,2})\.(\d{1,2})', date_str.strip())
    if match:
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return None


def _date_to_int(date_tuple):
    """将 (year, month, day) 转换为可比较的整数"""
    return date_tuple[0] * 10000 + date_tuple[1] * 100 + date_tuple[2]


def _extract_validity_from_source(file_name, source_dir):
    """从源文件中提取有效日期

    在源文件的"有效日期"章节中查找与政策标题最相关的日期。
    """
    if not file_name or not source_dir:
        return ''

    # 构建源文件路径
    if file_name.lower().endswith('.md'):
        source_file = Path(source_dir) / file_name
    else:
        source_file = Path(source_dir) / (file_name + '.md')

    if not source_file.exists():
        return ''

    try:
        with open(source_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # 查找"有效日期"章节
        section_match = re.search(
            r'##\s*(?:\d+\.)?\s*有效日期\s*\n(.*?)(?=\n##\s|\Z)',
            content,
            re.DOTALL
        )
        if not section_match:
            return ''

        section_text = section_match.group(1)
        policy_title = file_name.replace('.md', '')

        # 提取核心政策标题（去掉地名）
        core_title = re.sub(r'^[厦门深圳北京上海广州天津重庆香港澳门省市区县]+', '', policy_title)

        date_pattern = r'(\d{4})[年\-/](\d{1,2})[月\-/](\d{1,2})[日]?'
        lines = section_text.split('\n')

        # 按分号分割每个独立政策/文件段
        all_fragments = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # 按分号分割（处理"《A》自X实施；《B》自Y实施"的情况）
            fragments = re.split(r'[；;]', line)
            for frag in fragments:
                frag = frag.strip()
                if frag:
                    all_fragments.append(frag)

        # 查找与政策标题匹配的段落
        matched_fragment = None
        first_fragment = None

        for frag in all_fragments:
            # 记录第一个段落
            if first_fragment is None:
                first_fragment = frag

            # 检查段落是否包含核心政策标题关键词
            if core_title and any(kw in frag for kw in core_title):
                if matched_fragment is None or len(frag) < len(matched_fragment):
                    matched_fragment = frag

        # 优先使用包含政策标题的段落，其次使用第一段
        target_frag = matched_fragment if matched_fragment else first_fragment

        if not target_frag:
            return ''

        # 提取日期
        start_match = re.search(date_pattern, target_frag)
        if not start_match:
            return ''

        start_year = int(start_match.group(1))
        start_month = int(start_match.group(2))
        start_day = int(start_match.group(3))
        start_date = f"{start_year}.{start_month:02d}.{start_day:02d}"

        # 查找有效期时长（更严格的匹配：必须紧跟在"有效期"后面）
        years_match = re.search(r'有效期[为至]?(\d+)\s*[年个月]', target_frag)
        if years_match:
            years = int(years_match.group(1))
            if '个月' in target_frag:
                from datetime import datetime, timedelta
                start_dt = datetime(start_year, start_month, start_day)
                end_dt = start_dt + timedelta(days=years * 30 - 1)
                end_date = f"{end_dt.year}.{end_dt.month:02d}.{end_dt.day:02d}"
            else:
                # 有效期X年：结束日期 = 开始日期 + X年 - 1天
                end_year = start_year + years
                end_month = start_month
                end_day = start_day - 1 if start_day > 1 else 28
                end_date = f"{end_year}.{end_month:02d}.{end_day:02d}"
            return f"{start_date}-{end_date}"

        # 没有有效期说明 → 长期有效
        return f"{start_date}-长期"

    except Exception as e:
        print(f"  [有效日期-提取失败] {file_name}: {e}")
        return ''


def _process_apply_time_display(policy):
    """根据政策有效期和申报时间，生成完整的申报状态描述"""
    # 当前系统日期（2026年，根据 prompt_extract.md 中的假设）
    current_date = (2026, 6, 20)
    current_int = _date_to_int(current_date)
    current_year = current_date[0]

    apply_time_raw = policy.get('申报条件', {}).get('申报时间', '')
    validity = policy.get('元数据', {}).get('有效日期', '')

    # 如果申报时间为空或为"常年受理"，改为显示"政策申报中，申报日期：日常工作日"
    if not apply_time_raw or apply_time_raw in ('常年受理', '-', ''):
        policy['申报条件']['申报时间'] = '政策申报中，申报日期：日常工作日'
        return

    # ===== 解析多批次格式：YYYY.MM.DD-YYYY.MM.DD;第一批:MM.DD;第二批:MM.DD =====
    batch_info = []
    date_range_part = apply_time_raw

    # 分离日期范围和批次信息
    if ';' in apply_time_raw:
        parts = apply_time_raw.split(';')
        date_range_part = parts[0]
        # 解析批次信息：格式为"第一批:MM.DD"或"第一批:08.08"
        for part in parts[1:]:
            if ':' in part:
                batch_name, batch_date = part.split(':', 1)
                batch_name = batch_name.strip()
                batch_date = batch_date.strip()
                # 解析批次日期（月.日格式）
                if re.fullmatch(r'\d{1,2}\.\d{1,2}', batch_date):
                    month, day = batch_date.rsplit('.', 1)
                    month, day = int(month), int(day)
                    batch_info.append((batch_name, month, day))

    # 解析日期范围
    apply_parts = date_range_part.split('-')
    if len(apply_parts) != 2:
        return

    start_date = _parse_date(apply_parts[0])
    end_date = _parse_date(apply_parts[1])
    if not start_date or not end_date:
        return

    # ===== 跨年修复：如果申报时间是往年的，更新为当前年份 =====
    if start_date[0] < current_year:
        start_date = (current_year, start_date[1], start_date[2])
        end_date = (current_year, end_date[1], end_date[2])
        # 重新构建申报时间字符串
        date_range_str = f"{start_date[0]}.{start_date[1]:02d}.{start_date[2]:02d}-{end_date[0]}.{end_date[1]:02d}.{end_date[2]:02d}"
    else:
        date_range_str = date_range_part

    # 更新批次年份
    if batch_info:
        updated_batch_info = []
        for batch_name, month, day in batch_info:
            updated_batch_info.append((batch_name, month, day))
        batch_info = updated_batch_info

    start_int = _date_to_int(start_date)
    end_int = _date_to_int(end_date)

    # 解析政策有效期
    validity_int = None
    if validity:
        val_parts = validity.split('-')
        if len(val_parts) == 2:
            val_end = _parse_date(val_parts[1])
            if val_end:
                validity_int = _date_to_int(val_end)

    # 判断政策是否已过期
    if validity_int is not None and validity_int < current_int:
        policy['申报条件']['申报时间'] = '当前政策已过期，敬请及时关注有关部门最新消息。'
        return

    # 申报年份
    apply_year = start_date[0]

    # 构建展示内容
    def _build_batch_display():
        """构建批次截止信息展示"""
        if not batch_info:
            return ""
        batch_parts = []
        for batch_name, month, day in batch_info:
            batch_parts.append(f"{batch_name}截止{month:02d}.{day:02d}")
        return "，" + "，".join(batch_parts)

    # 情况一：申报中（当前日期在申报日期范围内）
    if start_int <= current_int <= end_int:
        policy['申报条件']['申报时间'] = f'政策申报进行中，申报时段：{date_range_str}{_build_batch_display()}'
    # 情况二：已结束（同年）（当前日期 > 申报日期范围，且年份相同）
    elif current_int > end_int and current_date[0] == apply_year:
        policy['申报条件']['申报时间'] = f'{apply_year}年政策申报已结束{_build_batch_display()}，申报时间为：{date_range_str}'
    # 情况三：未启动（跨年）（当前日期 > 申报日期范围，且年份不同）
    elif current_int > end_int and current_date[0] != apply_year:
        policy['申报条件']['申报时间'] = f'{current_date[0]}政策暂未启动申报，参照往年申报安排，今年预计申报时间：{date_range_str}{_build_batch_display()}'


def process_policy(policy, source_dir=None):
    """处理单个政策，提取关键词

    关键词来源（按优先级）：
    1. 政策类型：每个具体项单独提取
    2. 政策标题（去除地区后）：保留核心事项
    3. 行业领域：每个行业单独提取
    4. 扶持类型：每个具体项单独提取
    5. 最高扶持金额：有值则提取
    """
    # ---- 申报时间展示逻辑：生成完整的申报状态描述 ----
    _process_apply_time_display(policy)

    # 政策标题使用文件名（去掉.md后缀）
    file_name = policy.get('文件名', '')
    if file_name:
        policy_title = file_name
        if policy_title.lower().endswith('.md'):
            policy_title = policy_title[:-3]
        policy['元数据']['政策标题'] = policy_title
    else:
        policy_title = policy.get('元数据', {}).get('政策标题', '')

    # ---- 有效日期后处理：如果为空，从源文件提取 ----
    validity = policy.get('元数据', {}).get('有效日期', '')
    if not validity and source_dir:
        extracted_validity = _extract_validity_from_source(file_name, source_dir)
        if extracted_validity:
            policy['元数据']['有效日期'] = extracted_validity
            print(f"  [有效日期-后处理] {policy_title}: {extracted_validity}")

    # 不再提取关键词，保留 extractdata.py 提取的原始内容
    pass

    # ---- 附件分类：将指导性附件合并到实施细则，清理附件字段 ----
    # 附件字段在申报材料及方式里面
    attachment_raw = policy.get('申报材料及方式', {}).get('附件', '') or policy.get('申报条件', {}).get('附件', '') or policy.get('附件', '') or policy.get('元数据', {}).get('附件', '')
    # 处理附件可能是数组或字符串的情况
    if isinstance(attachment_raw, list):
        att_names = [a.strip() for a in attachment_raw if a.strip()]
        attachment_str = '；'.join(attachment_raw)
    else:
        attachment_str = attachment_raw
        att_names = [a.strip() for a in re.split(r'[、；;]', attachment_str) if a.strip()] if attachment_str else []
    if attachment_str and attachment_str not in ('-', '无') and att_names:
        if att_names:
            guideline_atts, form_atts = classify_attachments(att_names)

            # 1. 将指导性附件合并到实施细则
            if guideline_atts:
                existing_related = policy.get('元数据', {}).get('实施细则', [])
                if not isinstance(existing_related, list):
                    existing_related = []
                new_related = []
                seen_clean: set[str] = set()
                for old in existing_related:
                    c = clean_attachment_name(old)
                    if old.startswith(('《', '附件', '附')) and c in [clean_attachment_name(g) for g in guideline_atts]:
                        continue
                    new_related.append(old)
                    seen_clean.add(c)
                for att in guideline_atts:
                    if att not in seen_clean:
                        new_related.append(att)
                        seen_clean.add(att)
                policy.setdefault('元数据', {})['实施细则'] = new_related

            # 2. 清理附件字段：保留与申报材料相关的附件（表格、申报材料等）
            # 定义与申报材料相关的保留关键词
            KEEP_KEYWORDS = re.compile(
                r'(?:申请表|申报表|申请书|任务书|信息表|登记表|承诺书|汇总表|审查表|推荐表|'
                r'明细表|统计表|备案表|承诺函|申报清单|申报材料|材料清单|附件表格|空白表格|'
                r'填报表格|申报填报|填报模板|模板|空白模板|盖章件|扫描件|电子版|'
                r'工作表|汇总清单|清单表|报名表|参评表|推荐名单|申报企业|汇总名单)',
                re.IGNORECASE
            )
            # 定义排除的文件类型关键词（指导性文件不保留在附件中）
            EXCLUDE_KEYWORDS = re.compile(r'(?:指南|指引|办法|细则|规定|规程|规范|标准|解答|问答|手册|工作说明|填报说明|填写示例|解读|政策)', re.IGNORECASE)

            filtered_form_atts = []
            for att in form_atts:
                stem = Path(att).stem
                # 如果含排除关键词则跳过
                if EXCLUDE_KEYWORDS.search(stem):
                    continue
                # 保留含申报材料相关关键词的文件
                if KEEP_KEYWORDS.search(stem):
                    filtered_form_atts.append(att)

            # 更新附件字段（保持数组格式）
            if filtered_form_atts:
                policy.setdefault('申报材料及方式', {})['附件'] = filtered_form_atts
            else:
                policy.setdefault('申报材料及方式', {})['附件'] = []

    # ---- 实施细则关联匹配：与 Attribute/实施细则.json 模糊匹配 ----
    _merge_related_by_llm(policy, _IMPLEMENT_RULE_TITLES)

    # # ---- 指导政策匹配：与 Attribute/指导政策.json 模糊匹配 ----
    # _merge_guiding_by_llm(policy, _GUIDING_RULE_TITLES)

    # # ---- 指导政策清洗：只保留在指导政策.json中存在的条目 ----
    # guiding_items = policy.get('元数据', {}).get('指导政策', [])
    # if isinstance(guiding_items, list) and _GUIDING_RULE_TITLES:
    #     def _strip_bookmarks(s: str) -> str:
    #         s = s.strip()
    #         s = re.sub(r'^《|》$', '', s)
    #         s = re.sub(r'[（(][^）)]*[）)]$', '', s)
    #         return s.strip()

    #     # 将指导政策.json中的标题建成集合（用于模糊匹配）
    #     guiding_set = set(_GUIDING_RULE_TITLES)
    #     filtered_guiding = []
    #     for item in guiding_items:
    #         stripped = _strip_bookmarks(item)
    #         # 精确匹配：去掉书名号后与指导政策.json中的标题完全一致
    #         if stripped in guiding_set:
    #             filtered_guiding.append(item)
    #             continue
    #         # 模糊匹配：去掉"关于印发""的通知"等冗余词后匹配
    #         simplified = re.sub(r'^(?:关于)?印发', '', stripped)
    #         simplified = re.sub(r'的通知$', '', simplified)
    #         simplified = simplified.strip()
    #         if simplified in guiding_set:
    #             # 用指导政策.json中的标准标题替换
    #             filtered_guiding.append(simplified)
    #             continue
    #     policy['元数据']['指导政策'] = filtered_guiding

    # ---- 最终去重清理：去书名号去重 + 自身标题过滤 + 表格类清理 ----
    def _strip_bookmarks(s: str) -> str:
        """去掉《》书名号及内部引用的文号部分，用于去重比较"""
        s = s.strip()
        s = re.sub(r'^《|》$', '', s)
        s = re.sub(r'[（(][^）)]*[）)]$', '', s)
        return s.strip()

    policy_title = policy.get('元数据', {}).get('政策标题', '')
    # 只处理关联文件，不处理指导政策（保留原始内容）
    for field in ('实施细则',):
        items = policy.get('元数据', {}).get(field, [])
        if isinstance(items, list):
            # 脏数据清洗：过滤掉非字符串项（LLM可能返回 dict）
            items = [i for i in items if isinstance(i, str)]
            seen_stripped: dict[str, str] = {}
            deduped: list[str] = []
            for item in items:
                stripped = _strip_bookmarks(item)
                # 过滤与政策标题相同的项
                if stripped == policy_title:
                    continue
                # 关联文件中去除残留的表格/数据类附件
                if field == '实施细则':
                    ext = Path(item).suffix.lower() if '.' in item else ''
                    if ext in _DATA_EXT or ext in _ARCHIVE_EXT:
                        continue
                    if _EXCLUDE_KW.search(item):
                        continue
                # 去书名号后去重
                if stripped in seen_stripped:
                    prev = seen_stripped[stripped]
                    if prev.startswith('《') and not item.startswith('《'):
                        idx = deduped.index(prev)
                        deduped[idx] = item
                        seen_stripped[stripped] = item
                    continue
                seen_stripped[stripped] = item
                deduped.append(item)
            policy['元数据'][field] = deduped

    # ---- 保留原有的有效日期字段 ----
    # 如果元数据中已有有效日期，保留不动
    # convert_keywords.py 不再从申报条件.政策有效期同步（该字段已取消）

    # ---- 政策原标题后处理：选择与政策标题最接近的文件名 ----
    def _strip_bookmarks_for_title(s: str) -> str:
        """去掉《》书名号及内部引用的文号部分"""
        s = s.strip()
        s = re.sub(r'^《|》$', '', s)
        s = re.sub(r'[（(][^）)]*[）)]$', '', s)
        return s.strip()

    def _calc_similarity(title1: str, title2: str) -> int:
        """计算两个标题的公共字符数量（简单相似度）"""
        chars1 = set(title1)
        chars2 = set(title2)
        return len(chars1 & chars2)

    guiding = policy.get('元数据', {}).get('指导政策', [])
    implement = policy.get('元数据', {}).get('实施细则', [])
    notice = policy.get('元数据', {}).get('通知公告', [])

    # 合并所有文件名，去掉书名号
    all_titles = []
    for items in [guiding, implement, notice]:
        if isinstance(items, list):
            for item in items:
                if isinstance(item, str) and item.strip():
                    cleaned = _strip_bookmarks_for_title(item)
                    if cleaned:
                        all_titles.append(cleaned)

    # 找出与政策标题最接近的文件名
    best_match = None
    best_score = 0

    for title in all_titles:
        score = _calc_similarity(policy_title, title)
        if score > best_score:
            best_score = score
            best_match = title

    # 写入政策原标题字段
    if best_match:
        policy['元数据']['政策原标题'] = best_match

    # ---- 最高扶持金额后处理：只保留最高的一个金额 ----
    def _parse_amount(amount_str: str) -> float:
        """从金额字符串中提取数值（万元为单位）"""
        if not amount_str:
            return 0
        # 匹配数字
        import re
        match = re.search(r'([\d.]+)', amount_str)
        if not match:
            return 0
        value = float(match.group(1))
        if '亿' in amount_str:
            value *= 10000
        return value

    support = policy.get('支持方式', {})
    highest_amount = support.get('最高扶持金额', '')
    if highest_amount and '；' in highest_amount:
        # 分割多个金额，找出最高的
        amounts = [a.strip() for a in highest_amount.split('；') if a.strip()]
        max_value = 0
        max_amount = ''
        for amt in amounts:
            value = _parse_amount(amt)
            if value > max_value:
                max_value = value
                max_amount = amt
        if max_amount:
            support['最高扶持金额'] = max_amount


# ===================== 实施细则关联匹配（LLM） =====================

# 缓存：实施细则标题列表（全局只加载一次）
_IMPLEMENT_RULE_TITLES: list[str] = []
# 缓存：指导政策标题列表（全局只加载一次）
_GUIDING_RULE_TITLES: list[str] = []


def load_implementation_rules(json_path: str | Path) -> list[str]:
    """加载 Attribute/实施细则.json 中的政策标题列表（去重）。"""
    path = Path(json_path)
    if not path.exists():
        print(f"  [警告] 实施细则文件不存在: {path}")
        return []
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    # 兼容两种格式：纯数组 或 {"政策标题列表": [...]}
    if isinstance(data, list):
        titles = data
    else:
        titles = data.get('政策标题列表', [])
    # 去重，保持顺序
    seen: set[str] = set()
    unique: list[str] = []
    for t in titles:
        if isinstance(t, str) and t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def _pre_filter_candidates_guiding(
    policy_title: str,
    policy_summary: str,
    candidates: list[str],
    min_overlap: int = 4,
) -> list[str]:
    """双重预筛（指导政策专用，比实施细则更严格）。

    第一层：3-gram 字符级重叠过滤（min_overlap 默认 4）
    第二层：核心政策关键词交叉验证（min_core 默认 3，比实施细则的 2 更严格）
    """
    combined = policy_title + policy_summary[:200]
    cn_only = re.sub(r'[^一-鿿]', '', combined)
    p_grams = set(cn_only[i:i + 3] for i in range(len(cn_only) - 2))

    # 指导政策常见的宽泛 3-gram（比实施细则更多）
    _GUIDING_STOP_GRAMS = {
        '厦门市', '管理办法', '若干措施', '实施细则', '管理工作',
        '办公室关', '人民政政', '人民政府', '科学技术',
        '高新技术', '新技术企', '技术企业', '科技企业', '科技创新',
        '技创新', '创新引领', '引领工程',
        # 指导政策高频通用词
        '深入实施', '施科技创', '科技创新', '技创新引', '新引领工',
        '引领工程', '争创国家', '国家区域', '区域科技', '域科技创新',
        '加快推进', '快推进厦', '推进厦门', '科学城建', '城建设若',
        '深化拓展', '化拓展科', '拓展科技', '技创新和',
        '和产业创', '产业创新', '业创新深', '创新深度', '深度融合',
        '若干意见', '实施意见', '实施办法', '行动计划', '发展规划',
        '高质量发', '量发展', '促进生', '生物医', '医药产', '药产业',
    }
    p_grams -= _GUIDING_STOP_GRAMS

    if not p_grams:
        return candidates

    filtered = []
    for c in candidates:
        # 第一层：3-gram 过滤
        c_cn = re.sub(r'[^一-鿿]', '', c)
        c_grams = set(c_cn[i:i + 3] for i in range(len(c_cn) - 2))
        c_grams -= _GUIDING_STOP_GRAMS
        if len(p_grams & c_grams) < min_overlap:
            continue
        # 第二层：核心关键词交叉验证（指导政策要求至少 3 个核心词重叠）
        if not _core_keywords_overlap(policy_title, c, min_overlap=3):
            continue
        filtered.append(c)
    return filtered


def _extract_core_keywords(title: str) -> set[str]:
    """提取标题中的核心政策关键词（去除通用后缀和停用词）。

    提取 2~4 字连续中文片段，去除政策通用后缀和过于宽泛的词汇，
    用于跨标题的核心事项比对。
    """
    cn_only = re.sub(r'[^一-鿿]', '', title)

    # 政策通用后缀——在核心词提取时去掉
    _CORE_SUFFIXES = (
        '管理办法', '实施细则', '若干措施', '实施意见', '实施办法',
        '申报通知', '工作通知', '评选办法', '管理规定', '工作指引',
        '申报指南', '办事指南', '操作规程', '政策解读', '政策问答',
        '奖励办法', '评价办法', '工作指南', '执行指引', '若干政策',
    )
    for suffix in _CORE_SUFFIXES:
        cn_only = cn_only.replace(suffix, '')

    # 提取 2~4 字连续中文片段
    keywords = set()
    for n in (4, 3, 2):
        for i in range(len(cn_only) - n + 1):
            seg = cn_only[i:i + n]
            keywords.add(seg)

    # 停用 2~3-gram：过于通用、容易跨领域误匹配的词
    _CORE_STOP = {
        '厦门', '门市', '管理', '办法', '实施', '细则', '若干', '措施',
        '关于', '开展', '通知', '意见', '规定', '年度', '年度度',
        '人民', '民政', '政府', '科学', '技术', '科技局', '科技',
        '工作', '认定', '申报', '组织', '发展', '建设', '推进',
        '高新', '企业', '高新技', '新技术', '技术企', '术企业',
        '创新', '产业', '服务', '服务企', '服务业', '务企业',
        '厦门市', '管理办法', '实施细则', '若干措施', '管理工作',
        '办公室关', '人民政府', '科学技术', '实施意见', '实施办法',
        '工作通知', '申报通知', '申报指南', '工作指引', '办事指南',
        '深入', '深入实', '入实施', '加快推进', '快推进', '推进厦',
        '科技创新', '技创新', '创新引', '新引领', '引领工',
        '争创', '国家', '家区域', '区域',
        '高质量', '质量发', '量发展',
    }
    keywords -= _CORE_STOP
    # 去掉含数字的片段（如"2025""二十"等年份/序号）
    keywords = {k for k in keywords if not re.search(r'[\d零一二三四五六七八九十百千万]', k)}
    return keywords


def _core_keywords_overlap(title1: str, title2: str, min_overlap: int = 2) -> bool:
    """检查两个标题是否有足够的核心关键词重叠。"""
    kw1 = _extract_core_keywords(title1)
    kw2 = _extract_core_keywords(title2)
    return len(kw1 & kw2) >= min_overlap


def _pre_filter_candidates(policy_title: str, policy_summary: str, candidates: list[str], min_overlap: int = 3) -> list[str]:
    """双重预筛：3-gram 重叠 + 核心关键词交叉验证。

    第一层：3-gram 字符级重叠过滤（快速粗筛）
    第二层：核心政策关键词交叉验证（去除仅因通用词重叠的误匹配）
    """
    combined = policy_title + policy_summary[:200]
    cn_only = re.sub(r'[^一-鿿]', '', combined)
    p_grams = set(cn_only[i:i + 3] for i in range(len(cn_only) - 2))

    # 去掉过于通用的 3-gram（如"厦门市""管理办法"）
    _STOP_GRAMS = {
        '厦门市', '管理办法', '若干措施', '实施细则', '管理工作',
        '建设与运', '办公室关', '人民政政', '人民政府', '科学技术',
        '高新技术', '新技术企', '技术企业', '科技企业', '科技创新',
        '技创新', '创新引领', '引领工程', '深入实施', '施科技创',
        '加快推进', '快推进厦', '推进厦门', '科学城建', '城建设若',
        '若干意见', '实施意见', '实施办法', '行动计划', '发展规划',
        '高质量发', '量发展', '深化拓展', '化拓展科', '拓展科技',
        '和产业创', '产业创新', '业创新深', '创新深度', '深度融合',
    }
    p_grams -= _STOP_GRAMS

    if not p_grams:
        return candidates

    filtered = []
    for c in candidates:
        # 第一层：3-gram 过滤
        c_cn = re.sub(r'[^一-鿿]', '', c)
        c_grams = set(c_cn[i:i + 3] for i in range(len(c_cn) - 2))
        c_grams -= _STOP_GRAMS
        if len(p_grams & c_grams) < min_overlap:
            continue
        # 第二层：核心关键词交叉验证（至少 2 个核心词重叠）
        if not _core_keywords_overlap(policy_title, c, min_overlap=2):
            continue
        filtered.append(c)
    return filtered


def llm_match_related(
    policy_title: str,
    policy_summary: str,
    candidate_titles: list[str],
    api_url: str,
    api_key: str,
    model_name: str,
) -> list[str]:
    """调用 LLM 判断哪些候选实施细则与当前政策关联。"""
    if not candidate_titles:
        return []

    candidates_text = "\n".join(f"- {t}" for t in candidate_titles)
    prompt = f"""你是一个政府政策关联分析专家。请严格判断以下候选实施细则中，哪些与当前政策存在直接的配套/执行关联。

⚠️ 关联判定标准（极其严格，宁漏勿错）：
- 当前政策是某个细则的"申报通知/填报通知/年度执行通知"，则该细则是其关联文件
- 当前政策的标题中明确指向某个细则（如"科学仪器设备资源共享"对应"科学仪器设备资源共享管理办法"）
- 两者属于完全相同的政策事项（同一个管理办法 + 其申报/执行通知）
- **核心要求：两个标题中的核心政策事项名称必须完全相同**（如"科学仪器设备资源共享"必须完全一致，"生物与新医药技术"必须完全一致）

❌ 以下情况不算关联，必须排除：
- 仅因同属"科技"领域或同由科技局发布而关联——这是泛领域关联，不是政策关联
- 标题无直接对应关系（如"科学仪器"≠"重点实验室"≠"新型研发机构"）
- 上位框架性政策的关联（应归入"指导政策"，不在此处）
- 核心政策事项名称不完全相同——即使看起来相关，只要事项名称不完全一致就不算（如"高新技术企业"≠"专精特新"，"科学仪器"≠"科学仪器设备资源共享"）
- 仅因都含有"科技""创新""厦门"等通用词而关联——这些是泛领域词，不是政策事项

当前政策：
- 标题：{policy_title}
- 摘要：{policy_summary[:500]}

候选实施细则列表：
{candidates_text}

请严格返回 JSON 数组，只包含与当前政策有直接配套/执行关系的细则标题。若均无直接关系则返回空数组 []。
不要输出 JSON 以外的任何内容。

输出格式示例：
["厦门市科学仪器设备资源共享管理办法"]"""

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "你是一个严谨的政策关联分析助手，只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2048,
        "chat_template_kwargs": {"enable_thinking": False},
    }

    for attempt in range(3):
        try:
            resp = requests.post(api_url, headers=headers, json=payload, timeout=(30, 120))
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]

            # 解析 JSON 数组
            arr_match = re.search(r'\[[\s\S]*\]', content)
            if arr_match:
                result = json.loads(arr_match.group(0))
                if isinstance(result, list):
                    # 清洗：只保留纯字符串项，过滤掉 LLM 返回的 dict 等非标准格式
                    clean: list[str] = []
                    for t in result:
                        if isinstance(t, str):
                            clean.append(t.strip())
                        elif isinstance(t, dict):
                            # 尝试提取 dict 中的 content/name/title 字段
                            val = t.get('content') or t.get('name') or t.get('title') or ''
                            if isinstance(val, str) and val.strip():
                                clean.append(val.strip())
                    return [c for c in clean if c]
            return []
        except Exception as exc:
            print(f"  [LLM匹配] 第{attempt+1}次请求失败: {exc}")
            if attempt < 2:
                time.sleep(3)

    return []


def _merge_related_by_llm(policy: dict, rule_titles: list[str]) -> None:
    """用 LLM 从实施细则列表中匹配关联文件，合并进元数据.关联文件。"""
    if not rule_titles:
        return

    policy_title = policy.get('元数据', {}).get('政策标题', '')
    if not policy_title:
        return

    # 排除已经在关联文件中的细则（避免重复匹配）
    existing = policy.get('元数据', {}).get('实施细则', [])
    existing_set = set(existing)
    # 同时排除与政策标题完全相同的（即自身）
    candidates = [
        t for t in rule_titles
        if t not in existing_set and t != policy_title
    ]
    if not candidates:
        return

    # 预筛：只保留与当前政策有核心词重叠的候选
    policy_summary = policy.get('摘要', '')
    candidates = _pre_filter_candidates(policy_title, policy_summary, candidates)

    if not candidates:
        return

    api_url, api_key, model_name = _get_llm_config()

    if not api_url or not model_name:
        print("  [警告] 缺少模型配置，跳过 LLM 关联匹配")
        return

    matched = llm_match_related(policy_title, policy_summary, candidates, api_url, api_key, model_name)
    if matched:
        related = policy.setdefault('元数据', {}).setdefault('实施细则', [])
        if not isinstance(related, list):
            related = []
        existing_set = set(related)
        validated = []
        for m in matched:
            if m not in existing_set:
                # 后验证：LLM返回的结果必须与当前政策有至少2个核心词重叠，否则丢弃
                if _core_keywords_overlap(policy_title, m, min_overlap=2):
                    related.append(m)
                    existing_set.add(m)
                    validated.append(m)
                else:
                    print(f"  [LLM关联-过滤] 核心词不匹配，丢弃: {m}")
        if validated:
            policy['元数据']['实施细则'] = related
            print(f"  [LLM关联] {policy_title} -> +{validated}")


# ===================== 指导政策匹配（LLM） =====================


def load_guiding_rules(json_path: str | Path) -> list[str]:
    """加载 Attribute/指导政策.json 中的政策标题列表（去重）。"""
    path = Path(json_path)
    if not path.exists():
        print(f"  [警告] 指导政策文件不存在: {path}")
        return []
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    # 兼容两种格式：纯数组 或 {"政策标题列表": [...]}
    if isinstance(data, list):
        titles = data
    else:
        titles = data.get('政策标题列表', [])
    seen: set[str] = set()
    unique: list[str] = []
    for t in titles:
        if isinstance(t, str) and t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def llm_match_guiding(
    policy_title: str,
    policy_summary: str,
    candidate_titles: list[str],
    api_url: str,
    api_key: str,
    model_name: str,
) -> list[str]:
    """调用 LLM 判断哪些候选指导政策与当前政策存在指导/依据关系。"""
    if not candidate_titles:
        return []

    candidates_text = "\n".join(f"- {t}" for t in candidate_titles)
    prompt = f"""你是一个政府政策关联分析专家。请严格判断以下候选指导政策中，哪些与当前政策存在直接的指导/依据关系。

⚠️ 判定标准（极其严格，宁漏勿错）：
- 当前政策正文中明确引用/依据该指导政策（如"根据《XXX》制定"）
- 该指导政策是当前政策的直接上位法/框架性文件
- 当前政策是该指导政策的直接落地实施细则
- **核心要求：两个标题中的核心政策事项名称必须完全相同**（如"生物医药产业高质量发展"必须完全一致，"科技创新引领工程"必须完全一致）

❌ 以下情况不算指导关系，必须排除：
- 仅因同属"科技创新"领域而关联——这是泛领域关联，绝对不算
- 标题无直接对应关系（如"重点实验室"≠"科技创新引领工程"，"高新技术企业"≠"科学城建设"）
- 间接的、远层的政策关联
- 仅因同由科技局发布而关联
- 框架性政策与具体事项完全不对应（如"争创国家区域科技创新中心"与"重点实验室建设"无直接对应）
- 核心政策事项名称不完全相同——即使看起来相关，只要事项名称不完全一致就不算（如"科技创新引领"≠"科技创新引领工程"≠"争创国家区域科技创新中心"，"高质量发展"是通用词不是政策事项）
- 仅因都含有"科技""创新""厦门""发展"等通用词而关联——这些是泛领域词，不是政策事项

当前政策：
- 标题：{policy_title}
- 摘要：{policy_summary[:500]}

候选指导政策列表：
{candidates_text}

请严格返回 JSON 数组，只包含与当前政策有直接指导/依据关系的政策标题。若均无直接关系则返回空数组 []。
不要输出 JSON 以外的任何内容。

输出格式示例：
["深入实施科技创新引领工程争创国家区域科技创新中心的若干措施"]"""

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "你是一个严谨的政策关联分析助手，只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2048,
        "chat_template_kwargs": {"enable_thinking": False},
    }

    for attempt in range(3):
        try:
            resp = requests.post(api_url, headers=headers, json=payload, timeout=(30, 120))
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]

            arr_match = re.search(r'\[[\s\S]*\]', content)
            if arr_match:
                result = json.loads(arr_match.group(0))
                if isinstance(result, list):
                    return [str(t).strip() for t in result if str(t).strip()]
            return []
        except Exception as exc:
            print(f"  [LLM指导] 第{attempt+1}次请求失败: {exc}")
            if attempt < 2:
                time.sleep(3)

    return []


def _get_llm_config() -> tuple[str, str, str]:
    """从 .env 读取模型配置，返回 (api_url, api_key, model_name)。"""
    _env_path = Path(__file__).resolve().parent / '.env'
    api_url = api_key = model_name = ''
    if _env_path.exists():
        for line in _env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line.startswith('#') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            k, v = k.strip(), v.strip()
            if k == 'METADATA_API_URL':
                api_url = v
            elif k == 'METADATA_API_KEY':
                api_key = v
            elif k == 'METADATA_MODEL_NAME':
                model_name = v
    return api_url, api_key, model_name


def _merge_guiding_by_llm(policy: dict, guiding_titles: list[str]) -> None:
    """用 LLM 从指导政策列表中匹配，合并进元数据.指导政策。"""
    if not guiding_titles:
        return

    policy_title = policy.get('元数据', {}).get('政策标题', '')
    if not policy_title:
        return

    # 排除已在指导政策中和与自身同名的
    existing = policy.get('元数据', {}).get('指导政策', [])
    existing_set = set(existing) if isinstance(existing, list) else set()
    candidates = [
        t for t in guiding_titles
        if t not in existing_set and t != policy_title
    ]
    if not candidates:
        return

    # 预筛：只保留与当前政策标题有核心词重叠的候选（比实施细则更严格，min_overlap=4）
    policy_summary = policy.get('摘要', '')
    candidates = _pre_filter_candidates_guiding(policy_title, policy_summary, candidates)

    if not candidates:
        return

    api_url, api_key, model_name = _get_llm_config()

    if not api_url or not model_name:
        print("  [警告] 缺少模型配置，跳过指导政策匹配")
        return

    matched = llm_match_guiding(policy_title, policy_summary, candidates, api_url, api_key, model_name)
    if matched:
        guiding = policy.setdefault('元数据', {}).setdefault('指导政策', [])
        if not isinstance(guiding, list):
            guiding = []
        existing_set = set(guiding)
        validated = []
        for m in matched:
            if m not in existing_set:
                # 后验证：指导政策匹配要求至少3个核心词重叠（比实施细则更严格）
                if _core_keywords_overlap(policy_title, m, min_overlap=3):
                    guiding.append(m)
                    existing_set.add(m)
                    validated.append(m)
                else:
                    print(f"  [LLM指导-过滤] 核心词不匹配，丢弃: {m}")
        if validated:
            policy['元数据']['指导政策'] = guiding
            print(f"  [LLM指导] {policy_title} -> +{validated}")

def main():
    global _IMPLEMENT_RULE_TITLES, _GUIDING_RULE_TITLES

    # 加载实施细则标题列表
    rules_path = Path('d:/Kejiju/kejiju/metadata/Attribute/实施细则.json')
    _IMPLEMENT_RULE_TITLES = load_implementation_rules(rules_path)
    print(f"已加载 {len(_IMPLEMENT_RULE_TITLES)} 条实施细则标题")

    # 加载指导政策标题列表
    guiding_path = Path('d:/Kejiju/kejiju/metadata/Attribute/指导政策.json')
    _GUIDING_RULE_TITLES = load_guiding_rules(guiding_path)
    print(f"已加载 {len(_GUIDING_RULE_TITLES)} 条指导政策标题")

    input_dir = Path('d:/Kejiju/kejiju/metadata/output')
    source_dir = Path('d:/Kejiju/kejiju/metadata/importantplus')  # 源文件目录
    output_dir = Path('d:/Kejiju/kejiju/metadata/output1')
    attribute_dir = Path('d:/Kejiju/kejiju/metadata/Attribute')

    output_dir.mkdir(parents=True, exist_ok=True)
    attribute_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    all_policies = []  # 收集所有处理过的政策数据

    for json_file in sorted(input_dir.rglob('*.json')):
        print(f"处理: {json_file}")
        # 保持子目录结构
        relative = json_file.relative_to(input_dir)
        output_path = output_dir / relative
        output_path.parent.mkdir(parents=True, exist_ok=True)
        n = process_file(json_file, output_path, source_dir)
        count += n
        print(f"  -> {n} 条政策")

        # 收集政策数据用于分类汇总
        if n > 0:
            with open(output_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if '政策数据列表' in data:
                    all_policies.extend(data['政策数据列表'])
                elif '元数据' in data:
                    all_policies.append(data)

    # ===================== 按政策属性分类输出到 Attribute 文件夹 =====================
    guiding_titles = set()
    implement_titles = set()
    notice_titles = set()

    for policy_data in all_policies:
        policy_title = policy_data.get('元数据', {}).get('政策标题', '')
        policy_attribute = policy_data.get('元数据', {}).get('政策属性', '')

        if not policy_title:
            continue

        # 指导政策分类
        if '指导政策' in policy_attribute:
            guiding_titles.add(policy_title)

        # 实施细则分类
        if '实施细则' in policy_attribute:
            implement_titles.add(policy_title)

        # 通知公告分类
        if '通知公告' in policy_attribute:
            notice_titles.add(policy_title)

    # 去重后排序
    guiding_list = sorted(list(guiding_titles))
    implement_list = sorted(list(implement_titles))
    notice_list = sorted(list(notice_titles))

    # 输出指导政策.json
    guiding_output = {
        "指导政策数": len(guiding_list),
        "政策标题列表": guiding_list,
    }
    guiding_json_path = attribute_dir / "指导政策.json"
    with open(guiding_json_path, 'w', encoding='utf-8') as f:
        json.dump(guiding_output, f, ensure_ascii=False, indent=2)
    print(f"\n指导政策汇总: {len(guiding_list)} 条 -> {guiding_json_path}")

    # 输出实施细则.json
    implement_output = {
        "实施细则数": len(implement_list),
        "政策标题列表": implement_list,
    }
    implement_json_path = attribute_dir / "实施细则.json"
    with open(implement_json_path, 'w', encoding='utf-8') as f:
        json.dump(implement_output, f, ensure_ascii=False, indent=2)
    print(f"实施细则汇总: {len(implement_list)} 条 -> {implement_json_path}")

    # 输出通知公告.json
    notice_output = {
        "通知公告数": len(notice_list),
        "政策标题列表": notice_list,
    }
    notice_json_path = attribute_dir / "通知公告.json"
    with open(notice_json_path, 'w', encoding='utf-8') as f:
        json.dump(notice_output, f, ensure_ascii=False, indent=2)
    print(f"通知公告汇总: {len(notice_list)} 条 -> {notice_json_path}")

    print(f"\n完成！共处理 {count} 条政策")

if __name__ == '__main__':
    main()