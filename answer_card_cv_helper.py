from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Check potential locations for bundled dependencies:
# 1. BASE_DIR / "runtime" / "pydeps314" (portable standalone package)
# 2. BASE_DIR / "pydeps314" (original Codex launcher directory)
for candidate in [BASE_DIR / "runtime" / "pydeps314", BASE_DIR / "pydeps314"]:
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
        break

import cv2
import numpy as np
from PIL import Image

# Check potential locations for app modules (marker_detector, marker_utils):
# 1. BASE_DIR / "app" (portable package)
# 2. BASE_DIR (if run from app dir directly)
# 3. Fallback to legacy dev path if present
for candidate in [
    BASE_DIR / "app",
    BASE_DIR,
    Path(r"C:\Users\Administrator\.openclaw\workspace\answer_card_autograder"),
]:
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
        break

from marker_detector import detect_markers_with_orientation  # noqa: E402
from marker_utils import detect_corner_markers  # noqa: E402


def orient_image(input_path: Path, output_image_path: Path, output_meta_path: Path) -> int:
    pil_img = Image.open(input_path).convert("RGB")
    bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    best = detect_markers_with_orientation(bgr)
    oriented_bgr = best["image"]
    oriented_rgb = cv2.cvtColor(oriented_bgr, cv2.COLOR_BGR2RGB)
    Image.fromarray(oriented_rgb).save(output_image_path)
    output_meta_path.write_text(
        json.dumps({"rotation": best.get("rotation", "rot0")}, ensure_ascii=False),
        encoding="utf-8",
    )
    return 0


def detect_markers(input_path: Path, output_meta_path: Path) -> int:
    attempts = [
        {"max_side": 1000, "manual_threshold": None, "min_area_ratio": 0.00015},
        {"max_side": 1400, "manual_threshold": None, "min_area_ratio": 0.00012},
        {"max_side": 1800, "manual_threshold": None, "min_area_ratio": 0.00010},
        {"max_side": 1400, "manual_threshold": 150, "min_area_ratio": 0.00012},
        {"max_side": 1800, "manual_threshold": 160, "min_area_ratio": 0.00010},
        {"max_side": 2200, "manual_threshold": 170, "min_area_ratio": 0.00008},
    ]
    errors = []

    for attempt in attempts:
        try:
            payload = detect_corner_markers(input_path, **attempt)
            payload["helper_attempt"] = attempt
            output_meta_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return 0
        except Exception as exc:
            errors.append(
                {
                    "attempt": attempt,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    raise RuntimeError(json.dumps(errors, ensure_ascii=False, indent=2))


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: answer_card_cv_helper.py <orient|markers> ...")

    mode = sys.argv[1].strip().lower()
    if mode == "orient":
        if len(sys.argv) != 5:
            raise SystemExit("usage: answer_card_cv_helper.py orient <input> <output_image> <output_meta>")
        return orient_image(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))

    if mode == "markers":
        if len(sys.argv) != 4:
            raise SystemExit("usage: answer_card_cv_helper.py markers <input> <output_meta>")
        return detect_markers(Path(sys.argv[2]), Path(sys.argv[3]))

    raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    raise SystemExit(main())
