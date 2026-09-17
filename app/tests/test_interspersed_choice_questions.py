"""Unit tests for interspersed choice questions and direct paper answer parsing."""
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
sys.path[:0] = [str(APP), str(ROOT)]

from enhanced_answer_card_gui_stats import EnhancedAnswerCardStatsGUI


class InterspersedChoiceQuestionsTests(unittest.TestCase):
    def setUp(self):
        self.gui = EnhancedAnswerCardStatsGUI.__new__(EnhancedAnswerCardStatsGUI)
        self.gui.template_manifest = {'current_template': 'direct_paper_choice_v1'}
        self.gui.current_template_key = 'direct_paper_choice_v1'
        self.gui.current_template_name = '试卷直接批改模板-v1'
        self.gui.current_template_entry = {'direct_paper_choice': True}
        self.gui.selected_zones = ['paper_front', 'paper_back']
        self.gui.available_zone_names = ['paper_front', 'paper_back']
        self.gui.is_direct_paper_choice_template = lambda: True
        self.gui.answer_key = {'__global__': {}}
        self.gui.direct_paper_structure = {}

    def test_is_pure_choice_answer_line(self):
        valid_lines = [
            "1. A",
            "2. B",
            "3. C",
            "4. D",
            "6. A",
            "8. B",
            "9. C",
            "10. D",
            "11. A",
            "1: A",
            "2、 B",
            "【1】 C",
            "(1) D",
            " 10 .   C  ",
        ]
        for line in valid_lines:
            self.assertTrue(
                self.gui.is_pure_choice_answer_line(line),
                f"Expected '{line}' to be recognized as pure choice answer line",
            )

        invalid_lines = [
            "1. D (2分)",
            "5. (1) 如图所示 (2) 10N",
            "7. 0.5A",
            "7. (1) 0.5 (2) 1.5",
            "8. 答案是A因为根据公式",
            "9. 200伏特",
            "一、选择题",
            "二、填空题",
            "",
            "A",
        ]
        for line in invalid_lines:
            self.assertFalse(
                self.gui.is_pure_choice_answer_line(line),
                f"Expected '{line}' NOT to be recognized as pure choice answer line",
            )

    def test_extract_direct_choice_answers_from_text(self):
        sample_text = """
1. A
2. D
3. C
4. B
5. (1) 反射 (2) 折射 (3) 虚像 (4) 等大
6. D
7. (1) 2.5 (2) 5.0
8. A
9. D
10. A
11. C
12. 如图所示略
"""
        choice_answers = self.gui.extract_direct_choice_answers_from_text(sample_text)
        expected = {
            1: ['A'],
            2: ['D'],
            3: ['C'],
            4: ['B'],
            6: ['D'],
            8: ['A'],
            9: ['D'],
            10: ['A'],
            11: ['C'],
        }
        self.assertEqual(choice_answers, expected)

    def test_subjective_answers_skip_pure_choice(self):
        sample_text = """
1. A
2. D
3. C
4. B
5. 反射；折射；虚像；等大
6. D
7. 2.5；5.0
8. A
9. D
10. A
11. C
"""
        # Define subjective items for Q5 (4 blanks) and Q7 (2 blanks)
        items = [
            {
                'item_id': 'q5',
                'question_no': 5,
                'name': '第5题',
                'blanks': [
                    {'blank_no': 1, 'score': 1, 'rect': {'x': 100, 'y': 100, 'width': 50, 'height': 20}},
                    {'blank_no': 2, 'score': 1, 'rect': {'x': 100, 'y': 130, 'width': 50, 'height': 20}},
                    {'blank_no': 3, 'score': 1, 'rect': {'x': 100, 'y': 160, 'width': 50, 'height': 20}},
                    {'blank_no': 4, 'score': 1, 'rect': {'x': 100, 'y': 190, 'width': 50, 'height': 20}},
                ],
            },
            {
                'item_id': 'q7',
                'question_no': 7,
                'name': '第7题',
                'blanks': [
                    {'blank_no': 1, 'score': 1, 'rect': {'x': 100, 'y': 250, 'width': 50, 'height': 20}},
                    {'blank_no': 2, 'score': 1, 'rect': {'x': 100, 'y': 280, 'width': 50, 'height': 20}},
                ],
            },
        ]
        self.gui.subjective_config = {'items': items}
        self.gui.subjective_items = lambda: items
        self.gui.calculation_question_config = lambda: []
        self.gui.optional_experiment_drawing_config = lambda: {}
        self.gui.current_objective_question_numbers = lambda: [1, 2, 3, 4, 6, 8, 9, 10, 11]

        values = self.gui.parse_direct_paper_subjective_answers(sample_text)
        self.assertEqual(len(values), 6)
        self.assertEqual(values['q5_b1'], '反射')
        self.assertEqual(values['q5_b2'], '折射')
        self.assertEqual(values['q5_b3'], '虚像')
        self.assertEqual(values['q5_b4'], '等大')
        self.assertEqual(values['q7_b1'], '2.5')
        self.assertEqual(values['q7_b2'], '5.0')

    def test_reassign_zones_choice_questions_by_slot(self):
        zones = [
            {
                'side': 'front',
                'questions': [
                    {'question_no_in_zone': 1, 'options': {'A': {}, 'B': {}, 'C': {}, 'D': {}}},
                    {'question_no_in_zone': 2, 'options': {'A': {}, 'B': {}, 'C': {}, 'D': {}}},
                    {'question_no_in_zone': 3, 'options': {'A': {}, 'B': {}, 'C': {}, 'D': {}}},
                    {'question_no_in_zone': 4, 'options': {'A': {}, 'B': {}, 'C': {}, 'D': {}}},
                    {'question_no_in_zone': 5, 'options': {'A': {}}},
                ],
            },
            {
                'side': 'back',
                'questions': [
                    {'question_no_in_zone': 5, 'options': {'B': {}, 'C': {}, 'D': {}}},
                    {'question_no_in_zone': 6, 'options': {'A': {}, 'B': {}, 'C': {}, 'D': {}}},
                    {'question_no_in_zone': 7, 'options': {'A': {}, 'B': {}, 'C': {}, 'D': {}}},
                    {'question_no_in_zone': 8, 'options': {'A': {}, 'B': {}, 'C': {}, 'D': {}}},
                    {'question_no_in_zone': 9, 'options': {'A': {}, 'B': {}, 'C': {}, 'D': {}}},
                ],
            },
        ]
        new_qnos = [1, 2, 3, 4, 6, 8, 9, 10, 11]
        reassigned = self.gui.reassign_zones_choice_questions_by_slot(zones, new_qnos)
        self.assertEqual(reassigned, 9)

        front_qnos = [q['question_no_in_zone'] for q in zones[0]['questions']]
        back_qnos = [q['question_no_in_zone'] for q in zones[1]['questions']]

        self.assertEqual(front_qnos, [1, 2, 3, 4, 6])
        self.assertEqual(back_qnos, [6, 8, 9, 10, 11])

    def test_cross_page_split_choice_question_reconciliation(self):
        self.gui.answer_config = {
            'mode': 'direct_paper_choice',
            'direct_mark_threshold': 3.0,
            'direct_single_gap': 1.0,
            'direct_single_adaptive_gap': 2.0,
            'zones': [
                {
                    'zone_name': 'paper_front',
                    'side': 'front',
                    'questions': [
                        {'question_no_in_zone': 6, 'question_type': 'single', 'options': {'A': {}}},
                    ],
                },
                {
                    'zone_name': 'paper_back',
                    'side': 'back',
                    'questions': [
                        {'question_no_in_zone': 6, 'question_type': 'single', 'options': {'B': {}, 'C': {}, 'D': {}}},
                    ],
                },
            ],
        }
        self.gui.question_type_for = lambda qno, qobj=None: 'single'
        self.gui.question_score_for = lambda qno, qobj=None: 3
        self.gui.expected_answers_for_question = lambda qno: ['D']
        self.gui.normalize_answer = lambda ans, qno=None, qobj=None: str(ans).upper().strip()

        # Scenario: Student filled D on back page (73.23). Option A on front page has smudge (3.55) or 0.0.
        answers = {
            'paper_front': {
                6: {
                    'selected': ['A'],
                    'scores': {'A': 3.55},
                    'threshold': 3.0,
                    'projected_points': {'A': {'x': 100, 'y': 200}},
                    'question_type': 'single',
                },
            },
            'paper_back': {
                6: {
                    'selected': ['D'],
                    'scores': {'B': 0.0, 'C': 0.0, 'D': 73.23},
                    'threshold': 3.0,
                    'projected_points': {'B': {'x': 10, 'y': 20}, 'C': {'x': 10, 'y': 40}, 'D': {'x': 10, 'y': 60}},
                    'question_type': 'single',
                },
            },
        }

        reconciled = self.gui.reconcile_cross_zone_choice_answers(answers)
        self.assertEqual(reconciled['paper_front'][6]['selected'], [])
        self.assertEqual(reconciled['paper_back'][6]['selected'], ['D'])
        self.assertEqual(reconciled['paper_front'][6]['unified_selected'], ['D'])
        self.assertEqual(reconciled['paper_back'][6]['unified_selected'], ['D'])

        # Grade through calculate_scores
        score_info = self.gui.calculate_scores(reconciled)
        front_q6 = score_info['detailed_scores']['paper_front']['details'][6]
        back_q6 = score_info['detailed_scores']['paper_back']['details'][6]

        self.assertEqual(front_q6['status'], 'correct')
        self.assertEqual(front_q6['selected'], ['D'])
        self.assertEqual(front_q6['score'], 3)
        self.assertEqual(back_q6['status'], 'correct')
        self.assertEqual(back_q6['selected'], ['D'])
        self.assertEqual(score_info['correct_answers'], 1)
        self.assertEqual(score_info['raw_score'], 3)

    def test_cross_page_split_choice_student_marked_both_a_and_d(self):
        self.gui.answer_config = {
            'mode': 'direct_paper_choice',
            'direct_mark_threshold': 3.0,
            'direct_single_gap': 1.0,
            'direct_single_adaptive_gap': 2.0,
            'zones': [
                {
                    'zone_name': 'paper_front',
                    'side': 'front',
                    'questions': [
                        {'question_no_in_zone': 6, 'question_type': 'single', 'options': {'A': {}}},
                    ],
                },
                {
                    'zone_name': 'paper_back',
                    'side': 'back',
                    'questions': [
                        {'question_no_in_zone': 6, 'question_type': 'single', 'options': {'B': {}, 'C': {}, 'D': {}}},
                    ],
                },
            ],
        }
        self.gui.question_type_for = lambda qno, qobj=None: 'single'
        self.gui.question_score_for = lambda qno, qobj=None: 3
        self.gui.expected_answers_for_question = lambda qno: ['D']
        self.gui.normalize_answer = lambda ans, qno=None, qobj=None: str(ans).upper().strip()

        # If student actually marked both A (70.0) and D (69.5)
        answers = {
            'paper_front': {
                6: {
                    'selected': ['A'],
                    'scores': {'A': 70.0},
                    'threshold': 3.0,
                    'question_type': 'single',
                },
            },
            'paper_back': {
                6: {
                    'selected': ['D'],
                    'scores': {'B': 0.0, 'C': 0.0, 'D': 69.5},
                    'threshold': 3.0,
                    'question_type': 'single',
                },
            },
        }

        reconciled = self.gui.reconcile_cross_zone_choice_answers(answers)
        self.assertEqual(reconciled['paper_front'][6]['selected'], ['A'])
        self.assertEqual(reconciled['paper_back'][6]['selected'], ['D'])
        self.assertEqual(reconciled['paper_front'][6]['unified_selected'], ['A', 'D'])

        score_info = self.gui.calculate_scores(reconciled)
        front_q6 = score_info['detailed_scores']['paper_front']['details'][6]
        self.assertEqual(front_q6['status'], 'wrong')
        self.assertEqual(front_q6['score'], 0)


if __name__ == '__main__':
    unittest.main()
