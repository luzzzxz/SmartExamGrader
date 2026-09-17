from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Tuple

import cv2
import numpy as np
from PIL import Image

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE_JSON = PROJECT_DIR / 'marker_template.json'
DEFAULT_DEBUG_IMAGE = PROJECT_DIR / 'marker_detection_debug.png'
DEFAULT_ALIGNED_IMAGE = PROJECT_DIR / 'marker_aligned_preview.png'
EDGE_IGNORE_PX = 16


@dataclass
class MarkerSpec:
    key: str
    label: str
    roi: Tuple[float, float, float, float]  # x1, y1, x2, y2 in relative coords
    expected_ratio: float
    min_ratio: float
    max_ratio: float
    min_area_ratio: float
    max_area_ratio: float = 0.0025


MARKER_SPECS: List[MarkerSpec] = [
    MarkerSpec('top_left_big_square', '左上大方块', (0.00, 0.00, 0.22, 0.15), 1.0, 0.75, 1.30, 0.0006, 0.0018),
    MarkerSpec('top_right_small_square', '右上小方块', (0.78, 0.00, 1.00, 0.15), 1.0, 0.75, 1.30, 0.00030, 0.00085),
    MarkerSpec('bottom_left_vertical_rect', '左下竖矩形', (0.00, 0.85, 0.22, 1.00), 0.45, 0.20, 0.75, 0.00035, 0.0010),
    MarkerSpec('bottom_right_horizontal_rect', '右下横矩形', (0.78, 0.85, 1.00, 1.00), 2.35, 1.50, 3.50, 0.00035, 0.0012),
]


@dataclass
class Detection:
    key: str
    label: str
    bbox: Tuple[int, int, int, int]
    center: Tuple[float, float]
    area: float
    ratio: float
    score: float


ROTATIONS = [
    ('rot0', None),
    ('rot180', cv2.ROTATE_180),
    ('rot90cw', cv2.ROTATE_90_CLOCKWISE),
    ('rot90ccw', cv2.ROTATE_90_COUNTERCLOCKWISE),
]


def load_bgr(image_path: Path) -> np.ndarray:
    pil = Image.open(image_path).convert('RGB')
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def preprocess(bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, kernel)
    return th


def roi_to_abs(spec: MarkerSpec, width: int, height: int) -> Tuple[int, int, int, int]:
    x1 = int(spec.roi[0] * width)
    y1 = int(spec.roi[1] * height)
    x2 = int(spec.roi[2] * width)
    y2 = int(spec.roi[3] * height)
    return x1, y1, x2, y2


def contour_score(spec: MarkerSpec, x: int, y: int, w: int, h: int, area: float, img_w: int, img_h: int) -> float:
    if h <= 0 or w <= 0:
        return -1.0
    ratio = w / h
    if not (spec.min_ratio <= ratio <= spec.max_ratio):
        return -1.0
    area_ratio = area / (img_w * img_h)
    if not (spec.min_area_ratio <= area_ratio <= spec.max_area_ratio):
        return -1.0
    fill = area / max(w * h, 1)
    ratio_penalty = abs(ratio - spec.expected_ratio)
    score = area * 0.001 + fill * 10.0 - ratio_penalty * 6.0
    return float(score)


