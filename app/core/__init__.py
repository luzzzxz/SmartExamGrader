from __future__ import annotations

try:
    from core.db_session import DatabaseSessionMixin
    from core.web_sync import ManualWebSyncMixin
    try:
        from core.pdf_overlay import PdfOverlayExportMixin
    except Exception:
        PdfOverlayExportMixin = object
    from core.lecture_presentation import (
        build_lecture_presentation_data,
        export_lecture_html,
        export_lecture_excel,
        find_exam_word_paper,
        parse_word_exam_paper,
    )
except ImportError:
    from .db_session import DatabaseSessionMixin
    from .web_sync import ManualWebSyncMixin
    try:
        from .pdf_overlay import PdfOverlayExportMixin
    except Exception:
        PdfOverlayExportMixin = object
    from .lecture_presentation import (
        build_lecture_presentation_data,
        export_lecture_html,
        export_lecture_excel,
        find_exam_word_paper,
        parse_word_exam_paper,
    )

__all__ = [
    "DatabaseSessionMixin",
    "ManualWebSyncMixin",
    "PdfOverlayExportMixin",
    "build_lecture_presentation_data",
    "export_lecture_html",
    "export_lecture_excel",
    "find_exam_word_paper",
    "parse_word_exam_paper",
]


