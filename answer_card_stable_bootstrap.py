from __future__ import annotations

import ctypes
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path



BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR / "app"
APP_SCRIPT = PROJECT_DIR / "enhanced_answer_card_gui_stats.py"
HELPER_SCRIPT = BASE_DIR / "answer_card_cv_helper.py"

# Candidate dependency locations (portable package vs development environment)
for candidate in [BASE_DIR / "runtime" / "pydeps314", BASE_DIR / "pydeps314"]:
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
        break


from PIL import Image


_INSTANCE_MUTEX_HANDLE = None


def acquire_single_instance() -> bool:
    """Keep stale windows from overwriting the active grading session."""
    global _INSTANCE_MUTEX_HANDLE
    if sys.platform != "win32":
        return True

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool

    handle = kernel32.CreateMutexW(
        None,
        False,
        r"Local\AnswerCardAutograderStableBootstrap",
    )
    if not handle:
        return True
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        ctypes.windll.user32.MessageBoxW(
            None,
            "答题卡批改程序已经打开。请使用现有窗口，避免两个窗口互相覆盖批改进度。",
            "程序已经运行",
            0x40,
        )
        return False
    _INSTANCE_MUTEX_HANDLE = handle
    return True


def import_module_from_path(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_helper(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [sys.executable, str(HELPER_SCRIPT), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        # Feed the existing fallback path instead of aborting the whole page.
        return subprocess.CompletedProcess([str(HELPER_SCRIPT), *args], 1, '', str(error))


def patch_processing(app_module) -> None:
    """Run heavy CV tasks in separate helper subprocess to avoid crashing main UI."""
    original_auto_orient_image = app_module.EnhancedAnswerCardStatsGUI.auto_orient_image
    original_detect_scan_homography = app_module.EnhancedAnswerCardStatsGUI.detect_scan_homography

    def is_direct_choice_context(gui_self, side=None):
        answer_config = getattr(gui_self, "answer_config", {}) or {}
        return (
            side in ("front", "back")
            and str(answer_config.get("mode") or "").startswith("direct_paper_choice")
        )

    def build_page_scale_fallback(self, image_path, side=None):
        with Image.open(image_path) as image:
            img_width, img_height = image.size

        side_payload = ((getattr(self, "answer_config", {}) or {}).get("sides") or {}).get(side or "") or {}
        template_image = side_payload.get("template_image") or self.answer_config.get("template_image", {})
        tpl_width = float(template_image.get("width") or img_width)
        tpl_height = float(template_image.get("height") or img_height)
        sx = img_width / max(tpl_width, 1.0)
        sy = img_height / max(tpl_height, 1.0)

        matrix = [
            [sx, 0.0, 0.0],
            [0.0, sy, 0.0],
            [0.0, 0.0, 1.0],
        ]

        names = ["top_left", "top_right", "bottom_right", "bottom_left"]
        markers = {}
        template_points = (
            self.get_template_marker_points(side)
            if side and hasattr(self, "get_template_marker_points")
            else self.template_marker_points
        )
        for name, (px, py) in zip(names, template_points):
            px2 = round(px * sx, 2)
            py2 = round(py * sy, 2)
            markers[name] = {
                "center": {"x": px2, "y": py2},
                "bbox": {"left": px2, "top": py2, "right": px2, "bottom": py2},
                "area_scaled": 0,
                "fill_ratio": 0.0,
                "diagnostics": {
                    "detector": "page_scale_fallback",
                    "scale_x": round(sx, 6),
                    "scale_y": round(sy, 6),
                },
            }

        marker_payload = {
            "image_path": str(image_path),
            "image_size": {"width": img_width, "height": img_height},
            "scaled_detection_size": {"width": img_width, "height": img_height},
            "threshold": None,
            "scale": 1.0,
            "candidates_found": 0,
            "detector": "page_scale_fallback",
            "markers": markers,
        }
        return matrix, marker_payload

    def safe_auto_orient_image(self, image_path):
        if (getattr(self, "answer_config", {}) or {}).get("mode") == "direct_paper_choice":
            return original_auto_orient_image(self, image_path)

        with tempfile.TemporaryDirectory(prefix="answer_card_orient_") as temp_dir:
            temp_dir_path = Path(temp_dir)
            oriented_path = temp_dir_path / "oriented.png"
            meta_path = temp_dir_path / "meta.json"
            result = run_helper("orient", str(image_path), str(oriented_path), str(meta_path))
            if result.returncode == 0 and oriented_path.exists() and meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                image = Image.open(oriented_path).convert("RGB")
                return image, meta.get("rotation", "rot0")

            if hasattr(self, "status_var"):
                self.status_var.set(f"自动旋正失败，改用原图继续：{Path(image_path).name}")
            image = Image.open(image_path).convert("RGB")
            return image, "rot0-fallback"

    def safe_detect_scan_homography(self, image_path, side=None):
        if is_direct_choice_context(self, side=side):
            return original_detect_scan_homography(self, image_path, side=side)

        with tempfile.TemporaryDirectory(prefix="answer_card_markers_") as temp_dir:
            meta_path = Path(temp_dir) / "markers.json"
            result = run_helper("markers", str(image_path), str(meta_path))
            if result.returncode != 0 or not meta_path.exists():
                if hasattr(self, "status_var"):
                    self.status_var.set(f"定位点识别失败，改用模板比例继续：{Path(image_path).name}")
                return build_page_scale_fallback(self, image_path, side=side)

            marker_payload = json.loads(meta_path.read_text(encoding="utf-8"))
            scan_markers = marker_payload["markers"]
            scan_points = [
                (scan_markers["top_left"]["center"]["x"], scan_markers["top_left"]["center"]["y"]),
                (scan_markers["top_right"]["center"]["x"], scan_markers["top_right"]["center"]["y"]),
                (scan_markers["bottom_right"]["center"]["x"], scan_markers["bottom_right"]["center"]["y"]),
                (scan_markers["bottom_left"]["center"]["x"], scan_markers["bottom_left"]["center"]["y"]),
            ]
            template_points = (
                self.get_template_marker_points(side)
                if side and hasattr(self, "get_template_marker_points")
                else self.template_marker_points
            )
            matrix = app_module.compute_homography(template_points, scan_points)
            return matrix, marker_payload

    app_module.EnhancedAnswerCardStatsGUI.auto_orient_image = safe_auto_orient_image
    app_module.EnhancedAnswerCardStatsGUI.detect_scan_homography = safe_detect_scan_homography


def main() -> None:
    if not acquire_single_instance():
        return
    sys.path.insert(0, str(PROJECT_DIR))
    app_module = import_module_from_path(APP_SCRIPT, "answer_card_gui_stable")
    patch_processing(app_module)
    app_module.main()


if __name__ == "__main__":
    main()

