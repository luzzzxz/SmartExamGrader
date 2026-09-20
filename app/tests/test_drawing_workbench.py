import sys
from pathlib import Path
import unittest
import tempfile
import json
from unittest.mock import MagicMock

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP.parent)]

import enhanced_answer_card_gui_stats as main_mod
from core.pdf_overlay import PdfOverlayExportMixin


class TestDrawingWorkbenchComments(unittest.TestCase):
    def test_default_categories(self):
        """测试默认作图题常见错误分类完整性"""
        expected_categories = [
            '光学',
            '力的示意图',
            '杠杆作图',
            '电路实物连接',
            '电路图',
            '滑轮',
            '通用与规范',
        ]
        self.assertEqual(list(main_mod.DEFAULT_DRAWING_ERROR_COMMENTS.keys()), expected_categories)
        for cat in expected_categories:
            items = main_mod.DEFAULT_DRAWING_ERROR_COMMENTS[cat]
            self.assertIsInstance(items, list)
            self.assertTrue(len(items) > 0, f"{cat} 分类应该有默认批语")

    def test_load_and_save_drawing_error_comments(self):
        """测试即时添加批语与持久化存储"""
        temp_dir = Path(tempfile.mkdtemp())
        orig_path = main_mod.DRAWING_ERROR_COMMENTS_PATH
        try:
            test_comments_path = temp_dir / "test_drawing_comments.json"
            main_mod.DRAWING_ERROR_COMMENTS_PATH = test_comments_path

            # 文件不存在时，应返回默认批语
            loaded = main_mod.load_drawing_error_comments()
            self.assertEqual(set(loaded.keys()), set(main_mod.DEFAULT_DRAWING_ERROR_COMMENTS.keys()))

            # 添加自定义错误批语到 "光学" 分类
            custom_comment = "折射光线未画箭头"
            loaded["光学"].append(custom_comment)
            main_mod.save_drawing_error_comments(loaded)

            self.assertTrue(test_comments_path.exists())

            # 再次加载，验证持久化生效
            reloaded = main_mod.load_drawing_error_comments()
            self.assertIn(custom_comment, reloaded["光学"])

            # 验证添加一个新分类
            reloaded["热学作图"] = ["晶体熔化图像曲线未画水平段"]
            main_mod.save_drawing_error_comments(reloaded)

            reloaded2 = main_mod.load_drawing_error_comments()
            self.assertIn("热学作图", reloaded2)
            self.assertEqual(reloaded2["热学作图"], ["晶体熔化图像曲线未画水平段"])
        finally:
            main_mod.DRAWING_ERROR_COMMENTS_PATH = orig_path
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestDrawingWorkbenchZoomPersistence(unittest.TestCase):
    def test_ui_settings_drawing_zoom_persistence(self):
        """测试 drawing_workbench_scale 与 drawing_workbench_is_fit_width 设置的保存与加载"""
        temp_dir = Path(tempfile.mkdtemp())
        orig_path = main_mod.UI_SETTINGS_PATH
        try:
            main_mod.UI_SETTINGS_PATH = temp_dir / "test_ui_settings.json"

            app = main_mod.EnhancedAnswerCardStatsGUI.__new__(main_mod.EnhancedAnswerCardStatsGUI)
            app.answer_threshold_var = MagicMock(get=lambda: 0.5)
            app.student_id_threshold_var = MagicMock(get=lambda: 0.5)
            app.sample_box_var = MagicMock(get=lambda: 12)
            app.student_id_sample_box_var = MagicMock(get=lambda: 12)
            app.debug_info_var = MagicMock(get=lambda: False)
            app.mixed_single_side_mode_var = MagicMock(get=lambda: False)
            app.last_open_dir = ""
            app.selected_folder_path = ""
            app.default_export_dir = ""
            app.overlay_offset_x_mm_var = MagicMock(get=lambda: 0.0)
            app.overlay_offset_y_mm_var = MagicMock(get=lambda: 0.0)
            app.overlay_scale_percent_var = MagicMock(get=lambda: 100.0)
            app.overlay_back_offset_x_mm_var = MagicMock(get=lambda: 0.0)
            app.overlay_back_offset_y_mm_var = MagicMock(get=lambda: 0.0)
            app.overlay_back_scale_percent_var = MagicMock(get=lambda: 100.0)
            app.overlay_print_percent_score_var = MagicMock(get=lambda: False)
            app.overlay_total_display_mode_var = MagicMock(get=lambda: 'score')
            app.overlay_grade_mode = 'score'
            app.notebook = None

            app._drawing_workbench_scale = 0.92
            app._drawing_workbench_is_fit_width = False

            app.save_ui_settings()
            self.assertTrue(main_mod.UI_SETTINGS_PATH.exists())
            data = json.loads(main_mod.UI_SETTINGS_PATH.read_text(encoding='utf-8'))
            self.assertAlmostEqual(data.get('drawing_workbench_scale'), 0.92)
            self.assertFalse(data.get('drawing_workbench_is_fit_width'))

            # 新建实例加载
            app2 = main_mod.EnhancedAnswerCardStatsGUI.__new__(main_mod.EnhancedAnswerCardStatsGUI)
            app2.answer_threshold_var = MagicMock()
            app2.student_id_threshold_var = MagicMock()
            app2.sample_box_var = MagicMock()
            app2.student_id_sample_box_var = MagicMock()
            app2.debug_info_var = MagicMock()
            app2.auto_latest_zone_var = MagicMock()
            app2.mixed_single_side_mode_var = MagicMock()
            app2.overlay_offset_x_mm_var = MagicMock()
            app2.overlay_offset_y_mm_var = MagicMock()
            app2.overlay_scale_percent_var = MagicMock()
            app2.overlay_back_offset_x_mm_var = MagicMock()
            app2.overlay_back_offset_y_mm_var = MagicMock()
            app2.overlay_back_scale_percent_var = MagicMock()
            app2.overlay_print_percent_score_var = MagicMock()
            app2.overlay_total_display_mode_var = MagicMock()
            app2.load_ui_settings()

            self.assertAlmostEqual(getattr(app2, '_drawing_workbench_scale', None), 0.92)
            self.assertFalse(getattr(app2, '_drawing_workbench_is_fit_width', None))

        finally:
            main_mod.UI_SETTINGS_PATH = orig_path
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


