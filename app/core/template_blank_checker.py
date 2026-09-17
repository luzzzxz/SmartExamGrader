# -*- coding: utf-8 -*-
"""
模板试卷红横线与选择题小红框校验工具
功能：
1. 自动检测Word试卷（如 *模板*.docx）中的：
   - 选择题选项小红框（☐/□）：检查是否与答案题数严格匹配（每题4个，总数=题数×4）
   - 填空题红色填空横线：检查横线数量是否与参考答案数量一致，以及横线是否过短
2. 重点防范微调试卷插图时，图片覆盖/吞掉选项小红框或填空横线
3. 提供可视化双题型比对表、详细诊断及一键Word跳转修复
"""

import os
import sys
import re
import json
import docx
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# ---------------------------------------------------------------------------
# 核心检测算法
# ---------------------------------------------------------------------------

def check_docx_template_elements(docx_path, min_eff_len=7, external_answers=None):
    """
    全面检测Word试卷文档中的选择题小红框与填空题红色横线。
    :param docx_path: docx 文件路径
    :param min_eff_len: 填空横线最短有效字符宽度，默认7（全角计2，半角计1）
    :param external_answers: 可选外部答案字典
    :return: 包含完整校验结果的字典
    """
    p = Path(docx_path)
    if not p.exists():
        return {'ok': False, 'error': f'文件不存在: {docx_path}'}
    if p.suffix.lower() != '.docx':
        return {'ok': False, 'error': f'仅支持 .docx 格式文档: {docx_path}'}

    try:
        doc = docx.Document(str(p))
    except Exception as e:
        return {'ok': False, 'error': f'无法打开Word文档: {e}'}

    # 1. 查找【参考答案】段落分界点
    ans_idx = len(doc.paragraphs)
    for i, para in enumerate(doc.paragraphs):
        t = para.text.strip()
        if any(kw in t for kw in ['参考答案', '答案与解析', '【参考答案】', '试题答案', '答案:']):
            ans_idx = i
            break

    # 2. 解析参考答案
    choice_answers = {}
    blank_answers = {}
    if ans_idx < len(doc.paragraphs):
        for i in range(ans_idx + 1, len(doc.paragraphs)):
            t = doc.paragraphs[i].text.strip()
            if not t or any(kw in t for kw in ['计算题', '作图题', '简答题', '综合题']):
                continue
            t_clean = re.sub(r'[\r\n]+', '', t)
            m = re.match(r'^(\d+)[\.．、\s]+(.*)$', t_clean)
            if m:
                q_no = int(m.group(1))
                ans_text = m.group(2).strip()
                # 判断是否为纯选择题答案（如 A、B、CD、A,B,C）
                if re.match(r'^[A-D](\s*[,，、]\s*[A-D])*$', ans_text, re.IGNORECASE):
                    choice_answers[q_no] = ans_text
                else:
                    parts = [x.strip() for x in re.split(r'[；;]+', ans_text) if x.strip()]
                    if not parts and ans_text:
                        parts = [ans_text]
                    blank_answers[q_no] = {
                        'answers': parts,
                        'count': len(parts),
                        'raw': ans_text
                    }

    # 补充外部答案
    if external_answers and isinstance(external_answers, dict):
        for q_no_raw, ans_val in external_answers.items():
            try:
                q_no = int(q_no_raw)
            except Exception:
                continue
            if q_no not in choice_answers and q_no not in blank_answers:
                if isinstance(ans_val, str) and re.match(r'^[A-D](\s*[,，、]\s*[A-D])*$', ans_val.strip(), re.I):
                    choice_answers[q_no] = ans_val.strip()
                elif isinstance(ans_val, list):
                    blank_answers[q_no] = {'answers': ans_val, 'count': len(ans_val), 'raw': ';'.join(ans_val)}
                elif isinstance(ans_val, str):
                    parts = [x.strip() for x in re.split(r'[；;]+', ans_val) if x.strip()]
                    blank_answers[q_no] = {'answers': parts, 'count': len(parts), 'raw': ans_val}

    def is_red_color(r):
        r_xml = r._r.xml
        color = str(r.font.color.rgb) if r.font.color and r.font.color.rgb else ''
        return (
            color.lower() in ['ff0000', 'red', 'c00000']
            or 'w:color w:val="FF0000"' in r_xml
            or 'w:color w:val="red"' in r_xml
            or 'w:val="255"' in r_xml
            or 'w:color w:val="C00000"' in r_xml
        )

    def is_underline(r):
        r_xml = r._r.xml
        u = r.font.underline
        return (u is not None and u is not False) or ('<w:u' in r_xml and 'w:val="none"' not in r_xml)

    # 3. 扫描题干（0 ~ ans_idx - 1）中的小红框与红横线
    current_q_no = None
    questions_detected = {}

    def ensure_q(q):
        if q not in questions_detected:
            questions_detected[q] = {
                'blanks': [],
                'red_boxes': [],
                'all_boxes': [],
                'stem_preview': ''
            }

    for p_idx in range(ans_idx):
        para = doc.paragraphs[p_idx]
        p_text = para.text.strip()
        m_q = re.match(r'^(\d+)[\.．、\s]+', p_text)
        if m_q:
            current_q_no = int(m_q.group(1))
            ensure_q(current_q_no)
            if not questions_detected[current_q_no]['stem_preview']:
                questions_detected[current_q_no]['stem_preview'] = p_text[:100]

        in_blank = False
        blank_chars = []

        for r in para.runs:
            text = r.text
            has_red = is_red_color(r)
            has_u = is_underline(r)
            has_underscore = bool(re.search(r'[_＿]{2,}', text))

            # 检查小红框 (☐ / □ / ☒ / ☑ 等)
            for ch in text:
                if ch in ['☐', '□', '☑', '☒'] or ord(ch) in [0x2610, 0x25a1, 0xf06f, 0xf0a8]:
                    if current_q_no is not None:
                        ensure_q(current_q_no)
                        box_item = {'p_idx': p_idx, 'ch': ch, 'is_red': has_red}
                        questions_detected[current_q_no]['all_boxes'].append(box_item)
                        if has_red:
                            questions_detected[current_q_no]['red_boxes'].append(box_item)

            # 检查红色填空下划线（必须非空）
            is_blank_run = (has_u and text.strip() == '' and len(text) > 0) or has_underscore
            is_red_blank = is_blank_run and has_red

            if is_red_blank:
                if not in_blank:
                    in_blank = True
                    blank_chars = [text]
                else:
                    blank_chars.append(text)
            else:
                if in_blank:
                    full_text = "".join(blank_chars)
                    eff_len = sum(2 if ord(ch) > 127 else 1 for ch in full_text)
                    if eff_len > 0 and current_q_no is not None:
                        ensure_q(current_q_no)
                        questions_detected[current_q_no]['blanks'].append({
                            'p_idx': p_idx,
                            'text': full_text,
                            'eff_len': eff_len,
                            'is_too_short': eff_len < min_eff_len
                        })
                    in_blank = False
                    blank_chars = []

        if in_blank:
            full_text = "".join(blank_chars)
            eff_len = sum(2 if ord(ch) > 127 else 1 for ch in full_text)
            if eff_len > 0 and current_q_no is not None:
                ensure_q(current_q_no)
                questions_detected[current_q_no]['blanks'].append({
                    'p_idx': p_idx,
                    'text': full_text,
                    'eff_len': eff_len,
                    'is_too_short': eff_len < min_eff_len
                })

    # 表格扫描
    for t_idx, table in enumerate(doc.tables):
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    p_text = para.text.strip()
                    m_q = re.match(r'^(\d+)[\.．、\s]+', p_text)
                    tbl_q = int(m_q.group(1)) if m_q else current_q_no
                    if tbl_q:
                        ensure_q(tbl_q)
                    in_blank = False
                    blank_chars = []
                    for r in para.runs:
                        text = r.text
                        has_red = is_red_color(r)
                        has_u = is_underline(r)
                        has_underscore = bool(re.search(r'[_＿]{2,}', text))
                        for ch in text:
                            if ch in ['☐', '□', '☑', '☒'] or ord(ch) in [0x2610, 0x25a1, 0xf06f, 0xf0a8]:
                                if tbl_q:
                                    box_item = {'p_idx': -1, 'ch': ch, 'is_red': has_red}
                                    questions_detected[tbl_q]['all_boxes'].append(box_item)
                                    if has_red:
                                        questions_detected[tbl_q]['red_boxes'].append(box_item)
                        is_red_blank = ((has_u and text.strip() == '' and len(text) > 0) or has_underscore) and has_red
                        if is_red_blank:
                            if not in_blank:
                                in_blank = True
                                blank_chars = [text]
                            else:
                                blank_chars.append(text)
                        else:
                            if in_blank:
                                full_text = "".join(blank_chars)
                                eff_len = sum(2 if ord(ch) > 127 else 1 for ch in full_text)
                                if eff_len > 0 and tbl_q:
                                    questions_detected[tbl_q]['blanks'].append({
                                        'p_idx': -1,
                                        'text': full_text,
                                        'eff_len': eff_len,
                                        'is_too_short': eff_len < min_eff_len
                                    })
                                in_blank = False
                                blank_chars = []
                    if in_blank and tbl_q:
                        full_text = "".join(blank_chars)
                        eff_len = sum(2 if ord(ch) > 127 else 1 for ch in full_text)
                        if eff_len > 0:
                            questions_detected[tbl_q]['blanks'].append({
                                'p_idx': -1,
                                'text': full_text,
                                'eff_len': eff_len,
                                'is_too_short': eff_len < min_eff_len
                            })

    # 4. 逐题核对比对
    results = []

    # (A) 选择题核对
    num_choice_qs = len(choice_answers)
    expected_choice_boxes = num_choice_qs * 4
    total_detected_choice_boxes = 0
    choice_mismatches = []

    for q_no in sorted(choice_answers.keys()):
        ans_val = choice_answers[q_no]
        det = questions_detected.get(q_no, {'red_boxes': [], 'all_boxes': [], 'stem_preview': '', 'blanks': []})
        r_box_cnt = len(det['red_boxes'])
        total_detected_choice_boxes += r_box_cnt

        status = 'ok'
        status_text = '✅ 正常 (4个红方框)'
        if r_box_cnt != 4:
            choice_mismatches.append(q_no)
            if r_box_cnt < 4:
                status = 'missing_box'
                status_text = f'❌ 缺少红方框 (仅{r_box_cnt}/4个，疑被图片遮挡)'
            else:
                status = 'extra_box'
                status_text = f'⚠️ 红方框多于4个 (检测到{r_box_cnt}个)'

        results.append({
            'q_no': q_no,
            'q_type': 'choice',
            'q_type_text': '选择题',
            'expected_count': 4,
            'actual_count': r_box_cnt,
            'status': status,
            'status_text': status_text,
            'answers': [ans_val],
            'min_len': 0,
            'stem_preview': det['stem_preview'],
            'boxes': det['red_boxes'],
            'blanks': []
        })

    # (B) 填空题核对
    total_blank_blanks = 0
    total_blank_answers = 0
    blank_mismatches = []
    blank_shorts = []

    for q_no in sorted(blank_answers.keys()):
        b_info = blank_answers[q_no]
        det = questions_detected.get(q_no, {'blanks': [], 'stem_preview': '', 'red_boxes': []})
        b_cnt = len(det['blanks'])
        a_cnt = b_info['count']
        total_blank_blanks += b_cnt
        total_blank_answers += a_cnt

        short_blanks = [b for b in det['blanks'] if b['is_too_short']]
        min_len = min([b['eff_len'] for b in det['blanks']]) if det['blanks'] else 0

        status = 'ok'
        status_text = '✅ 匹配通过'
        if b_cnt != a_cnt:
            blank_mismatches.append(q_no)
            if b_cnt < a_cnt:
                status = 'missing_blank'
                status_text = f'❌ 缺少横线 (差{a_cnt - b_cnt}条，疑被图片吞掉)'
            else:
                status = 'extra_blank'
                status_text = f'❌ 横线多于答案 (多{b_cnt - a_cnt}条)'
        elif short_blanks:
            blank_shorts.append(q_no)
            status = 'too_short'
            status_text = f'⚠️ 横线过短 ({len(short_blanks)}处低于{min_eff_len}字符)'

        results.append({
            'q_no': q_no,
            'q_type': 'blank',
            'q_type_text': '填空题',
            'expected_count': a_cnt,
            'actual_count': b_cnt,
            'status': status,
            'status_text': status_text,
            'answers': b_info['answers'],
            'min_len': min_len,
            'stem_preview': det['stem_preview'],
            'boxes': [],
            'blanks': det['blanks']
        })

    choice_passed = (len(choice_mismatches) == 0) and (total_detected_choice_boxes == expected_choice_boxes)
    blank_passed = (len(blank_mismatches) == 0) and (len(blank_shorts) == 0) and (total_blank_blanks == total_blank_answers)
    overall_passed = choice_passed and blank_passed

    return {
        'ok': True,
        'filename': p.name,
        'filepath': str(p.resolve()),
        'overall_passed': overall_passed,
        'choice_summary': {
            'num_questions': num_choice_qs,
            'expected_boxes': expected_choice_boxes,
            'actual_boxes': total_detected_choice_boxes,
            'passed': choice_passed,
            'mismatch_qs': choice_mismatches
        },
        'blank_summary': {
            'num_questions': len(blank_answers),
            'expected_blanks': total_blank_answers,
            'actual_blanks': total_blank_blanks,
            'passed': blank_passed,
            'mismatch_qs': blank_mismatches,
            'short_qs': blank_shorts
        },
        'results': results
    }

