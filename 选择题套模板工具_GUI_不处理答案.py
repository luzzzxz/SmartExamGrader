# -*- coding: utf-8 -*-
"""
选择题 Word 文档套模板工具

功能：
- 选择任意 .docx 原文件
- 选择任意 .dotx 模板
- 复制“参考答案”之前的内容到模板中
- 给选项 A/B/C/D 前添加字号为 10 的小方框“☐”
- 可通过界面开关将小方框和填空横线设为红色
- 给填空题的下划线空格前后各加两个不带下划线的半角空格
- 将从“填空题”到“计算题”（若无计算题则到末尾）之间有横线的段落设为 1.5 倍行距
- 将所有图片设置为“黑白 50%”
- 将图片设置为紧密环绕，并移动到页面文字区域最右侧，避免超出右边界
- “参考答案”之后仅精简计算题答案，保留“计算题”、题号和小题号
- 其他答案内容沿用原有处理方式
- 另存为“原文件名套用模板.docx”，也可以在界面中手动指定输出文件

使用前请确保：
1. 已安装 Microsoft Word。
2. 已安装 pywin32：python -m pip install pywin32
"""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Iterable

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


DEFAULT_ANSWER_MARK = "参考答案"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE_PATH = SCRIPT_DIR / "直批模板.dotx"
if not DEFAULT_TEMPLATE_PATH.exists():
    fallback = Path("D:/Backup/Documents/新选择题模板A4.dotx")
    if fallback.exists():
        DEFAULT_TEMPLATE_PATH = fallback
CHECK_BOX = "☐ "
WORD_RED = 255
WORD_OBJECT_CHARS = "\x01\x07\x08"


def get_constant(constants, name: str, default: int) -> int:
    try:
        return getattr(constants, name)
    except Exception:
        return default


def extract_answers(text: str) -> str:
    """
    从“1．【答案】A”整理为“1.A”，多选如“A,B,C”会保留逗号。
    """
    cleaned = text.replace("\r", "\n").replace("\x07", "")
    pattern = re.compile(
        r"(\d+)\s*[.．、]?\s*【答案】\s*([A-D](?:\s*[,，、]\s*[A-D])*)",
        re.IGNORECASE,
    )

    items: list[str] = []
    for number, answer in pattern.findall(cleaned):
        normalized_answer = re.sub(r"\s+", "", answer.upper())
        normalized_answer = normalized_answer.replace("，", ",").replace("、", ",")
        items.append(f"{number}.{normalized_answer}")
    return "".join(items)


def option_matches(text: str) -> Iterable[re.Match[str]]:
    """
    匹配常见选项开头：A.、A．、A、A)、A）以及 (A)。
    """
    pattern = re.compile(r"(^|[\s　（(])([ABCD])(?=[.．、)）])")
    return pattern.finditer(text)


def word_text_offset(text: str, python_index: int) -> int:
    """把 Python 字符下标换算成 Word 使用的 UTF-16 字符位置。"""
    return len(text[:python_index].encode("utf-16-le")) // 2


def major_section_kind(text: str) -> str | None:
    """识别“单选题/填空题”等大题标题，避免跨题型添加方框。"""
    cleaned = text.replace("\r", "").replace("\x07", "").strip()
    if not cleaned:
        return None

    is_numbered_heading = bool(
        re.match(r"^[一二三四五六七八九十]+\s*[、.．)）]", cleaned)
    )
    is_named_choice_heading = len(cleaned) <= 40 and any(
        name in cleaned for name in ("选择题", "单选题", "多选题")
    )
    if is_named_choice_heading:
        return "choice"

    non_choice_names = (
        "填空题",
        "作图题",
        "实验题",
        "计算题",
        "解答题",
        "简答题",
        "综合题",
        "判断题",
    )
    if is_numbered_heading and any(name in cleaned for name in non_choice_names):
        return "other"
    return None


def add_check_boxes_to_options(doc, use_red: bool = False) -> int:
    inserted = 0
    paragraphs = list(doc.Paragraphs)
    has_choice_sections = any(
        major_section_kind(paragraph.Range.Text) == "choice"
        for paragraph in paragraphs
    )
    in_choice_section = not has_choice_sections

    for paragraph in paragraphs:
        text = paragraph.Range.Text
        section_kind = major_section_kind(text)
        if section_kind is not None:
            in_choice_section = section_kind == "choice"
            continue
        if not in_choice_section:
            continue

        matches = list(option_matches(text))
        if not matches:
            continue

        for match in reversed(matches):
            letter_start = match.start(2)
            insert_at = paragraph.Range.Start + word_text_offset(text, letter_start)

            before_range = doc.Range(max(paragraph.Range.Start, insert_at - len(CHECK_BOX)), insert_at)
            if before_range.Text == CHECK_BOX:
                continue

            insert_range = doc.Range(insert_at, insert_at)
            insert_range.InsertAfter(CHECK_BOX)
            box_range = doc.Range(insert_at, insert_at + len(CHECK_BOX))
            box_range.Font.Size = 10
            if use_red:
                doc.Range(insert_at, insert_at + 1).Font.Color = WORD_RED
            inserted += 1

    return inserted


def is_underlined_blank_char(character, constants) -> bool:
    underline_none = get_constant(constants, "wdUnderlineNone", 0)
    text = character.Text.replace("\r", "")
    if text not in {" ", "　", "\t", "\xa0"}:
        return False
    try:
        return int(character.Font.Underline) != underline_none
    except Exception:
        return False


def set_range_not_underlined(word_range, constants) -> None:
    underline_none = get_constant(constants, "wdUnderlineNone", 0)
    word_range.Font.Underline = underline_none


def set_underlined_blank_red(word_range) -> None:
    word_range.Font.Color = WORD_RED
    try:
        word_range.Font.UnderlineColor = WORD_RED
    except Exception:
        pass


