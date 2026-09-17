# -*- coding: utf-8 -*-
"""
试卷讲评课件与数据包生成核心模块
- 聚合班级学情、超均分表扬榜、分数段分布
- 聚合逐题正确率、选项人数及具体学生名单明细
- 从不含“模板”的同文件夹 Word 试卷提取原卷题干与选项文本
- 准确统计填空题/主观题各空得分率、满分名单、零分名单及典型错答词
- 导出独立免安装的 HTML 交互讲评幻灯片（支持全屏、键盘切题、选项名单点击展开、题干醒目展示）
- 导出包含完整题目原文、人名与选项分布的讲评专用 Excel 数据包
"""

import base64
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from core.grading_state import result_percentage, result_rank


def normalized_qkey(value):
    val_str = str(value or '').strip()
    match = re.search(r'(\d{1,3})', val_str)
    return str(int(match.group(1))) if match else val_str


def find_exam_word_paper(folder_path):
    """
    在指定文件夹中查找真实的考试试卷 Word 文档
    规则：文件名不含“模板”，且不以“~$”开头
    """
    if not folder_path:
        return None
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        return None

    docx_candidates = [
        p for p in folder.glob('*.docx')
        if not p.name.startswith('~$') and '模板' not in p.name
    ]
    if not docx_candidates:
        return None

    # 排除透打、排版等辅助打印文件
    primaries = [
        p for p in docx_candidates
        if not any(x in p.stem.lower() for x in ('compact_print', '透打', '打印', '答案'))
    ]
    if primaries:
        docx_candidates = primaries

    # 若文件夹名称包含在文件名中，优先匹配
    folder_key = folder.name.replace(' ', '').replace('_', '').replace('-', '').lower()
    for p in docx_candidates:
        p_key = p.stem.replace(' ', '').replace('_', '').replace('-', '').lower()
        if folder_key and folder_key in p_key:
            return p

    # 默认按文件最后修改时间取最新的一份原卷
    docx_candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return docx_candidates[0]


def extract_element_images(elem, doc):
    """
    从 Word 元素 (Paragraph / Table Cell) 中提取所有图片二进制，转换为 base64 Data URI
    兼容 DrawingML (a:blip) 与 VML (v:imagedata)
    """
    images = []
    if elem is None or doc is None:
        return images
    try:
        nodes = elem.xpath('.//*[local-name()="blip" or local-name()="imagedata"]')
        for node in nodes:
            for attr_k, attr_v in node.attrib.items():
                if attr_k.endswith('embed') or attr_k.endswith('id'):
                    target = doc.part.related_parts.get(attr_v)
                    if target and hasattr(target, 'partname') and hasattr(target, 'blob'):
                        partname = target.partname
                        ext = partname.split('.')[-1].lower() if '.' in partname else 'png'
                        if ext == 'jpg':
                            ext = 'jpeg'
                        b64 = base64.b64encode(target.blob).decode('utf-8')
                        data_uri = f'data:image/{ext};base64,{b64}'
                        if data_uri not in images:
                            images.append(data_uri)
    except Exception as exc:
        print(f"[extract_element_images] 提取图片异常: {exc}")
    return images


def parse_word_exam_paper(docx_path):
    """
    解析试卷 Word 文档，提取客观选择题（题干、选项与配图）、填空题、计算题的题目原文
    返回: { qno(int): { 'stem': str, 'options': dict, 'images': list, 'full_text': str } }
    """
    if not docx_path or not Path(docx_path).exists():
        return {}
    try:
        import docx
        doc = docx.Document(docx_path)
    except Exception as exc:
        print(f"[parse_word_exam_paper] 打开 Word 失败: {exc}")
        return {}

    blocks = []
    for child in doc.element.body:
        tag = child.tag.split('}')[-1]
        if tag == 'p':
            p = docx.text.paragraph.Paragraph(child, doc)
            txt = unicodedata.normalize('NFKC', p.text).strip()
            imgs = extract_element_images(child, doc)
            if txt or imgs:
                blocks.append({'text': txt, 'images': imgs})
        elif tag == 'tbl':
            tbl = docx.table.Table(child, doc)
            for row in tbl.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        txt = unicodedata.normalize('NFKC', p.text).strip()
                        imgs = extract_element_images(p._element, doc)
                        if txt or imgs:
                            blocks.append({'text': txt, 'images': imgs})

    # 截断答案区域（遇到“参考答案”、“【答案】”等停止，避免解析混入题干）
    clean_blocks = []
    for item in blocks:
        line = item['text']
        if line and re.match(r'^(参考答案|【\s*答案\s*】|答案与解析|答案及解析|答案\s*[:：]|【\s*参考答案\s*】)', line):
            break
        clean_blocks.append(item)

    questions = {}
    current_qno = None
    current_stem_lines = []
    current_options = {}
    current_full_lines = []
    current_images = []

    q_start_regex = re.compile(r'^([0-9]{1,3})[\.．\、\s](.*)$')
    opt_split_regex = re.compile(r'([A-G])[\.．\、\s](.*?)(?=(?:[A-G][\.．\、\s])|$)')

    def commit_current():
        nonlocal current_qno, current_stem_lines, current_options, current_full_lines, current_images
        if current_qno is not None:
            stem_text = '\n'.join(current_stem_lines).strip()
            full_text = '\n'.join(current_full_lines).strip()
            questions[current_qno] = {
                'qno': current_qno,
                'stem': stem_text,
                'options': current_options,
                'images': list(current_images),
                'full_text': full_text,
            }
        current_qno = None
        current_stem_lines = []
        current_options = {}
        current_full_lines = []
        current_images = []

    for item in clean_blocks:
        line = item['text']
        imgs = item['images']

        # 忽略大题标题，如“一、单选题（共8题）”
        if line and re.match(r'^[一二三四五六七八九十]+[\、\.\s]', line):
            commit_current()
            continue

        m = q_start_regex.match(line) if line else None
        if m:
            commit_current()
            current_qno = int(m.group(1))
            current_full_lines.append(line)
            if imgs:
                current_images.extend(imgs)
            rest = m.group(2).strip()

            opt_matches = list(opt_split_regex.finditer(rest))
            if opt_matches and opt_matches[0].start() > 0:
                stem_part = rest[:opt_matches[0].start()].strip()
                if stem_part:
                    current_stem_lines.append(stem_part)
                for om in opt_matches:
                    opt_letter = om.group(1).upper()
                    opt_val = om.group(2).strip()
                    current_options[opt_letter] = opt_val
            elif opt_matches and opt_matches[0].start() == 0:
                for om in opt_matches:
                    opt_letter = om.group(1).upper()
                    opt_val = om.group(2).strip()
                    current_options[opt_letter] = opt_val
            else:
                if rest:
                    current_stem_lines.append(rest)
            continue

        if current_qno is not None:
            if line:
                current_full_lines.append(line)
            if imgs:
                current_images.extend(imgs)
            if line:
                opt_matches = list(opt_split_regex.finditer(line))
                if opt_matches and opt_matches[0].start() == 0:
                    for om in opt_matches:
                        opt_letter = om.group(1).upper()
                        opt_val = om.group(2).strip()
                        current_options[opt_letter] = opt_val
                else:
                    current_stem_lines.append(line)

    commit_current()
    return questions


