"""
Unit tests for LLM grading service optimizations:
- Vision preprocessing (height >= 160px, padding 24px)
- Two-stage prompt output parsing
- Confidence threshold & needs_manual triggering
- Vague text ('[模糊]') auto fallback to manual review
- Synonym / trend acceptance in free-fill
- Option confinement in choice-fill
- Physics causality reversal check
"""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
sys.path[:0] = [str(APP), str(ROOT)]

import llm_grading_service as llm


class LlmOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        # Create a tiny 80x25 test crop
        self.tiny_crop = Path(self.temp_dir.name) / "tiny_crop.jpg"
        img = Image.new("RGB", (80, 25), color="white")
        img.save(self.tiny_crop)

    def _mock_grade(self, response_json_str, expected_answers=None, expected_keywords=None, ocr_config=None, config=None):
        raw = json.dumps({"choices": [{"message": {"content": response_json_str}}]}).encode("utf-8")
        task = {
            "crop_path": str(self.tiny_crop),
            "part": {"label": "第10题", "score": 1.0, "ocr": ocr_config or {}},
        }
        cfg = config or {
            "endpoint": "https://example.invalid/v1",
            "api_key": "test_key",
            "model": "qwen-vl-max",
            "timeout": 1,
            "strictness": "strict",
            "confidence_threshold": 0.85,
        }
        with patch.object(llm.urllib.request, "urlopen", return_value=io.BytesIO(raw)):
            return llm.grade_single_subjective_task(
                task,
                expected_answers or [],
                expected_keywords or [],
                config=cfg,
                ocr_config=ocr_config,
            )

    def test_image_preprocessing_upscales_and_pads(self):
        raw_bytes = llm.prepare_llm_crop_image_bytes(str(self.tiny_crop))
        with Image.open(io.BytesIO(raw_bytes)) as im:
            w, h = im.size
            # Original was 25px height, scaled to >= 180 + 48 pad = >= 228
            self.assertGreaterEqual(h, 160)
            self.assertGreaterEqual(w, 80)

    def test_high_confidence_correct_answer_grades_full(self):
        resp = json.dumps({
            "recognized_text": "做功",
            "confidence": 0.98,
            "score": 1.0,
            "has_correction": False,
            "reason": "命中标准答案"
        })
        res = self._mock_grade(resp, ["做功"], ["做功"])
        self.assertTrue(res["success"])
        self.assertEqual(res["score"], 1.0)
        self.assertFalse(res["needs_manual"])
        self.assertEqual(res["recognized_text"], "做功")

    def test_low_confidence_triggers_needs_manual(self):
        resp = json.dumps({
            "recognized_text": "做功",
            "confidence": 0.72,  # Below 0.85 threshold
            "score": 1.0,
            "has_correction": True,
            "reason": "字迹涂改较严重，疑似做功"
        })
        res = self._mock_grade(resp, ["做功"], ["做功"])
        self.assertTrue(res["success"])
        self.assertTrue(res["needs_manual"])
        self.assertIn("置信度偏低", res["reason"])

    def test_vague_token_triggers_needs_manual(self):
        resp = json.dumps({
            "recognized_text": "[模糊]",
            "confidence": 0.40,
            "score": 0.0,
            "has_correction": False,
            "reason": "字迹完全辨识不清"
        })
        res = self._mock_grade(resp, ["凸透镜"], ["凸透镜"])
        self.assertTrue(res["success"])
        self.assertTrue(res["needs_manual"])
        self.assertIn("字迹模糊退回人工", res["reason"])

    def test_trend_synonym_in_free_fill(self):
        resp = json.dumps({
            "recognized_text": "变大",
            "confidence": 0.95,
            "score": 1.0,
            "has_correction": False,
            "reason": "同向变化趋势"
        })
        # Free fill without choice_fill flag
        res = self._mock_grade(resp, ["增大"], [])
        self.assertTrue(res["success"])
        self.assertEqual(res["score"], 1.0)
        self.assertFalse(res["needs_manual"])

    def test_choice_fill_rejects_unlisted_synonym(self):
        resp = json.dumps({
            "recognized_text": "变大",
            "confidence": 0.95,
            "score": 1.0,
            "has_correction": False,
            "reason": "与增大同义"
        })
        ocr_cfg = {"choice_fill": True, "candidate_words": "增大/减小/不变"}
        res = self._mock_grade(resp, ["增大"], [], ocr_config=ocr_cfg)
        self.assertTrue(res["success"])
        self.assertEqual(res["score"], 0.0)
        self.assertIn("选填题未选原词", res["reason"])

    def test_physics_causality_inversion_overrides_to_zero(self):
        resp = json.dumps({
            "recognized_text": "入射角等于反射角",
            "confidence": 0.95,
            "score": 1.0,
            "has_correction": False,
            "reason": "大小相等"
        })
        res = self._mock_grade(resp, ["反射角等于入射角"], ["反射角", "等于", "入射角"])
        self.assertTrue(res["success"])
        self.assertEqual(res["score"], 0.0)
        self.assertIn("因果颠倒判0分", res["reason"])


if __name__ == "__main__":
    unittest.main()