def is_fill_blank_heading(text: str) -> bool:
    """识别“填空题”等大题标题。"""
    cleaned = text.replace("\r", "").replace("\x07", "").strip()
    if not cleaned or len(cleaned) > 50:
        return False
    is_numbered = bool(re.match(r"^[一二三四五六七八九十\d]+[、.．\s)）]", cleaned))
    is_short = len(cleaned) <= 20 and any(kw in cleaned for kw in ("填空题", "填空"))
    return ("填空" in cleaned) and (is_numbered or is_short)


def is_calculation_heading(text: str) -> bool:
    """识别“计算题/解答题”等大题标题，用于划定填空题行距调整的截止范围。"""
    cleaned = text.replace("\r", "").replace("\x07", "").strip()
    if not cleaned or len(cleaned) > 50:
        return False
    if not any(kw in cleaned for kw in ("计算题", "计算", "解答题")):
        return False
    is_numbered = bool(re.match(r"^[一二三四五六七八九十]+[、.．\s)）]", cleaned))
    is_arabic_numbered = bool(re.match(r"^\d+\s*[、.．\s]\s*(?:计算|解答)", cleaned))
    is_short = len(cleaned) <= 20 and any(
        cleaned.startswith(kw) or cleaned.endswith(kw) for kw in ("计算题", "计算", "解答题")
    )
    return is_numbered or is_arabic_numbered or is_short


def add_spaces_around_underlined_blanks(
    doc,
    constants,
    use_red: bool = False,
    target_range=None,
    adjust_line_spacing: bool = True,
) -> tuple[int, int]:
    """
    给由“带下划线的空格”组成的填空横线前后各加两个普通半角空格，
    并识别直接输入的连续下划线字符。
    同时将从“填空题”下一行到“计算题”上一行（若无计算题则到末尾）之间包含横线的段落设为 1.5 倍行距。
    """
    sequences: list[tuple[int, int]] = []
    literal_underscore_sequences: list[tuple[int, int]] = []
    paragraphs = list(target_range.Paragraphs) if target_range is not None else list(doc.Paragraphs)

    has_fill_heading = any(is_fill_blank_heading(p.Range.Text) for p in paragraphs)
    in_fill_blank_scope = not has_fill_heading
    line_space_1pt5 = get_constant(constants, "wdLineSpace1pt5", 1)
    underline_none = get_constant(constants, "wdUnderlineNone", 0)
    line_spaced_paragraphs = 0

    for paragraph in paragraphs:
        paragraph_text = paragraph.Range.Text

        # 范围控制：遇到“填空题”大题标题，从其下一行（下一个段落）开始生效
        if is_fill_blank_heading(paragraph_text):
            in_fill_blank_scope = True
            continue

        # 范围控制：遇到“计算题/解答题”等大题标题，上一段已结束，退出行距调整范围
        if in_fill_blank_scope and is_calculation_heading(paragraph_text):
            in_fill_blank_scope = False

        para_has_lines = False

        for match in re.finditer(r"[_＿]{2,}", paragraph_text):
            start = paragraph.Range.Start + word_text_offset(paragraph_text, match.start())
            end = paragraph.Range.Start + word_text_offset(paragraph_text, match.end())
            literal_underscore_sequences.append((start, end))
            para_has_lines = True

        current_start = None
        current_end = None

        for index in range(1, paragraph.Range.Characters.Count + 1):
            character = paragraph.Range.Characters(index)
            if is_underlined_blank_char(character, constants):
                if current_start is None:
                    current_start = character.Start
                current_end = character.End
                continue

            if current_start is not None and current_end is not None:
                sequences.append((current_start, current_end))
                para_has_lines = True
                current_start = None
                current_end = None

        if current_start is not None and current_end is not None:
            sequences.append((current_start, current_end))
            para_has_lines = True

        # 如果段落内包含下划线格式（即使不是空格，如划线的变量或公式），也视为包含横线
        if not para_has_lines and in_fill_blank_scope:
            try:
                if int(paragraph.Range.Font.Underline) != underline_none:
                    para_has_lines = True
            except Exception:
                pass

        # 调整处于指定范围内的横线段落行距为 1.5 倍
        if in_fill_blank_scope and para_has_lines and adjust_line_spacing:
            try:
                paragraph.Format.LineSpacingRule = line_space_1pt5
                line_spaced_paragraphs += 1
            except Exception:
                pass

    operations = [
        (start, end, False) for start, end in sequences
    ] + [
        (start, end, True) for start, end in literal_underscore_sequences
    ]

    # 所有横线必须共用一个倒序队列，避免插入空格后其他横线的位置失效。
    for start, end, is_literal in sorted(operations, key=lambda item: item[0], reverse=True):
        if use_red:
            if is_literal:
                doc.Range(start, end).Font.Color = WORD_RED
            else:
                set_underlined_blank_red(doc.Range(start, end))

        after_range = doc.Range(end, end)
        after_range.InsertAfter("  ")
        set_range_not_underlined(doc.Range(end, end + 2), constants)

        before_range = doc.Range(start, start)
        before_range.InsertBefore("  ")
        set_range_not_underlined(doc.Range(start, start + 2), constants)

    return len(sequences) + len(literal_underscore_sequences), line_spaced_paragraphs


def remove_answer_markers(doc) -> int:
    marker = "\u3010\u7b54\u6848\u3011"
    removed = 0

    for paragraph in list(doc.Paragraphs):
        while True:
            text = paragraph.Range.Text
            offset = text.rfind(marker)
            if offset < 0:
                break
            try:
                delete_word_range(doc.Range(paragraph.Range.Start + offset, paragraph.Range.Start + offset + len(marker)))
                removed += 1
            except Exception:
                break

    return removed


def remove_legacy_answer_headings_from_questions(doc) -> int:
    """删除题目区中单独残留的旧标题“答案解析部分”。"""
    removed = 0
    for paragraph in reversed(list(doc.Paragraphs)):
        text = paragraph.Range.Text.replace("\r", "").replace("\x07", "").strip()
        if text != "答案解析部分":
            continue
        try:
            delete_word_range(paragraph.Range)
            removed += 1
        except Exception:
            continue
    return removed


