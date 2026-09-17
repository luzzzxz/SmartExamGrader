from __future__ import annotations

import csv
import json
import re
import sqlite3
import unicodedata
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


CATALOG_COLUMNS = ('知识点编号', '知识点内容')
IDENTITY_COLUMNS = ('题号', '知识点编号')
DEFAULT_QUESTION_BANK_URL = 'https://your-question-bank-server.example.com'
MANUAL_KNOWLEDGE_TESTS_FILE = 'manual_knowledge_tests.json'


def load_question_bank_url(project_dir: Path) -> str:
    path = project_dir / 'knowledge_bridge_settings.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        value = str(data.get('question_bank_url') or '').strip()
        if value:
            return value
    except Exception:
        pass
    return DEFAULT_QUESTION_BANK_URL


def save_question_bank_url(project_dir: Path, value: str) -> None:
    path = project_dir / 'knowledge_bridge_settings.json'
    path.write_text(
        json.dumps({'question_bank_url': value}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def _read_csv_rows(path: Path):
    last_error = None
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            with path.open('r', encoding=encoding, newline='') as handle:
                reader = csv.DictReader(handle)
                fieldnames = [str(name or '').strip() for name in (reader.fieldnames or [])]
                rows = [
                    {str(key or '').strip(): str(value or '').strip() for key, value in row.items()}
                    for row in reader
                ]
            return fieldnames, rows
        except Exception as exc:
            last_error = exc
    raise last_error or RuntimeError(f'无法读取CSV：{path}')


def load_knowledge_identity_csv(path: Path):
    """Load a specific question-to-knowledge CSV instead of guessing from a folder."""
    path = Path(path)
    fieldnames, rows = _read_csv_rows(path)
    if not all(column in fieldnames for column in IDENTITY_COLUMNS):
        raise ValueError('CSV必须包含“题号”和“知识点编号”两列。')
    mapping = defaultdict(list)
    for row in rows:
        question_no = normalize_question_no(row.get('题号'))
        code = str(row.get('知识点编号') or '').strip()
        if question_no and code and code not in mapping[question_no]:
            mapping[question_no].append(code)
    if not mapping:
        raise ValueError('CSV中没有可用的题号—知识点对应关系。')
    return dict(mapping)


def load_manual_knowledge_tests(project_dir: Path):
    path = project_dir / MANUAL_KNOWLEDGE_TESTS_FILE
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return []
    tests = data.get('tests') if isinstance(data, dict) else []
    return [item for item in tests if isinstance(item, dict)]


def save_manual_knowledge_tests(project_dir: Path, tests):
    path = project_dir / MANUAL_KNOWLEDGE_TESTS_FILE
    path.write_text(
        json.dumps({'tests': tests}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def export_manual_wrong_csv(project_dir: Path, record: dict, target_dir=None):
    """Export only manually entered wrong questions in the question-bank import format."""
    target_dir = Path(target_dir) if target_dir else Path(r'D:\题库系统_v2\exports')
    if not target_dir.exists() and target_dir == Path(r'D:\题库系统_v2\exports'):
        target_dir = project_dir / 'exports'
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[^A-Za-z0-9\u4e00-\u9fff_.-]+', '_', str(record.get('name') or '手工错题'))
    date_text = re.sub(r'[^0-9]', '', str(record.get('date') or '')) or datetime.now().strftime('%Y%m%d')
    target = target_dir / f'{safe_name}_{date_text}_手工错题.csv'
    with target.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['考试ID', '学生编号', '姓名', '题号', '学生答案', '得分', '满分', '是否正确'])
        for entry in record.get('entries') or []:
            is_class = str(entry.get('target') or '') == 'class'
            student_id = '' if is_class else str(entry.get('student_id') or '').strip()
            student_name = '全班共性错题' if is_class else str(entry.get('student_name') or '').strip()
            for question_no in entry.get('wrong_questions') or []:
                normalized = normalize_question_no(question_no)
                if normalized:
                    writer.writerow([
                        record.get('name') or '', student_id, student_name, normalized,
                        '', 0, 1, '错误',
                    ])
    return target


def normalize_question_no(value):
    text = unicodedata.normalize('NFKC', str(value or '')).strip()
    match = re.search(r'(\d{1,3})', text)
    if not match:
        return ''
    return str(int(match.group(1)))


def load_knowledge_catalog(project_dir: Path):
    candidates = [
        Path(r'D:\题库系统_v2\exports\知识点编号对应内容.csv'),
        project_dir / '知识点编号对应内容.csv',
    ]
    catalog = {}
    weights = {}
    source_path = None
    for path in candidates:
        if not path.exists():
            continue
        try:
            fieldnames, rows = _read_csv_rows(path)
        except Exception:
            continue
        if not all(column in fieldnames for column in CATALOG_COLUMNS):
            continue
        for row in rows:
            code = row.get('知识点编号', '').strip()
            if code:
                catalog[code] = row.get('知识点内容', '').strip() or code
                try:
                    w = float(row.get('知识点权重', '').strip() or '1')
                except (ValueError, TypeError):
                    w = 1.0
                weights[code] = max(w, 0.0)
        source_path = path
        break
    return catalog, weights, source_path


def find_knowledge_identity(folder: Path):
    if not folder.exists() or not folder.is_dir():
        return {}, None
    candidates = sorted(
        folder.glob('*.csv'),
        key=lambda path: (
            0 if '知识点' in path.name else 1,
            1 if '题库系统' in path.name else 0,
            -path.stat().st_mtime,
        ),
    )
    for path in candidates:
        try:
            fieldnames, rows = _read_csv_rows(path)
        except Exception:
            continue
        if not all(column in fieldnames for column in IDENTITY_COLUMNS):
            continue
        mapping = defaultdict(list)
        for row in rows:
            question_no = normalize_question_no(row.get('题号'))
            code = str(row.get('知识点编号') or '').strip()
            if question_no and code and code not in mapping[question_no]:
                mapping[question_no].append(code)
        if mapping:
            return dict(mapping), path
    return {}, None


def knowledge_map_from_scheme_payload(payload):
    if not isinstance(payload, dict):
        return {}
    identity = payload.get('knowledge_identity') or {}
    rows = identity.get('rows') if isinstance(identity, dict) else []
    if not isinstance(rows, list):
        return {}
    mapping = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        question_no = normalize_question_no(row.get('题号'))
        code = str(row.get('知识点编号') or '').strip()
        if question_no and code and code not in mapping[question_no]:
            mapping[question_no].append(code)
    return dict(mapping)


def load_json_payload(path: Path):
    try:
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def find_session_knowledge_identity(project_dir: Path, session_id: str, result_payloads):
    safe_id = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(session_id or '')).strip('._-')
    snapshot_path = project_dir / 'session_answer_keys' / f'{safe_id}_answer_key.json'
    candidate_paths = []
    if snapshot_path.exists():
        snapshot = load_json_payload(snapshot_path)
        mapping = knowledge_map_from_scheme_payload(snapshot)
        if mapping:
            return mapping, snapshot_path
        source_path = str(snapshot.get('source_scheme_path') or '').strip()
        if source_path:
            candidate_paths.append(Path(source_path))
    for result in result_payloads or []:
        source_path = str(result.get('answer_scheme_path') or '').strip()
        if source_path:
            candidate_paths.append(Path(source_path))
    seen = set()
    for path in candidate_paths:
        path_key = str(path).lower()
        if path_key in seen or not path.exists():
            continue
        seen.add(path_key)
        mapping = knowledge_map_from_scheme_payload(load_json_payload(path))
        if mapping:
            return mapping, path
    return {}, None


def _question_from_label(value):
    return normalize_question_no(value)


def load_session_part_question_map(project_dir: Path, session_id: str):
    path = project_dir / 'session_template_configs' / f'{session_id}_subjective_config.json'
    if not path.exists():
        return {}
    try:
        config = json.loads(path.read_text(encoding='utf-8-sig'))
    except Exception:
        return {}

    result = {}

    def register(part_id, *labels):
        if not part_id:
            return
        for label in labels:
            question_no = _question_from_label(label)
            if question_no:
                result[str(part_id)] = question_no
                return

    for item in config.get('items') or []:
        if not isinstance(item, dict):
            continue
        item_qno = str(item.get('question_no') or '').strip()
        item_label = item.get('answer_label') or item.get('label') or item_qno
        register(item.get('part_id'), item_label, item_qno)

        for blank_index, blank in enumerate(item.get('blanks') or [], 1):
            if not isinstance(blank, dict):
                continue
            blank_no = blank.get('blank_no') or blank_index
            part_id = blank.get('part_id') or f'q{item_qno}_b{blank_no}'
            register(
                part_id,
                blank.get('answer_label'),
                blank.get('label'),
                item_label,
                item_qno,
            )

        for sub_index, sub in enumerate(item.get('subquestions') or [], 1):
            if not isinstance(sub, dict):
                continue
            sub_no = sub.get('sub_no') or sub.get('question_no') or sub_index
            sub_label = sub.get('answer_label') or sub.get('label') or item_label
            register(sub.get('part_id'), sub_label, item_label, item_qno)
            for blank_index, blank in enumerate(sub.get('blanks') or [], 1):
                if not isinstance(blank, dict):
                    continue
                blank_no = blank.get('blank_no') or blank_index
                part_id = blank.get('part_id') or f'q{item_qno}_s{sub_no}_b{blank_no}'
                register(
                    part_id,
                    blank.get('answer_label'),
                    blank.get('label'),
                    sub_label,
                    item_label,
                    item_qno,
                )
    return result


def manual_test_to_session(record):
    """Represent a hand-entered wrong-answer record like a normal scoring session."""
    raw_map = record.get('knowledge_map') or {}
    knowledge_map = {
        normalize_question_no(question_no): [str(code).strip() for code in codes if str(code).strip()]
        for question_no, codes in raw_map.items()
        if normalize_question_no(question_no) and isinstance(codes, list)
    }
    knowledge_map = {question_no: codes for question_no, codes in knowledge_map.items() if codes}
    if not knowledge_map:
        return None

    payloads = []
    for entry in record.get('entries') or []:
        if not isinstance(entry, dict):
            continue
        wrong_questions = []
        for value in entry.get('wrong_questions') or []:
            question_no = normalize_question_no(value)
            if question_no and question_no in knowledge_map and question_no not in wrong_questions:
                wrong_questions.append(question_no)
        if not wrong_questions:
            continue
        details = {
            question_no: {'full_score': 1, 'score': 0}
            for question_no in wrong_questions
        }
        payloads.append({
            'roster_class': str(record.get('class_name') or '').strip(),
            'score_id': str(entry.get('student_id') or '').strip(),
            'student_name': str(entry.get('student_name') or '').strip(),
            'class_summary': str(entry.get('target') or '') == 'class',
            'score_info': {'detailed_scores': {'manual': {'details': details}}},
        })
    if not payloads:
        return None
    date_text = str(record.get('date') or '').strip() or datetime.now().strftime('%Y-%m-%d')
    return {
        'id': str(record.get('id') or f'manual-{uuid.uuid4()}'),
        'name': str(record.get('name') or '手工录入测试').strip(),
        'created_at': f'{date_text}T12:00:00',
        'roster_name': str(record.get('class_name') or '').strip(),
        'folder': Path(''),
        'knowledge_map': knowledge_map,
        'identity_path': Path(str(record.get('identity_csv') or '')),
        'part_question_map': {},
        'payloads': payloads,
        'is_manual': True,
    }


def record_question_no(part_id, record, part_question_map):
    if part_id in part_question_map:
        return part_question_map[part_id]
    for key in ('answer_label', 'label', 'question_no'):
        question_no = _question_from_label((record or {}).get(key))
        if question_no:
            return question_no
    for pattern in (
        r'q(\d+)_s\d+_b\d+',
        r'q(\d+)_b\d+',
        r'q(\d+)',
        r'calc_q(\d+)_s.+',
    ):
        match = re.fullmatch(pattern, str(part_id or ''))
        if match:
            return str(int(match.group(1)))
    return ''


def extract_question_scores(payload, part_question_map=None):
    part_question_map = part_question_map or {}
    per_question = defaultdict(lambda: {'score': 0.0, 'max_score': 0.0})
    objective_seen = set()
    detailed_scores = ((payload.get('score_info') or {}).get('detailed_scores') or {})
    for zone_data in detailed_scores.values():
        for qno, detail in ((zone_data or {}).get('details') or {}).items():
            question_no = normalize_question_no(qno)
            if not question_no or question_no in objective_seen:
                continue
            try:
                max_score = float((detail or {}).get('full_score') or 0)
                score = float((detail or {}).get('score') or 0)
            except Exception:
                continue
            if max_score <= 0:
                continue
            objective_seen.add(question_no)
            per_question[question_no]['score'] += max(0.0, min(max_score, score))
            per_question[question_no]['max_score'] += max_score

    subjective_scores = ((payload.get('subjective') or {}).get('scores') or {})
    if isinstance(subjective_scores, dict):
        for part_id, record in subjective_scores.items():
            if not isinstance(record, dict) or record.get('score') is None:
                continue
            try:
                max_score = float(record.get('max_score') or 0)
                score = float(record.get('score') or 0)
            except Exception:
                continue
            if max_score <= 0:
                continue
            question_no = record_question_no(str(part_id), record, part_question_map)
            if not question_no:
                continue
            per_question[question_no]['score'] += max(0.0, min(max_score, score))
            per_question[question_no]['max_score'] += max_score
    return dict(per_question)


def student_key(payload):
    if bool((payload or {}).get('class_summary')):
        return ''
    name = str(payload.get('student_name') or '').strip()
    score_id = str(payload.get('score_id') or '').strip()
    if name and name not in {'未匹配', '未识别', '未知'}:
        return f'name:{name}'
    if score_id and '?' not in score_id and score_id not in {'未匹配', '未识别'}:
        return f'id:{score_id}'
    return ''


def payload_class(payload, session=None):
    class_name = str((payload or {}).get('roster_class') or '').strip()
    if class_name not in {'', '未导入名册', '未匹配', '未识别', '未知'}:
        return class_name
    roster_name = str((session or {}).get('roster_name') or '').strip()
    if roster_name:
        return roster_name
    return class_name or '未指定班级'


def load_analysis_dataset(db_path: Path, project_dir: Path):
    catalog, weights, catalog_path = load_knowledge_catalog(project_dir)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        session_rows = conn.execute(
            '''
            SELECT s.*, COUNT(r.id) AS result_count
            FROM sessions s
            LEFT JOIN session_results r ON r.session_id = s.id
            GROUP BY s.id
            HAVING COUNT(r.id) > 0
            ORDER BY s.created_at ASC
            '''
        ).fetchall()
        sessions = []
        students = defaultdict(lambda: {'names': Counter(), 'ids': Counter(), 'count': 0})
        classes = Counter()
        class_students = defaultdict(
            lambda: defaultdict(lambda: {'names': Counter(), 'ids': Counter(), 'count': 0})
        )
        for session_row in session_rows:
            session = dict(session_row)
            result_rows = conn.execute(
                'SELECT payload_json FROM session_results WHERE session_id = ? ORDER BY file_name',
                (session['id'],),
            ).fetchall()
            payloads = []
            inferred_folder = None
            for result_row in result_rows:
                try:
                    payload = json.loads(result_row['payload_json'])
                except Exception:
                    continue
                payloads.append(payload)
                if inferred_folder is None:
                    input_path = str(payload.get('input_path') or '').strip()
                    if input_path:
                        inferred_folder = Path(input_path).parent

            folder = Path(str(session.get('folder_path') or ''))
            if not folder.exists() and inferred_folder is not None:
                folder = inferred_folder
            knowledge_map, identity_path = find_session_knowledge_identity(
                project_dir,
                session['id'],
                payloads,
            )
            if not knowledge_map:
                knowledge_map, identity_path = find_knowledge_identity(folder)
            if not knowledge_map:
                continue
            part_map = load_session_part_question_map(project_dir, session['id'])
            session.update({
                'folder': folder,
                'knowledge_map': knowledge_map,
                'identity_path': identity_path,
                'part_question_map': part_map,
                'payloads': payloads,
            })
            sessions.append(session)
            for payload in payloads:
                class_name = payload_class(payload, session)
                classes[class_name] += 1
                key = student_key(payload)
                if not key:
                    continue
                name = str(payload.get('student_name') or '').strip()
                score_id = str(payload.get('score_id') or '').strip()
                for info in (students[key], class_students[class_name][key]):
                    if name and name not in {'未匹配', '未识别', '未知'}:
                        info['names'][name] += 1
                    if score_id and '?' not in score_id:
                        info['ids'][score_id] += 1
                    info['count'] += 1
    finally:
        conn.close()

    for record in load_manual_knowledge_tests(project_dir):
        session = manual_test_to_session(record)
        if not session:
            continue
        sessions.append(session)
        for payload in session['payloads']:
            class_name = payload_class(payload, session)
            classes[class_name] += 1
            key = student_key(payload)
            if not key:
                continue
            name = str(payload.get('student_name') or '').strip()
            score_id = str(payload.get('score_id') or '').strip()
            for info in (students[key], class_students[class_name][key]):
                if name and name not in {'未匹配', '未识别', '未知'}:
                    info['names'][name] += 1
                if score_id and '?' not in score_id:
                    info['ids'][score_id] += 1
                info['count'] += 1
    sessions.sort(key=lambda item: str(item.get('created_at') or ''))
    return {
        'sessions': sessions,
        'students': students,
        'classes': classes,
        'class_students': class_students,
        'catalog': catalog,
        'weights': weights,
        'catalog_path': catalog_path,
    }


def session_display_labels(sessions):
    counts = Counter()
    labels = []
    mapping = {}
    for session in sessions:
        date_text = str(session.get('created_at') or '')[:10]
        base = f'{date_text}  {session.get("name") or "未命名测试"}'
        counts[base] += 1
        label = base if counts[base] == 1 else f'{base}（{counts[base]}）'
        labels.append(label)
        mapping[label] = session
    return labels, mapping


def analyze_payloads(sessions, catalog, payload_filter, latest_only=False, weights=None):
    weights = weights or {}
    all_details = defaultdict(list)
    used_tests = 0
    skipped_question_count = 0

    if latest_only:
        # 以最后一次为准：每个 (学生, 知识点) 只保留最后一次 session 的数据
        # sessions 已按 created_at ASC 排序，后出现的自动覆盖前面的
        latest_data = {}  # key: (student_key_str, code) -> {score, max_score, wrong}
        for session in sessions:
            matched_payloads = [
                payload for payload in session['payloads']
                if payload_filter(payload, session)
            ]
            if not matched_payloads:
                continue
            used_tests += 1
            for payload in matched_payloads:
                sk = student_key(payload)
                question_scores = extract_question_scores(payload, session.get('part_question_map'))
                for question_no, result in question_scores.items():
                    codes = session['knowledge_map'].get(question_no) or []
                    if not codes:
                        skipped_question_count += 1
                        continue
                    share = 1.0 / len(codes)
                    detail_entry = {
                        'date': str(session.get('created_at') or '')[:10],
                        'session_name': session.get('name') or '',
                        'student_name': str(payload.get('student_name') or '').strip(),
                        'score_id': str(payload.get('score_id') or '').strip(),
                        'question_no': question_no,
                        'score': result['score'],
                        'max_score': result['max_score'],
                    }
                    for code in codes:
                        all_details[code].append(detail_entry)
                        is_wrong = result['score'] + 1e-9 < result['max_score']
                        # 后出现的 session 覆盖前面的 → 最后一次为准
                        latest_data[(sk, code)] = {
                            'score': result['score'] * share,
                            'max_score': result['max_score'] * share,
                            'wrong': is_wrong,
                        }
        # 汇总：用覆盖后的最终数据
        summary = defaultdict(lambda: {
            'score': 0.0, 'max_score': 0.0,
            'question_count': 0, 'wrong_count': 0,
        })
        for (_sk, code), data in latest_data.items():
            item = summary[code]
            item['score'] += data['score']
            item['max_score'] += data['max_score']
            item['question_count'] += 1
            if data['wrong']:
                item['wrong_count'] += 1
    else:
        # 全部累加：原有逻辑
        summary = defaultdict(lambda: {
            'score': 0.0, 'max_score': 0.0,
            'question_count': 0, 'wrong_count': 0,
        })
        for session in sessions:
            matched_payloads = [
                payload for payload in session['payloads']
                if payload_filter(payload, session)
            ]
            if not matched_payloads:
                continue
            used_tests += 1
            for payload in matched_payloads:
                question_scores = extract_question_scores(payload, session.get('part_question_map'))
                for question_no, result in question_scores.items():
                    codes = session['knowledge_map'].get(question_no) or []
                    if not codes:
                        skipped_question_count += 1
                        continue
                    share = 1.0 / len(codes)
                    detail_entry = {
                        'date': str(session.get('created_at') or '')[:10],
                        'session_name': session.get('name') or '',
                        'student_name': str(payload.get('student_name') or '').strip(),
                        'score_id': str(payload.get('score_id') or '').strip(),
                        'question_no': question_no,
                        'score': result['score'],
                        'max_score': result['max_score'],
                    }
                    for code in codes:
                        all_details[code].append(detail_entry)
                        item = summary[code]
                        item['score'] += result['score'] * share
                        item['max_score'] += result['max_score'] * share
                        item['question_count'] += 1
                        if result['score'] + 1e-9 < result['max_score']:
                            item['wrong_count'] += 1

    rows = []
    for code, item in summary.items():
        mastery = item['score'] / item['max_score'] * 100 if item['max_score'] else 0.0
        weight = weights.get(code, 1.0)
        risk_score = (100.0 - mastery) * weight
        rows.append({
            'code': code,
            'name': catalog.get(code, '未在知识点总表中找到'),
            'mastery': mastery,
            'weight': weight,
            'risk_score': risk_score,
            'details': all_details.get(code, []),
            **item,
        })
    rows.sort(key=lambda item: (-item['risk_score'], item['mastery'], item['code']))
    return rows, used_tests, skipped_question_count


def analyze_student(sessions, selected_key, catalog, selected_class='', latest_only=False, weights=None):
    return analyze_payloads(
        sessions,
        catalog,
        lambda payload, session: (
            student_key(payload) == selected_key
            and (not selected_class or payload_class(payload, session) == selected_class)
        ),
        latest_only=latest_only,
        weights=weights,
    )


def analyze_class(sessions, selected_class, catalog, latest_only=False, weights=None):
    return analyze_payloads(
        sessions,
        catalog,
        lambda payload, session: payload_class(payload, session) == selected_class,
        latest_only=latest_only,
        weights=weights,
    )


def open_window(app):
    project_dir = Path(__file__).resolve().parent
    dataset = load_analysis_dataset(project_dir / 'answer_card_app.db', project_dir)

    sessions = dataset['sessions']
    if not sessions and not getattr(app, 'rosters', {}):
        messagebox.showinfo(
            '学生知识点分析',
            '暂时没有测试记录，也没有可选择的班级名册。',
            parent=app.root,
        )
        return
    available_class_names = set(dataset['classes']) | set(getattr(app, 'rosters', {}).keys())
    if not available_class_names:
        messagebox.showinfo('学生知识点分析', '没有找到可用于班级分析的信息。', parent=app.root)
        return

    win = tk.Toplevel(app.root)
    win.title('知识点缺陷分析（班级总体 / 学生个人）')
    win.geometry('1180x780')
    win.minsize(920, 620)
    win.transient(app.root)

    class_labels = sorted(available_class_names)

    def make_student_choices(class_name):
        labels = []
        mapping = {}
        for key, info in dataset['class_students'].get(class_name, {}).items():
            name = info['names'].most_common(1)[0][0] if info['names'] else ''
            score_id = info['ids'].most_common(1)[0][0] if info['ids'] else ''
            label = f'{name or "未匹配"}（{score_id or "无学号"}）'
            if label in mapping:
                label = f'{label} #{len(labels) + 1}'
            labels.append(label)
            mapping[label] = key
        for score_id, name in (getattr(app, 'rosters', {}).get(class_name, {}) or {}).items():
            score_id = str(score_id or '').strip()
            name = str(name or '').strip()
            if not name and not score_id:
                continue
            label = f'{name or "未命名"}（{score_id or "无学号"}）'
            if label in mapping:
                continue
            labels.append(label)
            mapping[label] = f'name:{name}' if name else f'id:{score_id}'
        labels.sort()
        return labels, mapping

    student_labels, student_label_to_key = make_student_choices(class_labels[0])

    test_labels, test_label_map = session_display_labels(sessions)
    min_date = min(str(item.get('created_at') or '')[:10] for item in sessions)
    max_date = max(str(item.get('created_at') or '')[:10] for item in sessions)

    filters = ttk.LabelFrame(win, text='统计范围')
    filters.pack(fill=tk.X, padx=12, pady=(10, 6))

    analysis_mode_var = tk.StringVar(value='班级总体')
    class_var = tk.StringVar(value=class_labels[0])
    student_var = tk.StringVar(value=student_labels[0] if student_labels else '')
    scope_var = tk.StringVar(value='全部有知识点的测试')
    start_date_var = tk.StringVar(value=min_date)
    end_date_var = tk.StringVar(value=max_date)
    start_test_var = tk.StringVar(value=test_labels[0])
    end_test_var = tk.StringVar(value=test_labels[-1])
    weak_only_var = tk.BooleanVar(value=False)
    weak_line_var = tk.DoubleVar(value=80.0)
    stat_mode_var = tk.StringVar(value='全部累加')
    question_bank_url_var = tk.StringVar(value=load_question_bank_url(project_dir))

    ttk.Label(filters, text='分析对象').grid(row=0, column=0, padx=(10, 4), pady=8, sticky='w')
    analysis_mode_box = ttk.Combobox(
        filters,
        textvariable=analysis_mode_var,
        values=('班级总体', '学生个人'),
        state='readonly',
        width=12,
    )
    analysis_mode_box.grid(row=0, column=1, padx=4, pady=8, sticky='w')
    ttk.Label(filters, text='班级').grid(row=0, column=2, padx=(14, 4), pady=8, sticky='w')
    class_box = ttk.Combobox(filters, textvariable=class_var, values=class_labels, state='readonly', width=16)
    class_box.grid(row=0, column=3, padx=4, pady=8, sticky='w')
    ttk.Label(filters, text='学生').grid(row=0, column=4, padx=(14, 4), pady=8, sticky='w')
    student_box = ttk.Combobox(filters, textvariable=student_var, values=student_labels, state='disabled', width=24)
    student_box.grid(row=0, column=5, columnspan=2, padx=4, pady=8, sticky='w')

    ttk.Label(filters, text='方式').grid(row=1, column=0, padx=(10, 4), pady=(0, 8), sticky='w')
    scope_box = ttk.Combobox(
        filters,
        textvariable=scope_var,
        values=('全部有知识点的测试', '按日期范围', '按起止测试'),
        state='readonly',
        width=18,
    )
    scope_box.grid(row=1, column=1, padx=4, pady=(0, 8), sticky='w')
    ttk.Checkbutton(filters, text='只显示薄弱知识点', variable=weak_only_var).grid(
        row=1, column=2, columnspan=2, padx=(16, 4), pady=(0, 8)
    )
    ttk.Label(filters, text='薄弱线').grid(row=1, column=4, padx=(8, 2), pady=(0, 8), sticky='e')
    ttk.Entry(filters, textvariable=weak_line_var, width=6).grid(row=1, column=5, padx=(2, 4), pady=(0, 8), sticky='w')
    ttk.Label(filters, text='统计方式').grid(row=1, column=6, padx=(8, 2), pady=(0, 8), sticky='e')
    stat_mode_box = ttk.Combobox(
        filters,
        textvariable=stat_mode_var,
        values=('全部累加', '以最后一次为准'),
        state='readonly',
        width=14,
    )
    stat_mode_box.grid(row=1, column=7, padx=(2, 10), pady=(0, 8), sticky='w')

    ttk.Label(filters, text='日期').grid(row=2, column=0, padx=(10, 4), pady=(0, 8), sticky='w')
    ttk.Entry(filters, textvariable=start_date_var, width=12).grid(row=2, column=1, padx=4, pady=(0, 8), sticky='w')
    ttk.Label(filters, text='至').grid(row=2, column=2, padx=4, pady=(0, 8))
    ttk.Entry(filters, textvariable=end_date_var, width=12).grid(row=2, column=3, padx=4, pady=(0, 8), sticky='w')

    ttk.Label(filters, text='测试').grid(row=2, column=4, padx=(10, 4), pady=(0, 8), sticky='e')
    start_test_box = ttk.Combobox(filters, textvariable=start_test_var, values=test_labels, state='readonly', width=25)
    start_test_box.grid(row=2, column=5, padx=4, pady=(0, 8), sticky='w')
    ttk.Label(filters, text='至').grid(row=2, column=6, padx=2, pady=(0, 8))
    end_test_box = ttk.Combobox(filters, textvariable=end_test_var, values=test_labels, state='readonly', width=25)
    end_test_box.grid(row=2, column=7, padx=(2, 10), pady=(0, 8), sticky='w')
    filters.columnconfigure(5, weight=1)
    filters.columnconfigure(7, weight=1)

    status_var = tk.StringVar(value='')

    def normalized_question_bank_url():
        value = question_bank_url_var.get().strip().rstrip('/')
        if not value.startswith(('http://', 'https://')):
            raise ValueError('题库系统地址必须以 http:// 或 https:// 开头。')
        return value

    def save_question_bank_address(show_message=True):
        try:
            value = normalized_question_bank_url()
        except ValueError as exc:
            messagebox.showwarning('题库系统地址', str(exc), parent=win)
            return False
        question_bank_url_var.set(value)
        save_question_bank_url(project_dir, value)
        status_var.set('已保存题库系统地址。')
        if show_message:
            messagebox.showinfo('题库系统地址', '题库系统地址已保存。', parent=win)
        return True

    ttk.Label(filters, text='题库系统地址').grid(row=3, column=0, padx=(10, 4), pady=(0, 8), sticky='w')
    ttk.Entry(filters, textvariable=question_bank_url_var, width=48).grid(
        row=3, column=1, columnspan=6, padx=4, pady=(0, 8), sticky='ew'
    )
    ttk.Button(filters, text='保存地址', command=save_question_bank_address).grid(
        row=3, column=7, padx=(4, 10), pady=(0, 8), sticky='e'
    )

    ttk.Label(win, textvariable=status_var).pack(fill=tk.X, padx=16, pady=(0, 4))

    panes = ttk.Panedwindow(win, orient=tk.VERTICAL)
    panes.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))

    summary_frame = ttk.LabelFrame(panes, text='知识点掌握情况（风险分最高的排在前面）')
    detail_frame = ttk.LabelFrame(panes, text='所选知识点的测试明细')
    panes.add(summary_frame, weight=3)
    panes.add(detail_frame, weight=2)

    summary_columns = ('code', 'name', 'weight', 'questions', 'wrong', 'score', 'mastery', 'risk')
    summary_tree = ttk.Treeview(summary_frame, columns=summary_columns, show='headings', selectmode='extended')
    headings = {
        'code': '知识点编号',
        'name': '知识点内容',
        'weight': '权重',
        'questions': '涉及题数',
        'wrong': '扣分次数',
        'score': '得分/满分',
        'mastery': '掌握率',
        'risk': '风险分',
    }
    widths = {'code': 100, 'name': 300, 'weight': 60, 'questions': 80, 'wrong': 80, 'score': 110, 'mastery': 80, 'risk': 80}
    for column in summary_columns:
        summary_tree.heading(column, text=headings[column])
        summary_tree.column(column, width=widths[column], anchor='center' if column != 'name' else 'w')
    summary_tree.tag_configure('weak', foreground='#b42318')
    summary_tree.tag_configure('medium', foreground='#9a6700')
    summary_tree.tag_configure('good', foreground='#137333')
    summary_scroll = ttk.Scrollbar(summary_frame, orient=tk.VERTICAL, command=summary_tree.yview)
    summary_tree.configure(yscrollcommand=summary_scroll.set)
    summary_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    summary_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    detail_columns = ('date', 'session', 'student', 'question', 'score', 'result')
    detail_tree = ttk.Treeview(detail_frame, columns=detail_columns, show='headings')
    for column, text, width in (
        ('date', '日期', 100),
        ('session', '测试', 240),
        ('student', '学生', 180),
        ('question', '题号', 80),
        ('score', '得分/满分', 120),
        ('result', '结果', 90),
    ):
        detail_tree.heading(column, text=text)
        detail_tree.column(column, width=width, anchor='center' if column not in {'session', 'student'} else 'w')
    detail_scroll = ttk.Scrollbar(detail_frame, orient=tk.VERTICAL, command=detail_tree.yview)
    detail_tree.configure(yscrollcommand=detail_scroll.set)
    detail_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    state = {'rows': {}, 'visible_sessions': []}

    def selected_summary_rows():
        return [
            state['rows'][item_id]
            for item_id in summary_tree.selection()
            if item_id in state['rows']
        ]

    def export_selected_knowledge_rows():
        rows = selected_summary_rows()
        if not rows:
            messagebox.showinfo('发送到题库', '请先用鼠标、Ctrl 或 Shift 选中一个或多个知识点。', parent=win)
            return
        codes = list(dict.fromkeys(
            str(row.get('code') or '').strip()
            for row in rows
            if str(row.get('code') or '').strip()
        ))
        if not codes:
            messagebox.showwarning('发送到题库', '所选项目没有有效的知识点编号。', parent=win)
            return
        if not save_question_bank_address(show_message=False):
            return
        target_url = f'{normalized_question_bank_url()}/api/grading_knowledge_request'
        payload = json.dumps({'knowledge_codes': codes}, ensure_ascii=False).encode('utf-8')
        request = Request(
            target_url,
            data=payload,
            headers={'Content-Type': 'application/json; charset=utf-8'},
            method='POST',
        )
        try:
            with urlopen(request, timeout=12) as response:
                if not 200 <= int(response.status) < 300:
                    raise RuntimeError(f'题库系统返回 HTTP {response.status}')
                response.read()
        except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as exc:
            status_var.set('发送失败，请检查题库系统地址是否正确，或题库系统是否已启动。')
            messagebox.showerror(
                '发送失败',
                '发送失败，请检查题库系统地址是否正确，或题库系统是否已启动。\n\n'
                f'当前地址：{normalized_question_bank_url()}\n原因：{exc}',
                parent=win,
            )
            return
        win.clipboard_clear()
        win.clipboard_append(', '.join(codes))
        status_var.set(f'已发送 {len(codes)} 个知识点到题库系统。')
        messagebox.showinfo(
            '已发送到题库',
            f'已发送 {len(codes)} 个知识点。\n\n'
            '请到题库系统的“按知识点明细组卷”，点击“接收阅卷端知识点”。\n'
            '知识点编号也已复制，必要时可直接粘贴。',
            parent=win,
        )

    export_menu = tk.Menu(win, tearoff=0)
    export_menu.add_command(label='发送所选知识点到题库', command=export_selected_knowledge_rows)

    def show_export_menu(event):
        item_id = summary_tree.identify_row(event.y)
        if item_id:
            if item_id not in summary_tree.selection():
                summary_tree.selection_set(item_id)
            summary_tree.focus(item_id)
        if not summary_tree.selection():
            return
        try:
            export_menu.tk_popup(event.x_root, event.y_root)
        finally:
            export_menu.grab_release()

    def update_student_choices():
        nonlocal student_labels, student_label_to_key
        student_labels, student_label_to_key = make_student_choices(class_var.get())
        student_box.configure(values=student_labels)
        if student_var.get() not in student_label_to_key:
            student_var.set(student_labels[0] if student_labels else '')
        if analysis_mode_var.get() == '学生个人' and student_labels:
            student_box.configure(state='readonly')
        else:
            student_box.configure(state='disabled')

    def selected_sessions():
        mode = scope_var.get()
        if mode == '全部有知识点的测试':
            return list(sessions)
        if mode == '按日期范围':
            try:
                start = datetime.strptime(start_date_var.get().strip(), '%Y-%m-%d').date()
                end = datetime.strptime(end_date_var.get().strip(), '%Y-%m-%d').date()
            except ValueError:
                raise ValueError('日期请按 YYYY-MM-DD 填写。')
            if start > end:
                start, end = end, start
            return [
                session for session in sessions
                if start <= datetime.fromisoformat(str(session.get('created_at'))).date() <= end
            ]
        start_session = test_label_map.get(start_test_var.get())
        end_session = test_label_map.get(end_test_var.get())
        if not start_session or not end_session:
            return []
        start_index = sessions.index(start_session)
        end_index = sessions.index(end_session)
        if start_index > end_index:
            start_index, end_index = end_index, start_index
        return sessions[start_index:end_index + 1]

    def open_manual_entry():
        entry_win = tk.Toplevel(win)
        entry_win.title('手工录入错题测试')
        entry_win.geometry('760x500')
        entry_win.minsize(680, 440)
        entry_win.transient(win)
        entry_win.grab_set()

        body = ttk.Frame(entry_win, padding=14)
        body.pack(fill=tk.BOTH, expand=True)
        test_name_var = tk.StringVar()
        date_var = tk.StringVar(value=datetime.now().strftime('%Y-%m-%d'))
        manual_class_var = tk.StringVar(value=class_var.get() if class_var.get() in class_labels else class_labels[0])
        target_var = tk.StringVar(value='class')
        student_var_manual = tk.StringVar()
        csv_path_var = tk.StringVar()
        student_labels_manual = []
        student_map_manual = {}

        ttk.Label(body, text='测试名称').grid(row=0, column=0, padx=(0, 8), pady=(0, 8), sticky='e')
        ttk.Entry(body, textvariable=test_name_var, width=42).grid(row=0, column=1, columnspan=2, pady=(0, 8), sticky='ew')
        ttk.Label(body, text='测试日期').grid(row=0, column=3, padx=(14, 8), pady=(0, 8), sticky='e')
        ttk.Entry(body, textvariable=date_var, width=12).grid(row=0, column=4, pady=(0, 8), sticky='w')

        ttk.Label(body, text='班级').grid(row=1, column=0, padx=(0, 8), pady=6, sticky='e')
        manual_class_box = ttk.Combobox(body, textvariable=manual_class_var, values=class_labels, state='readonly', width=22)
        manual_class_box.grid(row=1, column=1, pady=6, sticky='w')
        ttk.Radiobutton(body, text='全班共性错题', variable=target_var, value='class').grid(row=1, column=2, padx=(14, 6), pady=6, sticky='w')
        ttk.Radiobutton(body, text='单个学生', variable=target_var, value='student').grid(row=1, column=3, padx=6, pady=6, sticky='w')

        ttk.Label(body, text='学生').grid(row=2, column=0, padx=(0, 8), pady=6, sticky='e')
        manual_student_box = ttk.Combobox(body, textvariable=student_var_manual, state='disabled', width=28)
        manual_student_box.grid(row=2, column=1, columnspan=2, pady=6, sticky='w')

        ttk.Label(body, text='题号—知识点CSV').grid(row=3, column=0, padx=(0, 8), pady=6, sticky='e')
        ttk.Entry(body, textvariable=csv_path_var).grid(row=3, column=1, columnspan=3, pady=6, sticky='ew')

        def choose_identity_csv():
            path = filedialog.askopenfilename(
                parent=entry_win,
                title='选择题号—知识点CSV',
                filetypes=[('CSV 文件', '*.csv'), ('所有文件', '*.*')],
            )
            if path:
                csv_path_var.set(path)

        ttk.Button(body, text='选择CSV', command=choose_identity_csv).grid(row=3, column=4, padx=(8, 0), pady=6, sticky='w')

        ttk.Label(body, text='错题题号').grid(row=4, column=0, padx=(0, 8), pady=(10, 4), sticky='ne')
        wrong_text = tk.Text(body, height=8, wrap=tk.WORD)
        wrong_text.grid(row=4, column=1, columnspan=4, pady=(10, 4), sticky='nsew')
        ttk.Label(
            body,
            text='用空格、逗号或换行分隔，例如：2, 5, 8, 12。全班录入只记录共同错题，不会虚构为每个学生都错。',
            foreground='#555555',
        ).grid(row=5, column=1, columnspan=4, pady=(0, 8), sticky='w')

        def refresh_manual_students(*_args):
            nonlocal student_labels_manual, student_map_manual
            student_labels_manual, student_map_manual = make_student_choices(manual_class_var.get())
            manual_student_box.configure(values=student_labels_manual)
            if student_var_manual.get() not in student_map_manual:
                student_var_manual.set(student_labels_manual[0] if student_labels_manual else '')
            manual_student_box.configure(state='readonly' if target_var.get() == 'student' else 'disabled')

        def parse_wrong_questions(text):
            result = []
            for token in re.split(r'[，,;；\s]+', str(text or '').strip()):
                if not token:
                    continue
                range_match = re.fullmatch(r'(\d+)\s*[-~至]\s*(\d+)', token)
                if range_match:
                    start, end = map(int, range_match.groups())
                    if abs(end - start) <= 100:
                        result.extend(str(item) for item in range(min(start, end), max(start, end) + 1))
                    continue
                question_no = normalize_question_no(token)
                if question_no:
                    result.append(question_no)
            return list(dict.fromkeys(result))

        def save_entry(and_export=False):
            name = test_name_var.get().strip()
            if not name:
                messagebox.showwarning('手工录入', '请填写测试名称。', parent=entry_win)
                return
            try:
                datetime.strptime(date_var.get().strip(), '%Y-%m-%d')
            except ValueError:
                messagebox.showwarning('手工录入', '测试日期请按 YYYY-MM-DD 填写。', parent=entry_win)
                return
            try:
                identity_path = Path(csv_path_var.get().strip())
                if not identity_path.exists():
                    raise FileNotFoundError('请先选择存在的题号—知识点CSV。')
                knowledge_map = load_knowledge_identity_csv(identity_path)
            except Exception as exc:
                messagebox.showerror('CSV无效', str(exc), parent=entry_win)
                return
            wrong_questions = parse_wrong_questions(wrong_text.get('1.0', tk.END))
            if not wrong_questions:
                messagebox.showwarning('手工录入', '请至少输入一个错题题号。', parent=entry_win)
                return
            unknown_questions = [question_no for question_no in wrong_questions if question_no not in knowledge_map]
            if unknown_questions:
                messagebox.showerror(
                    '题号未匹配',
                    '以下题号没有出现在所选 CSV 中：' + '、'.join(unknown_questions),
                    parent=entry_win,
                )
                return

            entry = {'target': target_var.get(), 'wrong_questions': wrong_questions}
            if target_var.get() == 'student':
                label = student_var_manual.get()
                key = student_map_manual.get(label)
                if not key:
                    messagebox.showwarning('手工录入', '请选择一个学生。', parent=entry_win)
                    return
                name_match = re.match(r'^(.*?)（(.*?)）$', label)
                entry.update({
                    'student_name': name_match.group(1).strip() if name_match else label,
                    'student_id': name_match.group(2).strip() if name_match else '',
                })

            record = {
                'id': f'manual-{uuid.uuid4()}',
                'name': name,
                'date': date_var.get().strip(),
                'class_name': manual_class_var.get().strip(),
                'identity_csv': str(identity_path),
                'knowledge_map': knowledge_map,
                'entries': [entry],
            }
            tests = load_manual_knowledge_tests(project_dir)
            tests.append(record)
            save_manual_knowledge_tests(project_dir, tests)
            export_path = export_manual_wrong_csv(project_dir, record) if and_export else None
            message = '已保存手工错题记录，并已加入知识点缺陷分析。'
            if export_path:
                message += f'\n\n已导出题库可导入的错题CSV：\n{export_path}'
            messagebox.showinfo('手工录入完成', message, parent=entry_win)
            entry_win.destroy()
            win.destroy()
            open_window(app)

        actions = ttk.Frame(body)
        actions.grid(row=6, column=0, columnspan=5, pady=(10, 0), sticky='e')
        ttk.Button(actions, text='保存记录', command=lambda: save_entry(False)).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text='保存并导出错题CSV', command=lambda: save_entry(True)).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text='取消', command=entry_win.destroy).pack(side=tk.LEFT, padx=4)
        body.columnconfigure(1, weight=1)
        body.columnconfigure(2, weight=1)
        body.columnconfigure(3, weight=1)
        body.rowconfigure(4, weight=1)
        manual_class_box.bind('<<ComboboxSelected>>', refresh_manual_students)
        target_var.trace_add('write', refresh_manual_students)
        refresh_manual_students()

    def show_detail(_event=None):
        for item_id in detail_tree.get_children():
            detail_tree.delete(item_id)
        selection = summary_tree.selection()
        if not selection:
            return
        row = state['rows'].get(selection[0])
        if not row:
            return
        for detail in sorted(
            row['details'],
            key=lambda item: (
                item['date'],
                item['session_name'],
                int(item['question_no']),
                item.get('student_name') or '',
            ),
        ):
            is_correct = detail['score'] + 1e-9 >= detail['max_score']
            student_text = detail.get('student_name') or '未匹配'
            if detail.get('score_id'):
                student_text += f"（{detail['score_id']}）"
            detail_tree.insert('', tk.END, values=(
                detail['date'],
                detail['session_name'],
                student_text,
                f"第{detail['question_no']}题",
                f"{detail['score']:g}/{detail['max_score']:g}",
                '正确' if is_correct else '扣分',
            ))

    def refresh():
        try:
            filtered_sessions = selected_sessions()
            weak_line = float(weak_line_var.get())
        except Exception as exc:
            messagebox.showerror('范围设置错误', str(exc), parent=win)
            return
        selected_class = class_var.get()
        latest_only = stat_mode_var.get() == '以最后一次为准'
        if analysis_mode_var.get() == '班级总体':
            rows, used_tests, skipped = analyze_class(filtered_sessions, selected_class, dataset['catalog'], latest_only=latest_only, weights=dataset['weights'])
            subject_text = f'班级 {selected_class}'
            summary_tree.heading('questions', text='作答题次')
            summary_tree.heading('wrong', text='扣分题次')
        else:
            selected_key = student_label_to_key.get(student_var.get())
            rows, used_tests, skipped = analyze_student(
                filtered_sessions,
                selected_key,
                dataset['catalog'],
                selected_class,
                latest_only=latest_only,
                weights=dataset['weights'],
            )
            subject_text = student_var.get() or '未选择学生'
            summary_tree.heading('questions', text='涉及题数')
            summary_tree.heading('wrong', text='扣分次数')
        summary_frame.configure(text=f'{subject_text}的知识点掌握情况（风险分最高的排在前面）')
        detail_frame.configure(text=f'{subject_text}：所选知识点的测试明细')
        if weak_only_var.get():
            rows = [row for row in rows if row['mastery'] < weak_line]
        for item_id in summary_tree.get_children():
            summary_tree.delete(item_id)
        for item_id in detail_tree.get_children():
            detail_tree.delete(item_id)
        state['rows'] = {}
        for index, row in enumerate(rows):
            if row['mastery'] < 60:
                tag = 'weak'
            elif row['mastery'] < weak_line:
                tag = 'medium'
            else:
                tag = 'good'
            item_id = summary_tree.insert('', tk.END, values=(
                row['code'],
                row['name'],
                f"{row['weight']:g}",
                row['question_count'],
                row['wrong_count'],
                f"{row['score']:g}/{row['max_score']:g}",
                f"{row['mastery']:.1f}%",
                f"{row['risk_score']:.0f}",
            ), tags=(tag,))
            state['rows'][item_id] = row
        weak_count = sum(1 for row in rows if row['mastery'] < weak_line)
        status = f'{subject_text}：只统计已完成评分的题目。纳入 {used_tests} 次测试，统计 {len(rows)} 个知识点，其中低于 {weak_line:g}% 的有 {weak_count} 个。'
        if skipped:
            skipped_unit = '个已评分题次' if analysis_mode_var.get() == '班级总体' else '道已评分题'
            status += f' 有 {skipped} {skipped_unit}在知识点身份证中没有对应题号，已跳过。'
        if dataset['catalog_path']:
            status += f' 知识点总表：{dataset["catalog_path"].name}'
        status_var.set(status)
        if summary_tree.get_children():
            first = summary_tree.get_children()[0]
            summary_tree.selection_set(first)
            summary_tree.focus(first)
            show_detail()

    def refresh_analysis_context(*_args):
        update_student_choices()
        refresh()

    button_row = ttk.Frame(filters)
    button_row.grid(row=0, column=8, rowspan=3, padx=(8, 10), pady=8, sticky='ns')
    ttk.Button(button_row, text='刷新分析', command=refresh).pack(fill=tk.X, pady=(0, 4))
    ttk.Button(button_row, text='手工录入错题', command=open_manual_entry).pack(fill=tk.X, pady=(0, 4))
    ttk.Button(button_row, text='关闭', command=win.destroy).pack(fill=tk.X)

    summary_tree.bind('<<TreeviewSelect>>', show_detail)
    def select_all_summary_rows(_event=None):
        summary_tree.selection_set(summary_tree.get_children())
        return 'break'

    summary_tree.bind('<Button-3>', show_export_menu)
    summary_tree.bind('<Control-a>', select_all_summary_rows)
    student_box.bind('<<ComboboxSelected>>', lambda _event: refresh())
    scope_box.bind('<<ComboboxSelected>>', lambda _event: refresh())
    stat_mode_box.bind('<<ComboboxSelected>>', lambda _event: refresh())
    analysis_mode_var.trace_add('write', refresh_analysis_context)
    class_var.trace_add('write', refresh_analysis_context)
    weak_only_var.trace_add('write', lambda *_args: refresh())
    refresh()
