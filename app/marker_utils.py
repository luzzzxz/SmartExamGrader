from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import numpy as np
from PIL import Image, ImageDraw

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None

try:
    from marker_detector import detect_markers_once, load_bgr
except Exception:  # pragma: no cover
    detect_markers_once = None
    load_bgr = None


Point = Tuple[float, float]


def load_image_any(path: Path, pdf_zoom: float = 2.0) -> Image.Image:
    """读取图片或 PDF 首页面，统一转成 RGB 图片。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == '.pdf':
        if fitz is None:
            raise RuntimeError('读取 PDF 需要安装 PyMuPDF(fitz)')
        doc = fitz.open(path)
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=fitz.Matrix(pdf_zoom, pdf_zoom), alpha=False)
        return Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
    return Image.open(path).convert('RGB')


def resize_for_detection(img: Image.Image, max_side: int = 1000) -> tuple[Image.Image, float]:
    width, height = img.size
    longest = max(width, height)
    if longest <= max_side:
        return img.copy(), 1.0
    scale = max_side / float(longest)
    resized = img.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)
    return resized, scale


def otsu_threshold(gray: Image.Image) -> int:
    hist = gray.histogram()
    total = sum(hist)
    sum_total = sum(i * count for i, count in enumerate(hist))
    sum_back = 0.0
    weight_back = 0
    max_variance = -1.0
    threshold = 127
    for i, count in enumerate(hist):
        weight_back += count
        if weight_back == 0:
            continue
        weight_fore = total - weight_back
        if weight_fore == 0:
            break
        sum_back += i * count
        mean_back = sum_back / weight_back
        mean_fore = (sum_total - sum_back) / weight_fore
        variance = weight_back * weight_fore * (mean_back - mean_fore) ** 2
        if variance > max_variance:
            max_variance = variance
            threshold = i
    return threshold


def _connected_components(binary: bytearray, width: int, height: int) -> list[dict]:
    visited = bytearray(width * height)
    components: List[dict] = []
    neighbors = (-1, 1, -width, width)

    for idx, is_dark in enumerate(binary):
        if not is_dark or visited[idx]:
            continue
        visited[idx] = 1
        q = deque([idx])
        area = 0
        min_x = max_x = idx % width
        min_y = max_y = idx // width

        while q:
            cur = q.popleft()
            x = cur % width
            y = cur // width
            area += 1
            if x < min_x:
                min_x = x
            if x > max_x:
                max_x = x
            if y < min_y:
                min_y = y
            if y > max_y:
                max_y = y

            for step in neighbors:
                nxt = cur + step
                if nxt < 0 or nxt >= width * height:
                    continue
                nx = nxt % width
                ny = nxt // width
                if abs(nx - x) + abs(ny - y) != 1:
                    continue
                if binary[nxt] and not visited[nxt]:
                    visited[nxt] = 1
                    q.append(nxt)

        bbox_w = max_x - min_x + 1
        bbox_h = max_y - min_y + 1
        fill_ratio = area / max(1, bbox_w * bbox_h)
        components.append({
            'bbox': [min_x, min_y, max_x, max_y],
            'area': area,
            'width': bbox_w,
            'height': bbox_h,
            'fill_ratio': round(fill_ratio, 4),
            'center': ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0),
        })
    return components


def detect_corner_markers_by_shape(image_path: Path) -> dict | None:
    if detect_markers_once is None or load_bgr is None:
        return None
    try:
        bgr = load_bgr(Path(image_path))
        detections, score = detect_markers_once(bgr)
        if not detections:
            return None
        mapping = {
            'top_left': 'top_left_big_square',
            'top_right': 'top_right_small_square',
            'bottom_right': 'bottom_right_horizontal_rect',
            'bottom_left': 'bottom_left_vertical_rect',
        }
        markers: Dict[str, dict] = {}
        for generic_key, detector_key in mapping.items():
            det = detections.get(detector_key)
            if det is None:
                return None
            x, y, w, h = det.bbox
            fill_ratio = float(det.area) / max(w * h, 1)
            markers[generic_key] = {
                'center': {'x': round(det.center[0], 2), 'y': round(det.center[1], 2)},
                'bbox': {
                    'left': round(x, 2),
                    'top': round(y, 2),
                    'right': round(x + w, 2),
                    'bottom': round(y + h, 2),
                },
                'area_scaled': int(round(det.area)),
                'fill_ratio': round(fill_ratio, 4),
                'diagnostics': {
                    'width_scaled': int(w),
                    'height_scaled': int(h),
                    'ratio': round(float(det.ratio), 4),
                    'score': round(float(det.score), 4),
                    'detector': 'shape',
                },
            }
        return {
            'image_path': str(Path(image_path)),
            'image_size': {'width': int(bgr.shape[1]), 'height': int(bgr.shape[0])},
            'scaled_detection_size': {'width': int(bgr.shape[1]), 'height': int(bgr.shape[0])},
            'threshold': None,
            'scale': 1.0,
            'candidates_found': len(detections),
            'detector': 'shape',
            'markers': markers,
        }
    except Exception:
        return None


def detect_corner_markers(
    image_path: Path,
    *,
    max_side: int = 1000,
    manual_threshold: int | None = None,
    min_area_ratio: float = 0.00015,
    corner_window_ratio: float = 0.45,
) -> dict:
    """检测四角定位块，返回原图坐标系中的中心点与框。优先使用形状感知检测，失败时回退到通用黑块检测。"""
    shape_payload = detect_corner_markers_by_shape(image_path)
    if shape_payload is not None:
        return shape_payload

    img = load_image_any(Path(image_path))
    small, scale = resize_for_detection(img, max_side=max_side)
    gray = small.convert('L')
    threshold = manual_threshold if manual_threshold is not None else min(170, otsu_threshold(gray))
    pixels = gray.load()
    width, height = small.size
    binary = bytearray(1 if pixels[x, y] <= threshold else 0 for y in range(height) for x in range(width))
    components = _connected_components(binary, width, height)

    min_area = width * height * min_area_ratio
    candidates = []
    for comp in components:
        area = comp['area']
        w = comp['width']
        h = comp['height']
        aspect = max(w / max(1, h), h / max(1, w))
        if area < min_area:
            continue
        if aspect > 2.6:
            continue
        if comp['fill_ratio'] < 0.08:
            continue
        candidates.append(comp)

    if not candidates:
        raise RuntimeError('没有检测到像样的定位块候选，请检查模板是否清晰、定位块是否足够黑。')

    corner_targets = {
        'top_left': (0, 0),
        'top_right': (width - 1, 0),
        'bottom_right': (width - 1, height - 1),
        'bottom_left': (0, height - 1),
    }
    markers: Dict[str, dict] = {}
    used_ids = set()
    diag = (width ** 2 + height ** 2) ** 0.5

    def in_corner_window(name: str, center_x: float, center_y: float) -> bool:
        if 'left' in name and center_x > width * 0.35:
            return False
        if 'right' in name and center_x < width * 0.65:
            return False
        if 'top' in name and center_y > height * 0.35:
            return False
        if 'bottom' in name and center_y < height * 0.65:
            return False
        return True

    def edge_distance_ratio(name: str, center_x: float, center_y: float) -> float:
        values = []
        if 'left' in name:
            values.append(center_x / max(1.0, width))
        if 'right' in name:
            values.append((width - center_x) / max(1.0, width))
        if 'top' in name:
            values.append(center_y / max(1.0, height))
        if 'bottom' in name:
            values.append((height - center_y) / max(1.0, height))
        return sum(values) / max(1, len(values))

    def score_component(name: str, comp: dict, cx: float, cy: float, area_hint: float | None = None) -> float:
        center_x, center_y = comp['center']
        dist = ((center_x - cx) ** 2 + (center_y - cy) ** 2) ** 0.5 / max(1.0, diag)
        area_bonus = min(comp['area'] / (width * height * 0.002), 1.0) * 0.18
        aspect = max(comp['width'] / max(1, comp['height']), comp['height'] / max(1, comp['width']))
        aspect_penalty = max(0.0, aspect - 1.0) * 0.18
        fill_ratio = comp['fill_ratio']
        fill_penalty = 0.0 if fill_ratio >= 0.35 else (0.35 - fill_ratio) * 0.25
        score = dist + aspect_penalty + fill_penalty - area_bonus
        # 只对左下/右下角额外加强：必须更贴近边缘，且尺寸别明显偏离其余角块
        if name == 'bottom_left':
            score += edge_distance_ratio(name, center_x, center_y) * 2.6
            if area_hint is not None:
                score += abs(comp['area'] - area_hint) / max(area_hint, 1.0) * 0.65
            # 左下误检常来自页面内部填涂，这里直接惩罚“离左边/下边都不够近”的候选
            if center_x > width * 0.20:
                score += 1.5
            if center_y < height * 0.86:
                score += 1.5
        elif name == 'bottom_right':
            score += edge_distance_ratio(name, center_x, center_y) * 2.8
            if area_hint is not None:
                score += abs(comp['area'] - area_hint) / max(area_hint, 1.0) * 0.75
            # 右下误检常来自页面内部小黑块；不够靠右/靠下的候选直接重罚
            if center_x < width * 0.85:
                score += 2.5
            if center_y < height * 0.80:
                score += 2.0
            # 右下模板通常偏横向矩形，过于接近方块或竖向时加罚
            ratio_wh = comp['width'] / max(1, comp['height'])
            if ratio_wh < 1.3:
                score += 1.2
        return score

    chosen_components = {}
    area_reference = []

    for name, (cx, cy) in corner_targets.items():
        available = [(idx, comp) for idx, comp in enumerate(candidates) if idx not in used_ids]
        windowed = [(idx, comp) for idx, comp in available if in_corner_window(name, comp['center'][0], comp['center'][1])]
        pool = windowed if windowed else available

        area_hint = float(np.median(area_reference)) if area_reference else None
        scored = []
        for idx, comp in pool:
            score = score_component(name, comp, cx, cy, area_hint)
            scored.append((score, idx, comp))
        scored.sort(key=lambda item: item[0])

        best = None
        if name != 'bottom_left' and name != 'bottom_right':
            if scored:
                _, idx, comp = scored[0]
                best = (idx, comp)
        else:
            # 左下/右下角都使用更保守的挑选：先看前几名候选，明显不贴边或尺寸异常就跳过
            for _, idx, comp in scored[:6]:
                cx0, cy0 = comp['center']
                if name == 'bottom_left':
                    near_primary = cx0 <= width * 0.22
                    near_secondary = cy0 >= height * 0.80
                else:
                    near_primary = cx0 >= width * 0.85
                    near_secondary = cy0 >= height * 0.80
                area_ok = True
                if area_hint is not None:
                    area_ratio = comp['area'] / max(area_hint, 1.0)
                    area_ok = 0.40 <= area_ratio <= 2.0
                # 右下角额外硬性要求
                if name == 'bottom_right':
                    aspect_ok = (comp['width'] / max(1, comp['height'])) >= 1.3
                    # 必须足够大，避免被小填涂框骗走
                    if comp['area'] < 400:
                        continue
                else:
                    aspect_ok = True
                if near_primary and near_secondary and area_ok and aspect_ok:
                    best = (idx, comp)
                    break
            if best is None and scored:
                _, idx, comp = scored[0]
            # 右下角即使回退也必须面积足够，否则报错
            if name == 'bottom_right' and comp['area'] < 400:
                raise RuntimeError(f'右下角定位块失败：未找到面积≥400的候选，实际最优候选面积={comp["area"]}。请检查右下角定位块是否完整清晰。')
                best = (idx, comp)

        if best is None:
            raise RuntimeError(f'未能定位到 {name} 定位块，请检查该角定位块是否完整。')
        used_ids.add(best[0])
        comp = best[1]
        chosen_components[name] = comp
        area_reference.append(comp['area'])
        ox0, oy0, ox1, oy1 = [round(v / scale, 2) for v in comp['bbox']]
        ocx, ocy = round(comp['center'][0] / scale, 2), round(comp['center'][1] / scale, 2)
        markers[name] = {
            'center': {'x': ocx, 'y': ocy},
            'bbox': {'left': ox0, 'top': oy0, 'right': ox1, 'bottom': oy1},
            'area_scaled': comp['area'],
            'fill_ratio': comp['fill_ratio'],
            'diagnostics': {
                'width_scaled': comp['width'],
                'height_scaled': comp['height'],
                'edge_distance_ratio': round(edge_distance_ratio(name, comp['center'][0], comp['center'][1]), 4),
            },
        }

    return {
        'image_path': str(Path(image_path)),
        'image_size': {'width': img.width, 'height': img.height},
        'scaled_detection_size': {'width': width, 'height': height},
        'threshold': threshold,
        'scale': scale,
        'candidates_found': len(candidates),
        'markers': markers,
    }


def save_marker_debug_image(image_path: Path, marker_payload: dict, output_path: Path) -> None:
    img = load_image_any(Path(image_path)).copy()
    draw = ImageDraw.Draw(img)
    for name, marker in marker_payload['markers'].items():
        bbox = marker['bbox']
        draw.rectangle((bbox['left'], bbox['top'], bbox['right'], bbox['bottom']), outline='red', width=6)
        c = marker['center']
        draw.ellipse((c['x'] - 10, c['y'] - 10, c['x'] + 10, c['y'] + 10), outline='blue', width=4)
        draw.text((bbox['left'], bbox['top'] - 24), name, fill='red')
    img.save(output_path)


def solve_linear_system(matrix: List[List[float]], values: List[float]) -> List[float]:
    n = len(values)
    a = [row[:] + [values[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise RuntimeError('线性方程组不可解，无法计算透视变换。')
        a[col], a[pivot] = a[pivot], a[col]
        pivot_val = a[col][col]
        for j in range(col, n + 1):
            a[col][j] /= pivot_val
        for r in range(n):
            if r == col:
                continue
            factor = a[r][col]
            if abs(factor) < 1e-12:
                continue
            for j in range(col, n + 1):
                a[r][j] -= factor * a[col][j]
    return [a[i][n] for i in range(n)]


def compute_homography(src_points: Iterable[Point], dst_points: Iterable[Point]) -> List[List[float]]:
    src = list(src_points)
    dst = list(dst_points)
    if len(src) != 4 or len(dst) != 4:
        raise ValueError('计算透视变换需要 4 对点。')
    matrix = []
    values = []
    for (x, y), (u, v) in zip(src, dst):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        values.append(u)
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        values.append(v)
    a, b, c, d, e, f, g, h = solve_linear_system(matrix, values)
    return [
        [a, b, c],
        [d, e, f],
        [g, h, 1.0],
    ]


def apply_homography(matrix: List[List[float]], point: Point) -> Point:
    x, y = point
    denom = matrix[2][0] * x + matrix[2][1] * y + matrix[2][2]
    if abs(denom) < 1e-12:
        raise RuntimeError('透视变换分母接近 0，点投影失败。')
    tx = (matrix[0][0] * x + matrix[0][1] * y + matrix[0][2]) / denom
    ty = (matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]) / denom
    return round(tx, 2), round(ty, 2)


def ordered_marker_points(marker_payload: dict) -> List[Point]:
    markers = marker_payload['markers']
    return [
        (markers['top_left']['center']['x'], markers['top_left']['center']['y']),
        (markers['top_right']['center']['x'], markers['top_right']['center']['y']),
        (markers['bottom_right']['center']['x'], markers['bottom_right']['center']['y']),
        (markers['bottom_left']['center']['x'], markers['bottom_left']['center']['y']),
    ]


def marker_geometry_stats(marker_payload: dict) -> dict:
    pts = ordered_marker_points(marker_payload)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    diag = (width ** 2 + height ** 2) ** 0.5
    return {
        'width': round(width, 4),
        'height': round(height, 4),
        'diag': round(diag, 4),
    }


def four_marker_signature(point: Point, marker_points: List[Point], normalize_by: float) -> dict:
    names = ['top_left', 'top_right', 'bottom_right', 'bottom_left']
    sig = {}
    for name, (mx, my) in zip(names, marker_points):
        dist = ((point[0] - mx) ** 2 + (point[1] - my) ** 2) ** 0.5
        sig[name] = round(dist / max(1e-6, normalize_by), 6)
    return sig


def signature_error(sig_a: dict, sig_b: dict) -> float:
    keys = ['top_left', 'top_right', 'bottom_right', 'bottom_left']
    return sum(abs(sig_a[k] - sig_b[k]) for k in keys) / len(keys)