def detect_marker_in_roi(binary: np.ndarray, spec: MarkerSpec) -> Detection | None:
    img_h, img_w = binary.shape[:2]
    rx1, ry1, rx2, ry2 = roi_to_abs(spec, img_w, img_h)
    roi = binary[ry1:ry2, rx1:rx2]
    contours, _ = cv2.findContours(roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best = None
    best_score = -1e18
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area <= 0:
            continue
        x, y, w, h = cv2.boundingRect(cnt)
        ax, ay = rx1 + x, ry1 + y
        if ax <= EDGE_IGNORE_PX or ay <= EDGE_IGNORE_PX or ax + w >= img_w - EDGE_IGNORE_PX or ay + h >= img_h - EDGE_IGNORE_PX:
            # 扫描仪边缘黑边通常会贴边，优先排除
            continue
        score = contour_score(spec, ax, ay, w, h, area, img_w, img_h)
        if score > best_score:
            ratio = w / max(h, 1)
            best_score = score
            best = Detection(
                key=spec.key,
                label=spec.label,
                bbox=(ax, ay, w, h),
                center=(ax + w / 2, ay + h / 2),
                area=float(area),
                ratio=float(ratio),
                score=float(score),
            )
    return best


def validate_marker_layout(detections: Dict[str, Detection], img_w: int, img_h: int) -> bool:
    if len(detections) != 4:
        return False
    tl = detections.get('top_left_big_square')
    tr = detections.get('top_right_small_square')
    bl = detections.get('bottom_left_vertical_rect')
    br = detections.get('bottom_right_horizontal_rect')
    if not (tl and tr and bl and br):
        return False
    if tl.score <= 0 or tr.score <= 0 or bl.score <= 0 or br.score <= 0:
        return False
    # 左上大方块必须明显大于右上小方块
    if tl.area < tr.area * 1.15:
        return False
    # 四角形成的矩形几何对齐约束（上下边水平，左右边竖直）
    if abs(tl.center[1] - tr.center[1]) / img_h > 0.06:
        return False
    if abs(bl.center[1] - br.center[1]) / img_h > 0.06:
        return False
    if abs(tl.center[0] - bl.center[0]) / img_w > 0.06:
        return False
    if abs(tr.center[0] - br.center[0]) / img_w > 0.06:
        return False
    return True


def detect_markers_once(bgr: np.ndarray) -> Tuple[Dict[str, Detection], float]:
    binary = preprocess(bgr)
    img_h, img_w = binary.shape[:2]
    detections: Dict[str, Detection] = {}
    total_score = 0.0
    for spec in MARKER_SPECS:
        det = detect_marker_in_roi(binary, spec)
        if det is None:
            return {}, -1e18
        detections[spec.key] = det
        total_score += det.score
    if not validate_marker_layout(detections, img_w, img_h):
        return {}, -1e18
    return detections, total_score


def rotate_bgr(bgr: np.ndarray, code) -> np.ndarray:
    return bgr if code is None else cv2.rotate(bgr, code)


def detect_markers_with_orientation(bgr: np.ndarray, allowed_rotations=None):
    best = None
    for rotation_name, rotation_code in ROTATIONS:
        if allowed_rotations and rotation_name not in allowed_rotations:
            continue
        rotated = rotate_bgr(bgr, rotation_code)
        detections, score = detect_markers_once(rotated)
        if not detections:
            continue
        if best is None or score > best['score']:
            best = {
                'rotation': rotation_name,
                'score': score,
                'image': rotated,
                'detections': detections,
            }
    if best is None:
        raise RuntimeError('未能识别到完整的四个定位点，请检查模板图或标记形状。')
    return best


def draw_debug(bgr: np.ndarray, detections: Dict[str, Detection], out_path: Path):
    debug = bgr.copy()
    for spec in MARKER_SPECS:
        det = detections.get(spec.key)
        rx1, ry1, rx2, ry2 = roi_to_abs(spec, debug.shape[1], debug.shape[0])
        cv2.rectangle(debug, (rx1, ry1), (rx2, ry2), (180, 180, 0), 2)
        if det is None:
            continue
        x, y, w, h = det.bbox
        cv2.rectangle(debug, (x, y), (x + w, y + h), (0, 0, 255), 3)
        cv2.putText(
            debug,
            f"{spec.label} r={det.ratio:.2f}",
            (x, max(20, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
    cv2.imwrite(str(out_path), debug)


def to_jsonable(best_result: dict, source_file: str) -> dict:
    img = best_result['image']
    detections = best_result['detections']
    return {
        'source_file': source_file,
        'rotation': best_result['rotation'],
        'page_size': {'width': int(img.shape[1]), 'height': int(img.shape[0])},
        'markers': {
            key: {
                'label': det.label,
                'bbox': {'x': int(det.bbox[0]), 'y': int(det.bbox[1]), 'w': int(det.bbox[2]), 'h': int(det.bbox[3])},
                'center': {'x': round(det.center[0], 2), 'y': round(det.center[1], 2)},
                'area': round(det.area, 2),
                'ratio': round(det.ratio, 3),
                'score': round(det.score, 3),
            }
            for key, det in detections.items()
        },
    }


def save_template_config(best_result: dict, source_file: str, out_json: Path):
    payload = {
        'project': '六宫格阅卡',
        'kind': 'marker_template',
        **to_jsonable(best_result, source_file),
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def load_template_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def align_bgr_to_template(best_result: dict, template_cfg: dict) -> np.ndarray:
    detections = best_result['detections']
    src = []
    dst = []
    for spec in MARKER_SPECS:
        cur = detections[spec.key]
        ref = template_cfg['markers'][spec.key]
        src.append(cur.center)
        dst.append((ref['center']['x'], ref['center']['y']))
    src = np.array(src, dtype=np.float32)
    dst = np.array(dst, dtype=np.float32)
    H = cv2.getPerspectiveTransform(src, dst)
    img = best_result['image']
    width = int(template_cfg['page_size']['width'])
    height = int(template_cfg['page_size']['height'])
    warped = cv2.warpPerspective(img, H, (width, height), borderValue=(255, 255, 255))
    return warped


def align_to_template(best_result: dict, template_cfg: dict, out_path: Path):
    warped = align_bgr_to_template(best_result, template_cfg)
    cv2.imwrite(str(out_path), warped)


def align_image_path_to_template(image_path: Path, template_json_path: Path) -> tuple[np.ndarray, dict]:
    bgr = load_bgr(image_path)
    best = detect_markers_with_orientation(bgr)
    template_cfg = load_template_config(template_json_path)
    warped = align_bgr_to_template(best, template_cfg)
    return warped, best


def auto_pick_latest_template() -> Path:
    candidates = [p for p in PROJECT_DIR.iterdir() if p.is_file() and p.suffix.lower() in {'.jpg', '.jpeg', '.png'} and '模板' in p.name]
    if not candidates:
        raise SystemExit('未找到模板图片，请手动传入图片路径。')
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image', nargs='?', help='待检测图片路径；不填时自动使用项目目录下最新模板图')
    ap.add_argument('--write-template', action='store_true', help='将当前图片识别结果写入 marker_template.json')
    ap.add_argument('--template-json', default=str(DEFAULT_TEMPLATE_JSON), help='模板定位点 JSON，用于对齐预览')
    ap.add_argument('--debug-image', default=str(DEFAULT_DEBUG_IMAGE), help='调试图输出路径')
    ap.add_argument('--aligned-image', default=str(DEFAULT_ALIGNED_IMAGE), help='对齐图输出路径')
    args = ap.parse_args()

    image_path = Path(args.image) if args.image else auto_pick_latest_template()
    bgr = load_bgr(image_path)
    best = detect_markers_with_orientation(bgr)
    draw_debug(best['image'], best['detections'], Path(args.debug_image))
    result_json = to_jsonable(best, image_path.name)
    print(json.dumps(result_json, ensure_ascii=False, indent=2))

    if args.write_template:
        save_template_config(best, image_path.name, Path(args.template_json))
        print(f'Wrote template config: {args.template_json}')
    elif Path(args.template_json).exists():
        template_cfg = load_template_config(Path(args.template_json))
        align_to_template(best, template_cfg, Path(args.aligned_image))
        print(f'Wrote aligned preview: {args.aligned_image}')

    print(f'Wrote debug image: {args.debug_image}')


if __name__ == '__main__':
    main()