def build_lecture_presentation_data(
    summary_data,
    session_name='测试讲评',
    answer_config=None,
    answer_key=None,
    knowledge_payload=None,
    subjective_config=None,
    calc_config=None,
    word_paper_path=None,
    folder_path=None,
    submission_check=None,
):
    """
    提取课件与数据包所需的全部结构化数据
    - 支持自动绑定并读取 Word 原卷题目和选项
    - 修复填空与主观题得分匹配逻辑
    """
    summary_data = summary_data or []
    total_students = len(summary_data)
    if total_students == 0:
        return {}

    answer_config = answer_config or {}
    knowledge_payload = knowledge_payload or {}
    subjective_config = subjective_config or {}

    # 1. 尝试定位并解析 Word 原卷文档
    if not word_paper_path and folder_path:
        word_paper_path = find_exam_word_paper(folder_path)

    exam_questions_map = {}
    bound_word_name = ''
    if word_paper_path and Path(word_paper_path).exists():
        bound_word_name = Path(word_paper_path).name
        exam_questions_map = parse_word_exam_paper(word_paper_path)

    # 2. 知识点映射表 {题号: [知识点编号]}
    knowledge_map = defaultdict(list)
    for row in (knowledge_payload.get('rows') or []):
        qkey = normalized_qkey(row.get('题号'))
        code = str(row.get('知识点编号') or '').strip()
        if qkey and code and code not in knowledge_map[qkey]:
            knowledge_map[qkey].append(code)

    # 3. 学生成绩与统计
    student_scores = []
    max_total_score = 0.0
    has_any_subjective_graded = any(
        ((item.get('subjective') or {}).get('graded_count') or 0) > 0
        or bool((item.get('subjective') or {}).get('configured'))
        or bool((item.get('subjective') or {}).get('scores'))
        for item in summary_data
    )

    for item in summary_data:
        st_name = str(item.get('student_name') or '未命名').strip()
        st_id = str(item.get('score_id') or '').strip()
        obj_score = float(item.get('raw_score') or 0.0)
        obj_max = float(item.get('max_total_score') or 0.0)

        subj = item.get('subjective') or {}
        subj_score = float(subj.get('raw_score') or 0.0)
        subj_max = float(subj.get('max_score') or 0.0)

        combined_raw = item.get('combined_raw_score')
        if combined_raw is not None:
            total_score = float(combined_raw)
        else:
            total_score = obj_score + (subj_score if subj.get('include_in_total_score', False) else 0.0)

        combined_max = item.get('combined_max_score')
        if combined_max is not None:
            max_score_curr = float(combined_max)
        else:
            max_score_curr = obj_max + (subj_max if subj.get('include_in_total_score', False) else 0.0)

        if max_score_curr > max_total_score:
            max_total_score = max_score_curr

        student_scores.append({
            'name': st_name,
            'score_id': st_id,
            'total_score': round(total_score, 1),
            'obj_score': round(obj_score, 1),
            'subj_score': round(subj_score, 1),
            'file': item.get('file', ''),
            'rank': result_rank(item, summary_data),
            'ranking_score': result_percentage(item),
        })

    if max_total_score <= 0.0:
        max_total_score = 100.0

    # 统一百分制折合比例（解决第1页两种分数混杂的问题，全页统一采用100分制）
    pct_scale = 100.0 / max_total_score if max_total_score > 0 else 1.0

    for s in student_scores:
        s['score_100'] = round(s['total_score'] * pct_scale, 1)
        s['obj_score_100'] = round(s['obj_score'] * pct_scale, 1)
        s['subj_score_100'] = round(s['subj_score'] * pct_scale, 1)

    all_totals_100 = [s['score_100'] for s in student_scores]
    all_objs_100 = [s['obj_score_100'] for s in student_scores]
    all_subjs_100 = [s['subj_score_100'] for s in student_scores]

    raw_all_totals = [s['total_score'] for s in student_scores]
    raw_avg_total = round(sum(raw_all_totals) / total_students, 1)
    raw_max_score = max(raw_all_totals) if raw_all_totals else 0.0
    raw_min_score = min(raw_all_totals) if raw_all_totals else 0.0

    avg_total_score_100 = round(sum(all_totals_100) / total_students, 1)
    avg_obj_score_100 = round(sum(all_objs_100) / total_students, 1)
    avg_subj_score_100 = round(sum(all_subjs_100) / total_students, 1)
    max_score_100 = max(all_totals_100) if all_totals_100 else 0.0
    min_score_100 = min(all_totals_100) if all_totals_100 else 0.0

    # 及格 (>=60分) 与良好/优秀 (>=80分) 纯按百分制判定
    pass_students = [s for s in student_scores if s['score_100'] >= 60.0]
    pass_count = len(pass_students)
    pass_rate = round((pass_count / total_students) * 100.0, 1)

    excellent_students = [s for s in student_scores if s['score_100'] >= 80.0]
    excellent_count = len(excellent_students)
    excellent_rate = round((excellent_count / total_students) * 100.0, 1)

    # 4. 高于平均分学生表扬榜（按百分制成绩降序排列）
    above_avg_students = [s for s in student_scores if s['score_100'] >= avg_total_score_100]
    above_avg_students.sort(key=lambda x: (x['ranking_score'], x['obj_score_100']), reverse=True)
    for s in above_avg_students:
        s['diff_from_avg'] = round(s['score_100'] - avg_total_score_100, 1)

    above_avg_count = len(above_avg_students)
    above_avg_rate = round((above_avg_count / total_students) * 100.0, 1)

    # 5. 分数段统计（完全统一按 100 分制）
    bracket_defs = [
        {'name': '90-100分 (优秀)', 'tag': 'excellent', 'min_pct': 90.0, 'max_pct': 100.0, 'students': []},
        {'name': '80-89分 (良好)', 'tag': 'good', 'min_pct': 80.0, 'max_pct': 89.99, 'students': []},
        {'name': '70-79分 (中等)', 'tag': 'medium', 'min_pct': 70.0, 'max_pct': 79.99, 'students': []},
        {'name': '60-69分 (及格)', 'tag': 'pass', 'min_pct': 60.0, 'max_pct': 69.99, 'students': []},
        {'name': '60分以下 (待加强)', 'tag': 'fail', 'min_pct': 0.0, 'max_pct': 59.99, 'students': []},
    ]
    for s in student_scores:
        eq_score = s['score_100']
        assigned = False
        for b in bracket_defs:
            if b['min_pct'] <= eq_score <= b['max_pct']:
                b['students'].append(s['name'])
                assigned = True
                break
        if not assigned:
            if eq_score < 60.0:
                bracket_defs[-1]['students'].append(s['name'])
            else:
                bracket_defs[0]['students'].append(s['name'])

    for b in bracket_defs:
        b['count'] = len(b['students'])
        b['pct'] = round((b['count'] / total_students) * 100.0, 1)

    # 6. 逐题讲评数据
    questions_data = []

    def get_standard_answer_for(q_no, zone_name=None):
        if answer_key:
            if isinstance(answer_key, dict):
                if '__global__' in answer_key and str(q_no) in answer_key['__global__']:
                    val = answer_key['__global__'][str(q_no)]
                    return ''.join(sorted([str(x).upper() for x in val])) if isinstance(val, (list, set)) else str(val).upper()
                if str(q_no) in answer_key:
                    val = answer_key[str(q_no)]
                    return ''.join(sorted([str(x).upper() for x in val])) if isinstance(val, (list, set)) else str(val).upper()
                if zone_name and zone_name in answer_key and str(q_no) in answer_key[zone_name]:
                    val = answer_key[zone_name][str(q_no)]
                    return ''.join(sorted([str(x).upper() for x in val])) if isinstance(val, (list, set)) else str(val).upper()
        # Fallback to summary_data
        for item in summary_data:
            zb = (item.get('zone_breakdowns') or {}).get(zone_name or '')
            if zb and 'question_numbers' in zb and 'standard_answers' in zb:
                try:
                    idx = zb['question_numbers'].index(q_no)
                    if idx < len(zb['standard_answers']):
                        ans = zb['standard_answers'][idx]
                        if ans:
                            return ''.join(sorted([str(x).upper() for x in ans])) if isinstance(ans, (list, set)) else str(ans).upper()
                except Exception:
                    pass
        return ''

    # 6.1 客观选择题分析
    zones = []
    if isinstance(answer_config, dict):
        if answer_config.get('zones'):
            zones = answer_config.get('zones')
        elif answer_config.get('direct_paper_structure', {}).get('answer_config', {}).get('zones'):
            zones = answer_config.get('direct_paper_structure', {}).get('answer_config', {}).get('zones')
        elif answer_config.get('template_structure', {}).get('zones'):
            zones = answer_config.get('template_structure', {}).get('zones')
        elif answer_config.get('answer_config', {}).get('zones'):
            zones = answer_config.get('answer_config', {}).get('zones')
        elif answer_config.get('questions'):
            zones = [answer_config]

    # 如果仍无 zones，从 summary_data 或 answer_key 自动发现客观题结构
    if not zones and summary_data:
        first_answers = (summary_data[0].get('answers') or {})
        for zname, zdict in first_answers.items():
            if isinstance(zdict, dict) and zdict:
                q_list = []
                for qk in sorted(zdict.keys(), key=lambda x: int(x) if x.isdigit() else 999):
                    q_list.append({
                        'question_no_in_zone': int(qk) if qk.isdigit() else qk,
                        'question_type': 'single',
                        'score': 3.0,
                    })
                if q_list:
                    zones.append({
                        'zone_name': zname,
                        'title': zname,
                        'questions': q_list,
                    })

    if not zones and answer_key and isinstance(answer_key, dict):
        for zk, zv in answer_key.items():
            if isinstance(zv, dict) and zv and zk != '__global__':
                q_list = []
                for qk in sorted(zv.keys(), key=lambda x: int(x) if x.isdigit() else 999):
                    q_list.append({
                        'question_no_in_zone': int(qk) if qk.isdigit() else qk,
                        'question_type': 'single',
                        'score': 3.0,
                    })
                if q_list:
                    zones.append({
                        'zone_name': zk,
                        'title': zk,
                        'questions': q_list,
                    })

    for zone in zones:
        zone_name = zone.get('zone_name')
        zone_title = zone.get('title') or zone_name
        for question in zone.get('questions', []):
            q_no = question.get('question_no_in_zone')
            if q_no is None:
                continue
            qkey = normalized_qkey(q_no)
            qtype = question.get('question_type', 'single')
            qscore = float(question.get('score', 3.0) or 3.0)

            # 匹配 Word 试卷中的题目题干与选项文字
            q_num_int = int(qkey) if qkey.isdigit() else None
            word_q = exam_questions_map.get(q_num_int) if q_num_int else exam_questions_map.get(qkey)
            stem_text = word_q.get('stem', '') if word_q else ''
            options_text_map = word_q.get('options', {}) if word_q else {}
            q_images = word_q.get('images', []) if word_q else []

            # 标准答案解析
            expected_str = get_standard_answer_for(q_no, zone_name)
            if not expected_str:
                exp_list = question.get('expected_answers') or question.get('standard_answers') or []
                if not exp_list and isinstance(question.get('options'), dict):
                    exp_list = [opt_k for opt_k, opt_v in question['options'].items() if opt_v.get('is_correct')]
                expected_str = ''.join(sorted([str(x).upper() for x in exp_list])) if exp_list else ''

            # 统计每个学生的实际选项
            option_students = defaultdict(list)
            correct_students = []
            wrong_students = []

            for item in summary_data:
                st_name = str(item.get('student_name') or '未命名').strip()
                ans_info = (item.get('answers') or {}).get(zone_name, {}).get(str(q_no)) or {}
                selected = ans_info.get('selected') or []
                if not selected:
                    for zk, zv in (item.get('answers') or {}).items():
                        if str(q_no) in zv:
                            selected = zv[str(q_no)].get('selected') or []
                            break

                norm_selected = ''.join(sorted([str(x).upper() for x in selected]))
                if not norm_selected:
                    norm_selected = '未作答'

                option_students[norm_selected].append(st_name)

                # 判断对错（以标准答案比对）
                if expected_str and norm_selected == expected_str:
                    correct_students.append(st_name)
                else:
                    wrong_students.append({
                        'name': st_name,
                        'selected': norm_selected,
                    })

            correct_count = len(correct_students)
            wrong_count = len(wrong_students)
            accuracy = round((correct_count / total_students) * 100.0, 1) if total_students else 0.0

            # 整理选项卡片
            options_cards = []
            standard_options = ['A', 'B', 'C', 'D']

            wrong_counter = Counter()
            for opt, st_list in option_students.items():
                if opt != expected_str and opt != '未作答':
                    wrong_counter[opt] = len(st_list)
            most_common_wrong = wrong_counter.most_common(1)[0] if wrong_counter else ('无', 0)

            all_seen_keys = set(option_students.keys())
            ordered_keys = [opt for opt in standard_options]
            for extra_key in sorted(all_seen_keys):
                if extra_key not in ordered_keys and extra_key != '未作答':
                    ordered_keys.append(extra_key)
            if '未作答' in all_seen_keys:
                ordered_keys.append('未作答')

            for key in ordered_keys:
                st_list = option_students.get(key, [])
                count = len(st_list)
                pct = round((count / total_students) * 100.0, 1)
                is_correct = bool(expected_str and key == expected_str)
                is_distractor = bool(not is_correct and key == most_common_wrong[0] and count >= 3)
                card_text = options_text_map.get(key, '')

                options_cards.append({
                    'key': key,
                    'text': card_text,
                    'is_correct': is_correct,
                    'is_distractor': is_distractor,
                    'count': count,
                    'pct': pct,
                    'students': st_list,
                })

            suggestion = ''
            if accuracy >= 90.0:
                suggestion = '全班掌握极好，整体正确率达90%以上，可快速带过。'
            elif accuracy < 50.0:
                if most_common_wrong[0] != '无':
                    suggestion = f'高危错题（正确率低于50%），{most_common_wrong[1]} 人误选干扰项 {most_common_wrong[0]}，需课堂重点剖析考点与解题突破口。'
                else:
                    suggestion = '高危错题，错误学生分布分散，建议重讲基本原理与概念。'
            elif most_common_wrong[0] != '无' and most_common_wrong[1] >= 6:
                suggestion = f'典型易错题，主要失分集中在选项 {most_common_wrong[0]}（{most_common_wrong[1]}人），建议引导学生对比选项 {most_common_wrong[0]} 与正确答案 {expected_str} 的本质区别。'
            else:
                suggestion = '整体掌握良好，适度提醒个别马虎失分同学。'

            questions_data.append({
                'category': 'objective',
                'qno': q_no,
                'qkey': qkey,
                'zone_name': zone_name,
                'zone_title': zone_title,
                'title': f'第 {q_no} 题',
                'stem': stem_text,
                'images': q_images,
                'options_text': options_text_map,
                'qtype': '单选题' if qtype == 'single' else ('多选题' if qtype == 'multi' else '选择题'),
                'max_score': qscore,
                'expected': expected_str or '未设',
                'knowledge_codes': knowledge_map.get(qkey, []),
                'accuracy': accuracy,
                'correct_count': correct_count,
                'wrong_count': wrong_count,
                'most_common_wrong': most_common_wrong[0],
                'most_common_wrong_count': most_common_wrong[1],
                'options_cards': options_cards,
                'teaching_suggestion': suggestion,
            })

    # 6.2 主观填空题分析
    # 正确支持 items 及其内部 blanks，并按题号聚合
    if has_any_subjective_graded:
        raw_subj_items = subjective_config.get('items', []) if isinstance(subjective_config, dict) else []

        # 整理所有空位，并按大题号归组
        grouped_blanks = defaultdict(list)
        seen_part_ids = set()

        for item_meta in raw_subj_items:
            qno = str(item_meta.get('question_no') or '')
            q_label = str(item_meta.get('label') or item_meta.get('answer_label') or qno).strip()
            blanks = item_meta.get('blanks') or []

            if blanks:
                for b_idx, blank in enumerate(blanks, 1):
                    bno = blank.get('blank_no', b_idx)
                    pid = blank.get('part_id') or f"q{qno}_b{bno}"
                    if pid in seen_part_ids:
                        continue
                    seen_part_ids.add(pid)

                    ocr_cfg = blank.get('ocr') or {}
                    exp = ocr_cfg.get('expected_answer') or blank.get('expected_answer') or item_meta.get('expected_answer') or ''
                    max_s = float(blank.get('score') or item_meta.get('max_score') or 1.0)
                    b_label = str(blank.get('label') or q_label).strip()
                    parent_q = normalized_qkey(b_label or q_label)

                    grouped_blanks[parent_q].append({
                        'part_id': pid,
                        'clean_id': pid.lstrip('q'),
                        'qno': qno,
                        'bno': bno,
                        'label': b_label,
                        'max_score': max_s,
                        'expected': exp,
                        'kind': item_meta.get('kind', 'fill_blank'),
                    })
            else:
                pid = item_meta.get('part_id') or f"q{qno}"
                if pid in seen_part_ids:
                    continue
                seen_part_ids.add(pid)
                exp = str(item_meta.get('expected_answer') or '').strip()
                max_s = float(item_meta.get('max_score', 1.0) or 1.0)
                parent_q = normalized_qkey(q_label)

                grouped_blanks[parent_q].append({
                    'part_id': pid,
                    'clean_id': pid.lstrip('q'),
                    'qno': qno,
                    'bno': 1,
                    'label': q_label,
                    'max_score': max_s,
                    'expected': exp,
                    'kind': item_meta.get('kind', 'fill_blank'),
                })

        # 按大题号排序生成题目讲评数据
        for parent_q, blist in sorted(grouped_blanks.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999):
            # 获取 Word 试卷中的原题干
            q_num_int = int(parent_q) if parent_q.isdigit() else None
            word_q = exam_questions_map.get(q_num_int) if q_num_int else exam_questions_map.get(parent_q)
            stem_text = word_q.get('stem', '') if word_q else ''
            q_images = word_q.get('images', []) if word_q else []

            blanks_detail = []
            q_total_score = 0.0
            q_total_avg = 0.0
            low_acc_blanks = []

            for b_idx, b in enumerate(blist, 1):
                pid = b['part_id']
                clean_id = b['clean_id']
                qno_raw = b['qno']
                b_max = b['max_score']
                q_total_score += b_max

                full_stus = []
                part_stus = []
                zero_stus = []
                scores_sum = 0.0
                wrong_ans_counter = Counter()

                for st in summary_data:
                    s_name = str(st.get('student_name') or '未命名').strip()
                    subj_scores = ((st.get('subjective') or {}).get('scores') or {})

                    # 多级兼容匹配打分记录
                    rec = (
                        subj_scores.get(pid)
                        or subj_scores.get(clean_id)
                        or subj_scores.get(qno_raw)
                        or subj_scores.get(f"q{qno_raw}")
                    )

                    score_val = 0.0
                    ans_text = ''
                    if isinstance(rec, dict):
                        score_val = float(rec.get('score') or 0.0)
                        ans_text = str(rec.get('auto_grade_answer') or rec.get('paddle_ocr_text') or '').strip()
                    elif isinstance(rec, (int, float)):
                        score_val = float(rec)

                    scores_sum += score_val

                    if score_val >= b_max - 1e-6:
                        full_stus.append(s_name)
                    elif score_val > 0.0:
                        part_stus.append({'name': s_name, 'score': round(score_val, 1), 'ans': ans_text})
                    else:
                        zero_stus.append(s_name)
                        if ans_text and ans_text != '未识别/未作答':
                            wrong_ans_counter[ans_text] += 1

                b_avg = round(scores_sum / total_students, 2) if total_students else 0.0
                b_acc = round((b_avg / b_max) * 100.0, 1) if b_max else 0.0
                q_total_avg += b_avg

                if b_acc < 60.0:
                    low_acc_blanks.append((b_idx, b_acc))

                common_wrong = wrong_ans_counter.most_common(3)
                blank_label_str = f"第 ({b_idx}) 空" if len(blist) > 1 else "填空"

                blanks_detail.append({
                    'blank_index': b_idx,
                    'part_id': pid,
                    'label': blank_label_str,
                    'expected': b['expected'] or '详见评分参考',
                    'max_score': b_max,
                    'avg_score': b_avg,
                    'accuracy': b_acc,
                    'full_score_students': full_stus,
                    'partial_score_students': part_stus,
                    'zero_score_students': zero_stus,
                    'common_wrong': common_wrong,
                })

            overall_q_acc = round((q_total_avg / q_total_score) * 100.0, 1) if q_total_score else 0.0

            # 诊断建议
            if overall_q_acc >= 85.0:
                sug = '全班掌握扎实，核心概念理解准确，可快速核对答案。'
            elif low_acc_blanks:
                low_desc = '、'.join([f"第({b[0]})空(正确率{b[1]}%)" for b in low_acc_blanks])
                sug = f'失分重点在 {low_desc}，需针对性剖析学生典型填答漏洞与规范术语。'
            else:
                sug = '中等掌握水平，注意提醒学生规范书写与关键词准确度。'

            questions_data.append({
                'category': 'subjective',
                'qno': parent_q,
                'qkey': parent_q,
                'title': f'第 {parent_q} 题',
                'stem': stem_text,
                'images': q_images,
                'qtype': f'填空题 (共{len(blist)}空)' if len(blist) > 1 else '填空题',
                'max_score': round(q_total_score, 1),
                'avg_score': round(q_total_avg, 2),
                'accuracy': overall_q_acc,
                'expected': '；'.join([f"({b['blank_index']}) {b['expected']}" for b in blanks_detail]) if len(blanks_detail) > 1 else (blanks_detail[0]['expected'] if blanks_detail else ''),
                'knowledge_codes': knowledge_map.get(parent_q, []),
                'blanks': blanks_detail,
                'teaching_suggestion': sug,
            })

    # 6.3 计算题分析
    calc_meta = (subjective_config.get('calculation_questions') or {}) if isinstance(subjective_config, dict) else {}
    if calc_meta.get('enabled') and calc_meta.get('questions') and has_any_subjective_graded:
        for calc_q in calc_meta.get('questions', []):
            cq_no = calc_q.get('question_no')
            cq_score = float(calc_q.get('max_score', 0.0) or 0.0)
            cq_key = normalized_qkey(cq_no)

            # 获取 Word 试卷中的计算题题干与小问
            q_num_int = int(cq_key) if cq_key.isdigit() else None
            word_q = exam_questions_map.get(q_num_int) if q_num_int else exam_questions_map.get(cq_key)
            stem_text = word_q.get('stem', '') if word_q else ''
            q_images = word_q.get('images', []) if word_q else []

            calc_full = []
            calc_part = []
            calc_zero = []
            calc_sum = 0.0
            for item in summary_data:
                st_name = str(item.get('student_name') or '未命名').strip()
                c_scores = ((item.get('subjective') or {}).get('calculation_scores') or {})
                c_rec = c_scores.get(str(cq_no)) or c_scores.get(cq_no)
                val = float(c_rec.get('score', 0.0) if isinstance(c_rec, dict) else (c_rec or 0.0))
                calc_sum += val
                if val >= cq_score - 1e-6:
                    calc_full.append(st_name)
                elif val > 0.0:
                    calc_part.append({'name': st_name, 'score': round(val, 1)})
                else:
                    calc_zero.append(st_name)

            c_avg = round(calc_sum / total_students, 1) if total_students else 0.0
            c_acc = round((c_avg / cq_score) * 100.0, 1) if cq_score else 0.0

            questions_data.append({
                'category': 'calculation',
                'qno': cq_no,
                'qkey': cq_key,
                'title': f'第 {cq_no} 题 计算题',
                'stem': stem_text,
                'images': q_images,
                'qtype': '计算题',
                'max_score': cq_score,
                'expected': '详见解题步骤与分步赋分',
                'knowledge_codes': knowledge_map.get(cq_key, []),
                'avg_score': c_avg,
                'accuracy': c_acc,
                'full_score_students': calc_full,
                'partial_score_students': calc_part,
                'zero_score_students': calc_zero,
                'teaching_suggestion': f'得分率 {c_acc}%，平均分 {c_avg}/{cq_score}，失分学生 {len(calc_zero) + len(calc_part)} 人。重点纠正解题公式、单位换算与计算步骤规范。',
            })

    return {
        'meta': {
            'title': session_name,
            'bound_word_paper': bound_word_name,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'total_students': total_students,
            # 统一百分制字段 (消除第1页两种分数混杂的困扰)
            'max_total_score': 100.0,
            'raw_max_total_score': max_total_score,
            'avg_total_score': avg_total_score_100,
            'raw_avg_total_score': raw_avg_total,
            'avg_obj_score': avg_obj_score_100,
            'avg_subj_score': avg_subj_score_100,
            'max_score': max_score_100,
            'raw_max_score': raw_max_score,
            'min_score': min_score_100,
            'raw_min_score': raw_min_score,
            'pass_count': pass_count,
            'pass_rate': pass_rate,
            'excellent_count': excellent_count,
            'excellent_rate': excellent_rate,
            'above_avg_count': above_avg_count,
            'above_avg_rate': above_avg_rate,
        },
        'honor_roll': above_avg_students,
        'brackets': bracket_defs,
        'questions': questions_data,
        'submission_check': submission_check,
    }


def export_lecture_excel(data, output_path):
    """
    导出课件专用 Excel 数据包（包含题目题干、选项文本、学生名单明细）
    """
    try:
        import pandas as pd
    except Exception:
        pd = None

    if pd is None:
        raise RuntimeError('缺少 pandas 库，无法生成 Excel 文件。')

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    meta = data.get('meta', {})
    honor_roll = data.get('honor_roll', [])
    brackets = data.get('brackets', [])
    questions = data.get('questions', [])

    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        # Sheet 1: 超越平均分光荣榜
        honor_rows = []
        for s in honor_roll:
            honor_rows.append({
                '名次': f"第 {s['rank']} 名",
                '姓名': s['name'],
                '学号': s['score_id'],
                '总分': s['total_score'],
                '客观分': s['obj_score'],
                '主观分': s['subj_score'],
                '高于均分差值': f"+{s['diff_from_avg']} 分",
            })
        df_honor = pd.DataFrame(honor_rows)
        df_honor.to_excel(writer, sheet_name='超越平均分光荣榜', index=False)

        # Sheet 2: 班级学情与分数段
        bracket_rows = [
            {'统计项/分数区间': '试卷名称', '数值/人数': meta.get('title'), '占比/说明': '本次评讲试卷'},
            {'统计项/分数区间': '关联试卷Word', '数值/人数': meta.get('bound_word_paper') or '未检测到同目录试卷Word', '占比/说明': '题目题干提取源'},
            {'统计项/分数区间': '参考总人数', '数值/人数': f"{meta.get('total_students')} 人", '占比/说明': '实考人数'},
            {'统计项/分数区间': '全班总均分', '数值/人数': f"{meta.get('avg_total_score')} 分", '占比/说明': f"客观均分 {meta.get('avg_obj_score')} / 主观均分 {meta.get('avg_subj_score')}"},
            {'统计项/分数区间': '最高分 (状元)', '数值/人数': f"{meta.get('max_score')} 分", '占比/说明': f"最低分 {meta.get('min_score')} 分"},
            {'统计项/分数区间': '及格率 (>=60%)', '数值/人数': f"{meta.get('pass_count')} 人", '占比/说明': f"{meta.get('pass_rate')}%"},
            {'统计项/分数区间': '优秀率 (>=85%)', '数值/人数': f"{meta.get('excellent_count')} 人", '占比/说明': f"{meta.get('excellent_rate')}%"},
            {'统计项/分数区间': '超均分人数', '数值/人数': f"{meta.get('above_avg_count')} 人", '占比/说明': f"{meta.get('above_avg_rate')}%"},
            {'统计项/分数区间': '----------------', '数值/人数': '----------', '占比/说明': '----------------'},
        ]
        for b in brackets:
            bracket_rows.append({
                '统计项/分数区间': b['name'],
                '数值/人数': f"{b['count']} 人",
                '占比/说明': f"{b['pct']}% (包含: {'、'.join(b['students']) if b['students'] else '无'})",
            })
        df_bracket = pd.DataFrame(bracket_rows)
        df_bracket.to_excel(writer, sheet_name='班级学情与分数段', index=False)

        # Sheet 3: 客观题逐题选项与名单明细
        obj_rows = []
        for q in [item for item in questions if item.get('category') == 'objective']:
            cards_map = {c['key']: c for c in q.get('options_cards', [])}
            opts_text = q.get('options_text', {})

            def fmt_opt(opt_key):
                card = cards_map.get(opt_key)
                if not card:
                    return 0, '无'
                cnt = card['count']
                names = '、'.join(card['students']) if card['students'] else '无'
                return cnt, names

            a_cnt, a_names = fmt_opt('A')
            b_cnt, b_names = fmt_opt('B')
            c_cnt, c_names = fmt_opt('C')
            d_cnt, d_names = fmt_opt('D')
            unans_cnt, unans_names = fmt_opt('未作答')

            extra_opts = [k for k in cards_map if k not in ('A', 'B', 'C', 'D', '未作答')]
            extra_info = []
            for ek in extra_opts:
                ecard = cards_map[ek]
                extra_info.append(f"{ek}({ecard['count']}人): {'、'.join(ecard['students'])}")
            extra_str = '；'.join(extra_info) if extra_info else '无'

            obj_rows.append({
                '题号': q['qno'],
                '题型': q['qtype'],
                '题目题干': q.get('stem') or '暂无题目原文',
                '标准答案': q['expected'],
                '关联知识点': '、'.join(q.get('knowledge_codes', [])) or '未标',
                '满分': q['max_score'],
                '全班正确率': f"{q['accuracy']}%",
                '正确人数': q['correct_count'],
                '错误人数': q['wrong_count'],
                '选项A文本': opts_text.get('A', ''),
                '选A人数': a_cnt,
                '选A名单': a_names,
                '选项B文本': opts_text.get('B', ''),
                '选B人数': b_cnt,
                '选B名单': b_names,
                '选项C文本': opts_text.get('C', ''),
                '选C人数': c_cnt,
                '选C名单': c_names,
                '选项D文本': opts_text.get('D', ''),
                '选D人数': d_cnt,
                '选D名单': d_names,
                '未作答人数': unans_cnt,
                '未作答名单': unans_names,
                '其他组合选项': extra_str,
                '主要干扰项': q['most_common_wrong'],
                '讲评诊断建议': q['teaching_suggestion'],
            })
        df_obj = pd.DataFrame(obj_rows)
        df_obj.to_excel(writer, sheet_name='客观题逐题选项与名单', index=False)

        # Sheet 4: 主观与填空题明细
        subj_questions = [item for item in questions if item.get('category') in ('subjective', 'calculation')]
        if subj_questions:
            subj_rows = []
            for q in subj_questions:
                if q.get('category') == 'subjective':
                    blanks = q.get('blanks', [])
                    for b in blanks:
                        part_stu = [f"{p['name']}({p['score']}分)" for p in b.get('partial_score_students', [])]
                        c_wrong_str = '、'.join([f"{ans}({cnt}人)" for ans, cnt in b.get('common_wrong', [])])
                        subj_rows.append({
                            '题号': q['qno'],
                            '空位标签': b['label'],
                            '题目题干': q.get('stem') or '暂无题目原文',
                            '标准参考答案': b['expected'],
                            '关联知识点': '、'.join(q.get('knowledge_codes', [])) or '未标',
                            '满分': b['max_score'],
                            '平均得分': b['avg_score'],
                            '得分率': f"{b['accuracy']}%",
                            '满分人数': len(b.get('full_score_students', [])),
                            '满分名单': '、'.join(b.get('full_score_students', [])) or '无',
                            '部分得分人数': len(part_stu),
                            '部分得分名单': '、'.join(part_stu) or '无',
                            '零分/失分人数': len(b.get('zero_score_students', [])),
                            '零分/失分名单': '、'.join(b.get('zero_score_students', [])) or '无',
                            '常见典型错答': c_wrong_str or '无明显聚集',
                            '本大题讲评诊断建议': q['teaching_suggestion'],
                        })
                elif q.get('category') == 'calculation':
                    part_stu = [f"{p['name']}({p['score']}分)" for p in q.get('partial_score_students', [])]
                    subj_rows.append({
                        '题号': q['qno'],
                        '空位标签': '整题计算',
                        '题目题干': q.get('stem') or '暂无题目原文',
                        '标准参考答案': q['expected'],
                        '关联知识点': '、'.join(q.get('knowledge_codes', [])) or '未标',
                        '满分': q['max_score'],
                        '平均得分': q['avg_score'],
                        '得分率': f"{q['accuracy']}%",
                        '满分人数': len(q.get('full_score_students', [])),
                        '满分名单': '、'.join(q.get('full_score_students', [])) or '无',
                        '部分得分人数': len(part_stu),
                        '部分得分名单': '、'.join(part_stu) or '无',
                        '零分/失分人数': len(q.get('zero_score_students', [])),
                        '零分/失分名单': '、'.join(q.get('zero_score_students', [])) or '无',
                        '常见典型错答': '详见步骤扣分',
                        '本大题讲评诊断建议': q['teaching_suggestion'],
                    })
            df_subj = pd.DataFrame(subj_rows)
            df_subj.to_excel(writer, sheet_name='主观与填空题明细', index=False)


