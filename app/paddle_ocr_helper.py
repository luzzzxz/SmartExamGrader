#!/usr/bin/env python3
import json
import os
import platform
import re
import sys
import time


def patch_windows_platform_probe():
    """Avoid PaddleX import hanging in Windows WMI platform detection."""
    if os.name != 'nt':
        return
    platform.system = lambda: 'Windows'
    platform.win32_ver = lambda *args, **kwargs: ('10', '10.0.0', 'SP0', 'Multiprocessor Free')
    platform.machine = lambda: 'AMD64'
    platform.processor = lambda: 'AMD64'

    def fast_uname():
        return platform.uname_result('Windows', '', '10.0.0', '10.0.0', 'AMD64', 'AMD64')

    platform.uname = fast_uname


def extract_page_text(page):
    data = getattr(page, 'json', page)
    if callable(data):
        data = data()
    if isinstance(data, dict) and 'res' in data:
        data = data.get('res') or {}
    if not isinstance(data, dict):
        return '', []

    texts = data.get('rec_texts') or []
    scores = data.get('rec_scores') or []
    cleaned = [re.sub(r'\s+', '', str(text or '')).strip() for text in texts]
    cleaned = [text for text in cleaned if text]
    return ''.join(cleaned), scores


def main():
    os.environ.setdefault('FLAGS_use_mkldnn', '0')

    payload_text = sys.stdin.buffer.read().decode('utf-8', errors='replace')
    payload = json.loads(payload_text)
    image_paths = payload.get('image_paths') or []
    if not image_paths:
        raise SystemExit('no image_paths')
    profile = str(payload.get('profile') or payload.get('model_profile') or 'v5_mobile').strip().lower()
    if profile in {'v6', 'v6_medium', 'pp-ocrv6', 'ppocrv6'}:
        det_model = payload.get('text_detection_model_name') or 'PP-OCRv6_medium_det'
        rec_model = payload.get('text_recognition_model_name') or 'PP-OCRv6_medium_rec'
        engine_name = 'PaddleOCRv6'
    else:
        det_model = payload.get('text_detection_model_name') or 'PP-OCRv5_mobile_det'
        rec_model = payload.get('text_recognition_model_name') or 'PP-OCRv5_mobile_rec'
        engine_name = 'PaddleOCR'

    patch_windows_platform_probe()
    from paddleocr import PaddleOCR

    start = time.time()
    ocr = PaddleOCR(
        text_detection_model_name=det_model,
        text_recognition_model_name=rec_model,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    init_seconds = time.time() - start

    results = []
    for image_path in image_paths:
        item_start = time.time()
        pages = ocr.predict(str(image_path))
        text_parts = []
        score_parts = []
        for page in pages:
            text, scores = extract_page_text(page)
            if text:
                text_parts.append(text)
            score_parts.extend(scores or [])
        score = None
        if score_parts:
            score = sum(float(value) for value in score_parts) / len(score_parts)
        results.append({
            'image_path': str(image_path),
            'text': ''.join(text_parts).strip(),
            'score': score,
            'seconds': round(time.time() - item_start, 3),
        })

    output = json.dumps(
        {
            'engine': 'PaddleOCR',
            'profile': profile,
            'det_model': det_model,
            'rec_model': rec_model,
            'engine_name': engine_name,
            'init_seconds': round(init_seconds, 3),
            'results': results,
        },
        ensure_ascii=False,
    )
    sys.stdout.buffer.write(output.encode('utf-8'))


if __name__ == '__main__':
    main()
