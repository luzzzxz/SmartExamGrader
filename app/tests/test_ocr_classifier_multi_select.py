import sys
from pathlib import Path
import unittest
import tkinter as tk
from tkinter import ttk
from unittest.mock import MagicMock

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP.parent)]

import enhanced_answer_card_gui_stats as main_mod


class TestOCRClassifierMultiSelect(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_get_target_groups_logic(self):
        """测试根据左侧复选框 checked_ids 或树多选提取目标分组的逻辑与顺序"""
        group_tree = ttk.Treeview(self.root, columns=('check', 'part', 'expected', 'ocr', 'count'), show='headings')
        group1 = {
            'part_id': 'part_1',
            'label': '第1题',
            'ocr_text': '光的反射',
            'expected': '光的反射',
            'tasks': [{'entry_key': 'stu1.jpg', 'part_id': 'part_1', 'part': {'score': 2.0}}],
        }
        group2 = {
            'part_id': 'part_2',
            'label': '第2题',
            'ocr_text': '凸透镜',
            'expected': '凸透镜',
            'tasks': [{'entry_key': 'stu2.jpg', 'part_id': 'part_2', 'part': {'score': 1.0}}],
        }
        group3 = {
            'part_id': 'part_3',
            'label': '第3题',
            'ocr_text': '重力',
            'expected': '弹力',
            'tasks': [{'entry_key': 'stu3.jpg', 'part_id': 'part_3', 'part': {'score': 2.0}}],
        }

        dialog_state = {
            'groups': {
                'group_0': group1,
                'group_1': group2,
                'group_2': group3,
            },
            'selected': 'group_0',
            'checked_ids': set(),
        }

        group_tree.insert('', tk.END, iid='group_0', values=('☐', '第1题', '光的反射', '光的反射', 1))
        group_tree.insert('', tk.END, iid='group_1', values=('☐', '第2题', '凸透镜', '凸透镜', 1))
        group_tree.insert('', tk.END, iid='group_2', values=('☐', '第3题', '弹力', '重力', 1))

        def current_group():
            return dialog_state['groups'].get(dialog_state.get('selected'))

        def get_target_groups():
            checked_ids = dialog_state.get('checked_ids', set())
            all_children = group_tree.get_children('')
            if checked_ids:
                return [dialog_state['groups'][k] for k in all_children if k in checked_ids and k in dialog_state['groups']]
            tree_selected = set(group_tree.selection())
            if len(tree_selected) > 1:
                return [dialog_state['groups'][k] for k in all_children if k in tree_selected and k in dialog_state['groups']]
            if tree_selected:
                sel_id = list(tree_selected)[0]
                if sel_id in dialog_state['groups']:
                    return [dialog_state['groups'][sel_id]]
            curr = current_group()
            if curr:
                return [curr]
            return []

        # 1. 默认无复选无多选，返回当前选中的 group_0
        self.assertEqual(get_target_groups(), [group1])

        # 2. 复选 group_0 和 group_2
        dialog_state['checked_ids'] = {'group_2', 'group_0'}
        targets = get_target_groups()
        self.assertEqual(len(targets), 2)
        # 应保持树视图自上而下的显示顺序: group_0 在前, group_2 在后
        self.assertEqual(targets[0]['part_id'], 'part_1')
        self.assertEqual(targets[1]['part_id'], 'part_3')

        # 3. 复选全部 3 组
        dialog_state['checked_ids'] = {'group_0', 'group_1', 'group_2'}
        self.assertEqual([g['part_id'] for g in get_target_groups()], ['part_1', 'part_2', 'part_3'])

        # 4. 取消复选，使用树多选 (Shift/Ctrl 选了 group_1 和 group_2)
        dialog_state['checked_ids'].clear()
        group_tree.selection_set('group_1', 'group_2')
        self.assertEqual([g['part_id'] for g in get_target_groups()], ['part_2', 'part_3'])

    def test_multi_group_gallery_summary_and_toggles(self):
        """测试多组聚合下的判分统计、全对、全错、反选及提交逻辑"""
        task1 = {'entry_key': 's1.jpg', 'part_id': 'p1', 'part': {'score': 2.0, 'label': '17题(1)'}, 'entry': {'student_name': '张三', 'score_id': '01'}}
        task2 = {'entry_key': 's2.jpg', 'part_id': 'p1', 'part': {'score': 2.0, 'label': '17题(1)'}, 'entry': {'student_name': '李四', 'score_id': '02'}}
        task3 = {'entry_key': 's3.jpg', 'part_id': 'p2', 'part': {'score': 3.0, 'label': '17题(2)'}, 'entry': {'student_name': '王五', 'score_id': '03'}}

        groupA = {'part_id': 'p1', 'label': '17题(1)', 'ocr_text': '折射', 'expected': '折射', 'tasks': [task1, task2]}
        groupB = {'part_id': 'p2', 'label': '17题(2)', 'ocr_text': '放大', 'expected': '缩小', 'tasks': [task3]}

        gallery_stat_var = tk.StringVar(value='')
        gallery_submit_btn = tk.Button(self.root, text='')
        bottom_submit_btn = tk.Button(self.root, text='')

        dialog_state = {
            'current_gallery_groups': [groupA, groupB],
            'current_gallery_group': groupA,
            'persisted_decisions': {},
            'gallery_decisions': {},
            'gallery_max_scores': {
                ('s1.jpg', 'p1'): 2.0,
                ('s2.jpg', 'p1'): 2.0,
                ('s3.jpg', 'p2'): 3.0,
            },
            'bottom_submit_btn': bottom_submit_btn,
        }

        def update_gallery_summary():
            target_groups = dialog_state.get('current_gallery_groups') or []
            if not target_groups and dialog_state.get('current_gallery_group'):
                target_groups = [dialog_state['current_gallery_group']]

            all_tasks = [t for g in target_groups for t in g.get('tasks', [])]
            if not all_tasks:
                gallery_stat_var.set('')
                gallery_submit_btn.configure(text='🚀 提交全部判分', state='disabled')
                if 'bottom_submit_btn' in dialog_state:
                    dialog_state['bottom_submit_btn'].configure(text='🚀 提交全部判分', state='disabled')
                return

            total = len(all_tasks)
            right_cnt = sum(
                1 for t in all_tasks
                if dialog_state['persisted_decisions'].get((t['entry_key'], t['part_id']), 0.0) > 0
            )
            wrong_cnt = total - right_cnt

            if len(target_groups) <= 1:
                status_txt = f"判定状态：✔ 对 {right_cnt} 份  |  ❌ 错 {wrong_cnt} 份  (共 {total} 份，点击图片直接切换)"
                submit_txt = f"🚀 提交本组全部判分 (对 {right_cnt} / 错 {wrong_cnt})"
            else:
                status_txt = f"判定状态：已选 {len(target_groups)} 个分类 | ✔ 对 {right_cnt} 份  |  ❌ 错 {wrong_cnt} 份  (共 {total} 份，点击图片直接切换)"
                submit_txt = f"🚀 批量提交已选 {len(target_groups)} 组全部判分 (对 {right_cnt} / 错 {wrong_cnt})"

            gallery_stat_var.set(status_txt)
            gallery_submit_btn.configure(text=submit_txt, state='normal')
            if 'bottom_submit_btn' in dialog_state:
                dialog_state['bottom_submit_btn'].configure(text=submit_txt, state='normal')

        def set_all_gallery_correct():
            target_groups = dialog_state.get('current_gallery_groups') or []
            for group in target_groups:
                for task_item in group.get('tasks', []):
                    t_key = (task_item['entry_key'], task_item['part_id'])
                    m_score = dialog_state['gallery_max_scores'].get(t_key, 1.0)
                    dialog_state['persisted_decisions'][t_key] = m_score
                    dialog_state['gallery_decisions'][t_key] = m_score
            update_gallery_summary()

        def set_all_gallery_wrong():
            target_groups = dialog_state.get('current_gallery_groups') or []
            for group in target_groups:
                for task_item in group.get('tasks', []):
                    t_key = (task_item['entry_key'], task_item['part_id'])
                    dialog_state['persisted_decisions'][t_key] = 0.0
                    dialog_state['gallery_decisions'][t_key] = 0.0
            update_gallery_summary()

        def invert_gallery_decisions():
            target_groups = dialog_state.get('current_gallery_groups') or []
            for group in target_groups:
                for task_item in group.get('tasks', []):
                    t_key = (task_item['entry_key'], task_item['part_id'])
                    cur = dialog_state['persisted_decisions'].get(t_key, 0.0)
                    m_score = dialog_state['gallery_max_scores'].get(t_key, 1.0)
                    new_val = 0.0 if cur > 0 else m_score
                    dialog_state['persisted_decisions'][t_key] = new_val
                    dialog_state['gallery_decisions'][t_key] = new_val
            update_gallery_summary()

        # 初始状态：全部为 0
        update_gallery_summary()
        self.assertIn('已选 2 个分类', gallery_stat_var.get())
        self.assertIn('对 0 份  |  ❌ 错 3 份', gallery_stat_var.get())
        self.assertIn('批量提交已选 2 组全部判分 (对 0 / 错 3)', gallery_submit_btn['text'])
        self.assertEqual(gallery_submit_btn['text'], bottom_submit_btn['text'])

        # 测试全部设为对
        set_all_gallery_correct()
        self.assertEqual(dialog_state['persisted_decisions'][('s1.jpg', 'p1')], 2.0)
        self.assertEqual(dialog_state['persisted_decisions'][('s2.jpg', 'p1')], 2.0)
        self.assertEqual(dialog_state['persisted_decisions'][('s3.jpg', 'p2')], 3.0)
        self.assertIn('对 3 份  |  ❌ 错 0 份', gallery_stat_var.get())
        self.assertIn('(对 3 / 错 0)', gallery_submit_btn['text'])

        # 测试反选
        invert_gallery_decisions()
        self.assertEqual(dialog_state['persisted_decisions'][('s1.jpg', 'p1')], 0.0)
        self.assertEqual(dialog_state['persisted_decisions'][('s2.jpg', 'p1')], 0.0)
        self.assertEqual(dialog_state['persisted_decisions'][('s3.jpg', 'p2')], 0.0)
        self.assertIn('对 0 份  |  ❌ 错 3 份', gallery_stat_var.get())

        # 单个切换
        dialog_state['persisted_decisions'][('s1.jpg', 'p1')] = 2.0
        update_gallery_summary()
        self.assertIn('对 1 份  |  ❌ 错 2 份', gallery_stat_var.get())

        # 测试全部设为错
        set_all_gallery_wrong()
        self.assertEqual(dialog_state['persisted_decisions'][('s1.jpg', 'p1')], 0.0)
        self.assertIn('对 0 份  |  ❌ 错 3 份', gallery_stat_var.get())

    def test_multi_group_batch_submission(self):
        """测试多组一键批量提交写入 all_scores 结构与状态清除"""
        all_scores = {}
        task1 = {'entry_key': 's1.jpg', 'part_id': 'p1', 'part': {'score': 2.0, 'label': '17题(1)'}, 'entry': {'student_name': '张三', 'score_id': '01', 'file': 's1.jpg'}}
        task2 = {'entry_key': 's2.jpg', 'part_id': 'p2', 'part': {'score': 3.0, 'label': '17题(2)'}, 'entry': {'student_name': '李四', 'score_id': '02', 'file': 's2.jpg'}}

        groupA = {'part_id': 'p1', 'label': '17题(1)', 'ocr_text': '折射', 'tasks': [task1]}
        groupB = {'part_id': 'p2', 'label': '17题(2)', 'ocr_text': '反射', 'tasks': [task2]}

        dialog_state = {
            'current_gallery_groups': [groupA, groupB],
            'current_gallery_group': groupA,
            'checked_ids': {'group_0', 'group_1'},
            'persisted_decisions': {
                ('s1.jpg', 'p1'): 2.0,
                ('s2.jpg', 'p2'): 0.0,
            },
            'gallery_max_scores': {
                ('s1.jpg', 'p1'): 2.0,
                ('s2.jpg', 'p2'): 3.0,
            },
        }

        valid_targets = dialog_state['current_gallery_groups']
        total_processed = 0
        total_right = 0
        total_wrong = 0

        for group in valid_targets:
            for task_item in group['tasks']:
                t_key = (task_item['entry_key'], task_item['part_id'])
                score = dialog_state['persisted_decisions'].get(t_key, 0.0)
                m_score = dialog_state['gallery_max_scores'].get(t_key, float(task_item['part'].get('score') or 1.0))
                assigned = max(0.0, min(float(score), m_score))
                if assigned > 0:
                    total_right += 1
                else:
                    total_wrong += 1

                entry_scores = all_scores.setdefault(task_item['entry_key'], {})
                record = entry_scores.setdefault(task_item['part_id'], {})
                record.update({
                    'score': assigned,
                    'max_score': m_score,
                    'manual_graded': True,
                    'teacher_regraded': True,
                    'auto_graded': False,
                    'ocr_auto_need_manual': False,
                    'auto_grade_engine': 'OCR分类看图判分',
                    'auto_grade_reason': f'看图批量判分：{assigned}分 (满分{m_score}分)',
                    'label': task_item['part'].get('label', ''),
                    'student_name': task_item['entry'].get('student_name', ''),
                    'score_id': task_item['entry'].get('score_id', ''),
                    'file': task_item['entry'].get('file', ''),
                })
                dialog_state['persisted_decisions'].pop(t_key, None)
                total_processed += 1

        dialog_state['checked_ids'].clear()

        self.assertEqual(total_processed, 2)
        self.assertEqual(total_right, 1)
        self.assertEqual(total_wrong, 1)

        # 验证得分写入
        self.assertEqual(all_scores['s1.jpg']['p1']['score'], 2.0)
        self.assertTrue(all_scores['s1.jpg']['p1']['manual_graded'])
        self.assertEqual(all_scores['s2.jpg']['p2']['score'], 0.0)
        self.assertTrue(all_scores['s2.jpg']['p2']['manual_graded'])

        # 验证暂存和复选状态清空
        self.assertEqual(len(dialog_state['persisted_decisions']), 0)
        self.assertEqual(len(dialog_state['checked_ids']), 0)


if __name__ == '__main__':
    unittest.main()