def is_picture_shape(shape, constants) -> bool:
    mso_picture = get_constant(constants, "msoPicture", 13)
    mso_linked_picture = get_constant(constants, "msoLinkedPicture", 11)
    return int(shape.Type) in {mso_picture, mso_linked_picture}


def set_picture_black_white_50(picture, constants) -> bool:
    black_white = get_constant(constants, "msoPictureBlackAndWhite", 3)
    try:
        picture.PictureFormat.ColorType = black_white
        return True
    except Exception:
        return False


def set_all_pictures_black_white_50(doc, constants) -> int:
    changed = 0
    picture_inline_types = {
        get_constant(constants, "wdInlineShapePicture", 3),
        get_constant(constants, "wdInlineShapeLinkedPicture", 4),
    }

    for index in range(1, doc.InlineShapes.Count + 1):
        inline_shape = doc.InlineShapes(index)
        if int(inline_shape.Type) not in picture_inline_types:
            continue
        if set_picture_black_white_50(inline_shape, constants):
            changed += 1

    for index in range(1, doc.Shapes.Count + 1):
        shape = doc.Shapes(index)
        if not is_picture_shape(shape, constants):
            continue
        if set_picture_black_white_50(shape, constants):
            changed += 1

    return changed


def is_option_paragraph_text(text: str) -> bool:
    cleaned = text.replace("\r", "").replace("\x07", "").lstrip(" \t　")
    if cleaned.startswith(CHECK_BOX):
        cleaned = cleaned[len(CHECK_BOX):].lstrip(" \t　")
    return bool(re.match(r"^[ABCD][.．、)）]", cleaned))


def previous_nonempty_paragraph(doc, paragraph):
    for index in range(1, doc.Paragraphs.Count + 1):
        current = doc.Paragraphs(index)
        if current.Range.Start != paragraph.Range.Start:
            continue

        for previous_index in range(index - 1, 0, -1):
            previous = doc.Paragraphs(previous_index)
            if paragraph_text_without_mark(previous).strip():
                return previous
        return None

    return None


def is_option_related_anchor(doc, anchor_range) -> bool:
    try:
        paragraph = anchor_range.Paragraphs(1)
    except Exception:
        return False

    if is_option_paragraph_text(paragraph.Range.Text):
        return True

    previous = previous_nonempty_paragraph(doc, paragraph)
    return previous is not None and is_option_paragraph_text(previous.Range.Text)


def is_inline_picture_after_option(doc, inline_shape) -> bool:
    return is_option_related_anchor(doc, inline_shape.Range)


def is_floating_picture_after_option(doc, shape) -> bool:
    try:
        return is_option_related_anchor(doc, shape.Anchor)
    except Exception:
        return False


def move_shape_to_right_inside_page(shape, page_setup, constants, forced_top=None, relative_to_anchor_paragraph: bool = False) -> None:
    wrap_tight = get_constant(constants, "wdWrapTight", 1)
    relative_to_margin = get_constant(constants, "wdRelativeHorizontalPositionMargin", 0)
    relative_to_page = get_constant(constants, "wdRelativeVerticalPositionPage", 1)
    relative_to_paragraph = get_constant(constants, "wdRelativeVerticalPositionParagraph", 2)
    vertical_position_to_page = get_constant(constants, "wdVerticalPositionRelativeToPage", 6)
    mso_true = get_constant(constants, "msoTrue", -1)

    if forced_top is None:
        try:
            original_top = shape.Information(vertical_position_to_page)
        except Exception:
            original_top = shape.Top
    else:
        original_top = forced_top

    shape.WrapFormat.Type = wrap_tight
    shape.RelativeHorizontalPosition = relative_to_margin

    usable_width = float(page_setup.PageWidth) - float(page_setup.LeftMargin) - float(page_setup.RightMargin)
    if usable_width <= 0:
        return

    try:
        shape.LockAspectRatio = mso_true
    except Exception:
        pass

    if float(shape.Width) > usable_width:
        shape.Width = usable_width

    # 只调整横向位置到右侧，保留图片原来的纵向位置。
    shape.Left = max(0, usable_width - float(shape.Width))
    if relative_to_anchor_paragraph:
        shape.RelativeVerticalPosition = relative_to_paragraph
        shape.Top = 0
    else:
        shape.RelativeVerticalPosition = relative_to_page
        shape.Top = original_top


def format_pictures(doc, constants) -> int:
    processed = 0
    converted_shape_ids: set[int] = set()
    vertical_position_to_page = get_constant(constants, "wdVerticalPositionRelativeToPage", 6)
    picture_inline_types = {
        get_constant(constants, "wdInlineShapePicture", 3),
        get_constant(constants, "wdInlineShapeLinkedPicture", 4),
    }

    for index in range(doc.InlineShapes.Count, 0, -1):
        inline_shape = doc.InlineShapes(index)
        if int(inline_shape.Type) in picture_inline_types:
            if is_inline_picture_after_option(doc, inline_shape):
                continue
            try:
                original_top = inline_shape.Range.Information(vertical_position_to_page)
            except Exception:
                original_top = None
            shape = inline_shape.ConvertToShape()
            move_shape_to_right_inside_page(
                shape,
                doc.PageSetup,
                constants,
                forced_top=original_top,
                relative_to_anchor_paragraph=True,
            )
            try:
                converted_shape_ids.add(int(shape.ID))
            except Exception:
                pass
            processed += 1

    for index in range(1, doc.Shapes.Count + 1):
        shape = doc.Shapes(index)
        try:
            if int(shape.ID) in converted_shape_ids:
                continue
        except Exception:
            pass
        if not is_picture_shape(shape, constants):
            continue
        if is_floating_picture_after_option(doc, shape):
            continue
        move_shape_to_right_inside_page(shape, doc.PageSetup, constants)
        processed += 1

    compact_empty_anchor_paragraphs_before_a_options(doc, constants)
    return processed


def paragraph_text_without_mark(paragraph) -> str:
    text = paragraph.Range.Text.replace("\r", "")
    for char in WORD_OBJECT_CHARS:
        text = text.replace(char, "")
    return text