class DummyPdfApp(PdfOverlayExportMixin):
    def __init__(self):
        self.summary_data = []
        self.current_template_mode = 'direct_paper'
        self.status_var = MagicMock()
        self.fake_payload = {
            'scores': {},
            'drawing_annotations': {},
        }

    def load_subjective_score_payload(self):
        return self.fake_payload

    def subjective_entry_key(self, entry):
        return entry.get('file') or entry.get('score_id')

    def is_direct_paper_choice_template(self):
        return True

    def is_mixed_objective_subjective_template(self):
        return False

    def drawing_question_config(self):
        return [
            {
                'part_id': 'draw_16_1',
                'question_no': '16',
                'label': '第16题(1)',
                'side': 'back',
                'max_score': 2.0,
                'rect': {'x': 100, 'y': 100, 'w': 300, 'h': 200},
                'kind': 'drawing',
            }
        ]

    def project_template_rect(self, matrix, rect):
        x = float(rect['x'])
        y = float(rect['y'])
        w = float(rect['w'])
        h = float(rect['h'])
        return x, y, x + w, y + h


class TestDrawingWorkbenchPdfOverlay(unittest.TestCase):
    def test_drawing_annotations_mark_ops_generation(self):
        """测试作图题批注（笔迹、对勾、叉号、常见错误印章）生成 PDF 批改图形指令"""
        app = DummyPdfApp()
        entry = {'file': 'student_001.jpg'}

        # 构造学生 16 题第 1 小问的作图批注数据
        app.fake_payload['drawing_annotations'] = {
            'student_001.jpg': {
                'draw_16_1': [
                    {
                        'type': 'line',
                        'points': [[0.1, 0.1], [0.2, 0.2]],
                        'color': '#DC2626',
                        'width': 3,
                    },
                    {
                        'type': 'check',
                        'u': 0.15,
                        'v': 0.15,
                        'size': 24,
                    },
                    {
                        'type': 'cross',
                        'u': 0.18,
                        'v': 0.18,
                        'size': 22,
                    },
                    {
                        'type': 'stamp',
                        'u': 0.14,
                        'v': 0.22,
                        'text': '未标垂直符号',
                    },
                ]
            }
        }

        # 1. 验证错误印章汇总统计
        summary = app.summarize_drawing_error_stamps()
        self.assertIn('draw_16_1', summary)
        self.assertEqual(summary['draw_16_1']['未标垂直符号'], 1)

        # 2. 验证生成标注 ops
        ops = app.collect_drawing_annotations_mark_ops(entry, matrix=None, side='back')
        self.assertTrue(len(ops) >= 4, "应该包含笔迹、对勾、叉号、印章文字")

        # 验证印章文字是否在 ops 中
        stamp_text_ops = [op for op in ops if op.get('type') == 'text' and op.get('text') == '[未标垂直符号]']
        self.assertEqual(len(stamp_text_ops), 1)

        # 验证对勾和叉号 ops
        check_ops = [op for op in ops if op.get('type') == 'objective_symbol' and op.get('symbol') == 'V']
        self.assertEqual(len(check_ops), 1)
        cross_ops = [op for op in ops if op.get('type') == 'objective_symbol' and op.get('symbol') == 'X']
        self.assertEqual(len(cross_ops), 1)


if __name__ == '__main__':
    unittest.main()
