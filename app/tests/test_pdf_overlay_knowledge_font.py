import unittest
import numpy as np
from core.pdf_overlay import PdfOverlayExportMixin


class DummyOverlayApp(PdfOverlayExportMixin):
    def __init__(self):
        self.knowledge_identity_payload = {
            'rows': [
                {'题号': '1', '知识点编号': '1.1.1'},
                {'题号': '15', '知识点编号': '2.1.1'},
                {'题号': '24', '知识点编号': '10.2.1'},
                {'题号': '25', '知识点编号': '10.3.1'},
            ]
        }
        self.answer_config = {
            'zones': [{'zone_name': 'choice', 'side': 'front'}]
        }

    def is_direct_paper_choice_template(self):
        return False


class TestPdfOverlayKnowledgeFont(unittest.TestCase):
    def setUp(self):
        self.app = DummyOverlayApp()
        self.entry = {
            'score_info': {
                'detailed_scores': {
                    'choice': {'details': {'1': {'status': 'wrong'}}}
                }
            },
            'answers': {
                'choice': {'1': {'projected_points': {'A': {'x': 100, 'y': 200}}}}
            },
            'subjective': {
                'scores': {'15-1': {'score': 0}}
            },
            'calculation_scores': {},
        }
        self.app.iter_subjective_parts = lambda: [
            {'part_id': '15-1', 'question_no': '15', 'score': 2, 'side': 'front', 'rect': {'x': 50, 'y': 300, 'w': 100, 'h': 40}}
        ]
        self.app.project_template_rect = lambda m, r: (r['x'], r['y'], r['x'] + r['w'], r['y'] + r['h'])
        self.app.calculation_score_rows_for_entry = lambda e: [
            {'question_no': '24', 'has_score': True, 'raw_score': 0, 'max_score': 10},
            {'question_no': '25', 'has_score': True, 'raw_score': 0, 'max_score': 10},
        ]

    def test_knowledge_point_font_size_is_doubled_to_56(self):
        matrix = np.eye(3)
        front_ops = self.app.collect_wrong_knowledge_mark_ops(self.entry, matrix=matrix, side='front')
        self.assertEqual(len(front_ops), 2)
        for op in front_ops:
            self.assertEqual(op['font_size'], 56, f"Expected font_size 56 (2x), got {op.get('font_size')}")
            self.assertTrue(op.get('prefer_chinese_font'))

        # Check objective op
        obj_op = front_ops[0]
        self.assertEqual(obj_op['text'], '1.1.1')
        # Bottom of text should be 200 - 50 = 150 -> top is 150 - 56 = 94
        self.assertEqual(obj_op['xy'], (100.0, 94.0))

        # Check subjective op
        subj_op = front_ops[1]
        self.assertEqual(subj_op['text'], '2.1.1')
        # Bottom of text should be 300 - 10 = 290 -> top is 290 - 56 = 234
        self.assertEqual(subj_op['xy'], (50, 234.0))

        # Check calculation ops on back side
        back_ops = self.app.collect_wrong_knowledge_mark_ops(self.entry, matrix=matrix, side='back')
        self.assertEqual(len(back_ops), 2)
        self.assertEqual(back_ops[0]['font_size'], 56)
        self.assertEqual(back_ops[1]['font_size'], 56)
        # Verify line spacing matches doubled font size (difference in y should be 68)
        y0 = back_ops[0]['xy'][1]
        y1 = back_ops[1]['xy'][1]
        self.assertEqual(round(y1 - y0), 68)


if __name__ == '__main__':
    unittest.main()