def is_empty_paragraph(paragraph) -> bool:
    return not paragraph_text_without_mark(paragraph).strip()


def compact_paragraph(paragraph, constants) -> None:
    line_space_exactly = get_constant(constants, "wdLineSpaceExactly", 4)
    paragraph.Range.Font.Size = 1
    paragraph.Format.SpaceBefore = 0
    paragraph.Format.SpaceAfter = 0
    paragraph.Format.LineSpacingRule = line_space_exactly
    paragraph.Format.LineSpacing = 1


def paragraph_starts_with_a_option(paragraph) -> bool:
    text = paragraph.Range.Text.lstrip(" \t　")
    return any(text.startswith(f"{CHECK_BOX}A{punctuation}") for punctuation in (".", "．", "、", ")", "）"))


def next_paragraph(doc, paragraph):
    for index in range(1, doc.Paragraphs.Count + 1):
        current = doc.Paragraphs(index)
        if current.Range.Start == paragraph.Range.Start:
            if index < doc.Paragraphs.Count:
                return doc.Paragraphs(index + 1)
            return None
    return None


def compact_empty_anchor_paragraphs_before_a_options(doc, constants) -> int:
    compacted = 0
    seen_starts: set[int] = set()

    for index in range(1, doc.Shapes.Count + 1):
        shape = doc.Shapes(index)
        try:
            paragraph = shape.Anchor.Paragraphs(1)
        except Exception:
            continue

        if paragraph.Range.Start in seen_starts:
            continue
        seen_starts.add(paragraph.Range.Start)

        if not is_empty_paragraph(paragraph):
            continue

        following = next_paragraph(doc, paragraph)
        if following is None or not paragraph_starts_with_a_option(following):
            continue

        try:
            compact_paragraph(paragraph, constants)
            compacted += 1
        except Exception:
            continue

    return compacted


def is_answer_blank_text(text: str) -> bool:
    cleaned = text.replace("\r", "").strip()
    for char in WORD_OBJECT_CHARS:
        cleaned = cleaned.replace(char, "")
    cleaned = re.sub(r"[\s　]+", "", cleaned)
    return cleaned in {"（）", "()", "（)", "(）"}


def delete_word_range(word_range) -> None:
    last_error = None
    for _ in range(8):
        try:
            word_range.Delete()
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.25)
    raise last_error


def paragraph_has_anchored_picture(doc, paragraph) -> bool:
    paragraph_start = paragraph.Range.Start
    paragraph_end = paragraph.Range.End

    for index in range(1, doc.Shapes.Count + 1):
        shape = doc.Shapes(index)
        try:
            anchor_start = shape.Anchor.Start
        except Exception:
            continue
        if paragraph_start <= anchor_start < paragraph_end:
            return True

    return False


def delete_answer_blank_chars_in_range(doc, word_range) -> bool:
    """
    只删除“（　　）”本身和其中空白，不删除段落标记或图片对象字符。
    """
    text = word_range.Text
    match = re.search(r"[（(][\s　\x01\x07\x08]*[）)]", text)
    if not match:
        return False

    for offset in range(match.end() - 1, match.start() - 1, -1):
        char = text[offset]
        if char in WORD_OBJECT_CHARS or char == "\r":
            continue
        delete_word_range(doc.Range(word_range.Start + offset, word_range.Start + offset + 1))

    return True


def word_replace_all(doc, find_text: str, replace_text: str = "", wildcards: bool = False) -> bool:
    find = doc.Content.Find
    find.ClearFormatting()
    find.Replacement.ClearFormatting()
    find.Text = find_text
    find.Replacement.Text = replace_text
    find.Forward = True
    find.Wrap = 1
    find.Format = False
    find.MatchCase = False
    find.MatchWholeWord = False
    find.MatchWildcards = wildcards
    find.MatchSoundsLike = False
    find.MatchAllWordForms = False
    return bool(find.Execute(Replace=2))


def remove_answer_blank_placeholders(doc) -> int:
    """
    删除题干里的“（　　）/（ ）/( )”占位符，只删字符，不删段落标记或图片锚点。
    """
    removed = 0

    wildcard_patterns = [
        "（[ 　]@）",
        "\\([ 　]@\\)",
    ]
    for pattern_text in wildcard_patterns:
        if word_replace_all(doc, pattern_text, "", wildcards=True):
            removed += 1

    pattern = re.compile(r"[（(][\s　\x01\x07\x08]*[）)]")

    for paragraph in list(doc.Paragraphs):
        text = paragraph.Range.Text
        if any(ord(char) > 0xFFFF for char in text):
            continue
        matches = list(pattern.finditer(text))
        for match in reversed(matches):
            if any(char in text[match.start():match.end()] for char in WORD_OBJECT_CHARS):
                continue
            try:
                delete_word_range(doc.Range(paragraph.Range.Start + match.start(), paragraph.Range.Start + match.end()))
                removed += 1
            except Exception:
                continue

    return removed


def remove_orphan_answer_blank_marks(doc) -> int:
    """
    清掉被 Word 拆散后残留在题干末尾的单个“（/）/(/)”。
    """
    removed = 0
    pattern = re.compile(r"[\s　]*[（(）)][\s　]*(?=\r?$)")

    for paragraph in list(doc.Paragraphs):
        text = paragraph.Range.Text
        if any(ord(char) > 0xFFFF for char in text):
            continue
        match = pattern.search(text)
        if not match:
            continue
        end = len(text.rstrip("\r"))
        if match.start() >= end:
            continue
        try:
            delete_word_range(doc.Range(paragraph.Range.Start + match.start(), paragraph.Range.Start + end))
            removed += 1
        except Exception:
            continue

    return removed


def find_option_checkbox_positions(doc) -> list[int]:
    positions: list[int] = []
    option_prefixes = [
        f"{CHECK_BOX}A{punctuation}"
        for punctuation in (".", "．", "、", ")", "）")
    ]

    for paragraph in list(doc.Paragraphs):
        text = paragraph.Range.Text
        for prefix in option_prefixes:
            offset = text.find(prefix)
            if offset >= 0:
                positions.append(paragraph.Range.Start + offset)
                break

    return positions


