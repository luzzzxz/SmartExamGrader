import sys
from pathlib import Path
TESTS_DIR = Path(__file__).resolve().parent
APP_DIR = TESTS_DIR.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import unittest
import copy
from core.pdf_overlay import (
    PdfOverlayExportMixin,
    DEFAULT_OVERLAY_GRADE_RANK_THRESHOLDS,
    DEFAULT_OVERLAY_GRADE_RANK_REMAINDER,
    DEFAULT_OVERLAY_GRADE_THRESHOLDS,
)


class DummyRankOverlayApp(PdfOverlayExportMixin):
    def __init__(self, summary_data=None, grade_mode='rank', rank_thresholds=None, remainder='C', use_grade=True):
        self.summary_data = summary_data or []
        self.overlay_grade_mode = grade_mode
        self.overlay_grade_rank_thresholds = (
            rank_thresholds if rank_thresholds is not None else copy.deepcopy(DEFAULT_OVERLAY_GRADE_RANK_THRESHOLDS)
        )
        self.overlay_grade_rank_remainder = remainder
        self.overlay_grade_thresholds = copy.deepcopy(DEFAULT_OVERLAY_GRADE_THRESHOLDS)
        self._use_grade = use_grade

    def overlay_uses_grade(self):
        return self._use_grade


