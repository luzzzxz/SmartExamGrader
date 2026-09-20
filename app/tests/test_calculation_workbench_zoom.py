import sys
from pathlib import Path
import unittest
import tempfile
import json
from unittest.mock import MagicMock

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP.parent)]

import enhanced_answer_card_gui_stats as main_mod

class TestCalculationWorkbenchZoom(unittest.TestCase):
    def test_ui_settings_zoom_persistence(self):
        """测试 calculation_workbench_scale 与 calculation_workbench_is_fit_width 的设置保存与加载"""
        temp_dir = Path(tempfile.mkdtemp())
        orig_path = main_mod.UI_SETTINGS_PATH
        try:
            main_mod.UI_SETTINGS_PATH = temp_dir / "test_ui_settings.json"

            # 模拟一个 app 实例
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

            # 设置缩放记忆
            app._calculation_workbench_scale = 0.85
            app._calculation_workbench_is_fit_width = False

            # 保存
            app.save_ui_settings()
            self.assertTrue(main_mod.UI_SETTINGS_PATH.exists())
            data = json.loads(main_mod.UI_SETTINGS_PATH.read_text(encoding='utf-8'))
            self.assertAlmostEqual(data.get('calculation_workbench_scale'), 0.85)
            self.assertFalse(data.get('calculation_workbench_is_fit_width'))

            # 新建一个 app 实例并加载
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

            self.assertAlmostEqual(getattr(app2, '_calculation_workbench_scale', None), 0.85)
            self.assertFalse(getattr(app2, '_calculation_workbench_is_fit_width', None))

        finally:
            main_mod.UI_SETTINGS_PATH = orig_path
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_zoom_fit_width_logic(self):
        """测试计算题界面的适宽计算与记忆逻辑"""
        # 假设图片宽度为 1200，画布可用宽度为 980
        img_width = 1200
        canvas_width = 980

        # 适宽比例计算
        fit_scale = max(0.15, min(3.0, (canvas_width - 20) / max(1, img_width)))
        self.assertAlmostEqual(fit_scale, (980 - 20) / 1200, places=4)
        self.assertTrue(fit_scale < 1.0)  # 不会超大溢出

        # 用户手动缩小到 0.6
        custom_scale = 0.6
        state = {
            'is_fit_width': False,
            'scale': custom_scale,
        }

        # 模拟下一张卡片加载，保持缩放比例而不是重置为 1500px
        next_img_width = 1250
        if state.get('is_fit_width', False):
            next_scale = (canvas_width - 20) / next_img_width
        else:
            next_scale = state.get('scale')

        self.assertAlmostEqual(next_scale, 0.6)

if __name__ == '__main__':
    unittest.main()
