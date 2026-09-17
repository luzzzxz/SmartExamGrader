"""Track explicit exports and refresh live views without rendering files."""
import copy
import hashlib
import json
from pathlib import Path

from core.persistence import atomic_write_json


class DerivedResultsMixin:
    def result_revision(self):
        data = {
            'session_id': (self.current_session or {}).get('id'),
            'template_key': self.current_template_key,
            'answers': getattr(self, 'answer_key', {}),
            'subjective_config': getattr(self, 'subjective_config', {}),
            'results': self.summary_data,
        }
        def canonical(value):
            if isinstance(value, dict):
                # Audit timestamps and history are not a change to displayed
                # grades. Viewing/saving progress must not invalidate previews.
                return {str(key): canonical(item) for key, item in value.items()
                        if not str(key).endswith('_at')
                        and key not in ('grading_history', 'score_file')}
            if isinstance(value, (list, tuple)):
                return [canonical(item) for item in value]
            return value
        return hashlib.sha256(json.dumps(canonical(data), ensure_ascii=False, sort_keys=True,
                                        default=str).encode('utf-8')).hexdigest()

    def linked_outputs_path(self):
        return self.subjective_scores_path().with_suffix('.outputs.json')

    def load_linked_outputs(self):
        path = self.linked_outputs_path()
        data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
            'session_id': (self.current_session or {}).get('id'),
            'template_key': self.current_template_key, 'outputs': {},
        }
        if (not isinstance(data, dict) or not isinstance(data.get('outputs'), dict)
                or any(not isinstance(item, dict) for item in data['outputs'].values())):
            raise ValueError('导出登记损坏，成绩已保留，请重新导出相关文件')
        if (data.get('session_id') != (self.current_session or {}).get('id')
                or data.get('template_key') != self.current_template_key):
            raise ValueError('导出登记属于另一测试或模板，已停止更新。')
        return data

    def register_linked_output(self, kind, primary_path, paths, options=None):
        data = self.load_linked_outputs()
        primary = str(Path(primary_path).resolve())
        data['outputs'][primary] = {
            'kind': kind, 'revision': self.result_revision(),
            'files': {str(Path(path).resolve()): hashlib.sha256(Path(path).read_bytes()).hexdigest()
                      for path in paths},
            'options': options or {},
        }
        for path in paths:
            atomic_write_json(self.output_owner_path(path), {
                'session_id': data['session_id'], 'template_key': data['template_key'],
                'primary_path': primary,
            })
        atomic_write_json(self.linked_outputs_path(), data)

    @staticmethod
    def output_owner_path(path):
        path = Path(path)
        return path.with_suffix(path.suffix + '.grading-source.json')

    def overlay_export_options(self):
        values = {}
        for name, value in vars(self).items():
            if name.startswith('overlay_') and name.endswith('_var'):
                values[name] = value.get()
        for name in ('overlay_calibration_matrices', 'overlay_grade_mode',
                     'overlay_grade_rank_remainder', 'overlay_grade_rank_thresholds',
                     'overlay_grade_thresholds', 'overlay_knowledge_font_size'):
            if hasattr(self, name):
                values[name] = copy.deepcopy(getattr(self, name))
        return values



    def notify_result_views(self):
        revision = self.result_revision()
        if getattr(self, '_notified_result_revision', None) == revision:
            return
        active = []
        self._last_result_view_errors = []
        for window, callback in getattr(self, '_result_view_subscribers', []):
            try:
                if window.winfo_exists():
                    active.append((window, callback))
                    callback()
            except Exception as exc:
                self._last_result_view_errors.append(f'预览刷新失败，请重新打开预览：{exc}')
        self._result_view_subscribers = active
        if not self._last_result_view_errors:
            self._notified_result_revision = revision
