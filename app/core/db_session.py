from __future__ import annotations

from collections import Counter
import copy
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
import uuid

from core.persistence import atomic_write_json

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = APP_DIR
DB_PATH = PROJECT_DIR / 'answer_card_app.db'
SESSION_ANSWER_KEYS_DIR = PROJECT_DIR / 'session_answer_keys'
SESSION_TEMPLATE_CONFIGS_DIR = PROJECT_DIR / 'session_template_configs'
SUPPORTED_IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tif', '.tiff'}


class DatabaseSessionMixin:
    """Mixin class providing SQLite database initialization, session record management,
    folder binding, and JSON session snapshot operations."""

    def init_database(self):
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute(
                '''
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    template_key TEXT,
                    template_name TEXT,
                    created_at TEXT NOT NULL,
                    roster_name TEXT,
                    note TEXT,
                    folder_path TEXT,
                    manual_web_job_id TEXT
                )
                '''
            )
            conn.execute(
                '''
                CREATE TABLE IF NOT EXISTS session_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    score_id TEXT,
                    student_name TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, file_name),
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                )
                '''
            )
            existing_columns = {
                row[1] for row in conn.execute('PRAGMA table_info(sessions)').fetchall()
            }
            if 'folder_path' not in existing_columns:
                conn.execute('ALTER TABLE sessions ADD COLUMN folder_path TEXT')
            if 'manual_web_job_id' not in existing_columns:
                conn.execute('ALTER TABLE sessions ADD COLUMN manual_web_job_id TEXT')
            conn.commit()
        finally:
            conn.close()

    def update_session_hint(self):
        if self.current_session:
            self.session_hint_var.set(f"当前绑定：{self.current_session['kind_label']}《{self.current_session['name']}》")
        else:
            self.session_hint_var.set('当前未绑定测试/调查')

    def require_current_session_for_grading(self, action='批改'):
        if self.current_session and self.current_session.get('id'):
            session_folder = self.normalize_folder_path(self.current_session.get('folder_path'))
            active_folder = self.normalize_folder_path(self.current_folder_context())
            if session_folder and active_folder and session_folder != active_folder:
                self.status_var.set(f'已阻止{action}：当前文件夹与测试绑定文件夹不一致')
                messagebox.showerror(
                    '测试与文件夹不一致',
                    f'当前测试：{self.current_session.get("name", "未命名")}\n'
                    f'测试绑定：{self.current_session.get("folder_path", "")}\n'
                    f'当前文件夹：{self.current_folder_context()}\n\n'
                    '为防止不同班级成绩串在一起，本次操作已停止。请重新打开当前文件夹，程序会自动加载或创建正确测试。',
                    parent=self.root,
                )
                return False
            return True
        self.status_var.set(f'尚未创建或打开测试，不能{action}')
        messagebox.showwarning(
            '请先创建测试',
            f'为避免改卷进度或网页人工成绩与其他班级混在一起，{action}前必须先创建或打开测试。\n\n'
            '请先打开试卷文件夹，程序会自动创建新测试；如果该文件夹已有测试，则会自动加载。',
            parent=self.root,
        )
        return False

    def normalize_paper_identity_name(self, value, use_last_path_part=False):
        text = unicodedata.normalize('NFKC', str(value or '')).strip().lower()
        if use_last_path_part:
            parts = [part.strip() for part in re.split(r'[\\/]+', text) if part.strip()]
            if parts:
                text = parts[-1]
        text = re.sub(r'\.(json|docx?|xlsx?|csv)$', '', text, flags=re.IGNORECASE)
        return re.sub(r'[^0-9a-z\u3400-\u9fff]+', '', text)

    def confirm_answer_scheme_matches_current_session(self):
        if self.current_template_mode != 'grader' or not self.has_standard_answers():
            return True
        session_name = str((self.current_session or {}).get('name') or '').strip()
        scheme_name = str(getattr(self, 'loaded_answer_key_scheme_name', '') or '').strip()
        if not scheme_name:
            scheme_path = getattr(self, 'loaded_answer_key_scheme_path', '') or ''
            if scheme_path:
                scheme_name = Path(scheme_path).stem

        test_key = self.normalize_paper_identity_name(session_name, use_last_path_part=True)
        scheme_key = self.normalize_paper_identity_name(scheme_name, use_last_path_part=True)
        names_match = bool(
            test_key
            and scheme_key
            and min(len(test_key), len(scheme_key)) >= 2
            and (test_key in scheme_key or scheme_key in test_key)
        )
        if names_match:
            return True

        confirmation_key = (
            str((self.current_session or {}).get('id') or ''),
            scheme_key,
            self.subjective_answer_signature() if self.subjective_items() else '',
        )
        if getattr(self, '_confirmed_answer_scheme_mismatch', None) == confirmation_key:
            return True

        display_scheme = scheme_name or '未命名（当前界面答案）'
        proceed = messagebox.askyesno(
            '答案方案可能不匹配',
            '当前测试名称和答案方案名称没有匹配到同一试卷关键词。\n\n'
            f'测试：{session_name or "未命名"}\n'
            f'答案方案：{display_scheme}\n\n'
            '例如测试“24-1\\1-3章”和答案方案“1-3章”会自动匹配。\n'
            '是否确认仍使用当前答案继续批改？',
            parent=self.root,
        )
        if proceed:
            self._confirmed_answer_scheme_mismatch = confirmation_key
            return True
        self.status_var.set('已暂停批改：请加载与当前测试名称对应的答案方案')
        return False

    def list_saved_sessions(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                '''
                SELECT s.id, s.name, s.kind, s.template_key, s.template_name, s.created_at, s.roster_name, s.note, s.folder_path,
                       COUNT(r.id) AS result_count
                FROM sessions s
                LEFT JOIN session_results r ON r.session_id = s.id
                GROUP BY s.id, s.name, s.kind, s.template_key, s.template_name, s.created_at, s.roster_name, s.note, s.folder_path
                ORDER BY s.created_at DESC
                '''
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def normalize_folder_path(self, folder_path):
        if not folder_path:
            return ''
        try:
            return str(Path(folder_path).resolve()).rstrip('\\/').lower()
        except Exception:
            return str(folder_path).strip().rstrip('\\/').lower()

    def collect_image_paths_from_folder(self, folder_path):
        folder = Path(folder_path)
        if not folder.exists():
            return []
        paths = []
        for ext in SUPPORTED_IMAGE_EXTS:
            paths.extend(folder.glob(f'*{ext}'))
            paths.extend(folder.glob(f'*{ext.upper()}'))
        template_pages = set()
        if self.is_direct_paper_choice_template():
            pair = self.find_direct_paper_structure_template_pair(folder)
            template_pages = {
                Path(path).resolve()
                for path in (pair.get('front'), pair.get('back'))
                if path
            }
        return sorted(
            {
                path for path in paths
                if not self.is_generated_image_artifact(path)
                and path.resolve() not in template_pages
            },
            key=self.natural_path_sort_key,
        )

    def is_generated_image_artifact(self, path):
        name = Path(path).name.lower()
        generated_prefixes = (
            'debug_',
            'choice_boxes_',
            'student_id_points_preview',
            'template_marker_detection_debug',
        )
        generated_tokens = (
            '整卡标注',
            '透打标注',
            'marked_answer',
            'answer_under_lines_preview',
            'crop_contact_sheet',
            'options_sheet',
            'overlay_preview',
        )
        return name.startswith(generated_prefixes) or any(token.lower() in name for token in generated_tokens)

    def natural_path_sort_key(self, path):
        name = Path(path).name
        parts = re.split(r'(\d+)', name)
        return [int(part) if part.isdigit() else part.lower() for part in parts]

    def infer_folder_from_entries(self, entries):
        counts = Counter()
        for entry in entries or []:
            input_path = entry.get('input_path') or ''
            if input_path:
                try:
                    parent = Path(input_path).parent
                    if str(parent) and parent.exists():
                        counts[str(parent)] += 1
                except Exception:
                    pass
        if not counts:
            return ''
        return counts.most_common(1)[0][0]

    def current_folder_context(self):
        if getattr(self, 'selected_folder_path', ''):
            return str(self.selected_folder_path)
        if self.image_paths:
            try:
                return str(Path(self.image_paths[0]).parent)
            except Exception:
                pass
        return str(self.last_open_dir or '')

    def set_folder_context(self, folder_path, load_images=True, show_preview=True):
        if not folder_path:
            return
        folder = Path(folder_path)
        self.selected_folder_path = str(folder)
        self.default_export_dir = str(folder)
        self.last_open_dir = str(folder)
        if hasattr(self, 'find_word_paper_in_folder') and hasattr(self, 'bind_wrongbook_paper'):
            try:
                paper = self.find_word_paper_in_folder(folder)
                if paper:
                    self.bind_wrongbook_paper(paper)
            except Exception:
                pass
        if load_images:
            self.image_paths = self.collect_image_paths_from_folder(folder)
            if show_preview:
                self.show_preview_by_index(0 if self.image_paths else -1)
        self.save_ui_settings()

    def select_roster_for_folder(self, folder_path):
        folder = Path(folder_path)
        parent_name = str(folder.parent.name or '').strip()
        if not parent_name:
            return ''
        candidates = [parent_name, f'{parent_name}班']
        roster_names = list((getattr(self, 'rosters', {}) or {}).keys())
        for candidate in candidates:
            if candidate in roster_names:
                self.roster_class_var.set(candidate)
                return candidate

        def normalized(value):
            return re.sub(r'[\s_-]+', '', unicodedata.normalize('NFKC', str(value or '')).lower())

        candidate_keys = {normalized(value) for value in candidates}
        for roster_name in roster_names:
            if normalized(roster_name) in candidate_keys:
                self.roster_class_var.set(roster_name)
                return roster_name
        return ''

    def update_session_folder_path(self, session_id=None, folder_path=None):
        session_id = session_id or (self.current_session or {}).get('id')
        folder_path = folder_path or self.current_folder_context()
        if not session_id or not folder_path:
            return
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute('UPDATE sessions SET folder_path = ? WHERE id = ?', (str(folder_path), session_id))
            conn.commit()
        finally:
            conn.close()

    def find_saved_session_for_folder(self, folder_path):
        target = self.normalize_folder_path(folder_path)
        if not target:
            return None
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                'SELECT * FROM sessions ORDER BY created_at DESC'
            ).fetchall()
            matches = [dict(row) for row in rows
                       if self.normalize_folder_path(row['folder_path']) == target]
            if len(matches) > 1:
                raise ValueError('该文件夹关联多个测试，请从“打开测试”明确选择本次测试，不能自动猜选。')
            if matches:
                return matches[0]

            result_rows = conn.execute(
                '''
                SELECT s.*, r.payload_json
                FROM sessions s
                JOIN session_results r ON r.session_id = s.id
                ORDER BY s.created_at DESC
                '''
            ).fetchall()
        finally:
            conn.close()

        for row in result_rows:
            # A result left in an old folder must never rebind an existing test.
            if row['folder_path']:
                continue
            session_id = row['id']
            try:
                payload = json.loads(row['payload_json'])
                input_path = payload.get('input_path') or ''
                parent = Path(input_path).parent if input_path else None
                if parent and self.normalize_folder_path(parent) == target:
                    session = {key: row[key] for key in row.keys() if key != 'payload_json'}
                    self.update_session_folder_path(session_id, folder_path)
                    session['folder_path'] = str(folder_path)
                    return session
            except Exception:
                pass
        return None

    def auto_load_session_for_folder(self, folder_path):
        try:
            session = self.find_saved_session_for_folder(folder_path)
        except ValueError as exc:
            messagebox.showwarning('需要选择测试', str(exc))
            return True
        if not session:
            return False
        # A saved test owns its answer scheme. Always restore that snapshot so
        # switching folders cannot leave the previous class's answers active.
        self.load_session_results(session['id'], load_answer_snapshot=True)
        self.status_var.set(f"已选择文件夹，并自动加载测试/调查《{session.get('name', '')}》")
        return True

    def default_session_name_for_folder(self, folder_path):
        folder = Path(folder_path)
        parent_name = str(folder.parent.name or '').strip()
        folder_name = str(folder.name or '').strip() or datetime.now().strftime('%Y%m%d-%H%M%S')
        return f'{parent_name}\\{folder_name}' if parent_name else folder_name

    def create_session_for_folder(self, folder_path):
        folder = Path(folder_path)
        if not folder.exists() or not folder.is_dir():
            raise ValueError('选择的试卷文件夹不存在。')
        session_id = str(uuid.uuid4())
        created_at = datetime.now().isoformat(timespec='seconds')
        kind_label = '调查' if self.current_template_mode == 'collector' else '测试'
        kind = 'collector' if kind_label == '调查' else 'grader'
        name = self.default_session_name_for_folder(folder)
        linked_manual_job_id = ''
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute(
                'INSERT INTO sessions '
                '(id, name, kind, template_key, template_name, created_at, roster_name, note, folder_path, manual_web_job_id) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    session_id,
                    name,
                    kind,
                    self.current_template_key,
                    self.current_template_name,
                    created_at,
                    self.roster_class_var.get(),
                    '打开文件夹时自动创建',
                    str(folder),
                    linked_manual_job_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        self.current_session = {
            'id': session_id,
            'name': name,
            'kind': kind,
            'kind_label': kind_label,
            'created_at': created_at,
            'folder_path': str(folder),
            'manual_web_job_id': linked_manual_job_id,
        }
        # Only explicitly selected working state is saved; folder selection
        # clears the previous test's answers before creating this record.
        self.save_session_template_config_snapshot(session_id)
        self.save_session_answer_key_snapshot(session_id)
        self.update_session_hint()
        self.status_var.set(f'已选择文件夹，并自动创建{kind_label}《{name}》')
        self._user_explicitly_cleared = False
        try:
            self.save_ui_settings()
        except Exception:
            pass
        return self.current_session

    def load_session_results(self, session_id, load_answer_snapshot=True):
        if hasattr(self, 'context_change_allowed') and not self.context_change_allowed():
            return False
        preserved_answer_state = None
        if (not load_answer_snapshot and
                str((self.current_session or {}).get('id')) != str(session_id)):
            raise ValueError('跨测试打开必须恢复该测试自己的答案快照。')
        if not load_answer_snapshot:
            preserved_answer_state = {
                'template_key': getattr(self, 'current_template_key', ''),
                'answer_key': self.clone_answer_key(getattr(self, 'answer_key', {}) or {}),
                'loaded_answer_key': self.clone_answer_key(getattr(self, 'loaded_answer_key', {}) or {}),
                'loaded_answer_key_scheme_path': getattr(self, 'loaded_answer_key_scheme_path', None),
                'loaded_answer_key_scheme_name': getattr(self, 'loaded_answer_key_scheme_name', ''),
                'knowledge_identity': self.normalize_knowledge_identity_payload(
                    getattr(self, 'knowledge_identity_payload', {}) or {}
                ),
            }

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            session_row = conn.execute('SELECT * FROM sessions WHERE id = ?', (session_id,)).fetchone()
            result_rows = conn.execute(
                'SELECT payload_json FROM session_results WHERE session_id = ? ORDER BY file_name',
                (session_id,),
            ).fetchall()
        finally:
            conn.close()

        if not session_row:
            raise RuntimeError('未找到该测试/调查记录。')

        session_data = dict(session_row)
        # A failed snapshot load must not leave the previous test's rows bound
        # to the newly selected session.
        self.summary_data = []
        self.image_paths = []
        self.selected_folder_path = ''
        template_key = session_data.get('template_key')
        if template_key and template_key in self.template_entries and template_key != self.current_template_key:
            self.load_template_by_key(template_key)
            self.update_template_ui_state()

        if preserved_answer_state and preserved_answer_state.get('template_key') == self.current_template_key:
            self.answer_key = self.clone_answer_key(preserved_answer_state.get('answer_key') or {})
            self.loaded_answer_key = self.clone_answer_key(preserved_answer_state.get('loaded_answer_key') or {})
            self.loaded_answer_key_scheme_path = preserved_answer_state.get('loaded_answer_key_scheme_path')
            self.loaded_answer_key_scheme_name = preserved_answer_state.get('loaded_answer_key_scheme_name') or ''
            self.knowledge_identity_payload = self.normalize_knowledge_identity_payload(
                preserved_answer_state.get('knowledge_identity') or {}
            )

        kind = session_data.get('kind', 'collector')
        kind_label = '调查' if kind == 'collector' else '测试'
        self.current_session = {
            'id': session_data['id'],
            'name': session_data['name'],
            'kind': kind,
            'kind_label': kind_label,
            'created_at': session_data.get('created_at', ''),
            'folder_path': session_data.get('folder_path', ''),
            'manual_web_job_id': session_data.get('manual_web_job_id', ''),
        }
        roster_name = str(session_data.get('roster_name') or '').strip()
        self.roster_class_var.set(roster_name if roster_name in self.rosters else '不匹配名单')
        self.subjective_config = {}
        self.load_session_template_config_snapshot(session_data['id'])
        if load_answer_snapshot:
            self.clear_answer_scheme_state()
            loaded = self.load_session_answer_key_snapshot(session_data['id'])
            if not loaded and self.is_direct_paper_choice_template():
                self._session_requires_structure = True
        elif preserved_answer_state and preserved_answer_state.get('template_key') == self.current_template_key:
            self.answer_key = self.clone_answer_key(preserved_answer_state.get('answer_key') or {})
            self.loaded_answer_key = self.clone_answer_key(preserved_answer_state.get('loaded_answer_key') or {})
            self.loaded_answer_key_scheme_path = preserved_answer_state.get('loaded_answer_key_scheme_path')
            self.loaded_answer_key_scheme_name = preserved_answer_state.get('loaded_answer_key_scheme_name') or ''
            self.knowledge_identity_payload = self.normalize_knowledge_identity_payload(
                preserved_answer_state.get('knowledge_identity') or {}
            )
            scheme_path = self.loaded_answer_key_scheme_path
            if scheme_path and Path(scheme_path).exists():
                try:
                    scheme_payload = json.loads(Path(scheme_path).read_text(encoding='utf-8-sig'))
                    if not scheme_payload.get('template_key') or scheme_payload.get('template_key') == self.current_template_key:
                        self.apply_subjective_config_from_scheme(scheme_payload.get('subjective_config') or {})
                        self.apply_subjective_ocr_settings_from_scheme(scheme_payload.get('subjective_ocr_settings') or {})
                except Exception:
                    pass

        # Loading saved work must not re-recognize papers or rewrite scores.
        payloads = [json.loads(row['payload_json']) for row in result_rows]
        payloads.sort(key=lambda item: self.natural_path_sort_key(item.get('file') or ''))
        self.summary_data = payloads
        if hasattr(self, 'result_context_signature'):
            self._loaded_results_signature = self.result_context_signature()
            for entry in payloads:
                entry.setdefault('_session_id', session_data['id'])
                entry.setdefault('_result_context_signature', self._loaded_results_signature)
        folder_path = session_data.get('folder_path') or self.infer_folder_from_entries(payloads)
        if folder_path:
            self.current_session['folder_path'] = str(folder_path)
            self.set_folder_context(folder_path, load_images=True, show_preview=False)
            if not session_data.get('folder_path'):
                self.update_session_folder_path(session_data['id'], folder_path)
        if self.subjective_items():
            self.apply_subjective_scores_to_entries()
        self.current_preview_index = -1
        if self.image_paths:
            self.show_preview_by_index(0)
        else:
            self.preview_canvas.delete('all')
            self.preview_label_var.set('历史记录不显示原始图片')
        self.detail_text.delete(1.0, tk.END)
        self.summary_text.delete(1.0, tk.END)
        self.analysis_text.delete(1.0, tk.END)
        self.wrong_detail_text.delete(1.0, tk.END)

        for entry in self.summary_data:
            self.display_single_result(entry)

        if self.summary_data:
            first = self.summary_data[0]
            zones = first.get('zones') or []
            if zones:
                if self.is_direct_paper_choice_template():
                    self.selected_zones = list(self.available_zone_names)
                    self.selected_zones_var.set('试卷正背面自动')
                else:
                    self.selected_zones = list(zones)
                    self.selected_zones_var.set('自动匹配' if self.auto_latest_zone_var.get() else '手动设置')
            roster_name = first.get('roster_class')
            if roster_name and roster_name in self.rosters:
                self.roster_class_var.set(roster_name)

        self.update_session_hint()
        self.update_summary_display()
        self.update_analysis_display()
        self.status_var.set(f"已打开{kind_label}：{session_data['name']}（{len(self.summary_data)} 份）")
        self._user_explicitly_cleared = False
        try:
            self.save_ui_settings()
        except Exception:
            pass

    def open_session_browser(self):
        if hasattr(self, 'context_change_allowed') and not self.context_change_allowed():
            return
        sessions = self.list_saved_sessions()
        if not sessions:
            messagebox.showinfo('提示', '还没有已保存的测试/调查。')
            return

        win = tk.Toplevel(self.root)
        win.title('打开测试/调查')
        win.geometry('860x460')
        win.minsize(760, 380)

        ttk.Label(win, text='选择一个已保存的测试/调查', font=('Arial', 12, 'bold')).pack(anchor='w', padx=12, pady=(12, 6))

        columns = ('kind', 'name', 'template', 'roster', 'count', 'created_at')
        tree = ttk.Treeview(win, columns=columns, show='headings', height=14)
        tree.heading('kind', text='类型')
        tree.heading('name', text='名称')
        tree.heading('template', text='模板')
        tree.heading('roster', text='名册')
        tree.heading('count', text='结果数')
        tree.heading('created_at', text='创建时间')
        tree.column('kind', width=70, anchor='center')
        tree.column('name', width=220)
        tree.column('template', width=150)
        tree.column('roster', width=100)
        tree.column('count', width=70, anchor='center')
        tree.column('created_at', width=180)
        tree.pack(fill='both', expand=True, padx=12, pady=8)

        session_map = {}
        for session in sessions:
            iid = session['id']
            session_map[iid] = session
            kind_label = '调查' if session.get('kind') == 'collector' else '测试'
            tree.insert(
                '',
                'end',
                iid=iid,
                values=(
                    kind_label,
                    session.get('name', ''),
                    session.get('template_name', '') or session.get('template_key', ''),
                    session.get('roster_name', ''),
                    session.get('result_count', 0),
                    session.get('created_at', ''),
                ),
            )

        def open_selected(*_):
            selected = tree.selection()
            if not selected:
                messagebox.showwarning('警告', '请先选择一条记录。', parent=win)
                return
            session_id = selected[0]
            try:
                self.load_session_results(session_id)
            except Exception as e:
                messagebox.showerror('打开失败', str(e), parent=win)
                return
            win.destroy()

        def delete_selected():
            selected = tree.selection()
            if not selected:
                messagebox.showwarning('警告', '请先选择一条记录。', parent=win)
                return
            session_id = selected[0]
            session = session_map.get(session_id, {})
            name = session.get('name', '未命名记录')
            if not messagebox.askyesno('确认删除', f'确定删除《{name}》及其保存结果吗？', parent=win):
                return
            conn = sqlite3.connect(DB_PATH)
            try:
                conn.execute('DELETE FROM session_results WHERE session_id = ?', (session_id,))
                conn.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
                conn.commit()
            finally:
                conn.close()
            score_path = self.session_subjective_scores_path(session_id)
            if score_path and score_path.exists():
                try:
                    score_path.unlink()
                except Exception:
                    pass

            if self.current_session and self.current_session.get('id') == session_id:
                self.current_session = None
                self.update_session_hint()
                self.status_var.set('当前绑定的测试/调查已删除')
                try:
                    self.save_ui_settings()
                except Exception:
                    pass

            tree.delete(session_id)
            session_map.pop(session_id, None)
            messagebox.showinfo('成功', f'已删除《{name}》', parent=win)

        btn = ttk.Frame(win)
        btn.pack(fill='x', padx=12, pady=(0, 12))
        ttk.Button(btn, text='打开', command=open_selected).pack(side='left', padx=6)
        ttk.Button(btn, text='删除', command=delete_selected).pack(side='left', padx=6)
        ttk.Button(btn, text='关闭', command=win.destroy).pack(side='left', padx=6)
        tree.bind('<Double-1>', open_selected)

    def create_session_record(self):
        if hasattr(self, 'context_change_allowed') and not self.context_change_allowed():
            return
        if self.current_session and self.summary_data:
            messagebox.showwarning('已有测试成绩', '请先打开新试卷文件夹或清空当前工作，再创建另一测试。')
            return
        win = tk.Toplevel(self.root)
        win.title('创建测试/调查')
        win.geometry('520x280')
        win.minsize(520, 280)
        win.maxsize(520, 280)
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        kind_var = tk.StringVar(value='调查' if self.current_template_mode == 'collector' else '测试')
        name_var = tk.StringVar()

        header = ttk.Frame(win)
        header.pack(fill='x', padx=20, pady=(12, 6))
        ttk.Label(header, text='创建测试/调查', font=('Arial', 12, 'bold')).pack(anchor='w')
        ttk.Label(header, text='填好名称后，点底部“确定创建”即可。', foreground='#666').pack(anchor='w', pady=(4, 0))

        form = ttk.Frame(win)
        form.pack(fill='both', expand=True, padx=20, pady=8)

        ttk.Label(form, text='类型').grid(row=0, column=0, sticky='w', pady=6)
        kind_box = ttk.Combobox(form, textvariable=kind_var, values=['测试', '调查'], state='readonly', width=12)
        kind_box.grid(row=0, column=1, sticky='w', pady=6)

        ttk.Label(form, text='名称').grid(row=1, column=0, sticky='w', pady=6)
        name_entry = ttk.Entry(form, textvariable=name_var, width=36)
        name_entry.grid(row=1, column=1, sticky='w', pady=6)

        ttk.Label(form, text='备注').grid(row=2, column=0, sticky='nw', pady=6)
        note_text = tk.Text(form, height=4, width=36)
        note_text.grid(row=2, column=1, sticky='w', pady=6)

        def save_session():
            name = name_var.get().strip()
            if not name:
                messagebox.showwarning('警告', '请先填写名称', parent=win)
                name_entry.focus_set()
                return
            kind_label = kind_var.get().strip() or ('调查' if self.current_template_mode == 'collector' else '测试')
            kind = 'collector' if kind_label == '调查' else 'grader'
            session_id = str(uuid.uuid4())
            created_at = datetime.now().isoformat(timespec='seconds')
            note = note_text.get('1.0', 'end-1c').strip()
            folder_path = str(self.current_folder_context() or '')
            linked_manual_job_id = ''
            conn = sqlite3.connect(DB_PATH)
            try:
                conn.execute(
                    'INSERT INTO sessions (id, name, kind, template_key, template_name, created_at, roster_name, note, folder_path, manual_web_job_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (
                        session_id,
                        name,
                        kind,
                        self.current_template_key,
                        self.current_template_name,
                        created_at,
                        self.roster_class_var.get(),
                        note,
                        folder_path,
                        linked_manual_job_id,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            self.current_session = {
                'id': session_id,
                'name': name,
                'kind': kind,
                'kind_label': kind_label,
                'created_at': created_at,
                'folder_path': folder_path,
                'manual_web_job_id': linked_manual_job_id,
            }
            bound_count = 0
            if self.summary_data:
                if self.subjective_items():
                    payload = self.load_subjective_score_payload()
                    self.save_subjective_score_payload(payload)
                    self.apply_subjective_scores_to_entries(payload)
                self.save_results_to_database()
                self.detail_text.delete(1.0, tk.END)
                for entry in self.summary_data:
                    self.display_single_result(entry)
                self.update_summary_display()
                self.update_analysis_display()
                bound_count = len(self.summary_data)
            self.update_session_hint()
            self._user_explicitly_cleared = False
            try:
                self.save_ui_settings()
            except Exception:
                pass
            if bound_count:
                self.status_var.set(f"已创建{kind_label}：{name}，并绑定当前 {bound_count} 份改卷结果")
                messagebox.showinfo('成功', f'已创建{kind_label}：{name}\n已把当前 {bound_count} 份改卷结果和主观题进度绑定到这个记录。', parent=win)
            else:
                self.status_var.set(f"已创建{kind_label}：{name}")
                messagebox.showinfo('成功', f'已创建{kind_label}：{name}', parent=win)
            win.destroy()

        btn = ttk.Frame(win)
        btn.pack(side='bottom', fill='x', padx=20, pady=(0, 14))
        ttk.Button(btn, text='确定创建', command=save_session).pack(side='left')
        ttk.Button(btn, text='取消', command=win.destroy).pack(side='left', padx=8)

        win.bind('<Return>', lambda e: save_session())
        win.bind('<KP_Enter>', lambda e: save_session())
        name_entry.focus_set()

    def append_to_current_session(self):
        if not self.current_session:
            messagebox.showwarning('警告', '当前还没有绑定测试/调查，不能追加。')
            return
        if not self.image_paths:
            messagebox.showwarning('警告', '请先选择要追加的图片或文件夹。')
            return
        if not messagebox.askyesno('确认追加', f"要把当前选择的文件追加到《{self.current_session['name']}》吗？"):
            return
        self.process_all(append_mode=True)

    def save_results_to_database(self, replace_existing=False):
        if not self.current_session or not self.summary_data:
            return
        self.validate_result_ownership(self.summary_data)
        # Validate identity before writing any score file or snapshot.
        session_id = self.current_session.get('id')
        if not session_id:
            raise RuntimeError('拒绝保存：当前测试缺少 ID。')
        folder_path = str(self.current_folder_context() or '')
        bound_folder = self.normalize_folder_path(self.current_session.get('folder_path'))
        active_folder = self.normalize_folder_path(folder_path)
        if bound_folder and active_folder and bound_folder != active_folder:
            raise RuntimeError('拒绝保存：当前文件夹与测试绑定文件夹不一致。')
        conn = sqlite3.connect(DB_PATH, timeout=15)
        try:
            conn.execute('BEGIN IMMEDIATE')
            stored = conn.execute('SELECT folder_path FROM sessions WHERE id = ?', (session_id,)).fetchone()
            if stored is None:
                raise RuntimeError('拒绝保存：当前测试已不存在，请重新打开测试。')
            stored_folder = self.normalize_folder_path(stored[0])
            if stored_folder and active_folder and stored_folder != active_folder:
                raise RuntimeError('拒绝保存：数据库中的测试绑定文件夹与当前文件夹不一致。')
            if self.subjective_items():
                payload = self.load_subjective_score_payload()
                if hasattr(self, 'prepare_subjective_payload_for_grading'):
                    self.prepare_subjective_payload_for_grading(payload)
                self.save_subjective_score_payload(payload)
                if (hasattr(self, 'apply_subjective_scores_to_entries')
                        and not getattr(self, '_applying_subjective_totals', False)):
                    previous = getattr(self, '_suppress_subjective_autosave', False)
                    self._suppress_subjective_autosave = True
                    try:
                        self.apply_subjective_scores_to_entries(payload)
                    finally:
                        self._suppress_subjective_autosave = previous
            self.save_session_template_config_snapshot()
            self.save_session_answer_key_snapshot()
            now = datetime.now().isoformat(timespec='seconds')
            if folder_path:
                conn.execute('UPDATE sessions SET folder_path = ? WHERE id = ?', (folder_path, session_id))
            result_rosters = {str(item.get('roster_class')) for item in self.summary_data if item.get('roster_class')}
            if len(result_rosters) == 1:
                conn.execute('UPDATE sessions SET roster_name = ? WHERE id = ?', (result_rosters.pop(), session_id))
            if replace_existing:
                conn.execute('DELETE FROM session_results WHERE session_id = ?', (self.current_session['id'],))
            for item in self.summary_data:
                conn.execute(
                    '''
                    INSERT INTO session_results (session_id, file_name, score_id, student_name, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, file_name) DO UPDATE SET
                        score_id=excluded.score_id,
                        student_name=excluded.student_name,
                        payload_json=excluded.payload_json,
                        created_at=excluded.created_at
                    ''',
                    (
                        self.current_session['id'],
                        str(item.get('file', '')),
                        str(item.get('score_id', '')),
                        str(item.get('student_name', '')),
                        json.dumps(item, ensure_ascii=False, default=str),
                        now,
                    ),
                )
            conn.commit()
            if folder_path:
                self.current_session['folder_path'] = folder_path
        finally:
            conn.close()

    def validate_result_ownership(self, entries):
        session = self.current_session or {}
        folder = self.normalize_folder_path(session.get('folder_path'))
        rosters = {str(entry.get('roster_class')) for entry in entries if entry.get('roster_class')}
        if len(rosters) > 1:
            raise ValueError('结果中存在多个班级，已停止保存或导出，请分班级重新批改。')
        for entry in entries:
            owner = entry.get('_session_id')
            if owner and owner != session.get('id'):
                raise ValueError('结果属于其他测试，已停止保存或导出。')
            paths = [entry.get('input_path')] + list((entry.get('side_files') or {}).values())
            for value in paths:
                if value and folder and self.normalize_folder_path(Path(value).parent) != folder:
                    raise ValueError('结果图片不在当前测试文件夹中，已停止保存或导出。')


    def save_session_answer_key_snapshot(self, session_id=None):
        path = self.session_answer_key_path(session_id)
        if not path:
            return None
        answer_key = self.clone_answer_key(self.answer_key or self.loaded_answer_key or {})
        ocr_settings = self.get_subjective_ocr_settings_for_scheme() if self.subjective_items() else {}
        payload = {
            'version': 1,
            'session_id': session_id or (self.current_session or {}).get('id', ''),
            'session_name': (self.current_session or {}).get('name', ''),
            'template_key': self.current_template_key,
            'template_name': self.current_template_name,
            'answer_key': answer_key,
            'direct_paper_structure': ({} if getattr(self, '_session_requires_structure', False)
                                       else self.get_direct_paper_structure_for_scheme(path)),
            'template_structure': self.get_template_structure_for_scheme(path),
            'subjective_config': copy.deepcopy(getattr(self, 'subjective_config', {}) or {}),
            'subjective_ocr_settings': ocr_settings,
            'knowledge_identity': self.normalize_knowledge_identity_payload(self.knowledge_identity_payload),
            'source_scheme_name': getattr(self, 'loaded_answer_key_scheme_name', ''),
            'source_scheme_path': getattr(self, 'loaded_answer_key_scheme_path', ''),
            'saved_at': datetime.now().isoformat(timespec='seconds'),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, payload)
        return path

    def load_session_answer_key_snapshot(self, session_id=None):
        path = self.session_answer_key_path(session_id)
        if not path or not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding='utf-8-sig'))
        except Exception as exc:
            raise ValueError('测试答案快照损坏，已停止加载。') from exc
        if not isinstance(payload, dict):
            raise ValueError('测试答案快照格式无效。')
        expected_id = session_id or (self.current_session or {}).get('id')
        if payload.get('session_id') and payload['session_id'] != expected_id:
            raise ValueError('答案快照属于其他测试，已停止加载。')
        if payload.get('template_key') and payload.get('template_key') != self.current_template_key:
            raise ValueError('答案快照模板与当前测试不一致。')
        source_scheme_path = payload.get('source_scheme_path') or ''
        source_scheme_name = payload.get('source_scheme_name') or ''
        if self.is_direct_paper_choice_template():
            self._session_requires_structure = not bool(payload.get('direct_paper_structure'))
        # Reopening an old test must use its saved answers. A newer shared
        # scheme is only adopted through the explicit load-scheme action.
        self.set_global_answer_map(self.extract_answer_map(payload.get('answer_key') or {}))
        # Keep provenance only; working state must not point at a mutable shared file.
        self.loaded_answer_key_scheme_path = None
        self.loaded_answer_key_scheme_name = source_scheme_name or ''
        self.apply_template_structure_from_scheme(payload.get('template_structure') or {})
        if self.is_direct_paper_choice_template():
            self.apply_direct_paper_structure_from_scheme(payload.get('direct_paper_structure') or {})
            self.apply_subjective_config_from_scheme(payload.get('subjective_config') or {})
        elif self.is_mixed_objective_subjective_template():
            self.apply_subjective_config_from_scheme(payload.get('subjective_config') or {})
        self.apply_subjective_ocr_settings_from_scheme(payload.get('subjective_ocr_settings') or {})
        self.knowledge_identity_payload = self.normalize_knowledge_identity_payload(payload.get('knowledge_identity') or {})
        return True

    def save_session_template_config_snapshot(self, session_id=None):
        path = self.session_template_config_path(session_id)
        if not path:
            return None
        config = getattr(self, 'subjective_config', None)
        if not isinstance(config, dict):
            return None
        snapshot = copy.deepcopy(config)
        snapshot['_session_snapshot'] = {
            'session_id': session_id or (self.current_session or {}).get('id', ''),
            'session_name': (self.current_session or {}).get('name', ''),
            'template_key': self.current_template_key,
            'template_name': self.current_template_name,
            'saved_at': datetime.now().isoformat(timespec='seconds'),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, snapshot)
        return path

    def load_session_template_config_snapshot(self, session_id=None):
        path = self.session_template_config_path(session_id)
        if not path or not path.exists():
            return False
        try:
            snapshot = json.loads(path.read_text(encoding='utf-8-sig'))
        except Exception as exc:
            raise ValueError('测试结构快照损坏，已停止加载。') from exc
        if not isinstance(snapshot, dict):
            raise ValueError('测试结构快照格式无效。')
        meta = snapshot.pop('_session_snapshot', None)
        if meta:
            expected_id = session_id or (self.current_session or {}).get('id')
            if meta.get('session_id') and meta['session_id'] != expected_id:
                raise ValueError('结构快照属于其他测试。')
            if meta.get('template_key') and meta['template_key'] != self.current_template_key:
                raise ValueError('结构快照模板与当前测试不一致。')
        # The snapshot saved for this session is the authoritative subjective config for this session.
        # It must NOT be merged with whatever subjective_config happened to be in memory from a previously opened session!
        merged = copy.deepcopy(snapshot)
        if self.is_direct_paper_choice_template():
            merged = self.normalize_direct_subjective_scoring(merged)
        if meta:
            merged['_session_snapshot'] = meta
        self.subjective_config = merged
        config_path = getattr(self, 'subjective_config_path', None)
        if config_path:
            try:
                config_file = Path(config_path)
                config_file.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(config_file, merged)
            except Exception:
                pass
        return True