class TestOverlayGradeByRank(unittest.TestCase):
    def test_default_rank_thresholds_distribution(self):
        # 50 students: scores 100, 99, ..., 51
        cohort = [
            {'score_id': f's_{i:02d}', 'student_name': f'Student_{i:02d}', 'combined_score': 100 - i + 1}
            for i in range(1, 51)
        ]
        app = DummyRankOverlayApp(summary_data=cohort, grade_mode='rank')

        # 1-5: A+
        for i in range(5):
            entry = cohort[i]
            self.assertEqual(app.overlay_grade_by_rank(entry), 'A+', f"Student {i+1} should be A+")
            self.assertEqual(app.overlay_grade_for_entry(entry), 'A+')
            self.assertEqual(app.printed_total_score_text(entry), 'A+')

        # 6-15: A
        for i in range(5, 15):
            entry = cohort[i]
            self.assertEqual(app.overlay_grade_by_rank(entry), 'A', f"Student {i+1} should be A")
            self.assertEqual(app.overlay_grade_for_entry(entry), 'A')

        # 16-30: B+
        for i in range(15, 30):
            entry = cohort[i]
            self.assertEqual(app.overlay_grade_by_rank(entry), 'B+', f"Student {i+1} should be B+")
            self.assertEqual(app.overlay_grade_for_entry(entry), 'B+')

        # 31-40: B
        for i in range(30, 40):
            entry = cohort[i]
            self.assertEqual(app.overlay_grade_by_rank(entry), 'B', f"Student {i+1} should be B")
            self.assertEqual(app.overlay_grade_for_entry(entry), 'B')

        # 41-50: C (remainder)
        for i in range(40, 50):
            entry = cohort[i]
            self.assertEqual(app.overlay_grade_by_rank(entry), 'C', f"Student {i+1} should be C")
            self.assertEqual(app.overlay_grade_for_entry(entry), 'C')

    def test_ties_competition_ranking(self):
        # Tied for 5th place: 4 students with 100, 2 students with 95, 1 student with 90
        cohort = [
            {'student_name': 'S1', 'combined_score': 100},
            {'student_name': 'S2', 'combined_score': 100},
            {'student_name': 'S3', 'combined_score': 100},
            {'student_name': 'S4', 'combined_score': 100},
            {'student_name': 'S5', 'combined_score': 95},  # tied 5th
            {'student_name': 'S6', 'combined_score': 95},  # tied 5th
            {'student_name': 'S7', 'combined_score': 90},  # 7th
        ]
        app = DummyRankOverlayApp(summary_data=cohort, grade_mode='rank')

        # S1..S4: rank 1 <= 5 -> A+
        for i in range(4):
            self.assertEqual(app.overlay_grade_by_rank(cohort[i]), 'A+')

        # S5 and S6: 4 students have higher score, so rank is 5 <= 5 -> both get A+
        self.assertEqual(app.overlay_grade_by_rank(cohort[4]), 'A+')
        self.assertEqual(app.overlay_grade_by_rank(cohort[5]), 'A+')

        # S7: 6 students have higher score, so rank is 7 > 5, <= 15 -> gets A
        self.assertEqual(app.overlay_grade_by_rank(cohort[6]), 'A')

    def test_fallback_to_score_mode(self):
        cohort = [
            {'student_name': 'S1', 'combined_score': 65},  # Rank 1, but score is 65 (D in score mode)
        ]
        app = DummyRankOverlayApp(summary_data=cohort, grade_mode='score')
        # In score mode, 65 is D (60 <= score < 70)
        self.assertEqual(app.overlay_grade_for_entry(cohort[0]), 'D')

        # But if switched to rank mode, rank 1 <= 5 -> A+
        app.overlay_grade_mode = 'rank'
        self.assertEqual(app.overlay_grade_for_entry(cohort[0]), 'A+')

    def test_custom_rank_thresholds_and_remainder(self):
        cohort = [
            {'student_name': 'S1', 'combined_score': 100},
            {'student_name': 'S2', 'combined_score': 90},
            {'student_name': 'S3', 'combined_score': 80},
            {'student_name': 'S4', 'combined_score': 70},
        ]
        custom_thresholds = {
            'A+': 1,
            'A': 2,
            'B+': None,
            'B': None,
            'C+': None,
            'C': None,
            'D': None,
        }
        app = DummyRankOverlayApp(
            summary_data=cohort,
            grade_mode='rank',
            rank_thresholds=custom_thresholds,
            remainder='D',
        )
        self.assertEqual(app.overlay_grade_by_rank(cohort[0]), 'A+')  # rank 1
        self.assertEqual(app.overlay_grade_by_rank(cohort[1]), 'A')   # rank 2
        self.assertEqual(app.overlay_grade_by_rank(cohort[2]), 'D')   # rank 3 -> remainder D
        self.assertEqual(app.overlay_grade_by_rank(cohort[3]), 'D')   # rank 4 -> remainder D

    def test_teacher_header_mark_ops_rank(self):
        cohort = [
            {'score_id': '1', 'student_name': '张三', 'combined_score': 100},
            {'score_id': '2', 'student_name': '李四', 'combined_score': 90},
            {'score_id': '3', 'student_name': '王五', 'combined_score': 50},
        ]
        app = DummyRankOverlayApp(summary_data=cohort, grade_mode='rank')
        ops = app.teacher_header_mark_ops((1000, 1000))
        self.assertTrue(len(ops) >= 2)
        header_text = ops[0]['text']
        self.assertIn('平均等级', header_text)
        items_text = ops[1]['text']
        self.assertIn('张三 A+', items_text)
        self.assertIn('李四 A', items_text)

    def test_overlay_student_rank(self):
        cohort = [
            {'student_name': 'S1', 'combined_score': 100},
            {'student_name': 'S2', 'combined_score': 95},
            {'student_name': 'S3', 'combined_score': 95},
            {'student_name': 'S4', 'combined_score': 80},
        ]
        app = DummyRankOverlayApp(summary_data=cohort, grade_mode='rank')
        self.assertEqual(app.overlay_student_rank(cohort[0]), 1)
        self.assertEqual(app.overlay_student_rank(cohort[1]), 2)
        self.assertEqual(app.overlay_student_rank(cohort[2]), 2)  # tie
        self.assertEqual(app.overlay_student_rank(cohort[3]), 4)

    def test_collect_total_score_header_ops_in_rank_mode(self):
        cohort = [
            {'student_name': 'S1', 'combined_score': 100},
            {'student_name': 'S2', 'combined_score': 90},
        ]
        app = DummyRankOverlayApp(summary_data=cohort, grade_mode='rank', use_grade=True)
        app.student_id_mark_region_rect = lambda _m: None
        ops = app.collect_total_score_header_ops(cohort[0], (1000, 1000), None)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]['text'], 'A+')
        self.assertTrue(ops[0]['prefer_chinese_font'])


if __name__ == '__main__':
    unittest.main()
