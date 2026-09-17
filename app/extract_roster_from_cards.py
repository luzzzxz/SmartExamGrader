#!/usr/bin/env python3
"""Extract student IDs and handwritten names from paired direct-paper scans.

Student cards are read as front/back pairs. Optional leading template pages can
be skipped explicitly. This helper writes review files only and never updates a
roster.
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import tkinter as tk
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from enhanced_answer_card_gui_stats import EnhancedAnswerCardStatsGUI, PADDLE_OCR_HELPER, PADDLE_OCR_PYTHON_CANDIDATES


def natural_key(path):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r'(\d+)', path.name)]


def find_font(size):
    for path in (
        Path(r'C:\Windows\Fonts\msyh.ttc'),
        Path(r'C:\Windows\Fonts\simhei.ttf'),
        Path(r'C:\Windows\Fonts\simsun.ttc'),
    ):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def find_ocr_python():
    for path in PADDLE_OCR_PYTHON_CANDIDATES:
        if Path(path).exists():
            return Path(path)
    raise RuntimeError('没有找到 PaddleOCR Python 环境。')


def run_paddle_ocr_batch(image_paths):
    payload = json.dumps(
        {'image_paths': [str(path) for path in image_paths], 'profile': 'v6_medium'},
        ensure_ascii=False,
    )
    child_env = dict(os.environ)
    # The grading app may be launched with a Python 3.14 dependency overlay.
    # PaddleOCR owns a Python 3.13 environment and must not import that overlay.
    child_env.pop('PYTHONPATH', None)
    completed = subprocess.run(
        [str(find_ocr_python()), str(PADDLE_OCR_HELPER)],
        input=payload.encode('utf-8'),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env,
        check=False,
        timeout=max(180, len(image_paths) * 12),
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode('utf-8', errors='replace')[-3000:]
        raise RuntimeError(f'PaddleOCR 批量识别失败：{detail}')
    stdout_text = completed.stdout.decode('utf-8-sig', errors='replace')
    for line in reversed(stdout_text.splitlines()):
        if line.lstrip().startswith('{'):
            return json.loads(line)
    raise RuntimeError(
        'PaddleOCR 没有返回识别结果。' +
        completed.stderr.decode('utf-8', errors='replace')[-1000:]
    )


def run_paddle_ocr(image_paths):
    combined = {'engine': 'PaddleOCR', 'profile': 'v6_medium', 'results': []}
    # Large all-class batches can finish without flushing JSON on Windows.
    # Smaller isolated batches also bound model memory use.
    for start in range(0, len(image_paths), 8):
        payload = run_paddle_ocr_batch(image_paths[start:start + 8])
        combined['results'].extend(payload.get('results') or [])
    return combined


def clean_name_candidate(text, recognized_id):
    text = str(text or '')
    text = text.replace('姓名', '').replace('初中物理组卷', '')
    text = text.replace(str(recognized_id or ''), '')
    text = re.sub(r'[0-9０-９:：.．,，;；_\-—\s]+', '', text)
    chinese = ''.join(re.findall(r'[\u3400-\u9fff]', text))
    for noise in ('初中物理组卷', '物理组卷', '组卷'):
        chinese = chinese.replace(noise, '')
    return chinese[:6]


def align_and_crop_name(app, image_path, recognition, output_path):
    oriented, _rotation = app.orient_image_by_rotation(image_path, recognition['rotation'])
    scan = cv2.cvtColor(np.array(oriented.convert('RGB')), cv2.COLOR_RGB2BGR)
    homography = np.asarray(recognition['homography'], dtype=float)
    template = app.answer_config['sides']['front']['template_image']
    width = int(template['width'])
    height = int(template['height'])
    aligned = cv2.warpPerspective(
        scan,
        np.linalg.inv(homography),
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    # The name and any teacher-written replacement ID occupy this fixed strip.
    x1, x2 = int(width * 0.49), int(width * 0.82)
    y1, y2 = int(height * 0.015), int(height * 0.115)
    crop = aligned[y1:y2, x1:x2]
    ok, encoded = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 96])
    if not ok:
        raise RuntimeError(f'无法保存姓名裁图：{image_path.name}')
    encoded.tofile(str(output_path))


def make_review_sheets(rows, output_dir):
    font = find_font(27)
    small_font = find_font(23)
    per_sheet = 10
    cell_w, cell_h = 940, 330
    for start in range(0, len(rows), per_sheet):
        subset = rows[start:start + per_sheet]
        sheet = Image.new('RGB', (cell_w * 2, cell_h * 5), 'white')
        draw = ImageDraw.Draw(sheet)
        for offset, row in enumerate(subset):
            col = offset % 2
            line = offset // 2
            left = col * cell_w
            top = line * cell_h
            draw.rectangle((left, top, left + cell_w - 1, top + cell_h - 1), outline='#777777', width=2)
            title = (
                f"{row['序号']:02d}  {row['文件']}  填涂:{row['填涂学号']}  "
                f"OCR:{row['姓名候选']}  学号最小差:{row['最小领先差']:.1f}"
            )
            draw.text((left + 12, top + 8), title, fill='black', font=font)
            crop = Image.open(row['姓名裁图']).convert('RGB')
            crop.thumbnail((cell_w - 24, cell_h - 70), Image.Resampling.LANCZOS)
            sheet.paste(crop, (left + 12, top + 58))
            if row['OCR原文'] and row['OCR原文'] != row['姓名候选']:
                draw.text((left + 12, top + cell_h - 32), row['OCR原文'][:42], fill='#555555', font=small_font)
        number = start // per_sheet + 1
        sheet.save(output_dir / f'姓名学号核对_{number}.jpg', quality=95)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('folder', type=Path)
    parser.add_argument('--template', default='direct_paper_choice_v1')
    parser.add_argument('--class-name', default='', help='复核文件中的班级名称；默认使用文件夹名')
    parser.add_argument('--skip-leading-pages', type=int, default=2, help='学生卷前要跳过的模板页数量')
    args = parser.parse_args()
    folder = args.folder.resolve()
    class_name = str(args.class_name or folder.name).strip() or '待确认班级'
    safe_class_name = re.sub(r'[\\/:*?"<>|]+', '_', class_name).strip(' .') or '待确认班级'
    images = sorted(
        [path for path in folder.iterdir() if path.suffix.lower() in {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tif', '.tiff'}],
        key=natural_key,
    )
    images = [path for path in images if not path.name.startswith('姓名核对_')]
    if len(images) < 4:
        raise SystemExit('图片数量不足。')
    if args.skip_leading_pages < 0 or args.skip_leading_pages >= len(images):
        raise SystemExit('要跳过的前置页数量不合理。')
    student_pages = images[args.skip_leading_pages:]
    if len(student_pages) % 2:
        raise SystemExit('去掉前置模板页后图片不是偶数张，无法按正反面配对。')
    front_pages = student_pages[0::2]

    output_dir = folder / '姓名学号提取_待确认'
    crop_dir = output_dir / '姓名裁图'
    crop_dir.mkdir(parents=True, exist_ok=True)

    root = tk.Tk()
    root.withdraw()
    app = EnhancedAnswerCardStatsGUI(root)
    app.load_template_by_key(args.template)
    rows = []
    try:
        for index, path in enumerate(front_pages, 1):
            recognition = app.recognize_direct_paper_side(path, side='front', read_student=True)
            row_details = recognition.get('student_id_rows') or []
            crop_path = crop_dir / f'{path.stem}_姓名.jpg'
            align_and_crop_name(app, path, recognition, crop_path)
            rows.append({
                '序号': index,
                '文件': path.name,
                '填涂学号': recognition.get('student_id') or '',
                '方向': recognition.get('rotation') or '',
                '最小领先差': min([float(item.get('gap') or 0) for item in row_details] or [0]),
                '最小相对黑度': min([float(item.get('best_adjusted') or 0) for item in row_details] or [0]),
                '姓名裁图': str(crop_path),
                '姓名候选': '',
                'OCR原文': '',
                'OCR置信度': '',
                '学号复核': '',
                '姓名复核': '',
            })
    finally:
        root.destroy()

    ocr_payload = run_paddle_ocr([Path(row['姓名裁图']) for row in rows])
    ocr_results = ocr_payload.get('results') or []
    for row, item in zip(rows, ocr_results):
        row['OCR原文'] = str(item.get('text') or '')
        row['OCR置信度'] = item.get('score') if item.get('score') is not None else ''
        row['姓名候选'] = clean_name_candidate(row['OCR原文'], row['填涂学号'])

    ids = Counter(row['填涂学号'] for row in rows if row['填涂学号'])
    for row in rows:
        reasons = []
        if '?' in row['填涂学号']:
            reasons.append('含?')
        if ids[row['填涂学号']] > 1:
            reasons.append('学号重复')
        if row['最小领先差'] < 12:
            reasons.append('填涂差值低')
        row['学号复核'] = '、'.join(reasons)
        if len(row['姓名候选']) < 2 or len(row['姓名候选']) > 4:
            row['姓名复核'] = '需看图'

    csv_path = output_dir / f'{safe_class_name}班姓名学号_待确认.csv'
    fields = [
        '序号', '文件', '填涂学号', '姓名候选', 'OCR原文', 'OCR置信度',
        '最小领先差', '最小相对黑度', '学号复核', '姓名复核', '方向', '姓名裁图',
    ]
    with csv_path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / f'{safe_class_name}班姓名学号_待确认.json').write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    make_review_sheets(rows, output_dir)
    print(json.dumps({
        'students': len(rows),
        'csv': str(csv_path),
        'duplicate_ids': sorted([student_id for student_id, count in ids.items() if count > 1]),
        'uncertain_ids': [row['文件'] for row in rows if row['学号复核']],
        'uncertain_names': [row['文件'] for row in rows if row['姓名复核']],
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