# 兼容旧函数名
check_docx_red_blanks = check_docx_template_elements

# ---------------------------------------------------------------------------
# 可视化检测窗口类
# ---------------------------------------------------------------------------

class TemplateBlankCheckerWindow:
    def __init__(self, master=None, initial_path=None):
        if master is None:
            self.root = tk.Tk()
            self.is_toplevel = False
        else:
            self.root = tk.Toplevel(master)
            self.is_toplevel = True
            try:
                if master.winfo_exists() and master.winfo_viewable():
                    self.root.transient(master)
            except Exception:
                pass

        self.root.title("模板试卷红横线与选择题小红框校验工具")
        self.root.geometry("1020x720")
        self.root.minsize(900, 600)
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

        # 数据变量
        self.path_var = tk.StringVar()
        self.min_len_var = tk.IntVar(value=7)
        self.current_report = None

        found_target = self._resolve_initial_path(initial_path)
        if found_target:
            self.path_var.set(str(found_target))

        self._build_ui()

        # 若已定位到目标模板，窗口就绪后自动开始检测并弹出提示
        if self.path_var.get().strip():
            self.root.after(200, lambda: self.start_check(show_popup=True))

    def _resolve_initial_path(self, initial_path):
        """智能解析待测文件路径"""
        if initial_path:
            p = Path(initial_path)
            if p.is_file() and p.suffix.lower() == '.docx':
                return p
            elif p.is_dir():
                cands = [x for x in p.glob("*模板*.docx") if not x.name.startswith("~$") and '删除' not in x.name]
                if cands:
                    cands.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                    return cands[0]
                all_docx = [x for x in p.glob("*.docx") if not x.name.startswith("~$") and '删除' not in x.name]
                if all_docx:
                    all_docx.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                    return all_docx[0]

        # 尝试从主程序配置读取最近活跃测试目录
        try:
            cfg_paths = [
                Path(__file__).resolve().parent.parent / "enhanced_gui_settings.json",
                Path(__file__).resolve().parent.parent.parent / "app" / "enhanced_gui_settings.json",
            ]
            for cfg_path in cfg_paths:
                if cfg_path.exists():
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                    for key in ("selected_folder_path", "last_open_dir", "default_export_dir"):
                        cand = cfg.get(key)
                        if cand and Path(cand).exists() and Path(cand).is_dir():
                            tmpls = [x for x in Path(cand).glob("*模板*.docx") if not x.name.startswith("~$") and '删除' not in x.name]
                            if tmpls:
                                tmpls.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                                return tmpls[0]
        except Exception:
            pass

        cwd = Path.cwd()
        cands = [x for x in cwd.glob("*模板*.docx") if not x.name.startswith("~$") and '删除' not in x.name]
        if cands:
            cands.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            return cands[0]
        return None

    def _build_ui(self):
        # 1. 顶部操作栏
        top_frame = ttk.LabelFrame(self.root, text="待检测试卷文档", padding=10)
        top_frame.pack(fill=tk.X, padx=12, pady=(10, 6))

        row1 = ttk.Frame(top_frame)
        row1.pack(fill=tk.X)

        ttk.Label(row1, text="Word文档路径:", font=("Microsoft YaHei", 9, "bold")).pack(side=tk.LEFT)
        self.path_entry = ttk.Entry(row1, textvariable=self.path_var)
        self.path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)

        ttk.Button(row1, text="浏览...", command=self._browse_file).pack(side=tk.LEFT, padx=4)

        row2 = ttk.Frame(top_frame)
        row2.pack(fill=tk.X, pady=(8, 0))

        ttk.Label(row2, text="填空横线最短宽度:").pack(side=tk.LEFT)
        spin = ttk.Spinbox(row2, from_=3, to=25, textvariable=self.min_len_var, width=4)
        spin.pack(side=tk.LEFT, padx=4)
        ttk.Label(row2, text="字符 (低于此值报警)   |   选择题规则：每题必须有4个选项小红框(A/B/C/D)", foreground="#555").pack(side=tk.LEFT)

        btn_box = ttk.Frame(row2)
        btn_box.pack(side=tk.RIGHT)

        self.btn_open_word = ttk.Button(btn_box, text="📝 在 Word 中打开此文件", command=self._open_in_word)
        self.btn_open_word.pack(side=tk.LEFT, padx=6)

        self.btn_check = ttk.Button(btn_box, text="🔍 开始全面检测", command=lambda: self.start_check(show_popup=True))
        self.btn_check.pack(side=tk.LEFT, padx=4)

        # 2. 状态横幅（大提示栏）
        self.banner_frame = tk.Frame(self.root, bg="#E8F4FD", height=58, bd=1, relief="solid")
        self.banner_frame.pack(fill=tk.X, padx=12, pady=6)
        self.banner_frame.pack_propagate(False)

        self.banner_label = tk.Label(
            self.banner_frame,
            text="ℹ️ 请选择带有“模板”关键词的 Word 试卷文档，然后点击【开始全面检测】",
            font=("Microsoft YaHei", 10, "bold"),
            bg="#E8F4FD",
            fg="#0D47A1"
        )
        self.banner_label.pack(expand=True, fill=tk.BOTH, padx=10)

        # 3. 中间表格与明细
        mid_paned = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        mid_paned.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)

        # 上半部分：题目表格
        table_frame = ttk.LabelFrame(mid_paned, text="各题小红框 / 红横线与参考答案全面核对表", padding=6)
        mid_paned.add(table_frame, weight=3)

        cols = ("q_no", "q_type", "status", "actual_count", "expected_count", "min_len", "ans_preview")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("q_no", text="题号")
        self.tree.heading("q_type", text="题型")
        self.tree.heading("status", text="匹配状态")
        self.tree.heading("actual_count", text="实际识别标记")
        self.tree.heading("expected_count", text="标准期望")
        self.tree.heading("min_len", text="横线最短宽")
        self.tree.heading("ans_preview", text="参考答案")

        self.tree.column("q_no", width=65, anchor="center")
        self.tree.column("q_type", width=70, anchor="center")
        self.tree.column("status", width=250, anchor="w")
        self.tree.column("actual_count", width=120, anchor="center")
        self.tree.column("expected_count", width=120, anchor="center")
        self.tree.column("min_len", width=95, anchor="center")
        self.tree.column("ans_preview", width=240, anchor="w")

        tree_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self._on_select_row)

        # 下半部分：选中项详细信息与排错建议
        detail_frame = ttk.LabelFrame(mid_paned, text="题目诊断详情与排错修复指导", padding=8)
        mid_paned.add(detail_frame, weight=2)

        self.detail_text = tk.Text(detail_frame, wrap="word", font=("Microsoft YaHei", 9), height=7)
        self.detail_text.pack(fill=tk.BOTH, expand=True)

        # 4. 底部快捷操作与说明
        bottom_frame = ttk.Frame(self.root, padding=(12, 4))
        bottom_frame.pack(fill=tk.X)

        ttk.Label(
            bottom_frame,
            text="💡 防遮挡提示：调整图片大小时容易覆盖选项红方框或填空横线。若有异常，请点击【在 Word 中打开此文件】微调保存后重新检测。",
            foreground="#555"
        ).pack(side=tk.LEFT)

        ttk.Button(bottom_frame, text="关闭", command=self.close).pack(side=tk.RIGHT, padx=4)

    def close(self):
        self.root.destroy()

    def _browse_file(self):
        curr = self.path_var.get().strip()
        init_dir = None
        if curr:
            try:
                p = Path(curr)
                init_dir = str(p.parent if p.is_file() else p)
            except Exception:
                pass
        if not init_dir:
            try:
                cfg_path = Path(__file__).resolve().parent.parent / "enhanced_gui_settings.json"
                if cfg_path.exists():
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                    cand = cfg.get("selected_folder_path") or cfg.get("last_open_dir")
                    if cand and Path(cand).exists():
                        init_dir = str(cand)
            except Exception:
                pass

        f = filedialog.askopenfilename(
            parent=self.root,
            title="选择带有模板关键词的 Word 试卷",
            initialdir=init_dir,
            filetypes=[("Word模板文档", "*模板*.docx"), ("Word文档", "*.docx"), ("所有文件", "*.*")]
        )
        if f:
            self.path_var.set(f)
            self.start_check(show_popup=True)

    def _open_in_word(self):
        p = self.path_var.get().strip()
        if not p or not Path(p).exists():
            messagebox.showwarning("提示", "请先选择有效的 Word 文件！", parent=self.root)
            return
        try:
            os.startfile(str(Path(p).resolve()))
        except Exception as e:
            messagebox.showerror("打开失败", f"无法启动 Word 打开文件：\n{e}", parent=self.root)

    def start_check(self, show_popup=True):
        p = self.path_var.get().strip()
        if not p:
            if show_popup:
                messagebox.showwarning("提示", "请先选择需要检测的 Word 文档！", parent=self.root)
            return

        min_len = self.min_len_var.get()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.detail_text.delete("1.0", tk.END)

        report = check_docx_template_elements(p, min_eff_len=min_len)
        self.current_report = report

        if not report['ok']:
            self._set_banner(f"❌ 检测失败: {report.get('error')}", bg="#FFEBEE", fg="#C62828")
            if show_popup:
                messagebox.showerror("检测出错", report.get('error'), parent=self.root)
            return

        results = report['results']
        choice_s = report['choice_summary']
        blank_s = report['blank_summary']

        # 填充表格
        for r in results:
            q_no = f"第 {r['q_no']} 题"
            q_type = r['q_type_text']
            ans_str = "；".join(r['answers']) if r['answers'] else "-"
            
            if r['q_type'] == 'choice':
                act_str = f"{r['actual_count']} 个小红框"
                exp_str = "4 个选项红框"
                min_l_str = "-"
            else:
                act_str = f"{r['actual_count']} 条红横线"
                exp_str = f"{r['expected_count']} 空答案"
                min_l_str = f"{r['min_len']} 字符" if r['actual_count'] > 0 else "-"

            tag = "ok"
            if r['status'] in ['missing_box', 'extra_box', 'missing_blank', 'extra_blank']:
                tag = "error"
            elif r['status'] == 'too_short':
                tag = "warning"

            self.tree.insert(
                "",
                tk.END,
                iid=str(r['q_no']),
                values=(q_no, q_type, r['status_text'], act_str, exp_str, min_l_str, ans_str),
                tags=(tag,)
            )

        self.tree.tag_configure("ok", foreground="#2E7D32")
        self.tree.tag_configure("error", foreground="#C62828", font=("Microsoft YaHei", 9, "bold"))
        self.tree.tag_configure("warning", foreground="#E65100")

        # 综合评判横幅与弹窗提示
        c_passed = choice_s['passed']
        b_passed = blank_s['passed']
        overall_passed = report['overall_passed']

        if overall_passed:
            c_msg = f"选择题（{choice_s['num_questions']}题×4={choice_s['actual_boxes']}个红方框）"
            b_msg = f"填空题（{blank_s['num_questions']}题/{blank_s['actual_blanks']}条红横线）"
            banner_txt = f"🎉【检测全部通过】{c_msg} 与 {b_msg} 均完全匹配，横线长度充足，无图片遮挡！"
            self._set_banner(banner_txt, bg="#E8F5E9", fg="#2E7D32")

            if show_popup:
                messagebox.showinfo(
                    "检测通过",
                    f"恭喜！试卷全项检测通过！\n\n"
                    f"• 文件：{report['filename']}\n"
                    f"• 选择题：共 {choice_s['num_questions']} 题，检测到 {choice_s['actual_boxes']} 个小红框（每题4个A/B/C/D红框全齐）\n"
                    f"• 填空题：共 {blank_s['num_questions']} 题，检测到 {blank_s['actual_blanks']} 条红横线（与答案完全一致且无过短现象）\n\n"
                    f"未发现图片遮挡或标记遗漏，可放心打印及改卷！",
                    parent=self.root
                )
        else:
            err_details = []
            banner_alerts = []

            if not c_passed:
                m_qs = [str(q) for q in choice_s['mismatch_qs']]
                banner_alerts.append(f"选择题第 {', '.join(m_qs)} 题红方框不匹配(实际{choice_s['actual_boxes']}/应有{choice_s['expected_boxes']}个)")
                err_details.append(
                    f"【选择题小红框异常】\n"
                    f"• 答案共有 {choice_s['num_questions']} 题选择题，标准应有 {choice_s['expected_boxes']} 个小红框(每题4个)；\n"
                    f"• 实际仅检测到 {choice_s['actual_boxes']} 个小红框；\n"
                    f"• 异常题号：第 {', '.join(m_qs)} 题（极可能被插图遮挡或选项漏加方框）！"
                )

            if not b_passed:
                if blank_s['mismatch_qs']:
                    m_qs = [str(q) for q in blank_s['mismatch_qs']]
                    banner_alerts.append(f"填空题第 {', '.join(m_qs)} 题红横线缺少或不符")
                    err_details.append(
                        f"【填空题红横线异常】\n"
                        f"• 填空答案共需 {blank_s['expected_blanks']} 条横线，实际识别到 {blank_s['actual_blanks']} 条；\n"
                        f"• 异常题号：第 {', '.join(m_qs)} 题（极可能被插图遮挡或漏标红色下划线）！"
                    )
                if blank_s['short_qs']:
                    s_qs = [str(q) for q in blank_s['short_qs']]
                    banner_alerts.append(f"填空题第 {', '.join(s_qs)} 题横线过短")
                    err_details.append(
                        f"【填空题横线过短】\n"
                        f"• 第 {', '.join(s_qs)} 题有效宽度不足 {min_len} 字符，学生无法书写且摄像头不易定位！"
                    )

            banner_txt = f"⚠️【警告：发现异常！】" + "；".join(banner_alerts)
            self._set_banner(banner_txt, bg="#FFEBEE", fg="#C62828")

            if show_popup:
                messagebox.showwarning(
                    "检测警告：发现不匹配项",
                    "注意！试卷存在以下不匹配项，请重点核对：\n\n"
                    + "\n\n".join(err_details)
                    + "\n\n💡 建议：点击【在 Word 中打开此文件】排查附近图片是否遮挡了选项方框或横线！",
                    parent=self.root
                )

    def _set_banner(self, text, bg, fg):
        self.banner_frame.configure(bg=bg)
        self.banner_label.configure(text=text, bg=bg, fg=fg)

    def _on_select_row(self, event):
        sel = self.tree.selection()
        if not sel or not self.current_report:
            return
        q_no = int(sel[0])
        r_item = next((r for r in self.current_report['results'] if r['q_no'] == q_no), None)
        if not r_item:
            return

        self.detail_text.delete("1.0", tk.END)
        lines = []
        if r_item['q_type'] == 'choice':
            lines.append(f"【第 {q_no} 题 (选择题) 诊断详情】")
            lines.append(f"• 当前状态：{r_item['status_text']}")
            lines.append(f"• 识别到的选项小红框：{r_item['actual_count']} 个 (标准应为 4 个，分别对应 A/B/C/D)")
            lines.append(f"• 参考答案内容：{''.join(r_item['answers'])}")
            lines.append(f"• 题干预览：{r_item['stem_preview']}")
            lines.append("")
            if r_item['status'] == 'missing_box':
                lines.append(f"💡 修复建议：该选择题仅检测到 {r_item['actual_count']} 个小红框，缺少 {4 - r_item['actual_count']} 个！极可能是移动排版插图时遮挡住了选项前面的小方框，或者原题某个选项漏掉了小红框。请点击【在 Word 中打开此文件】微调插图位置。")
            elif r_item['status'] == 'extra_box':
                lines.append("💡 修复建议：该选择题红方框多于 4 个，请检查是否多选了选项或题干正文里包含了多余的方框符号。")
            else:
                lines.append("✅ 该选择题的 4 个选项小红框完整齐备，配置正常。")
        else:
            lines.append(f"【第 {q_no} 题 (填空题) 诊断详情】")
            lines.append(f"• 当前状态：{r_item['status_text']}")
            lines.append(f"• 识别到的红色横线数：{r_item['actual_count']} 条")
            lines.append(f"• 参考答案提取数：{r_item['expected_count']} 处")
            if r_item['answers']:
                lines.append(f"• 参考答案内容：{' ； '.join(r_item['answers'])}")
            else:
                lines.append("• 参考答案内容：(Word末尾答案中未提取到本题)")

            if r_item['blanks']:
                blank_lens = [f"第{i+1}空: {b['eff_len']}字符" for i, b in enumerate(r_item['blanks'])]
                lines.append(f"• 各横线有效长度：{', '.join(blank_lens)}")
            else:
                lines.append("• 各横线有效长度：(未检测到任何红色横线)")

            lines.append(f"• 题干预览：{r_item['stem_preview']}")
            lines.append("")
            if r_item['status'] == 'missing_blank':
                lines.append("💡 修复建议：该题答案有多空，但检测到的横线偏少！请检查该题附近是否有插图遮挡住了横线，或横线颜色不是鲜红色 (RGB: 255, 0, 0)。")
            elif r_item['status'] == 'extra_blank':
                lines.append("💡 修复建议：该题红横线多于参考答案，请检查是否题干中其他文字带了红色下划线，或答案分界漏掉了部分分号。")
            elif r_item['status'] == 'too_short':
                lines.append("💡 修复建议：该题部分横线过短，建议在 Word 中补打几个红色空格加长横线。")
            else:
                lines.append("✅ 该题配置与横线条数完全正常。")

        self.detail_text.insert(tk.END, "\n".join(lines))

def open_template_blank_checker_dialog(parent, initial_path=None):
    """供主程序调用的接口函数"""
    app = TemplateBlankCheckerWindow(master=parent, initial_path=initial_path)
    return app.root

TemplateBlankCheckerDialog = TemplateBlankCheckerWindow

if __name__ == '__main__':
    test_p = None
    if len(sys.argv) > 1:
        test_p = sys.argv[1]
    app = TemplateBlankCheckerWindow(master=None, initial_path=test_p)
    app.root.mainloop()
