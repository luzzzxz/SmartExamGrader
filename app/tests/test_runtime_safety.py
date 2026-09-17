"""Offline regression checks: all writes and HTTP jobs use temporary directories."""
import importlib.util
import io
import json
import os
import re
import shutil
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import Mock, patch

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
sys.path[:0] = [str(APP), str(ROOT)]

from core.persistence import atomic_write_json, read_score_payload, replace_file_batch
from core import db_session
from core.web_sync import ManualWebSyncMixin
import llm_grading_service as llm
import answer_card_stable_bootstrap as bootstrap
from enhanced_answer_card_gui_stats import EnhancedAnswerCardStatsGUI


class PersistenceTests(unittest.TestCase):
    def test_second_output_replace_failure_restores_first_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_a, old_b, new_a, new_b = [root / name for name in ('a', 'b', 'new-a', 'new-b')]
            for path, content in ((old_a, 'old-a'), (old_b, 'old-b'), (new_a, 'new-a'), (new_b, 'new-b')):
                path.write_text(content)
            original_replace = os.replace
            def replace(source, target):
                if Path(source) == new_b:
                    raise OSError('second file locked')
                return original_replace(source, target)
            with patch('core.persistence.os.replace', side_effect=replace):
                with self.assertRaises(OSError):
                    replace_file_batch([(new_a, old_a), (new_b, old_b)])
            self.assertEqual(old_a.read_text(), 'old-a')
            self.assertEqual(old_b.read_text(), 'old-b')

    def test_failed_replace_preserves_original_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '成绩.json'
            atomic_write_json(path, {'scores': {'0001': {'q1': 1}}})
            old = path.read_bytes()
            with patch('core.persistence.os.replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    atomic_write_json(path, {'scores': {}})
            self.assertEqual(path.read_bytes(), old)
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_invalid_numbers_do_not_erase_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'scores.json'
            atomic_write_json(path, {'scores': {}})
            old = path.read_bytes()
            for value in (float('nan'), float('inf')):
                with self.assertRaises(ValueError):
                    atomic_write_json(path, {'value': value})
                self.assertEqual(path.read_bytes(), old)

    def test_corruption_is_not_an_empty_score_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'scores.json'
            self.assertEqual(read_score_payload(path, {'scores': {}}), {'scores': {}})
            for text in ('{broken', '[]', '{"scores": []}', '{"scores": {"a": null}}'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    read_score_payload(path, {'scores': {}})


class LlmTests(unittest.TestCase):
    def grade(self, response, answers=None, **task_options):
        with tempfile.TemporaryDirectory() as folder:
            crop = Path(folder) / 'crop.png'
            crop.write_bytes(b'test image, no external call')
            task = {'crop_path': str(crop), 'part': {'score': 1, **task_options}}
            raw = json.dumps({'choices': [{'message': {'content': response}}]}).encode()
            with patch.object(llm.urllib.request, 'urlopen', return_value=io.BytesIO(raw)):
                return llm.grade_single_subjective_task(task, answers or [], [],
                    config={'endpoint': 'https://example.invalid/v1', 'api_key': 'test',
                            'model': 'test', 'timeout': 1, 'strictness': 'strict'})

    def test_invalid_response_stays_ungraded(self):
        cases = ['not json', '[]', '{}', '{"recognized_text":"a"}',
                 '{"recognized_text":"a","score":NaN}',
                 '{"recognized_text":"a","score":Infinity}',
                 '{"recognized_text":"a","score":true}',
                 '{"recognized_text":"a","score":2}',
                 '{"recognized_text":"a","score":-1}']
        for content in cases:
            with self.subTest(content=content):
                result = self.grade(content)
                self.assertFalse(result['success'])
                self.assertTrue(result['error'])

    def test_valid_zero_and_full_score(self):
        for score in (0, 1):
            result = self.grade(json.dumps({'recognized_text': 'a', 'score': score}))
            self.assertTrue(result['success'])
            self.assertEqual(result['score'], score)

    def test_zero_maximum_is_not_replaced_by_one(self):
        result = self.grade('{"recognized_text":"a","score":0}', score=0)
        self.assertEqual(result['max_score'], 0)

    def test_free_trend_synonym_survives_strict_mode(self):
        result = self.grade('{"recognized_text":"变大","score":1}', ['增大'])
        self.assertEqual(result['score'], 1)

    def test_choice_fill_still_requires_original_word(self):
        result = self.grade('{"recognized_text":"变大","score":1}', ['增大'],
                            ocr={'choice_fill': True, 'candidate_words': '增大/减小'})
        self.assertEqual(result['score'], 0)

    def test_negative_or_compound_trend_does_not_force_full_marks(self):
        for text in ('不增大', '先增大后减小', '不会变大'):
            with self.subTest(text=text):
                self.assertFalse(llm.match_trend_direction(text, '增大')[0])
                result = self.grade(json.dumps({'recognized_text': text, 'score': 0}), ['增大'])
                self.assertEqual(result['score'], 0)

    def test_secret_has_no_source_default_and_env_is_supported(self):
        self.assertEqual(llm.DEFAULT_CONFIG['api_key'], '')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text('{"api_key":"local", "model":"existing"}')
            with patch.object(llm, 'get_config_file_path', return_value=path):
                with patch.dict(os.environ, {'ANSWER_CARD_LLM_API_KEY': 'test-env'}):
                    self.assertEqual(llm.load_llm_config()['api_key'], 'test-env')
                    self.assertEqual(llm.load_llm_config()['model'], 'existing')
                path.write_text('{broken')
                self.assertFalse(llm.load_llm_config()['enabled'])


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'test.db'
        self.db_patch = patch.object(db_session, 'DB_PATH', self.db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.gui = db_session.DatabaseSessionMixin()
        self.gui.init_database()
        self.folder = str(Path(self.temp.name) / 'classA')
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO sessions(id,name,kind,created_at,folder_path) VALUES ('s1','test','grader','today',?)", (self.folder,))
        self.gui.current_session = {'id': 's1', 'folder_path': self.folder}
        self.gui.current_folder_context = Mock(return_value=self.folder)
        self.gui.summary_data = [{'file': '01.jpg', 'score_id': '0001', 'score': 1}]
        self.gui.subjective_items = Mock(return_value=[{'part_id': 'q1'}])
        self.gui.load_subjective_score_payload = Mock(return_value={'scores': {}})
        for name in ('save_subjective_score_payload', 'save_session_template_config_snapshot', 'save_session_answer_key_snapshot'):
            setattr(self.gui, name, Mock())

    def assert_no_writes(self):
        self.gui.save_subjective_score_payload.assert_not_called()
        self.gui.save_session_template_config_snapshot.assert_not_called()
        self.gui.save_session_answer_key_snapshot.assert_not_called()

    def test_folder_mismatch_rejected_before_any_file_write(self):
        self.gui.current_folder_context.return_value = str(Path(self.temp.name) / 'classB')
        with self.assertRaises(RuntimeError):
            self.gui.save_results_to_database()
        self.assert_no_writes()

    def test_deleted_session_cannot_create_orphan_results(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('DELETE FROM sessions')
        with self.assertRaises(RuntimeError):
            self.gui.save_results_to_database()
        self.assert_no_writes()

    def test_database_binding_checked_even_if_memory_is_wrong(self):
        self.gui.current_session['folder_path'] = ''
        self.gui.current_folder_context.return_value = str(Path(self.temp.name) / 'classB')
        with self.assertRaises(RuntimeError):
            self.gui.save_results_to_database()
        self.assert_no_writes()

    def test_score_save_failure_is_reported_and_db_rolled_back(self):
        self.gui.save_subjective_score_payload.side_effect = OSError('disk failure')
        with self.assertRaises(OSError):
            self.gui.save_results_to_database()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM session_results').fetchone()[0], 0)

    def test_valid_save_preserves_leading_zero_id(self):
        self.gui.save_results_to_database()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self.assertEqual(conn.execute('SELECT score_id FROM session_results').fetchone()[0], '0001')

    def test_existing_results_roundtrip_for_each_saved_template(self):
        source = APP / 'answer_card_app.db'
        if not source.exists():
            self.skipTest('No local historical database')
        samples = {}
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as conn:
            for key, raw in conn.execute('SELECT s.template_key,r.payload_json FROM sessions s JOIN session_results r ON s.id=r.session_id'):
                samples.setdefault(key, json.loads(raw))
        self.gui.subjective_items.return_value = []
        for key, payload in samples.items():
            with self.subTest(template=key):
                # Rebind the read-only historical sample to this temporary test.
                payload = dict(payload)
                if payload.get('input_path'):
                    payload['input_path'] = str(Path(self.folder) / Path(payload['input_path']).name)
                if payload.get('side_files'):
                    payload['side_files'] = {side: str(Path(self.folder) / Path(value).name)
                                             for side, value in payload['side_files'].items() if value}
                if payload.get('_session_id'):
                    payload['_session_id'] = 's1'
                self.gui.summary_data = [payload]
                self.gui.save_results_to_database(replace_existing=True)
                with closing(sqlite3.connect(self.db)) as conn:
                    saved = json.loads(conn.execute('SELECT payload_json FROM session_results').fetchone()[0])
                self.assertEqual(saved, payload)

    def test_snapshot_is_not_replaced_by_newer_shared_scheme(self):
        snapshot = Path(self.temp.name) / 'snapshot.json'
        shared = Path(self.temp.name) / 'shared.json'
        atomic_write_json(snapshot, {'template_key': 't1', 'answer_key': {'1': 'A'},
                                    'source_scheme_path': str(shared)})
        atomic_write_json(shared, {'answer_key': {'1': 'B'}})
        gui = self.gui
        gui.current_template_key = 't1'
        gui.session_answer_key_path = Mock(return_value=snapshot)
        gui.extract_answer_map = lambda value: value
        gui.set_global_answer_map = Mock()
        for name in ('remember_loaded_answer_scheme', 'apply_template_structure_from_scheme',
                     'apply_subjective_ocr_settings_from_scheme', 'normalize_knowledge_identity_payload',
                     'load_answer_scheme_file_into_state'):
            setattr(gui, name, Mock())
        gui.is_direct_paper_choice_template = lambda: False
        gui.is_mixed_objective_subjective_template = lambda: False
        self.assertTrue(gui.load_session_answer_key_snapshot())
        gui.set_global_answer_map.assert_called_once_with({'1': 'A'})
        gui.load_answer_scheme_file_into_state.assert_not_called()


class DesktopScoreTests(unittest.TestCase):
    def test_wrong_identity_or_corruption_never_falls_back_to_empty_scores(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'scores.json'
            gui = EnhancedAnswerCardStatsGUI.__new__(EnhancedAnswerCardStatsGUI)
            gui.subjective_scores_path = lambda: path
            gui.current_session = {'id': 's1'}
            gui.current_template_key = 't1'
            for payload in ('{broken', '{"session_id":"s2","scores":{}}',
                            '{"template_key":"t2","scores":{}}'):
                path.write_text(payload)
                with self.assertRaises(ValueError):
                    gui.load_subjective_score_payload()
                self.assertEqual(path.read_text(), payload)

    def test_timeout_and_launch_failure_reach_existing_fallback(self):
        for error in (subprocess.TimeoutExpired('helper', 90), OSError('launch failed')):
            with patch.object(bootstrap.subprocess, 'run', side_effect=error):
                self.assertNotEqual(bootstrap.run_helper('orient').returncode, 0)


class WebSyncTests(unittest.TestCase):
    def make_gui(self, remote):
        gui = ManualWebSyncMixin()
        gui.require_current_session_for_grading = lambda action: True
        gui.manual_web_job_candidates = lambda: ['job1']
        gui.manual_web_request = Mock(return_value={'scores': remote})
        gui.bind_manual_web_job_to_current_session = Mock()
        gui.load_subjective_score_payload = lambda: {'scores': {}}
        gui.iter_subjective_parts = lambda: iter([{'part_id': 'q1', 'score': 1}])
        gui.direct_calculation_manual_parts = lambda: []
        gui.summary_data = [{'key': 'student-1'}]
        gui.subjective_entry_key = lambda entry: entry['key']
        for name in ('save_subjective_score_payload', 'apply_subjective_scores_to_entries',
                     'display_single_result', 'update_summary_display', 'update_analysis_display',
                     'save_results_to_database'):
            setattr(gui, name, Mock())
        gui.detail_text = Mock()
        gui.status_var = Mock()
        return gui

    def test_invalid_download_never_saves_or_deletes_remote_job(self):
        cases = [{'foreign': {'q1': {'score': 1}}},
                 {'student-1': {'unknown': {'score': 1}}},
                 {'student-1': {'q1': {'score': 'NaN'}}},
                 {'student-1': {'q1': {'score': 2}}}]
        for remote in cases:
            with self.subTest(remote=remote), patch('core.web_sync.messagebox.showerror') as error:
                gui = self.make_gui(remote)
                gui.download_manual_web_scores()
                error.assert_called_once()
                gui.save_subjective_score_payload.assert_not_called()
                gui.bind_manual_web_job_to_current_session.assert_not_called()
                self.assertTrue(all(call.args[0] == 'GET' for call in gui.manual_web_request.call_args_list))

    def test_valid_download_uses_local_maximum_and_save_precedes_delete(self):
        gui = self.make_gui({'student-1': {'q1': {'score': 1, 'max_score': 100}}})
        events = []
        gui.save_results_to_database.side_effect = lambda: events.append('save')
        gui.manual_web_request.side_effect = lambda method, route: (
            {'scores': {'student-1': {'q1': {'score': 1, 'max_score': 100}}}}
            if method == 'GET' else events.append('delete'))
        with patch('core.web_sync.messagebox.showinfo'):
            gui.download_manual_web_scores()
        self.assertEqual(events, ['save', 'delete'])
        payload = gui.save_subjective_score_payload.call_args.args[0]
        self.assertEqual(payload['scores']['student-1']['q1']['max_score'], 1)


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        config = Path(cls.temp.name) / 'config.json'
        config.write_text(json.dumps({'api_token': 'offline-test', 'teacher_username': 'teacher',
                                     'teacher_password_hash': 'unused', 'session_secret': 'test-secret'}))
        spec = importlib.util.spec_from_file_location('audit_web', APP / 'manual_grading_web_server.py')
        cls.web = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {'MANUAL_WEB_CONFIG': str(config), 'MANUAL_WEB_DATA_DIR': str(Path(cls.temp.name) / 'data')}):
            spec.loader.exec_module(cls.web)
        class QuietHandler(cls.web.Handler):
            def log_message(self, *args):
                pass
        cls.server = cls.web.ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temp.cleanup()

    def setUp(self):
        self.job_id = self.id().split('.')[-1]
        self.folder = self.web.job_dir(self.job_id)
        self.tasks = [{'entry_key': f'student-{i}', 'part_id': 'q1', 'max_score': 1,
                       'score_id': f'{i:04d}', 'student_name': 'fixture', 'image': 'images/a.png'} for i in range(24)]
        self.manifest = {'job_id': self.job_id, 'tasks': self.tasks}
        self.web.write_json(self.folder / 'manifest.json', self.manifest)
        self.web.write_json(self.folder / 'scores.json', {'scores': {}})
        (self.folder / 'images').mkdir(exist_ok=True)
        (self.folder / 'images/a.png').write_bytes(b'fixture')

    def request(self, route, body=None, token='offline-test'):
        headers = {'X-Manual-Grading-Token': token}
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        req = urllib.request.Request(self.base + route, data=body, headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def score(self, entry, **values):
        return self.request(f'/api/jobs/{self.job_id}/score',
                            {'entry_key': entry, 'part_id': 'q1', 'score': 1, **values})

    def archive(self, **extra):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(self.manifest))
            archive.writestr('images/a.png', b'fixture')
            for name, data in extra.items():
                archive.writestr(name, data)
        return stream.getvalue()

    def test_unauthorized_request_is_rejected(self):
        status, _ = self.request('/api/jobs', token='wrong')
        self.assertEqual(status, 401)

    def test_concurrent_scores_are_all_preserved(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda task: self.score(task['entry_key']), self.tasks))
        self.assertTrue(all(status == 200 for status, _ in results))
        payload = json.loads((self.folder / 'scores.json').read_text())
        self.assertEqual(len(payload['scores']), len(self.tasks))

    def test_forged_maximum_and_student_metadata_are_not_trusted(self):
        status, _ = self.score('student-0', max_score=100, student_name='forged')
        self.assertEqual(status, 200)
        record = json.loads((self.folder / 'scores.json').read_text())['scores']['student-0']['q1']
        self.assertEqual(record['max_score'], 1)
        self.assertEqual(record['student_name'], 'fixture')
        self.assertEqual(record['score_id'], '0000')

    def test_invalid_scores_and_foreign_entries_are_rejected(self):
        for value in (2, -1, None, True, 'NaN', 'Infinity', 'not-a-number'):
            with self.subTest(value=value):
                self.assertEqual(self.score('student-0', score=value)[0], 400)
        self.assertEqual(self.score('foreign')[0], 400)
        self.assertEqual(json.loads((self.folder / 'scores.json').read_text()), {'scores': {}})

    def test_malformed_json_is_a_controlled_error(self):
        status, _ = self.request(f'/api/jobs/{self.job_id}/score', b'{broken')
        self.assertEqual(status, 400)

    def test_corrupt_score_file_is_not_overwritten(self):
        path = self.folder / 'scores.json'
        path.write_text('{broken')
        self.assertEqual(self.score('student-0')[0], 400)
        self.assertEqual(path.read_text(), '{broken')

    def test_reupload_preserves_existing_scores(self):
        self.score('student-0')
        self.assertEqual(self.request('/api/upload', self.archive())[0], 200)
        self.assertEqual(self.request(f'/api/jobs/{self.job_id}/scores')[1]['scores']['student-0']['q1']['score'], 1)
        self.assertTrue((self.folder / 'images/a.png').is_file())

    def test_archive_path_traversal_is_rejected_without_erasing_job(self):
        for name in ('../escape.txt', 'images/../../escape.txt', 'images\\..\\escape.txt', '/absolute.txt'):
            with self.subTest(name=name):
                old = (self.folder / 'manifest.json').read_bytes()
                self.assertEqual(self.request('/api/upload', self.archive(**{name: 'bad'}))[0], 400)
                self.assertEqual((self.folder / 'manifest.json').read_bytes(), old)

    def test_extraction_failure_preserves_existing_job(self):
        self.score('student-0')
        old = (self.folder / 'scores.json').read_bytes()
        with patch.object(self.web.shutil, 'copyfileobj', side_effect=OSError('disk full')):
            self.assertEqual(self.request('/api/upload', self.archive())[0], 500)
        self.assertEqual((self.folder / 'scores.json').read_bytes(), old)

    def test_image_route_cannot_read_score_json_or_sibling_directory(self):
        for rel in ('scores.json', '../other/scores.json', '..%5cother%5ca.png'):
            self.assertEqual(self.request(f'/api/jobs/{self.job_id}/image/{rel}')[0], 404)

    def test_delete_round_trip(self):
        self.assertEqual(self.request(f'/api/jobs/{self.job_id}/delete', b'{}')[0], 200)
        self.assertFalse(self.folder.exists())

    def test_rendered_javascript_parses_and_checks_save_response(self):
        handler = object.__new__(self.web.Handler)
        captured = []
        handler.send_html = lambda body, status=200: captured.append(body)
        handler.page_job(self.job_id)
        script = re.search(r'<script>(.*?)</script>', captured[0], re.S).group(1)
        self.assertIn('if(!result.ok)throw', script)
        self.assertNotIn("getElementById('meta').innerHTML", script)
        self.assertIn('if(saving||!tasks.length)return', script)
        if shutil.which('node'):
            result = subprocess.run(['node', '-e', 'new Function(process.argv[1]);', script],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_corrupt_scores_get_returns_controlled_error(self):
        (self.folder / 'scores.json').write_text('{broken')
        self.assertEqual(self.request(f'/api/jobs/{self.job_id}/scores')[0], 500)


    def test_reupload_cannot_change_owner_or_context(self):
        self.score('student-0')
        old = (self.folder / 'scores.json').read_bytes()
        self.manifest['session_id'] = 'different-test'
        self.assertEqual(self.request('/api/upload', self.archive())[0], 400)
        self.assertEqual((self.folder / 'scores.json').read_bytes(), old)

    def test_reupload_changed_answer_does_not_reuse_score(self):
        self.score('student-0')
        self.manifest['tasks'][0]['expected_answer'] = 'new-answer'
        self.assertEqual(self.request('/api/upload', self.archive())[0], 200)
        scores = self.request(f'/api/jobs/{self.job_id}/scores')[1]['scores']
        self.assertNotIn('student-0', scores)

    def test_reupload_changed_scan_does_not_reuse_score(self):
        self.score('student-0')
        (self.folder / 'images/a.png').write_bytes(b'old-paper')
        self.assertEqual(self.request('/api/upload', self.archive())[0], 200)
        self.assertFalse(self.request(f'/api/jobs/{self.job_id}/scores')[1]['scores'])

    def test_stale_browser_cannot_score_reuploaded_job(self):
        self.assertEqual(self.request('/api/upload', self.archive())[0], 200)
        self.assertEqual(self.score('student-0')[0], 409)
        revision = json.loads((self.folder / 'manifest.json').read_text())['revision']
        self.assertEqual(self.score('student-0', job_revision=revision)[0], 200)
        self.assertEqual(self.request('/api/upload', self.archive())[0], 200)
        self.assertEqual(self.score('student-0', job_revision=revision)[0], 409)



if __name__ == '__main__':
    unittest.main()
