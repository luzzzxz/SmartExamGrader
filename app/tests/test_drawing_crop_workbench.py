import sys
from pathlib import Path
import unittest
import tempfile
import json
from unittest.mock import MagicMock
from PIL import Image

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP.parent)]

import enhanced_answer_card_gui_stats as main_mod
from core.pdf_overlay import PdfOverlayExportMixin


class TestDrawingCropWorkbench(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.app = main_mod.EnhancedAnswerCardStatsGUI.__new__(main_mod.EnhancedAnswerCardStatsGUI)
        self.app.selected_folder_path = str(self.temp_dir)
        self.app.default_export_dir = str(self.temp_dir)
        self.app.current_session = None

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_drawing_question_crop_image_from_disk(self):
        """测试从 主观题裁图 目录匹配并加载作图题裁切小图"""
        crops_root = self.temp_dir / '主观题裁图'
        student_dir = crops_root / 'session_test' / '0101_张三_0.01'
        student_dir.mkdir(parents=True, exist_ok=True)

        # 创建两张小图：10_back_13.jpg (500x360), 11_back_14.jpg (550x380)
        img13_path = student_dir / '10_back_13.jpg'
        Image.new('RGB', (500, 360), color=(255, 0, 0)).save(img13_path)
        img14_path = student_dir / '11_back_14.jpg'
        Image.new('RGB', (550, 380), color=(0, 255, 0)).save(img14_path)

        entry = {
            'score_id': '0101',
            'student_name': '张三',
            'file': '0.01.jpg',
        }
        q13 = {
            'part_id': 'qB010_drawing_10',
            'question_no': 'B010',
            'label': '13',
            'side': 'back',
            'rect': {'x': 1086, 'y': 1282, 'w': 499, 'h': 360},
        }
        q14 = {
            'part_id': 'qB011_drawing_11',
            'question_no': 'B011',
            'label': '第14题(作图)',
            'side': 'back',
            'rect': {'x': 1635, 'y': 1286, 'w': 549, 'h': 376},
        }

        # 匹配第 13 题
        crop13, path13 = self.app.get_drawing_question_crop_image(entry, q13)
        self.assertIsNotNone(crop13)
        self.assertEqual(crop13.size, (500, 360))
        self.assertEqual(Path(path13).name, '10_back_13.jpg')

        # 匹配第 14 题（即使 label 包含文字“第14题(作图)”）
        crop14, path14 = self.app.get_drawing_question_crop_image(entry, q14)
        self.assertIsNotNone(crop14)
        self.assertEqual(crop14.size, (550, 380))
        self.assertEqual(Path(path14).name, '11_back_14.jpg')

    def test_get_drawing_question_crop_image_dynamic_fallback(self):
        """测试在无裁图目录时动态从原始试卷图进行 rect 裁切"""
        paper_back = self.temp_dir / 'paper_back.jpg'
        # 建立一张大图 2000x3000
        Image.new('RGB', (2000, 3000), color=(240, 240, 240)).save(paper_back)

        entry = {
            'score_id': '0202',
            'student_name': '李四',
            'side_files': {
                'back': {'path': str(paper_back)}
            }
        }
        q = {
            'part_id': 'qB010_drawing_10',
            'question_no': '13',
            'label': '13',
            'side': 'back',
            'rect': {'x': 500, 'y': 800, 'w': 300, 'h': 200},
        }

        crop, path = self.app.get_drawing_question_crop_image(entry, q)
        self.assertIsNotNone(crop)
        # padding 12px 左右上下各 +12 => w = 300 + 24 = 324, h = 200 + 24 = 224
        self.assertEqual(crop.size, (324, 224))
        self.assertEqual(str(path), str(paper_back))

    def test_sequential_grading_flow_logic(self):
        """测试用户要求的顺序流程：单题改完全班所有学生，再自动进入下一题从学生1开始"""
        students = ['S01_张三', 'S02_李四', 'S03_王五']
        questions = ['Q13_作图1', 'Q14_作图2']

        state = {
            'entry_index': 0,
            'question_index': 0,
            'log': [],
        }

        def mock_submit_and_next():
            cur_s = students[state['entry_index']]
            cur_q = questions[state['question_index']]
            state['log'].append((cur_q, cur_s))

            if state['entry_index'] < len(students) - 1:
                state['entry_index'] += 1
                return 'next_student'
            if state['question_index'] < len(questions) - 1:
                state['question_index'] += 1
                state['entry_index'] = 0
                return 'next_question'
            return 'finished'

        steps = []
        for _ in range(10):
            res = mock_submit_and_next()
            steps.append(res)
            if res == 'finished':
                break

        expected_log = [
            ('Q13_作图1', 'S01_张三'),
            ('Q13_作图1', 'S02_李四'),
            ('Q13_作图1', 'S03_王五'),
            ('Q14_作图2', 'S01_张三'),
            ('Q14_作图2', 'S02_李四'),
            ('Q14_作图2', 'S03_王五'),
        ]
        self.assertEqual(state['log'], expected_log)
        self.assertEqual(steps, ['next_student', 'next_student', 'next_question', 'next_student', 'next_student', 'finished'])

    def test_score_choices_integer_only(self):
        """测试打分选项仅生成 0, 1, 2 等整数分，不包含 0.5 等半步长"""
        for max_score in [2.0, 3.0]:
            max_int = int(max_score)
            choices = [float(i) for i in range(max_int + 1)]
            if max_score > max_int and max_score not in choices:
                choices.append(max_score)
            choices = sorted(list(set(choices)))
            # 2分题应仅有 [0.0, 1.0, 2.0]
            if max_score == 2.0:
                self.assertEqual(choices, [0.0, 1.0, 2.0])
                self.assertNotIn(0.5, choices)
                self.assertNotIn(1.5, choices)
            elif max_score == 3.0:
                self.assertEqual(choices, [0.0, 1.0, 2.0, 3.0])
                self.assertNotIn(0.5, choices)
                self.assertNotIn(2.5, choices)


class DummyPdfOverlayApp(PdfOverlayExportMixin):
    def __init__(self):
        self.fake_payload = {'drawing_annotations': {}}

    def load_subjective_score_payload(self):
        return self.fake_payload

    def subjective_entry_key(self, entry):
        return entry.get('file')

    def drawing_question_config(self):
        return [
            {
                'part_id': 'q_draw_1',
                'question_no': '13',
                'side': 'back',
                'rect': {'x': 1000, 'y': 1200, 'w': 500, 'h': 300},
            }
        ]

    def project_template_rect(self, matrix, rect):
        x = float(rect['x'])
        y = float(rect['y'])
        w = float(rect['w'])
        h = float(rect['h'])
        return x, y, x + w, y + h


class TestDrawingWorkbenchPdfCoordinates(unittest.TestCase):
    def test_drawing_crop_coordinates_mapped_to_full_page_rect(self):
        """测试在裁切小图上做出的批注 (u, v)，在生成试卷 PDF 叠加层时，准确映射回该题目在大图的 rect 区域"""
        app = DummyPdfOverlayApp()
        entry = {'file': '0.01.jpg'}

        # 在裁切小图中央 (u=0.5, v=0.5) 盖一个叉号和错误印章，左上角 (u=0.1, v=0.1) 到 (u=0.3, v=0.3) 画一笔红线
        app.fake_payload['drawing_annotations'] = {
            '0.01.jpg': {
                'q_draw_1': [
                    {'type': 'line', 'points': [[0.1, 0.1], [0.3, 0.3]], 'color': '#DC2626', 'width': 3},
                    {'type': 'cross', 'u': 0.5, 'v': 0.5, 'size': 20},
                    {'type': 'stamp', 'u': 0.5, 'v': 0.8, 'text': '折射角错误'},
                ]
            }
        }

        ops = app.collect_drawing_annotations_mark_ops(entry, matrix=None, side='back')
        self.assertTrue(len(ops) >= 3)

        # 题目 rect: x=1000, y=1200, w=500, h=300
        # cross 的 u=0.5, v=0.5 => 映射后坐标应当为 center = (1000 + 0.5*500, 1200 + 0.5*300) = (1250, 1350)
        cross_op = next(op for op in ops if op.get('symbol') == 'X')
        self.assertAlmostEqual(cross_op['center'][0], 1250.0)
        self.assertAlmostEqual(cross_op['center'][1], 1350.0)

        # stamp 的 u=0.5, v=0.8 => xy = (1250, 1200 + 0.8*300) = (1250, 1440)
        stamp_op = next(op for op in ops if op.get('type') == 'text' and '折射角错误' in op.get('text'))
        self.assertAlmostEqual(stamp_op['xy'][0], 1250.0)
        self.assertAlmostEqual(stamp_op['xy'][1], 1440.0)

        # line 的第 1 个点 (0.1, 0.1) => x1 = 1050, y1 = 1230; 第 2 个点 (0.3, 0.3) => x2 = 1150, y2 = 1290
        line_op = next(op for op in ops if op.get('type') == 'line')
        self.assertAlmostEqual(line_op['points'][0], 1050.0)
        self.assertAlmostEqual(line_op['points'][1], 1230.0)
        self.assertAlmostEqual(line_op['points'][2], 1150.0)
        self.assertAlmostEqual(line_op['points'][3], 1290.0)


if __name__ == '__main__':
    unittest.main()
