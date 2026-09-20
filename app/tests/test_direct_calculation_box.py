import unittest
import numpy as np
import cv2
import tempfile
from pathlib import Path
from PIL import Image
from app.enhanced_answer_card_gui_stats import EnhancedAnswerCardStatsGUI


class TestDirectCalculationBox(unittest.TestCase):
    def setUp(self):
        self.gui = EnhancedAnswerCardStatsGUI.__new__(EnhancedAnswerCardStatsGUI)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_direct_yellow_template_mask_color_separation(self):
        """Yellow mask must accurately isolate yellow/gold/amber ink and reject red, blue, green, black, white, gray."""
        img = np.full((10, 5, 3), 255, dtype=np.uint8)
        # Row 0: Standard Yellow (Word standard yellow: RGB(255, 255, 0) -> BGR(0, 255, 255))
        img[0, :] = [30, 240, 250]
        # Row 1: Golden Yellow (Word gold/accent 4: RGB(255, 192, 0) -> BGR(0, 192, 255))
        img[1, :] = [20, 180, 240]
        # Row 2: Amber / Orange-yellow (RGB(255, 165, 0) -> BGR(0, 165, 255))
        img[2, :] = [20, 140, 240]
        # Row 3: Pure Red (choice / blank ink, BGR: 0, 0, 220)
        img[3, :] = [0, 0, 220]
        # Row 4: Pure Blue (drawing box ink, BGR: 220, 100, 20)
        img[4, :] = [220, 100, 20]
        # Row 5: Green ink (BGR: 30, 180, 30)
        img[5, :] = [30, 180, 30]
        # Row 6: Black print ink (BGR: 20, 20, 20)
        img[6, :] = [20, 20, 20]
        # Row 7: White paper background (BGR: 255, 255, 255)
        img[7, :] = [255, 255, 255]
        # Row 8: Gray text / noise (BGR: 128, 128, 128)
        img[8, :] = [128, 128, 128]

        mask = self.gui.direct_yellow_template_mask(img)
        # Yellow and gold pixels must be selected (255)
        self.assertEqual(int(mask[0, 2]), 255)
        self.assertEqual(int(mask[1, 2]), 255)
        self.assertEqual(int(mask[2, 2]), 255)
        # All non-yellow pixels must be rejected (0)
        self.assertEqual(int(mask[3, 2]), 0, "Red ink should be rejected by yellow mask")
        self.assertEqual(int(mask[4, 2]), 0, "Blue ink should be rejected by yellow mask")
        self.assertEqual(int(mask[5, 2]), 0, "Green ink should be rejected by yellow mask")
        self.assertEqual(int(mask[6, 2]), 0, "Black ink should be rejected by yellow mask")
        self.assertEqual(int(mask[7, 2]), 0, "White background should be rejected by yellow mask")
        self.assertEqual(int(mask[8, 2]), 0, "Gray noise should be rejected by yellow mask")

    def test_direct_green_template_mask_color_separation(self):
        """Green mask must accurately isolate green ink and reject red, blue, black, white, gray."""
        img = np.full((10, 5, 3), 255, dtype=np.uint8)
        # Row 0: Bright green guide ink (Word standard green ~ RGB(0, 176, 80) -> BGR(80, 176, 0))
        img[0, :] = [80, 176, 0]
        # Row 1: Olive / Dark green ink (BGR: 50, 140, 40)
        img[1, :] = [50, 140, 40]
        # Row 2: Pure Red (choice / blank ink, BGR: 0, 0, 220)
        img[2, :] = [0, 0, 220]
        # Row 3: Pure Blue (drawing box ink, BGR: 220, 100, 20)
        img[3, :] = [220, 100, 20]
        # Row 4: Black print ink (BGR: 20, 20, 20)
        img[4, :] = [20, 20, 20]
        # Row 5: White paper background (BGR: 255, 255, 255)
        img[5, :] = [255, 255, 255]
        # Row 6: Gray text / noise (BGR: 128, 128, 128)
        img[6, :] = [128, 128, 128]

        mask = self.gui.direct_green_template_mask(img)
        # Green pixels must be selected (255)
        self.assertEqual(int(mask[0, 2]), 255)
        self.assertEqual(int(mask[1, 2]), 255)
        # All non-green pixels must be rejected (0)
        self.assertEqual(int(mask[2, 2]), 0, "Red ink should be rejected by green mask")
        self.assertEqual(int(mask[3, 2]), 0, "Blue ink should be rejected by green mask")
        self.assertEqual(int(mask[4, 2]), 0, "Black ink should be rejected by green mask")
        self.assertEqual(int(mask[5, 2]), 0, "White background should be rejected by green mask")
        self.assertEqual(int(mask[6, 2]), 0, "Gray noise should be rejected by green mask")

    def test_detect_direct_calculation_boxes_yellow(self):
        """Yellow hollow rectangle on back page must be detected as calculation box."""
        h, w = 1200, 800
        img = np.full((h, w, 3), 255, dtype=np.uint8)
        bx, by, bw, bh = 60, 400, 680, 600
        # Draw yellow hollow rectangle with 6px border in BGR: (30, 220, 240)
        cv2.rectangle(img, (bx, by), (bx + bw, by + bh), (30, 220, 240), thickness=6)

        # On back page: should detect calculation box
        back_boxes = self.gui.detect_direct_calculation_boxes(img, side='back')
        self.assertEqual(len(back_boxes), 1)
        box = back_boxes[0]
        self.assertEqual(box['kind'], 'calculation')
        self.assertEqual(box['ink_color'], 'yellow')
        self.assertAlmostEqual(box['rect']['x'], bx, delta=8)
        self.assertAlmostEqual(box['rect']['y'], by, delta=8)
        self.assertAlmostEqual(box['rect']['w'], bw, delta=12)
        self.assertAlmostEqual(box['rect']['h'], bh, delta=12)

    def test_detect_direct_calculation_boxes_scanned_olive_green(self):
        """Scanned dark olive green hollow rectangle (low saturation) must be detected as calculation box."""
        h, w = 1500, 1000
        img = np.full((h, w, 3), 255, dtype=np.uint8)
        bx, by, bw, bh = 50, 600, 900, 800
        # Draw scanned dark olive green in BGR: (90, 110, 85)
        cv2.rectangle(img, (bx, by), (bx + bw, by + bh), (90, 110, 85), thickness=6)

        back_boxes = self.gui.detect_direct_calculation_boxes(img, side='back')
        self.assertEqual(len(back_boxes), 1)
        box = back_boxes[0]
        self.assertEqual(box['kind'], 'calculation')
        self.assertEqual(box['ink_color'], 'green')
        self.assertAlmostEqual(box['rect']['x'], bx, delta=8)
        self.assertAlmostEqual(box['rect']['y'], by, delta=8)
        self.assertAlmostEqual(box['rect']['w'], bw, delta=12)
        self.assertAlmostEqual(box['rect']['h'], bh, delta=12)

    def test_drawing_and_calculation_box_coexist_without_interference(self):
        """Back page with red blanks, blue drawing box, and calculation box are all properly separated."""
        h, w = 1500, 900
        img = np.full((h, w, 3), 255, dtype=np.uint8)

        # 1. Red fill blank underline at top
        cv2.line(img, (100, 120), (350, 120), (20, 20, 220), thickness=3)

        # 2. Blue drawing question box in the middle
        # Blue in BGR: (210, 120, 20)
        dbx, dby, dbw, dbh = 100, 250, 300, 220
        cv2.rectangle(img, (dbx, dby), (dbx + dbw, dby + dbh), (210, 120, 20), thickness=5)

        # 3. Yellow calculation question box at the bottom
        # Yellow in BGR: (30, 210, 240)
        cbx, cby, cbw, cbh = 80, 600, 740, 700
        cv2.rectangle(img, (cbx, cby), (cbx + cbw, cby + cbh), (30, 210, 240), thickness=6)

        # Save to temp image file
        test_img_path = self.temp_path / "0.2.jpg"
        cv2.imwrite(str(test_img_path), img)

        # Subjective line detector should detect the red line and the blue drawing box
        lines, _rejected = self.gui.detect_direct_subjective_lines_from_red_template(test_img_path, side='back')
        self.assertEqual(len(lines), 2)
        # Line 1 is the red underline
        self.assertEqual(lines[0]['kind'], 'fill_blank')
        # Line 2 is the blue drawing box
        self.assertEqual(lines[1]['kind'], 'drawing')
        self.assertEqual(lines[1]['answer_label'], '作图')

        # Calculation detector should detect the yellow calculation box
        c_boxes = self.gui.detect_direct_calculation_boxes(test_img_path, side='back')
        self.assertEqual(len(c_boxes), 1)
        self.assertEqual(c_boxes[0]['kind'], 'calculation')
        self.assertEqual(c_boxes[0]['ink_color'], 'yellow')
        self.assertAlmostEqual(c_boxes[0]['x'], cbx, delta=10)
        self.assertAlmostEqual(c_boxes[0]['y'], cby, delta=10)

    def test_build_direct_calculation_region_from_box(self):
        """build_direct_calculation_region prioritizes detected calculation box over legacy line heuristic."""
        calc_box = {'x': 50.0, 'y': 500.0, 'w': 700.0, 'h': 600.0}
        dummy_lines = [{'y': 200.0, 'h': 5.0}]

        region = self.gui.build_direct_calculation_region(
            lines=dummy_lines,
            image_width=800,
            image_height=1200,
            top_offset=15.0,
            calculation_box=calc_box,
        )
        self.assertTrue(region['enabled'])
        self.assertEqual(region['source'], 'detected_calculation_box')
        self.assertEqual(region['side'], 'back')
        self.assertEqual(region['rect']['x'], 50.0)
        # y should include top_offset (500 + 15 = 515)
        self.assertEqual(region['rect']['y'], 515.0)
        self.assertEqual(region['rect']['w'], 700.0)
        self.assertEqual(region['rect']['h'], 600.0)

    def test_direct_template_guide_ink_mask_includes_boxes(self):
        """Yellow and green calculation boxes are included in guide ink mask so they are blackened on printable template."""
        h, w = 1000, 800
        img = np.full((h, w, 3), 255, dtype=np.uint8)
        # Draw yellow hollow rectangle
        cv2.rectangle(img, (80, 400), (700, 900), (30, 210, 240), thickness=6)

        guide_mask = self.gui.direct_template_guide_ink_mask(img)
        # The borders of the yellow calculation box must be marked (> 0) in guide mask
        self.assertGreater(int(guide_mask[400, 200]), 0)
        self.assertGreater(int(guide_mask[900, 200]), 0)
        self.assertGreater(int(guide_mask[500, 80]), 0)
        self.assertGreater(int(guide_mask[500, 700]), 0)
        # Interior of the hollow box should remain 0 (not filled)
        self.assertEqual(int(guide_mask[600, 400]), 0)


if __name__ == '__main__':
    unittest.main()