def remove_blank_lines_before_options(doc) -> int:
    """
    模拟在“☐A．”这类选项前按一次 Backspace。
    只处理 A 选项前面的空段或单独的“（　　）”。
    """
    removed = 0

    for position in sorted(find_option_checkbox_positions(doc), reverse=True):
        if position <= doc.Content.Start:
            continue

        current_paragraph = doc.Range(position, position).Paragraphs(1)
        if position != current_paragraph.Range.Start:
            before_option_range = doc.Range(current_paragraph.Range.Start, position)
            before_option = before_option_range.Text
            if is_answer_blank_text(before_option):
                if delete_answer_blank_chars_in_range(doc, before_option_range):
                    removed += 1
                continue
            if before_option.strip():
                continue

        try:
            previous_paragraph = doc.Range(current_paragraph.Range.Start - 1, current_paragraph.Range.Start - 1).Paragraphs(1)
        except Exception:
            continue

        if is_answer_blank_text(previous_paragraph.Range.Text):
            if delete_answer_blank_chars_in_range(doc, previous_paragraph.Range):
                removed += 1
            if paragraph_has_anchored_picture(doc, previous_paragraph):
                continue
            try:
                delete_word_range(doc.Range(previous_paragraph.Range.Start, previous_paragraph.Range.Start + 1))
            except Exception:
                pass
            continue

        if not is_empty_paragraph(previous_paragraph):
            continue

        if paragraph_has_anchored_picture(doc, previous_paragraph):
            continue

        delete_word_range(doc.Range(current_paragraph.Range.Start - 1, current_paragraph.Range.Start))
        removed += 1

    return removed


def open_word_document(word, path: Path, read_only: bool):
    before_count = word.Documents.Count
    doc = word.Documents.Open(
        FileName=str(path),
        ReadOnly=read_only,
        AddToRecentFiles=False,
    )
    if doc is not None:
        return doc
    if word.Documents.Count > before_count:
        return word.Documents(word.Documents.Count)
    raise RuntimeError(f"Word 已尝试打开文件，但没有返回文档对象：{path}")


def add_document_from_template(word, template_path: Path):
    before_count = word.Documents.Count
    doc = word.Documents.Add(Template=str(template_path), NewTemplate=False)
    if doc is not None:
        return doc
    if word.Documents.Count > before_count:
        return word.Documents(word.Documents.Count)
    raise RuntimeError(f"Word 已尝试按模板创建文档，但没有返回文档对象：{template_path}")


def is_answer_section_heading(text: str) -> bool:
    cleaned = text.replace("\r", "").replace("\x07", "").strip()
    if not cleaned:
        return False
    return bool(re.match(r"^[一二三四五六七八九十]+[、.．]\s*\S*", cleaned))


def set_range_single_spacing(word_range, constants) -> int:
    line_space_single = get_constant(constants, "wdLineSpaceSingle", 0)
    changed = 0

    for paragraph in list(word_range.Paragraphs):
        try:
            paragraph.Format.LineSpacingRule = line_space_single
            paragraph.Format.SpaceBefore = 0
            paragraph.Format.SpaceAfter = 0
            try:
                paragraph.Format.LineUnitBefore = 0
                paragraph.Format.LineUnitAfter = 0
            except Exception:
                pass
            changed += 1
        except Exception:
            continue

    return changed


def remove_answer_section_headings(answer_output_range) -> int:
    removed_headings = 0

    for paragraph in reversed(list(answer_output_range.Paragraphs)):
        if not is_answer_section_heading(paragraph.Range.Text):
            continue
        try:
            delete_word_range(paragraph.Range)
            removed_headings += 1
        except Exception:
            continue

    return removed_headings


def replace_paragraph_content(paragraph, text: str) -> None:
    content_range = paragraph.Range.Duplicate
    raw_text = content_range.Text
    if raw_text.endswith("\r\x07"):
        content_range.End -= 2
    elif raw_text.endswith("\r"):
        content_range.End -= 1
    content_range.Text = text


def is_calculation_answer_heading(text: str) -> bool:
    cleaned = text.replace("\r", "").replace("\x07", "").strip()
    if "计算题" not in cleaned or len(cleaned) > 40:
        return False
    return cleaned == "计算题" or is_answer_section_heading(cleaned)


def calculation_answer_summary(texts: list[str]) -> tuple[str, list[str]] | None:
    if not texts:
        return None

    first_text = texts[0].replace("\r", "").replace("\x07", "").strip()
    number_match = re.match(
        r"^(\d+)(?:\s*[.．、]|\s*(?=[（(])|\s*(?=[解答])|\s*$)",
        first_text,
    )
    if not number_match:
        return None

    markers: list[str] = []
    for text in texts:
        for marker in re.findall(r"[（(]\s*(\d+)\s*[）)]", text):
            if marker not in markers:
                markers.append(marker)

    return number_match.group(1), markers


def compact_calculation_answer_sections(answer_output_range) -> tuple[int, int]:
    """计算题答案仅保留栏目名、题号和去重后的小题号。"""
    paragraphs = list(answer_output_range.Paragraphs)
    section_indexes = [
        index
        for index, paragraph in enumerate(paragraphs)
        if is_calculation_answer_heading(paragraph.Range.Text)
    ]
    compacted_sections = 0
    compacted_questions = 0

    for section_index in reversed(section_indexes):
        section_end = len(paragraphs)
        for index in range(section_index + 1, len(paragraphs)):
            if is_answer_section_heading(paragraphs[index].Range.Text):
                section_end = index
                break

        question_starts: list[int] = []
        for index in range(section_index + 1, section_end):
            text = paragraphs[index].Range.Text.replace("\r", "").replace("\x07", "").strip()
            if re.match(r"^\d+(?:\s*[.．、]|\s*(?=[（(])|\s*(?=[解答])|\s*$)", text):
                question_starts.append(index)

        summaries: dict[int, str] = {}
        for position, start_index in enumerate(question_starts):
            end_index = question_starts[position + 1] if position + 1 < len(question_starts) else section_end
            texts = [paragraphs[index].Range.Text for index in range(start_index, end_index)]
            summary = calculation_answer_summary(texts)
            if summary is None:
                continue
            number, markers = summary
            summaries[start_index] = number + "".join(f"（{marker}）" for marker in markers)

        # 未识别出题号时只规范栏目名，不删除原答案，避免意外丢失内容。
        if summaries:
            for index in range(section_end - 1, section_index, -1):
                if index in summaries:
                    replace_paragraph_content(paragraphs[index], summaries[index])
                else:
                    delete_word_range(paragraphs[index].Range)
            compacted_questions += len(summaries)

        replace_paragraph_content(paragraphs[section_index], "计算题")
        compacted_sections += 1

    return compacted_sections, compacted_questions


