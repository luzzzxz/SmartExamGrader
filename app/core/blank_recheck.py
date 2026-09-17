#!/usr/bin/env python3
"""
轻量级空白复核核心算法与数据集收集模块
用于在第一层高速 OCR 与第二层高精度 OCR 之间识别学生答题裁图是否存在真实笔迹，
防止真实学生手写答案因高速 OCR 漏读而被误判为空白 0 分。
"""

from __future__ import annotations

import cv2
from datetime import datetime
import json
import numpy as np
from pathlib import Path
import re
import shutil
import threading
from typing import Any, Dict, Optional, Tuple, Union


def safe_read_image(source: Union[str, Path, np.ndarray]) -> Optional[np.ndarray]:
    """安全读取图片，兼容 Windows 中文路径与内存 ndarray。"""
    if isinstance(source, np.ndarray):
        if len(source.shape) == 2:
            return cv2.cvtColor(source, cv2.COLOR_GRAY2BGR)
        return source
    try:
        path_str = str(source)
        if not Path(path_str).exists():
            return None
        return cv2.imdecode(np.fromfile(path_str, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None


def inspect_blank_crop(source: Union[str, Path, np.ndarray]) -> Dict[str, Any]:
    """
    对答题裁图进行底层图像特征分析，提取墨迹与连通域特征。
    """
    img = safe_read_image(source)
    if img is None or img.size == 0:
        return {
            'valid': False,
            'bg_brightness': 255.0,
            'total_hw_area': 0,
            'max_hw_area': 0,
            'max_hw_height': 0,
            'max_hw_width': 0,
            'comp_count': 0,
            'dark_ratio': 0.0,
            'components': [],
        }

    h, w = img.shape[:2]
    if h < 6 or w < 6:
        return {
            'valid': False,
            'bg_brightness': 255.0,
            'total_hw_area': 0,
            'max_hw_area': 0,
            'max_hw_height': 0,
            'max_hw_width': 0,
            'comp_count': 0,
            'dark_ratio': 0.0,
            'components': [],
        }

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # 1. 估计纸张白底灰度（取第 85 百分位数，通常在 195~240 之间）
    bg_brightness = float(np.percentile(gray, 85))

    # 2. 墨迹阈值：书写墨水显著深于纸张背景
    ink_thresh = max(40, min(195, int(bg_brightness - 35)))
    ink_mask = (gray < ink_thresh).astype(np.uint8)

    # 3. 过滤底部印刷下划线/基准线
    # 下划线特征：位于裁图下部 35%，宽度较大（>= 35% 裁图宽），高度很小（<= 14px）
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(ink_mask)
    for i in range(1, num_labels):
        x, y, cw, ch, area = stats[i]
        is_baseline = (
            y >= int(h * 0.65)
            and ch <= max(12, int(h * 0.20))
            and cw >= int(w * 0.35)
        )
        # 同时检查边缘残余框线（极边缘极细线条）
        is_top_border = (y == 0 and ch <= 3 and cw >= int(w * 0.50))
        if is_baseline or is_top_border:
            ink_mask[labels == i] = 0

    # 4. 提取有效笔画连通块（剔除孤立噪点：面积 < 15 且单向跨度 < 5）
    num_hw, hw_labels, hw_stats, _ = cv2.connectedComponentsWithStats(ink_mask)
    components = []
    for i in range(1, num_hw):
        x, y, cw, ch, area = hw_stats[i]
        if area < 15 and max(cw, ch) < 5:
            continue
        components.append({
            'x': int(x),
            'y': int(y),
            'w': int(cw),
            'h': int(ch),
            'area': int(area),
        })

    total_hw_area = sum(c['area'] for c in components)
    max_hw_area = max([c['area'] for c in components], default=0)
    max_hw_height = max([c['h'] for c in components], default=0)
    max_hw_width = max([c['w'] for c in components], default=0)
    dark_ratio = round(float(total_hw_area) / float(h * w or 1), 5)

    return {
        'valid': True,
        'width': w,
        'height': h,
        'bg_brightness': round(bg_brightness, 1),
        'ink_thresh': ink_thresh,
        'total_hw_area': total_hw_area,
        'max_hw_area': max_hw_area,
        'max_hw_height': max_hw_height,
        'max_hw_width': max_hw_width,
        'comp_count': len(components),
        'dark_ratio': dark_ratio,
        'components': components,
    }


def evaluate_crop_blank_status(source: Union[str, Path, np.ndarray]) -> Tuple[str, Dict[str, Any], str]:
    """
    复核裁图是否确实为空白，返回：
    - status: 'blank'（确实无笔迹）、'has_handwriting'（明显存在笔迹）、'uncertain'（无法确定）
    - features: 图像特征字典
    - reason: 说明原因
    """
    features = inspect_blank_crop(source)
    if not features.get('valid'):
        return 'blank', features, '裁图无效或为空白'

    total_area = features['total_hw_area']
    max_area = features['max_hw_area']
    max_h = features['max_hw_height']

    # 1. 确实无笔迹：面积极小、单块面积小、垂直跨度小
    if total_area < 80 and max_h < 14 and max_area < 55:
        return 'blank', features, f'空白复核确认无笔迹 (笔迹{total_area}px, 最大块{max_area}px)'

    # 2. 明显存在笔迹：
    # - 面积达到 280px 以上；
    # - 或者单连通块 >= 160px 且垂直跨度 >= 16px（单个汉字或数字笔画）；
    # - 或者垂直跨度达到 25px 且单连通块 >= 100px。
    has_hw = (
        total_area >= 280
        or (max_area >= 160 and max_h >= 16)
        or (max_h >= 25 and max_area >= 100)
    )
    if has_hw:
        return 'has_handwriting', features, f'空白复核检测到笔迹 (笔迹{total_area}px, 最大块{max_area}px, 高度{max_h}px)'

    # 3. 介于两者之间：疑似极轻笔迹、橡皮擦除痕迹或噪点，无法断定
    return 'uncertain', features, f'空白复核无法确定是否有笔迹 (笔迹{total_area}px, 最大块{max_area}px, 高度{max_h}px)'


class BlankRecheckDatasetCollector:
    """
    空白复核样本数据收集器。
    用于沉淀“答题区域图片 + 系统判断 + 人工判断（空白/非空白）”，
    为后续训练专用轻量二分类模型（Blank / Non-blank）积累标注数据集。
    """

    def __init__(self, dataset_dir: Optional[Union[str, Path]] = None):
        if dataset_dir is None:
            # 默认保存在主程序目录下的 blank_recheck_dataset
            dataset_dir = Path(__file__).resolve().parent.parent / 'blank_recheck_dataset'
        self.dataset_dir = Path(dataset_dir)
        self.images_dir = self.dataset_dir / 'images'
        self.metadata_file = self.dataset_dir / 'metadata.jsonl'
        self._lock = threading.Lock()
        self._ensure_dirs()

    def _ensure_dirs(self):
        try:
            self.images_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def record_sample(
        self,
        crop_path: Union[str, Path],
        system_judgment: str,
        features: Dict[str, Any],
        layer1_text: str = '',
        layer1_score: Optional[float] = None,
        layer2_text: Optional[str] = None,
        layer2_score: Optional[float] = None,
        session_id: str = '',
        student_name: str = '',
        score_id: str = '',
        part_id: str = '',
        label: str = '',
        entry_key: str = '',
    ) -> Optional[str]:
        """记录一个经过空白复核的样本及系统判断。"""
        src = Path(crop_path)
        if not src.exists():
            return None

        sample_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{score_id or 'std'}_{part_id or 'p'}_{src.stem}"
        safe_sample_id = re.sub(r'[^A-Za-z0-9_.-]+', '_', sample_id).strip('._-')
        dest_filename = f"{safe_sample_id}.jpg"
        dest_path = self.images_dir / dest_filename

        with self._lock:
            try:
                self._ensure_dirs()
                shutil.copy2(src, dest_path)
            except Exception:
                pass

            record = {
                'sample_id': safe_sample_id,
                'created_at': datetime.now().isoformat(timespec='seconds'),
                'image_rel_path': f"images/{dest_filename}",
                'session_id': session_id,
                'student_name': student_name,
                'score_id': score_id,
                'part_id': part_id,
                'label': label,
                'entry_key': entry_key,
                'layer1_text': layer1_text,
                'layer1_score': layer1_score,
                'system_judgment': system_judgment,
                'features': {
                    'bg_brightness': features.get('bg_brightness'),
                    'total_hw_area': features.get('total_hw_area'),
                    'max_hw_area': features.get('max_hw_area'),
                    'max_hw_height': features.get('max_hw_height'),
                    'comp_count': features.get('comp_count'),
                    'dark_ratio': features.get('dark_ratio'),
                },
                'layer2_text': layer2_text,
                'layer2_score': layer2_score,
                'manual_judgment': None,  # 待教师人工最终判分后回填
                'manual_score': None,
                'manual_updated_at': None,
            }

            try:
                with open(self.metadata_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(record, ensure_ascii=False) + '\n')
            except Exception:
                pass

        return safe_sample_id

    def update_layer2_result(
        self,
        crop_path: Union[str, Path],
        layer2_text: str,
        layer2_score: Optional[float] = None,
    ):
        """更新样本的第二层高精度 OCR 识别结果。"""
        if not self.metadata_file.exists():
            return
        filename = Path(crop_path).name
        with self._lock:
            try:
                lines = self.metadata_file.read_text(encoding='utf-8').splitlines()
                updated_lines = []
                for line in lines:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                        if Path(rec.get('image_rel_path', '')).stem.endswith(Path(filename).stem):
                            rec['layer2_text'] = layer2_text
                            rec['layer2_score'] = layer2_score
                        updated_lines.append(json.dumps(rec, ensure_ascii=False))
                    except Exception:
                        updated_lines.append(line)
                self.metadata_file.write_text('\n'.join(updated_lines) + '\n', encoding='utf-8')
            except Exception:
                pass

    def update_manual_judgment(
        self,
        session_id: str,
        part_id: str,
        score_id_or_entry: str,
        manual_score: Optional[float],
        manual_judgment: Optional[str] = None,
        manual_text: str = '',
    ):
        """当教师在界面打分或修改成绩时，自动关联更新样本的人工判断结果。"""
        if not self.metadata_file.exists():
            return

        # 若未显式传入 manual_judgment，根据分数或文字自动判定
        if manual_judgment is None:
            if manual_score is not None and manual_score > 0:
                manual_judgment = 'non_blank'
            elif manual_text and manual_text.strip() not in ('', '未识别到文字', '空白'):
                manual_judgment = 'non_blank'
            else:
                manual_judgment = 'blank'

        with self._lock:
            try:
                lines = self.metadata_file.read_text(encoding='utf-8').splitlines()
                updated_lines = []
                for line in lines:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                        match_session = (not session_id or rec.get('session_id') == session_id)
                        match_part = (rec.get('part_id') == part_id)
                        match_identity = (
                            rec.get('score_id') == score_id_or_entry
                            or rec.get('student_name') == score_id_or_entry
                            or rec.get('entry_key') == score_id_or_entry
                        )
                        if match_session and match_part and match_identity:
                            rec['manual_judgment'] = manual_judgment
                            rec['manual_score'] = manual_score
                            rec['manual_text'] = manual_text
                            rec['manual_updated_at'] = datetime.now().isoformat(timespec='seconds')
                        updated_lines.append(json.dumps(rec, ensure_ascii=False))
                    except Exception:
                        updated_lines.append(line)

                self.metadata_file.write_text('\n'.join(updated_lines) + '\n', encoding='utf-8')
            except Exception:
                pass

    def get_dataset_stats(self) -> Dict[str, Any]:
        """获取当前收集到的样本集统计信息。"""
        if not self.metadata_file.exists():
            return {'total': 0, 'blank': 0, 'non_blank': 0, 'pending_manual': 0}
        total = 0
        blank = 0
        non_blank = 0
        pending = 0
        try:
            with open(self.metadata_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    total += 1
                    mj = rec.get('manual_judgment')
                    if mj == 'blank':
                        blank += 1
                    elif mj == 'non_blank':
                        non_blank += 1
                    else:
                        pending += 1
        except Exception:
            pass
        return {
            'total': total,
            'blank': blank,
            'non_blank': non_blank,
            'pending_manual': pending,
        }


# 全局单例收集器
_global_dataset_collector: Optional[BlankRecheckDatasetCollector] = None


def get_blank_recheck_collector() -> BlankRecheckDatasetCollector:
    global _global_dataset_collector
    if _global_dataset_collector is None:
        _global_dataset_collector = BlankRecheckDatasetCollector()
    return _global_dataset_collector
