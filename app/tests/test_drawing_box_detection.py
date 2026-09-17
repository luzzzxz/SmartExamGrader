import unittest
import numpy as np
import cv2
from pathlib import Path
from app.enhanced_answer_card_gui_stats import EnhancedAnswerCardStatsGUI


class TestDrawingBoxDetection(unittest.TestCase):
    def setUp(self):
        self.gui = EnhancedAnswerCardStatsGUI.__new__(EnhancedAnswerCardStatsGUI)

    def test_direct_blue_template_mask_rejects_dark_text_fringe(self):
        # Create a synthetic image:
        # 1. Dark gray text anti-aliasing pixel with slight blue tint (e.g. B=93, G=79, R=80)
        # 2. Real bright blue line pixel (e.g. B=200, G=150, R=120)
        img = np.full((50, 50, 3), 255, dtype=np.uint8)
        # Dark text edge pixel
        img[10:15, 10:15] = [93, 79, 80]
        # Real blue guide ink
        img[30:35, 30:35] = [200, 150, 120]

        mask = self.gui.direct_blue_template_mask(img)
        # Dark text fringe must be rejected
        self.assertEqual(int(mask[12, 12]), 0)
        # Real blue guide ink must be accepted
        self.assertEqual(int(mask[32, 32]), 255)

    def test_detect_direct_drawing_boxes_tilted_and_offset(self):
        # Create a hollow rectangle with slight vertical offset/noise on top
        h, w = 600, 800
        blue_mask = np.zeros((h, w), dtype=np.uint8)
        bx, by, bw, bh = 100, 150, 400, 300
        # Draw 5px thick rectangle border
        cv2.rectangle(blue_mask, (bx, by), (bx + bw, by + bh), 255, thickness=5)
        # Add a tiny 3px speckle 8px above the top border
        blue_mask[by - 8 : by - 5, bx + 50 : bx + 60] = 255

        boxes = self.gui.detect_direct_drawing_boxes_from_blue_mask(blue_mask)
        self.assertEqual(len(boxes), 1)
        box = boxes[0]
        self.assertEqual(box['kind'], 'drawing')
        # Refined y should be very close to the actual rectangle top 'by' (150), not 'by-8' (142)
        self.assertAlmostEqual(box['y'], by, delta=6)
        self.assertAlmostEqual(box['w'], bw, delta=10)
        self.assertAlmostEqual(box['h'], bh, delta=10)

    def test_sort_direct_subjective_lines_side_by_side_boxes(self):
        # Side-by-side drawing boxes where right box is taller and its top is higher
        # Like Question 12 (left, short) and Question 13 (right, tall) in 7.1-8.4
        blanks = [
            {'x': 100.0, 'y': 200.0, 'w': 200.0, 'h': 5.0, 'kind': 'fill_blank'},
            {'x': 100.0, 'y': 400.0, 'w': 200.0, 'h': 5.0, 'kind': 'fill_blank'},
        ]
        b12 = {'x': 200.0, 'y': 600.0, 'w': 250.0, 'h': 200.0, 'kind': 'drawing'}
        b13 = {'x': 500.0, 'y': 520.0, 'w': 250.0, 'h': 300.0, 'kind': 'drawing'}

        ordered = self.gui.sort_direct_subjective_lines_reading_order(blanks + [b13, b12])
        self.assertEqual(len(ordered), 4)
        self.assertEqual(ordered[0]['kind'], 'fill_blank')
        self.assertEqual(ordered[1]['kind'], 'fill_blank')
        # b12 (x=200, left) must come before b13 (x=500, right)
        self.assertEqual(ordered[2]['x'], 200.0)
        self.assertEqual(ordered[3]['x'], 500.0)


if __name__ == '__main__':
    unittest.main()