def find_content_marker_range(doc, marker_text: str, wd_find_stop: int):
    candidates = [marker_text.strip() or DEFAULT_ANSWER_MARK]

    for candidate in candidates:
        marker_range = doc.Content.Duplicate
        marker_range.Find.ClearFormatting()
        marker_range.Find.Text = candidate
        marker_range.Find.Wrap = wd_find_stop
        if marker_range.Find.Execute():
            return marker_range, candidate

    raise RuntimeError(f"在原文件中没有找到题目结束标记：{'、'.join(candidates)}。")


def copy_questions_to_template(
    source_path: Path,
    template_path: Path,
    output_path: Path,
    answer_mark: str,
    log: Callable[[str], None] | None = None,
    use_red_marks: bool = False,
) -> tuple[int, int, int]:
    try:
        import pythoncom
        import win32com.client
        from win32com.client import constants
    except ImportError as exc:
        raise RuntimeError("缺少 pywin32，请先运行：python -m pip install pywin32") from exc

    def write_log(message: str) -> None:
        if log:
            log(message)

    pythoncom.CoInitialize()
    word = None
    source_doc = None
    target_doc = None
    try:
        wd_find_stop = get_constant(constants, "wdFindStop", 0)
        wd_format_original = get_constant(constants, "wdFormatOriginalFormatting", 16)
        wd_format_document_default = get_constant(constants, "wdFormatDocumentDefault", 16)

        write_log("正在启动 Word...")
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0

        write_log("正在打开原文件...")
        source_doc = open_word_document(word, source_path, read_only=True)

        write_log("正在按模板创建新文档...")
        target_doc = add_document_from_template(word, template_path)

        marker_range, used_marker = find_content_marker_range(source_doc, answer_mark, wd_find_stop)
        write_log(f"已找到题目结束标记：{used_marker}")

        question_range = source_doc.Range(source_doc.Content.Start, marker_range.Start)
        answer_range = source_doc.Range(marker_range.Start, source_doc.Content.End)

        write_log("正在复制题目内容到模板...")
        paste_range = target_doc.Range(target_doc.Content.End - 1, target_doc.Content.End - 1)
        if target_doc.Content.End - target_doc.Content.Start > 1:
            paste_range.InsertParagraphAfter()
            paste_range = target_doc.Range(target_doc.Content.End - 1, target_doc.Content.End - 1)
        question_range.Copy()
        try:
            paste_range.PasteAndFormat(wd_format_original)
        except Exception:
            paste_range.PasteAndFormat(wd_format_document_default)
        question_output_range = target_doc.Range(paste_range.Start, target_doc.Content.End - 1)
        changed_line_count = set_range_single_spacing(question_output_range, constants)
        if changed_line_count:
            write_log(f"已设置题目区单倍行距：{changed_line_count} 段")

        legacy_heading_count = remove_legacy_answer_headings_from_questions(target_doc)
        if legacy_heading_count:
            write_log(f"已删除题目区旧标题：{legacy_heading_count} 处")

        write_log("正在给选项添加方框...")
        box_count = add_check_boxes_to_options(target_doc, use_red=use_red_marks)

        write_log("正在处理填空题横线前后的空格并调整行距...")
        fill_blank_count, line_spaced_count = add_spaces_around_underlined_blanks(
            target_doc,
            constants,
            use_red=use_red_marks,
            target_range=question_output_range,
            adjust_line_spacing=True,
        )
        if fill_blank_count:
            write_log(f"已处理填空横线：{fill_blank_count} 处")
        if line_spaced_count:
            write_log(f"已设置填空横线段落 1.5 倍行距：{line_spaced_count} 段")

        write_log("正在删除题干中的括号占位符...")
        placeholder_count = remove_answer_blank_placeholders(target_doc)
        if placeholder_count:
            write_log(f"已删除括号占位符：{placeholder_count} 个")

        write_log("正在处理图片位置和环绕方式...")
        format_pictures(target_doc, constants)

        write_log("正在删除图片移动后留下的空回车行...")
        blank_line_count = remove_blank_lines_before_options(target_doc)
        if blank_line_count:
            write_log(f"已删除空回车行：{blank_line_count} 行")

        if answer_range.Text.strip():
            write_log("正在原样保留答案部分...")
            end_range = target_doc.Range(target_doc.Content.End - 1, target_doc.Content.End - 1)
            end_range.InsertParagraphAfter()
            end_range = target_doc.Range(target_doc.Content.End - 1, target_doc.Content.End - 1)
            answer_output_start = end_range.Start
            answer_range.Copy()
            try:
                end_range.PasteAndFormat(wd_format_original)
            except Exception:
                end_range.PasteAndFormat(wd_format_document_default)
            answer_output_range = target_doc.Range(answer_output_start, target_doc.Content.End - 1)
            calculation_sections, calculation_questions = compact_calculation_answer_sections(
                answer_output_range
            )
            if calculation_sections:
                write_log(
                    f"已精简计算题答案：{calculation_sections} 个栏目，"
                    f"保留 {calculation_questions} 个题号"
                )
            removed_answer_headings = remove_answer_section_headings(answer_output_range)
            if removed_answer_headings:
                write_log(f"已删除答案部分大题号：{removed_answer_headings} 处")

        write_log("正在将所有图片设置为黑白 50%...")
        picture_count = set_all_pictures_black_white_50(target_doc, constants)
        if picture_count:
            write_log(f"已设置黑白 50% 图片：{picture_count} 张")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_log("正在保存新文件...")
        target_doc.SaveAs2(str(output_path), FileFormat=16)
        return box_count, picture_count, fill_blank_count
    finally:
        if source_doc is not None:
            source_doc.Close(SaveChanges=False)
        if target_doc is not None:
            target_doc.Close(SaveChanges=False)
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("选择题套模板工具（不处理答案版）")
        self.geometry("760x460")
        self.minsize(680, 420)

        self.source_var = tk.StringVar()
        self.template_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.answer_mark_var = tk.StringVar(value=DEFAULT_ANSWER_MARK)
        self.red_marks_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="请选择原 Word 文档和模板文件。")
        self.output_touched = False

        self._build_ui()
        self.load_default_template()

        import sys
        target_path = None
        if len(sys.argv) > 1 and sys.argv[1]:
            p = Path(sys.argv[1])
            if p.exists():
                if p.is_file() and p.suffix.lower() == '.docx':
                    target_path = p
                elif p.is_dir():
                    target_path = self.find_original_paper_in_folder(p)

        if not target_path:
            target_path = self.find_paper_from_app_settings()

        if target_path and target_path.exists():
            self.source_var.set(str(target_path.resolve()))
            self.update_default_output()

        if len(sys.argv) > 2 and sys.argv[2] and Path(sys.argv[2]).exists():
            self.template_var.set(str(Path(sys.argv[2]).resolve()))

    @staticmethod
    def find_original_paper_in_folder(folder_path: Path) -> Path | None:
        """智能在测试文件夹中查找原卷 Word 文档（排除模板、打印等辅助文档）"""
        try:
            p = Path(folder_path)
            if not p.exists() or not p.is_dir():
                return None
            docx_files = [f for f in p.glob("*.docx") if not f.name.startswith("~$")]
            if not docx_files:
                return None
            excluded = ['模板', 'compact_print', '透打', '打印辅助', '讲评', '删除']
            non_template = [f for f in docx_files if not any(kw in f.name for kw in excluded)]
            if not non_template:
                filtered = [f for f in docx_files if '删除' not in f.name]
                return filtered[0] if filtered else docx_files[0]
            # 优先级1：匹配文件夹名
            folder_clean = re.sub(r'[\s_\-]+', '', p.name).lower()
            for f in non_template:
                if re.sub(r'[\s_\-]+', '', f.stem).lower() == folder_clean:
                    return f
            # 优先级2：以 paper_ 开头
            papers = [f for f in non_template if f.name.lower().startswith('paper_')]
            if papers:
                papers.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                return papers[0]
            non_template.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            return non_template[0]
        except Exception:
            return None

    @staticmethod
    def find_paper_from_app_settings() -> Path | None:
        """从主程序配置文件中回退查找最近使用的测试目录中的原卷"""
        try:
            cfg_candidates = [
                Path(__file__).resolve().parent / "app" / "enhanced_gui_settings.json",
                Path(__file__).resolve().parent / "enhanced_gui_settings.json",
            ]
            for cfg_path in cfg_candidates:
                if cfg_path.exists():
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                    for key in ("selected_folder_path", "last_open_dir", "default_export_dir"):
                        folder_cand = cfg.get(key)
                        if folder_cand and Path(folder_cand).exists():
                            paper = App.find_original_paper_in_folder(Path(folder_cand))
                            if paper:
                                return paper
        except Exception:
            pass
        return None

    def _build_ui(self) -> None:
        padding = {"padx": 12, "pady": 7}
        main = ttk.Frame(self)
        main.pack(fill="both", expand=True, padx=14, pady=14)

        main.columnconfigure(1, weight=1)

        ttk.Label(main, text="原 Word 文档").grid(row=0, column=0, sticky="w", **padding)
        ttk.Entry(main, textvariable=self.source_var).grid(row=0, column=1, sticky="ew", **padding)
        ttk.Button(main, text="选择...", command=self.choose_source).grid(row=0, column=2, sticky="ew", **padding)

        ttk.Label(main, text="模板文件").grid(row=1, column=0, sticky="w", **padding)
        ttk.Entry(main, textvariable=self.template_var).grid(row=1, column=1, sticky="ew", **padding)
        ttk.Button(main, text="选择...", command=self.choose_template).grid(row=1, column=2, sticky="ew", **padding)

        ttk.Label(main, text="输出文件").grid(row=2, column=0, sticky="w", **padding)
        output_entry = ttk.Entry(main, textvariable=self.output_var)
        output_entry.grid(row=2, column=1, sticky="ew", **padding)
        output_entry.bind("<KeyRelease>", self.mark_output_touched)
        ttk.Button(main, text="另存为...", command=self.choose_output).grid(row=2, column=2, sticky="ew", **padding)

        ttk.Label(main, text="题目结束标记").grid(row=3, column=0, sticky="w", **padding)
        ttk.Entry(main, textvariable=self.answer_mark_var).grid(row=3, column=1, sticky="ew", **padding)

        ttk.Checkbutton(
            main,
            text="方框和填空横线使用红色",
            variable=self.red_marks_var,
        ).grid(row=4, column=1, sticky="w", **padding)

        ttk.Separator(main).grid(row=5, column=0, columnspan=3, sticky="ew", pady=(10, 8))

        self.log_box = tk.Text(main, height=8, wrap="word", state="disabled")
        self.log_box.grid(row=6, column=0, columnspan=3, sticky="nsew", padx=12, pady=7)
        main.rowconfigure(6, weight=1)

        bottom = ttk.Frame(main)
        bottom.grid(row=7, column=0, columnspan=3, sticky="ew", padx=12, pady=(10, 0))
        bottom.columnconfigure(0, weight=1)

        ttk.Label(bottom, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        self.run_button = ttk.Button(bottom, text="开始处理", command=self.start_processing)
        self.run_button.grid(row=0, column=1, sticky="e")

    def load_default_template(self) -> None:
        if DEFAULT_TEMPLATE_PATH.exists():
            self.template_var.set(str(DEFAULT_TEMPLATE_PATH.resolve()))
            self.status_var.set(f"已自动填入同目录直批模板（{DEFAULT_TEMPLATE_PATH.name}），请选择原 Word 文档。")
        else:
            self.status_var.set("未找到默认直批模板，请手动选择模板文件。")

    def choose_source(self) -> None:
        init_dir = None
        if self.source_var.get():
            try:
                p = Path(self.source_var.get())
                init_dir = str(p.parent if p.is_file() else p)
            except Exception:
                pass
        if not init_dir:
            try:
                cfg_candidates = [
                    Path(__file__).resolve().parent / "app" / "enhanced_gui_settings.json",
                    Path(__file__).resolve().parent / "enhanced_gui_settings.json",
                ]
                for cfg_path in cfg_candidates:
                    if cfg_path.exists():
                        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                        cand = cfg.get("selected_folder_path") or cfg.get("last_open_dir")
                        if cand and Path(cand).exists():
                            init_dir = str(cand)
                            break
            except Exception:
                pass

        filename = filedialog.askopenfilename(
            title="请选择原 Word 文档",
            initialdir=init_dir,
            filetypes=[("Word 文档", "*.docx"), ("所有文件", "*.*")],
        )
        if not filename:
            return
        self.source_var.set(filename)
        self.update_default_output()

    def choose_template(self) -> None:
        filename = filedialog.askopenfilename(
            title="请选择模板文件",
            filetypes=[("Word 模板", "*.dotx"), ("所有文件", "*.*")],
        )
        if filename:
            self.template_var.set(filename)

    def choose_output(self) -> None:
        initial_file = self.output_var.get().strip()
        initial_dir = str(Path(initial_file).parent) if initial_file else ""
        initial_name = Path(initial_file).name if initial_file else ""
        filename = filedialog.asksaveasfilename(
            title="请选择输出文件",
            initialdir=initial_dir,
            initialfile=initial_name,
            defaultextension=".docx",
            filetypes=[("Word 文档", "*.docx")],
        )
        if filename:
            self.output_touched = True
            self.output_var.set(filename)

    def mark_output_touched(self, _event=None) -> None:
        self.output_touched = True

    def update_default_output(self) -> None:
        if self.output_touched:
            return
        source_text = self.source_var.get().strip()
        if not source_text:
            return
        source_path = Path(source_text)
        self.output_var.set(str(source_path.with_name(f"{source_path.stem}套用模板不处理答案.docx")))

    def append_log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        self.status_var.set(message)

    def post_log(self, message: str) -> None:
        self.after(0, lambda: self.append_log(message))

    def validate_inputs(self) -> tuple[Path, Path, Path, str, bool] | None:
        source_path = Path(self.source_var.get().strip())
        template_path = Path(self.template_var.get().strip())
        output_path = Path(self.output_var.get().strip())
        answer_mark = self.answer_mark_var.get().strip()
        use_red_marks = bool(self.red_marks_var.get())

        if not source_path.exists() or source_path.suffix.lower() != ".docx":
            messagebox.showwarning("需要原 Word 文档", "请选择一个存在的 .docx 原文件。")
            return None
        if not template_path.exists() or template_path.suffix.lower() != ".dotx":
            messagebox.showwarning("需要模板文件", "请选择一个存在的 .dotx 模板文件。")
            return None
        if not output_path.name:
            messagebox.showwarning("需要输出文件", "请选择或填写输出文件。")
            return None
        if output_path.suffix.lower() != ".docx":
            output_path = output_path.with_suffix(".docx")
            self.output_var.set(str(output_path))
        if not answer_mark:
            messagebox.showwarning("需要题目结束标记", "请填写用于分隔题目和答案的文字，例如“参考答案”。")
            return None
        if output_path.exists():
            ok = messagebox.askyesno("文件已存在", f"{output_path.name} 已存在，是否覆盖？")
            if not ok:
                return None
        return source_path, template_path, output_path, answer_mark, use_red_marks

    def start_processing(self) -> None:
        validated = self.validate_inputs()
        if validated is None:
            return

        self.run_button.configure(state="disabled")
        self.append_log("开始处理。")

        worker = threading.Thread(target=self.process_worker, args=validated, daemon=True)
        worker.start()

    def process_worker(
        self,
        source_path: Path,
        template_path: Path,
        output_path: Path,
        answer_mark: str,
        use_red_marks: bool,
    ) -> None:
        try:
            box_count, picture_count, fill_blank_count = copy_questions_to_template(
                source_path,
                template_path,
                output_path,
                answer_mark,
                log=self.post_log,
                use_red_marks=use_red_marks,
            )
        except Exception as exc:
            message = str(exc)
            detail = traceback.format_exc()
            self.after(0, lambda message=message, detail=detail: self.processing_failed(message, detail))
            return

        self.after(0, lambda: self.processing_finished(output_path, box_count, picture_count, fill_blank_count))

    def processing_failed(self, message: str, detail: str) -> None:
        self.run_button.configure(state="normal")
        self.append_log("处理失败。")
        self.append_log(detail)
        messagebox.showerror("处理失败", message)

    def processing_finished(self, output_path: Path, box_count: int, picture_count: int, fill_blank_count: int) -> None:
        self.run_button.configure(state="normal")
        self.append_log("处理完成。")
        messagebox.showinfo(
            "处理完成",
            "已生成：\n"
            f"{output_path}\n\n"
            f"已添加方框：{box_count} 个\n"
            f"已处理填空横线：{fill_blank_count} 处\n"
            "行距调整：填空题横线段落已设为 1.5 倍行距\n"
            f"已设置黑白 50% 图片：{picture_count} 张\n"
            "答案处理：计算题仅保留栏目名、题号和小题号",
        )


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
