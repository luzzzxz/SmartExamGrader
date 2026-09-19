import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock
from collections import defaultdict
import numpy as np

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP.parent)]

from core.pdf_overlay import PdfOverlayExportMixin

class DummyApp(PdfOverlayExportMixin):
    def __init__(self):
        self.summary_data = []
        self.current_template_mode = 'direct_paper'
        self.overlay_offset_x_mm_var = MagicMock(get=lambda: 0.0)
        self.overlay_offset_y_mm_var = MagicMock(get=lambda: 0.0)
        self.overlay_scale_percent_var = MagicMock(get=lambda: 100.0)
        self.overlay_back_offset_x_mm_var = MagicMock(get=lambda: 0.0)
        self.overlay_back_offset_y_mm_var = MagicMock(get=lambda: 0.0)
        self.overlay_back_scale_percent_var = MagicMock(get=lambda: 100.0)
        self.overlay_calibration_matrices = {}
        self.status_var = MagicMock()
        self.fake_payload = {
            'scores': {},
            'calculation_annotations': {},
        }

    def load_subjective_score_payload(self):
        return self.fake_payload

    def subjective_entry_key(self, entry):
        return entry.get('file') or entry.get('score_id')

    def is_direct_paper_choice_template(self):
        return True

    def is_mixed_objective_subjective_template(self):
        return False

    def direct_calculation_region_config(self):
        return {
            'enabled': True,
            'rect': {'x': 100, 'y': 200, 'w': 1000, 'h': 1200}
        }

    def calculation_question_config(self):
        return [
            {
                'question_no': '14',
                'sub_scores': [
                    {'sub_no': '1', 'score': 3.0},
                    {'sub_no': '2', 'score': 4.0},
                    {'sub_no': '3', 'score': 3.0},
                ]
            }
        ]

    def project_template_rect(self, matrix, rect):
        x = float(rect['x'])
        y = float(rect['y'])
        w = float(rect['w'])
        h = float(rect['h'])
        return x, y, x + w, y + h

    def build_teacher_objective_analysis(self):
        return {}

    def build_teacher_subjective_analysis(self):
        return {
            'calc_14_1': {
                'part': {'question_no': '14', 'sub_no': '1', 'kind': 'calculation'},
                'average_score': 2.0,
                'max_score': 3.0,
            },
            'calc_14_2': {
                'part': {'question_no': '14', 'sub_no': '2', 'kind': 'calculation'},
                'average_score': 2.5,
                'max_score': 4.0,
            },
            'calc_14_3': {
                'part': {'question_no': '14', 'sub_no': '3', 'kind': 'calculation'},
                'average_score': 1.5,
                'max_score': 3.0,
            }
        }

    def objective_template_anchors_for_side(self, side):
        return []

    def teacher_header_mark_ops(self, image_size):
        return []

    def teacher_accuracy_color(self, acc):
        return (0, 150, 0)

    def plain_score_text(self, val):
        return f"{float(val):g}"

    def normalized_question_number_key(self, qno):
        return str(qno)


class TestCalculationAnnotationsAndCommentary(unittest.TestCase):
    def setUp(self):
        self.app = DummyApp()

    def test_collect_calculation_annotations_ops(self):
        entry = {'file': 'student_01.png', 'score_id': '01'}
        self.app.fake_payload['calculation_annotations'] = {
            'student_01.png': {
                '14': [
                    {'type': 'line', 'points': [(0.1, 0.2), (0.3, 0.25)], 'color': '#DC2626', 'width': 3},
                    {'type': 'check', 'u': 0.5, 'v': 0.3},
                    {'type': 'cross', 'u': 0.6, 'v': 0.4},
                    {'type': 'stamp', 'u': 0.4, 'v': 0.5, 'text': '没有原始公式'},
                    {'type': 'stamp', 'u': 0.4, 'v': 0.6, 'text': '没有单位'},
                ]
            }
        }

        ops = self.app.collect_calculation_annotations_mark_ops(entry, matrix=None, side='back')
        self.assertTrue(len(ops) >= 5, f"Expected at least 5 ops, got {len(ops)}")

        types = [op['type'] for op in ops]
        self.assertIn('line', types)
        self.assertIn('objective_symbol', types)
        self.assertIn('text', types)

        text_ops = [op for op in ops if op['type'] == 'text']
        stamp_texts = [op['text'] for op in text_ops]
        self.assertIn('[没有原始公式]', stamp_texts)
        self.assertIn('[没有单位]', stamp_texts)

        line_op = [op for op in ops if op['type'] == 'line'][0]
        x1, y1, x2, y2 = line_op['points']
        self.assertAlmostEqual(x1, 100 + 0.1 * 1000)
        self.assertAlmostEqual(y1, 200 + 0.2 * 1200)

    def test_summarize_calculation_error_stamps_and_teacher_commentary(self):
        self.app.fake_payload['calculation_annotations'] = {
            'student_01.png': {
                '14': [
                    {'type': 'stamp', 'text': '没有原始公式'},
                    {'type': 'stamp', 'text': '没有单位'},
                ]
            },
            'student_02.png': {
                '14': [
                    {'type': 'stamp', 'text': '没有原始公式'},
                    {'type': 'stamp', 'text': '没有下标'},
                ]
            },
            'student_03.png': {
                '14': [
                    {'type': 'stamp', 'text': '没有原始公式'},
                    {'type': 'stamp', 'text': '公式错误'},
                ]
            },
            'student_04.png': {
                '14': [
                    {'type': 'stamp', 'text': '没有单位'},
                ]
            },
        }

        stats = self.app.summarize_calculation_error_stamps()
        q14_stats = stats.get('14', {})
        self.assertEqual(q14_stats.get('没有原始公式'), 3)
        self.assertEqual(q14_stats.get('没有单位'), 2)
        self.assertEqual(q14_stats.get('没有下标'), 1)
        self.assertEqual(q14_stats.get('公式错误'), 1)

        ops = self.app.teacher_commentary_mark_ops(side='back', image_size=(2480, 3508))
        text_contents = [op.get('text') for op in ops if op.get('type') == 'text']
        
        avg_found = any('14题 平均' in t for t in text_contents)
        self.assertTrue(avg_found, f"Average score text not found in: {text_contents}")

        err_found = any('常见错误:' in t and '没有原始公式(3人)' in t and '没有单位(2人)' in t for t in text_contents)
        self.assertTrue(err_found, f"Error summary text not found in: {text_contents}")


if __name__ == '__main__':
    unittest.main()
