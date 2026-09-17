"""Regression tests for test/scheme/scan ownership. No production writes/network."""
import copy
import ast
import inspect
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import MagicMock, Mock, patch

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP.parent)]
from enhanced_answer_card_gui_stats import EnhancedAnswerCardStatsGUI as GUI
from core import db_session, pdf_overlay
from core.persistence import atomic_write_json


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.gui = GUI.__new__(GUI)
        g = self.gui
        g.current_session = {'id': 'test-a', 'name': 'A', 'folder_path': str(self.folder)}
        g.current_template_key = 't1'
        g.current_template_name = 'T1'
        g.current_template_mode = 'grader'
        g.summary_data = []
        g.subjective_config = {}
        g.answer_config = {'zones': []}
        g.available_zone_names = []
        g.answer_key = {'__global__': {1: ['A']}}
        g.loaded_answer_key = copy.deepcopy(g.answer_key)
        g.loaded_answer_key_scheme_path = None
        g.loaded_answer_key_scheme_name = 'paper-a'
        g.iter_subjective_parts = lambda: iter([{'part_id': 'q1_b1', 'score': 1,
                                               'kind': 'fill_blank', 'ocr': {'expected_answer': 'A'}}])

    def test_stale_score_payload_cannot_be_relabelled_as_current_test(self):
        old = {'session_id': 'test-b', 'template_key': 't1', 'scores': {}}
        before = copy.deepcopy(old)
        with self.assertRaises(ValueError):
            self.gui.save_subjective_score_payload(old)
        self.assertEqual(old, before)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_old_answers_cannot_be_restamped_on_save(self):
        payload = {'session_id': 'test-a', 'template_key': 't1',
                   'subjective_answer_signature': 'old-answer', 'scores': {'1.jpg': {'q1_b1': {'score': 1}}}}
        before = copy.deepcopy(payload)
        with self.assertRaises(ValueError):
            self.gui.save_subjective_score_payload(payload)
        self.assertEqual(payload, before)

    def test_opening_changed_rules_never_clears_existing_scores(self):
        path = self.folder / 'scores.json'
        payload = {'session_id': 'test-a', 'subjective_answer_signature': 'old',
                   'scores': {'1.jpg': {'q1_b1': {'score': 1}}}}
        atomic_write_json(path, payload)
        old_bytes = path.read_bytes()
        self.gui.subjective_scores_path = lambda: path
        before = copy.deepcopy(payload)
        with self.assertRaises(ValueError):
            self.gui.prepare_subjective_payload_for_grading(payload)
        self.assertEqual(payload, before)
        self.assertEqual(path.read_bytes(), old_bytes)
        backup = list(self.folder.glob('scores.before_rules_change_*.json'))
        self.assertEqual(backup, [])

    def single_regrade_fixture(self):
        g = self.gui
        self.parts = [{'part_id': 'q1_b1', 'score': 1, 'kind': 'fill_blank',
                       'ocr': {'expected_answer': 'A', 'enabled': True}},
                      {'part_id': 'q2_b1', 'score': 1, 'kind': 'fill_blank',
                       'ocr': {'expected_answer': 'C', 'enabled': True}}]
        g.iter_subjective_parts = lambda: iter(self.parts)
        g.summary_data = [{'file': '1.jpg', 'input_path': str(self.folder / '1.jpg')},
                          {'file': '2.jpg', 'input_path': str(self.folder / '2.jpg')}]
        g.subjective_scores_path = lambda: self.folder / 'scores.json'
        payload = {'session_id': 'test-a', 'template_key': 't1',
                   'subjective_answer_signature': g.subjective_answer_signature(),
                   'subjective_structure_signature': g.subjective_structure_signature(),
                   'scores': {g.subjective_entry_key(g.summary_data[0]): {
                       'q1_b1': {'score': 0, 'ocr_text': 'B', 'auto_graded': True},
                       'q2_b1': {'score': 1, 'manual_graded': True, 'note': 'keep'}},
                       g.subjective_entry_key(g.summary_data[1]): {
                           'q1_b1': {'score': 0.5, 'manual_graded': True, 'ocr_text': 'B'},
                           'q2_b1': {'score': 0, 'auto_graded': True, 'ocr_text': 'D'}}}}
        atomic_write_json(g.subjective_scores_path(), payload)
        g.load_subjective_score_payload = lambda: json.loads(g.subjective_scores_path().read_text(encoding='utf-8'))
        def update(settings):
            for part in self.parts:
                if part['part_id'] in settings:
                    part['ocr'] = copy.deepcopy(settings[part['part_id']])
        g.save_ocr_settings_for_parts = Mock(side_effect=update)
        g.sync_loaded_answer_scheme_ocr_settings = Mock(return_value=1)
        g.save_session_answer_key_snapshot = Mock()
        g.refresh_main_result_views = Mock()
        g.lookup_ocr_result_classification = lambda *args: None
        return payload

    def test_single_blank_correction_and_window_close_preserve_other_marks(self):
        g = self.gui
        window_payload = self.single_regrade_fixture()
        original = copy.deepcopy(window_payload)
        scores_alias = window_payload['scores']
        g.active_subjective_grading_refresh = lambda: g.refresh_subjective_window_payload(
            window_payload, g.load_subjective_score_payload())
        with patch('core.blank_recheck.get_blank_recheck_collector'):
            result = g.regrade_single_subjective_part('q1_b1', 'B')
        self.assertTrue(result['ok'])
        self.assertEqual(result['matched_count'], 1)
        self.assertEqual(result['skipped_manual'], 1)
        current = g.load_subjective_score_payload()
        for key in original['scores']:
            self.assertEqual(current['scores'][key]['q2_b1'], original['scores'][key]['q2_b1'])
        key1, key2 = original['scores']
        self.assertEqual(current['scores'][key1]['q1_b1']['score'], 1)
        self.assertEqual(current['scores'][key2]['q1_b1'], original['scores'][key2]['q1_b1'])
        self.assertIs(window_payload['scores'], scores_alias)
        self.assertEqual(window_payload['subjective_answer_signature'], g.subjective_answer_signature())
        g.save_subjective_score_payload(window_payload)  # Same call made on closing the live window.
        self.assertEqual(g.load_subjective_score_payload()['scores'], current['scores'])

    def test_single_blank_manual_override_is_explicit(self):
        g = self.gui
        self.single_regrade_fixture()
        with patch('core.blank_recheck.get_blank_recheck_collector'):
            result = g.regrade_single_subjective_part('q1_b1', 'B', override_manual=True)
        self.assertTrue(result['ok'])
        self.assertEqual(result['matched_count'], 2)

    def test_rule_change_invalidates_only_affected_auto_marks_and_is_idempotent(self):
        g = self.gui
        payload = self.single_regrade_fixture()
        g.prepare_subjective_payload_for_grading(payload)
        before = copy.deepcopy(payload)
        scores_alias = payload['scores']
        self.parts[0]['ocr']['expected_answer'] = 'B'
        g.save_subjective_score_payload(payload)
        key1, key2 = payload['scores']
        changed = payload['scores'][key1]['q1_b1']
        self.assertIsNone(changed['score'])
        self.assertEqual(changed['grading_history'][0]['record'], before['scores'][key1]['q1_b1'])
        self.assertEqual(changed['ocr_text'], 'B')
        self.assertIs(payload['scores'], scores_alias)
        for key in payload['scores']:
            self.assertEqual(payload['scores'][key]['q2_b1'], before['scores'][key]['q2_b1'])
        self.assertEqual(payload['scores'][key2]['q1_b1'], before['scores'][key2]['q1_b1'])
        after = copy.deepcopy(payload['scores'])
        g.save_subjective_score_payload(payload)
        self.assertEqual(payload['scores'], after)

    def test_scheme_rename_padding_and_confidence_do_not_invalidate_marks(self):
        g = self.gui
        payload = self.single_regrade_fixture()
        g.prepare_subjective_payload_for_grading(payload)
        before = copy.deepcopy(payload['scores'])
        g.loaded_answer_key_scheme_name = 'renamed'
        self.parts[0]['ocr'].update(pad_left=999, low_confidence_threshold=0.91)
        self.assertTrue(g.subjective_payload_matches_current_answers(payload))
        g.save_subjective_score_payload(payload)
        self.assertEqual(payload['scores'], before)

    def test_maximum_change_preserves_manual_evidence_for_review(self):
        g = self.gui
        payload = self.single_regrade_fixture()
        g.prepare_subjective_payload_for_grading(payload)
        self.parts[0]['score'] = 0.25
        g.save_subjective_score_payload(payload)
        for records in payload['scores'].values():
            self.assertIsNone(records['q1_b1']['score'])
            self.assertTrue(records['q1_b1']['grading_history'])

    def test_part_rule_map_never_bypasses_session_or_geometry(self):
        g = self.gui
        payload = self.single_regrade_fixture()
        g.prepare_subjective_payload_for_grading(payload)
        for field, value in [('session_id', 'other-class'), ('subjective_structure_signature', 'different-paper')]:
            bad = copy.deepcopy(payload)
            bad[field] = value
            before = copy.deepcopy(bad)
            with self.assertRaises(ValueError):
                g.save_subjective_score_payload(bad)
            self.assertEqual(bad, before)

    def test_unknown_legacy_rules_do_not_erase_displayed_totals(self):
        g = self.gui
        payload = self.single_regrade_fixture()
        g.summary_data[0]['combined_score'] = 77
        payload['subjective_answer_signature'] = 'unknown'
        before = copy.deepcopy(g.summary_data)
        with self.assertRaises(ValueError):
            g.apply_subjective_scores_to_entries(payload)
        self.assertEqual(g.summary_data, before)

    def test_single_blank_propagates_totals_ranking_preview_and_lecture_data(self):
        from core.lecture_presentation import build_lecture_presentation_data
        g = self.gui
        payload = self.single_regrade_fixture()
        for index, entry in enumerate(g.summary_data):
            entry.update(student_name=f'student-{index}', score_id=str(index), raw_score=index,
                         max_total_score=2, score=50 * index)
        g.save_results_to_database = Mock()
        g.update_summary_display = Mock()
        g.update_analysis_display = Mock()
        g.root = Mock()
        del g.refresh_main_result_views
        refreshed = Mock()
        g._result_view_subscribers = [(Mock(winfo_exists=lambda: True), refreshed)]
        g.apply_subjective_scores_to_entries(payload)
        self.assertEqual(g.overlay_student_rank(g.summary_data[0]), 2)
        with patch('core.blank_recheck.get_blank_recheck_collector'):
            result = g.regrade_single_subjective_part('q1_b1', 'B')
        self.assertTrue(result['ok'])
        self.assertEqual(g.summary_data[0]['combined_raw_score'], 2)
        self.assertEqual(g.summary_data[1]['combined_raw_score'], 1.5)
        self.assertEqual(g.overlay_student_rank(g.summary_data[0]), 1)
        self.assertEqual(g.overlay_student_rank(g.summary_data[1]), 2)
        self.assertEqual(g.printed_total_score_text(g.summary_data[0]), '50')
        refreshed.assert_called_once()
        g.save_results_to_database.assert_called()
        lecture = build_lecture_presentation_data(g.summary_data, session_name='A')
        self.assertEqual(lecture['meta']['raw_avg_total_score'], 1.8)
        self.assertEqual(lecture['meta']['raw_max_score'], 2)



    def test_output_registry_cannot_cross_tests(self):
        g = self.gui
        self.single_regrade_fixture()
        html = self.folder / 'lecture.html'
        html.write_text('old')
        g.register_linked_output('lecture', html, [html])
        g.current_session['id'] = 'another-test'
        g.export_lecture_presentation_package = Mock()
        with self.assertRaises(ValueError):
            g.load_linked_outputs()
        g.export_lecture_presentation_package.assert_not_called()



    def test_lecture_upload_names_are_scoped_by_test_and_excel_failure_is_reported(self):
        from core import web_sync
        g = self.gui
        html, xlsx = self.folder / 'same.html', self.folder / 'same.xlsx'
        html.write_text('html')
        xlsx.write_text('xlsx')
        g.load_manual_web_sync_config = lambda: {'server_url': 'https://school.invalid', 'api_token': 'fixture'}
        success = MagicMock()
        success.__enter__.return_value.read.return_value = b'{"ok":true}'
        with patch.object(web_sync.urllib.request, 'urlopen', return_value=success) as upload:
            self.assertTrue(g.upload_lecture_to_vps(html)[0])
            first = upload.call_args.args[0].get_header('X-file-name')
            g.current_session['id'] = 'test-b'
            self.assertTrue(g.upload_lecture_to_vps(html)[0])
            second = upload.call_args.args[0].get_header('X-file-name')
        self.assertNotEqual(first, second)
        with patch.object(web_sync.urllib.request, 'urlopen', side_effect=[success, OSError('excel offline')]):
            ok, detail = g.upload_lecture_to_vps(html, xlsx)
        self.assertFalse(ok)
        self.assertIn('excel offline', detail)

    def test_silent_lecture_export_uses_current_marks_without_dialog_or_upload(self):
        g = self.gui
        self.single_regrade_fixture()
        g.selected_folder_path = str(self.folder)
        g.save_results_to_database = Mock()
        g.build_submission_audit = Mock(return_value={})
        g.upload_lecture_to_vps = Mock(side_effect=AssertionError('unexpected upload'))
        path = self.folder / 'lecture.html'
        with patch('enhanced_answer_card_gui_stats.messagebox.showerror') as error:
            files = g.export_lecture_presentation_package(html_save_path=str(path), silent=True)
        self.assertTrue(all(file.is_file() for file in files))
        self.assertIn('raw_avg_total_score', path.read_text(encoding='utf-8'))
        g.upload_lecture_to_vps.assert_not_called()
        error.assert_not_called()

    def test_single_regrade_defers_files_until_explicit_export_with_latest_scores(self):
        g = self.gui
        payload = self.single_regrade_fixture()
        g.selected_folder_path = g.last_open_dir = str(self.folder)
        g.save_results_to_database = Mock()
        g.update_summary_display = g.update_analysis_display = Mock()
        g.build_submission_audit = Mock(return_value={})
        g.root = Mock()
        g.status_var = Mock()
        del g.refresh_main_result_views
        for index, entry in enumerate(g.summary_data):
            entry.update(student_name=f'student-{index}', score_id=str(index), raw_score=index,
                         max_total_score=2, score=50 * index)
        g.checked_overlay_entries = lambda: g.summary_data
        for name, value in [('overlay_offset_x_mm_var', 0), ('overlay_offset_y_mm_var', 0),
                            ('overlay_scale_percent_var', 100)]:
            setattr(g, name, Mock(get=lambda v=value: v))
        printed_scores = []
        g.build_marked_answer_card_overlay_ops = lambda entry, side: (
            [g.printed_total_score_text(entry)], (100, 100))
        def draw(pdf, ops, *args, **kwargs):
            printed_scores.append(ops[0])
            pdf.drawString(10, 10, ops[0])
        g.draw_overlay_ops_to_pdf_page = draw
        html, pdf = self.folder / 'lesson.html', self.folder / 'marks.pdf'
        g.export_lecture_presentation_package(html_save_path=str(html), silent=True)
        g.export_marked_card_overlay_pdf(pdf_path=str(pdf), silent=True)
        old_html = html.read_bytes()
        old_pdf = pdf.read_bytes()
        output_files = [html, pdf, html.with_suffix('.xlsx')]
        old_files = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in output_files}
        g.upload_lecture_to_vps = Mock(side_effect=AssertionError('unexpected upload'))
        self.assertEqual(printed_scores, ['38', '25'])
        printed_scores.clear()
        with patch('core.blank_recheck.get_blank_recheck_collector'):
            result = g.regrade_single_subjective_part('q1_b1', 'B')
        self.assertEqual(result['output_errors'], [])
        self.assertEqual(printed_scores, [])
        for path in output_files:
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), old_files[path])
        self.assertEqual(pdf.read_bytes(), old_pdf)
        g.upload_lecture_to_vps.assert_not_called()
        # Explicit export reads the saved, corrected marks and rebuilds only
        # the requested output, not every registered file.
        g.export_marked_card_overlay_pdf(pdf_path=str(pdf), silent=True)
        self.assertEqual(html.read_bytes(), old_html)
        g.export_lecture_presentation_package(html_save_path=str(html), silent=True)
        self.assertNotEqual(html.read_bytes(), old_html)
        self.assertEqual(printed_scores, ['38', '50'])
        self.assertTrue(pdf.read_bytes().startswith(b'%PDF'))
        for output in g.load_linked_outputs()['outputs'].values():
            self.assertEqual(output['revision'], g.result_revision())

    @staticmethod
    def grading_window_callback(name, bindings):
        # Execute the real Tk callback with fake widgets, without constructing
        # the app or touching its production settings/database.
        tree = ast.parse(textwrap.dedent(inspect.getsource(GUI.open_subjective_grading)))
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
        namespace = dict(vars(sys.modules[GUI.__module__]))
        namespace.update(bindings)
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<grading-window-test>', 'exec'), namespace)
        return namespace[name]

    def test_browsing_refresh_never_regrades_pending_records(self):
        g = self.gui
        payload = self.single_regrade_fixture()
        keys = list(payload['scores'])
        payload['scores'][keys[0]]['q1_b1'].update(auto_graded=False, ocr_auto_need_manual=True)
        before = copy.deepcopy(payload)
        g.save_subjective_score_payload = Mock(side_effect=AssertionError('browse attempted save'))
        tasks = [{'entry': entry, 'entry_key': key, 'part': self.parts[0], 'part_id': 'q1_b1',
                  'crop_path': self.folder / 'missing-crop.jpg'} for key, entry in zip(keys, g.summary_data)]
        state = {'index': 0}
        bindings = dict(self=g, payload=payload, all_scores=payload['scores'], tasks=tasks,
                        state=state, part_order={'q1_b1': 0}, is_manual_grade_task=lambda task: True)
        for name in ('standard_answer_var', 'title_var', 'progress_var', 'score_var', 'ocr_var',
                     'ocr_display_label', 'image_label'):
            bindings[name] = Mock()
        refresh = self.grading_window_callback('refresh', bindings)
        for index in (0, 1, 0):
            state['index'] = index
            refresh()
        self.assertEqual(payload, before)
        g.save_subjective_score_payload.assert_not_called()

    def test_closing_with_or_without_edits_never_exports_or_uploads(self):
        g = self.gui
        for edited in (False, True):
            with self.subTest(edited=edited):
                payload = self.single_regrade_fixture()
                html = self.folder / 'existing.html'
                html.write_text('old exported result')
                g.register_linked_output('lecture', html, [html], {'published': True})
                before = (html.read_bytes(), html.stat().st_mtime_ns)
                if edited:
                    first = next(iter(payload['scores'].values()))
                    first['q1_b1']['score'] = 0.75
                exporters = ['export_lecture_presentation_package', 'export_marked_card_overlay_pdf',
                             'export_marked_card_image_pdf', 'export_teacher_commentary_overlay_pdf',
                             'upload_lecture_to_vps']
                for name in exporters:
                    setattr(g, name, Mock(side_effect=AssertionError('unexpected output')))
                g.save_results_to_database = Mock()
                g.status_var = Mock()
                window = Mock(winfo_exists=lambda: True)
                close = self.grading_window_callback('close_window', dict(
                    self=g, payload=payload, state={}, win=window, refresh_main_result_views=Mock()))
                self.assertTrue(close())
                window.destroy.assert_called_once()
                self.assertEqual((html.read_bytes(), html.stat().st_mtime_ns), before)
                self.assertEqual(g.load_subjective_score_payload()['scores'], payload['scores'])
                for name in exporters:
                    getattr(g, name).assert_not_called()

    def test_audit_metadata_changes_do_not_refresh_views_but_score_changes_do(self):
        g = self.gui
        self.single_regrade_fixture()
        callback = Mock()
        g._result_view_subscribers = [(Mock(winfo_exists=lambda: True), callback)]
        g.notify_result_views()
        g.subjective_config['updated_at'] = 'new save time'
        g.summary_data[0]['updated_at'] = 'new result time'
        g.notify_result_views()
        callback.assert_called_once()
        g.summary_data[0]['combined_raw_score'] = 8
        g.notify_result_views()
        self.assertEqual(callback.call_count, 2)

    def test_regrade_persists_recomputed_totals_to_database(self):
        g = self.gui
        self.single_regrade_fixture()
        g.current_folder_context = lambda: str(self.folder)
        g.subjective_items = lambda: [{'question_no': 1}]
        g.save_session_template_config_snapshot = Mock()
        g.update_summary_display = g.update_analysis_display = Mock()
        del g.refresh_main_result_views
        db = self.folder / 'results.db'
        with patch.object(db_session, 'DB_PATH', db):
            g.init_database()
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute('insert into sessions(id,name,kind,created_at,folder_path) values(?,?,?,?,?)',
                             ('test-a', 'A', 'grader', 'today', str(self.folder)))
            with patch('core.blank_recheck.get_blank_recheck_collector'):
                result = g.regrade_single_subjective_part('q1_b1', 'B')
            self.assertEqual(result['output_errors'], [])
            with closing(sqlite3.connect(db)) as conn:
                rows = dict(conn.execute('select file_name,payload_json from session_results where session_id=?', ('test-a',)))
            self.assertEqual(json.loads(rows['1.jpg'])['combined_raw_score'], 2)
            self.assertEqual(json.loads(rows['2.jpg'])['combined_raw_score'], 0.5)

    def test_other_pdf_exports_register_for_result_updates(self):
        from PIL import Image
        g = self.gui
        self.single_regrade_fixture()
        g.last_open_dir = str(self.folder)
        g.save_results_to_database = Mock()
        g.root = g.status_var = Mock()
        g.checked_overlay_entries = lambda: g.summary_data
        for name, value in [('overlay_offset_x_mm_var', 0), ('overlay_offset_y_mm_var', 0),
                            ('overlay_scale_percent_var', 100)]:
            setattr(g, name, Mock(get=lambda v=value: v))
        g.build_marked_answer_card_image = lambda *args, **kw: Image.new('RGB', (32, 32), 'white')
        g.fit_marked_image_to_a4_page = lambda image: image
        g.overlay_calibration_template_image_size = lambda side: (100, 100)
        g.teacher_commentary_mark_ops = lambda *args: []
        g.draw_overlay_ops_to_pdf_page = Mock()
        marked, teacher = self.folder / 'marked.pdf', self.folder / 'teacher.pdf'
        g.export_marked_card_image_pdf(pdf_path=str(marked), silent=True)
        g.export_teacher_commentary_overlay_pdf(pdf_path=str(teacher), silent=True)
        registered = g.load_linked_outputs()['outputs']
        self.assertEqual({item['kind'] for item in registered.values()}, {'marked_card', 'teacher_commentary'})
        for path in (marked, teacher):
            self.assertTrue(path.read_bytes().startswith(b'%PDF'))

    def test_lecture_and_overlay_share_tied_ranking(self):
        from core.lecture_presentation import build_lecture_presentation_data
        g = self.gui
        g.summary_data = [
            {'score_id': str(index), 'student_name': str(index), 'raw_score': score,
             'score': score, 'max_total_score': 100}
            for index, score in enumerate([90, 90, 10])]
        lecture = build_lecture_presentation_data(g.summary_data, session_name='A')
        self.assertEqual([row['rank'] for row in lecture['honor_roll']], [1, 1])
        self.assertEqual([g.overlay_student_rank(row) for row in g.summary_data], [1, 1, 3])


    def test_preview_refresh_failure_is_separate_from_grading_save(self):
        g = self.gui
        self.single_regrade_fixture()
        callback = Mock(side_effect=[ValueError('preview unavailable'), None])
        g._result_view_subscribers = [(Mock(winfo_exists=lambda: True), callback)]
        g.notify_result_views()
        self.assertIn('preview unavailable', g._last_result_view_errors[0])
        g.notify_result_views()
        self.assertEqual(g._last_result_view_errors, [])

    def test_single_blank_rejects_foreign_test_before_editing_answer(self):
        g = self.gui
        payload = self.single_regrade_fixture()
        payload['session_id'] = 'foreign'
        atomic_write_json(g.subjective_scores_path(), payload)
        original = g.subjective_scores_path().read_bytes()
        result = g.regrade_single_subjective_part('q1_b1', 'B')
        self.assertFalse(result['ok'])
        g.save_ocr_settings_for_parts.assert_not_called()
        self.assertEqual(g.subjective_scores_path().read_bytes(), original)

    def test_explicit_legacy_regrade_keeps_ocr_text_and_manual_marks(self):
        path = self.folder / 'scores.json'
        scores = {'1.jpg': {'q1_b1': {'score': 1, 'manual_graded': True,
                                     'auto_grade_expected': 'old answer', 'ocr_text': 'recognized'}}}
        payload = {'session_id': 'test-a', 'template_key': 't1',
                   'subjective_answer_signature': 'old',
                   'subjective_structure_signature': self.gui.subjective_structure_signature(),
                   'scores': scores}
        atomic_write_json(path, payload)
        self.gui.subjective_scores_path = lambda: path
        self.gui.prepare_subjective_payload_for_regrading(payload)
        self.assertIs(payload['scores'], scores)
        self.assertEqual(scores['1.jpg']['q1_b1']['score'], 1)
        self.assertTrue(scores['1.jpg']['q1_b1']['manual_graded'])
        self.assertEqual(scores['1.jpg']['q1_b1']['ocr_text'], 'recognized')
        self.assertEqual(payload['subjective_answer_signature'], self.gui.subjective_answer_signature())
        self.assertGreaterEqual(len(list(self.folder.glob('scores.before_rules_change_*.json'))), 1)

    def test_regrade_cannot_reuse_ocr_after_structure_change(self):
        payload = {'session_id': 'test-a', 'subjective_answer_signature': 'old',
                   'subjective_structure_signature': 'other-paper', 'scores': {'1.jpg': {'q1_b1': {'score': 1}}}}
        before = copy.deepcopy(payload)
        with self.assertRaises(ValueError):
            self.gui.prepare_subjective_payload_for_regrading(payload)
        self.assertEqual(payload, before)

    def test_changed_source_image_blocks_old_overlay(self):
        path = self.folder / '1.jpg'
        path.write_bytes(b'old scan')
        entry = {'file': '1.jpg', 'input_path': str(path)}
        entry['_source_image_signature'] = self.gui.source_image_signature(entry)
        self.gui.summary_data = [entry]
        self.gui.validate_overlay_context()
        path.write_bytes(b'new different scan')
        with self.assertRaises(ValueError):
            self.gui.validate_overlay_context()

    def test_direct_line_files_are_separate_per_test(self):
        first = self.gui.direct_subjective_line_config_path()
        self.gui.current_session['id'] = 'test-b'
        self.assertNotEqual(first, self.gui.direct_subjective_line_config_path())

    def test_live_window_or_automation_blocks_context_switch(self):
        self.gui.root = Mock()
        self.gui._context_windows = [Mock(winfo_exists=lambda: True)]
        with patch('enhanced_answer_card_gui_stats.messagebox.showwarning'):
            self.assertFalse(self.gui.context_change_allowed())
            self.gui._context_windows = []
            self.gui._automation_running = True
            self.assertFalse(self.gui.context_change_allowed())
            self.gui._automation_running = False
            self.assertTrue(self.gui.context_change_allowed())

    def test_load_without_snapshot_never_fills_from_previous_scheme(self):
        g = self.gui
        db = self.folder / 'test.db'
        shared = self.folder / 'old-scheme.json'
        atomic_write_json(shared, {'answer_key': {'1': ['B']}})
        g.loaded_answer_key_scheme_path = str(shared)
        g.template_entries = {'t1': {}}
        g.rosters = {}
        g.is_direct_paper_choice_template = lambda: False
        g.subjective_items = lambda: []
        for name in ('load_session_template_config_snapshot', 'load_session_answer_key_snapshot'):
            setattr(g, name, Mock(return_value=False))
        for name in ('roster_class_var', 'preview_canvas', 'preview_label_var', 'detail_text',
                     'summary_text', 'analysis_text', 'wrong_detail_text', 'status_var'):
            setattr(g, name, Mock())
        for name in ('set_folder_context', 'display_single_result', 'update_session_hint',
                     'update_summary_display', 'update_analysis_display', 'save_ui_settings',
                     'fill_missing_answers_from_scheme_file'):
            setattr(g, name, Mock())
        with patch.object(db_session, 'DB_PATH', db):
            g.init_database()
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute('insert into sessions(id,name,kind,created_at,folder_path,template_key) values(?,?,?,?,?,?)',
                             ('test-b', 'B', 'grader', 'today', str(self.folder), 't1'))
                for filename in ('1.jpg', '10.jpg', '2.jpg'):
                    conn.execute('insert into session_results(session_id,file_name,payload_json,created_at) values(?,?,?,?)',
                                 ('test-b', filename, json.dumps({'file': filename}), 'today'))
            g.load_session_results('test-b')
        self.assertEqual(g.answer_key, {})
        self.assertIsNone(g.loaded_answer_key_scheme_path)
        g.fill_missing_answers_from_scheme_file.assert_not_called()
        self.assertEqual([e['file'] for e in g.summary_data], ['1.jpg', '2.jpg', '10.jpg'])
        self.assertTrue(all(e['_session_id'] == 'test-b' for e in g.summary_data))

    def test_foreign_or_unowned_legacy_scores_are_not_imported(self):
        g = self.gui
        scan = str(self.folder / '1.jpg')
        g.summary_data = [{'input_path': scan}]
        legacy = self.folder / 'legacy.json'
        g.legacy_subjective_score_paths = lambda: [legacy]
        for owner in ('test-b', None):
            atomic_write_json(legacy, {'session_id': owner, 'scores': {scan: {'q1_b1': {'score': 1}}}})
            payload = {'scores': {}}
            self.assertEqual(g.merge_legacy_subjective_scores(payload, self.folder / 'own.json'), 0)
            self.assertEqual(payload['scores'], {})

    def test_legacy_scores_require_matching_structure_and_answer(self):
        g = self.gui
        scan = str(self.folder / '1.jpg')
        g.summary_data = [{'input_path': scan}]
        legacy = self.folder / 'legacy.json'
        g.legacy_subjective_score_paths = lambda: [legacy]
        data = {'session_id': 'test-a', 'template_key': 't1',
                'subjective_answer_signature': g.subjective_answer_signature(),
                'subjective_structure_signature': g.subjective_structure_signature(),
                'scores': {scan: {'q1_b1': {'score': 1}}}}
        atomic_write_json(legacy, {**data, 'subjective_structure_signature': 'other-paper'})
        self.assertEqual(g.merge_legacy_subjective_scores({'scores': {}}, self.folder / 'own.json'), 0)
        atomic_write_json(legacy, data)
        self.assertEqual(g.merge_legacy_subjective_scores({'scores': {}}, self.folder / 'own.json'), 1)

    def test_grading_does_not_reload_changed_shared_scheme(self):
        g = self.gui
        shared = self.folder / 'shared.json'
        atomic_write_json(shared, {'answer_key': {'1': ['B']}})
        g.loaded_answer_key_scheme_path = str(shared)
        g.load_answer_scheme_file_into_state = Mock(side_effect=AssertionError('shared reload'))
        self.assertTrue(g.ensure_answer_scheme_loaded_for_grading())
        self.assertEqual(g.extract_answer_map(), {1: ['A']})
        g.load_answer_scheme_file_into_state.assert_not_called()

    def test_session_ocr_edit_does_not_modify_shared_scheme(self):
        g = self.gui
        path = self.folder / 'shared.json'
        path.write_text('{"original":true}')
        g.loaded_answer_key_scheme_path = str(path)
        g.save_session_answer_key_snapshot = Mock()
        g.get_subjective_ocr_settings_for_scheme = lambda: {'q1': {}}
        self.assertEqual(g.sync_loaded_answer_scheme_ocr_settings(), 1)
        g.save_session_answer_key_snapshot.assert_called_once()
        self.assertEqual(path.read_text(), '{"original":true}')

    def test_snapshot_rejects_other_test_and_corruption(self):
        g = self.gui
        path = self.folder / 'answers.json'
        g.session_answer_key_path = lambda sid=None: path
        for data in ('{broken', '{"session_id":"test-b","template_key":"t1"}'):
            path.write_text(data)
            with self.assertRaises(ValueError):
                g.load_session_answer_key_snapshot()
        self.assertEqual(g.extract_answer_map(), {1: ['A']})

    def test_structure_snapshot_rejects_other_test(self):
        g = self.gui
        path = self.folder / 'structure.json'
        g.session_template_config_path = lambda sid=None: path
        atomic_write_json(path, {'items': [], '_session_snapshot': {'session_id': 'test-b'}})
        with self.assertRaises(ValueError):
            g.load_session_template_config_snapshot()

    def test_same_name_scan_in_other_class_rejected(self):
        self.gui.summary_data = [{'file': '1.jpg', 'input_path': str(self.folder / 'other-class' / '1.jpg')}]
        with self.assertRaises(ValueError):
            self.gui.validate_result_ownership(self.gui.summary_data)

    def test_other_session_result_in_same_folder_rejected(self):
        with self.assertRaises(ValueError):
            self.gui.validate_result_ownership([{'file': '1.jpg', '_session_id': 'test-b'}])

    def test_scheme_switch_invalidates_overlay(self):
        g = self.gui
        g.summary_data = [{'file': '1.jpg', '_result_context_signature': g.result_context_signature()}]
        g.validate_overlay_context()
        g.set_global_answer_map({1: ['B']})
        with self.assertRaises(ValueError):
            g.validate_overlay_context()

    def test_overlay_sort_is_natural_and_missing_front_stops_batch(self):
        g = self.gui
        for name in ['10.jpg', '1.jpg', '2.jpg']:
            (self.folder / name).write_bytes(b'fixture')
        g.summary_data = [{'file': name, 'input_path': str(self.folder / name)}
                          for name in ['1.jpg', '10.jpg', '2.jpg']]
        self.assertEqual([e['file'] for e in g.checked_overlay_entries()], ['1.jpg', '2.jpg', '10.jpg'])
        (self.folder / '2.jpg').unlink()
        with self.assertRaises(ValueError):
            g.checked_overlay_entries()

    def test_partial_back_pages_cannot_shift_print_order(self):
        g = self.gui
        for name in ['1.jpg', '2.jpg', 'back.jpg']:
            (self.folder / name).write_bytes(b'fixture')
        g.summary_data = [{'file': '1.jpg', 'input_path': str(self.folder / '1.jpg'),
                           'side_files': {'back': str(self.folder / 'back.jpg')}},
                          {'file': '2.jpg', 'input_path': str(self.folder / '2.jpg')}]
        with self.assertRaises(ValueError):
            g.checked_overlay_entries()

    def test_job_id_changes_with_test_answer_structure_or_scan(self):
        g = self.gui
        scan = self.folder / '1.jpg'
        scan.write_bytes(b'old')
        g.summary_data = [{'input_path': str(scan)}]
        first = g.manual_web_job_id()
        g.current_session['manual_web_job_id'] = 'foreign-job'
        self.assertEqual(g.manual_web_job_candidates(), [first])
        scan.write_bytes(b'new-longer')
        self.assertNotEqual(g.manual_web_job_id(), first)
        second = g.manual_web_job_id()
        g.loaded_answer_key_scheme_name = 'paper-b'
        self.assertNotEqual(g.manual_web_job_id(), second)
        second = g.manual_web_job_id()
        g.current_session['id'] = 'test-b'
        self.assertNotEqual(g.manual_web_job_id(), second)

    def test_duplicate_folder_binding_requires_explicit_test_choice(self):
        db = self.folder / 'test.db'
        with patch.object(db_session, 'DB_PATH', db):
            self.gui.init_database()
            with closing(sqlite3.connect(db)) as conn, conn:
                for sid in ('a', 'b'):
                    conn.execute('insert into sessions(id,name,kind,created_at,folder_path) values(?,?,?,?,?)',
                                 (sid, sid, 'grader', 'today', str(self.folder)))
            with self.assertRaises(ValueError):
                self.gui.find_saved_session_for_folder(self.folder)

    def test_direct_snapshot_missing_image_does_not_reuse_previous_image(self):
        g = self.gui
        g.is_direct_paper_choice_template = lambda: True
        old = self.folder / 'template.jpg'
        old.write_bytes(b'previous-paper')
        g.current_template_paths = {'answer_config': self.folder / 'answer.json',
                                    'marker_config': self.folder / 'marker.json', 'template_image': old}
        structure = {'answer_config': {'mode': 'direct_paper_choice',
                                       'template_image': {'path': str(self.folder / 'missing.jpg')}}}
        with self.assertRaises(ValueError):
            g.apply_direct_paper_structure_from_scheme(structure)
        self.assertEqual(old.read_bytes(), b'previous-paper')

    def test_pdf_render_failure_preserves_existing_outputs(self):
        g = self.gui
        g.summary_data = [{'file': '1.jpg'}, {'file': '2.jpg'}]
        g.checked_overlay_entries = lambda: g.summary_data
        g.apply_subjective_scores_to_entries = Mock()
        g.ask_overlay_pdf_adjustments = lambda: True
        g.last_open_dir = str(self.folder)
        g.root = Mock()
        g.status_var = Mock()
        for name in ('overlay_offset_x_mm_var', 'overlay_offset_y_mm_var', 'overlay_scale_percent_var'):
            setattr(g, name, Mock(get=lambda: 0))
        g.build_marked_answer_card_overlay_ops = Mock(side_effect=[([], (100, 100)), ValueError('bad scan')])
        g.draw_overlay_ops_to_pdf_page = Mock()
        target = self.folder / 'output.pdf'
        target.write_bytes(b'previous-valid-pdf')
        with patch.object(pdf_overlay.filedialog, 'asksaveasfilename', return_value=str(target)), \
             patch.object(pdf_overlay.messagebox, 'showerror') as error:
            g.export_marked_card_overlay_pdf()
        error.assert_called_once()
        self.assertEqual(target.read_bytes(), b'previous-valid-pdf')
        self.assertEqual(len(list(self.folder.glob('.overlay-*'))), 0)


if __name__ == '__main__':
    unittest.main()
