from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
import unicodedata

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk, scrolledtext

APP_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = APP_DIR
DB_PATH = PROJECT_DIR / 'answer_card_app.db'
MANUAL_WEB_SYNC_CONFIG_PATH = PROJECT_DIR / 'manual_web_sync_config.json'
MANUAL_WEB_PACKAGES_DIR = PROJECT_DIR / 'manual_web_packages'
MANUAL_WEB_DEFAULT_API_URL = 'https://your-manual-grading-server.example.com'


class ManualWebSyncMixin:
    """Mixin class providing VPS manual grading web server synchronization."""

    def read_manual_web_vps_host_from_config_file(self):
        info_path = Path('D:/onedrive') / 'openclaw配置方法' / 'VPS信息.txt'
        if not info_path.exists():
            return ''
        try:
            lines = [line.strip() for line in info_path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        except Exception:
            return ''
        for line in reversed(lines):
            match = re.search(r'((?:\d{1,3}\.){3}\d{1,3}|[A-Za-z0-9.-]+\.[A-Za-z]{2,})', line)
            if match:
                return match.group(1)
        return ''

    def load_manual_web_sync_config(self):
        data = {}
        if MANUAL_WEB_SYNC_CONFIG_PATH.exists():
            try:
                data = json.loads(MANUAL_WEB_SYNC_CONFIG_PATH.read_text(encoding='utf-8-sig'))
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        if not data.get('server_url'):
            host = self.read_manual_web_vps_host_from_config_file()
            data['server_url'] = f'http://{host}:18765' if host else ''
        if not data.get('api_token'):
            data['api_token'] = uuid.uuid4().hex
        if not data.get('server_url'):
            data['server_url'] = simpledialog.askstring(
                '网页人工打分地址',
                '请输入网页人工打分服务地址，例如：http://你的VPS:18765',
                parent=self.root,
            ) or ''
        self.save_manual_web_sync_config(data)
        return data

    def save_manual_web_sync_config(self, data):
        try:
            MANUAL_WEB_SYNC_CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass

    def find_existing_folder_manual_web_job_id(self, folder_path=None):
        target_folder = self.normalize_folder_path(folder_path or self.current_folder_context())
        if not target_folder or not MANUAL_WEB_PACKAGES_DIR.exists():
            return ''
        try:
            packages = sorted(
                MANUAL_WEB_PACKAGES_DIR.glob('*.zip'),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except Exception:
            packages = []
        for package_path in packages:
            try:
                with zipfile.ZipFile(package_path, 'r') as zf:
                    manifest = json.loads(zf.read('manifest.json').decode('utf-8-sig'))
            except Exception:
                continue
            if str(manifest.get('session_id') or '').strip():
                continue
            source_score_file = str(manifest.get('source_score_file') or '').strip()
            source_folder = self.normalize_folder_path(Path(source_score_file).parent) if source_score_file else ''
            if source_folder != target_folder:
                continue
            job_id = str(manifest.get('job_id') or package_path.stem).strip()
            if job_id:
                return job_id
        return ''

    def manual_web_job_id(self):
        if self.current_session and self.current_session.get('id'):
            context = self.manual_web_context()
            digest = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()[:20]
            return 'session_' + re.sub(r'[^A-Za-z0-9_.-]+', '_', str(self.current_session.get('id'))).strip('._-') + '_' + digest
        base = '|'.join([
            str(getattr(self, 'selected_folder_path', '') or getattr(self, 'default_export_dir', '') or ''),
            str(getattr(self, 'current_template_key', '') or ''),
            str(len(self.summary_data or [])),
        ])
        return 'folder_' + hashlib.sha1(base.encode('utf-8', errors='ignore')).hexdigest()[:16]

    def manual_web_job_candidates(self):
        # A folder, reused file name, or old linked job is not test identity.
        return [self.manual_web_job_id()]

    def manual_web_context(self):
        images = []
        for entry in self.summary_data or []:
            for value in [entry.get('input_path')] + list((entry.get('side_files') or {}).values()):
                if value:
                    path = Path(value)
                    stat = path.stat() if path.is_file() else None
                    images.append([str(path.resolve()), stat.st_size if stat else None,
                                   stat.st_mtime_ns if stat else None])
        return {'session_id': (self.current_session or {}).get('id', ''),
                'template_key': self.current_template_key,
                'answers': self.subjective_answer_signature(),
                'structure': self.subjective_structure_signature(),
                'images': sorted(images)}

    def bind_manual_web_job_to_current_session(self, job_id):
        if not self.current_session or not self.current_session.get('id') or not job_id:
            return
        job_id = str(job_id).strip()
        self.current_session['manual_web_job_id'] = job_id
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute(
                'UPDATE sessions SET manual_web_job_id = ? WHERE id = ?',
                (job_id, self.current_session.get('id')),
            )
            conn.commit()
        finally:
            conn.close()

    def manual_web_job_name(self):
        if self.current_session and self.current_session.get('name'):
            return self.current_session.get('name')
        folder = getattr(self, 'selected_folder_path', '') or getattr(self, 'default_export_dir', '')
        return Path(folder).name if folder else datetime.now().strftime('manual-%Y%m%d-%H%M%S')

    def manual_web_score_choices(self, max_score):
        try:
            max_score = float(max_score or 0)
        except Exception:
            max_score = 1.0
        values = []
        step_count = int(max(0, round(max_score)))
        for index in range(step_count + 1):
            values.append(index)
        return values or [0, int(max_score)]

    def build_manual_web_task_package(self):
        if not self.summary_data:
            raise ValueError('请先完成一次批改，再上传网页人工打分任务。')
        parts = [part for part in self.iter_subjective_parts() if float(part.get('score') or 0) > 0]
        parts.extend(self.direct_calculation_manual_parts())
        if not parts:
            raise ValueError('当前模板没有可上传的主观题空位。')
        export_root, _saved, errors, crop_map = self.generate_subjective_crops(reuse_existing=True)
        if errors and not crop_map:
            raise ValueError('\n'.join(errors[:8]))

        payload = self.load_subjective_score_payload()
        payload = self.prepare_subjective_payload_for_grading(payload)
        all_scores = payload.get('scores', {})
        job_id = self.manual_web_job_id()
        MANUAL_WEB_PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
        package_path = MANUAL_WEB_PACKAGES_DIR / f'{job_id}.zip'

        part_payload = []
        for part in parts:
            part_payload.append({
                'part_id': part.get('part_id') or '',
                'label': part.get('label', ''),
                'kind': part.get('kind', ''),
                'score_category': part.get('score_category') or part.get('kind') or '',
                'max_score': float(part.get('score') or 0),
                'score_choices': self.manual_web_score_choices(part.get('score')),
                'expected_answer': ' / '.join(self.subjective_expected_answers_from_config(part.get('ocr'))),
            })

        entries_payload = []
        tasks = []
        part_order = {
            str(part.get('part_id') or ''): index
            for index, part in enumerate(parts)
        }
        entry_order = {}
        with zipfile.ZipFile(package_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            for entry_index, entry in enumerate(self.summary_data, 1):
                entry_key = self.subjective_entry_key(entry)
                entry_order[entry_key] = entry_index
                entry_scores = all_scores.get(entry_key, {}) if isinstance(all_scores, dict) else {}
                entries_payload.append({
                    'entry_key': entry_key,
                    'file': entry.get('file', ''),
                    'score_id': entry.get('score_id', ''),
                    'student_name': entry.get('student_name', ''),
                })
                for part in parts:
                    part_id = part.get('part_id') or ''
                    crop_id = 'calculation_back_region' if part.get('kind') == 'calculation' else part_id
                    crop_path = Path((crop_map.get(entry_key) or {}).get(crop_id) or '')
                    if not crop_path.exists():
                        continue
                    record = entry_scores.get(part_id, {}) if isinstance(entry_scores, dict) else {}
                    score_value = record.get('score') if isinstance(record, dict) else None
                    need_manual = True
                    if isinstance(record, dict):
                        need_manual = not bool(record.get('manual_graded') or record.get('auto_graded')) or bool(record.get('ocr_auto_need_manual'))
                    if not need_manual:
                        continue
                    image_name = f"images/{entry_index:04d}_{self.safe_file_part(part_id)}{crop_path.suffix.lower() or '.jpg'}"
                    zf.write(crop_path, image_name)
                    tasks.append({
                        'task_id': f'{entry_key}::{part_id}',
                        'entry_key': entry_key,
                        'part_id': part_id,
                        'label': part.get('label', ''),
                        'file': entry.get('file', ''),
                        'score_id': entry.get('score_id', ''),
                        'student_name': entry.get('student_name', ''),
                        'max_score': float(part.get('score') or 0),
                        'score_choices': self.manual_web_score_choices(part.get('score')),
                        'expected_answer': ' / '.join(self.subjective_expected_answers_from_config(part.get('ocr'))),
                        'image': image_name,
                        'current_score': score_value,
                        'pending': bool(need_manual),
                    })

            if not tasks:
                raise ValueError('当前没有需要上传到网页人工打分的空位。')

            # Mark one answer position across the whole class before moving to
            # the next position.  This mirrors the normal horizontal marking
            # workflow and makes neighbouring responses easy to compare.
            tasks.sort(key=lambda task: (
                part_order.get(str(task.get('part_id') or ''), len(part_order)),
                entry_order.get(str(task.get('entry_key') or ''), len(entry_order)),
            ))

            manifest = {
                'version': 1,
                'grading_context': self.manual_web_context(),
                'job_id': job_id,
                'job_name': self.manual_web_job_name(),
                'session_id': (self.current_session or {}).get('id', ''),
                'session_name': (self.current_session or {}).get('name', ''),
                'template_key': self.current_template_key,
                'template_name': self.current_template_name,
                'created_at': datetime.now().isoformat(timespec='seconds'),
                'source_score_file': str(self.subjective_scores_path()),
                'parts': part_payload,
                'entries': entries_payload,
                'tasks': tasks,
            }
            zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        return package_path, manifest

    def manual_web_request(self, method, path, body=None, content_type='application/json'):
        config = self.load_manual_web_sync_config()
        server_url = str(config.get('server_url') or '').rstrip('/')
        token = str(config.get('api_token') or '')
        if not server_url:
            raise ValueError('还没有设置网页人工打分服务地址。')
        data = body
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(server_url + path, data=data, method=method)
        request.add_header('X-Manual-Grading-Token', token)
        if data is not None:
            request.add_header('Content-Type', content_type)
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode('utf-8-sig'))
        except Exception:
            return {'raw': raw.decode('utf-8', errors='replace')}

    def show_copyable_text_window(self, title, text, parent=None):
        parent = parent or self.root
        win = tk.Toplevel(parent)
        win.title(title)
        win.geometry('760x300')
        win.transient(parent)
        ttk.Label(win, text=title, font=('Arial', 11, 'bold')).pack(anchor='w', padx=12, pady=(12, 6))
        box = scrolledtext.ScrolledText(win, height=7, wrap=tk.WORD, font=('Consolas', 11))
        box.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        box.insert('1.0', text)
        box.focus_set()
        box.tag_add('sel', '1.0', tk.END)
        try:
            parent.clipboard_clear()
            parent.clipboard_append(text)
        except Exception:
            pass
        footer = ttk.Frame(win)
        footer.pack(fill=tk.X, padx=12, pady=(0, 12))

        def copy_text():
            try:
                parent.clipboard_clear()
                parent.clipboard_append(box.get('1.0', tk.END).strip())
                self.status_var.set('已复制网页批改地址')
            except Exception as e:
                messagebox.showerror('复制失败', str(e), parent=win)

        ttk.Button(footer, text='复制地址', command=copy_text).pack(side=tk.LEFT, padx=4)
        ttk.Button(footer, text='关闭', command=win.destroy).pack(side=tk.LEFT, padx=4)

    def build_subjective_task_list_for_automation(self, reuse_existing=True):
        if not self.summary_data:
            raise ValueError('请先完成一次批改，才能生成主观题任务。')
        parts = [part for part in self.iter_subjective_parts() if float(part.get('score') or 0) > 0]
        if not parts:
            raise ValueError('当前模板没有主观题空位。')
        export_root, _saved, errors, crop_map = self.generate_subjective_crops(reuse_existing=reuse_existing)
        if errors and not crop_map:
            raise ValueError('\n'.join(errors[:8]))
        tasks = []
        for entry in self.summary_data:
            entry_key = self.subjective_entry_key(entry)
            for part in parts:
                part_id = part.get('part_id') or ''
                crop_path = Path((crop_map.get(entry_key) or {}).get(part_id) or '')
                if not crop_path.exists():
                    continue
                tasks.append({
                    'entry': entry,
                    'entry_key': entry_key,
                    'part': part,
                    'part_id': part_id,
                    'crop_path': str(crop_path),
                })
        if not tasks:
            raise ValueError(f'没有找到可处理的主观题裁图。\n目录：{export_root}')
        return tasks

    def mark_subjective_need_manual_record(self, record, task, reason):
        if record.get('manual_graded'):
            return
        for key in (
            'score',
            'max_score',
            'auto_graded',
            'auto_grade_match',
            'auto_grade_expected',
            'auto_grade_answer',
            'auto_grade_reason',
            'auto_grade_updated_at',
            'auto_grade_engine',
        ):
            record.pop(key, None)
        record.update({
            'ocr_auto_need_manual': True,
            'ocr_auto_reason': reason,
            'label': task['part'].get('label', ''),
            'student_name': task['entry'].get('student_name', ''),
            'score_id': task['entry'].get('score_id', ''),
            'file': task['entry'].get('file', ''),
        })

    def mark_subjective_auto_zero_record(self, record, task, answer_text, engine, reason):
        if record.get('manual_graded'):
            return
        max_score = float(task['part'].get('score') or 1)
        ocr_config = task['part'].get('ocr') or {}
        expected_answers = self.subjective_expected_answers_from_config(ocr_config)
        expected_text = ' / '.join(expected_answers)
        if not expected_text:
            expected_text = '关键词：' + ' / '.join(self.split_expected_keywords(ocr_config.get('expected_keywords')))
        record.update({
            'score': 0.0,
            'max_score': max_score,
            'auto_graded': True,
            'manual_graded': False,
            'auto_grade_match': False,
            'auto_grade_engine': engine,
            'auto_grade_expected': expected_text,
            'auto_grade_answer': '' if answer_text == '未识别到文字' else str(answer_text or '').strip(),
            'auto_grade_reason': reason,
            'auto_grade_updated_at': datetime.now().isoformat(timespec='seconds'),
            'ocr_auto_need_manual': False,
            'ocr_auto_reason': '',
            'label': task['part'].get('label', ''),
            'student_name': task['entry'].get('student_name', ''),
            'score_id': task['entry'].get('score_id', ''),
            'file': task['entry'].get('file', ''),
        })

    def handle_both_blank_with_recheck(self, record, task, crop_path=None, engine='PaddleOCR'):
        """
        双层OCR均未识别出文字时的仲裁逻辑：
        必须执行灰度笔迹对比（evaluate_crop_blank_status）：
        - 若灰度对比确认无笔迹 (blank)：按空白判0分，ocr_auto_need_manual=False。
        - 若灰度对比检测到有字/有笔迹 (has_handwriting 或 uncertain)：绝不判0分！送下一关（标记人工复核/大模型二审），ocr_auto_need_manual=True。
        """
        if not isinstance(record, dict) or record.get('manual_graded'):
            return 'skipped'

        cp = crop_path
        if not cp and isinstance(task, dict):
            cp = task.get('crop_path')
        if not cp and isinstance(record, dict):
            cp = record.get('crop_path')
        if not cp and hasattr(self, 'resolve_task_crop_path'):
            try:
                cp = self.resolve_task_crop_path(task, record)
            except Exception:
                pass

        if cp and Path(cp).exists():
            try:
                from core.blank_recheck import evaluate_crop_blank_status, get_blank_recheck_collector
                blank_status, features, blank_reason = evaluate_crop_blank_status(cp)
                try:
                    collector = get_blank_recheck_collector()
                    collector.record_sample(
                        crop_path=str(cp),
                        system_judgment=blank_status,
                        features=features,
                        layer1_text='',
                        layer1_score=None,
                        session_id=getattr(self, 'current_session', {}).get('id', '') if isinstance(getattr(self, 'current_session', None), dict) else '',
                        student_name=((task or {}).get('entry') or {}).get('student_name') or record.get('student_name', ''),
                        score_id=((task or {}).get('entry') or {}).get('score_id') or record.get('score_id', ''),
                        part_id=((task or {}).get('part') or {}).get('part_id') or record.get('part_id', ''),
                        label=((task or {}).get('part') or {}).get('label') or record.get('label', ''),
                        entry_key=(task or {}).get('entry_key') or record.get('file', ''),
                    )
                except Exception:
                    pass

                if blank_status == 'blank':
                    self.mark_subjective_auto_zero_record(
                        record, task, '', engine, '空白复核确认无笔迹，按空白判0分'
                    )
                    record['ocr_auto_need_manual'] = False
                    record['ocr_auto_reason'] = ''
                    return 'zero'
                else:
                    # has_handwriting 或 uncertain：有字送下一关
                    for key in (
                        'score', 'max_score', 'auto_graded', 'auto_grade_match',
                        'auto_grade_expected', 'auto_grade_answer',
                        'auto_grade_reason', 'auto_grade_updated_at', 'auto_grade_engine'
                    ):
                        record.pop(key, None)
                    reason_text = (
                        '两层OCR未识别文字，但灰度对比检测到有字，送下一关'
                        if blank_status == 'has_handwriting'
                        else '两层OCR未识别文字，灰度对比疑有笔迹，送下一关'
                    )
                    record.update({
                        'score': 0.0,
                        'max_score': float(((task or {}).get('part') or {}).get('score') or record.get('max_score') or 1.0),
                        'auto_grade_match': False,
                        'ocr_auto_need_manual': True,
                        'ocr_auto_reason': reason_text,
                        'auto_grade_reason': reason_text,
                        'auto_grade_updated_at': datetime.now().isoformat(timespec='seconds'),
                        'label': ((task or {}).get('part') or {}).get('label') or record.get('label', ''),
                        'student_name': ((task or {}).get('entry') or {}).get('student_name') or record.get('student_name', ''),
                        'score_id': ((task or {}).get('entry') or {}).get('score_id') or record.get('score_id', ''),
                        'part_id': ((task or {}).get('part') or {}).get('part_id') or record.get('part_id', ''),
                        'file': ((task or {}).get('entry') or {}).get('file') or record.get('file', ''),
                    })
                    return 'manual'
            except Exception:
                pass

        # 若无裁图文件或检测异常，绝不武断判0分，保底送人工下一关
        for key in (
            'score', 'max_score', 'auto_graded', 'auto_grade_match',
            'auto_grade_expected', 'auto_grade_answer',
            'auto_grade_reason', 'auto_grade_updated_at', 'auto_grade_engine'
        ):
            record.pop(key, None)
        record.update({
            'score': 0.0,
            'max_score': float(((task or {}).get('part') or {}).get('score') or record.get('max_score') or 1.0),
            'auto_grade_match': False,
            'ocr_auto_need_manual': True,
            'ocr_auto_reason': '两层OCR未识别到文字，待人工复核',
            'auto_grade_reason': '两层OCR未识别到文字，待人工复核',
            'auto_grade_updated_at': datetime.now().isoformat(timespec='seconds'),
            'label': ((task or {}).get('part') or {}).get('label') or record.get('label', ''),
            'student_name': ((task or {}).get('entry') or {}).get('student_name') or record.get('student_name', ''),
            'score_id': ((task or {}).get('entry') or {}).get('score_id') or record.get('score_id', ''),
            'part_id': ((task or {}).get('part') or {}).get('part_id') or record.get('part_id', ''),
            'file': ((task or {}).get('entry') or {}).get('file') or record.get('file', ''),
        })
        return 'manual'

    def normalize_scientific_notation_text(self, text):
        text = unicodedata.normalize('NFKC', str(text or '')).strip().lower()
        if not text:
            return ''
        superscript_map = str.maketrans({
            '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
            '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9',
            '⁻': '-', '⁺': '+',
        })
        text = text.translate(superscript_map)
        text = text.replace('＊', '*').replace('×', 'x').replace('X', 'x')
        text = re.sub(r'\s+', '', text)
        text = re.sub(r'乘以?', 'x', text)
        text = re.sub(r'的([+-]?\d+)次方', r'^\1', text)
        text = re.sub(r'10\^?([+-]?\d+)', r'10^\1', text)
        text = re.sub(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*[x*]\s*10\^?([+-]?\d+)', r'\1x10^\2', text)
        text = re.sub(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+))10\^([+-]?\d+)', r'\1x10^\2', text)
        return text

    def normalize_subjective_answer_for_match(self, text):
        text = unicodedata.normalize('NFKC', str(text or '')).strip().lower()
        text = self.normalize_scientific_notation_text(text)
        return re.sub(r'[\s，。！？、;；:：()（）\[\]【】{}<>《》"\'`~·_]+', '', text)

    def check_subjective_split_char_missing(self, actual_text, expected_answers):
        """
        拆字判错逻辑：
        只有当学生的作答文本中，完全没有出现标准答案中的任何一个字（完全写了别的字）时，才判定为错误（0分）。
        如果学生识别到了部分字（例如标准答案为“静止”，只识别到了“静”或“止”），则不能直接判错（可能是学生某个字没写好，需保留给人工复核）。
        """
        if not expected_answers:
            return False, set()
        actual_norm = self.normalize_subjective_answer_for_match(actual_text)
        if not actual_norm or actual_norm == '未识别到文字':
            return False, set()
        actual_chars = set(actual_norm)

        all_expected_chars = set()
        for expected in expected_answers:
            expected_norm = self.normalize_subjective_answer_for_match(expected)
            if expected_norm:
                all_expected_chars.update(expected_norm)

        if not all_expected_chars:
            return False, set()

        # 如果学生识别到了标准答案中的任何一个字，不判错（可能是某个字没写好，留给复核/人审）
        overlap = actual_chars & all_expected_chars
        if overlap:
            return False, set()

        # 完全没有出现标准答案中的任何一个字（完全写了别的字），才判定为错误
        return True, all_expected_chars

    def classify_ocr_text_for_auto_score(self, task, text, score=None, is_retry=False):
        ocr_config = task['part'].get('ocr') or {} if isinstance(task, dict) else {}
        expected_answers = self.subjective_expected_answers_from_config(ocr_config)
        wrong_answers = self.subjective_wrong_answers_from_config(ocr_config)
        text = re.sub(r'\s+', ' ', str(text or '')).strip() or '未识别到文字'
        if text == '未识别到文字':
            crop_path = task.get('crop_path') if isinstance(task, dict) else None
            if crop_path and Path(crop_path).exists():
                try:
                    from core.blank_recheck import evaluate_crop_blank_status, get_blank_recheck_collector
                    blank_status, features, blank_reason = evaluate_crop_blank_status(crop_path)
                    collector = get_blank_recheck_collector()
                    collector.record_sample(
                        crop_path=crop_path,
                        system_judgment=blank_status,
                        features=features,
                        layer1_text='',
                        layer1_score=score,
                        session_id=getattr(self, 'current_session', {}).get('id', '') if isinstance(getattr(self, 'current_session', None), dict) else '',
                        student_name=(task.get('entry') or {}).get('student_name', ''),
                        score_id=(task.get('entry') or {}).get('score_id', ''),
                        part_id=(task.get('part') or {}).get('part_id', ''),
                        label=(task.get('part') or {}).get('label', ''),
                        entry_key=task.get('entry_key', ''),
                    )
                    if is_retry:
                        if blank_status == 'has_handwriting':
                            return 'manual', '两层OCR未识别文字，但灰度对比检测到有字，送下一关', text
                        elif blank_status == 'blank':
                            return 'zero', '两次OCR均未识别到文字，空白复核确认无笔迹，按空白判0分', text
                        else:
                            return 'manual', '两层OCR未识别文字，灰度对比疑有笔迹，送下一关', text
                    else:
                        if blank_status == 'blank':
                            return 'zero', '空白复核确认无笔迹，按空白判0分', text
                        elif blank_status == 'has_handwriting':
                            return 'retry', '空白复核检测到笔迹，送入高精度复核', text
                        else:
                            return 'manual', '空白复核疑有笔迹，需人工确认', text
                except Exception:
                    pass
            if is_retry:
                return 'manual', '两层OCR未识别到文字，待人工复核', text
            return 'zero', '未识别到文字，按空白判0分', text

        if self.any_subjective_answer_matches(text, expected_answers):
            return 'correct', '', text
        if self.any_subjective_answer_matches(text, wrong_answers):
            return 'zero', '命中常见错答案', text
        if self.subjective_wrong_keyword_matches(text, ocr_config):
            return 'zero', '命中错误关键词', text
        remembered_action = self.lookup_ocr_result_classification(ocr_config, text)
        if remembered_action in ('alternate_answer', 'correct_keyword'):
            return 'correct', '命中分类记忆', text
        if remembered_action in ('wrong_answer', 'wrong_keyword'):
            return 'zero', '命中分类记忆中的错误答案', text
        if ocr_config.get('split_char_check', True) and expected_answers:
            is_missing, missing_chars = self.check_subjective_split_char_missing(text, expected_answers)
            if is_missing:
                expected_str = ' / '.join(expected_answers)
                return 'zero', f'拆字判错：完全未命中答案字（“{expected_str}”）', text
        if self.subjective_keyword_matches(text, ocr_config):
            return 'correct', '命中关键词', text
        need_review, review_reason = self.evaluate_paddle_ocr_review(text, score, ocr_config)
        if need_review:
            return 'retry', review_reason or '低置信度', text
        return 'retry', 'OCR结果与标准答案不一致', text

    def run_subjective_two_stage_ocr_automation(self, tasks, status_callback=None):
        payload = self.load_subjective_score_payload()
        payload = self.prepare_subjective_payload_for_grading(payload)
        all_scores = payload.setdefault('scores', {})
        target_tasks = []
        for task in tasks:
            ocr_config = task['part'].get('ocr') or {}
            if not ocr_config.get('enabled'):
                continue
            record = all_scores.get(task['entry_key'], {}).get(task['part_id'], {})
            if isinstance(record, dict) and record.get('manual_graded'):
                continue
            target_tasks.append(task)
        if not target_tasks:
            return {'auto': 0, 'manual': 0, 'skipped': len(tasks), 'ocr_targets': 0}

        def update_status(text):
            if callable(status_callback):
                status_callback(text)
            elif hasattr(self, 'status_var'):
                self.status_var.set(text)
                try:
                    self.root.update_idletasks()
                except Exception:
                    pass

        update_status(f'OCR自动判分：PaddleOCR 处理中 0/{len(target_tasks)}')
        try:
            paddle_results = self.run_paddle_ocr_for_crops(
                [task['crop_path'] for task in target_tasks],
                [task['part'].get('ocr') for task in target_tasks],
            )
        except Exception as e:
            paddle_results = [{'error': str(e)} for _task in target_tasks]

        updates = []
        retry_tasks = []
        for task, result in zip(target_tasks, paddle_results):
            if result.get('error'):
                retry_tasks.append((task, {'text': '', 'score': None, 'error': result.get('error')}, f"Paddle失败：{result.get('error')}"))
                continue
            text = re.sub(r'\s+', ' ', result.get('text', '')).strip() or '未识别到文字'
            score = result.get('score')
            action, reason, normalized_text = self.classify_ocr_text_for_auto_score(task, text, score)
            need_review, review_reason = self.evaluate_paddle_ocr_review(text, score, task['part'].get('ocr'))
            base = {
                'task': task,
                'paddle_text': text,
                'paddle_score': score,
                'paddle_need_review': need_review,
                'paddle_reason': review_reason,
            }
            if action == 'correct':
                updates.append({**base, 'engine': 'PaddleOCR', 'auto_text': normalized_text})
            elif action == 'zero':
                updates.append({**base, 'engine': 'PaddleOCR', 'auto_zero_text': normalized_text, 'zero_reason': reason})
            elif action == 'manual':
                updates.append({**base, 'engine': 'PaddleOCR', 'manual_reason': reason})
            else:
                retry_tasks.append((task, {**result, 'text': text, 'score': score, 'need_review': need_review, 'review_reason': review_reason}, reason))

        if retry_tasks:
            update_status(f'OCR自动判分：Paddle已处理 {len(updates)} 个，PaddleOCRv6复核 {len(retry_tasks)} 个')
        try:
            retry_results = self.run_paddle_ocr_for_crops(
                [task['crop_path'] for task, _paddle_result, _reason in retry_tasks],
                [task['part'].get('ocr') for task, _paddle_result, _reason in retry_tasks],
                profile='v6_medium',
            ) if retry_tasks else []
        except Exception as e:
            retry_results = [{'error': str(e)} for _task, _paddle_result, _reason in retry_tasks]

        for (task, paddle_result, reason), retry_result in zip(retry_tasks, retry_results):
            error = retry_result.get('error')
            retry_text = ''
            had_handwriting = ('空白复核检测到笔迹' in str(reason or ''))
            if not error:
                retry_text = re.sub(r'\s+', ' ', retry_result.get('text', '')).strip() or '未识别到文字'
                action, retry_reason, normalized_text = self.classify_ocr_text_for_auto_score(
                    task, retry_text, retry_result.get('score'), is_retry=True
                )
                first_text = re.sub(r'\s+', ' ', str(paddle_result.get('text') or '')).strip()
                both_ocr_blank = (
                    not paddle_result.get('error')
                    and first_text in ('', '未识别到文字')
                    and retry_text == '未识别到文字'
                    and not had_handwriting
                )
                if had_handwriting and task.get('crop_path'):
                    try:
                        from core.blank_recheck import get_blank_recheck_collector
                        get_blank_recheck_collector().update_layer2_result(
                            task['crop_path'], retry_text, retry_result.get('score')
                        )
                    except Exception:
                        pass
            else:
                action, retry_reason, normalized_text = 'manual', f'{reason}；PaddleOCRv6识别失败：{error}', ''
                both_ocr_blank = False
            base = {
                'task': task,
                'paddle_text': paddle_result.get('text', ''),
                'paddle_score': paddle_result.get('score'),
                'paddle_need_review': bool(paddle_result.get('need_review')),
                'paddle_reason': paddle_result.get('review_reason', ''),
                'dots_text': retry_text or (f'识别失败：{error}' if error else ''),
                'secondary_ocr_engine': 'PaddleOCRv6',
                'secondary_ocr_score': retry_result.get('score'),
            }
            if action == 'correct':
                updates.append({**base, 'engine': 'PaddleOCRv6', 'auto_text': normalized_text})
            elif action == 'zero':
                updates.append({
                    **base,
                    'engine': 'PaddleOCRv6',
                    'auto_zero_text': normalized_text,
                    'zero_reason': retry_reason if (retry_reason.startswith('未识别到文字') or retry_reason.startswith('拆字判错') or retry_reason.startswith('两次OCR')) else f'PaddleOCRv6{retry_reason}',
                })
            elif both_ocr_blank:
                updates.append({
                    **base,
                    'engine': 'PaddleOCRv6',
                    'auto_zero_text': '未识别到文字',
                    'zero_reason': '两次OCR均未识别到文字，按空白判0分',
                })
            else:
                manual_reason = retry_reason or reason or 'OCR结果与标准答案不一致'
                updates.append({**base, 'manual_reason': manual_reason})

        auto_count = 0
        manual_count = 0
        skipped_manual = 0
        for update in updates:
            task = update['task']
            entry_scores = all_scores.setdefault(task['entry_key'], {})
            record = entry_scores.setdefault(task['part_id'], {})
            if record.get('manual_graded'):
                skipped_manual += 1
                continue
            record.pop('ocr_manual_text', None)
            record.pop('ocr_manual_updated_at', None)
            record.update({
                'paddle_ocr_text': update.get('paddle_text', ''),
                'paddle_ocr_score': update.get('paddle_score'),
                'paddle_ocr_need_review': bool(update.get('paddle_need_review')),
                'paddle_ocr_review_reason': update.get('paddle_reason', ''),
                'paddle_ocr_updated_at': datetime.now().isoformat(timespec='seconds'),
            })
            if update.get('dots_text') is not None:
                record.update({
                    'ocr_text': update.get('dots_text', ''),
                    'ocr_engine': update.get('secondary_ocr_engine') or update.get('engine') or 'OCR',
                    'ocr_score': update.get('secondary_ocr_score'),
                    'ocr_updated_at': datetime.now().isoformat(timespec='seconds'),
                })
            if update.get('engine') and update.get('auto_text'):
                matched = self.apply_subjective_answer_auto_score(record, task, update['auto_text'])
                if matched:
                    record.update({
                        'auto_grade_engine': update.get('engine'),
                        'ocr_auto_need_manual': False,
                        'ocr_auto_reason': '',
                    })
                    auto_count += 1
                else:
                    self.mark_subjective_need_manual_record(record, task, 'OCR结果与标准答案不一致')
                    manual_count += 1
            elif update.get('auto_zero_text'):
                self.mark_subjective_auto_zero_record(
                    record,
                    task,
                    update.get('auto_zero_text', ''),
                    update.get('engine') or 'OCR',
                    update.get('zero_reason') or 'OCR判断为错误',
                )
                auto_count += 1
            else:
                self.mark_subjective_need_manual_record(record, task, update.get('manual_reason') or 'OCR结果与标准答案不一致')
                manual_count += 1

        self.save_subjective_score_payload(payload)
        self.apply_subjective_scores_to_entries(payload)
        return {
            'auto': auto_count,
            'manual': manual_count,
            'skipped': skipped_manual,
            'ocr_targets': len(target_tasks),
        }

    def refresh_result_views_after_subjective_update(self):
        if hasattr(self, 'detail_text'):
            self.detail_text.delete(1.0, tk.END)
            for entry in self.summary_data:
                self.display_single_result(entry)
        self.update_summary_display()
        self.update_analysis_display()
        self.save_results_to_database()

    def auto_ocr_and_upload_manual_web_tasks(self):
        if not self.require_current_session_for_grading('OCR并上传人工项'):
            return
        if not self.begin_automation_run('一键OCR并上传'):
            return
        try:
            if self.is_direct_paper_choice_template() and self.image_paths:
                # A previous run may have been started before template-page
                # filtering existed. Remove the detected template pair from both the pending list
                # and any stale in-memory results before OCR/upload begins.
                pair = self.find_direct_paper_structure_template_pair(self.selected_folder_path)
                template_paths = {
                    Path(path).resolve()
                    for path in (pair.get('front'), pair.get('back'))
                    if path
                }
                if template_paths:
                    template_names = {path.name for path in template_paths}
                    self.image_paths = [
                        path for path in self.image_paths
                        if Path(path).resolve() not in template_paths
                    ]
                    self.summary_data = [
                        entry for entry in self.summary_data
                        if str(entry.get('file') or '') not in template_names
                    ]
            if not self.summary_data:
                if not self.image_paths:
                    messagebox.showwarning('提示', '请先选择图片或文件夹。')
                    return
                self.status_var.set('一键流程：正在先批改选择题...')
                self.root.update_idletasks()
                self.process_all()
                if not self.summary_data:
                    return
            self.ensure_answer_scheme_loaded_for_grading()
            if not self.confirm_answer_scheme_matches_current_session():
                return
            self.status_var.set('一键流程：正在生成主观题裁图...')
            self.root.update_idletasks()
            tasks = self.build_subjective_task_list_for_automation(reuse_existing=True)
            stats = self.run_subjective_two_stage_ocr_automation(
                tasks,
                status_callback=lambda text: (self.status_var.set('一键流程：' + text), self.root.update_idletasks()),
            )
            self.refresh_result_views_after_subjective_update()
            self.status_var.set('一键流程：正在上传待人工批改部分到VPS...')
            self.root.update_idletasks()
            try:
                package_path, manifest = self.build_manual_web_task_package()
            except ValueError as package_error:
                if '没有需要上传' in str(package_error) or '没有需要' in str(package_error):
                    messagebox.showinfo(
                        '一键流程完成',
                        f"OCR已完成，当前没有需要上传到网页人工批改的空位。\n\n"
                        f"OCR处理：{stats.get('ocr_targets', 0)} 个空\n"
                        f"自动判分：{stats.get('auto', 0)} 个",
                    )
                    self.status_var.set('一键流程完成：没有待上传人工项')
                    return
                raise
            with package_path.open('rb') as fh:
                result = self.manual_web_request('POST', '/api/upload', body=fh.read(), content_type='application/zip')
            config = self.load_manual_web_sync_config()
            index_url = str(config.get('teacher_url') or config.get('server_url') or '').rstrip('/')
            job_url = result.get('job_url') or ''
            msg = (
                f"一键流程完成。\n\n"
                f"选择题：已生成学生结果\n"
                f"OCR处理：{stats.get('ocr_targets', 0)} 个空\n"
                f"自动判分：{stats.get('auto', 0)} 个\n"
                f"需要人工：{stats.get('manual', 0)} 个\n"
                f"已上传人工任务：{len(manifest.get('tasks') or [])} 个\n\n"
                f"网页登录入口：{index_url or job_url}\n"
                f"当前任务：{job_url}"
            )
            self.show_copyable_text_window('一键OCR并上传完成', msg)
            self.status_var.set(f"一键流程完成：已上传 {len(manifest.get('tasks') or [])} 个待人工空位")
        except urllib.error.URLError as e:
            messagebox.showerror('一键流程失败', f'无法连接网页人工打分服务。\n\n{e}')
        except Exception as e:
            messagebox.showerror('一键流程失败', str(e))
        finally:
            self.end_automation_run()

    def upload_manual_web_tasks(self):
        if not self.require_current_session_for_grading('上传网页人工打分'):
            return
        try:
            package_path, manifest = self.build_manual_web_task_package()
            with package_path.open('rb') as fh:
                result = self.manual_web_request('POST', '/api/upload', body=fh.read(), content_type='application/zip')
            url = result.get('job_url') or ''
            msg = f"已上传网页人工打分任务：{manifest.get('job_name')}\n任务数：{len(manifest.get('tasks') or [])}"
            if url:
                msg += f"\n\n网页地址：{url}"
            self.show_copyable_text_window('上传完成：网页人工打分地址', msg)
            self.status_var.set(f"网页人工打分任务已上传：{len(manifest.get('tasks') or [])} 个空位")
        except urllib.error.URLError as e:
            messagebox.showerror('上传失败', f'无法连接网页人工打分服务。\n\n{e}')
        except Exception as e:
            messagebox.showerror('上传失败', str(e))

    def download_manual_web_scores(self):
        if not self.require_current_session_for_grading('获取网页打分'):
            return
        try:
            job_id = ''
            remote_scores = {}
            checked_job_ids = self.manual_web_job_candidates()
            last_error = None
            for candidate_job_id in checked_job_ids:
                try:
                    result = self.manual_web_request('GET', f'/api/jobs/{candidate_job_id}/scores')
                except urllib.error.HTTPError as error:
                    if error.code in (404, 410):
                        continue
                    last_error = error
                    continue
                candidate_scores = result.get('scores') or {}
                if candidate_scores:
                    if getattr(self, 'current_session', None):
                        remote = self.manual_web_request('GET', f'/api/jobs/{candidate_job_id}')
                        manifest = remote.get('manifest') or {}
                        if (manifest.get('session_id') != self.current_session.get('id')
                                or manifest.get('grading_context') != self.manual_web_context()):
                            raise ValueError('网页任务的测试、答案、结构或原图版本与当前不一致，请重新上传当前任务。')
                    job_id = candidate_job_id
                    remote_scores = candidate_scores
                    break
            if not remote_scores:
                if last_error:
                    raise last_error
                checked_text = '、'.join(checked_job_ids) or '无可用任务ID'
                messagebox.showinfo('提示', f'VPS 上还没有这个任务的网页打分结果。\n\n已检查：{checked_text}')
                return
            payload = self.load_subjective_score_payload()
            local_scores = payload.setdefault('scores', {})
            score_parts = list(self.iter_subjective_parts())
            score_parts.extend(self.direct_calculation_manual_parts())
            part_map = {part.get('part_id'): part for part in score_parts}
            valid_entries = {self.subjective_entry_key(entry) for entry in self.summary_data}
            # Validate the entire response before changing local scores or deleting
            # the remote job. Otherwise filtering may drop foreign entries silently.
            if not isinstance(remote_scores, dict):
                raise ValueError('网页成绩格式无效，已保留远端任务。')
            for entry_key, entry_scores in remote_scores.items():
                if entry_key not in valid_entries or not isinstance(entry_scores, dict):
                    raise ValueError('网页成绩包含非当前测试的学生，已停止合并并保留远端任务。')
                for part_id, record in entry_scores.items():
                    if part_id not in part_map or not isinstance(record, dict):
                        raise ValueError('网页成绩空位与当前结构不一致，已保留远端任务。')
                    value = record.get('score')
                    maximum = float(part_map[part_id].get('score') or 0)
                    if (value is None or isinstance(value, bool) or not math.isfinite(float(value))
                            or not math.isfinite(maximum) or not 0 <= float(value) <= maximum):
                        raise ValueError('网页成绩分数无效或超过当前满分，已保留远端任务。')
            if hasattr(self, 'prepare_subjective_payload_for_grading'):
                payload = self.prepare_subjective_payload_for_grading(payload)
                local_scores = payload.setdefault('scores', {})
            self.bind_manual_web_job_to_current_session(job_id)
            merged = 0
            for entry_key, entry_scores in remote_scores.items():
                if not isinstance(entry_scores, dict):
                    continue
                target_entry = local_scores.setdefault(entry_key, {})
                for part_id, record in entry_scores.items():
                    if not isinstance(record, dict) or record.get('score') is None:
                        continue
                    part = part_map.get(part_id, {})
                    target_entry[part_id] = {
                        'score': float(record.get('score') or 0),
                        'max_score': float(part.get('score') or 0),
                        'manual_graded': True,
                        'auto_graded': False,
                        'label': record.get('label') or part.get('label', ''),
                        'kind': part.get('kind', ''),
                        'score_category': part.get('score_category') or part.get('kind') or '',
                        'student_name': record.get('student_name', ''),
                        'score_id': record.get('score_id', ''),
                        'file': record.get('file', ''),
                        'source': 'manual_web',
                        'updated_at': record.get('updated_at') or datetime.now().isoformat(timespec='seconds'),
                    }
                    merged += 1
            self.save_subjective_score_payload(payload)
            self.apply_subjective_scores_to_entries(payload)
            self.detail_text.delete(1.0, tk.END)
            for entry in self.summary_data:
                self.display_single_result(entry)
            self.update_summary_display()
            self.update_analysis_display()
            self.save_results_to_database()
            delete_msg = ''
            try:
                self.manual_web_request('POST', f'/api/jobs/{job_id}/delete')
                delete_msg = '\n\nVPS 上这个任务的数据也已经删除。'
            except Exception as delete_error:
                delete_msg = f'\n\n但删除 VPS 任务数据时失败：{delete_error}'
            messagebox.showinfo('获取完成', f'已从 VPS 获取并合并网页打分：{merged} 个空位。{delete_msg}')
            self.status_var.set(f'已获取网页打分：{merged} 个空位，并清理 VPS 任务')
        except urllib.error.URLError as e:
            messagebox.showerror('获取失败', f'无法连接网页人工打分服务。\n\n{e}')
        except Exception as e:
            messagebox.showerror('获取失败', str(e))

    def delete_current_manual_web_job(self):
        if not self.require_current_session_for_grading('删除网页人工任务'):
            return
        job_id = self.manual_web_job_id()
        job_name = self.manual_web_job_name()
        if not messagebox.askyesno(
            '确认删除',
            f'确定删除 VPS 上当前网页人工批改任务吗？\n\n任务：{job_name}\n\n这只删除网页端上传内容，不会删除本地批改进度。',
            parent=self.root,
        ):
            return
        try:
            result = self.manual_web_request('POST', f'/api/jobs/{job_id}/delete')
            if not result.get('ok', True):
                raise RuntimeError(result.get('error') or '删除失败')
            messagebox.showinfo('删除完成', 'VPS 上当前网页人工批改任务已经删除。', parent=self.root)
            self.status_var.set('已删除当前网页人工批改任务')
        except urllib.error.URLError as e:
            messagebox.showerror('删除失败', f'无法连接网页人工批改服务。\n\n{e}', parent=self.root)
        except Exception as e:
            messagebox.showerror('删除失败', str(e), parent=self.root)

    def upload_lecture_to_vps(self, html_path, xlsx_path=None):
        """上传试卷讲评课件（HTML及可选Excel数据包）到 VPS 讲评工作台"""
        config = self.load_manual_web_sync_config()
        server_url = str(config.get('server_url') or '').rstrip('/')
        token = str(config.get('api_token') or '')
        if not server_url:
            return False, '未配置 VPS 服务器地址'
        if not token:
            return False, '未配置 API Token'

        html_p = Path(html_path)
        if not html_p.exists():
            return False, f'HTML课件文件不存在: {html_p}'

        # A filename is not a test identity. Different classes routinely export
        # the same paper name; use a stable session namespace on the server.
        owner = str((self.current_session or {}).get('id') or '')
        if not owner:
            return False, '请先将课件绑定到测试后上传，避免同名课件覆盖其他班级'
        prefix = hashlib.sha256(owner.encode('utf-8')).hexdigest()[:16]

        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # 1. 上传 HTML 课件
        try:
            req_html = urllib.request.Request(
                f'{server_url}/api/lectures/upload-binary',
                data=html_p.read_bytes(),
                headers={
                    'X-Manual-Grading-Token': token,
                    'X-File-Name': urllib.parse.quote(f'{prefix}_{html_p.name}'),
                    'Content-Type': 'application/octet-stream',
                },
                method='POST',
            )
            with urllib.request.urlopen(req_html, context=ctx, timeout=30) as resp:
                raw = resp.read()
                res_html = json.loads(raw.decode('utf-8-sig', errors='replace'))
                if not res_html.get('ok'):
                    return False, res_html.get('error', '上传HTML课件失败')
        except Exception as e:
            return False, f'上传HTML至VPS失败: {e}'

        # 2. 上传配套 Excel 数据包（如果存在）
        if xlsx_path:
            xlsx_p = Path(xlsx_path)
            if xlsx_p.exists():
                try:
                    req_xlsx = urllib.request.Request(
                        f'{server_url}/api/lectures/upload-binary',
                        data=xlsx_p.read_bytes(),
                        headers={
                            'X-Manual-Grading-Token': token,
                            'X-File-Name': urllib.parse.quote(f'{prefix}_{xlsx_p.name}'),
                            'Content-Type': 'application/octet-stream',
                        },
                        method='POST',
                    )
                    with urllib.request.urlopen(req_xlsx, context=ctx, timeout=30) as resp:
                        result = json.loads(resp.read().decode('utf-8-sig', errors='replace'))
                        if not result.get('ok'):
                            return False, result.get('error', 'Excel数据包上传失败')
                except Exception as exc:
                    return False, f'HTML已上传，但Excel数据包上传失败：{exc}'

        target_url = config.get('teacher_url') or server_url
        return True, target_url

    def upload_selected_lecture_to_vps_dialog(self):
        """手动选择本地已有讲评课件上传至 VPS 工作台"""
        from tkinter import filedialog
        initial_dir = str(
            getattr(self, 'selected_folder_path', None)
            or getattr(self, 'last_open_dir', None)
            or Path.cwd()
        )
        file_path = filedialog.askopenfilename(
            parent=getattr(self, 'root', None),
            title="选择要上传到 VPS 的试卷讲评课件 (HTML)",
            initialdir=initial_dir,
            filetypes=[("HTML讲评课件", "*.html"), ("所有文件", "*.*")],
        )
        if not file_path:
            return

        html_p = Path(file_path)
        xlsx_name = html_p.stem.replace('【讲评课件】', '【讲评数据包】') + '.xlsx'
        xlsx_p = html_p.parent / xlsx_name
        xlsx_arg = xlsx_p if xlsx_p.exists() else None

        if hasattr(self, 'status_var'):
            self.status_var.set(f'正在上传课件 {html_p.name} 至 VPS...')
        if hasattr(self, 'root'):
            self.root.update_idletasks()

        ok, res = self.upload_lecture_to_vps(html_p, xlsx_arg)
        if ok:
            if hasattr(self, 'status_var'):
                self.status_var.set(f'讲评课件已成功上传至 VPS: {html_p.name}')
            msg = (
                f"✅ 试卷讲评课件已成功上传至 VPS 教学讲评工作台！\n\n"
                f"课件文件：{html_p.name}\n"
                f"{'数据包：' + xlsx_p.name if xlsx_arg else ''}\n"
                f"在线访问地址：{res}\n\n"
                f"是否立即在浏览器中打开 VPS 讲评工作台？"
            )
            if messagebox.askyesno("上传成功", msg, parent=getattr(self, 'root', None)):
                import webbrowser
                webbrowser.open(res)
        else:
            if hasattr(self, 'status_var'):
                self.status_var.set(f'上传 VPS 失败: {res}')
            messagebox.showerror("上传失败", f"上传讲评课件至 VPS 失败：\n\n{res}", parent=getattr(self, 'root', None))

    def open_vps_lecture_portal_in_browser(self):
        """在默认浏览器中打开 VPS 教学讲评工作台"""
        config = self.load_manual_web_sync_config()
        url = config.get('teacher_url') or config.get('server_url') or 'https://your-manual-grading-server.example.com'
        import webbrowser
        webbrowser.open(url)

