<div align="center">

# 📝 SmartExamGrader

**High-Precision · Offline-First · Adaptive Multi-Template · Integrated Exam Analytics & Error-Notebook Generator**

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python Version](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](https://www.python.org/)
[![OpenCV](https://img.shields.io/badge/OpenCV-4.x-orange.svg)](https://opencv.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)]()

[简体中文文档](README.md) · [Changelog](CHANGELOG.md) · [Issues](../../issues)

</div>

---

## 🌟 Highlights

`SmartExamGrader` is an intelligent, high-precision Optical Mark Recognition (OMR) and exam grading application tailored for teachers and educational institutions. Powered by OpenCV and computer vision techniques, it frees educators from tedious grading, manual score calculation, and performance tracking.

- 🔒 **Offline-First & Data Privacy**: All image processing, grading algorithms, and score databases operate strictly on the local machine without mandatory cloud dependencies.
- 🎯 **Adaptive Alignment & Perspective Correction**: Supports both scanner feeds and smartphone photos, automatically correcting skew, rotation, and slight distortions via corner markers and alignment guides.
- ⚡ **Rapid Batch OMR**: Multi-threaded processing accurately detects bubble fills with adaptive thresholding, handling hundreds of test sheets in seconds.
- ✍️ **Subjective Question Review & Collaborative Grading**: Distinguishes red ink strokes from printed question content. Includes an optional lightweight LAN web server for multi-teacher grading on tablets or mobile browsers.
- 📊 **Academic Analytics & Personalized Error Notebooks**: Automatically links questions to knowledge concepts, producing grade distribution curves and generating personalized Word/PDF error review booklets for students.
- 📦 **Seamless Migration & Data Portability**: Built-in export/import tools for full business data bundles, ensuring safe data migration between software upgrades and different machines.

---

## 🚀 Quick Start

### Option 1: Standalone Portable Package (Recommended for Non-Developers)

1. Navigate to the **[Releases](../../releases)** tab and download `SmartExamGrader-vX.X.X-Windows-Portable.zip`.
2. Extract the archive into any local folder (e.g., `D:\SmartExamGrader`).
3. Double-click **`启动答题批改系统.bat`** to launch the application directly.

### Option 2: Run from Source

#### Prerequisites
- Python 3.10 or higher (recommended: 3.11 / 3.14).
- Git.

```bash
git clone https://github.com/your-username/SmartExamGrader.git
cd SmartExamGrader

# Install core dependencies
pip install -r app/requirements-main.txt

# (Optional) For deep OCR text recognition:
pip install -r app/requirements-paddleocr.txt

# Launch GUI
python answer_card_stable_bootstrap.py
```

---

## 📄 License

This project is licensed under the [Apache License 2.0](LICENSE).