RAW_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>【讲评课件】__PAGE_TITLE__</title>
<style>
  :root {
    --bg-primary: #f1f5f9;
    --bg-secondary: #ffffff;
    --bg-card: #f8fafc;
    --text-primary: #000000;
    --text-secondary: #334155;
    --accent: #0284c7;
    --accent-hover: #0369a1;
    --success: #15803d;
    --warning: #b45309;
    --danger: #b91c1c;
    --card-border: #cbd5e1;
    --card-shadow: 0 4px 14px rgba(0, 0, 0, 0.06);
    --tag-bg: #e2e8f0;
    --tag-text: #0f172a;
    --stem-bg: #ffffff;
    --option-bg: #ffffff;
    --option-correct-bg: #f0fdf4;
    --option-distractor-bg: #fef2f2;
    --wb-dock-bg: rgba(255, 255, 255, 0.98);
    --wb-dock-border: #94a3b8;
    --wb-dock-shadow: 0 14px 36px rgba(0, 0, 0, 0.18);
    --wb-btn-bg: #f1f5f9;
    --wb-btn-border: #94a3b8;
    --wb-btn-text: #0f172a;
    --font-scale: 1.35;
    --current-wb-color: #ef4444;
  }

  [data-theme="dark"] {
    --bg-primary: #0a0f1d;
    --bg-secondary: #131f37;
    --bg-card: #1c2b48;
    --text-primary: #ffffff;
    --text-secondary: #94a3b8;
    --accent: #38bdf8;
    --accent-hover: #0ea5e9;
    --success: #22c55e;
    --warning: #f59e0b;
    --danger: #ef4444;
    --card-border: #293d61;
    --card-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
    --tag-bg: #293d61;
    --tag-text: #f1f5f9;
    --stem-bg: linear-gradient(180deg, var(--bg-secondary) 0%, rgba(19, 31, 55, 0.9) 100%);
    --option-bg: var(--bg-secondary);
    --option-correct-bg: linear-gradient(180deg, rgba(34, 197, 94, 0.15) 0%, var(--bg-secondary) 100%);
    --option-distractor-bg: linear-gradient(180deg, rgba(239, 68, 68, 0.15) 0%, var(--bg-secondary) 100%);
    --wb-dock-bg: rgba(15, 23, 42, 0.92);
    --wb-dock-border: rgba(255, 255, 255, 0.18);
    --wb-dock-shadow: 0 14px 40px rgba(0, 0, 0, 0.55);
    --wb-btn-bg: rgba(255, 255, 255, 0.08);
    --wb-btn-border: rgba(255, 255, 255, 0.12);
    --wb-btn-text: #f1f5f9;
  }

  * {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
    background-color: var(--bg-primary);
    color: var(--text-primary);
    height: 100vh;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    user-select: none;
    transition: background-color 0.25s ease, color 0.25s ease;
  }

  /* Compact Top Header (48px) */
  header {
    height: 48px;
    background-color: var(--bg-secondary);
    border-bottom: 1px solid var(--card-border);
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 20px;
    flex-shrink: 0;
    z-index: 100;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .brand h1 {
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--text-primary);
    letter-spacing: 0.5px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .jump-select {
    background-color: var(--bg-card);
    color: var(--text-primary);
    border: 1px solid var(--card-border);
    border-radius: 6px;
    padding: 4px 12px;
    font-size: 0.9rem;
    outline: none;
    cursor: pointer;
    max-width: 420px;
  }
  .header-actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .font-scale-group {
    display: flex;
    align-items: center;
    background-color: var(--bg-card);
    border: 1px solid var(--card-border);
    border-radius: 6px;
    padding: 2px;
  }
  .font-btn {
    background: transparent;
    border: none;
    color: var(--text-secondary);
    padding: 3px 8px;
    font-size: 0.82rem;
    font-weight: 600;
    border-radius: 4px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .font-btn:hover {
    color: var(--text-primary);
    background-color: var(--bg-primary);
  }
  .font-btn.active {
    background-color: var(--accent);
    color: #ffffff;
  }
  .btn {
    background-color: var(--bg-card);
    color: var(--text-primary);
    border: 1px solid var(--card-border);
    border-radius: 6px;
    padding: 5px 12px;
    font-size: 0.88rem;
    font-weight: 600;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    transition: all 0.2s ease;
  }
  .btn:hover {
    background-color: var(--tag-bg);
    border-color: var(--accent);
  }
  .btn-primary {
    background-color: var(--accent);
    color: #ffffff;
    border-color: var(--accent);
  }
  .btn-primary:hover {
    background-color: var(--accent-hover);
    border-color: var(--accent-hover);
  }

  /* Main Viewport */
  main {
    flex: 1;
    overflow: hidden;
    position: relative;
  }
  .slide {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    padding: 10px 24px;
    opacity: 0;
    pointer-events: none;
    transform: translateX(30px);
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .slide.active {
    opacity: 1;
    pointer-events: auto;
    transform: translateX(0);
  }

  /* Slide 1: 班级总览与超均分表扬榜 (纯百分制) */
  .metrics-grid {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 10px;
    margin-bottom: 10px;
    flex-shrink: 0;
  }
  .metric-card {
    background-color: var(--bg-secondary);
    border: 1px solid var(--card-border);
    border-radius: 10px;
    padding: 10px 14px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    box-shadow: var(--card-shadow);
  }
  .metric-card .label {
    font-size: 0.82rem;
    color: var(--text-secondary);
    margin-bottom: 2px;
  }
  .metric-card .value {
    font-size: 1.65rem;
    font-weight: 800;
    color: var(--accent);
  }
  .metric-card .sub-value {
    font-size: 0.72rem;
    color: var(--text-secondary);
    margin-top: 2px;
  }

  .overview-content {
    display: grid;
    grid-template-columns: 1.3fr 1fr;
    gap: 14px;
    flex: 1;
    min-height: 0;
  }
  .section-card {
    background-color: var(--bg-secondary);
    border: 1px solid var(--card-border);
    border-radius: 12px;
    padding: 14px 18px;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: var(--card-shadow);
  }
  .section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
    border-bottom: 1px solid var(--card-border);
    padding-bottom: 8px;
  }
  .section-header h2 {
    font-size: 1.1rem;
    font-weight: 700;
    color: var(--text-primary);
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .badge-count {
    background-color: rgba(2, 132, 199, 0.12);
    color: var(--accent);
    padding: 2px 8px;
    border-radius: 9999px;
    font-size: 0.82rem;
    font-weight: 700;
  }

  .honor-scroll {
    flex: 1;
    overflow-y: auto;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 10px;
    padding-right: 6px;
  }
  .honor-item {
    background-color: var(--bg-card);
    border: 1.5px solid var(--card-border);
    border-radius: 8px;
    padding: 10px 12px;
    display: flex;
    flex-direction: column;
    box-shadow: var(--card-shadow);
  }
  .honor-item.top-1 { border-color: #f59e0b; background: linear-gradient(135deg, rgba(245, 158, 11, 0.12), var(--bg-card)); }
  .honor-item.top-2 { border-color: #94a3b8; background: linear-gradient(135deg, rgba(148, 163, 184, 0.12), var(--bg-card)); }
  .honor-item.top-3 { border-color: #d97706; background: linear-gradient(135deg, rgba(217, 119, 6, 0.12), var(--bg-card)); }
  .honor-rank {
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--text-secondary);
    display: flex;
    justify-content: space-between;
  }
  .honor-name {
    font-size: 1.12rem;
    font-weight: 700;
    color: var(--text-primary);
    margin: 4px 0 2px 0;
  }
  .honor-score {
    font-size: 1.45rem;
    font-weight: 800;
    color: var(--accent);
  }
  .honor-diff {
    font-size: 0.8rem;
    color: var(--success);
    font-weight: 700;
    margin-top: 2px;
    background-color: rgba(22, 163, 74, 0.12);
    padding: 2px 6px;
    border-radius: 4px;
    display: inline-block;
    align-self: flex-start;
  }

  .distribution-container {
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: space-around;
    gap: 8px;
    padding-right: 6px;
  }
  .bracket-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .bracket-info {
    display: flex;
    justify-content: space-between;
    font-size: 0.95rem;
    color: var(--text-primary);
  }
  .bracket-bar-bg {
    height: 16px;
    background-color: #e2e8f0;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    overflow: hidden;
    box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.08);
  }
  [data-theme="dark"] .bracket-bar-bg {
    background-color: #1e293b;
    border-color: #334155;
    box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.3);
  }
  .bracket-bar-fill {
    height: 100%;
    border-radius: 7px;
    transition: width 0.4s ease;
  }
  .bracket-bar-fill.excellent { background: linear-gradient(90deg, #f59e0b, #d97706); }
  .bracket-bar-fill.good { background: linear-gradient(90deg, #3b82f6, #2563eb); }
  .bracket-bar-fill.medium { background: linear-gradient(90deg, #0d9488, #14b8a6); }
  .bracket-bar-fill.pass { background: linear-gradient(90deg, #10b981, #059669); }
  .bracket-bar-fill.fail { background: linear-gradient(90deg, #ef4444, #dc2626); }
  .bracket-bar-fill.improve { background: linear-gradient(90deg, #64748b, #475569); }
  .bracket-bar-fill.danger { background: linear-gradient(90deg, #dc2626, #991b1b); }

  /* Slide 2: 提交检查情况 (大字展开，学生名不折行) */
  .submission-metrics-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 14px;
    flex-shrink: 0;
  }
  .submission-content-layout {
    display: grid;
    grid-template-columns: 1.15fr 1fr;
    gap: 16px;
    flex: 1;
    min-height: 0;
  }
  .unsubmitted-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
    gap: 10px;
    overflow-y: auto;
    padding-right: 6px;
    max-height: calc(100vh - 270px);
  }
  .unsubmitted-tag {
    background: var(--bg-card);
    border: 1.5px solid var(--card-border);
    border-radius: 10px;
    padding: 10px 14px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    box-shadow: var(--card-shadow);
    transition: all 0.15s ease;
    white-space: nowrap;
  }
  .unsubmitted-tag:hover {
    border-color: #ef4444;
    transform: translateY(-2px);
    box-shadow: 0 6px 16px rgba(239, 68, 68, 0.15);
  }
  .unsubmitted-tag .stu-info-wrap {
    display: flex;
    align-items: center;
    gap: 10px;
    white-space: nowrap;
  }
  .unsubmitted-tag .stu-id {
    font-family: monospace;
    font-size: 1.15rem;
    font-weight: 800;
    color: var(--text-secondary);
    background: var(--tag-bg);
    padding: 2px 8px;
    border-radius: 6px;
    letter-spacing: 0.5px;
  }
  .unsubmitted-tag .stu-name {
    font-weight: 800;
    font-size: 1.35rem;
    color: #b91c1c;
    white-space: nowrap;
    letter-spacing: 0.5px;
  }
  .stu-status-badge {
    font-size: 0.85rem;
    font-weight: 700;
    color: #ef4444;
    background: rgba(239, 68, 68, 0.12);
    padding: 3px 8px;
    border-radius: 6px;
    white-space: nowrap;
  }
  .unmatched-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
    overflow-y: auto;
    max-height: calc(100vh - 270px);
    padding-right: 6px;
  }
  .unmatched-item {
    background: var(--bg-card);
    border: 1.5px solid var(--card-border);
    border-left: 6px solid #f59e0b;
    border-radius: 10px;
    padding: 14px 18px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    box-shadow: var(--card-shadow);
    transition: all 0.15s ease;
  }
  .unmatched-item:hover {
    box-shadow: 0 6px 16px rgba(245, 158, 11, 0.18);
    transform: translateY(-2px);
  }
  .unmatched-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .unmatched-file {
    font-family: monospace;
    font-weight: 800;
    font-size: 1.25rem;
    color: var(--accent);
  }
  .unmatched-badge {
    background: #fef3c7;
    color: #b45309;
    border: 1px solid #fcd34d;
    font-size: 0.9rem;
    font-weight: 800;
    padding: 3px 10px;
    border-radius: 12px;
  }
  .unmatched-body {
    font-size: 1.15rem;
    color: var(--text-primary);
    display: flex;
    align-items: center;
    gap: 20px;
  }
  .unmatched-body code {
    background: var(--tag-bg);
    padding: 3px 10px;
    border-radius: 6px;
    font-size: 1.25rem;
    color: #b45309;
    font-weight: 800;
  }
  .unmatched-note {
    font-size: 0.98rem;
    color: var(--text-secondary);
    line-height: 1.4;
  }

  /* Slide 3: 快速对答案 (透打四色分级 · 填空题超大字体) */
  .quick-answer-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
    padding: 10px 18px;
    background-color: var(--bg-secondary);
    border-radius: 10px;
    border: 1px solid var(--card-border);
    box-shadow: var(--card-shadow);
    flex-shrink: 0;
  }
  .quick-answer-legend {
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 0.88rem;
    font-weight: 700;
  }
  .legend-item {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 3px 10px;
    border-radius: 6px;
    border: 1px solid transparent;
  }
  .legend-item.tier-danger { color: #b91c1c; background: #fee2e2; border-color: #fca5a5; }
  .legend-item.tier-warn { color: #b45309; background: #fef3c7; border-color: #fcd34d; }
  .legend-item.tier-info { color: #1d4ed8; background: #dbeafe; border-color: #bfdbfe; }
  .legend-item.tier-success { color: #15803d; background: #dcfce7; border-color: #86efac; }

  .quick-answer-layout {
    display: grid;
    grid-template-columns: 1fr 1.05fr;
    gap: 16px;
    flex: 1;
    min-height: 0;
  }
  .quick-answer-card {
    overflow-y: auto;
  }
  .obj-answers-grid {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 12px;
    padding: 10px 4px;
  }
  .obj-ans-item {
    background: var(--bg-card);
    border: 2px solid var(--card-border);
    border-radius: 12px;
    padding: 12px 6px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 8px;
    box-shadow: var(--card-shadow);
    transition: all 0.2s ease;
    cursor: pointer;
  }
  .obj-ans-item:hover {
    transform: translateY(-3px) scale(1.04);
  }
  .obj-ans-item.tier-danger { border-color: #f87171; background: linear-gradient(180deg, rgba(239, 68, 68, 0.08) 0%, var(--bg-card) 100%); }
  .obj-ans-item.tier-warn { border-color: #fbbf24; background: linear-gradient(180deg, rgba(245, 158, 11, 0.08) 0%, var(--bg-card) 100%); }
  .obj-ans-item.tier-info { border-color: #60a5fa; background: linear-gradient(180deg, rgba(59, 130, 246, 0.08) 0%, var(--bg-card) 100%); }
  .obj-ans-item.tier-success { border-color: #4ade80; background: linear-gradient(180deg, rgba(34, 197, 94, 0.08) 0%, var(--bg-card) 100%); }

  .obj-ans-qno {
    font-size: 1.02rem;
    font-weight: 800;
    color: var(--text-primary);
  }
  .obj-ans-badge {
    width: 52px;
    height: 52px;
    border-radius: 50%;
    color: #ffffff;
    font-size: 1.95rem;
    font-weight: 900;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 4px 10px rgba(0, 0, 0, 0.18);
  }
  .obj-ans-badge.tier-danger { background: linear-gradient(135deg, #ef4444, #b91c1c); box-shadow: 0 4px 12px rgba(239, 68, 68, 0.4); }
  .obj-ans-badge.tier-warn { background: linear-gradient(135deg, #f59e0b, #d97706); box-shadow: 0 4px 12px rgba(245, 158, 11, 0.4); }
  .obj-ans-badge.tier-info { background: linear-gradient(135deg, #3b82f6, #1d4ed8); box-shadow: 0 4px 12px rgba(59, 130, 246, 0.4); }
  .obj-ans-badge.tier-success { background: linear-gradient(135deg, #22c55e, #15803d); box-shadow: 0 4px 12px rgba(34, 197, 94, 0.4); }

  .obj-ans-stat {
    font-size: 0.78rem;
    font-weight: 700;
    text-align: center;
    border-radius: 4px;
    padding: 2px 4px;
    line-height: 1.2;
  }
  .obj-ans-stat.tier-danger { color: #b91c1c; }
  .obj-ans-stat.tier-warn { color: #b45309; }
  .obj-ans-stat.tier-info { color: #1d4ed8; }
  .obj-ans-stat.tier-success { color: #15803d; }

  .subj-answers-list {
    display: flex;
    flex-direction: column;
    gap: 16px;
    padding: 6px 4px;
  }
  .subj-ans-block {
    background: var(--bg-card);
    border: 1.5px solid var(--card-border);
    border-radius: 12px;
    padding: 14px 18px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    box-shadow: var(--card-shadow);
  }
  .subj-ans-block.tier-danger { border-left: 8px solid #ef4444; }
  .subj-ans-block.tier-warn { border-left: 8px solid #f59e0b; }
  .subj-ans-block.tier-info { border-left: 8px solid #3b82f6; }
  .subj-ans-block.tier-success { border-left: 8px solid #22c55e; }

  .subj-ans-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px dashed var(--card-border);
    padding-bottom: 8px;
  }
  .subj-qtitle {
    font-size: 1.18rem;
    font-weight: 800;
    color: var(--text-primary);
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .btn-jump-tiny {
    background: var(--tag-bg);
    border: 1px solid var(--card-border);
    color: var(--accent);
    font-size: 0.85rem;
    font-weight: 700;
    padding: 3px 12px;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .btn-jump-tiny:hover {
    background: var(--accent);
    color: #ffffff;
    border-color: var(--accent);
  }
  .subj-blanks-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 12px;
  }
  .subj-blank-pill {
    background: var(--bg-secondary);
    border: 1.5px solid var(--card-border);
    border-radius: 8px;
    padding: 10px 14px;
    display: flex;
    align-items: center;
    gap: 12px;
    box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.04);
  }
  .blank-num {
    font-size: 1.2rem;
    font-weight: 800;
    color: var(--text-secondary);
    flex-shrink: 0;
  }
  .blank-val {
    font-size: 1.85rem;
    font-weight: 900;
    color: #0284c7;
    line-height: 1.2;
    word-break: break-word;
  }
  [data-theme="dark"] .blank-val {
    color: #38bdf8;
  }

  /* Question Header: Compact 38px */
  .question-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
    padding: 6px 14px;
    background-color: var(--bg-secondary);
    border-radius: 10px;
    border: 1px solid var(--card-border);
    box-shadow: var(--card-shadow);
    flex-shrink: 0;
  }
  .q-title-group {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .q-number {
    font-size: 1.45rem;
    font-weight: 800;
    color: var(--text-primary);
  }
  .badge {
    padding: 3px 10px;
    border-radius: 6px;
    font-size: 0.85rem;
    font-weight: 600;
  }
  .badge-type { background-color: rgba(2, 132, 199, 0.1); color: var(--accent); border: 1px solid rgba(2, 132, 199, 0.3); }
  .badge-answer { background-color: rgba(22, 163, 74, 0.1); color: var(--success); border: 1px solid rgba(22, 163, 74, 0.3); }
  .badge-knowledge { background-color: rgba(147, 51, 234, 0.1); color: #7e22ce; border: 1px solid rgba(147, 51, 234, 0.3); }
  .badge-score { background-color: var(--bg-card); color: var(--text-secondary); border: 1px solid var(--card-border); }
  .q-accuracy-badge {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .accuracy-circle {
    font-size: 1.25rem;
    font-weight: 800;
    padding: 3px 12px;
    border-radius: 6px;
  }
  .accuracy-circle.high { background-color: rgba(22, 163, 74, 0.12); color: var(--success); border: 1px solid rgba(22, 163, 74, 0.35); }
  .accuracy-circle.mid { background-color: rgba(2, 132, 199, 0.12); color: var(--accent); border: 1px solid rgba(2, 132, 199, 0.35); }
  .accuracy-circle.warn { background-color: rgba(217, 119, 6, 0.12); color: var(--warning); border: 1px solid rgba(217, 119, 6, 0.35); }
  .accuracy-circle.danger { background-color: rgba(220, 38, 38, 0.12); color: var(--danger); border: 1px solid rgba(220, 38, 38, 0.35); }

  /* Question Stem Card: HERO OF THE SCREEN (占用约 42% 空间，字号极大) */
  .question-stem-card {
    background: var(--stem-bg);
    border: 1.5px solid var(--card-border);
    border-left: 7px solid var(--accent);
    border-radius: 12px;
    padding: 16px 22px;
    margin-bottom: 10px;
    flex: 1 1 auto;
    min-height: 150px;
    max-height: 48vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: var(--card-shadow);
  }
  .stem-content-layout {
    display: flex;
    gap: 20px;
    align-items: flex-start;
    flex: 1;
    min-height: 0;
    overflow-y: auto;
  }
  .question-stem-text {
    flex: 1;
    font-size: calc(2.05rem * var(--font-scale, 1.35));
    line-height: 1.85;
    color: #000000;
    font-weight: 700;
    letter-spacing: 0.5px;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .stem-images-box {
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    align-items: center;
    flex-shrink: 0;
  }
  .q-thumb-wrap {
    position: relative;
    cursor: zoom-in;
    border-radius: 8px;
    overflow: hidden;
    border: 2px solid var(--card-border);
    background-color: #ffffff;
    box-shadow: 0 4px 16px rgba(0,0,0,0.1);
    transition: all 0.2s;
  }
  .q-thumb-wrap:hover {
    border-color: var(--accent);
    transform: scale(1.02);
  }
  .q-thumb-img {
    height: 190px;
    max-width: 360px;
    display: block;
    object-fit: contain;
    padding: 4px;
  }
  .q-thumb-badge {
    position: absolute;
    bottom: 4px;
    right: 4px;
    background: rgba(15, 23, 42, 0.8);
    color: #ffffff;
    font-size: 0.75rem;
    padding: 2px 6px;
    border-radius: 4px;
    pointer-events: none;
  }

  /* Options Grid: Huge Cards Filling Lower Screen (占用约 48% 空间) */
  .options-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    flex: 1.1 1 auto;
    min-height: 180px;
    margin-bottom: 8px;
  }
  .option-card {
    background: var(--option-bg);
    border: 2px solid var(--card-border);
    border-radius: 12px;
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    transition: all 0.2s ease;
    box-shadow: var(--card-shadow);
  }
  .option-card.correct {
    border-color: var(--success);
    background: var(--option-correct-bg);
  }
  .option-card.distractor {
    border-color: var(--danger);
    background: var(--option-distractor-bg);
  }
  .option-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 6px;
  }
  .opt-key-badge {
    font-size: calc(2.6rem * var(--font-scale, 1.35));
    font-weight: 900;
    width: 58px;
    height: 58px;
    display: flex;
    align-items: center;
    justify-content: center;
    background-color: var(--bg-card);
    border-radius: 10px;
    border: 2px solid var(--card-border);
    color: var(--text-primary);
  }
  .option-card.correct .opt-key-badge {
    background-color: rgba(22, 163, 74, 0.18);
    border-color: var(--success);
    color: var(--success);
  }
  .option-card.distractor .opt-key-badge {
    background-color: rgba(220, 38, 38, 0.18);
    border-color: var(--danger);
    color: var(--danger);
  }
  .opt-status-tag {
    font-size: 0.82rem;
    font-weight: 700;
    padding: 3px 8px;
    border-radius: 6px;
  }
  .opt-status-tag.correct { background-color: var(--success); color: #ffffff; }
  .opt-status-tag.distractor { background-color: var(--danger); color: #ffffff; }

  .option-text-wrap {
    flex: 1;
    display: flex;
    align-items: center;
    margin: 8px 0;
    overflow-y: auto;
  }
  .option-text {
    font-size: calc(1.72rem * var(--font-scale, 1.35));
    line-height: 1.6;
    color: #0f172a;
    font-weight: 600;
    word-break: break-word;
    letter-spacing: 0.3px;
  }

  .opt-stat-row {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 6px;
  }
  .opt-count-large {
    font-size: 1.55rem;
    font-weight: 800;
    color: var(--text-primary);
  }
  .option-card.correct .opt-count-large { color: var(--success); }
  .option-card.distractor .opt-count-large { color: var(--danger); }
  .opt-pct-large {
    font-size: 1.25rem;
    font-weight: 700;
    color: var(--text-secondary);
  }
  .opt-progress-bar-bg {
    height: 8px;
    background-color: var(--card-border);
    border-radius: 9999px;
    overflow: hidden;
    margin-bottom: 8px;
  }
  .opt-progress-bar-fill {
    height: 100%;
    background-color: var(--accent);
    border-radius: 9999px;
    transition: width 0.3s ease;
  }
  .option-card.correct .opt-progress-bar-fill { background-color: var(--success); }
  .option-card.distractor .opt-progress-bar-fill { background-color: var(--danger); }

  /* View Roster Button */
  .btn-view-roster {
    background-color: var(--bg-card);
    border: 1.5px solid var(--card-border);
    border-radius: 8px;
    padding: 7px 12px;
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--accent);
    cursor: pointer;
    transition: all 0.2s;
    text-align: center;
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
  }
  .btn-view-roster:hover {
    background-color: var(--accent);
    color: #ffffff;
    border-color: var(--accent);
    box-shadow: 0 4px 12px rgba(2, 132, 199, 0.25);
  }
  .option-card.correct .btn-view-roster {
    color: var(--success);
    border-color: rgba(22, 163, 74, 0.3);
  }
  .option-card.correct .btn-view-roster:hover {
    background-color: var(--success);
    color: #ffffff;
    border-color: var(--success);
  }
  .option-card.distractor .btn-view-roster {
    color: var(--danger);
    border-color: rgba(220, 38, 38, 0.3);
  }
  .option-card.distractor .btn-view-roster:hover {
    background-color: var(--danger);
    color: #ffffff;
    border-color: var(--danger);
  }
  .btn-roster-empty {
    font-size: 0.95rem;
    color: var(--text-secondary);
    text-align: center;
    padding: 7px;
    background: transparent;
  }

  /* Subjective Blanks Container */
  .subj-blanks-container {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: 12px;
    flex: 1.1 1 auto;
    min-height: 180px;
    margin-bottom: 8px;
    overflow-y: auto;
  }
  .blank-card {
    background-color: var(--bg-secondary);
    border: 1.5px solid var(--card-border);
    border-radius: 12px;
    padding: 14px 18px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    box-shadow: var(--card-shadow);
  }
  .blank-card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 6px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--card-border);
  }
  .blank-card-title {
    font-size: 1.45rem;
    font-weight: 800;
    color: var(--accent);
  }
  .blank-answer-box {
    background-color: var(--bg-card);
    border: 1px solid var(--card-border);
    border-radius: 8px;
    padding: 10px 14px;
    margin-bottom: 8px;
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
  }
  .blank-ans-label {
    font-size: 1rem;
    color: var(--text-secondary);
    font-weight: 600;
  }
  .blank-ans-val {
    font-size: calc(1.80rem * var(--font-scale, 1.35));
    font-weight: 800;
    color: #15803d;
    word-break: break-word;
  }
  .blank-score-info {
    font-size: 0.9rem;
    color: var(--text-secondary);
    white-space: nowrap;
  }

  .wrong-tags-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 8px;
    align-items: center;
  }
  .wrong-title {
    color: var(--danger);
    font-weight: 700;
    font-size: 0.95rem;
  }
  .wrong-tag {
    background: rgba(220, 38, 38, 0.1);
    color: var(--danger);
    border: 1px solid rgba(220, 38, 38, 0.25);
    border-radius: 5px;
    padding: 3px 8px;
    font-size: 0.95rem;
    font-weight: 600;
  }

  .blank-roster-btns {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin-top: auto;
  }
  .btn-roster-sub {
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 0.95rem;
    font-weight: 700;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    transition: all 0.2s;
  }
  .btn-roster-sub.success {
    background: rgba(22, 163, 74, 0.12);
    border: 1px solid rgba(22, 163, 74, 0.35);
    color: var(--success);
  }
  .btn-roster-sub.success:hover {
    background: var(--success);
    color: #ffffff;
    border-color: var(--success);
  }
  .btn-roster-sub.danger {
    background: rgba(220, 38, 38, 0.12);
    border: 1px solid rgba(220, 38, 38, 0.35);
    color: var(--danger);
  }
  .btn-roster-sub.danger:hover {
    background: var(--danger);
    color: #ffffff;
    border-color: var(--danger);
  }
  .btn-roster-sub.warning {
    background: rgba(217, 119, 6, 0.12);
    border: 1px solid rgba(217, 119, 6, 0.35);
    color: var(--warning);
  }
  .btn-roster-sub.warning:hover {
    background: var(--warning);
    color: #ffffff;
    border-color: var(--warning);
  }

  /* Compact Suggestion Strip: Takes zero vertical space away from question */
  .suggestion-box {
    background-color: #eff6ff;
    border-left: 5px solid #0284c7;
    border-radius: 0 6px 6px 0;
    padding: 5px 14px;
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.9rem;
    color: #1e3a8a;
    font-weight: 600;
    flex-shrink: 0;
  }
  .suggestion-box strong {
    color: #0369a1;
  }

  /* Bottom Controls: Compact 42px */
  footer {
    height: 42px;
    background-color: var(--bg-secondary);
    border-top: 1px solid var(--card-border);
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 20px;
    flex-shrink: 0;
  }
  .slide-counter {
    font-size: 0.95rem;
    font-weight: 700;
    color: var(--text-primary);
  }
  .nav-controls {
    display: flex;
    gap: 10px;
  }
  .keyboard-tips {
    font-size: 0.8rem;
    color: var(--text-secondary);
  }
  kbd {
    background: var(--bg-card);
    border: 1px solid var(--card-border);
    color: var(--text-primary);
    padding: 1px 5px;
    border-radius: 4px;
    font-size: 0.75rem;
  }

  /* Modal Dialog for Student Names */
  .modal-overlay {
    position: fixed;
    top: 0;
    left: 0;
    width: 100vw;
    height: 100vh;
    background: rgba(15, 23, 42, 0.6);
    backdrop-filter: blur(4px);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 999;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.25s ease;
  }
  .modal-overlay.show {
    opacity: 1;
    pointer-events: auto;
  }
  .modal-dialog {
    background-color: var(--bg-secondary);
    border: 1.5px solid var(--card-border);
    border-radius: 14px;
    width: 90%;
    max-width: 620px;
    max-height: 82vh;
    display: flex;
    flex-direction: column;
    box-shadow: 0 20px 50px rgba(0, 0, 0, 0.2);
    transform: translateY(20px) scale(0.96);
    transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    overflow: hidden;
  }
  .modal-overlay.show .modal-dialog {
    transform: translateY(0) scale(1);
  }
  .modal-header {
    padding: 12px 18px;
    border-bottom: 1px solid var(--card-border);
    display: flex;
    align-items: center;
    justify-content: space-between;
    background-color: var(--bg-card);
  }
  .modal-title {
    font-size: 1.1rem;
    font-weight: 700;
    color: var(--text-primary);
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .modal-close-btn {
    background: transparent;
    border: none;
    font-size: 1.4rem;
    color: var(--text-secondary);
    cursor: pointer;
    line-height: 1;
    padding: 2px 6px;
    border-radius: 4px;
  }
  .modal-close-btn:hover {
    color: var(--text-primary);
    background-color: var(--tag-bg);
  }
  .modal-toolbar {
    padding: 8px 18px;
    background-color: var(--bg-secondary);
    border-bottom: 1px solid var(--card-border);
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .modal-search-input {
    flex: 1;
    background-color: var(--bg-card);
    color: var(--text-primary);
    border: 1.5px solid var(--card-border);
    border-radius: 6px;
    padding: 7px 12px;
    font-size: 0.92rem;
    outline: none;
  }
  .modal-search-input:focus {
    border-color: var(--accent);
  }
  .modal-body {
    padding: 16px 18px;
    overflow-y: auto;
    flex: 1;
  }
  .modal-tags-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .modal-name-tag {
    background-color: var(--bg-card);
    color: var(--text-primary);
    border: 1px solid var(--card-border);
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 1.05rem;
    font-weight: 600;
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .modal-name-tag.correct {
    background-color: rgba(22, 163, 74, 0.12);
    border-color: rgba(22, 163, 74, 0.35);
    color: var(--success);
  }
  .modal-name-tag.danger {
    background-color: rgba(220, 38, 38, 0.12);
    border-color: rgba(220, 38, 38, 0.35);
    color: var(--danger);
  }
  .modal-name-tag.warning {
    background-color: rgba(217, 119, 6, 0.12);
    border-color: rgba(217, 119, 6, 0.35);
    color: var(--warning);
  }
  .modal-footer {
    padding: 10px 18px;
    border-top: 1px solid var(--card-border);
    display: flex;
    justify-content: space-between;
    align-items: center;
    background-color: var(--bg-card);
  }

  /* ================= Lightbox Modal (Zoom, Pan & Full Annotation) ================= */
  .lightbox-overlay {
    position: fixed;
    top: 0;
    left: 0;
    width: 100vw;
    height: 100vh;
    background: rgba(15, 23, 42, 0.94);
    backdrop-filter: blur(8px);
    display: flex;
    flex-direction: column;
    z-index: 1200;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.2s ease;
    user-select: none;
  }
  .lightbox-overlay.show {
    opacity: 1;
    pointer-events: auto;
  }
  .lightbox-toolbar {
    flex: 0 0 auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 16px;
    background: rgba(30, 41, 59, 0.94);
    border-bottom: 1px solid rgba(255, 255, 255, 0.12);
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
    z-index: 20;
    color: #f8fafc;
    gap: 10px;
  }
  .lb-header-left {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 180px;
    max-width: 280px;
  }
  .lb-icon {
    font-size: 1.2rem;
  }
  .lb-title {
    font-size: 0.98rem;
    font-weight: 700;
    color: #f8fafc;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .lb-header-center {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-wrap: wrap;
    gap: 8px;
  }
  .lb-header-right {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .lb-btn-group {
    display: inline-flex;
    align-items: center;
    background: rgba(15, 23, 42, 0.8);
    border: 1px solid rgba(255, 255, 255, 0.18);
    border-radius: 8px;
    padding: 3px;
    gap: 3px;
  }
  .lb-divider {
    width: 1px;
    height: 22px;
    background: rgba(255, 255, 255, 0.22);
    margin: 0 2px;
  }
  .lb-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
    background: transparent;
    color: #e2e8f0;
    border: none;
    border-radius: 6px;
    padding: 5px 9px;
    font-size: 0.86rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s ease;
    line-height: 1.1;
  }
  .lb-btn:hover {
    background: rgba(255, 255, 255, 0.18);
    color: #ffffff;
  }
  .lb-btn.active {
    background: #0284c7;
    color: #ffffff;
    box-shadow: 0 2px 8px rgba(2, 132, 199, 0.45);
  }
  .lb-btn-scale {
    min-width: 52px;
    font-variant-numeric: tabular-nums;
    font-size: 0.82rem;
  }
  .lb-btn-close {
    background: #ef4444;
    color: #ffffff;
    padding: 6px 14px;
  }
  .lb-btn-close:hover {
    background: #dc2626;
  }
  .lb-colors {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 0 4px;
  }
  .lb-color-dot {
    width: 20px;
    height: 20px;
    border-radius: 50%;
    border: 2px solid rgba(255, 255, 255, 0.4);
    cursor: pointer;
    transition: transform 0.15s ease, border-color 0.15s ease;
    padding: 0;
  }
  .lb-color-dot:hover {
    transform: scale(1.18);
  }
  .lb-color-dot.active {
    border-color: #ffffff;
    transform: scale(1.28);
    box-shadow: 0 0 8px rgba(255, 255, 255, 0.85);
  }
  .lb-sizes {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 0 2px;
  }
  .lb-size-btn {
    width: 24px;
    height: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    cursor: pointer;
    padding: 0;
  }
  .lb-size-btn:hover {
    background: rgba(255, 255, 255, 0.15);
  }
  .lb-size-btn.active {
    background: rgba(255, 255, 255, 0.25);
    border-color: rgba(255, 255, 255, 0.6);
  }
  .lb-size-indicator {
    background: #ffffff;
    border-radius: 50%;
  }

  .lightbox-viewport {
    flex: 1 1 auto;
    position: relative;
    width: 100vw;
    height: calc(100vh - 56px);
    overflow: hidden;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: grab;
  }
  .lightbox-viewport.panning {
    cursor: grabbing;
  }
  .lightbox-viewport.drawing {
    cursor: crosshair;
  }
  .lightbox-viewport.erasing {
    cursor: cell;
  }
  .lightbox-stage {
    position: relative;
    transform-origin: center center;
    user-select: none;
    background: #ffffff;
    box-shadow: 0 16px 50px rgba(0, 0, 0, 0.7);
    border-radius: 8px;
    display: block;
  }
  .lightbox-img {
    display: block;
    pointer-events: none;
    border-radius: 8px;
    max-width: none;
    max-height: none;
  }
  #lightboxCanvas {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    border-radius: 8px;
    touch-action: none;
  }
  .lb-hint {
    position: absolute;
    bottom: 12px;
    left: 50%;
    transform: translateX(-50%);
    background: rgba(15, 23, 42, 0.75);
    color: #cbd5e1;
    padding: 4px 14px;
    border-radius: 20px;
    font-size: 0.82rem;
    pointer-events: none;
    backdrop-filter: blur(4px);
    border: 1px solid rgba(255, 255, 255, 0.1);
  }

  /* ================= 白板批注与画笔系统 ================= */
  #whiteboardCanvas {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    z-index: 60;
    pointer-events: none;
    touch-action: none;
  }
  body.wb-active #whiteboardCanvas {
    pointer-events: auto;
  }
  body.wb-active.wb-pen #whiteboardCanvas {
    cursor: crosshair;
  }
  body.wb-active.wb-eraser #whiteboardCanvas {
    cursor: cell;
  }
  body.wb-pointer #whiteboardCanvas {
    pointer-events: none !important;
  }

  /* 顶部画笔按钮 */
  .btn-pen {
    background-color: var(--bg-card);
    border: 1px solid var(--card-border);
    color: var(--text-primary);
  }
  .btn-pen:hover {
    border-color: #ef4444;
    color: #ef4444;
  }
  .btn-pen.active {
    background-color: #ef4444 !important;
    border-color: #ef4444 !important;
    color: #ffffff !important;
    box-shadow: 0 0 14px rgba(239, 68, 68, 0.45);
  }

  /* 悬浮白板控制坞 (Glassmorphism Floating Dock) */
  .whiteboard-dock {
    position: fixed;
    bottom: 50px;
    left: 50%;
    transform: translateX(-50%);
    z-index: 120;
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--wb-dock-bg);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 1.5px solid var(--wb-dock-border);
    box-shadow: var(--wb-dock-shadow);
    border-radius: 9999px;
    padding: 6px 14px;
    user-select: none;
    animation: wbSlideUp 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  }

  @keyframes wbSlideUp {
    from {
      opacity: 0;
      transform: translate(-50%, 20px) scale(0.95);
    }
    to {
      opacity: 1;
      transform: translate(-50%, 0) scale(1);
    }
  }

  .wb-drag-badge {
    font-size: 0.92rem;
    font-weight: 800;
    color: var(--accent);
    padding: 4px 10px;
    border-radius: 9999px;
    background-color: rgba(2, 132, 199, 0.12);
    white-space: nowrap;
  }

  .wb-divider {
    width: 1px;
    height: 24px;
    background-color: var(--card-border);
    margin: 0 2px;
  }

  .wb-group {
    display: flex;
    align-items: center;
    gap: 5px;
  }

  .wb-btn {
    display: flex;
    align-items: center;
    gap: 5px;
    background: var(--wb-btn-bg);
    border: 1px solid var(--wb-btn-border);
    border-radius: 9999px;
    padding: 6px 13px;
    font-size: 0.88rem;
    font-weight: 700;
    color: var(--wb-btn-text);
    cursor: pointer;
    transition: all 0.15s ease;
    white-space: nowrap;
  }

  .wb-btn:hover {
    background-color: var(--tag-bg);
    border-color: var(--accent);
    transform: translateY(-1px);
  }

  .wb-btn#wbToolPen.active {
    background: var(--current-wb-color, #ef4444);
    border-color: var(--current-wb-color, #ef4444);
    color: #ffffff;
    box-shadow: 0 2px 12px rgba(239, 68, 68, 0.5);
  }

  .wb-btn#wbToolEraser.active {
    background: var(--warning);
    border-color: var(--warning);
    color: #ffffff;
    box-shadow: 0 2px 12px rgba(217, 119, 6, 0.5);
  }

  .wb-btn-mouse.active {
    background: var(--success) !important;
    border-color: var(--success) !important;
    color: #ffffff !important;
    box-shadow: 0 2px 12px rgba(22, 163, 74, 0.5);
  }

  .wb-btn-close {
    background: rgba(220, 38, 38, 0.12);
    border-color: rgba(220, 38, 38, 0.25);
    color: var(--danger);
  }
  .wb-btn-close:hover {
    background: var(--danger);
    color: #ffffff;
    border-color: var(--danger);
  }

  /* 粗细按钮 */
  .wb-widths {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .wb-width-btn {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: var(--wb-btn-bg);
    border: 1px solid var(--wb-btn-border);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: all 0.15s;
  }
  .wb-width-btn:hover {
    background-color: var(--tag-bg);
    transform: scale(1.1);
  }
  .wb-width-btn.active {
    border-color: var(--accent);
    background: rgba(2, 132, 199, 0.18);
    box-shadow: 0 0 8px rgba(2, 132, 199, 0.4);
  }
  .width-dot {
    border-radius: 50%;
    background-color: var(--current-wb-color, #ef4444);
    pointer-events: none;
  }

  /* 颜色按钮 */
  .wb-colors {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .wb-color-btn {
    width: 26px;
    height: 26px;
    border-radius: 50%;
    border: 2px solid transparent;
    cursor: pointer;
    transition: all 0.15s;
  }
  .wb-color-btn:hover {
    transform: scale(1.2);
  }
  .wb-color-btn.active {
    border-color: #0f172a;
    box-shadow: 0 0 10px rgba(0, 0, 0, 0.35), 0 0 0 2px #ffffff;
    transform: scale(1.18);
  }

  .wb-color-picker-label {
    position: relative;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: conic-gradient(red, yellow, lime, aqua, blue, magenta, red);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    box-shadow: 0 2px 6px rgba(0,0,0,0.25);
    transition: all 0.15s;
  }
  .wb-color-picker-label:hover {
    transform: scale(1.2);
  }
  .wb-color-picker-label input[type="color"] {
    position: absolute;
    opacity: 0;
    width: 0;
    height: 0;
    pointer-events: none;
  }
  .wb-color-picker-label .picker-icon {
    font-size: 0.75rem;
    filter: drop-shadow(0 1px 2px rgba(0,0,0,0.8));
    pointer-events: none;
  }
</style>
</head>
<body>

<header>
  <div class="brand">
    <h1>📊 __PAGE_TITLE__ · 交互讲评课件</h1>
    <select id="jumpSelect" class="jump-select" onchange="jumpToSlide(parseInt(this.value))">
      <option value="0">第 1 页: 班级总览与超均分表扬榜 (百分制)</option>
    </select>
  </div>
  <div class="header-actions">
    <div class="font-scale-group" title="调节投影字号">
      <button class="font-btn" onclick="setFontScale(1.15)">A 标准 (1.15)</button>
      <button class="font-btn active" id="btnFontLarge" onclick="setFontScale(1.35)">A+ 巨幕 (默认 1.35)</button>
      <button class="font-btn" onclick="setFontScale(1.55)">A++ 超巨幕 (1.55)</button>
    </div>
    <button class="btn btn-pen" id="togglePenBtn" onclick="toggleWhiteboardMode()" title="开启/关闭板书批注 (B)">🖊️ 板书批注</button>
    <button class="btn" id="themeBtn" onclick="toggleTheme()" title="切换明亮/深色主题">🌙 深色</button>
    <button class="btn btn-primary" onclick="toggleFullScreen()" title="全屏演示 (F11)">⛶ 全屏</button>
  </div>
</header>

<main id="slideContainer">
  <!-- Slides rendered dynamically by JavaScript -->
  <canvas id="whiteboardCanvas" class="whiteboard-canvas"></canvas>
</main>

<footer>
  <div class="keyboard-tips">
    提示：支持键盘 <kbd>←</kbd> <kbd>→</kbd> 翻页，<kbd>B</kbd> 开启板书画笔，<kbd>Ctrl+Z</kbd> 撤销，名单点击查看，配图点击放大
  </div>
  <div class="slide-counter" id="slideCounter">1 / 1</div>
  <div class="nav-controls">
    <button class="btn" id="prevBtn" onclick="prevSlide()">← 上一题 (P)</button>
    <button class="btn btn-primary" id="nextBtn" onclick="nextSlide()">下一题 (N) →</button>
  </div>
</footer>
<!-- Floating Whiteboard Dock -->
<div class="whiteboard-dock" id="whiteboardDock" style="display: none;">
  <div class="wb-drag-badge" title="板书批注工具箱">🖊️ 板书工具</div>
  
  <div class="wb-group">
    <button class="wb-btn active" id="wbToolPen" onclick="setWbTool('pen')" title="画笔 (自由书写)">
      <span class="wb-icon">✏️</span>
      <span class="wb-txt">画笔</span>
    </button>
    <button class="wb-btn" id="wbToolEraser" onclick="setWbTool('eraser')" title="橡皮擦 (擦除笔迹)">
      <span class="wb-icon">🧽</span>
      <span class="wb-txt">橡皮</span>
    </button>
  </div>

  <div class="wb-divider"></div>

  <!-- 笔迹粗细 -->
  <div class="wb-group wb-widths" title="调节笔迹粗细">
    <button class="wb-width-btn" data-width="3" onclick="setWbWidth(3)" title="细 (3px)">
      <span class="width-dot" style="width: 4px; height: 4px;"></span>
    </button>
    <button class="wb-width-btn active" data-width="6" onclick="setWbWidth(6)" title="中 (6px，默认)">
      <span class="width-dot" style="width: 8px; height: 8px;"></span>
    </button>
    <button class="wb-width-btn" data-width="12" onclick="setWbWidth(12)" title="粗 (12px)">
      <span class="width-dot" style="width: 14px; height: 14px;"></span>
    </button>
    <button class="wb-width-btn" data-width="20" onclick="setWbWidth(20)" title="特粗 (20px)">
      <span class="width-dot" style="width: 20px; height: 20px;"></span>
    </button>
  </div>

  <div class="wb-divider"></div>

  <!-- 调色板 -->
  <div class="wb-group wb-colors" title="选择画笔颜色">
    <button class="wb-color-btn active" data-color="#ef4444" style="background-color: #ef4444;" onclick="setWbColor('#ef4444')" title="警示红"></button>
    <button class="wb-color-btn" data-color="#facc15" style="background-color: #facc15;" onclick="setWbColor('#facc15')" title="重点黄"></button>
    <button class="wb-color-btn" data-color="#22c55e" style="background-color: #22c55e;" onclick="setWbColor('#22c55e')" title="答案绿"></button>
    <button class="wb-color-btn" data-color="#38bdf8" style="background-color: #38bdf8;" onclick="setWbColor('#38bdf8')" title="推导蓝"></button>
    <button class="wb-color-btn" data-color="#ffffff" style="background-color: #ffffff; border: 1px solid #94a3b8;" onclick="setWbColor('#ffffff')" title="粉笔白"></button>
    <label class="wb-color-picker-label" title="自定义任意颜色">
      <span class="picker-icon">🎨</span>
      <input type="color" id="wbCustomColor" value="#ef4444" onchange="setWbColor(this.value)">
    </label>
  </div>

  <div class="wb-divider"></div>

  <!-- 操作按钮 -->
  <div class="wb-group">
    <button class="wb-btn" onclick="undoWbStroke()" title="撤销上一笔 (Ctrl+Z)">
      <span class="wb-icon">↩</span>
      <span class="wb-txt">撤销</span>
    </button>
    <button class="wb-btn" onclick="clearCurrentSlideWb()" title="清空本页全部板书">
      <span class="wb-icon">🗑️</span>
      <span class="wb-txt">清屏</span>
    </button>
    <button class="wb-btn wb-btn-mouse" id="wbPointerBtn" onclick="toggleWbPointerMode()" title="切换鼠标模式 (临时点击底层名单/看图)">
      <span class="wb-icon">👆</span>
      <span class="wb-txt">鼠标</span>
    </button>
    <button class="wb-btn wb-btn-close" onclick="closeWhiteboardMode()" title="收起板书工具 (Esc)">
      <span class="wb-icon">✕</span>
      <span class="wb-txt">收起</span>
    </button>
  </div>
</div>


<!-- Global Student Roster Modal -->
<div class="modal-overlay" id="rosterModal" onclick="closeRosterModal(event)">
  <div class="modal-dialog" onclick="event.stopPropagation()">
    <div class="modal-header">
      <div class="modal-title" id="rosterModalTitle">👥 学生名单</div>
      <button class="modal-close-btn" onclick="closeRosterModal()">✕</button>
    </div>
    <div class="modal-toolbar">
      <input type="text" id="rosterSearchInput" class="modal-search-input" placeholder="🔍 快速搜索学生姓名..." oninput="filterModalNames(this.value)">
    </div>
    <div class="modal-body">
      <div class="modal-tags-grid" id="rosterTagsGrid"></div>
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="copyRosterNames()">📋 复制全员名单</button>
      <button class="btn btn-primary" onclick="closeRosterModal()">完成并关闭</button>
    </div>
  </div>
</div>

<!-- Global Image Lightbox Modal (Zoom, Pan & Annotation) -->
<div class="lightbox-overlay" id="imageLightbox">
  <div class="lightbox-toolbar" onclick="event.stopPropagation()">
    <div class="lb-header-left">
      <span class="lb-icon">🖼️</span>
      <span class="lb-title" id="lightboxCaption">试卷题目插图</span>
    </div>
    
    <div class="lb-header-center">
      <!-- 缩放控制组 -->
      <div class="lb-btn-group" title="缩放控制 (支持鼠标滚轮连续缩放)">
        <button class="lb-btn" onclick="zoomLb(-0.25)" title="缩小图片 (快捷键: 滚轮向下)">➖ 缩小</button>
        <button class="lb-btn lb-btn-scale" onclick="resetLbZoom()" id="lbZoomLevel" title="点击恢复 100% 原始比例">100%</button>
        <button class="lb-btn" onclick="zoomLb(0.25)" title="放大图片 (快捷键: 滚轮向上)">➕ 放大</button>
        <button class="lb-btn" onclick="fitLbImage()" title="适应窗口最佳显示">⛶ 适应窗口</button>
      </div>

      <div class="lb-divider"></div>

      <!-- 模式选择组 -->
      <div class="lb-btn-group">
        <button class="lb-btn active" id="lbModePan" onclick="setLbMode('pan')" title="漫游移动模式 (按住鼠标拖拽平移)">✋ 漫游移动</button>
        <button class="lb-btn" id="lbModePen" onclick="setLbMode('pen')" title="批注画笔 (直接在配图上书写划线)">✏️ 画笔批注</button>
        <button class="lb-btn" id="lbModeEraser" onclick="setLbMode('eraser')" title="橡皮擦除">🧹 橡皮擦</button>
      </div>

      <!-- 画笔选项组 -->
      <div class="lb-btn-group lb-pen-controls" id="lbPenControls">
        <!-- 颜色选择 -->
        <div class="lb-colors" title="画笔颜色选择">
          <button class="lb-color-dot active" style="background:#ef4444;" onclick="setLbColor('#ef4444')" title="红色"></button>
          <button class="lb-color-dot" style="background:#3b82f6;" onclick="setLbColor('#3b82f6')" title="蓝色"></button>
          <button class="lb-color-dot" style="background:#eab308;" onclick="setLbColor('#eab308')" title="黄色"></button>
          <button class="lb-color-dot" style="background:#10b981;" onclick="setLbColor('#10b981')" title="绿色"></button>
        </div>
        <!-- 粗细选择 -->
        <div class="lb-sizes" title="画笔粗细">
          <button class="lb-size-btn active" id="lbSizeThin" onclick="setLbWidth(3)" title="细线条">
            <span class="lb-size-indicator" style="width:4px;height:4px;"></span>
          </button>
          <button class="lb-size-btn" id="lbSizeMed" onclick="setLbWidth(7)" title="中等线条">
            <span class="lb-size-indicator" style="width:7px;height:7px;"></span>
          </button>
          <button class="lb-size-btn" id="lbSizeThick" onclick="setLbWidth(14)" title="粗线条">
            <span class="lb-size-indicator" style="width:12px;height:12px;"></span>
          </button>
        </div>
        <!-- 撤销 & 清屏 -->
        <button class="lb-btn" onclick="undoLbStroke()" title="撤销上一步批注 (Ctrl+Z)">↩️ 撤销</button>
        <button class="lb-btn" onclick="clearLbStrokes()" title="清空全部批注">🗑️ 清屏</button>
      </div>
    </div>

    <div class="lb-header-right">
      <button class="lb-btn lb-btn-close" onclick="closeLightbox()" title="退出全屏预览 (ESC)">✕ 关闭 (ESC)</button>
    </div>
  </div>

  <!-- Viewport Container -->
  <div class="lightbox-viewport" id="lightboxViewport">
    <div class="lightbox-stage" id="lightboxStage">
      <img class="lightbox-img" id="lightboxImg" src="" alt="题目插图" draggable="false">
      <canvas id="lightboxCanvas"></canvas>
    </div>
    <div class="lb-hint">💡 滚轮可连续缩放 · [漫游]按住拖拽移动 · [画笔]直接在图上标注 · 按 ESC 关闭</div>
  </div>
</div>

<script>
  const presentationData = __PRESENTATION_JSON__;
  let currentSlideIndex = 0;
  let totalSlides = 1;
  let currentModalStudents = [];
  let currentModalTagType = 'default';

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function setFontScale(scale) {
    document.documentElement.style.setProperty('--font-scale', scale);
    document.querySelectorAll('.font-btn').forEach(btn => {
      btn.classList.toggle('active', btn.getAttribute('onclick').includes(scale.toFixed(2)) || btn.getAttribute('onclick').includes(scale.toString()));
    });
  }

  // ================= Lightbox Zoom, Pan & Annotation System =================
  let lbScale = 1.0;
  let lbPanX = 0;
  let lbPanY = 0;
  let lbMode = 'pan'; // 'pan' | 'pen' | 'eraser'
  let lbColor = '#ef4444';
  let lbWidth = 4;
  let lbStrokes = [];
  let lbIsDrawing = false;
  let lbCurrentStroke = null;
  let lbIsPanning = false;
  let lbPanStartX = 0;
  let lbPanStartY = 0;
  let lbImgNaturalW = 800;
  let lbImgNaturalH = 600;
  let lbCanvas = null;
  let lbCtx = null;
  let lbEventsInitialized = false;

  function updateLbTransform() {
    const stage = document.getElementById('lightboxStage');
    const zoomIndicator = document.getElementById('lbZoomLevel');
    if (stage) {
      stage.style.transform = `translate(${lbPanX}px, ${lbPanY}px) scale(${lbScale})`;
    }
    if (zoomIndicator) {
      zoomIndicator.textContent = Math.round(lbScale * 100) + '%';
    }
  }

  function fitLbImage() {
    const vp = document.getElementById('lightboxViewport');
    if (!vp || !lbImgNaturalW || !lbImgNaturalH) return;
    const vpRect = vp.getBoundingClientRect();
    const availW = vpRect.width * 0.92;
    const availH = vpRect.height * 0.88;
    const fitScale = Math.min(availW / lbImgNaturalW, availH / lbImgNaturalH, 1.5);
    lbScale = Math.max(0.15, Number(fitScale.toFixed(2)));
    lbPanX = 0;
    lbPanY = 0;
    updateLbTransform();
  }

  function resetLbZoom() {
    lbScale = 1.0;
    lbPanX = 0;
    lbPanY = 0;
    updateLbTransform();
  }

  function zoomLb(delta) {
    let newScale = lbScale + delta;
    newScale = Math.max(0.2, Math.min(5.0, Number(newScale.toFixed(2))));
    lbScale = newScale;
    updateLbTransform();
  }

  function setLbMode(mode) {
    lbMode = mode;
    updateLbModeUI();
  }

  function updateLbModeUI() {
    const vp = document.getElementById('lightboxViewport');
    const btnPan = document.getElementById('lbModePan');
    const btnPen = document.getElementById('lbModePen');
    const btnEraser = document.getElementById('lbModeEraser');
    const canvas = document.getElementById('lightboxCanvas');
    
    if (btnPan) btnPan.classList.toggle('active', lbMode === 'pan');
    if (btnPen) btnPen.classList.toggle('active', lbMode === 'pen');
    if (btnEraser) btnEraser.classList.toggle('active', lbMode === 'eraser');
    
    if (vp) {
      vp.classList.remove('panning', 'drawing', 'erasing');
      if (lbMode === 'pen') vp.classList.add('drawing');
      else if (lbMode === 'eraser') vp.classList.add('erasing');
    }

    if (canvas) {
      canvas.style.pointerEvents = (lbMode === 'pan') ? 'none' : 'auto';
    }
  }

  function setLbColor(color) {
    lbColor = color;
    document.querySelectorAll('.lb-color-dot').forEach(dot => {
      dot.classList.toggle('active', dot.getAttribute('onclick').includes(color));
    });
    if (lbMode !== 'pen') {
      setLbMode('pen');
    }
  }

  function setLbWidth(width) {
    lbWidth = width;
    const btnThin = document.getElementById('lbSizeThin');
    const btnMed = document.getElementById('lbSizeMed');
    const btnThick = document.getElementById('lbSizeThick');
    if (btnThin) btnThin.classList.toggle('active', width <= 4);
    if (btnMed) btnMed.classList.toggle('active', width > 4 && width <= 9);
    if (btnThick) btnThick.classList.toggle('active', width > 9);
  }

  function getLbCanvasCoords(e) {
    const canvas = document.getElementById('lightboxCanvas');
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    return {
      x: (e.clientX - rect.left) * scaleX,
      y: (e.clientY - rect.top) * scaleY
    };
  }

  function handleLbPointerDown(e) {
    if (lbMode === 'pan') return;
    if (e.button !== undefined && e.button !== 0) return;
    
    lbIsDrawing = true;
    const canvas = document.getElementById('lightboxCanvas');
    try { canvas.setPointerCapture(e.pointerId); } catch (_) {}
    
    const pt = getLbCanvasCoords(e);
    lbCurrentStroke = {
      tool: lbMode,
      color: lbColor,
      width: lbWidth,
      points: [pt]
    };
    drawLbLivePoint(pt, lbMode, lbColor, lbWidth);
  }

  function handleLbPointerMove(e) {
    if (!lbIsDrawing || !lbCurrentStroke) return;
    const pt = getLbCanvasCoords(e);
    lbCurrentStroke.points.push(pt);
    drawLbLiveSegment(lbCurrentStroke);
  }

  function handleLbPointerUp(e) {
    if (!lbIsDrawing) return;
    lbIsDrawing = false;
    if (lbCurrentStroke && lbCurrentStroke.points.length > 0) {
      lbStrokes.push(lbCurrentStroke);
    }
    lbCurrentStroke = null;
    redrawLbCanvas();
  }

  function drawLbLivePoint(pt, tool, color, width) {
    if (!lbCtx) return;
    lbCtx.save();
    lbCtx.lineCap = 'round';
    lbCtx.lineJoin = 'round';
    if (tool === 'pen') {
      lbCtx.globalCompositeOperation = 'source-over';
      lbCtx.fillStyle = color;
      const r = Math.max(1, width / 2);
      lbCtx.beginPath();
      lbCtx.arc(pt.x, pt.y, r, 0, Math.PI * 2);
      lbCtx.fill();
    } else {
      lbCtx.globalCompositeOperation = 'destination-out';
      lbCtx.fillStyle = 'rgba(0,0,0,1)';
      const r = Math.max(2, (width * 3.5) / 2);
      lbCtx.beginPath();
      lbCtx.arc(pt.x, pt.y, r, 0, Math.PI * 2);
      lbCtx.fill();
    }
    lbCtx.restore();
  }

  function drawLbLiveSegment(stroke) {
    if (!lbCtx || !stroke || stroke.points.length < 2) return;
    const pts = stroke.points;
    const p1 = pts[pts.length - 2];
    const p2 = pts[pts.length - 1];

    lbCtx.save();
    lbCtx.lineCap = 'round';
    lbCtx.lineJoin = 'round';
    if (stroke.tool === 'pen') {
      lbCtx.globalCompositeOperation = 'source-over';
      lbCtx.strokeStyle = stroke.color;
      lbCtx.lineWidth = stroke.width;
    } else {
      lbCtx.globalCompositeOperation = 'destination-out';
      lbCtx.strokeStyle = 'rgba(0,0,0,1)';
      lbCtx.lineWidth = stroke.width * 3.5;
    }
    lbCtx.beginPath();
    lbCtx.moveTo(p1.x, p1.y);
    lbCtx.lineTo(p2.x, p2.y);
    lbCtx.stroke();
    lbCtx.restore();
  }

  function redrawLbCanvas() {
    const canvas = document.getElementById('lightboxCanvas');
    if (!canvas) return;
    if (!lbCtx) lbCtx = canvas.getContext('2d');
    
    lbCtx.clearRect(0, 0, canvas.width, canvas.height);
    
    lbStrokes.forEach(s => {
      lbCtx.save();
      lbCtx.lineCap = 'round';
      lbCtx.lineJoin = 'round';
      if (s.tool === 'pen') {
        lbCtx.globalCompositeOperation = 'source-over';
        lbCtx.strokeStyle = s.color;
        lbCtx.fillStyle = s.color;
        lbCtx.lineWidth = s.width;
      } else {
        lbCtx.globalCompositeOperation = 'destination-out';
        lbCtx.strokeStyle = 'rgba(0,0,0,1)';
        lbCtx.fillStyle = 'rgba(0,0,0,1)';
        lbCtx.lineWidth = s.width * 3.5;
      }
      
      if (s.points.length === 1) {
        lbCtx.beginPath();
        const r = (s.tool === 'pen' ? s.width : s.width * 3.5) / 2;
        lbCtx.arc(s.points[0].x, s.points[0].y, Math.max(1, r), 0, Math.PI * 2);
        lbCtx.fill();
      } else if (s.points.length > 1) {
        lbCtx.beginPath();
        lbCtx.moveTo(s.points[0].x, s.points[0].y);
        for (let i = 1; i < s.points.length; i++) {
          lbCtx.lineTo(s.points[i].x, s.points[i].y);
        }
        lbCtx.stroke();
      }
      lbCtx.restore();
    });
  }

  function undoLbStroke() {
    if (lbStrokes.length > 0) {
      lbStrokes.pop();
      redrawLbCanvas();
    }
  }

  function clearLbStrokes() {
    lbStrokes = [];
    redrawLbCanvas();
  }

  function openLightbox(src, caption) {
    const lb = document.getElementById('imageLightbox');
    const img = document.getElementById('lightboxImg');
    const cap = document.getElementById('lightboxCaption');
    const stage = document.getElementById('lightboxStage');
    const canvas = document.getElementById('lightboxCanvas');
    
    if (!lb || !img) return;

    cap.textContent = caption || '试卷题目插图';
    lbStrokes = [];
    lbMode = 'pan';
    updateLbModeUI();
    
    img.onload = () => {
      lbImgNaturalW = img.naturalWidth || 800;
      lbImgNaturalH = img.naturalHeight || 600;
      
      stage.style.width = lbImgNaturalW + 'px';
      stage.style.height = lbImgNaturalH + 'px';
      img.style.width = lbImgNaturalW + 'px';
      img.style.height = lbImgNaturalH + 'px';
      
      canvas.width = lbImgNaturalW;
      canvas.height = lbImgNaturalH;
      canvas.style.width = lbImgNaturalW + 'px';
      canvas.style.height = lbImgNaturalH + 'px';
      
      fitLbImage();
      redrawLbCanvas();
    };

    img.src = src;
    lb.classList.add('show');
    initLightboxEvents();
  }

  function closeLightbox() {
    const lb = document.getElementById('imageLightbox');
    if (lb) lb.classList.remove('show');
    const img = document.getElementById('lightboxImg');
    if (img) img.src = '';
    lbStrokes = [];
    if (lbCtx && lbCanvas) {
      lbCtx.clearRect(0, 0, lbCanvas.width, lbCanvas.height);
    }
  }

  function openImageLightbox(qIndex, imgIndex) {
    const q = presentationData.questions[qIndex];
    if (!q || !q.images || !q.images[imgIndex]) return;
    const src = q.images[imgIndex];
    const caption = `${q.title} 试卷配图 ${q.images.length > 1 ? '(' + (imgIndex + 1) + ')' : ''}`;
    openLightbox(src, caption);
  }

  function initLightboxEvents() {
    if (lbEventsInitialized) return;
    lbEventsInitialized = true;

    const vp = document.getElementById('lightboxViewport');
    const canvas = document.getElementById('lightboxCanvas');
    
    if (canvas) {
      lbCanvas = canvas;
      lbCtx = canvas.getContext('2d');
      canvas.addEventListener('pointerdown', handleLbPointerDown);
      canvas.addEventListener('pointermove', handleLbPointerMove);
      canvas.addEventListener('pointerup', handleLbPointerUp);
      canvas.addEventListener('pointercancel', handleLbPointerUp);
    }

    if (vp) {
      vp.addEventListener('wheel', (e) => {
        e.preventDefault();
        const delta = e.deltaY < 0 ? 0.15 : -0.15;
        zoomLb(delta);
      }, { passive: false });

      vp.addEventListener('pointerdown', (e) => {
        if (lbMode !== 'pan') return;
        if (e.button !== undefined && e.button !== 0) return;
        lbIsPanning = true;
        lbPanStartX = e.clientX - lbPanX;
        lbPanStartY = e.clientY - lbPanY;
        vp.classList.add('panning');
        try { vp.setPointerCapture(e.pointerId); } catch (_) {}
      });

      vp.addEventListener('pointermove', (e) => {
        if (!lbIsPanning) return;
        lbPanX = e.clientX - lbPanStartX;
        lbPanY = e.clientY - lbPanStartY;
        updateLbTransform();
      });

      const stopPan = () => {
        if (lbIsPanning) {
          lbIsPanning = false;
          vp.classList.remove('panning');
        }
      };

      vp.addEventListener('pointerup', stopPan);
      vp.addEventListener('pointercancel', stopPan);
    }
  }

  // Student Roster Modal functions
  function showRosterModal(title, students, tagType = 'default') {
    currentModalStudents = students || [];
    currentModalTagType = tagType;
    const modal = document.getElementById('rosterModal');
    const titleElem = document.getElementById('rosterModalTitle');
    const searchInput = document.getElementById('rosterSearchInput');
    if (searchInput) searchInput.value = '';

    titleElem.innerHTML = `👥 ${escapeHtml(title)} <span class="badge-count">${currentModalStudents.length} 人</span>`;
    renderModalTags('');
    modal.classList.add('show');
    if (searchInput) setTimeout(() => searchInput.focus(), 50);
  }

  function filterModalNames(keyword) {
    renderModalTags(keyword);
  }

  function renderModalTags(keyword) {
    const grid = document.getElementById('rosterTagsGrid');
    grid.innerHTML = '';
    const kw = (keyword || '').trim().toLowerCase();

    const filtered = currentModalStudents.filter(st => {
      if (!kw) return true;
      const nameStr = (typeof st === 'object' && st !== null ? st.name : String(st)).toLowerCase();
      return nameStr.includes(kw);
    });

    if (filtered.length === 0) {
      grid.innerHTML = `<div style="color:var(--text-secondary);padding:24px;text-align:center;width:100%;font-size:1rem;">${kw ? '未搜索到匹配的学生' : '暂无学生名单'}</div>`;
      return;
    }

    filtered.forEach(st => {
      const tag = document.createElement('div');
      tag.className = `modal-name-tag ${currentModalTagType}`;
      if (typeof st === 'object' && st !== null) {
        tag.innerHTML = `<strong>${escapeHtml(st.name)}</strong> <span style="font-size:0.85rem;opacity:0.85;">(${st.score}分${st.ans ? ' · ' + escapeHtml(st.ans) : ''})</span>`;
      } else {
        tag.textContent = st;
      }
      grid.appendChild(tag);
    });
  }

  function closeRosterModal(e) {
    const modal = document.getElementById('rosterModal');
    modal.classList.remove('show');
  }

  function copyAllAnswers() {
    const questions = presentationData.questions || [];
    let lines = ['【客观题参考答案】'];
    let objList = [];
    questions.forEach(q => {
      if (q.category === 'objective') {
        objList.push(`${q.title}: ${q.expected}`);
      }
    });
    lines.push(objList.join('  |  '));
    lines.push('');
    lines.push('【主观题参考答案】');
    questions.forEach(q => {
      if (q.category !== 'objective') {
        let bLines = [];
        (q.blanks || []).forEach(b => {
          bLines.push(`${b.label}: ${b.expected}`);
        });
        lines.push(`${q.title}: ${bLines.join('； ') || q.expected}`);
      }
    });
    const ansText = lines.join(String.fromCharCode(10));
    navigator.clipboard.writeText(ansText).then(() => {
      alert('已成功复制全卷标准参考答案到剪贴板！' + String.fromCharCode(10, 10) + ansText);
    }).catch(() => {
      alert('复制失败，请手动选择复制。');
    });
  }

  function copyUnsubmittedList() {
    const subCheck = presentationData.submission_check;
    if (!subCheck || !subCheck.unsubmitted_students || subCheck.unsubmitted_students.length === 0) return;
    const names = subCheck.unsubmitted_students.map(st => `${st.id} ${st.name}`).join('、');
    navigator.clipboard.writeText(names).then(() => {
      alert('已成功复制 ' + subCheck.unsubmitted_students.length + ' 位未交学生名单到剪贴板！' + String.fromCharCode(10, 10) + names);
    }).catch(() => {
      alert('复制失败，请手动选择复制。');
    });
  }

  function copyRosterNames() {
    if (!currentModalStudents || currentModalStudents.length === 0) return;
    const names = currentModalStudents.map(st => typeof st === 'object' && st !== null ? `${st.name}(${st.score}分)` : String(st)).join('、');
    navigator.clipboard.writeText(names).then(() => {
      alert('已成功复制名单到剪贴板！');
    }).catch(() => {
      alert('复制失败，请手动选择复制。');
    });
  }

  // Index-based Roster Openers (Clean, Safe, Fast)
  function openOptionRoster(qIndex, cardIndex) {
    const q = presentationData.questions[qIndex];
    if (!q || !q.options_cards || !q.options_cards[cardIndex]) return;
    const card = q.options_cards[cardIndex];
    const tagType = card.is_correct ? 'correct' : (card.is_distractor ? 'danger' : 'default');
    showRosterModal(`${q.title} · 选项 ${card.key} 作答名单`, card.students, tagType);
  }

  function openBlankRoster(qIndex, blankIndex, type) {
    const q = presentationData.questions[qIndex];
    if (!q || !q.blanks || !q.blanks[blankIndex]) return;
    const b = q.blanks[blankIndex];
    if (type === 'full') {
      showRosterModal(`${q.title} · ${b.label} 满分学生名单`, b.full_score_students, 'correct');
    } else if (type === 'part') {
      showRosterModal(`${q.title} · ${b.label} 部分得分学生名单`, b.partial_score_students, 'warning');
    } else {
      showRosterModal(`${q.title} · ${b.label} 失分/零分学生名单`, b.zero_score_students, 'danger');
    }
  }

  function openCalcRoster(qIndex, type) {
    const q = presentationData.questions[qIndex];
    if (!q) return;
    if (type === 'full') {
      showRosterModal(`${q.title} 满分学生名单`, q.full_score_students, 'correct');
    } else if (type === 'part') {
      showRosterModal(`${q.title} 部分得分学生名单`, q.partial_score_students, 'warning');
    } else {
      showRosterModal(`${q.title} 零分/未作答名单`, q.zero_score_students, 'danger');
    }
  }

  function initPresentation() {
    const container = document.getElementById('slideContainer');
    const select = document.getElementById('jumpSelect');
    select.innerHTML = '';
    const meta = presentationData.meta || {};
    const questions = presentationData.questions || [];
    const subCheck = presentationData.submission_check || null;

    let slideIdx = 0;

    // Slide 1: 班级总览与超均分表扬榜 (纯百分制)
    const slide1 = document.createElement('div');
    slide1.className = 'slide active';
    slide1.id = `slide-${slideIdx}`;

    const honorList = presentationData.honor_roll || [];
    let honorHtml = '';
    honorList.forEach((s, idx) => {
      const topClass = idx === 0 ? 'top-1' : (idx === 1 ? 'top-2' : (idx === 2 ? 'top-3' : ''));
      const medal = idx === 0 ? '🥇 ' : (idx === 1 ? '🥈 ' : (idx === 2 ? '🥉 ' : ''));
      honorHtml += `
        <div class="honor-item ${topClass}">
          <div class="honor-rank">
            <span>${medal}第 ${s.rank} 名</span>
            <span style="font-size:0.85rem;color:var(--text-secondary);">${s.score_id}号</span>
          </div>
          <div class="honor-name">${escapeHtml(s.name)}</div>
          <div class="honor-score">${s.score_100 || s.total_score} <span style="font-size:0.95rem;font-weight:600;color:#334155;">分</span></div>
          <div class="honor-diff">+${s.diff_from_avg}分</div>
        </div>
      `;
    });

    const brackets = presentationData.brackets || [];
    let bracketHtml = '';
    brackets.forEach(b => {
      bracketHtml += `
        <div class="bracket-row">
          <div class="bracket-info">
            <span><strong>${b.name}</strong></span>
            <span><strong>${b.count} 人</strong> (${b.pct}%)</span>
          </div>
          <div class="bracket-bar-bg">
            <div class="bracket-bar-fill ${b.tag}" style="width: ${b.pct}%;"></div>
          </div>
        </div>
      `;
    });

    slide1.innerHTML = `
      <div class="metrics-grid">
        <div class="metric-card">
          <span class="label">参试总人数</span>
          <span class="value">${meta.total_students || 0} <small style="font-size:1.1rem;">人</small></span>
          <span class="sub-value">满分 100 分制 · 答卷全部批改完成</span>
        </div>
        <div class="metric-card">
          <span class="label">全班平均分 (百分制)</span>
          <span class="value">${meta.avg_total_score || 0} <small style="font-size:1.1rem;">分</small></span>
          <span class="sub-value">客观题均分 ${meta.avg_obj_score}分 · 主观题均分 ${meta.avg_subj_score}分</span>
        </div>
        <div class="metric-card">
          <span class="label">班级最高分 (状元)</span>
          <span class="value" style="color:#b45309;">${meta.max_score || 0} <small style="font-size:1.1rem;">分</small></span>
          <span class="sub-value">最低分 ${meta.min_score || 0}分 · 全班极差 ${((meta.max_score || 0) - (meta.min_score || 0)).toFixed(1)}分</span>
        </div>
        <div class="metric-card">
          <span class="label">及格率 (>=60分)</span>
          <span class="value" style="color:#15803d;">${meta.pass_rate || 0}%</span>
          <span class="sub-value">${meta.pass_count || 0} 人及格</span>
        </div>
        <div class="metric-card">
          <span class="label">良好率 (>=80分)</span>
          <span class="value" style="color:#1d4ed8;">${meta.excellent_rate || 0}%</span>
          <span class="sub-value">${meta.excellent_count || 0} 人良好/优秀</span>
        </div>
        <div class="metric-card">
          <span class="label">超越平均分人数</span>
          <span class="value" style="color:#7e22ce;">${meta.above_avg_rate || 0}%</span>
          <span class="sub-value">${meta.above_avg_count || 0} 人高于均分</span>
        </div>
      </div>

      <div class="overview-content">
        <div class="section-card">
          <div class="section-header">
            <h2>🏆 超越全班平均分光荣榜 <span class="badge-count">${honorList.length} 人</span></h2>
            <span style="font-size:0.85rem;color:var(--text-secondary);">按百分制成绩降序排列 · 课堂点名表扬</span>
          </div>
          <div class="honor-scroll">
            ${honorHtml}
          </div>
        </div>

        <div class="section-card">
          <div class="section-header">
            <h2>📊 全班分数段结构分布 (百分制)</h2>
            <span style="font-size:0.85rem;color:var(--text-secondary);">统一按 100 分满分制划分</span>
          </div>
          <div class="bracket-list">
            ${bracketHtml}
          </div>
        </div>
      </div>
    `;

    const opt1 = document.createElement('option');
    opt1.value = slideIdx;
    opt1.textContent = `第 1 页: 班级总览与超均分表扬榜 (百分制)`;
    select.appendChild(opt1);
    container.appendChild(slide1);
    slideIdx++;

    // Slide 2: 提交检查情况 (大字号单行展开，学生姓名不折行)
    const slideSub = document.createElement('div');
    slideSub.className = 'slide';
    slideSub.id = `slide-${slideIdx}`;

    const effectiveSub = subCheck || {
      roster_total: meta.total_students || 0,
      submitted_count: meta.total_students || 0,
      unsubmitted_count: 0,
      submission_rate: 100,
      unsubmitted_students: [],
      unmatched_submissions: []
    };

    const unsubmittedList = effectiveSub.unsubmitted_students || [];
    let unsubmittedTagsHtml = '';
    unsubmittedList.forEach(st => {
      unsubmittedTagsHtml += `
        <div class="unsubmitted-tag">
          <div class="stu-info-wrap">
            <span class="stu-id">${escapeHtml(st.id || '')}</span>
            <span class="stu-name">${escapeHtml(st.name || '')}</span>
          </div>
          <span class="stu-status-badge">未提交</span>
        </div>
      `;
    });
    if (!unsubmittedTagsHtml) {
      unsubmittedTagsHtml = '<div style="color:var(--text-secondary);padding:16px;text-align:center;">全员已提交，无缺交人员！</div>';
    }

    const unmatchedList = effectiveSub.unmatched_submissions || [];
    let unmatchedItemsHtml = '';
    unmatchedList.forEach(um => {
      unmatchedItemsHtml += `
        <div class="unmatched-item">
          <div class="unmatched-head">
            <span class="unmatched-file">📄 ${escapeHtml(um.file || '')}</span>
            <span class="unmatched-badge">⚠️ 未匹配名册</span>
          </div>
          <div class="unmatched-body">
            <span><strong>填涂学号:</strong> <code>${escapeHtml(um.student_id || '')}</code></span>
            <span><strong>识别姓名:</strong> <span style="font-weight:800;color:#ef4444;">${escapeHtml(um.name || '未匹配')}</span></span>
          </div>
          <div class="unmatched-note">
            💡 <strong>核验建议:</strong> ${escapeHtml(um.note || '需人工核对答卷原图填涂及笔迹')}
          </div>
        </div>
      `;
    });
    if (!unmatchedItemsHtml) {
      unmatchedItemsHtml = '<div style="color:var(--text-secondary);padding:16px;text-align:center;">无异常答卷，全部正常匹配！</div>';
    }

    const subRate = effectiveSub.submission_rate !== undefined ? effectiveSub.submission_rate : ((effectiveSub.submitted_count / Math.max(1, effectiveSub.roster_total || 1)) * 100).toFixed(1);
    const unsubmittedRate = (100 - parseFloat(subRate)).toFixed(1);

    slideSub.innerHTML = `
      <div class="metrics-grid submission-metrics-grid">
        <div class="metric-card">
          <span class="label">名册应交总人数</span>
          <span class="value" style="color:var(--accent);">${effectiveSub.roster_total || 0} <small style="font-size:1.1rem;">人</small></span>
          <span class="sub-value">标准在籍学生名册底册</span>
        </div>
        <div class="metric-card">
          <span class="label">已提交答卷 (匹配成功)</span>
          <span class="value" style="color:#15803d;">${effectiveSub.submitted_count || 0} <small style="font-size:1.1rem;">份</small></span>
          <span class="sub-value">正常归档批改 · 提交率 ${subRate}%</span>
        </div>
        <div class="metric-card">
          <span class="label">未提交人数 (缺交/缺考)</span>
          <span class="value" style="color:#b91c1c;">${effectiveSub.unsubmitted_count || 0} <small style="font-size:1.1rem;">人</small></span>
          <span class="sub-value">未交率 ${unsubmittedRate}% · 需督促补交</span>
        </div>
        <div class="metric-card">
          <span class="label">异常提交 (未匹配到名册)</span>
          <span class="value" style="color:#b45309;">${unmatchedList.length} <small style="font-size:1.1rem;">份</small></span>
          <span class="sub-value">学号异常 · 待人工核验答卷原图</span>
        </div>
      </div>

      <div class="submission-content-layout">
        <div class="section-card">
          <div class="section-header" style="justify-content:space-between;">
            <div>
              <h2>⚠️ 未提交学生名单 <span class="badge-count" style="background:#fee2e2;color:#b91c1c;">${unsubmittedList.length} 人</span></h2>
              <span style="font-size:0.85rem;color:var(--text-secondary);">核对学号与姓名 · 课堂通报与课后跟进督促补交</span>
            </div>
            <button class="btn btn-primary" style="font-size:0.85rem;padding:5px 14px;" onclick="copyUnsubmittedList()">📋 一键复制未交名单</button>
          </div>
          <div class="unsubmitted-grid">
            ${unsubmittedTagsHtml}
          </div>
        </div>

        <div class="section-card">
          <div class="section-header">
            <div>
              <h2>🔍 未匹配到名册的提交 <span class="badge-count" style="background:#fef3c7;color:#b45309;">${unmatchedList.length} 份</span></h2>
              <span style="font-size:0.85rem;color:var(--text-secondary);">答卷已扫描批改，但填涂学号不在名册中</span>
            </div>
          </div>
          <div class="unmatched-list">
            ${unmatchedItemsHtml}
          </div>
        </div>
      </div>
    `;

    const optSub = document.createElement('option');
    optSub.value = slideIdx;
    optSub.textContent = `第 ${slideIdx + 1} 页: 提交检查情况 (参试与未交名单)`;
    select.appendChild(optSub);
    container.appendChild(slideSub);
    slideIdx++;

    // Slide 3: 快速对答案 (透打四色分级 + 填空题超大字排版)
    // 根据透打逻辑：错最多=红，错次多=黄，错再次多=蓝，错最少=绿
    const slideQuickAns = document.createElement('div');
    slideQuickAns.className = 'slide';
    slideQuickAns.id = `slide-${slideIdx}`;

    // 单题精讲从下一页开始（第 4 页，索引 3）
    const questionSlideStart = slideIdx + 1;

    function getTouDaTier(acc) {
      if (acc < 78) return { key: 'tier-danger', label: '🔴 错最多', color: '#ef4444' };
      if (acc < 85) return { key: 'tier-warn', label: '🟡 错次多', color: '#f59e0b' };
      if (acc < 90) return { key: 'tier-info', label: '🔵 错再次多', color: '#3b82f6' };
      return { key: 'tier-success', label: '🟢 错最少', color: '#22c55e' };
    }

    let tierCounts = { 'tier-danger': 0, 'tier-warn': 0, 'tier-info': 0, 'tier-success': 0 };
    questions.forEach(q => {
      const tier = getTouDaTier(q.accuracy || 0);
      tierCounts[tier.key]++;
    });

    let objAnsGridHtml = '';
    let subjAnsListHtml = '';
    let objCount = 0;
    let subjCount = 0;

    questions.forEach((q, qIdx) => {
      const targetSlide = questionSlideStart + qIdx;
      const acc = q.accuracy || 0;
      const tier = getTouDaTier(acc);
      const wrongCount = q.wrong_count !== undefined ? q.wrong_count : Math.round(((100 - acc) / 100) * (meta.total_students || 38));

      if (q.category === 'objective') {
        objCount++;
        objAnsGridHtml += `
          <div class="obj-ans-item ${tier.key}" onclick="jumpToSlide(${targetSlide})" title="点击直达 ${escapeHtml(q.title)} 详细讲评">
            <div class="obj-ans-qno">${escapeHtml(q.title)}</div>
            <div class="obj-ans-badge ${tier.key}">${escapeHtml(q.expected || '')}</div>
            <div class="obj-ans-stat ${tier.key}">
              <div>正答率 ${acc}%</div>
              <div style="font-size:0.75rem;opacity:0.85;">错 ${wrongCount}人</div>
            </div>
          </div>
        `;
      } else {
        subjCount++;
        const blanks = q.blanks || [];
        let pillsHtml = '';
        blanks.forEach(b => {
          pillsHtml += `
            <div class="subj-blank-pill">
              <span class="blank-num">${escapeHtml(b.label)}:</span>
              <span class="blank-val">${escapeHtml(b.expected || '')}</span>
            </div>
          `;
        });
        if (!pillsHtml) {
          pillsHtml = `
            <div class="subj-blank-pill" style="grid-column: 1 / -1;">
              <span class="blank-num">标准答案:</span>
              <span class="blank-val">${escapeHtml(q.expected || '')}</span>
            </div>
          `;
        }
        subjAnsListHtml += `
          <div class="subj-ans-block ${tier.key}">
            <div class="subj-ans-head">
              <div class="subj-qtitle">
                <span>${escapeHtml(q.title)} (${q.qtype} · 满分 ${q.max_score}分)</span>
                <span class="legend-item ${tier.key}" style="font-size:0.8rem;padding:2px 8px;">${tier.label} · 得分率 ${acc}%</span>
              </div>
              <button class="btn-jump-tiny" onclick="jumpToSlide(${targetSlide})">直达精讲 ➔</button>
            </div>
            <div class="subj-blanks-grid">
              ${pillsHtml}
            </div>
          </div>
        `;
      }
    });

    slideQuickAns.innerHTML = `
      <div class="quick-answer-header">
        <div>
          <h2 style="font-size:1.35rem;font-weight:800;display:flex;align-items:center;gap:10px;">
            ⚡ 全卷标准答案 · 快速对答案
            <span class="badge-count" style="font-size:0.85rem;background:#dcfce7;color:#15803d;">共 ${questions.length} 题</span>
          </h2>
          <span style="font-size:0.85rem;color:var(--text-secondary);">
            点击任意题卡可直达单题讲评 · 采用透打四色诊断逻辑进行错误程度分级预警
          </span>
        </div>
        <div style="display:flex;align-items:center;gap:12px;">
          <div class="quick-answer-legend">
            <span class="legend-item tier-danger">🔴 错最多 (${tierCounts['tier-danger']}题)</span>
            <span class="legend-item tier-warn">🟡 错次多 (${tierCounts['tier-warn']}题)</span>
            <span class="legend-item tier-info">🔵 错再次多 (${tierCounts['tier-info']}题)</span>
            <span class="legend-item tier-success">🟢 错最少 (${tierCounts['tier-success']}题)</span>
          </div>
          <button class="btn btn-primary" onclick="copyAllAnswers()" style="font-size:0.85rem;padding:5px 14px;">📋 复制全部答案</button>
        </div>
      </div>

      <div class="quick-answer-layout">
        <div class="section-card quick-answer-card">
          <div class="section-header">
            <h2>📝 客观选择题答案 <span class="badge-count">${objCount} 题</span></h2>
            <span style="font-size:0.82rem;color:var(--text-secondary);">按透打四级四色徽章标识 · 点击卡片直达单题</span>
          </div>
          <div class="obj-answers-grid">
            ${objAnsGridHtml}
          </div>
        </div>

        <div class="section-card quick-answer-card">
          <div class="section-header">
            <h2>✍️ 主观填空题答案 <span class="badge-count">${subjCount} 题</span></h2>
            <span style="font-size:0.82rem;color:var(--text-secondary);">超大字体高清投影 · 标明各空标准词</span>
          </div>
          <div class="subj-answers-list">
            ${subjAnsListHtml}
          </div>
        </div>
      </div>
    `;

    const optQuickAns = document.createElement('option');
    optQuickAns.value = slideIdx;
    optQuickAns.textContent = `第 ${slideIdx + 1} 页: 快速对答案 (全卷参考答案与透打四色诊断)`;
    select.appendChild(optQuickAns);
    container.appendChild(slideQuickAns);
    slideIdx++;

    // Questions Slides (Massive layout dominating the viewport)
    questions.forEach((q, qIndex) => {
      const thisSlideIdx = slideIdx;
      const slide = document.createElement('div');
      slide.className = 'slide';
      slide.id = `slide-${thisSlideIdx}`;

      const optElem = document.createElement('option');
      optElem.value = thisSlideIdx;
      optElem.textContent = `第 ${thisSlideIdx + 1} 页: ${q.title} (${q.qtype}, 正确率: ${q.accuracy}%)`;
      select.appendChild(optElem);

      const acc = q.accuracy || 0;
      const accClass = acc >= 85 ? 'high' : (acc >= 70 ? 'mid' : (acc >= 50 ? 'warn' : 'danger'));
      const knCodes = (q.knowledge_codes || []).map(code => `<span class="badge badge-knowledge">知识点: ${escapeHtml(code)}</span>`).join(' ');

      // 题干配图 HTML
      let imagesHtml = '';
      if (q.images && q.images.length > 0) {
        imagesHtml += '<div class="stem-images-box">';
        q.images.forEach((imgUri, imgIdx) => {
          imagesHtml += `
            <div class="q-thumb-wrap" onclick="openImageLightbox(${qIndex}, ${imgIdx})">
              <img src="${imgUri}" class="q-thumb-img" alt="题目插图">
              <span class="q-thumb-badge">🔍 点击全屏看图</span>
            </div>
          `;
        });
        imagesHtml += '</div>';
      }

      // 题干卡片 (HERO AREA OF THE SCREEN)
      let stemCardHtml = '';
      if (q.stem || imagesHtml) {
        stemCardHtml = `
          <div class="question-stem-card">
            <div class="stem-content-layout">
              <div class="question-stem-text">${escapeHtml(q.stem)}</div>
              ${imagesHtml}
            </div>
          </div>
        `;
      }

      if (q.category === 'objective') {
        // 客观选择题选项卡片：大字显示题干与选项，占满下半屏幕
        let optionsCardsHtml = '';
        (q.options_cards || []).forEach((card, cardIdx) => {
          const isCorrect = card.is_correct;
          const isDistractor = card.is_distractor;
          const cardClass = isCorrect ? 'correct' : (isDistractor ? 'distractor' : '');
          const statusBadge = isCorrect 
            ? '<span class="opt-status-tag correct">✅ 正确答案</span>' 
            : (isDistractor ? '<span class="opt-status-tag distractor">⚠️ 易错干扰项</span>' : '');

          const optTextDisplay = card.text ? `<div class="option-text-wrap"><div class="option-text">${escapeHtml(card.text)}</div></div>` : '<div class="option-text-wrap"></div>';

          // 名单触发按钮
          let rosterBtnHtml = '';
          if (card.count > 0) {
            rosterBtnHtml = `
              <button class="btn-view-roster" onclick="openOptionRoster(${qIndex}, ${cardIdx})">
                👥 查看名单 (${card.count}人)
              </button>
            `;
          } else {
            rosterBtnHtml = `<div class="btn-roster-empty">👥 0人选择</div>`;
          }

          optionsCardsHtml += `
            <div class="option-card ${cardClass}">
              <div class="option-head">
                <div class="opt-key-badge">${card.key}</div>
                ${statusBadge}
              </div>
              ${optTextDisplay}
              <div class="opt-stat-row">
                <span class="opt-count-large">${card.count} 人</span>
                <span class="opt-pct-large">(${card.pct}%)</span>
              </div>
              <div class="opt-progress-bar-bg">
                <div class="opt-progress-bar-fill" style="width: ${card.pct}%;"></div>
              </div>
              ${rosterBtnHtml}
            </div>
          `;
        });

        slide.innerHTML = `
          <div class="question-header">
            <div class="q-title-group">
              <span class="q-number">${q.title}</span>
              <span class="badge badge-type">${q.qtype}</span>
              <span class="badge badge-score">满分: ${q.max_score}分</span>
              <span class="badge badge-answer">标准答案: ${escapeHtml(q.expected)}</span>
              ${knCodes}
            </div>
            <div class="q-accuracy-badge">
              <span style="font-size:0.85rem;color:var(--text-secondary);">全班正确率</span>
              <span class="accuracy-circle ${accClass}">${q.accuracy}%</span>
            </div>
          </div>

          ${stemCardHtml}

          <div class="options-grid">
            ${optionsCardsHtml}
          </div>

          <div class="suggestion-box">
            <span>💡 <strong>诊断建议：</strong>${escapeHtml(q.teaching_suggestion || '无特定建议')}</span>
          </div>
        `;
      } else if (q.category === 'subjective') {
        // 主观填空题
        const blanks = q.blanks || [];
        let blanksCardsHtml = '';

        blanks.forEach((b, bIdx) => {
          const bAcc = b.accuracy || 0;
          const bAccClass = bAcc >= 85 ? 'high' : (bAcc >= 70 ? 'mid' : (bAcc >= 50 ? 'warn' : 'danger'));

          let wrongSnippetHtml = '';
          const commonWrongs = b.common_wrong || [];
          if (commonWrongs.length > 0) {
            const chips = commonWrongs.map(cw => `<span class="wrong-tag">${escapeHtml(cw[0])} (${cw[1]}人)</span>`).join(' ');
            wrongSnippetHtml = `<div class="wrong-tags-row"><span class="wrong-title">典型错答:</span> ${chips}</div>`;
          }

          const fullCount = (b.full_score_students || []).length;
          const zeroCount = (b.zero_score_students || []).length;
          const partCount = (b.partial_score_students || []).length;

          let partBtnHtml = '';
          if (partCount > 0) {
            partBtnHtml = `
              <button class="btn-roster-sub warning" onclick="openBlankRoster(${qIndex}, ${bIdx}, 'part')">
                ⚠️ 部分分 (${partCount}人)
              </button>
            `;
          }

          blanksCardsHtml += `
            <div class="blank-card">
              <div>
                <div class="blank-card-header">
                  <span class="blank-card-title">${escapeHtml(b.label)}</span>
                  <span class="accuracy-circle ${bAccClass}" style="font-size:1.1rem;padding:2px 10px;">得分率 ${b.accuracy}%</span>
                </div>
                <div class="blank-answer-box">
                  <span class="blank-ans-label">参考答案:</span>
                  <span class="blank-ans-val">${escapeHtml(b.expected)}</span>
                  <span class="blank-score-info">均分 ${b.avg_score} / 满分 ${b.max_score}分</span>
                </div>
                ${wrongSnippetHtml}
              </div>
              <div class="blank-roster-btns" style="${partCount > 0 ? 'grid-template-columns: repeat(3, 1fr);' : ''}">
                <button class="btn-roster-sub success" onclick="openBlankRoster(${qIndex}, ${bIdx}, 'full')">
                  🌟 满分名单 (${fullCount}人)
                </button>
                ${partBtnHtml}
                <button class="btn-roster-sub danger" onclick="openBlankRoster(${qIndex}, ${bIdx}, 'zero')">
                  ❌ 失分名单 (${zeroCount}人)
                </button>
              </div>
            </div>
          `;
        });

        slide.innerHTML = `
          <div class="question-header">
            <div class="q-title-group">
              <span class="q-number">${q.title}</span>
              <span class="badge badge-type">${q.qtype}</span>
              <span class="badge badge-score">总分: ${q.max_score}分</span>
              <span class="badge badge-answer">参考答案: ${escapeHtml(q.expected)}</span>
              ${knCodes}
            </div>
            <div class="q-accuracy-badge">
              <span style="font-size:0.85rem;color:var(--text-secondary);">全题平均得分率</span>
              <span class="accuracy-circle ${accClass}">${q.accuracy}%</span>
            </div>
          </div>

          ${stemCardHtml}

          <div class="subj-blanks-container">
            ${blanksCardsHtml}
          </div>

          <div class="suggestion-box">
            <span>💡 <strong>诊断建议：</strong>${escapeHtml(q.teaching_suggestion || '重点强化得分标准与规范表达。')}</span>
          </div>
        `;
      } else {
        // 计算题
        const fullCount = (q.full_score_students || []).length;
        const partCount = (q.partial_score_students || []).length;
        const zeroCount = (q.zero_score_students || []).length;

        slide.innerHTML = `
          <div class="question-header">
            <div class="q-title-group">
              <span class="q-number">${q.title}</span>
              <span class="badge badge-type">${q.qtype}</span>
              <span class="badge badge-score">满分: ${q.max_score}分</span>
              <span class="badge badge-answer">考查要点: ${escapeHtml(q.expected)}</span>
              ${knCodes}
            </div>
            <div class="q-accuracy-badge">
              <span style="font-size:0.85rem;color:var(--text-secondary);">得分率 (均分 ${q.avg_score}/${q.max_score})</span>
              <span class="accuracy-circle ${accClass}">${q.accuracy}%</span>
            </div>
          </div>

          ${stemCardHtml}

          <div class="subj-blanks-container" style="grid-template-columns: repeat(3, 1fr);">
            <div class="blank-card">
              <div class="blank-card-header">
                <span class="blank-card-title" style="color:#22c55e;">🌟 满分学生</span>
                <span style="font-weight:800;color:#22c55e;">${fullCount} 人</span>
              </div>
              <p style="font-size:0.95rem;color:var(--text-secondary);margin:8px 0 16px 0;">该题获得满分的优秀同学</p>
              <button class="btn-roster-sub success" onclick="openCalcRoster(${qIndex}, 'full')">
                👥 查看满分名单 (${fullCount}人)
              </button>
            </div>

            <div class="blank-card">
              <div class="blank-card-header">
                <span class="blank-card-title" style="color:#f59e0b;">⚠️ 部分得分</span>
                <span style="font-weight:800;color:#f59e0b;">${partCount} 人</span>
              </div>
              <p style="font-size:0.95rem;color:var(--text-secondary);margin:8px 0 16px 0;">步骤有扣分的同学及具体得分</p>
              <button class="btn-roster-sub warning" onclick="openCalcRoster(${qIndex}, 'part')">
                👥 查看部分得分名单 (${partCount}人)
              </button>
            </div>

            <div class="blank-card">
              <div class="blank-card-header">
                <span class="blank-card-title" style="color:#ef4444;">❌ 零分/未作答</span>
                <span style="font-weight:800;color:#ef4444;">${zeroCount} 人</span>
              </div>
              <p style="font-size:0.95rem;color:var(--text-secondary);margin:8px 0 16px 0;">未能得分需重点辅导的同学</p>
              <button class="btn-roster-sub danger" onclick="openCalcRoster(${qIndex}, 'zero')">
                👥 查看零分名单 (${zeroCount}人)
              </button>
            </div>
          </div>

          <div class="suggestion-box">
            <span>💡 <strong>诊断建议：</strong>${escapeHtml(q.teaching_suggestion || '重点强化得分标准与规范表达。')}</span>
          </div>
        `;
      }

      container.appendChild(slide);
      slideIdx++;
    });

    totalSlides = slideIdx;
    updateSlideControls();
  }

  function updateSlideControls() {
    const slides = document.querySelectorAll('.slide');
    slides.forEach((s, idx) => {
      if (idx === currentSlideIndex) {
        s.classList.add('active');
      } else {
        s.classList.remove('active');
      }
    });

    document.getElementById('slideCounter').textContent = `${currentSlideIndex + 1} / ${totalSlides}`;
    document.getElementById('jumpSelect').value = currentSlideIndex;
    document.getElementById('prevBtn').disabled = (currentSlideIndex === 0);
    document.getElementById('nextBtn').disabled = (currentSlideIndex === totalSlides - 1);

    // 自动重绘本页板书笔迹
    redrawSlideWhiteboard(currentSlideIndex);
  }

  function nextSlide() {
    if (currentSlideIndex < totalSlides - 1) {
      currentSlideIndex++;
      updateSlideControls();
    }
  }

  function prevSlide() {
    if (currentSlideIndex > 0) {
      currentSlideIndex--;
      updateSlideControls();
    }
  }

  function jumpToSlide(index) {
    if (index >= 0 && index < totalSlides) {
      currentSlideIndex = index;
      updateSlideControls();
    }
  }

  function toggleFullScreen() {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
    } else {
      if (document.exitFullscreen) {
        document.exitFullscreen().catch(() => {});
      }
    }
  }

  let isLight = true;
  function toggleTheme() {
    isLight = !isLight;
    const btn = document.getElementById('themeBtn');
    if (isLight) {
      document.documentElement.removeAttribute('data-theme');
      if (btn) btn.textContent = '🌙 深色';
    } else {
      document.documentElement.setAttribute('data-theme', 'dark');
      if (btn) btn.textContent = '☀️ 浅色';
    }
  }


  // ================= 白板批注系统逻辑 =================
  let wbActive = false;
  let wbPointerMode = false;
  let wbTool = 'pen';
  let wbColor = '#ef4444';
  let wbWidth = 6;
  let wbIsDrawing = false;
  let wbCurrentStroke = null;
  const slideStrokes = {};

  let wbCanvas = null;
  let wbCtx = null;

  function initWhiteboard() {
    wbCanvas = document.getElementById('whiteboardCanvas');
    if (!wbCanvas) return;
    wbCtx = wbCanvas.getContext('2d');
    resizeWhiteboardCanvas();

    wbCanvas.addEventListener('pointerdown', handleWbPointerDown);
    wbCanvas.addEventListener('pointermove', handleWbPointerMove);
    wbCanvas.addEventListener('pointerup', handleWbPointerUp);
    wbCanvas.addEventListener('pointercancel', handleWbPointerUp);
    wbCanvas.addEventListener('pointerleave', handleWbPointerUp);

    window.addEventListener('resize', () => {
      resizeWhiteboardCanvas();
    });

    document.documentElement.style.setProperty('--current-wb-color', wbColor);
  }

  function resizeWhiteboardCanvas() {
    if (!wbCanvas || !wbCtx) return;
    const container = document.getElementById('slideContainer');
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;

    wbCanvas.width = rect.width * dpr;
    wbCanvas.height = rect.height * dpr;
    wbCanvas.style.width = rect.width + 'px';
    wbCanvas.style.height = rect.height + 'px';

    wbCtx.scale(dpr, dpr);
    redrawSlideWhiteboard(currentSlideIndex);
  }

  function getCanvasCoords(e) {
    const rect = wbCanvas.getBoundingClientRect();
    return {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top
    };
  }

  function handleWbPointerDown(e) {
    if (!wbActive || wbPointerMode) return;
    if (e.button !== undefined && e.button !== 0) return;

    wbIsDrawing = true;
    try {
      wbCanvas.setPointerCapture(e.pointerId);
    } catch (_) {}

    const pt = getCanvasCoords(e);
    wbCurrentStroke = {
      tool: wbTool,
      color: wbColor,
      width: wbWidth,
      points: [pt]
    };

    wbCtx.save();
    wbCtx.lineCap = 'round';
    wbCtx.lineJoin = 'round';

    if (wbTool === 'pen') {
      wbCtx.globalCompositeOperation = 'source-over';
      wbCtx.strokeStyle = wbColor;
      wbCtx.fillStyle = wbColor;
      wbCtx.lineWidth = wbWidth;
    } else {
      wbCtx.globalCompositeOperation = 'destination-out';
      wbCtx.strokeStyle = 'rgba(0,0,0,1)';
      wbCtx.fillStyle = 'rgba(0,0,0,1)';
      wbCtx.lineWidth = wbWidth * 3.5;
    }

    wbCtx.beginPath();
    const dotRadius = (wbTool === 'pen' ? wbWidth : wbWidth * 3.5) / 2;
    wbCtx.arc(pt.x, pt.y, Math.max(0.5, dotRadius), 0, Math.PI * 2);
    wbCtx.fill();

    wbCtx.beginPath();
    wbCtx.moveTo(pt.x, pt.y);
  }

  function handleWbPointerMove(e) {
    if (!wbIsDrawing || !wbCurrentStroke) return;
    const pt = getCanvasCoords(e);
    wbCurrentStroke.points.push(pt);

    wbCtx.lineTo(pt.x, pt.y);
    wbCtx.stroke();
    wbCtx.beginPath();
    wbCtx.moveTo(pt.x, pt.y);
  }

  function handleWbPointerUp(e) {
    if (!wbIsDrawing) return;
    wbIsDrawing = false;
    try {
      wbCanvas.releasePointerCapture(e.pointerId);
    } catch (_) {}

    wbCtx.restore();

    if (wbCurrentStroke && wbCurrentStroke.points.length > 0) {
      if (!slideStrokes[currentSlideIndex]) {
        slideStrokes[currentSlideIndex] = [];
      }
      slideStrokes[currentSlideIndex].push(wbCurrentStroke);
    }
    wbCurrentStroke = null;
  }

  function redrawSlideWhiteboard(slideIndex) {
    if (!wbCanvas || !wbCtx) return;
    const dpr = window.devicePixelRatio || 1;

    wbCtx.save();
    wbCtx.setTransform(1, 0, 0, 1, 0, 0);
    wbCtx.clearRect(0, 0, wbCanvas.width, wbCanvas.height);
    wbCtx.restore();

    const strokes = slideStrokes[slideIndex] || [];
    if (strokes.length === 0) return;

    strokes.forEach(s => {
      wbCtx.save();
      wbCtx.lineCap = 'round';
      wbCtx.lineJoin = 'round';

      if (s.tool === 'pen') {
        wbCtx.globalCompositeOperation = 'source-over';
        wbCtx.strokeStyle = s.color;
        wbCtx.fillStyle = s.color;
        wbCtx.lineWidth = s.width;
      } else {
        wbCtx.globalCompositeOperation = 'destination-out';
        wbCtx.strokeStyle = 'rgba(0,0,0,1)';
        wbCtx.fillStyle = 'rgba(0,0,0,1)';
        wbCtx.lineWidth = s.width * 3.5;
      }

      if (s.points.length === 1) {
        wbCtx.beginPath();
        const r = (s.tool === 'pen' ? s.width : s.width * 3.5) / 2;
        wbCtx.arc(s.points[0].x, s.points[0].y, Math.max(0.5, r), 0, Math.PI * 2);
        wbCtx.fill();
      } else if (s.points.length > 1) {
        wbCtx.beginPath();
        wbCtx.moveTo(s.points[0].x, s.points[0].y);
        for (let i = 1; i < s.points.length; i++) {
          wbCtx.lineTo(s.points[i].x, s.points[i].y);
        }
        wbCtx.stroke();
      }
      wbCtx.restore();
    });
  }

  function toggleWhiteboardMode() {
    if (wbActive) {
      closeWhiteboardMode();
    } else {
      openWhiteboardMode();
    }
  }

  function openWhiteboardMode() {
    wbActive = true;
    wbPointerMode = false;
    document.body.classList.add('wb-active');
    document.body.classList.remove('wb-pointer');
    const toggleBtn = document.getElementById('togglePenBtn');
    if (toggleBtn) toggleBtn.classList.add('active');
    const dock = document.getElementById('whiteboardDock');
    if (dock) dock.style.display = 'flex';
    setWbTool(wbTool);
    redrawSlideWhiteboard(currentSlideIndex);
  }

  function closeWhiteboardMode() {
    wbActive = false;
    wbPointerMode = false;
    document.body.classList.remove('wb-active', 'wb-pen', 'wb-eraser', 'wb-pointer');
    const toggleBtn = document.getElementById('togglePenBtn');
    if (toggleBtn) toggleBtn.classList.remove('active');
    const dock = document.getElementById('whiteboardDock');
    if (dock) dock.style.display = 'none';
  }

  function toggleWbPointerMode() {
    wbPointerMode = !wbPointerMode;
    const btn = document.getElementById('wbPointerBtn');
    if (wbPointerMode) {
      document.body.classList.add('wb-pointer');
      if (btn) btn.classList.add('active');
      const penBtn = document.getElementById('wbToolPen');
      if (penBtn) penBtn.classList.remove('active');
      const eraserBtn = document.getElementById('wbToolEraser');
      if (eraserBtn) eraserBtn.classList.remove('active');
    } else {
      document.body.classList.remove('wb-pointer');
      if (btn) btn.classList.remove('active');
      setWbTool(wbTool);
    }
  }

  function setWbTool(tool) {
    wbTool = tool;
    wbPointerMode = false;
    document.body.classList.remove('wb-pointer');
    const ptrBtn = document.getElementById('wbPointerBtn');
    if (ptrBtn) ptrBtn.classList.remove('active');

    const penBtn = document.getElementById('wbToolPen');
    const eraserBtn = document.getElementById('wbToolEraser');

    if (tool === 'pen') {
      document.body.classList.add('wb-pen');
      document.body.classList.remove('wb-eraser');
      if (penBtn) penBtn.classList.add('active');
      if (eraserBtn) eraserBtn.classList.remove('active');
    } else {
      document.body.classList.add('wb-eraser');
      document.body.classList.remove('wb-pen');
      if (eraserBtn) eraserBtn.classList.add('active');
      if (penBtn) penBtn.classList.remove('active');
    }
  }

  function setWbWidth(width) {
    wbWidth = width;
    document.querySelectorAll('.wb-width-btn').forEach(btn => {
      if (parseInt(btn.getAttribute('data-width')) === width) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });
    if (wbPointerMode) {
      setWbTool('pen');
    }
  }

  function setWbColor(color) {
    wbColor = color;
    document.documentElement.style.setProperty('--current-wb-color', color);
    document.querySelectorAll('.wb-color-btn').forEach(btn => {
      if (btn.getAttribute('data-color') === color) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });
    const picker = document.getElementById('wbCustomColor');
    if (picker) picker.value = color;
    setWbTool('pen');
  }

  function undoWbStroke() {
    const strokes = slideStrokes[currentSlideIndex];
    if (strokes && strokes.length > 0) {
      strokes.pop();
      redrawSlideWhiteboard(currentSlideIndex);
    }
  }

  function clearCurrentSlideWb() {
    slideStrokes[currentSlideIndex] = [];
    redrawSlideWhiteboard(currentSlideIndex);
  }

  // Keyboard Shortcuts
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const lb = document.getElementById('imageLightbox');
      if (lb && lb.classList.contains('show')) {
        closeLightbox();
        return;
      }
      closeRosterModal();
      if (wbActive) {
        closeWhiteboardMode();
      }
      return;
    }
    // 快捷键支持：Ctrl+Z 撤销板书笔迹或图片批注
    if (e.ctrlKey && (e.key === 'z' || e.key === 'Z')) {
      e.preventDefault();
      const lb = document.getElementById('imageLightbox');
      if (lb && lb.classList.contains('show')) {
        undoLbStroke();
      } else {
        undoWbStroke();
      }
      return;
    }
    if (document.getElementById('rosterModal').classList.contains('show') || document.getElementById('imageLightbox').classList.contains('show')) {
      return;
    }
    if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT')) {
      return;
    }
    // B 键快速开关板书模式
    if ((e.key === 'b' || e.key === 'B') && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      toggleWhiteboardMode();
      return;
    }
    if (e.key === 'ArrowRight' || e.key === ' ' || e.key === 'PageDown' || e.key === 'n' || e.key === 'N') {
      nextSlide();
    } else if (e.key === 'ArrowLeft' || e.key === 'PageUp' || e.key === 'p' || e.key === 'P') {
      prevSlide();
    } else if (e.key === 'Home') {
      jumpToSlide(0);
    } else if (e.key === 'End') {
      jumpToSlide(totalSlides - 1);
    }
  });

  window.addEventListener('DOMContentLoaded', () => {
    initPresentation();
    initWhiteboard();
  });
</script>
</body>
</html>
"""

def export_lecture_html(data, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    json_data_str = json.dumps(data, ensure_ascii=False)
    page_title = str(data.get('meta', {}).get('title', '试卷讲评课件'))
    html = RAW_HTML.replace('__PRESENTATION_JSON__', json_data_str).replace('__PAGE_TITLE__', page_title)
    output_path.write_text(html, encoding='utf-8')
    return output_path

