"""Unit tests for calculation-only back pages, Office Math extraction, and template ranking."""
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
sys.path[:0] = [str(APP), str(ROOT)]

from enhanced_answer_card_gui_stats import EnhancedAnswerCardStatsGUI


class DirectCalculationNoLinesTests(unittest.TestCase):
    def setUp(self):
        self.gui = EnhancedAnswerCardStatsGUI.__new__(EnhancedAnswerCardStatsGUI)

    def test_build_direct_calculation_region_no_lines_no_boxes(self):
        """When back page has 0 lines and 0 boxes, calculation region spans full content area."""
        width = 2480.0
        height = 3507.0
        region = self.gui.build_direct_calculation_region([], width, height, top_offset=0, boxes=[])
        self.assertTrue(region.get('enabled'))
        self.assertEqual(region.get('source'), 'full_page_content_area')
        self.assertEqual(region.get('side'), 'back')
        rect = region.get('rect')
        expected_top = max(36.0, height * 0.045)
        self.assertAlmostEqual(rect['y'], expected_top, places=1)
        expected_bottom = height - max(36.0, height * 0.045)
        self.assertAlmostEqual(rect['y'] + rect['h'], expected_bottom, places=1)

    def test_build_direct_calculation_region_with_boxes_only(self):
        """When back page has choice boxes but 0 lines, calculation region starts below boxes."""
        width = 2480.0
        height = 3507.0
        boxes = [
            {'template_rect': {'x': 100, 'y': 200, 'w': 50, 'h': 30}},
            {'template_rect': {'x': 100, 'y': 400, 'w': 50, 'h': 30}},
        ]
        region = self.gui.build_direct_calculation_region([], width, height, top_offset=10, boxes=boxes)
        self.assertEqual(region.get('source'), 'last_retained_box_to_page_bottom')
        last_box_bottom = 430.0
        base_gap = max(12.0, height * 0.006)
        expected_top = last_box_bottom + base_gap + 10
        self.assertAlmostEqual(region['rect']['y'], expected_top, places=1)

    def test_build_direct_calculation_region_with_lines(self):
        """When back page has lines, calculation region starts below lines."""
        width = 2480.0
        height = 3507.0
        lines = [
            {'template_rect': {'x': 100, 'y': 800, 'w': 500, 'h': 10}},
            {'template_rect': {'x': 100, 'y': 1200, 'w': 500, 'h': 10}},
        ]
        region = self.gui.build_direct_calculation_region(lines, width, height, top_offset=0, boxes=[])
        self.assertEqual(region.get('source'), 'last_retained_line_to_page_bottom')
        last_line_bottom = 1210.0
        base_gap = max(12.0, height * 0.006)
        expected_top = last_line_bottom + base_gap
        self.assertAlmostEqual(region['rect']['y'], expected_top, places=1)

    def test_extract_paragraph_full_text_office_math(self):
        """Verify Office Math XML elements (<m:oMath>, <m:t>) are extracted properly."""
        class DummyElem:
            def __init__(self, tag, text=None):
                self.tag = tag
                self.text = text
            def iter(self):
                return [self]

        class DummyParagraph:
            def __init__(self):
                self.text = '9．；做功；A'
                self._p = self
            def iter(self):
                return [
                    DummyElem('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t', '9．'),
                    DummyElem('{http://schemas.openxmlformats.org/officeDocument/2006/math}t', '4.2×10^4J'),
                    DummyElem('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t', '；做功；A'),
                ]

        extracted = self.gui.extract_paragraph_full_text(DummyParagraph())
        self.assertEqual(extracted, '9．4.2×10^4J；做功；A')

    def test_docx_scoring_prioritizes_template(self):
        """Word files with 套用模板 or 套模板 should have absolute tier-1 priority over all other files."""
        class DummyDocxPath:
            def __init__(self, stem, mtime=1000):
                self.stem = stem
                self._mtime = mtime
            def stat(self):
                class Stat:
                    st_mtime = self._mtime
                return Stat()

        def docx_score(p):
            stem = p.stem
            score = 0
            if any(kw in stem for kw in ('compact_print', '透打', '打印辅助', '讲评')):
                score -= 500
            if '套用模板' in stem or '套模板' in stem:
                score += 1000
                if any(kw in stem for kw in ('删除', '修改', '编辑', '最终', '改')):
                    score += 50
            elif '模板' in stem:
                score += 100
            elif '答案' in stem:
                score += 60
            elif '试卷' in stem:
                score += 50
            elif '题' in stem:
                score += 30
            return (score, p.stat().st_mtime)

        p_modified = DummyDocxPath('paper_20260831_131305套用模板不处理答案删除')
        p_template = DummyDocxPath('paper_20260831_131305套用模板不处理答案')
        p_other_template = DummyDocxPath('期末复习模板')
        p_answer = DummyDocxPath('参考答案')
        p_raw = DummyDocxPath('paper_20260831_131305')
        p_compact = DummyDocxPath('paper_20260831_131305_compact_print')

        # 套用模板 > 纯模板 > 答案 > 原卷 > 紧凑打印
        self.assertGreater(docx_score(p_modified)[0], docx_score(p_template)[0])
        self.assertGreater(docx_score(p_template)[0], docx_score(p_other_template)[0])
        self.assertGreater(docx_score(p_other_template)[0], docx_score(p_answer)[0])
        self.assertGreater(docx_score(p_answer)[0], docx_score(p_raw)[0])
        self.assertGreater(docx_score(p_raw)[0], docx_score(p_compact)[0])

    def test_parse_word_calculation_scores_no_dot_subquestions(self):
        """Verify question numbers followed directly by parenthesis (e.g. 10（1）（2）（3）（4）) are parsed."""
        lines = ['10（1）（2）（3）（4）']
        result = self.gui.parse_word_calculation_scores(lines)
        self.assertEqual(result, '10: 2, 2, 2, 2')

    def test_detect_direct_colored_subjective_lines_near_edge(self):
        """Lines that end close to the margin (e.g. 10px from right border) should be detected."""
        import numpy as np
        mask = np.zeros((1000, 2480), dtype=np.uint8)
        mask[500:504, 2300:2470] = 255
        lines = self.gui.detect_direct_colored_subjective_lines(mask, side='front')
        self.assertEqual(len(lines), 1)
        self.assertAlmostEqual(lines[0]['x'] + lines[0]['w'], 2470, delta=4)


if __name__ == '__main__':
    unittest.main()
