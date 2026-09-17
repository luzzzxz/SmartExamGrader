from __future__ import annotations

import copy
import cv2
import hashlib
import json
import math
import os
import re
import tempfile
import threading
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
import reportlab.pdfgen.canvas as pdf_canvas_module

from marker_utils import compute_homography, apply_homography
from core.persistence import replace_file_batch
from core.grading_state import result_rank

APP_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = APP_DIR
OVERLAY_CALIBRATION_PRESETS_PATH = PROJECT_DIR / 'overlay_calibration_presets.json'
TEACHER_MARK_ASSETS_DIR = PROJECT_DIR / 'teacher_mark_assets'

DEFAULT_OVERLAY_GRADE_THRESHOLDS = {
    'A+': 95.0,
    'A': 90.0,
    'B+': 85.0,
    'B': 80.0,
    'C+': 75.0,
    'C': 70.0,
    'D': 60.0,
}

DEFAULT_OVERLAY_GRADE_RANK_THRESHOLDS = {
    'A+': 5,
    'A': 15,
    'B+': 30,
    'B': 40,
    'C+': None,
    'C': None,
    'D': None,
}
DEFAULT_OVERLAY_GRADE_RANK_REMAINDER = 'C'


class PdfOverlayExportMixin:
    """Mixin class providing marked card preview, ReportLab PDF overlay export,
    transparent printing calibration, and annotation drawing features."""

    # ----------------------------
    # 整卡标注预览
    # ----------------------------
    def checked_overlay_entries(self):
        self.validate_overlay_context()
        entries = sorted(self.summary_data, key=lambda entry: self.natural_path_sort_key(entry.get('file') or ''))
        has_back = any((entry.get('side_files') or {}).get('back') for entry in entries)
        for entry in entries:
            sides = ('front', 'back') if has_back else ('front',)
            for side in sides:
                value = (entry.get('side_files') or {}).get(side)
                if side == 'front':
                    value = value or entry.get('input_path')
                if not value or not Path(value).is_file():
                    raise ValueError(f"{entry.get('file', '')} 的{side}面原图缺失，已停止整批导出，避免透打页错位。")
        return entries

    def marked_card_font(self, size=32, bold=False):
        candidates = (
            ['msyhbd.ttc', 'simhei.ttf', 'arialbd.ttf']
            if bold else ['msyh.ttc', 'simhei.ttf', 'arial.ttf']
        )
        for name in candidates:
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                continue
        return ImageFont.load_default()

    def draw_text_badge(self, draw, xy, text, font, fill=(220, 0, 0), bg=(255, 255, 255, 225)):
        x, y = xy
        text = str(text or '').strip()
        if not text:
            return
        if bg is None:
            draw.text((x, y), text, fill=fill, font=font)
            return
        bbox = draw.textbbox((x, y), text, font=font)
        pad_x, pad_y = 8, 5
        box = (bbox[0] - pad_x, bbox[1] - pad_y, bbox[2] + pad_x, bbox[3] + pad_y)
        draw.rectangle(box, fill=bg, outline=fill, width=2)
        draw.text((x, y), text, fill=fill, font=font)

    def plain_score_text(self, value):
        try:
            return f"{float(value):g}"
        except Exception:
            return str(value or '').strip()

    def integer_score_text(self, value):
        try:
            return str(int(max(0.0, float(value)) + 0.5))
        except Exception:
            return str(value or '').strip()

    def overlay_uses_grade(self):
        mode_var = getattr(self, 'overlay_total_display_mode_var', None)
        return bool(mode_var is not None and str(mode_var.get() or '').strip().lower() == 'grade')

    def overlay_grade_for_score(self, percent_score):
        try:
            score = max(0.0, min(100.0, float(percent_score)))
        except Exception:
            score = 0.0
        thresholds = copy.deepcopy(DEFAULT_OVERLAY_GRADE_THRESHOLDS)
        saved = getattr(self, 'overlay_grade_thresholds', {}) or {}
        for grade in thresholds:
            try:
                thresholds[grade] = float(saved.get(grade, thresholds[grade]))
            except Exception:
                pass
        for grade in ('A+', 'A', 'B+', 'B', 'C+', 'C', 'D'):
            if score >= thresholds[grade]:
                return grade
        return 'E'

    def overlay_grade_mode_is_rank(self):
        mode = getattr(self, 'overlay_grade_mode', 'score')
        return str(mode or '').strip().lower() == 'rank'

    def overlay_student_rank(self, entry):
        if not isinstance(entry, dict):
            return 1
        return result_rank(entry, getattr(self, 'summary_data', []) or [])

    def overlay_grade_by_rank(self, entry):
        if not isinstance(entry, dict):
            return 'C'
        rank = self.overlay_student_rank(entry)
        thresholds = getattr(self, 'overlay_grade_rank_thresholds', {}) or DEFAULT_OVERLAY_GRADE_RANK_THRESHOLDS
        valid_thresholds = []
        for grade in ('A+', 'A', 'B+', 'B', 'C+', 'C', 'D'):
            val = thresholds.get(grade)
            if val is not None and str(val).strip() != '':
                try:
                    ival = int(float(str(val).strip()))
                    if ival > 0:
                        valid_thresholds.append((grade, ival))
                except Exception:
                    pass
        valid_thresholds.sort(key=lambda item: item[1])
        for grade, max_rank in valid_thresholds:
            if rank <= max_rank:
                return grade
        remainder = str(getattr(self, 'overlay_grade_rank_remainder', DEFAULT_OVERLAY_GRADE_RANK_REMAINDER) or DEFAULT_OVERLAY_GRADE_RANK_REMAINDER).strip()
        return remainder or 'C'

    def overlay_grade_for_entry(self, entry):
        if self.overlay_grade_mode_is_rank():
            return self.overlay_grade_by_rank(entry)
        score = 0.0
        if isinstance(entry, dict):
            score = entry.get('combined_score', entry.get('score', 0))
        return self.overlay_grade_for_score(score)

    def printed_total_score_text(self, entry):
        if self.overlay_uses_grade():
            return self.overlay_grade_for_entry(entry)
        percent_var = getattr(self, 'overlay_print_percent_score_var', None)
        use_percent = True if percent_var is None else bool(percent_var.get())
        if use_percent:
            return self.integer_score_text(entry.get('combined_score', entry.get('score', 0)))
        return self.integer_score_text(entry.get('combined_raw_score', entry.get('raw_score', 0)))

    def draw_centered_text_in_rect(self, draw, rect, text, font, fill=(220, 0, 0)):
        text = str(text or '').strip()
        if not rect or not text:
            return
        x1, y1, x2, y2 = rect
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = int((x1 + x2 - text_w) / 2)
        y = int((y1 + y2 - text_h) / 2)
        draw.text((x, y), text, fill=fill, font=font)

    def brighten_teacher_mark_image(self, mark):
        mark = mark.convert('RGBA')
        arr = np.array(mark, dtype=np.uint8)
        alpha = arr[:, :, 3]
        ink = alpha > 8
        if np.any(ink):
            strength = (alpha[ink].astype(np.float32) / 255.0)
            arr[:, :, 0][ink] = 255
            arr[:, :, 1][ink] = np.clip(8 + 18 * (1.0 - strength), 0, 32).astype(np.uint8)
            arr[:, :, 2][ink] = np.clip(2 + 8 * (1.0 - strength), 0, 18).astype(np.uint8)
        return Image.fromarray(arr, 'RGBA')

    def paste_teacher_mark_asset(self, base_image, asset, center_xy, target_h):
        if base_image is None or not asset:
            return False
        path = asset.get('path')
        if not path:
            return False
        try:
            mark = Image.open(path).convert('RGBA')
        except Exception:
            return False
        mark = self.brighten_teacher_mark_image(mark)
        target_h = max(1, int(round(float(target_h))))
        target_w = max(1, int(round(target_h * mark.width / max(1, mark.height))))
        mark = mark.resize((target_w, target_h), Image.Resampling.LANCZOS)
        x = int(round(float(center_xy[0]) - target_w / 2))
        y = int(round(float(center_xy[1]) - target_h / 2))
        base_image.paste(mark, (x, y), mark)
        return True

    def paste_teacher_mark_text_center(self, base_image, text, center_xy, target_h, seed):
        text = str(text or '').strip()
        if not text or any(ch not in '0123456789' for ch in text):
            return False
        target_h = max(1, int(round(float(target_h))))
        gap = max(1, int(round(target_h * 0.03)))
        pieces = []
        total_w = 0
        for pos, ch in enumerate(text):
            asset = self.choose_teacher_mark_asset(ch, f'{seed}:{pos}:{ch}')
            if not asset:
                return False
            try:
                mark = Image.open(asset.get('path')).convert('RGBA')
            except Exception:
                return False
            mark = self.brighten_teacher_mark_image(mark)
            target_w = max(1, int(round(target_h * mark.width / max(1, mark.height))))
            mark = mark.resize((target_w, target_h), Image.Resampling.LANCZOS)
            pieces.append(mark)
            total_w += target_w
        total_w += gap * max(0, len(pieces) - 1)
        x = int(round(float(center_xy[0]) - total_w / 2))
        y = int(round(float(center_xy[1]) - target_h / 2))
        for mark in pieces:
            base_image.paste(mark, (x, y), mark)
            x += mark.width + gap
        return True

    def paste_teacher_mark_text_left(self, base_image, text, xy, target_h, seed):
        text = str(text or '').strip()
        if not text or any(ch not in '0123456789' for ch in text):
            return False
        target_h = max(1, int(round(float(target_h))))
        gap = max(1, int(round(target_h * 0.03)))
        x = int(round(float(xy[0])))
        y = int(round(float(xy[1])))
        for pos, ch in enumerate(text):
            asset = self.choose_teacher_mark_asset(ch, f'{seed}:{pos}:{ch}')
            if not asset:
                return False
            try:
                mark = Image.open(asset.get('path')).convert('RGBA')
            except Exception:
                return False
            mark = self.brighten_teacher_mark_image(mark)
            target_w = max(1, int(round(target_h * mark.width / max(1, mark.height))))
            mark = mark.resize((target_w, target_h), Image.Resampling.LANCZOS)
            base_image.paste(mark, (x, y), mark)
            x += mark.width + gap
        return True

    def objective_point_from_template(self, zone_name, qno, option, matrix):
        qno_text = str(qno)
        option_text = str(option).strip()
        for zone in self.answer_config.get('zones', []):
            if zone.get('zone_name') != zone_name:
                continue
            for question in zone.get('questions', []):
                if str(question.get('question_no_in_zone')) != qno_text:
                    continue
                option_data = (question.get('options') or {}).get(option_text)
                if option_data and option_data.get('template_pixel'):
                    return self.project_template_point(matrix, option_data['template_pixel'])
        return None

    def use_objective_result_symbols(self):
        answer_config = getattr(self, 'answer_config', {}) or {}
        template_entry = getattr(self, 'current_template_entry', {}) or {}
        return answer_config.get('mode') == 'direct_paper_choice' or bool(template_entry.get('direct_paper_choice'))

    def objective_options_list(self, options):
        if isinstance(options, str):
            return [ch.upper() for ch in options if ch.strip()]
        return [str(item).strip().upper() for item in (options or []) if str(item).strip()]

    def objective_mark_point_for_options(self, zone_name, qno, options, answer_info, matrix):
        points = (answer_info or {}).get('projected_points', {}) or {}
        for option in self.objective_options_list(options):
            point = points.get(option) or points.get(str(option))
            if point:
                try:
                    return float(point.get('x', 0)), float(point.get('y', 0))
                except Exception:
                    pass
            projected = self.objective_point_from_template(zone_name, qno, option, matrix)
            if projected:
                return float(projected[0]), float(projected[1])
        return None

    def draw_objective_result_symbol(self, draw, center, symbol, size=78, width=12):
        x, y = float(center[0]), float(center[1])
        half = float(size) / 2.0
        red = (220, 0, 0)
        if symbol == 'V':
            draw.line(
                (
                    x - half * 0.55, y + half * 0.02,
                    x - half * 0.14, y + half * 0.48,
                    x + half * 0.62, y - half * 0.48,
                ),
                fill=red,
                width=int(width),
                joint='curve',
            )
            return
        draw.line((x - half * 0.55, y - half * 0.55, x + half * 0.55, y + half * 0.55), fill=red, width=int(width))
        draw.line((x - half * 0.55, y + half * 0.55, x + half * 0.55, y - half * 0.55), fill=red, width=int(width))

    def draw_objective_answer_marks(self, draw, entry, matrix):
        red = (220, 0, 0)
        use_symbols = self.use_objective_result_symbols()
        answers = entry.get('answers', {}) or {}
        detailed_scores = (entry.get('score_info') or {}).get('detailed_scores', {}) or {}
        for zone_name, zone_data in detailed_scores.items():
            details = zone_data.get('details', {}) or {}
            zone_answers = answers.get(zone_name, {}) or {}
            for qno, q_detail in details.items():
                expected_options = q_detail.get('expected', []) or []
                answer_info = zone_answers.get(qno) or zone_answers.get(str(qno)) or {}
                if use_symbols:
                    selected_options = q_detail.get('selected') or answer_info.get('selected') or []
                    if q_detail.get('status') == 'correct':
                        point = self.objective_mark_point_for_options(zone_name, qno, selected_options or expected_options, answer_info, matrix)
                        if point:
                            self.draw_objective_result_symbol(draw, point, 'V')
                    else:
                        wrong_point = self.objective_mark_point_for_options(zone_name, qno, selected_options or expected_options, answer_info, matrix)
                        if wrong_point:
                            self.draw_objective_result_symbol(draw, wrong_point, 'X')
                        correct_point = self.objective_mark_point_for_options(zone_name, qno, expected_options, answer_info, matrix)
                        if correct_point:
                            self.draw_objective_result_symbol(draw, correct_point, 'V')
                    continue
                if q_detail.get('status') == 'correct':
                    continue
                points = answer_info.get('projected_points', {}) or {}
                for option in expected_options:
                    point = points.get(option) or points.get(str(option))
                    if point:
                        x, y = int(point.get('x', 0)), int(point.get('y', 0))
                    else:
                        projected = self.objective_point_from_template(zone_name, qno, option, matrix)
                        if not projected:
                            continue
                        x, y = projected
                    size = 48
                    draw.line((x - size, y - size, x + size, y + size), fill=red, width=14)

    def draw_subjective_score_marks(self, draw, entry, matrix):
        payload = self.load_subjective_score_payload()
        all_scores = payload.get('scores', {}) if isinstance(payload, dict) else {}
        entry_scores = all_scores.get(self.subjective_entry_key(entry), {})
        font = self.marked_card_font(64, bold=True)
        red = (220, 0, 0)
        for part in self.iter_subjective_parts():
            max_score = float(part.get('score') or 0)
            if max_score <= 0:
                continue
            rect = part.get('rect')
            if not rect:
                continue
            try:
                x1, y1, x2, y2 = self.project_template_rect(matrix, rect)
            except Exception:
                continue
            record = entry_scores.get(part.get('part_id'), {}) if isinstance(entry_scores, dict) else {}
            score_value = record.get('score') if isinstance(record, dict) else None
            if score_value is None or str(score_value).strip() == '':
                continue
            score = float(score_value or 0)
            if part.get('kind') == 'drawing':
                self.draw_centered_text_in_rect(draw, (x1, y1, x2, y2), self.plain_score_text(score), font, fill=red)
            elif score < max_score:
                draw.line((x1, y1, x2, y2), fill=red, width=10)

    def marked_region_rect(self, matrix, region_name):
        regions = (getattr(self, 'subjective_config', {}) or {}).get('crop_regions', {}) or {}
        rect = regions.get(region_name)
        if not rect:
            return None
        try:
            return self.project_template_rect(matrix, rect)
        except Exception:
            return None

    def objective_mark_region_rect(self, matrix, entry):
        zone_names = entry.get('zones') or self.selected_zones or self.available_zone_names
        points = []
        for zone in self.answer_config.get('zones', []):
            if zone.get('zone_name') not in zone_names:
                continue
            for question in zone.get('questions', []) or []:
                for option in (question.get('options') or {}).values():
                    pixel = option.get('template_pixel') or {}
                    if 'x' not in pixel or 'y' not in pixel:
                        continue
                    try:
                        points.append(apply_homography(matrix, (float(pixel['x']), float(pixel['y']))))
                    except Exception:
                        continue
        if not points:
            return None
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return int(min(xs) - 90), int(min(ys) - 90), int(max(xs) + 90), int(max(ys) + 90)

    def student_id_mark_region_rect(self, matrix):
        if not self.student_config:
            return None
        points = []
        for zone in self.student_config.get('zones', []) or []:
            for question in zone.get('questions', []) or []:
                for option in (question.get('options') or {}).values():
                    pixel = option.get('template_pixel') or {}
                    if 'x' not in pixel or 'y' not in pixel:
                        continue
                    try:
                        points.append(apply_homography(matrix, (float(pixel['x']), float(pixel['y']))))
                    except Exception:
                        continue
        if not points:
            return None
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return int(min(xs) - 45), int(min(ys) - 45), int(max(xs) + 45), int(max(ys) + 45)

    def draw_big_score_at_region(self, draw, region_rect, text, font, image_size):
        if not region_rect or not text:
            return
        _image_w, _image_h = image_size
        self.draw_centered_text_in_rect(draw, region_rect, text, font, fill=(220, 0, 0))

    def draw_big_question_score_marks(self, draw, entry, matrix, image_size):
        font = self.marked_card_font(104, bold=True)
        subjective = entry.get('subjective') or {}
        category_scores = subjective.get('category_scores') or {}

        self.draw_big_score_at_region(
            draw,
            self.objective_mark_region_rect(matrix, entry),
            self.plain_score_text(entry.get('raw_score', 0)),
            font,
            image_size,
        )

        fill_score = category_scores.get('fill_blank') or {}
        if fill_score:
            self.draw_big_score_at_region(
                draw,
                self.marked_region_rect(matrix, 'fill_blank'),
                self.plain_score_text(fill_score.get('raw_score', 0)),
                font,
                image_size,
            )

        exp_score = category_scores.get('experiment') or {}
        if exp_score:
            self.draw_big_score_at_region(
                draw,
                self.marked_region_rect(matrix, 'experiment'),
                self.plain_score_text(exp_score.get('raw_score', 0)),
                font,
                image_size,
            )

        drawing_score = category_scores.get('drawing') or {}
        if drawing_score:
            self.draw_drawing_total_score_mark(
                draw,
                self.plain_score_text(drawing_score.get('raw_score', 0)),
                font,
                matrix,
            )

    def draw_drawing_total_score_mark(self, draw, text, font, matrix):
        drawing_parts = [
            part for part in self.iter_subjective_parts()
            if part.get('kind') == 'drawing' and str(part.get('question_no', '')).startswith('19')
        ]
        rects = []
        for part in drawing_parts:
            rect = part.get('rect')
            if not rect:
                continue
            try:
                rects.append(self.project_template_rect(matrix, rect))
            except Exception:
                continue
        if not rects:
            return
        min_x = min(rect[0] for rect in rects)
        min_y = min(rect[1] for rect in rects)
        anchor = (int(min_x + 115), int(min_y - 18))
        self.draw_text_badge(draw, anchor, text, font, fill=(220, 0, 0), bg=None)

    def draw_total_score_header(self, draw, entry, image_size, matrix):
        font = self.marked_card_font(132, bold=True)
        score_text = self.printed_total_score_text(entry)
        if not score_text:
            return
        bbox = draw.textbbox((0, 0), score_text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        student_rect = self.student_id_mark_region_rect(matrix)
        if student_rect:
            x1, y1, x2, y2 = student_rect
            x = int(x2 + 70)
            y = int((y1 + y2 - text_h) / 2)
            if x + text_w > image_size[0] - 36:
                x = max(36, image_size[0] - text_w - 54)
        else:
            x = max(36, image_size[0] - text_w - 54)
            y = 28
        y = max(18, y)
        draw.text((x, y), score_text, fill=(220, 0, 0), font=font)

    def collect_objective_answer_mark_ops(self, entry, matrix, side=None):
        ops = []
        answers = entry.get('answers', {}) or {}
        detailed_scores = (entry.get('score_info') or {}).get('detailed_scores', {}) or {}
        zone_side_lookup = {
            zone.get('zone_name'): str(zone.get('side') or 'front').lower()
            for zone in self.answer_config.get('zones', []) or []
        }
        for zone_name, zone_data in detailed_scores.items():
            if side and zone_side_lookup.get(zone_name, 'front') != side:
                continue
            details = zone_data.get('details', {}) or {}
            zone_answers = answers.get(zone_name, {}) or {}
            for qno, q_detail in details.items():
                expected_options = q_detail.get('expected', []) or []
                answer_info = zone_answers.get(qno) or zone_answers.get(str(qno)) or {}
                if q_detail.get('status') == 'correct':
                    continue
                points = answer_info.get('projected_points', {}) or {}
                expected_set = {str(option).strip().upper() for option in expected_options if str(option).strip()}
                expected_text = ''.join(sorted(expected_set))
                anchor = None
                for option in sorted(expected_set):
                    point = points.get(option) or points.get(str(option))
                    if point:
                        x, y = float(point.get('x', 0)), float(point.get('y', 0))
                    else:
                        projected = self.objective_point_from_template(zone_name, qno, option, matrix)
                        if not projected:
                            continue
                        x, y = projected
                    anchor = (x, y)
                    break
                if not anchor or not expected_text:
                    continue
                # A printed answer remains readable even when printer alignment
                # drifts; unlike a circle, it does not need to hit the checkbox.
                x, y = anchor
                font_size = 76
                half_width = max(48.0, font_size * 0.42 * len(expected_text))
                half_height = font_size * 0.62
                ops.append({
                    'type': 'center_text',
                    'rect': (
                        x - half_width,
                        y - half_height,
                        x + half_width,
                        y + half_height,
                    ),
                    'text': expected_text,
                    'font_size': font_size,
                    'bold': True,
                })
        return ops

    def normalized_question_number_key(self, value):
        text = unicodedata.normalize('NFKC', str(value or '')).strip()
        match = re.search(r'(\d{1,3})', text)
        return str(int(match.group(1))) if match else ''

    def _safe_normalize_knowledge_payload(self, payload):
        if hasattr(self, 'normalize_knowledge_identity_payload'):
            try:
                return self.normalize_knowledge_identity_payload(payload)
            except Exception:
                pass
        if isinstance(payload, dict) and payload.get('rows'):
            return payload
        return {}

    def knowledge_codes_by_question(self):
        payload = self._safe_normalize_knowledge_payload(getattr(self, 'knowledge_identity_payload', {}) or {})
        if not payload:
            scheme_path = getattr(self, 'loaded_answer_key_scheme_path', None)
            try:
                scheme_payload = json.loads(Path(scheme_path).read_text(encoding='utf-8-sig')) if scheme_path else {}
                payload = self._safe_normalize_knowledge_payload(scheme_payload.get('knowledge_identity') or {})
            except Exception:
                payload = {}
        if not payload and hasattr(self, 'session_answer_key_path'):
            try:
                snap_path = self.session_answer_key_path()
                if snap_path and snap_path.exists():
                    snap_payload = json.loads(snap_path.read_text(encoding='utf-8-sig'))
                    payload = self._safe_normalize_knowledge_payload(snap_payload.get('knowledge_identity') or {})
            except Exception:
                pass
        if not payload and getattr(self, 'selected_folder_path', None) and hasattr(self, 'read_knowledge_identity_csv'):
            try:
                folder = Path(self.selected_folder_path)
                csv_candidates = [p for p in folder.glob('*.csv') if '知识点' in p.stem and not p.name.startswith('~$')]
                if csv_candidates:
                    csv_candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    payload = self.read_knowledge_identity_csv(csv_candidates[0])
                    if payload:
                        self.knowledge_identity_payload = payload
            except Exception:
                pass

        mapping = defaultdict(list)
        for row in payload.get('rows', []) if isinstance(payload, dict) else []:
            qkey = self.normalized_question_number_key(row.get('题号'))
            code = str(row.get('知识点编号') or '').strip()
            if qkey and code and code not in mapping[qkey]:
                mapping[qkey].append(code)
        return dict(mapping)

    def teacher_accuracy_color(self, accuracy):
        value = float(accuracy or 0)
        if value <= 50.0:
            return (235, 0, 0)
        if value < 70.0:
            # A darker yellow stays visible on white paper.
            return (225, 155, 0)
        if value <= 85.0:
            return (0, 90, 220)
        return (0, 150, 55)

    def build_teacher_objective_analysis(self):
        aggregate = defaultdict(lambda: {
            'expected': set(),
            'total': 0,
            'correct': 0,
            'wrong_options': Counter(),
            'missing_options': Counter(),
            'blank': 0,
        })
        for entry in self.summary_data:
            seen = set()
            detailed = (entry.get('score_info') or {}).get('detailed_scores', {}) or {}
            for zone_name, zone_data in detailed.items():
                zone_answers = (entry.get('answers') or {}).get(zone_name, {}) or {}
                for qno, detail in ((zone_data or {}).get('details') or {}).items():
                    qkey = self.normalized_question_number_key(qno)
                    if not qkey or qkey in seen or float((detail or {}).get('full_score') or 0) <= 0:
                        continue
                    seen.add(qkey)
                    answer_info = zone_answers.get(qno) or zone_answers.get(str(qno)) or {}
                    expected = set(self.objective_options_list((detail or {}).get('expected') or []))
                    selected = set(self.objective_options_list((detail or {}).get('selected') or answer_info.get('selected') or []))
                    row = aggregate[qkey]
                    row['expected'].update(expected)
                    row['total'] += 1
                    if selected == expected and bool(expected):
                        row['correct'] += 1
                        continue
                    if not selected:
                        row['blank'] += 1
                    for option in sorted(selected - expected):
                        row['wrong_options'][option] += 1
                    for option in sorted(expected - selected):
                        row['missing_options'][option] += 1
        result = {}
        for qkey, row in aggregate.items():
            total = int(row['total'])
            row['accuracy'] = (float(row['correct']) / total * 100.0) if total else 0.0
            row['expected_text'] = ''.join(sorted(row['expected']))
            result[qkey] = row
        return result

    def build_teacher_subjective_analysis(self):
        payload = self.load_subjective_score_payload()
        all_scores = payload.get('scores', {}) if isinstance(payload, dict) else {}
        parts = self.subjective_score_parts(include_calculation=True)
        result = {}
        for part in parts:
            part_id = str(part.get('part_id') or '')
            max_score = float(part.get('score') or 0)
            if not part_id or max_score <= 0:
                continue
            graded = 0
            full = 0
            total_score = 0.0
            wrong_counter = Counter()
            for entry in self.summary_data:
                entry_scores = all_scores.get(self.subjective_entry_key(entry), {}) if isinstance(all_scores, dict) else {}
                record = entry_scores.get(part_id, {}) if isinstance(entry_scores, dict) else {}
                if not isinstance(record, dict) or record.get('score') is None:
                    continue
                graded += 1
                score = float(record.get('score') or 0)
                total_score += score
                if score >= max_score - 1e-9:
                    full += 1
                    continue
                text = self.question_bank_record_answer_text(record) or '空白'
                wrong_counter[text] += 1
            common_wrong, common_wrong_count = wrong_counter.most_common(1)[0] if wrong_counter else ('', 0)
            expected = '' if part.get('kind') in ('drawing', 'calculation') else self.subjective_answer_mark_text(part)
            result[part_id] = {
                'part': part,
                'graded': graded,
                'full': full,
                'accuracy': (full / graded * 100.0) if graded else 0.0,
                'average_score': (total_score / graded) if graded else 0.0,
                'average_percent': ((total_score / graded) / max_score * 100.0) if graded and max_score else 0.0,
                'max_score': max_score,
                'expected': expected,
                'common_wrong': common_wrong,
                'common_wrong_count': common_wrong_count,
            }
        return result

    def objective_template_anchors_for_side(self, side):
        anchors = []
        for zone in (getattr(self, 'answer_config', {}) or {}).get('zones', []) or []:
            zone_side = str(zone.get('side') or 'front').lower()
            if zone_side != str(side or 'front').lower():
                continue
            zone_name = zone.get('zone_name')
            for question in zone.get('questions', []) or []:
                qno = question.get('question_no_in_zone')
                qkey = self.normalized_question_number_key(qno)
                points = []
                for option in (question.get('options') or {}).values():
                    pixel = option.get('template_pixel') or {}
                    if 'x' in pixel and 'y' in pixel:
                        points.append((float(pixel['x']), float(pixel['y'])))
                if qkey and points:
                    anchors.append({
                        'zone_name': zone_name,
                        'question_no': qno,
                        'qkey': qkey,
                        'x': min(point[0] for point in points),
                        'y': min(point[1] for point in points),
                    })
        return anchors

    def teacher_header_mark_ops(self, image_size):
        if not self.summary_data:
            return []
        use_grade = self.overlay_uses_grade()
        percent_var = getattr(self, 'overlay_print_percent_score_var', None)
        use_percent = True if percent_var is None else bool(percent_var.get())

        def score_value(entry):
            if use_grade or use_percent:
                return float(entry.get('combined_score', entry.get('score', 0)) or 0)
            return float(entry.get('combined_raw_score', entry.get('raw_score', 0)) or 0)

        def display_value(value, entry=None):
            if use_grade:
                if entry is not None:
                    return self.overlay_grade_for_entry(entry)
                return self.overlay_grade_for_score(value)
            return self.integer_score_text(value)

        average = sum(score_value(entry) for entry in self.summary_data) / max(1, len(self.summary_data))
        ranked = sorted(
            (entry for entry in self.summary_data if score_value(entry) > average),
            key=lambda entry: (-score_value(entry), str(entry.get('score_id') or ''), str(entry.get('student_name') or '')),
        )
        items = [
            f"{entry.get('student_name') if entry.get('student_name') not in ('', '未匹配') else entry.get('score_id', '')} "
            f"{display_value(score_value(entry), entry=entry)}"
            for entry in ranked
        ]
        width, _height = image_size
        ops = [{
            'type': 'text',
            'xy': (int(width * 0.055), 42),
            'text': f"平均{'等级 ' if use_grade else ' '}{display_value(average)}  高于平均分",
            'font_size': 42,
            'bold': True,
            'prefer_chinese_font': True,
            'color': (180, 0, 0),
        }]
        per_line = 5
        for index in range(0, len(items), per_line):
            ops.append({
                'type': 'text',
                'xy': (int(width * 0.055), 94 + (index // per_line) * 48),
                'text': '   '.join(items[index:index + per_line]),
                'font_size': 36,
                'bold': True,
                'prefer_chinese_font': True,
                'color': (180, 0, 0),
            })
        return ops

    def teacher_commentary_mark_ops(self, side, image_size):
        side = str(side or 'front').lower()
        objective_rows = self.build_teacher_objective_analysis()
        subjective_rows = self.build_teacher_subjective_analysis()
        ops = self.teacher_header_mark_ops(image_size) if side == 'front' else []

        for anchor in self.objective_template_anchors_for_side(side):
            row = objective_rows.get(anchor['qkey'])
            if not row:
                continue
            accuracy = float(row.get('accuracy') or 0)
            wrong_bits = [f'{option}{count}' for option, count in sorted(row['wrong_options'].items())]
            details = []
            if wrong_bits:
                details.append('错选' + ' '.join(wrong_bits))
            if row.get('blank'):
                details.append(f"空{row['blank']}")
            text = f"答{row.get('expected_text') or '-'} 正确率{accuracy:.0f}%"
            if details:
                text += ' ' + ' '.join(details)
            ops.append({
                'type': 'text',
                'xy': (anchor['x'], max(8.0, anchor['y'] - 58.0)),
                'text': text,
                'font_size': 35,
                'bold': True,
                'prefer_chinese_font': True,
                'color': self.teacher_accuracy_color(accuracy),
            })

        calculation_rows = []
        drawing_groups = defaultdict(list)
        for row in subjective_rows.values():
            part = row['part']
            part_side = str(part.get('side') or ('back' if part.get('kind') == 'calculation' else 'front')).lower()
            if part.get('kind') == 'calculation':
                if side == 'back':
                    calculation_rows.append(row)
                continue
            if part_side != side:
                continue
            rect = part.get('rect') or {}
            if not rect:
                continue
            if part.get('kind') == 'drawing':
                drawing_groups[self.normalized_question_number_key(part.get('question_no'))].append(row)
                continue
            accuracy = float(row.get('accuracy') or 0)
            text = f"答{row.get('expected') or '-'} 正确率{accuracy:.0f}%"
            if row.get('common_wrong'):
                text += f" 常错{row['common_wrong']}({row['common_wrong_count']})"
            units = sum(1.0 if ord(ch) > 127 else 0.58 for ch in text)
            rect_width = max(80.0, float(rect.get('w') or 80))
            font_size = max(20.0, min(34.0, rect_width / max(1.0, units * 0.48)))
            ops.append({
                'type': 'text',
                'xy': (float(rect.get('x') or 0), float(rect.get('y') or 0) + float(rect.get('h') or 0) + 5.0),
                'text': text,
                'font_size': font_size,
                'bold': True,
                'prefer_chinese_font': True,
                'color': self.teacher_accuracy_color(accuracy),
            })

        for qkey, rows in drawing_groups.items():
            rects = [row['part'].get('rect') or {} for row in rows]
            rects = [rect for rect in rects if rect]
            if not rects:
                continue
            average_score = sum(float(row.get('average_score') or 0) for row in rows)
            max_score = sum(float(row.get('max_score') or 0) for row in rows)
            average_percent = (average_score / max_score * 100.0) if max_score else 0.0
            x = min(float(rect.get('x') or 0) for rect in rects)
            y = min(float(rect.get('y') or 0) for rect in rects)
            ops.append({
                'type': 'text',
                'xy': (x, max(5.0, y - 44.0)),
                'text': f"{qkey}题 平均{self.plain_score_text(average_score)}/{self.plain_score_text(max_score)}",
                'font_size': 34,
                'bold': True,
                'prefer_chinese_font': True,
                'color': self.teacher_accuracy_color(average_percent),
            })

        if calculation_rows:
            if self.is_direct_paper_choice_template():
                region = self.direct_calculation_region_config().get('rect') or {}
                base_x = float(region.get('x') or image_size[0] * 0.08)
                base_y = float(region.get('y') or image_size[1] * 0.70) + 26.0
            else:
                base_x = image_size[0] * 0.10
                base_y = image_size[1] * 0.15
            calculation_groups = defaultdict(list)
            for row in calculation_rows:
                calculation_groups[self.normalized_question_number_key(row['part'].get('question_no'))].append(row)
            for index, (qkey, rows) in enumerate(sorted(calculation_groups.items(), key=lambda item: int(item[0]) if item[0].isdigit() else 9999)):
                average_score = sum(float(row.get('average_score') or 0) for row in rows)
                max_score = sum(float(row.get('max_score') or 0) for row in rows)
                accuracy = (average_score / max_score * 100.0) if max_score else 0.0
                ops.append({
                    'type': 'text',
                    'xy': (base_x, base_y + index * 44),
                    'text': f"{qkey}题 平均{self.plain_score_text(average_score)}/{self.plain_score_text(max_score)}",
                    'font_size': 34,
                    'bold': True,
                    'prefer_chinese_font': True,
                    'color': self.teacher_accuracy_color(accuracy),
                })
        return ops

    def collect_wrong_knowledge_mark_ops(self, entry, matrix, side='front', image_size=None):
        knowledge_map = self.knowledge_codes_by_question()
        if not knowledge_map:
            return []
        side = str(side or 'front').lower()
        ops = []
        marked = set()
        detailed = (entry.get('score_info') or {}).get('detailed_scores', {}) or {}
        zone_side_lookup = {
            zone.get('zone_name'): str(zone.get('side') or 'front').lower()
            for zone in (getattr(self, 'answer_config', {}) or {}).get('zones', []) or []
        }
        knowledge_font_size = int(round(getattr(self, 'overlay_knowledge_font_size', 56)))
        for zone_name, zone_data in detailed.items():
            if zone_side_lookup.get(zone_name, 'front') != side:
                continue
            zone_answers = (entry.get('answers') or {}).get(zone_name, {}) or {}
            for qno, detail in ((zone_data or {}).get('details') or {}).items():
                qkey = self.normalized_question_number_key(qno)
                if not qkey or qkey in marked or not knowledge_map.get(qkey) or (detail or {}).get('status') == 'correct':
                    continue
                answer_info = zone_answers.get(qno) or zone_answers.get(str(qno)) or {}
                points = []
                for point in (answer_info.get('projected_points') or {}).values():
                    if isinstance(point, dict) and 'x' in point and 'y' in point:
                        points.append((float(point['x']), float(point['y'])))
                if not points and matrix is not None:
                    for zone in (getattr(self, 'answer_config', {}) or {}).get('zones', []) or []:
                        if zone.get('zone_name') != zone_name:
                            continue
                        question = next((item for item in zone.get('questions', []) or [] if str(item.get('question_no_in_zone')) == str(qno)), None)
                        for option in (question or {}).get('options', {}).values():
                            pixel = option.get('template_pixel') or {}
                            if 'x' in pixel and 'y' in pixel:
                                points.append(self.project_template_point(matrix, pixel))
                if not points:
                    continue
                ops.append({
                    'type': 'text',
                    'xy': (min(point[0] for point in points), max(4.0, min(point[1] for point in points) - (50.0 + knowledge_font_size))),
                    'text': '/'.join(knowledge_map[qkey]),
                    'font_size': knowledge_font_size,
                    'bold': True,
                    'prefer_chinese_font': True,
                    'color': (220, 0, 0),
                })
                marked.add(qkey)

        entry_scores = ((entry.get('subjective') or {}).get('scores') or {})
        for part in self.iter_subjective_parts():
            part_side = str(part.get('side') or 'front').lower()
            qkey = self.normalized_question_number_key(part.get('question_no'))
            if part_side != side or not qkey or qkey in marked or not knowledge_map.get(qkey):
                continue
            record = entry_scores.get(part.get('part_id'), {}) if isinstance(entry_scores, dict) else {}
            if not isinstance(record, dict) or record.get('score') is None:
                continue
            if float(record.get('score') or 0) >= float(part.get('score') or 0) - 1e-9:
                continue
            rect = part.get('rect') or {}
            if not rect or matrix is None:
                continue
            try:
                x1, y1, _x2, _y2 = self.project_template_rect(matrix, rect)
            except Exception:
                continue
            ops.append({
                'type': 'text',
                'xy': (x1, max(4.0, y1 - (10.0 + knowledge_font_size))),
                'text': '/'.join(knowledge_map[qkey]),
                'font_size': knowledge_font_size,
                'bold': True,
                'prefer_chinese_font': True,
                'color': (220, 0, 0),
            })
            marked.add(qkey)

        calculation_rows = self.calculation_score_rows_for_entry(entry)
        wrong_calculations = [
            row for row in calculation_rows
            if row.get('has_score') and float(row.get('raw_score') or 0) < float(row.get('max_score') or 0) - 1e-9
        ]
        if side == 'back' and wrong_calculations:
            if self.is_direct_paper_choice_template() and matrix is not None:
                region = self.direct_calculation_region_config().get('rect') or {}
                try:
                    x1, y1, x2, _y2 = self.project_template_rect(matrix, region)
                    base_x, base_y = x1 + (x2 - x1) * 0.68, y1 + 28
                except Exception:
                    base_x, base_y = 0, 0
            else:
                width, height = image_size or (2480, 3508)
                base_x, base_y = width * 0.68, height * 0.15
            line_step = max(34, int(round(knowledge_font_size * 1.22)))
            for index, row in enumerate(wrong_calculations):
                qkey = self.normalized_question_number_key(row.get('question_no'))
                if not qkey or not knowledge_map.get(qkey):
                    continue
                ops.append({
                    'type': 'text',
                    'xy': (base_x, base_y + index * line_step),
                    'text': '/'.join(knowledge_map[qkey]),
                    'font_size': knowledge_font_size,
                    'bold': True,
                    'prefer_chinese_font': True,
                    'color': (220, 0, 0),
                })
        return ops

    def subjective_answer_mark_text(self, part):
        if not isinstance(part, dict):
            return ''
        if part.get('kind') == 'drawing':
            return ''
        answers = self.subjective_expected_answers_from_config(part.get('ocr'))
        if not answers:
            return ''
        return ' / '.join(str(answer).strip() for answer in answers if str(answer).strip())

    def subjective_answer_mark_font_size(self, text, rect_width):
        text = str(text or '').strip()
        if not text:
            return 0
        width = max(1.0, float(rect_width or 1))
        units = 0.0
        for ch in text:
            units += 1.0 if ord(ch) > 127 else 0.58
        if units <= 0:
            return 0
        # Keep short answers easy to read, but shrink long phrase answers so
        # they stay near the blank instead of running across the whole page.
        return max(18.0, min(42.0, width / max(1.0, units * 0.72)))

    def collect_subjective_score_mark_ops(self, entry, matrix, side=None):
        ops = []
        payload = self.load_subjective_score_payload()
        all_scores = payload.get('scores', {}) if isinstance(payload, dict) else {}
        entry_scores = all_scores.get(self.subjective_entry_key(entry), {})
        for part in self.iter_subjective_parts():
            part_side = str(part.get('side') or 'front').lower()
            if side and part_side != str(side).lower():
                continue
            max_score = float(part.get('score') or 0)
            if max_score <= 0:
                continue
            rect = part.get('rect')
            if not rect:
                continue
            try:
                x1, y1, x2, y2 = self.project_template_rect(matrix, rect)
            except Exception:
                continue
            record = entry_scores.get(part.get('part_id'), {}) if isinstance(entry_scores, dict) else {}
            score_value = record.get('score') if isinstance(record, dict) else None
            if score_value is None or str(score_value).strip() == '':
                continue
            score = float(score_value or 0)
            answer_text = self.subjective_answer_mark_text(part)
            if answer_text and score < max_score:
                font_size = self.subjective_answer_mark_font_size(answer_text, abs(float(x2) - float(x1)))
                if font_size > 0:
                    ops.append({
                        'type': 'text',
                        'xy': (float(x1), float(y2) + 5.0),
                        'text': answer_text,
                        'font_size': font_size,
                        'bold': False,
                        'prefer_chinese_font': True,
                    })
            if part.get('kind') == 'drawing':
                ops.append({
                    'type': 'center_text',
                    'rect': (x1, y1, x2, y2),
                    'text': self.plain_score_text(score),
                    'font_size': 64,
                })
            elif score < max_score:
                ops.append({'type': 'line', 'points': (x1, y1, x2, y2), 'width': 10})
        return ops

    def collect_big_score_at_region_ops(self, region_rect, text, font_size=104):
        if not region_rect or not text:
            return []
        return [{
            'type': 'center_text',
            'rect': region_rect,
            'text': str(text),
            'font_size': font_size,
        }]

    def collect_drawing_total_score_mark_ops(self, text, matrix):
        if not text:
            return []
        drawing_parts = [
            part for part in self.iter_subjective_parts()
            if part.get('kind') == 'drawing' and str(part.get('question_no', '')).startswith('19')
        ]
        rects = []
        for part in drawing_parts:
            rect = part.get('rect')
            if not rect:
                continue
            try:
                rects.append(self.project_template_rect(matrix, rect))
            except Exception:
                continue
        if not rects:
            return []
        min_x = min(rect[0] for rect in rects)
        min_y = min(rect[1] for rect in rects)
        return [{
            'type': 'text',
            'xy': (int(min_x + 115), int(min_y - 18)),
            'text': str(text),
            'font_size': 104,
        }]

    def collect_big_question_score_mark_ops(self, entry, matrix):
        ops = []
        subjective = entry.get('subjective') or {}
        category_scores = subjective.get('category_scores') or {}
        ops.extend(self.collect_big_score_at_region_ops(
            self.objective_mark_region_rect(matrix, entry),
            self.plain_score_text(entry.get('raw_score', 0)),
        ))
        fill_score = category_scores.get('fill_blank') or {}
        if fill_score:
            ops.extend(self.collect_big_score_at_region_ops(
                self.marked_region_rect(matrix, 'fill_blank'),
                self.plain_score_text(fill_score.get('raw_score', 0)),
            ))
        exp_score = category_scores.get('experiment') or {}
        if exp_score:
            ops.extend(self.collect_big_score_at_region_ops(
                self.marked_region_rect(matrix, 'experiment'),
                self.plain_score_text(exp_score.get('raw_score', 0)),
            ))
        drawing_score = category_scores.get('drawing') or {}
        if drawing_score:
            ops.extend(self.collect_drawing_total_score_mark_ops(
                self.plain_score_text(drawing_score.get('raw_score', 0)),
                matrix,
            ))
        return ops

    def collect_total_score_header_ops(self, entry, image_size, matrix):
        score_text = self.printed_total_score_text(entry)
        if not score_text:
            return []
        font_size = 132
        font = self.marked_card_font(font_size, bold=True)
        probe = Image.new('RGB', (10, 10), 'white')
        draw = ImageDraw.Draw(probe)
        bbox = draw.textbbox((0, 0), score_text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        student_rect = self.student_id_mark_region_rect(matrix)
        if student_rect:
            x1, y1, x2, y2 = student_rect
            x = int(x2 + 70)
            y = int((y1 + y2 - text_h) / 2)
            if x + text_w > image_size[0] - 36:
                x = max(36, image_size[0] - text_w - 54)
        else:
            x = max(36, image_size[0] - text_w - 54)
            y = 28
        y = max(18, y)
        return [{
            'type': 'text',
            'xy': (x, y),
            'text': score_text,
            'font_size': font_size,
            'bold': True,
            'prefer_chinese_font': self.overlay_uses_grade(),
        }]

    def calculation_score_record_for(self, entry_scores, qno, sub_no):
        candidates = [
            f'calc_q{qno}_s{sub_no}',
            f'calculation_q{qno}_s{sub_no}',
            f'calculation_{qno}_{sub_no}',
            f'q{qno}_calc_s{sub_no}',
            f'q{qno}_s{sub_no}',
            f'q{qno}_s{sub_no}_b1',
            f'q{qno}_sub{sub_no}',
            f'q{qno}_{sub_no}',
        ]
        for key in candidates:
            record = (entry_scores or {}).get(key)
            if isinstance(record, dict) and record.get('score') is not None:
                return record
        qno_text = str(qno)
        sub_text = str(sub_no)
        for key, record in (entry_scores or {}).items():
            if not isinstance(record, dict) or record.get('score') is None:
                continue
            label = str(record.get('label') or '')
            if qno_text in str(key) and sub_text in str(key):
                return record
            if f'第{qno_text}题' in label and (f'({sub_text})' in label or f'第{sub_text}小题' in label):
                return record
        return None

    def calculation_score_rows_for_entry(self, entry):
        questions = self.calculation_question_config()
        if not questions:
            return []
        payload = self.load_subjective_score_payload()
        scores = payload.get('scores', {}) if isinstance(payload, dict) else {}
        entry_scores = scores.get(self.subjective_entry_key(entry), {}) if isinstance(scores, dict) else {}
        rows = []
        for question in questions:
            qno = question.get('question_no')
            subs = []
            raw_total = 0.0
            max_total = 0.0
            has_any = False
            for item in question.get('sub_scores', []) or []:
                sub_no = item.get('sub_no')
                max_score = float(item.get('score') or 0)
                record = self.calculation_score_record_for(entry_scores, qno, sub_no)
                score = None
                if isinstance(record, dict) and record.get('score') is not None:
                    score = float(record.get('score') or 0)
                    raw_total += score
                    has_any = True
                max_total += max_score
                subs.append({
                    'sub_no': sub_no,
                    'score': score,
                    'max_score': max_score,
                })
            if subs:
                rows.append({
                    'question_no': qno,
                    'subs': subs,
                    'raw_score': raw_total,
                    'max_score': max_total,
                    'has_score': has_any,
                })
        return rows

    def collect_calculation_back_page_mark_ops(self, entry, image_size):
        if not self.is_mixed_objective_subjective_template():
            return []
        rows = [row for row in self.calculation_score_rows_for_entry(entry) if row.get('has_score')]
        if not rows:
            return []
        width, height = image_size
        y_positions = [height * 0.18, height * 0.58]
        ops = []
        for index, row in enumerate(rows[:2]):
            qno = row.get('question_no')
            sub_texts = []
            for sub in row.get('subs', []) or []:
                if sub.get('score') is None:
                    continue
                sub_texts.append(f"({sub.get('sub_no')}){self.plain_score_text(sub.get('score'))}")
            if not sub_texts:
                continue
            text = f"{qno}: {' '.join(sub_texts)}  总{self.plain_score_text(row.get('raw_score'))}"
            ops.append({
                'type': 'text',
                'xy': (int(width * 0.10), int(y_positions[min(index, len(y_positions) - 1)])),
                'text': text,
                'font_size': 88,
            })
        return ops

    def collect_direct_calculation_region_mark_ops(self, entry, matrix):
        """Print direct-paper calculation scores at the calculation area's top-left."""
        if not self.is_direct_paper_choice_template():
            return []
        region = self.direct_calculation_region_config()
        rect = region.get('rect') if isinstance(region, dict) else None
        if not rect:
            return []
        rows = [row for row in self.calculation_score_rows_for_entry(entry) if row.get('has_score')]
        if not rows:
            return []
        try:
            x1, y1, x2, y2 = self.project_template_rect(matrix, rect)
        except Exception:
            return []
        width = max(1.0, float(x2) - float(x1))
        height = max(1.0, float(y2) - float(y1))
        font_size = max(38.0, min(78.0, (height - 34.0) / max(1, len(rows)) * 0.72))
        line_gap = max(font_size * 1.2, min(font_size * 1.45, height / max(1, len(rows))))
        x = float(x1) + max(24.0, width * 0.025)
        y = float(y1) + max(18.0, font_size * 0.18)
        ops = []
        for index, row in enumerate(rows):
            subs = []
            for sub in row.get('subs') or []:
                score = sub.get('score')
                if score is not None:
                    subs.append(f"({sub.get('sub_no')}){self.plain_score_text(score)}")
            if not subs:
                continue
            ops.append({
                'type': 'text',
                'xy': (x, y + index * line_gap),
                'text': f"{row.get('question_no')}题  {' '.join(subs)}  = {self.plain_score_text(row.get('raw_score', 0))}",
                'font_size': font_size,
                'bold': True,
            })
        return ops

    def entry_alignment_homography(self, entry, side='front'):
        payload = (entry.get('alignment_homographies') or {}).get(side)
        if payload is None:
            return None
        try:
            matrix = np.asarray(payload, dtype=float)
        except Exception:
            return None
        if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
            return None
        return matrix.tolist()

    def entry_image_path_for_side(self, entry, side='front'):
        side_files = entry.get('side_files') or {}
        return Path(side_files.get(side) or entry.get('input_path') or '')

    def orient_entry_image_for_side(self, entry, input_path, side='front'):
        rotation_name = (entry.get('side_orientations') or {}).get(side) or entry.get('orientation')
        if rotation_name:
            return self.orient_image_by_rotation(input_path, rotation_name)
        return self.auto_orient_image(input_path)

    def build_marked_answer_card_overlay_ops(self, entry, side='front'):
        input_path = self.entry_image_path_for_side(entry, side)
        if not input_path.exists():
            raise RuntimeError(f"鎵句笉鍒板師鍥撅細{entry.get('input_path') or entry.get('file')}")

        image, _rotation_name = self.orient_entry_image_for_side(entry, input_path, side)
        if side == 'back' and self.is_mixed_objective_subjective_template():
            ops = self.collect_calculation_back_page_mark_ops(entry, image.size)
            ops.extend(self.collect_wrong_knowledge_mark_ops(entry, None, side='back', image_size=image.size))
            return ops, image.size

        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            temp_path = Path(tmp.name)
        image.save(temp_path)
        try:
            matrix = self.entry_alignment_homography(entry, side)
            if matrix is None:
                matrix, _marker_payload = self.detect_scan_homography(
                    temp_path,
                    side=side if entry.get('side_files') else None,
                )
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

        ops = []
        ops.extend(self.collect_objective_answer_mark_ops(entry, matrix, side=side if entry.get('side_files') else None))
        subjective_side = side if entry.get('side_files') else None
        ops.extend(self.collect_subjective_score_mark_ops(entry, matrix, side=subjective_side))
        ops.extend(self.collect_wrong_knowledge_mark_ops(entry, matrix, side=side, image_size=image.size))
        if side == 'back' and self.is_direct_paper_choice_template():
            ops.extend(self.collect_direct_calculation_region_mark_ops(entry, matrix))
        if not entry.get('side_files') or side == 'front':
            if not self.is_direct_paper_choice_template():
                ops.extend(self.collect_big_question_score_mark_ops(entry, matrix))
            ops.extend(self.collect_total_score_header_ops(entry, image.size, matrix))
        elif side == 'back' and self.is_mixed_objective_subjective_template():
            ops.extend(self.collect_calculation_back_page_mark_ops(entry, image.size))
        return ops, image.size

    def teacher_mark_asset_groups(self):
        cached = getattr(self, '_teacher_mark_asset_groups', None)
        if cached is not None:
            return cached
        ranges = {
            'slash': range(0, 8),
            '0': range(8, 17),
            '1': range(17, 24),
            '2': range(24, 32),
            '3': range(32, 41),
            '4': range(41, 50),
            '5': range(50, 59),
            '6': range(59, 67),
            '7': range(67, 75),
            '8': range(75, 83),
            '9': range(83, 91),
        }
        groups = {}
        for key, index_range in ranges.items():
            variants = []
            for idx in index_range:
                path = TEACHER_MARK_ASSETS_DIR / f'asset_{idx:03d}.png'
                if not path.exists():
                    continue
                try:
                    with Image.open(path) as image:
                        variants.append({
                            'path': path,
                            'size': image.size,
                        })
                except Exception:
                    continue
            if variants:
                groups[key] = variants
        self._teacher_mark_asset_groups = groups
        return groups

    def choose_teacher_mark_asset(self, group_name, seed):
        variants = self.teacher_mark_asset_groups().get(group_name) or []
        if not variants:
            return None
        digest = hashlib.md5(str(seed).encode('utf-8', errors='ignore')).digest()
        index = int.from_bytes(digest[:4], 'big') % len(variants)
        return variants[index]

    def draw_overlay_ops_to_pdf_page(self, pdf_canvas, ops, image_size, page_size, offset_x_mm=0.0, offset_y_mm=0.0, scale_percent=100.0, calibration_matrix=None):
        page_w, page_h = page_size
        image_w, image_h = image_size
        scale = max(50.0, min(150.0, float(scale_percent or 100.0))) / 100.0
        offset_x = float(offset_x_mm or 0.0) * 72.0 / 25.4
        offset_y = float(offset_y_mm or 0.0) * 72.0 / 25.4
        sx = page_w * scale / max(1, image_w)
        sy = page_h * scale / max(1, image_h)

        def map_point(x, y):
            if calibration_matrix:
                try:
                    x, y = apply_homography(calibration_matrix, (float(x), float(y)))
                except Exception:
                    pass
            return offset_x + float(x) * sx, page_h - (offset_y + float(y) * sy)

        def map_rect(rect):
            x1, y1, x2, y2 = rect
            left, top = map_point(x1, y1)
            right, bottom = map_point(x2, y2)
            return left, bottom, right, top

        pdf_font_name = getattr(self, 'overlay_pdf_font_name', 'Helvetica-Bold')
        chinese_pdf_font_name = getattr(self, 'overlay_pdf_chinese_font_name', pdf_font_name)
        marker_red = (1.0, 0.05, 0.0)
        pdf_canvas.setStrokeColorRGB(*marker_red)
        pdf_canvas.setFillColorRGB(*marker_red)
        try:
            from reportlab.lib.utils import ImageReader
        except Exception:
            ImageReader = None
        image_reader_cache = getattr(self, '_overlay_pdf_asset_reader_cache', None)
        if image_reader_cache is None:
            image_reader_cache = {}
            self._overlay_pdf_asset_reader_cache = image_reader_cache

        def draw_handwritten_slash(x1, y1, x2, y2, width):
            dx = x2 - x1
            dy = y2 - y1
            length = max(1.0, (dx * dx + dy * dy) ** 0.5)
            nx = -dy / length
            ny = dx / length
            wiggle = max(0.6, min(2.2, width * 0.32))
            sign = 1 if int(abs(x1 * 13 + y1 * 7)) % 2 else -1
            c1x = x1 + dx * 0.34 + nx * wiggle * sign
            c1y = y1 + dy * 0.34 + ny * wiggle * sign
            c2x = x1 + dx * 0.68 - nx * wiggle * 0.45 * sign
            c2y = y1 + dy * 0.68 - ny * wiggle * 0.45 * sign
            try:
                pdf_canvas.setLineCap(1)
                pdf_canvas.setLineJoin(1)
            except Exception:
                pass
            pdf_canvas.setLineWidth(width)
            pdf_canvas.bezier(x1, y1, c1x, c1y, c2x, c2y, x2, y2)

        def asset_reader(asset):
            if not asset or ImageReader is None:
                return None
            path = asset.get('path')
            if not path:
                return None
            key = str(path)
            if key not in image_reader_cache:
                mark = self.brighten_teacher_mark_image(Image.open(path).convert('RGBA'))
                image_reader_cache[key] = ImageReader(mark)
            return image_reader_cache[key]

        def draw_asset_center(asset, center_x, center_y, target_h):
            reader = asset_reader(asset)
            if reader is None:
                return False
            src_w, src_h = asset.get('size') or (1, 1)
            target_h = max(1.0, float(target_h))
            target_w = target_h * float(src_w) / max(1.0, float(src_h))
            pdf_canvas.drawImage(
                reader,
                center_x - target_w / 2.0,
                center_y - target_h / 2.0,
                width=target_w,
                height=target_h,
                mask='auto',
            )
            return True

        def draw_asset_text_center(text, center_x, center_y, target_h, seed):
            text = str(text or '').strip()
            if not text or any(ch not in '0123456789' for ch in text):
                return False
            assets = []
            total_w = 0.0
            gap = max(0.4, target_h * 0.03)
            for pos, ch in enumerate(text):
                asset = self.choose_teacher_mark_asset(ch, f'{seed}:{pos}:{ch}')
                if not asset:
                    return False
                src_w, src_h = asset.get('size') or (1, 1)
                w = target_h * float(src_w) / max(1.0, float(src_h))
                assets.append((asset, w))
                total_w += w
            total_w += gap * max(0, len(assets) - 1)
            x = center_x - total_w / 2.0
            for asset, w in assets:
                reader = asset_reader(asset)
                if reader is None:
                    return False
                pdf_canvas.drawImage(
                    reader,
                    x,
                    center_y - target_h / 2.0,
                    width=w,
                    height=target_h,
                    mask='auto',
                )
                x += w + gap
            return True

        def draw_asset_text_left(text, left_x, top_y, target_h, seed):
            text = str(text or '').strip()
            if not text or any(ch not in '0123456789' for ch in text):
                return False
            x = left_x
            gap = max(0.4, target_h * 0.03)
            for pos, ch in enumerate(text):
                asset = self.choose_teacher_mark_asset(ch, f'{seed}:{pos}:{ch}')
                if not asset:
                    return False
                src_w, src_h = asset.get('size') or (1, 1)
                w = target_h * float(src_w) / max(1.0, float(src_h))
                reader = asset_reader(asset)
                if reader is None:
                    return False
                pdf_canvas.drawImage(
                    reader,
                    x,
                    top_y - target_h,
                    width=w,
                    height=target_h,
                    mask='auto',
                )
                x += w + gap
            return True

        def draw_objective_symbol_pdf(center_x, center_y, symbol, size, width):
            size = max(6.0, float(size))
            width = max(0.8, float(width))
            half = size / 2.0
            try:
                pdf_canvas.setLineCap(1)
                pdf_canvas.setLineJoin(1)
            except Exception:
                pass
            pdf_canvas.setStrokeColorRGB(*marker_red)
            pdf_canvas.setLineWidth(width)
            if symbol == 'V':
                pdf_canvas.line(center_x - half * 0.55, center_y - half * 0.02, center_x - half * 0.14, center_y - half * 0.48)
                pdf_canvas.line(center_x - half * 0.14, center_y - half * 0.48, center_x + half * 0.62, center_y + half * 0.48)
                return
            pdf_canvas.line(center_x - half * 0.55, center_y - half * 0.55, center_x + half * 0.55, center_y + half * 0.55)
            pdf_canvas.line(center_x - half * 0.55, center_y + half * 0.55, center_x + half * 0.55, center_y - half * 0.55)

        for op in ops:
            op_type = op.get('type')
            raw_color = op.get('color')
            if isinstance(raw_color, (list, tuple)) and len(raw_color) >= 3:
                try:
                    values = [float(raw_color[index]) for index in range(3)]
                    op_color = tuple(value / 255.0 if value > 1.0 else value for value in values)
                except Exception:
                    op_color = marker_red
            else:
                op_color = marker_red
            pdf_canvas.setStrokeColorRGB(*op_color)
            pdf_canvas.setFillColorRGB(*op_color)
            if op_type == 'line':
                x1, y1, x2, y2 = op.get('points') or (0, 0, 0, 0)
                px1, py1 = map_point(x1, y1)
                px2, py2 = map_point(x2, y2)
                width = max(0.7, float(op.get('width') or 1) * (sx + sy) / 2.0)
                try:
                    pdf_canvas.setLineCap(1)
                    pdf_canvas.setLineJoin(1)
                except Exception:
                    pass
                pdf_canvas.setStrokeColorRGB(*op_color)
                pdf_canvas.setLineWidth(width)
                pdf_canvas.line(px1, py1, px2, py2)
            elif op_type == 'circle':
                center = op.get('center')
                if not center:
                    continue
                px, py = map_point(center[0], center[1])
                factor = (sx + sy) / 2.0
                radius = max(2.0, float(op.get('radius') or 24) * factor)
                width = max(0.7, float(op.get('width') or 4) * factor)
                pdf_canvas.setStrokeColorRGB(*op_color)
                pdf_canvas.setLineWidth(width)
                pdf_canvas.ellipse(px - radius, py - radius, px + radius, py + radius, stroke=1, fill=0)
            elif op_type == 'objective_symbol':
                center = op.get('center')
                if not center:
                    continue
                px, py = map_point(center[0], center[1])
                factor = (sx + sy) / 2.0
                draw_objective_symbol_pdf(
                    px,
                    py,
                    str(op.get('symbol') or 'X').upper(),
                    float(op.get('size') or 78) * factor,
                    float(op.get('width') or 12) * factor,
                )
            elif op_type == 'center_text':
                text = str(op.get('text') or '').strip()
                rect = op.get('rect')
                if not text or not rect:
                    continue
                left, bottom, right, top = map_rect(rect)
                font_size = max(6.0, float(op.get('font_size') or 32) * sy)
                font_name = chinese_pdf_font_name if op.get('prefer_chinese_font') else pdf_font_name
                pdf_canvas.setFont(font_name, font_size)
                text_w = pdf_canvas.stringWidth(text, font_name, font_size)
                x = (left + right - text_w) / 2.0
                y = (bottom + top) / 2.0 - font_size * 0.35
                pdf_canvas.drawString(x, y, text)
            elif op_type == 'text':
                text = str(op.get('text') or '').strip()
                xy = op.get('xy')
                if not text or not xy:
                    continue
                x, y_top = map_point(xy[0], xy[1])
                font_size = max(6.0, float(op.get('font_size') or 32) * sy)
                font_name = chinese_pdf_font_name if op.get('prefer_chinese_font') else pdf_font_name
                pdf_canvas.setFont(font_name, font_size)
                pdf_canvas.drawString(x, y_top - font_size * 0.78, text)

    def draw_overlay_ops_to_image(self, image, ops):
        draw = ImageDraw.Draw(image, 'RGBA')
        default_red = (220, 0, 0)
        for op in ops:
            op_type = op.get('type')
            raw_color = op.get('color')
            if isinstance(raw_color, (list, tuple)) and len(raw_color) >= 3:
                try:
                    color = tuple(max(0, min(255, int(round(float(raw_color[index]))))) for index in range(3))
                except Exception:
                    color = default_red
            else:
                color = default_red
            if op_type == 'line':
                x1, y1, x2, y2 = op.get('points') or (0, 0, 0, 0)
                width = max(1, int(round(float(op.get('width') or 1))))
                draw.line((x1, y1, x2, y2), fill=color, width=width)
            elif op_type == 'circle':
                center = op.get('center')
                if not center:
                    continue
                x, y = float(center[0]), float(center[1])
                radius = max(2, int(round(float(op.get('radius') or 24))))
                width = max(1, int(round(float(op.get('width') or 4))))
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=color, width=width)
            elif op_type == 'objective_symbol':
                center = op.get('center')
                if not center:
                    continue
                self.draw_objective_result_symbol(
                    draw,
                    center,
                    str(op.get('symbol') or 'X').upper(),
                    size=float(op.get('size') or 78),
                    width=max(1, int(round(float(op.get('width') or 12)))),
                )
            elif op_type == 'center_text':
                text = str(op.get('text') or '').strip()
                rect = op.get('rect')
                if not text or not rect:
                    continue
                x1, y1, x2, y2 = rect
                font_size = float(op.get('font_size') or 32)
                font = self.marked_card_font(int(round(font_size)), bold=bool(op.get('bold', True)))
                self.draw_centered_text_in_rect(draw, rect, text, font, fill=color)
            elif op_type == 'text':
                text = str(op.get('text') or '').strip()
                xy = op.get('xy')
                if not text or not xy:
                    continue
                font_size = float(op.get('font_size') or 32)
                font = self.marked_card_font(int(round(font_size)), bold=bool(op.get('bold', True)))
                draw.text((float(xy[0]), float(xy[1])), text, fill=color, font=font)

    def build_marked_answer_card_image(self, entry, side='front'):
        input_path = self.entry_image_path_for_side(entry, side)
        if not input_path.exists():
            raise RuntimeError(f"找不到原图：{entry.get('input_path') or entry.get('file')}")

        image, rotation_name = self.orient_entry_image_for_side(entry, input_path, side)
        if side == 'back' and self.is_mixed_objective_subjective_template():
            marked = image.convert('RGB')
            ops = self.collect_calculation_back_page_mark_ops(entry, marked.size)
            ops.extend(self.collect_wrong_knowledge_mark_ops(entry, None, side='back', image_size=marked.size))
            self.draw_overlay_ops_to_image(marked, ops)
            return marked

        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            temp_path = Path(tmp.name)
        image.save(temp_path)
        try:
            matrix = self.entry_alignment_homography(entry, side)
            if matrix is None:
                matrix, _marker_payload = self.detect_scan_homography(
                    temp_path,
                    side=side if entry.get('side_files') else None,
                )
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

        marked = image.convert('RGB')
        ops = []
        ops.extend(self.collect_objective_answer_mark_ops(entry, matrix, side=side if entry.get('side_files') else None))
        subjective_side = side if entry.get('side_files') else None
        ops.extend(self.collect_subjective_score_mark_ops(entry, matrix, side=subjective_side))
        ops.extend(self.collect_wrong_knowledge_mark_ops(entry, matrix, side=side, image_size=marked.size))
        if side == 'back' and self.is_direct_paper_choice_template():
            ops.extend(self.collect_direct_calculation_region_mark_ops(entry, matrix))
        if not entry.get('side_files') or side == 'front':
            if not self.is_direct_paper_choice_template():
                ops.extend(self.collect_big_question_score_mark_ops(entry, matrix))
            ops.extend(self.collect_total_score_header_ops(entry, marked.size, matrix))
        self.draw_overlay_ops_to_image(marked, ops)
        return marked

    def fit_marked_image_to_a4_page(self, image, dpi=200):
        a4_size = (int(round(8.27 * dpi)), int(round(11.69 * dpi)))
        page = Image.new('RGB', a4_size, 'white')
        image = image.convert('RGB')
        scale = min(a4_size[0] / max(1, image.width), a4_size[1] / max(1, image.height))
        target_size = (
            max(1, int(round(image.width * scale))),
            max(1, int(round(image.height * scale))),
        )
        resized = image.resize(target_size, Image.Resampling.LANCZOS)
        page.paste(resized, ((a4_size[0] - resized.width) // 2, (a4_size[1] - resized.height) // 2))
        return page

    def export_marked_card_image_pdf(self, pdf_path=None, silent=False):
        if not self.summary_data:
            if silent:
                raise ValueError('当前没有可导出的测试成绩')
            messagebox.showwarning('提示', '请先完成批改，才可以导出整卡标注PDF。')
            return

        self.apply_subjective_scores_to_entries()
        entries = self.checked_overlay_entries()
        if not entries:
            messagebox.showwarning('提示', '没有找到可导出的原始答题卡图片。')
            return

        base_name = '整卡标注'
        if self.current_session and self.current_session.get('name'):
            base_name = self.current_session.get('name')
        safe_name = re.sub(r'[\\/:*?"<>|]+', '_', base_name).strip(' .') or '整卡标注'
        # 校准 PDF 是固定尺子，不能继承当前透打偏移/缩放。
        # 否则“导出校准 -> 读取校准”会把已有校准再次叠加，数值越读越跑。
        offset_x_mm = 0.0
        offset_y_mm = 0.0
        scale_percent = 100.0
        initial_dir = self.last_open_dir if Path(str(self.last_open_dir)).exists() else str(PROJECT_DIR)
        pdf_path = pdf_path or filedialog.asksaveasfilename(
            title='保存整卡标注PDF',
            initialdir=initial_dir,
            initialfile=f'{safe_name}_整卡标注.pdf',
            defaultextension='.pdf',
            filetypes=[('PDF文件', '*.pdf'), ('所有文件', '*.*')],
        )
        if not pdf_path:
            return

        pages = []
        failed = []
        total = len(entries)
        try:
            for idx, entry in enumerate(entries, 1):
                self.status_var.set(f'正在生成整卡标注PDF：{idx}/{total}')
                self.root.update_idletasks()
                try:
                    sides = ['front', 'back'] if (entry.get('side_files') or {}).get('back') else ['front']
                    for side in sides:
                        marked = self.build_marked_answer_card_image(entry, side=side)
                        pages.append(self.fit_marked_image_to_a4_page(marked))
                except Exception as e:
                    raise RuntimeError(f"{entry.get('file', '')} 生成失败，整批停止，原文件已保留：{e}") from e

            if not pages:
                raise RuntimeError('没有成功生成任何整卡标注页面。')
            first, rest = pages[0], pages[1:]
            with tempfile.TemporaryDirectory(prefix='.marked-', dir=Path(pdf_path).parent) as folder:
                staged = Path(folder) / Path(pdf_path).name
                first.save(staged, 'PDF', save_all=True, append_images=rest, resolution=200.0)
                replace_file_batch([(staged, pdf_path)])
            self.register_linked_output('marked_card', pdf_path, [pdf_path], self.overlay_export_options())
            self.status_var.set(f'整卡标注PDF已导出：{pdf_path}')
            if silent:
                return [Path(pdf_path)]
            extra = ''
            if failed:
                extra = f'\n\n有 {len(failed)} 页生成失败，前几项：\n' + '\n'.join(failed[:5])
            messagebox.showinfo('导出完成', f'已导出整卡标注PDF：\n{pdf_path}{extra}')
        except Exception as e:
            self.status_var.set('整卡标注PDF导出失败')
            if silent:
                raise
            messagebox.showerror('导出失败', str(e))

    def build_marked_answer_card_overlay_image(self, entry, side='front'):
        input_path = self.entry_image_path_for_side(entry, side)
        if not input_path.exists():
            raise RuntimeError(f"找不到原图：{entry.get('input_path') or entry.get('file')}")

        image, _rotation_name = self.orient_entry_image_for_side(entry, input_path, side)
        if side == 'back' and self.is_mixed_objective_subjective_template():
            overlay = Image.new('RGB', image.size, 'white')
            ops = self.collect_calculation_back_page_mark_ops(entry, overlay.size)
            ops.extend(self.collect_wrong_knowledge_mark_ops(entry, None, side='back', image_size=overlay.size))
            self.draw_overlay_ops_to_image(overlay, ops)
            return overlay

        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            temp_path = Path(tmp.name)
        image.save(temp_path)
        try:
            matrix = self.entry_alignment_homography(entry, side)
            if matrix is None:
                matrix, _marker_payload = self.detect_scan_homography(
                    temp_path,
                    side=side if entry.get('side_files') else None,
                )
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

        overlay = Image.new('RGB', image.size, 'white')
        ops = []
        ops.extend(self.collect_objective_answer_mark_ops(entry, matrix, side=side if entry.get('side_files') else None))
        subjective_side = side if entry.get('side_files') else None
        ops.extend(self.collect_subjective_score_mark_ops(entry, matrix, side=subjective_side))
        ops.extend(self.collect_wrong_knowledge_mark_ops(entry, matrix, side=side, image_size=overlay.size))
        if side == 'back' and self.is_direct_paper_choice_template():
            ops.extend(self.collect_direct_calculation_region_mark_ops(entry, matrix))
        if not entry.get('side_files') or side == 'front':
            if not self.is_direct_paper_choice_template():
                ops.extend(self.collect_big_question_score_mark_ops(entry, matrix))
            ops.extend(self.collect_total_score_header_ops(entry, overlay.size, matrix))
        elif side == 'back' and self.is_mixed_objective_subjective_template():
            ops.extend(self.collect_calculation_back_page_mark_ops(entry, overlay.size))
        self.draw_overlay_ops_to_image(overlay, ops)
        return overlay

    def fit_overlay_to_a4_page(self, overlay, dpi=200, offset_x_mm=0.0, offset_y_mm=0.0, scale_percent=100.0):
        a4_size = (int(round(8.27 * dpi)), int(round(11.69 * dpi)))
        page = Image.new('RGB', a4_size, 'white')
        scale = max(50.0, min(150.0, float(scale_percent or 100.0))) / 100.0
        resized_size = (
            max(1, int(round(a4_size[0] * scale))),
            max(1, int(round(a4_size[1] * scale))),
        )
        resized = overlay.resize(resized_size, Image.Resampling.LANCZOS)
        px_per_mm = dpi / 25.4
        offset_x = int(round(float(offset_x_mm or 0.0) * px_per_mm))
        offset_y = int(round(float(offset_y_mm or 0.0) * px_per_mm))
        page.paste(resized, (offset_x, offset_y))
        return page

    def overlay_calibration_template_image_size(self, side='front'):
        side_payload = ((getattr(self, 'answer_config', {}) or {}).get('sides') or {}).get(side or 'front') or {}
        template_info = side_payload.get('template_image') or (getattr(self, 'answer_config', {}) or {}).get('template_image') or {}
        width = template_info.get('width')
        height = template_info.get('height')
        if width and height:
            return float(width), float(height)
        template_path = template_info.get('path')
        if template_path:
            path = Path(template_path)
            if not path.is_absolute():
                path = PROJECT_DIR / path
            if path.exists():
                with Image.open(path) as image:
                    return float(image.width), float(image.height)
        if self.summary_data:
            entry = self.summary_data[0]
            image_path = self.entry_image_path_for_side(entry, side)
            if image_path.exists():
                with Image.open(image_path) as image:
                    return float(image.width), float(image.height)
        return 2480.0, 3508.0

    def overlay_calibration_template_points(self, side='front'):
        points = self.get_template_marker_points(side)
        top_left = np.array(points[0], dtype=np.float32)
        top_right = np.array(points[1], dtype=np.float32)
        span = float(np.linalg.norm(top_right - top_left))
        # Keep calibration targets away from the black corner markers.  When the
        # red target is too close, scanned red/black edges can merge and bias the
        # detected center, which makes the final overlay drift.
        bottom_right = np.array(points[2], dtype=np.float32)
        bottom_left = np.array(points[3], dtype=np.float32)
        min_x = float(min(top_left[0], bottom_left[0]))
        max_x = float(max(top_right[0], bottom_right[0]))
        min_y = float(min(top_left[1], top_right[1]))
        max_y = float(max(bottom_left[1], bottom_right[1]))
        width = max_x - min_x
        height = max_y - min_y
        # These are internal calibration targets, not corner-marker centers.
        # The answer sheet itself may be produced by shrinking an A4 master on a
        # duplicator, so the real corner markers can sit outside an ordinary
        # printer's printable area. Keep red targets well inside the page.
        left_x = min_x + width * 0.30
        right_x = min_x + width * 0.70
        top_y = min_y + height * 0.24
        bottom_y = min_y + height * 0.68
        return [
            (left_x, top_y),
            (right_x, top_y),
            (right_x, bottom_y),
            (left_x, bottom_y),
        ]

    def export_overlay_marker_center_pdf(self, side='front', parent=None):
        side_label = '背面' if side == 'back' else '正面'
        initial_dir = self.last_open_dir if Path(str(self.last_open_dir)).exists() else str(PROJECT_DIR)
        output = filedialog.asksaveasfilename(
            title=f'保存{side_label}四角中心点PDF',
            initialdir=initial_dir,
            initialfile=f'{side_label}_四角中心点校验.pdf',
            defaultextension='.pdf',
            filetypes=[('PDF文件', '*.pdf'), ('所有文件', '*.*')],
            parent=parent,
        )
        if not output:
            return
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas as pdf_canvas_module
        except Exception as e:
            messagebox.showerror('导出失败', f'缺少PDF导出依赖 reportlab：{e}', parent=parent)
            return

        image_w, image_h = self.overlay_calibration_template_image_size(side)
        page_w, page_h = A4
        sx = page_w / max(1.0, image_w)
        sy = page_h / max(1.0, image_h)

        def map_point(x, y):
            return float(x) * sx, page_h - float(y) * sy

        pdf = pdf_canvas_module.Canvas(str(output), pagesize=A4, pageCompression=0)
        pdf.setLineWidth(1.8)
        labels = ['左上', '右上', '右下', '左下']
        for index, (x, y) in enumerate(self.get_template_marker_points(side), 1):
            px, py = map_point(x, y)
            radius = 7.0
            pdf.setStrokeColorRGB(1.0, 0.0, 0.0)
            pdf.setFillColorRGB(1.0, 0.0, 0.0)
            pdf.circle(px, py, radius, stroke=1, fill=0)
            pdf.line(px - radius * 1.6, py, px + radius * 1.6, py)
            pdf.line(px, py - radius * 1.6, px, py + radius * 1.6)
            pdf.setFillColorRGB(0.15, 0.15, 0.15)
            pdf.setFont('Helvetica-Bold', 8)
            pdf.drawString(px + 10, py + 8, f'{index}-{labels[index - 1]}')
        pdf.save()
        self.status_var.set(f'{side_label}四角中心点PDF已导出：{output}')
        messagebox.showinfo(
            '导出完成',
            f'已导出{side_label}四角中心点PDF：\n{output}\n\n'
            '这张PDF只用于诊断：打印到空心定位块答题卡上，看红十字是否落在四个空心框中心。',
            parent=parent,
        )

    def export_overlay_calibration_pdf(self, side='front', offset_x_mm=0.0, offset_y_mm=0.0, scale_percent=100.0, parent=None):
        side_label = '背面' if side == 'back' else '正面'
        initial_dir = self.last_open_dir if Path(str(self.last_open_dir)).exists() else str(PROJECT_DIR)
        output = filedialog.asksaveasfilename(
            title=f'保存{side_label}透打校准PDF',
            initialdir=initial_dir,
            initialfile=f'{side_label}_透打校准.pdf',
            defaultextension='.pdf',
            filetypes=[('PDF文件', '*.pdf'), ('所有文件', '*.*')],
            parent=parent,
        )
        if not output:
            return
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas as pdf_canvas_module
        except Exception as e:
            messagebox.showerror('导出失败', f'缺少PDF导出依赖 reportlab：{e}', parent=parent)
            return

        image_w, image_h = self.overlay_calibration_template_image_size(side)
        page_w, page_h = A4
        scale = max(50.0, min(150.0, float(scale_percent or 100.0))) / 100.0
        offset_x = float(offset_x_mm or 0.0) * 72.0 / 25.4
        offset_y = float(offset_y_mm or 0.0) * 72.0 / 25.4
        sx = page_w * scale / max(1.0, image_w)
        sy = page_h * scale / max(1.0, image_h)

        def map_point(x, y):
            return offset_x + float(x) * sx, page_h - (offset_y + float(y) * sy)

        pdf = pdf_canvas_module.Canvas(str(output), pagesize=A4, pageCompression=0)
        pdf.setLineWidth(1.8)
        for index, (x, y) in enumerate(self.overlay_calibration_template_points(side), 1):
            px, py = map_point(x, y)
            radius = 9.0
            pdf.setStrokeColorRGB(1.0, 0.0, 0.0)
            pdf.setFillColorRGB(1.0, 0.0, 0.0)
            pdf.circle(px, py, radius, stroke=1, fill=0)
            pdf.line(px - radius * 1.4, py, px + radius * 1.4, py)
            pdf.line(px, py - radius * 1.4, px, py + radius * 1.4)
            pdf.setFillColorRGB(0.15, 0.15, 0.15)
            pdf.setFont('Helvetica-Bold', 9)
            pdf.drawString(px + 14, py + 10, f'{side_label}-{index}')
        pdf.save()
        export_values = {
            'side': side,
            'offset_x': float(offset_x_mm or 0.0),
            'offset_y': float(offset_y_mm or 0.0),
            'scale': float(scale_percent or 100.0),
            'pdf_path': str(output),
            'created_at': datetime.now().isoformat(timespec='seconds'),
        }
        exports = dict(getattr(self, 'last_overlay_calibration_exports', {}) or {})
        exports[side] = export_values
        self.last_overlay_calibration_exports = exports
        matrices = dict(getattr(self, 'overlay_calibration_matrices', {}) or {})
        if side in matrices:
            matrices.pop(side, None)
            self.overlay_calibration_matrices = matrices
        try:
            Path(str(output) + '.calibration.json').write_text(json.dumps(export_values, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass
        try:
            global_export_path = PROJECT_DIR / 'overlay_calibration_last_exports.json'
            global_exports = {}
            if global_export_path.exists():
                global_exports = json.loads(global_export_path.read_text(encoding='utf-8-sig'))
                if not isinstance(global_exports, dict):
                    global_exports = {}
            global_exports[side] = export_values
            global_export_path.write_text(json.dumps(global_exports, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass
        self.status_var.set(f'{side_label}透打校准PDF已导出：{output}')
        messagebox.showinfo(
            '导出完成',
            f'已导出{side_label}透打校准PDF：\n{output}\n\n'
            '打印到一张有定位块的答题卡上，再把这张纸彩色扫描回来，点击“读取校准图”即可自动计算偏移。',
            parent=parent,
        )

    def detect_overlay_calibration_red_centers(self, image, expected_points):
        rgb = np.array(image.convert('RGB'))
        r = rgb[:, :, 0].astype(np.int16)
        g = rgb[:, :, 1].astype(np.int16)
        b = rgb[:, :, 2].astype(np.int16)
        mask = ((r > 105) & ((r - g) > 32) & ((r - b) > 32)).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        components = []
        for idx in range(1, num_labels):
            area = int(stats[idx, cv2.CC_STAT_AREA])
            if area < 8 or area > 6000:
                continue
            cx, cy = centroids[idx]
            x = int(stats[idx, cv2.CC_STAT_LEFT])
            y = int(stats[idx, cv2.CC_STAT_TOP])
            w = int(stats[idx, cv2.CC_STAT_WIDTH])
            h = int(stats[idx, cv2.CC_STAT_HEIGHT])
            ratio = w / max(1.0, float(h))
            is_target = area >= 1200 and w >= 45 and h >= 45 and 0.55 <= ratio <= 1.85
            components.append((float(cx), float(cy), area, is_target))
        if not components:
            raise RuntimeError('没有识别到红色校准点。请用彩色模式扫描，或确认校准PDF确实打印上去了。')

        chosen = []
        used = set()
        target_components = [item for item in components if item[3]]
        search_components = target_components if len(target_components) >= len(expected_points) else components
        for ex, ey in expected_points:
            best = None
            for comp_index, (cx, cy, area, _is_target) in enumerate(search_components):
                if comp_index in used:
                    continue
                dist = float(np.hypot(cx - ex, cy - ey))
                if best is None or dist < best[0]:
                    best = (dist, comp_index, cx, cy, area)
            if best is None or best[0] > max(180.0, image.width * 0.12):
                raise RuntimeError('红色校准点离预期位置太远，可能选错了扫描图或纸张方向不对。')
            used.add(best[1])
            chosen.append((best[2], best[3]))
        return chosen

    def remove_red_overlay_for_marker_detection(self, image):
        rgb = np.array(image.convert('RGB'))
        r = rgb[:, :, 0].astype(np.int16)
        g = rgb[:, :, 1].astype(np.int16)
        b = rgb[:, :, 2].astype(np.int16)
        mask = ((r > 105) & ((r - g) > 32) & ((r - b) > 32)).astype(np.uint8)
        mask = cv2.dilate(mask, np.ones((7, 7), np.uint8), iterations=1)
        rgb[mask > 0] = 255
        return Image.fromarray(rgb)

    def calculate_overlay_calibration_from_scan(self, scan_path, side='front', current_offset_x=0.0, current_offset_y=0.0, current_scale=100.0):
        image, _rotation = self.auto_orient_image(scan_path)
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            temp_path = Path(tmp.name)
        marker_image = self.remove_red_overlay_for_marker_detection(image)
        marker_image.save(temp_path)
        try:
            matrix, _payload = self.detect_scan_homography(temp_path, side=side)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
        template_points = self.overlay_calibration_template_points(side)
        expected = [apply_homography(matrix, point) for point in template_points]
        actual = self.detect_overlay_calibration_red_centers(image, expected)

        if len(expected) < 4 or len(actual) < 4:
            raise RuntimeError('四点校准需要识别到 4 个红色校准点。')
        exp_vec = np.array(expected[1], dtype=np.float32) - np.array(expected[0], dtype=np.float32)
        act_vec = np.array(actual[1], dtype=np.float32) - np.array(actual[0], dtype=np.float32)
        exp_v_vec = np.array(expected[2], dtype=np.float32) - np.array(expected[1], dtype=np.float32)
        act_v_vec = np.array(actual[2], dtype=np.float32) - np.array(actual[1], dtype=np.float32)
        exp_len = float(np.linalg.norm(exp_vec))
        act_len = float(np.linalg.norm(act_vec))
        exp_v_len = float(np.linalg.norm(exp_v_vec))
        act_v_len = float(np.linalg.norm(act_v_vec))
        if exp_len <= 1 or act_len <= 1 or exp_v_len <= 1 or act_v_len <= 1:
            raise RuntimeError('校准点距离异常，无法计算缩放。')
        scale_ratio = act_len / exp_len
        scale_v_ratio = act_v_len / exp_v_len
        # Do not auto-adjust scale from only the two top calibration points.
        # That distance is easily affected by scan perspective and red-center
        # detection, and a false 93%-style scale pulls all marks toward the
        # page's upper-left. Keep printer scaling fixed; use calibration for
        # translation only.
        new_scale = float(current_scale or 100.0) / max(1e-6, (scale_ratio + scale_v_ratio) / 2.0)

        exp_mid = np.mean(np.array(expected, dtype=np.float32), axis=0)
        act_mid = np.mean(np.array(actual, dtype=np.float32), axis=0)
        error_px = act_mid - exp_mid
        dx_mm = float(error_px[0]) * 210.0 / max(1.0, float(image.width))
        dy_mm = float(error_px[1]) * 297.0 / max(1.0, float(image.height))
        new_x = float(current_offset_x or 0.0) - dx_mm
        new_y = float(current_offset_y or 0.0) - dy_mm
        def affine_transform_from_points(src_points, dst_points):
            src_array = np.array(src_points, dtype=np.float64)
            dst_array = np.array(dst_points, dtype=np.float64)
            design = np.column_stack([src_array[:, 0], src_array[:, 1], np.ones(len(src_array))])
            ax, _res_x, _rank_x, _sing_x = np.linalg.lstsq(design, dst_array[:, 0], rcond=None)
            ay, _res_y, _rank_y, _sing_y = np.linalg.lstsq(design, dst_array[:, 1], rcond=None)
            return ax, ay

        def apply_affine(coeffs, point):
            ax, ay = coeffs
            x, y = float(point[0]), float(point[1])
            return (
                float(ax[0] * x + ax[1] * y + ax[2]),
                float(ay[0] * x + ay[1] * y + ay[2]),
            )

        scan_to_template = compute_homography(expected, template_points)
        actual_to_expected = affine_transform_from_points(actual, expected)
        compensated_points = [
            apply_homography(scan_to_template, apply_affine(actual_to_expected, point))
            for point in expected
        ]
        calibration_strength = 0.65
        compensated_points = [
            (
                float(src[0]) + (float(dst[0]) - float(src[0])) * calibration_strength,
                float(src[1]) + (float(dst[1]) - float(src[1])) * calibration_strength,
            )
            for src, dst in zip(template_points, compensated_points)
        ]
        compensation_matrix = compute_homography(template_points, compensated_points)
        return {
            'offset_x': round(new_x, 2),
            'offset_y': round(new_y, 2),
            'scale': round(new_scale, 2),
            'matrix': compensation_matrix,
            'raw': {
                'expected': expected,
                'actual': actual,
                'error_px': (float(error_px[0]), float(error_px[1])),
                'error_mm': (dx_mm, dy_mm),
                'scale_ratio': scale_ratio,
                'scale_v_ratio': scale_v_ratio,
            },
        }

    def ask_overlay_pdf_adjustments(self):
        win = tk.Toplevel(self.root)
        win.title('透打位置校准')
        win.geometry('980x820')
        win.minsize(920, 720)
        win.resizable(True, True)
        win.transient(self.root)
        win.grab_set()

        result = {'ok': False}
        front_x_var = tk.StringVar(value=f'{float(self.overlay_offset_x_mm_var.get()):g}')
        front_y_var = tk.StringVar(value=f'{float(self.overlay_offset_y_mm_var.get()):g}')
        front_scale_var = tk.StringVar(value=f'{float(self.overlay_scale_percent_var.get()):g}')
        back_x_var = tk.StringVar(value=f'{float(self.overlay_back_offset_x_mm_var.get()):g}')
        back_y_var = tk.StringVar(value=f'{float(self.overlay_back_offset_y_mm_var.get()):g}')
        back_scale_var = tk.StringVar(value=f'{float(self.overlay_back_scale_percent_var.get()):g}')
        display_mode_var = tk.StringVar(value='grade' if self.overlay_uses_grade() else 'score')
        saved_grade_mode = getattr(self, 'overlay_grade_mode', 'score')
        grade_mode_var = tk.StringVar(value='rank' if saved_grade_mode == 'rank' else 'score')
        saved_grade_thresholds = getattr(self, 'overlay_grade_thresholds', {}) or {}
        grade_threshold_vars = {
            grade: tk.StringVar(value=f'{float(saved_grade_thresholds.get(grade, value)):g}')
            for grade, value in DEFAULT_OVERLAY_GRADE_THRESHOLDS.items()
        }
        saved_grade_rank_thresholds = getattr(self, 'overlay_grade_rank_thresholds', {}) or {}
        grade_rank_vars = {}
        for grade, default_val in DEFAULT_OVERLAY_GRADE_RANK_THRESHOLDS.items():
            val = saved_grade_rank_thresholds.get(grade, default_val)
            val_str = '' if val is None else str(int(val))
            grade_rank_vars[grade] = tk.StringVar(value=val_str)
        saved_remainder = getattr(self, 'overlay_grade_rank_remainder', DEFAULT_OVERLAY_GRADE_RANK_REMAINDER)
        remainder_grade_var = tk.StringVar(value=str(saved_remainder or DEFAULT_OVERLAY_GRADE_RANK_REMAINDER).strip())
        direct_back_calibration = self.is_direct_paper_choice_template()
        read_baseline_values = {
            'front': (
                float(self.overlay_offset_x_mm_var.get()),
                float(self.overlay_offset_y_mm_var.get()),
                float(self.overlay_scale_percent_var.get()),
            ),
            'back': (
                float(self.overlay_back_offset_x_mm_var.get()),
                float(self.overlay_back_offset_y_mm_var.get()),
                float(self.overlay_back_scale_percent_var.get()),
            ),
        }
        scan_result_cache = {}

        def load_overlay_presets():
            try:
                if OVERLAY_CALIBRATION_PRESETS_PATH.exists():
                    data = json.loads(OVERLAY_CALIBRATION_PRESETS_PATH.read_text(encoding='utf-8-sig'))
                    if isinstance(data, dict):
                        return data
            except Exception:
                return {}
            return {}

        def save_overlay_presets(presets):
            try:
                OVERLAY_CALIBRATION_PRESETS_PATH.write_text(
                    json.dumps(presets, ensure_ascii=False, indent=2),
                    encoding='utf-8',
                )
            except Exception as e:
                messagebox.showerror('保存失败', f'保存位置预设失败：\n{e}', parent=win)
                return False
            return True

        def current_preset_payload():
            payload = {
                'front': {
                    'offset_x': float(front_x_var.get().strip() or 0),
                    'offset_y': float(front_y_var.get().strip() or 0),
                    'scale': float(front_scale_var.get().strip() or 100),
                },
                'back': {
                    'offset_x': float(back_x_var.get().strip() or 0),
                    'offset_y': float(back_y_var.get().strip() or 0),
                    'scale': float(back_scale_var.get().strip() or 100),
                },
                'matrices': copy.deepcopy(getattr(self, 'overlay_calibration_matrices', {}) or {}),
            }
            return payload

        def apply_preset_payload(payload):
            if not isinstance(payload, dict):
                messagebox.showwarning('提示', '这个位置预设格式不正确。', parent=win)
                return
            front = payload.get('front') or {}
            back = payload.get('back') or {}
            if front:
                set_side_values('front', front)
            if back and direct_back_calibration:
                set_side_values('back', back)
            matrices = payload.get('matrices')
            if isinstance(matrices, dict):
                self.overlay_calibration_matrices = copy.deepcopy(matrices)

        def refresh_preset_combo():
            presets = load_overlay_presets()
            names = sorted(str(name) for name in presets.keys())
            preset_combo['values'] = names
            if names and preset_name_var.get() not in names:
                preset_name_var.set(names[0])

        def save_current_as_preset():
            name = preset_name_var.get().strip()
            if not name:
                messagebox.showwarning('提示', '请先输入一个预设名称。', parent=win)
                return
            try:
                payload = current_preset_payload()
            except Exception:
                messagebox.showerror('参数错误', '偏移和缩放必须填写数字。', parent=win)
                return
            presets = load_overlay_presets()
            presets[name] = payload
            if save_overlay_presets(presets):
                refresh_preset_combo()
                preset_name_var.set(name)
                messagebox.showinfo('已保存', f'已保存位置预设：{name}', parent=win)

        def load_selected_preset():
            name = preset_name_var.get().strip()
            if not name:
                messagebox.showwarning('提示', '请先选择或输入一个预设名称。', parent=win)
                return
            presets = load_overlay_presets()
            payload = presets.get(name)
            if not payload:
                messagebox.showwarning('提示', f'没有找到位置预设：{name}', parent=win)
                return
            try:
                apply_preset_payload(payload)
            except Exception as e:
                messagebox.showerror('加载失败', str(e), parent=win)
                return
            messagebox.showinfo('已加载', f'已加载位置预设：{name}\n点击“保存并导出”后会使用这组参数。', parent=win)

        def delete_selected_preset():
            name = preset_name_var.get().strip()
            if not name:
                messagebox.showwarning('提示', '请先选择要删除的位置预设。', parent=win)
                return
            presets = load_overlay_presets()
            if name not in presets:
                messagebox.showwarning('提示', f'没有找到位置预设：{name}', parent=win)
                return
            if not messagebox.askyesno('确认删除', f'确定删除位置预设“{name}”吗？\n这不会影响当前已经填写的偏移参数。', parent=win):
                return
            presets.pop(name, None)
            if save_overlay_presets(presets):
                preset_name_var.set('')
                refresh_preset_combo()
                messagebox.showinfo('已删除', f'已删除位置预设：{name}', parent=win)

        ttk.Label(win, text='透打标注位置校准', font=('Arial', 12, 'bold')).pack(anchor='w', padx=18, pady=(16, 6))
        hint_text = (
            '正面和背面分开校准。导出会生成两份PDF：正面和背面都按倒序排页，适配打印机从PDF最后一页先打印。\n'
            '如果打印出来整体偏低，就把对应面的“纵向偏移”调成负数。打印时建议选择“实际大小/100%”。'
            if direct_back_calibration else
            '当前模板背面不做精确校准：正面照常校准；背面计算题只在大致上方打印24题分数、下方打印25题分数。'
        )
        ttk.Label(win, text=hint_text, wraplength=850).pack(anchor='w', padx=18, pady=(0, 12))

        preset_name_var = tk.StringVar()
        preset_frame = ttk.LabelFrame(win, text='位置预设')
        preset_frame.pack(fill=tk.X, padx=18, pady=(0, 8))
        ttk.Label(preset_frame, text='预设名称').pack(side=tk.LEFT, padx=(10, 4), pady=8)
        preset_combo = ttk.Combobox(preset_frame, textvariable=preset_name_var, width=24)
        preset_combo.pack(side=tk.LEFT, padx=4, pady=8)
        ttk.Button(preset_frame, text='加载预设', command=load_selected_preset).pack(side=tk.LEFT, padx=4)
        ttk.Button(preset_frame, text='保存当前为预设', command=save_current_as_preset).pack(side=tk.LEFT, padx=4)
        ttk.Button(preset_frame, text='删除预设', command=delete_selected_preset).pack(side=tk.LEFT, padx=4)
        ttk.Label(preset_frame, text='可为不同打印机、不同试卷纸张保存不同偏移。').pack(side=tk.LEFT, padx=(12, 4))
        refresh_preset_combo()

        form = ttk.Frame(win)
        form.pack(fill=tk.X, padx=18, pady=6)
        ttk.Label(form, text='面').grid(row=0, column=0, sticky='w', padx=4, pady=6)
        ttk.Label(form, text='横向偏移(mm)').grid(row=0, column=1, sticky='w', padx=4, pady=6)
        ttk.Label(form, text='纵向偏移(mm)').grid(row=0, column=2, sticky='w', padx=4, pady=6)
        ttk.Label(form, text='缩放比例(%)').grid(row=0, column=3, sticky='w', padx=4, pady=6)
        ttk.Label(form, text='说明').grid(row=0, column=4, sticky='w', padx=4, pady=6)
        ttk.Label(form, text='正面').grid(row=1, column=0, sticky='w', padx=4, pady=8)
        ttk.Entry(form, textvariable=front_x_var, width=12).grid(row=1, column=1, sticky='w', padx=4)
        ttk.Entry(form, textvariable=front_y_var, width=12).grid(row=1, column=2, sticky='w', padx=4)
        ttk.Entry(form, textvariable=front_scale_var, width=12).grid(row=1, column=3, sticky='w', padx=4)
        ttk.Label(form, text='正面PDF：奇数页倒序，如45,43...1').grid(row=1, column=4, sticky='w', padx=4)
        if direct_back_calibration:
            ttk.Label(form, text='背面').grid(row=2, column=0, sticky='w', padx=4, pady=8)
            ttk.Entry(form, textvariable=back_x_var, width=12).grid(row=2, column=1, sticky='w', padx=4)
            ttk.Entry(form, textvariable=back_y_var, width=12).grid(row=2, column=2, sticky='w', padx=4)
            ttk.Entry(form, textvariable=back_scale_var, width=12).grid(row=2, column=3, sticky='w', padx=4)
            ttk.Label(form, text='背面PDF：内部倒序，让打印机先打第一张纸的背面').grid(row=2, column=4, sticky='w', padx=4)
        else:
            ttk.Label(form, text='背面').grid(row=2, column=0, sticky='w', padx=4, pady=8)
            ttk.Label(form, text='不校准').grid(row=2, column=1, columnspan=3, sticky='w', padx=4)
            ttk.Label(form, text='只打印计算题分数的大致位置').grid(row=2, column=4, sticky='w', padx=4)
        ttk.Label(form, text='横向：负数向左，正数向右；纵向：负数向上，正数向下；缩放一般保持100。').grid(
            row=3, column=0, columnspan=5, sticky='w', padx=4, pady=(10, 0)
        )
        score_frame = ttk.LabelFrame(win, text='学生卷顶部结果')
        score_frame.pack(fill=tk.X, padx=18, pady=(6, 0))
        mode_row = ttk.Frame(score_frame)
        mode_row.pack(fill=tk.X, padx=10, pady=(7, 3))
        ttk.Label(mode_row, text='输出方式：').pack(side=tk.LEFT)
        ttk.Radiobutton(mode_row, text='分数', value='score', variable=display_mode_var).pack(side=tk.LEFT, padx=(2, 12))
        ttk.Radiobutton(mode_row, text='等级', value='grade', variable=display_mode_var).pack(side=tk.LEFT, padx=(2, 16))
        ttk.Checkbutton(
            mode_row,
            text='分数模式按100分制打印（取消则打印原始总分）',
            variable=self.overlay_print_percent_score_var,
        ).pack(side=tk.LEFT)

        grade_rule_row = ttk.Frame(score_frame)
        grade_rule_row.pack(fill=tk.X, padx=10, pady=(2, 3))
        ttk.Label(grade_rule_row, text='等级划分规则：').pack(side=tk.LEFT)
        ttk.Radiobutton(
            grade_rule_row,
            text='按分数段（标准分界）',
            value='score',
            variable=grade_mode_var,
            command=lambda: update_grade_panels(),
        ).pack(side=tk.LEFT, padx=(2, 12))
        ttk.Radiobutton(
            grade_rule_row,
            text='按人数固定（名次排名）',
            value='rank',
            variable=grade_mode_var,
            command=lambda: update_grade_panels(),
        ).pack(side=tk.LEFT, padx=(2, 16))

        # 1. 按分数段面板
        grade_score_panel = ttk.Frame(score_frame)
        ttk.Label(grade_score_panel, text='等级最低分（按100分制）：').pack(side=tk.LEFT)
        for grade in ('A+', 'A', 'B+', 'B', 'C+', 'C', 'D'):
            ttk.Label(grade_score_panel, text=f'{grade}≥').pack(side=tk.LEFT, padx=(5, 1))
            ttk.Entry(grade_score_panel, textvariable=grade_threshold_vars[grade], width=4).pack(side=tk.LEFT)
        ttk.Label(grade_score_panel, text='  E＜D').pack(side=tk.LEFT, padx=(6, 2))

        def restore_default_grade_thresholds():
            for grade, value in DEFAULT_OVERLAY_GRADE_THRESHOLDS.items():
                grade_threshold_vars[grade].set(f'{value:g}')

        ttk.Button(grade_score_panel, text='恢复默认分界', command=restore_default_grade_thresholds).pack(side=tk.LEFT, padx=(10, 0))

        # 2. 按人数固定面板
        grade_rank_panel = ttk.Frame(score_frame)
        rank_row1 = ttk.Frame(grade_rank_panel)
        rank_row1.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(rank_row1, text='各等级截止名次：').pack(side=tk.LEFT)
        for grade in ('A+', 'A', 'B+', 'B', 'C+', 'C', 'D'):
            ttk.Label(rank_row1, text=f'{grade}前').pack(side=tk.LEFT, padx=(5, 1))
            ttk.Entry(rank_row1, textvariable=grade_rank_vars[grade], width=4).pack(side=tk.LEFT)
            ttk.Label(rank_row1, text='名').pack(side=tk.LEFT, padx=(1, 2))

        rank_row2 = ttk.Frame(grade_rank_panel)
        rank_row2.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(rank_row2, text='其他名次统一设为：').pack(side=tk.LEFT)
        remainder_combo = ttk.Combobox(
            rank_row2,
            textvariable=remainder_grade_var,
            values=['A+', 'A', 'B+', 'B', 'C+', 'C', 'D', 'E'],
            width=4,
            state='readonly',
        )
        remainder_combo.pack(side=tk.LEFT, padx=(2, 8))

        def restore_default_grade_rank_thresholds():
            for grade, value in DEFAULT_OVERLAY_GRADE_RANK_THRESHOLDS.items():
                grade_rank_vars[grade].set('' if value is None else str(int(value)))
            remainder_grade_var.set(DEFAULT_OVERLAY_GRADE_RANK_REMAINDER)

        ttk.Button(rank_row2, text='恢复默认人数', command=restore_default_grade_rank_thresholds).pack(side=tk.LEFT, padx=4)
        ttk.Label(
            rank_row2,
            text='（提示：留空表示跳过该等级；同分学生并列同名次且得相同等级）',
            foreground='#666666',
        ).pack(side=tk.LEFT, padx=(10, 0))

        grade_tip_label = ttk.Label(
            score_frame,
            text='这些等级规则会自动记住；老师讲评透打默认使用相同的分数/等级方式。',
        )
        grade_tip_label.pack(anchor='w', padx=10, pady=(2, 7))

        def update_grade_panels():
            if grade_mode_var.get() == 'rank':
                grade_score_panel.pack_forget()
                grade_rank_panel.pack(fill=tk.X, padx=10, pady=(2, 5), before=grade_tip_label)
            else:
                grade_rank_panel.pack_forget()
                grade_score_panel.pack(fill=tk.X, padx=10, pady=(2, 5), before=grade_tip_label)

        update_grade_panels()

        def current_side_values(side):
            if side == 'back':
                return (
                    float(back_x_var.get().strip() or 0),
                    float(back_y_var.get().strip() or 0),
                    float(back_scale_var.get().strip() or 100),
                )
            return (
                float(front_x_var.get().strip() or 0),
                float(front_y_var.get().strip() or 0),
                float(front_scale_var.get().strip() or 100),
            )

        def set_side_values(side, values):
            target = (back_x_var, back_y_var, back_scale_var) if side == 'back' else (front_x_var, front_y_var, front_scale_var)
            target[0].set(f"{float(values['offset_x']):g}")
            target[1].set(f"{float(values['offset_y']):g}")
            target[2].set(f"{float(values['scale']):g}")

        def export_calibration_for_side(side):
            try:
                current_side_values(side)
            except Exception:
                messagebox.showerror('参数错误', '请先确认偏移和缩放填写的是数字。', parent=win)
                return
            self.export_overlay_calibration_pdf(
                side=side,
                offset_x_mm=0.0,
                offset_y_mm=0.0,
                scale_percent=100.0,
                parent=win,
            )

        def clear_calibration_matrix(side):
            matrices = dict(getattr(self, 'overlay_calibration_matrices', {}) or {})
            if side in matrices:
                matrices.pop(side, None)
                self.overlay_calibration_matrices = matrices
                self.save_ui_settings()
                messagebox.showinfo('已清除', f'已清除{"背面" if side == "back" else "正面"}四点校准。现在只使用上面的偏移和缩放。', parent=win)
            else:
                messagebox.showinfo('提示', f'{"背面" if side == "back" else "正面"}没有已保存的四点校准。', parent=win)

        def read_calibration_for_side(side):
            try:
                offset_x, offset_y, scale = current_side_values(side)
            except Exception:
                messagebox.showerror('参数错误', '请先确认偏移和缩放填写的是数字。', parent=win)
                return
            path = filedialog.askopenfilename(
                title=f'选择{"背面" if side == "back" else "正面"}校准扫描图',
                initialdir=self.last_open_dir if Path(str(self.last_open_dir)).exists() else str(PROJECT_DIR),
                filetypes=[('图片文件', '*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff'), ('所有文件', '*.*')],
                parent=win,
            )
            if not path:
                return
            try:
                scan_path = Path(path)
                try:
                    stat = scan_path.stat()
                    cache_key = (side, str(scan_path.resolve()), int(stat.st_mtime), int(stat.st_size))
                except Exception:
                    cache_key = (side, str(scan_path))
                if cache_key in scan_result_cache:
                    values = copy.deepcopy(scan_result_cache[cache_key])
                    set_side_values(side, values)
                    raw = values.get('raw') or {}
                    err = raw.get('error_mm') or (0, 0)
                    messagebox.showinfo(
                        '校准完成',
                        f'这张校准图已经读取过，已使用第一次的稳定结果：\n'
                        f'横向 {values["offset_x"]:g} mm，纵向 {values["offset_y"]:g} mm，缩放 {values["scale"]:g}%\n\n'
                        f'本次测得打印误差约：横向 {err[0]:.2f} mm，纵向 {err[1]:.2f} mm。',
                        parent=win,
                    )
                    return
                export_values = (getattr(self, 'last_overlay_calibration_exports', {}) or {}).get(side) or {}
                if not export_values:
                    sidecar_candidates = []
                    try:
                        sidecar_candidates = sorted(
                            scan_path.parent.glob('*.calibration.json'),
                            key=lambda item: item.stat().st_mtime,
                            reverse=True,
                        )
                    except Exception:
                        sidecar_candidates = []
                    for sidecar in sidecar_candidates:
                        try:
                            candidate = json.loads(sidecar.read_text(encoding='utf-8-sig'))
                        except Exception:
                            continue
                        if candidate.get('side') == side:
                            export_values = candidate
                            break
                if not export_values:
                    try:
                        global_export_path = PROJECT_DIR / 'overlay_calibration_last_exports.json'
                        global_exports = json.loads(global_export_path.read_text(encoding='utf-8-sig'))
                        if isinstance(global_exports, dict):
                            export_values = global_exports.get(side) or {}
                    except Exception:
                        export_values = {}
                if export_values:
                    offset_x = float(export_values.get('offset_x', offset_x))
                    offset_y = float(export_values.get('offset_y', offset_y))
                    scale = float(export_values.get('scale', scale))
                else:
                    offset_x, offset_y, scale = read_baseline_values.get(side, (offset_x, offset_y, scale))
                values = self.calculate_overlay_calibration_from_scan(
                    scan_path,
                    side=side,
                    current_offset_x=offset_x,
                    current_offset_y=offset_y,
                    current_scale=scale,
                )
                scan_result_cache[cache_key] = copy.deepcopy(values)
                if values.get('matrix'):
                    matrices = dict(getattr(self, 'overlay_calibration_matrices', {}) or {})
                    matrices[side] = values.get('matrix')
                    self.overlay_calibration_matrices = matrices
                set_side_values(side, values)
                raw = values.get('raw') or {}
                err = raw.get('error_mm') or (0, 0)
                messagebox.showinfo(
                    '校准完成',
                    f'已自动填入{"背面" if side == "back" else "正面"}校准参数：\n'
                    f'横向 {values["offset_x"]:g} mm，纵向 {values["offset_y"]:g} mm，缩放 {values["scale"]:g}%\n\n'
                    f'本次测得打印误差约：横向 {err[0]:.2f} mm，纵向 {err[1]:.2f} mm。',
                    parent=win,
                )
            except Exception as e:
                messagebox.showerror('校准失败', str(e), parent=win)

        calibration_frame = ttk.LabelFrame(win, text='自动校准（可选）')
        calibration_frame.pack(fill=tk.X, padx=18, pady=(10, 0))
        ttk.Label(
            calibration_frame,
            text='步骤：先导出校准PDF并打印到答题卡上，再彩色扫描回来，读取校准图即可自动填写上面的偏移和缩放。',
            wraplength=850,
        ).pack(anchor='w', padx=10, pady=(8, 4))
        calibration_buttons = ttk.Frame(calibration_frame)
        calibration_buttons.pack(fill=tk.X, padx=10, pady=(0, 4))
        ttk.Button(calibration_buttons, text='清除正面四点校准', command=lambda: clear_calibration_matrix('front')).pack(side=tk.LEFT, padx=4)
        ttk.Button(calibration_buttons, text='导出正面中心点PDF', command=lambda: self.export_overlay_marker_center_pdf('front', parent=win)).pack(side=tk.LEFT, padx=4)
        ttk.Button(calibration_buttons, text='导出正面校准PDF', command=lambda: export_calibration_for_side('front')).pack(side=tk.LEFT, padx=4)
        ttk.Button(calibration_buttons, text='读取正面校准图', command=lambda: read_calibration_for_side('front')).pack(side=tk.LEFT, padx=4)
        if direct_back_calibration:
            calibration_buttons_back = ttk.Frame(calibration_frame)
            calibration_buttons_back.pack(fill=tk.X, padx=10, pady=(0, 8))
            ttk.Button(calibration_buttons_back, text='清除背面四点校准', command=lambda: clear_calibration_matrix('back')).pack(side=tk.LEFT, padx=4)
            ttk.Button(calibration_buttons_back, text='导出背面中心点PDF', command=lambda: self.export_overlay_marker_center_pdf('back', parent=win)).pack(side=tk.LEFT, padx=4)
            ttk.Button(calibration_buttons_back, text='导出背面校准PDF', command=lambda: export_calibration_for_side('back')).pack(side=tk.LEFT, padx=4)
            ttk.Button(calibration_buttons_back, text='读取背面校准图', command=lambda: read_calibration_for_side('back')).pack(side=tk.LEFT, padx=4)

        def save_and_continue():
            try:
                front_x = float(front_x_var.get().strip() or 0)
                front_y = float(front_y_var.get().strip() or 0)
                front_scale = float(front_scale_var.get().strip() or 100)
                if front_scale <= 0:
                    raise ValueError
                if direct_back_calibration:
                    back_x = float(back_x_var.get().strip() or 0)
                    back_y = float(back_y_var.get().strip() or 0)
                    back_scale = float(back_scale_var.get().strip() or 100)
                    if back_scale <= 0:
                        raise ValueError
            except Exception:
                messagebox.showerror('参数错误', '偏移和缩放必须填写数字。', parent=win)
                return
            chosen_grade_mode = grade_mode_var.get().strip().lower()

            grade_thresholds = None
            try:
                grade_thresholds = {
                    grade: float(grade_threshold_vars[grade].get().strip())
                    for grade in ('A+', 'A', 'B+', 'B', 'C+', 'C', 'D')
                }
                ordered_values = [grade_thresholds[grade] for grade in ('A+', 'A', 'B+', 'B', 'C+', 'C', 'D')]
                if any(value < 0 or value > 100 for value in ordered_values):
                    raise ValueError
                if any(left <= right for left, right in zip(ordered_values, ordered_values[1:])):
                    if chosen_grade_mode == 'score':
                        messagebox.showerror(
                            '等级分界错误',
                            '等级最低分必须从 A+ 到 D 依次降低，不能相同或倒置。',
                            parent=win,
                        )
                        return
                    grade_thresholds = None
            except Exception:
                if chosen_grade_mode == 'score':
                    messagebox.showerror('等级分界错误', 'A+ 到 D 的等级最低分必须填写0到100之间的数字。', parent=win)
                    return
                grade_thresholds = None

            rank_thresholds = {}
            active_cutoffs = []
            has_rank_error = False
            for grade in ('A+', 'A', 'B+', 'B', 'C+', 'C', 'D'):
                val_str = grade_rank_vars[grade].get().strip()
                if val_str:
                    try:
                        val_int = int(val_str)
                        if val_int <= 0:
                            raise ValueError
                        rank_thresholds[grade] = val_int
                        active_cutoffs.append((grade, val_int))
                    except Exception:
                        has_rank_error = True
                        if chosen_grade_mode == 'rank':
                            messagebox.showerror('人数分界错误', f'{grade} 的截止名次必须填写大于0的正整数。', parent=win)
                            return
                else:
                    rank_thresholds[grade] = None

            if active_cutoffs:
                for i in range(len(active_cutoffs) - 1):
                    g1, c1 = active_cutoffs[i]
                    g2, c2 = active_cutoffs[i + 1]
                    if c1 >= c2:
                        has_rank_error = True
                        if chosen_grade_mode == 'rank':
                            messagebox.showerror(
                                '人数分界错误',
                                f'等级截止名次必须依次递增：\n{g1}（前{c1}人）必须小于 {g2}（前{c2}人）。',
                                parent=win,
                            )
                            return

            remainder_grade = remainder_grade_var.get().strip() or DEFAULT_OVERLAY_GRADE_RANK_REMAINDER

            self.overlay_offset_x_mm_var.set(front_x)
            self.overlay_offset_y_mm_var.set(front_y)
            self.overlay_scale_percent_var.set(front_scale)
            if direct_back_calibration:
                self.overlay_back_offset_x_mm_var.set(back_x)
                self.overlay_back_offset_y_mm_var.set(back_y)
                self.overlay_back_scale_percent_var.set(back_scale)
            self.overlay_total_display_mode_var.set('grade' if display_mode_var.get() == 'grade' else 'score')
            if grade_thresholds is not None:
                self.overlay_grade_thresholds = grade_thresholds
            self.overlay_grade_mode = 'rank' if chosen_grade_mode == 'rank' else 'score'
            if not has_rank_error:
                self.overlay_grade_rank_thresholds = rank_thresholds
            self.overlay_grade_rank_remainder = remainder_grade
            self.save_ui_settings()
            result['ok'] = True
            win.destroy()

        buttons = ttk.Frame(win)
        buttons.pack(side=tk.BOTTOM, fill=tk.X, padx=18, pady=(12, 12))
        ttk.Button(buttons, text='保存并导出', command=save_and_continue).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text='取消', command=win.destroy).pack(side=tk.LEFT, padx=4)
        win.bind('<Return>', lambda _e: save_and_continue())
        win.protocol('WM_DELETE_WINDOW', win.destroy)
        self.root.wait_window(win)
        return result['ok']

    def export_marked_card_overlay_pdf(self, pdf_path=None, silent=False):
        if not self.summary_data:
            if silent:
                raise ValueError('当前没有可导出的测试成绩')
            messagebox.showwarning('提示', '请先完成批改，才可以导出透打标注PDF。')
            return

        try:
            entries = self.checked_overlay_entries()
            self.apply_subjective_scores_to_entries()
        except Exception as exc:
            if silent:
                raise
            messagebox.showerror('无法导出', str(exc))
            return
        if not entries:
            messagebox.showwarning('提示', '没有找到可导出的原始答题卡图片。')
            return
        if not silent and not self.ask_overlay_pdf_adjustments():
            return

        base_name = '透打标注'
        if self.current_session and self.current_session.get('name'):
            base_name = self.current_session.get('name')
        safe_name = re.sub(r'[\\/:*?"<>|]+', '_', base_name).strip(' .') or '透打标注'
        initial_dir = self.last_open_dir if Path(str(self.last_open_dir)).exists() else str(PROJECT_DIR)
        pdf_path = pdf_path or filedialog.asksaveasfilename(
            title='保存正面倒序透打标注PDF（背面会自动另存为倒序PDF）',
            initialdir=initial_dir,
            initialfile=f'{safe_name}_正面倒序透打标注.pdf',
            defaultextension='.pdf',
            filetypes=[('PDF文件', '*.pdf'), ('所有文件', '*.*')],
        )
        if not pdf_path:
            return

        failed = []
        staging_dir = None
        try:
            try:
                from reportlab.lib.pagesizes import A4
                from reportlab.pdfbase import pdfmetrics
                from reportlab.pdfbase.ttfonts import TTFont
                from reportlab.pdfgen import canvas as pdf_canvas_module
            except Exception as e:
                raise RuntimeError(f'缺少PDF向量导出依赖 reportlab：{e}')

            self.overlay_pdf_font_name = 'Helvetica-Bold'
            for font_name, font_path in (
                ('OverlayArialBD', r'C:\Windows\Fonts\arialbd.ttf'),
                ('OverlayMSYHBD', r'C:\Windows\Fonts\msyhbd.ttc'),
                ('OverlaySimHei', r'C:\Windows\Fonts\simhei.ttf'),
                ('OverlayMSYH', r'C:\Windows\Fonts\msyh.ttc'),
            ):
                try:
                    if Path(font_path).exists():
                        pdfmetrics.registerFont(TTFont(font_name, font_path))
                        self.overlay_pdf_font_name = font_name
                        break
                except Exception:
                    continue
            self.overlay_pdf_chinese_font_name = self.overlay_pdf_font_name
            for font_name, font_path in (
                ('OverlayMSYHBD', r'C:\Windows\Fonts\msyhbd.ttc'),
                ('OverlaySimHei', r'C:\Windows\Fonts\simhei.ttf'),
                ('OverlayMSYH', r'C:\Windows\Fonts\msyh.ttc'),
                ('OverlayArialUnicode', r'C:\Windows\Fonts\arialuni.ttf'),
            ):
                try:
                    if Path(font_path).exists():
                        pdfmetrics.registerFont(TTFont(font_name, font_path))
                        self.overlay_pdf_chinese_font_name = font_name
                        break
                except Exception:
                    continue

            self._overlay_pdf_asset_reader_cache = {}

            output_path = Path(pdf_path)
            staging_dir = tempfile.TemporaryDirectory(prefix='.overlay-', dir=output_path.parent)
            staged_outputs = []
            front_jobs = list(reversed(entries))
            back_jobs = [entry for entry in reversed(entries) if (entry.get('side_files') or {}).get('back')]
            exported_paths = []

            def render_overlay_pdf(target_path, jobs, side, label, offset_x, offset_y, scale_percent):
                if not jobs:
                    return 0
                staged_path = Path(staging_dir.name) / target_path.name
                pdf = pdf_canvas_module.Canvas(str(staged_path), pagesize=A4, pageCompression=0)
                page_count = 0
                total_jobs = len(jobs)
                calibration_matrix = (getattr(self, 'overlay_calibration_matrices', {}) or {}).get(side)
                for idx, entry in enumerate(jobs, 1):
                    self.status_var.set(f'正在生成{label}透打标注PDF：{idx}/{total_jobs}')
                    self.root.update_idletasks()
                    try:
                        ops, image_size = self.build_marked_answer_card_overlay_ops(entry, side=side)
                        self.draw_overlay_ops_to_pdf_page(
                            pdf,
                            ops,
                            image_size,
                            A4,
                            offset_x_mm=float(offset_x),
                            offset_y_mm=float(offset_y),
                            scale_percent=float(scale_percent),
                            calibration_matrix=calibration_matrix,
                        )
                        pdf.showPage()
                        page_count += 1
                    except Exception as e:
                        raise RuntimeError(f"{label} {entry.get('file', '')} 生成失败；整批已停止，未跳页：{e}") from e
                if page_count > 0:
                    pdf.save()
                    exported_paths.append(target_path)
                    staged_outputs.append((staged_path, target_path))
                return page_count

            front_pages = render_overlay_pdf(
                output_path,
                front_jobs,
                'front',
                '正面倒序',
                self.overlay_offset_x_mm_var.get(),
                self.overlay_offset_y_mm_var.get(),
                self.overlay_scale_percent_var.get(),
            )
            if self.is_direct_paper_choice_template():
                back_offset_x = self.overlay_back_offset_x_mm_var.get()
                back_offset_y = self.overlay_back_offset_y_mm_var.get()
                back_scale = self.overlay_back_scale_percent_var.get()
                back_calibration_text = (
                    f'背面校准：横向 {back_offset_x:g} mm，纵向 {back_offset_y:g} mm，缩放 {back_scale:g}%。'
                )
            else:
                back_offset_x = 0.0
                back_offset_y = 0.0
                back_scale = 100.0
                back_calibration_text = '背面：不做校准，只在大致上方打印24题分数、下方打印25题分数。'
            back_path = output_path.with_name(f'{output_path.stem}_背面倒序{output_path.suffix}')
            back_pages = render_overlay_pdf(
                back_path,
                back_jobs,
                'back',
                '背面',
                back_offset_x,
                back_offset_y,
                back_scale,
            )

            if front_pages <= 0 and back_pages <= 0:
                raise RuntimeError('没有成功生成任何标注页面。')
            replace_file_batch(staged_outputs)
            self.register_linked_output('overlay', output_path, exported_paths,
                                        self.overlay_export_options())
            self.status_var.set(f'透打标注PDF已导出：{output_path}')
            if silent:
                return exported_paths
            extra = ''
            if failed:
                extra = f'\n\n有 {len(failed)} 页生成失败，前几项：\n' + '\n'.join(failed[:5])
            exported_text = '\n'.join(str(path) for path in exported_paths)
            back_text = (
                f'\n背面PDF已按“先打第一张纸背面”的倒序规则生成，共 {back_pages} 页。'
                if back_pages else '\n没有检测到背面文件，只生成正面PDF。'
            )
            messagebox.showinfo(
                '导出完成',
                f'已导出透打标注PDF：\n{exported_text}\n\n'
                f'正面PDF已按倒序生成，共 {front_pages} 页。\n'
                f'正面校准：横向 {self.overlay_offset_x_mm_var.get():g} mm，纵向 {self.overlay_offset_y_mm_var.get():g} mm，缩放 {self.overlay_scale_percent_var.get():g}%。\n'
                f'{back_calibration_text}'
                f'{back_text}\n'
                f'打印时建议先选“实际大小/100%”试一张。{extra}'
            )
        except Exception as e:
            self.status_var.set('透打标注PDF导出失败')
            if silent:
                raise
            messagebox.showerror('导出失败', str(e))
        finally:
            if staging_dir is not None:
                staging_dir.cleanup()

    def export_teacher_commentary_overlay_pdf(self, pdf_path=None, silent=False):
        if not self.summary_data:
            if silent:
                raise ValueError('当前没有可导出的测试成绩')
            messagebox.showwarning('提示', '请先完成批改，才可以生成老师讲评透打PDF。')
            return
        if self.current_template_mode == 'collector':
            if silent:
                raise ValueError('错题采集模板不支持讲评透打')
            messagebox.showwarning('提示', '错题采集模板没有完整逐题得分，不能生成讲评正确率。')
            return

        try:
            self.validate_overlay_context()
            self.apply_subjective_scores_to_entries()
        except Exception as exc:
            if silent:
                raise
            messagebox.showerror('无法导出', str(exc))
            return
        if not silent and not self.ask_overlay_pdf_adjustments():
            return

        base_name = '老师讲评'
        if self.current_session and self.current_session.get('name'):
            base_name = str(self.current_session.get('name') or base_name)
        safe_name = re.sub(r'[\\/:*?"<>|]+', '_', base_name).strip(' .') or '老师讲评'
        initial_dir = self.last_open_dir if Path(str(self.last_open_dir)).exists() else str(PROJECT_DIR)
        pdf_path = pdf_path or filedialog.asksaveasfilename(
            title='保存老师讲评透打PDF',
            initialdir=initial_dir,
            initialfile=f'{safe_name}_老师讲评透打.pdf',
            defaultextension='.pdf',
            filetypes=[('PDF文件', '*.pdf'), ('所有文件', '*.*')],
        )
        if not pdf_path:
            return

        staging_dir = None
        try:
            try:
                from reportlab.lib.pagesizes import A4
                from reportlab.pdfbase import pdfmetrics
                from reportlab.pdfbase.ttfonts import TTFont
                from reportlab.pdfgen import canvas as pdf_canvas_module
            except Exception as exc:
                raise RuntimeError(f'缺少PDF向量导出依赖 reportlab：{exc}')

            self.overlay_pdf_font_name = 'Helvetica-Bold'
            self.overlay_pdf_chinese_font_name = 'Helvetica-Bold'
            for font_name, font_path in (
                ('TeacherMSYHBD', r'C:\Windows\Fonts\msyhbd.ttc'),
                ('TeacherSimHei', r'C:\Windows\Fonts\simhei.ttf'),
                ('TeacherMSYH', r'C:\Windows\Fonts\msyh.ttc'),
            ):
                try:
                    if Path(font_path).exists():
                        pdfmetrics.registerFont(TTFont(font_name, font_path))
                        self.overlay_pdf_font_name = font_name
                        self.overlay_pdf_chinese_font_name = font_name
                        break
                except Exception:
                    continue

            side_payloads = (getattr(self, 'answer_config', {}) or {}).get('sides') or {}
            has_back = bool(
                side_payloads.get('back')
                or any(str(zone.get('side') or 'front').lower() == 'back' for zone in (getattr(self, 'answer_config', {}) or {}).get('zones', []) or [])
                or any(str(part.get('side') or 'front').lower() == 'back' for part in self.iter_subjective_parts())
                or self.calculation_question_config()
            )
            sides = ['front', 'back'] if has_back else ['front']
            staging_dir = tempfile.TemporaryDirectory(prefix='.commentary-', dir=Path(pdf_path).parent)
            staged = Path(staging_dir.name) / Path(pdf_path).name
            pdf = pdf_canvas_module.Canvas(str(staged), pagesize=A4, pageCompression=0)
            self._overlay_pdf_asset_reader_cache = {}
            page_count = 0
            for side in sides:
                image_size = self.overlay_calibration_template_image_size(side)
                ops = self.teacher_commentary_mark_ops(side, image_size)
                if side == 'front':
                    offset_x = self.overlay_offset_x_mm_var.get()
                    offset_y = self.overlay_offset_y_mm_var.get()
                    scale_percent = self.overlay_scale_percent_var.get()
                elif self.is_direct_paper_choice_template():
                    offset_x = self.overlay_back_offset_x_mm_var.get()
                    offset_y = self.overlay_back_offset_y_mm_var.get()
                    scale_percent = self.overlay_back_scale_percent_var.get()
                else:
                    offset_x = 0.0
                    offset_y = 0.0
                    scale_percent = 100.0
                calibration_matrix = (getattr(self, 'overlay_calibration_matrices', {}) or {}).get(side)
                self.draw_overlay_ops_to_pdf_page(
                    pdf,
                    ops,
                    image_size,
                    A4,
                    offset_x_mm=float(offset_x),
                    offset_y_mm=float(offset_y),
                    scale_percent=float(scale_percent),
                    calibration_matrix=calibration_matrix,
                )
                pdf.showPage()
                page_count += 1
            pdf.save()
            replace_file_batch([(staged, pdf_path)])
            self.register_linked_output('teacher_commentary', pdf_path, [pdf_path], self.overlay_export_options())
            self.status_var.set(f'老师讲评透打PDF已导出：{pdf_path}')
            if silent:
                return [Path(pdf_path)]
            messagebox.showinfo(
                '导出完成',
                f'已生成老师讲评透打PDF：\n{pdf_path}\n\n共 {page_count} 页；第1页顶部包含平均分和高于平均分名单。',
            )
        except Exception as exc:
            self.status_var.set('老师讲评透打PDF导出失败')
            if silent:
                raise
            messagebox.showerror('导出失败', str(exc))
        finally:
            if staging_dir is not None:
                staging_dir.cleanup()

    def open_marked_card_browser(self):
        if not self.summary_data:
            messagebox.showwarning('提示', '请先完成批改，才可以查看整卡标注预览。')
            return

        try:
            entries = self.checked_overlay_entries()
            self.apply_subjective_scores_to_entries()
        except Exception as exc:
            messagebox.showerror('无法预览', str(exc))
            return
        if not entries:
            messagebox.showwarning('提示', '没有找到可预览的原始答题卡图片。')
            return
        preview_pages = []
        for entry in entries:
            preview_pages.append({'entry': entry, 'side': 'front', 'side_label': '正面'})
            if (entry.get('side_files') or {}).get('back'):
                preview_pages.append({'entry': entry, 'side': 'back', 'side_label': '背面'})
        if not preview_pages:
            messagebox.showwarning('提示', '没有找到可预览的原始答题卡图片。')
            return

        win = tk.Toplevel(self.root)
        win.title('整卡标注预览')
        self.track_context_window(win)
        win.geometry('1120x820')
        win.minsize(820, 620)
        win.transient(self.root)

        state = {
            'index': 0,
            'photo': None,
            'cache': {},
            'preloading': set(),
            'preload_worker_running': False,
            'lock': threading.Lock(),
            'epoch': 0,
        }
        toolbar = ttk.Frame(win)
        toolbar.pack(fill=tk.X, padx=10, pady=(6, 4))
        info_var = tk.StringVar()
        search_var = tk.StringVar()

        # 第一行：常用操作与导航
        row1 = ttk.Frame(toolbar)
        row1.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(row1, text='上一张', command=lambda: show_index(state['index'] - 1)).pack(side=tk.LEFT, padx=3)
        ttk.Button(row1, text='下一张', command=lambda: show_index(state['index'] + 1)).pack(side=tk.LEFT, padx=3)
        ttk.Button(row1, text='重新生成当前', command=lambda: regenerate_current()).pack(side=tk.LEFT, padx=(8, 3))
        ttk.Button(row1, text='刷新全部预览', command=lambda: refresh_all_preview()).pack(side=tk.LEFT, padx=3)
        ttk.Button(row1, text='导出整卡标注PDF', command=self.export_marked_card_image_pdf).pack(side=tk.LEFT, padx=(10, 3))
        ttk.Button(row1, text='导出透打标注PDF', command=lambda: export_overlay_and_refresh()).pack(side=tk.LEFT, padx=3)
        ttk.Label(row1, text='查找:').pack(side=tk.LEFT, padx=(12, 2))
        search_entry = ttk.Entry(row1, textvariable=search_var, width=14)
        search_entry.pack(side=tk.LEFT, padx=2)
        ttk.Button(row1, text='搜索', command=lambda: search_student()).pack(side=tk.LEFT, padx=3)

        # 第二行：同步设置与状态显示
        row2 = ttk.Frame(toolbar)
        row2.pack(fill=tk.X, pady=(2, 2))

        browser_grade_mode_var = tk.StringVar(value=getattr(self, 'overlay_grade_mode', 'score'))

        ttk.Label(row2, text='顶部输出方式：').pack(side=tk.LEFT)
        ttk.Radiobutton(row2, text='分数', value='score', variable=self.overlay_total_display_mode_var, command=lambda: apply_output_mode()).pack(side=tk.LEFT, padx=2)
        ttk.Radiobutton(row2, text='等级', value='grade', variable=self.overlay_total_display_mode_var, command=lambda: apply_output_mode()).pack(side=tk.LEFT, padx=(2, 8))

        rank_mode_frame = ttk.Frame(row2)
        ttk.Label(rank_mode_frame, text='等级规则：').pack(side=tk.LEFT)
        ttk.Radiobutton(rank_mode_frame, text='按分数段', value='score', variable=browser_grade_mode_var, command=lambda: apply_rank_mode()).pack(side=tk.LEFT, padx=2)
        ttk.Radiobutton(rank_mode_frame, text='按人数固定（名次）', value='rank', variable=browser_grade_mode_var, command=lambda: apply_rank_mode()).pack(side=tk.LEFT, padx=2)

        def update_rank_mode_visibility():
            if self.overlay_uses_grade():
                rank_mode_frame.pack(side=tk.LEFT, padx=4)
            else:
                rank_mode_frame.pack_forget()

        def apply_output_mode():
            self.save_ui_settings()
            update_rank_mode_visibility()
            refresh_all_preview()

        def apply_rank_mode():
            self.overlay_grade_mode = browser_grade_mode_var.get()
            self.save_ui_settings()
            refresh_all_preview()

        def open_adjustments():
            if self.ask_overlay_pdf_adjustments():
                browser_grade_mode_var.set(getattr(self, 'overlay_grade_mode', 'score'))
                update_rank_mode_visibility()
                refresh_all_preview()

        def export_overlay_and_refresh():
            self.export_marked_card_overlay_pdf()
            browser_grade_mode_var.set(getattr(self, 'overlay_grade_mode', 'score'))
            update_rank_mode_visibility()
            refresh_all_preview()

        update_rank_mode_visibility()

        ttk.Button(row2, text='⚙ 等级/透打参数设置...', command=open_adjustments).pack(side=tk.LEFT, padx=8)
        ttk.Label(row2, textvariable=info_var, font=('Arial', 10, 'bold'), foreground='#1a5276').pack(side=tk.LEFT, padx=8)

        canvas = tk.Canvas(win, bg='#2f2f2f', highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        def pick_next_preload_index_locked():
            current = state['index']
            order = list(range(current + 1, len(preview_pages))) + list(range(0, current))
            for idx in order:
                if idx in state['cache'] or idx in state['preloading']:
                    continue
                return idx
            return None

        def ensure_preload_worker():
            # Render incrementally on Tk's thread. A background renderer used
            # mutable template state and Tk variables after a test switch.
            if not win.winfo_exists() or state['preload_worker_running']:
                return
            idx = pick_next_preload_index_locked()
            if idx is None:
                return
            state['preload_worker_running'] = True
            epoch = state['epoch']
            state['preloading'].add(idx)

            def render_next():
                if not win.winfo_exists():
                    state['preload_worker_running'] = False
                    return
                try:
                    if epoch != state['epoch']:
                        return
                    self.validate_overlay_context()
                    page = preview_pages[idx]
                    result = self.build_marked_answer_card_image(page['entry'], side=page['side'])
                    finish_preload(idx, result, None, epoch)
                except Exception as exc:
                    finish_preload(idx, None, exc, epoch)
                finally:
                    state['preloading'].discard(idx)
                    state['preload_worker_running'] = False
            win.after(80, render_next)

        def finish_preload(idx, image, error, epoch):
            with state['lock']:
                state['preloading'].discard(idx)
                if epoch != state['epoch']:
                    return
                if error is None and image is not None:
                    state['cache'][idx] = image
            if idx == state['index']:
                render_current()

        def schedule_preload_after_current():
            ensure_preload_worker()

        def search_student():
            keyword = search_var.get().strip().lower()
            if not keyword:
                return
            for idx, page in enumerate(preview_pages):
                entry = page['entry']
                haystack = ' '.join([
                    str(entry.get('score_id', '')),
                    str(entry.get('student_name', '')),
                    str(entry.get('file', '')),
                    str(page.get('side_label', '')),
                ]).lower()
                if keyword in haystack:
                    show_index(idx)
                    search_entry.select_range(0, tk.END)
                    return
            messagebox.showinfo('未找到', f'没有找到包含“{search_var.get().strip()}”的学生或文件。', parent=win)

        def render_current():
            if not win.winfo_exists():
                return
            canvas.delete('all')
            page = preview_pages[state['index']]
            entry = page['entry']
            side = page['side']
            try:
                self.validate_overlay_context()
                with state['lock']:
                    image = state['cache'].get(state['index'])
                if image is None:
                    image = self.build_marked_answer_card_image(entry, side=side)
                    with state['lock']:
                        state['cache'][state['index']] = image
            except Exception as e:
                info_var.set(f"生成失败：{e}")
                canvas.create_text(30, 30, anchor='nw', fill='white', text=str(e), font=('Arial', 14))
                return

            canvas_w = max(200, canvas.winfo_width() - 20)
            canvas_h = max(200, canvas.winfo_height() - 20)
            scale = min(canvas_w / image.width, canvas_h / image.height, 1.0)
            display_size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
            display = image.resize(display_size, Image.Resampling.LANCZOS)
            state['photo'] = ImageTk.PhotoImage(display)
            x = max(10, (canvas.winfo_width() - display_size[0]) // 2)
            y = max(10, (canvas.winfo_height() - display_size[1]) // 2)
            canvas.create_image(x, y, anchor='nw', image=state['photo'])
            canvas.config(scrollregion=(0, 0, canvas.winfo_width(), canvas.winfo_height()))

            res_str = self.printed_total_score_text(entry)
            if self.overlay_uses_grade():
                if self.overlay_grade_mode_is_rank():
                    rank = self.overlay_student_rank(entry)
                    detail_str = f"等级: {res_str} (第{rank}名)"
                else:
                    detail_str = f"等级: {res_str}"
            else:
                detail_str = f"成绩: {res_str}分"

            with state['lock']:
                cached_count = len(state['cache'])
                preloading_count = len(state['preloading'])
            info_var.set(
                f"{state['index'] + 1}/{len(preview_pages)}  "
                f"{entry.get('score_id', '')} {entry.get('student_name', '')}  {page.get('side_label', '')}  |  "
                f"{detail_str}  |  "
                f"缓存 {cached_count}/{len(preview_pages)}"
                + (f"  预处理中 {preloading_count}" if preloading_count else "")
            )
            schedule_preload_after_current()

        def show_index(index):
            state['index'] = max(0, min(len(preview_pages) - 1, index))
            render_current()

        def regenerate_current():
            with state['lock']:
                state['cache'].pop(state['index'], None)
            render_current()

        def refresh_all_preview():
            with state['lock']:
                state['epoch'] += 1
                state['cache'].clear()
                state['preloading'].clear()
            render_current()

        self._result_view_subscribers = getattr(self, '_result_view_subscribers', [])
        self._result_view_subscribers.append((win, refresh_all_preview))

        win.bind('<Left>', lambda _e: show_index(state['index'] - 1))
        win.bind('<Right>', lambda _e: show_index(state['index'] + 1))
        search_entry.bind('<Return>', lambda _e: search_student())
        canvas.bind('<Configure>', lambda _e: render_current())
        win.after(100, render_current)
