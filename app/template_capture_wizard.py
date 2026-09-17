#!/usr/bin/env python3
import json
import math
import shutil
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

from marker_utils import detect_corner_markers, save_marker_debug_image
from template_registry import ensure_template_registry, slugify, upsert_template

PROJECT_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PROJECT_DIR / 'templates'
PRESETS_DIR = PROJECT_DIR / 'template_presets'
MARKER_ORDER = ['top_left', 'top_right', 'bottom_right', 'bottom_left']
DIGIT_OPTIONS = [str(i) for i in range(10)]
DEFAULT_OPTION_KEYS = ['A', 'B', 'C', 'D']


class TemplateCaptureWizard:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title('模板录入向导')
        self.root.geometry('820x620')

        self.template_name_var = tk.StringVar()
        self.mode_var = tk.StringVar(value='collector')
        self.image_path_var = tk.StringVar()
        self.student_id_enabled_var = tk.BooleanVar(value=False)
        self.student_id_digits_var = tk.IntVar(value=4)
        self.zone_count_var = tk.IntVar(value=1)
        self.questions_per_zone_var = tk.IntVar(value=10)
        self.default_question_type_var = tk.StringVar(value='single')
        self.preset_var = tk.StringVar(value='')

        self.template_image_path: Path | None = None
        self.current_template_dir: Path | None = None
        self.marker_payload: dict | None = None
        self.marker_points: dict = {}
        self.answer_points: dict = {}
        self.student_points: dict = {}
        self.question_type_overrides: dict[int, str] = {}
        self.loaded_preset: dict | None = None
        self.layout_widgets = []
        self.preset_files = self.load_presets()

        self.create_widgets()

    def load_presets(self):
        PRESETS_DIR.mkdir(parents=True, exist_ok=True)
        files = {}
        for p in sorted(PRESETS_DIR.glob('*.json')):
            files[p.stem] = p
        return files

    def create_widgets(self):
        frm = ttk.Frame(self.root)
        frm.pack(fill='both', expand=True, padx=14, pady=12)

        preset_frame = ttk.LabelFrame(frm, text='0. 预设')
        preset_frame.pack(fill='x', pady=(0, 10))
        ttk.Label(preset_frame, text='模板预设').grid(row=0, column=0, sticky='w', padx=8, pady=8)
        preset_values = [''] + list(self.preset_files.keys())
        self.preset_box = ttk.Combobox(preset_frame, textvariable=self.preset_var, values=preset_values, state='readonly', width=32)
        self.preset_box.grid(row=0, column=1, sticky='w', padx=8, pady=8)
        ttk.Button(preset_frame, text='加载预设', command=self.apply_selected_preset).grid(row=0, column=2, sticky='w', padx=8, pady=8)
        ttk.Label(preset_frame, text='像 tc 这种复杂模板，建议先加载预设再录入。').grid(row=1, column=0, columnspan=3, sticky='w', padx=8, pady=(0, 8))

        basic = ttk.LabelFrame(frm, text='1. 模板基本信息')
        basic.pack(fill='x', pady=(0, 10))

        ttk.Label(basic, text='模板名称').grid(row=0, column=0, sticky='w', padx=8, pady=8)
        ttk.Entry(basic, textvariable=self.template_name_var, width=28).grid(row=0, column=1, sticky='w', padx=8, pady=8)

        ttk.Label(basic, text='模板用途').grid(row=1, column=0, sticky='w', padx=8, pady=8)
        mode_frame = ttk.Frame(basic)
        mode_frame.grid(row=1, column=1, sticky='w', padx=8, pady=8)
        ttk.Radiobutton(mode_frame, text='正常阅卷', variable=self.mode_var, value='grader').pack(side='left', padx=(0, 12))
        ttk.Radiobutton(mode_frame, text='错题采集', variable=self.mode_var, value='collector').pack(side='left')

        ttk.Label(basic, text='模板图片').grid(row=2, column=0, sticky='w', padx=8, pady=8)
        ttk.Entry(basic, textvariable=self.image_path_var, width=56).grid(row=2, column=1, sticky='w', padx=8, pady=8)
        ttk.Button(basic, text='选择图片', command=self.choose_image).grid(row=2, column=2, sticky='w', padx=8, pady=8)

        layout = ttk.LabelFrame(frm, text='2. 题位结构')
        layout.pack(fill='x', pady=(0, 10))
        self.layout_frame = layout

        ttk.Label(layout, text='答题区数量').grid(row=0, column=0, sticky='w', padx=8, pady=8)
        zone_spin = ttk.Spinbox(layout, from_=1, to=20, textvariable=self.zone_count_var, width=8)
        zone_spin.grid(row=0, column=1, sticky='w', padx=8, pady=8)

        ttk.Label(layout, text='每区题数').grid(row=0, column=2, sticky='w', padx=8, pady=8)
        question_spin = ttk.Spinbox(layout, from_=1, to=80, textvariable=self.questions_per_zone_var, width=8)
        question_spin.grid(row=0, column=3, sticky='w', padx=8, pady=8)

        ttk.Label(layout, text='默认题型').grid(row=1, column=0, sticky='w', padx=8, pady=8)
        default_type_box = ttk.Combobox(layout, textvariable=self.default_question_type_var, values=['single', 'multi'], state='readonly', width=10)
        default_type_box.grid(row=1, column=1, sticky='w', padx=8, pady=8)
        type_button = ttk.Button(layout, text='设置某些题为多选', command=self.configure_question_types)
        type_button.grid(row=1, column=2, columnspan=2, sticky='w', padx=8, pady=8)

        student_check = ttk.Checkbutton(layout, text='启用学号区', variable=self.student_id_enabled_var)
        student_check.grid(row=2, column=0, sticky='w', padx=8, pady=8)
        ttk.Label(layout, text='学号位数').grid(row=2, column=1, sticky='e', padx=8, pady=8)
        student_spin = ttk.Spinbox(layout, from_=1, to=12, textvariable=self.student_id_digits_var, width=8)
        student_spin.grid(row=2, column=2, sticky='w', padx=8, pady=8)

        self.layout_note_var = tk.StringVar(value='普通模板时可手动设置；用了预设后可忽略这里。')
        ttk.Label(layout, textvariable=self.layout_note_var, foreground='#666').grid(row=3, column=0, columnspan=4, sticky='w', padx=8, pady=(0, 8))
        self.layout_widgets = [zone_spin, question_spin, default_type_box, type_button, student_check, student_spin]

        actions = ttk.LabelFrame(frm, text='3. 执行')
        actions.pack(fill='x', pady=(0, 10))
        ttk.Button(actions, text='开始录入模板', command=self.run_capture).pack(side='left', padx=8, pady=10)
        ttk.Button(actions, text='关闭', command=self.root.destroy).pack(side='left', padx=8, pady=10)

        self.log_text = tk.Text(frm, height=16, wrap='word')
        self.log_text.pack(fill='both', expand=True)
        self.log('录入顺序：选模板图 → 自动识别四角定位块 → 手动点选题位 → 自动登记到模板库。')

    def log(self, text: str):
        self.log_text.insert('end', text + '\n')
        self.log_text.see('end')
        self.root.update_idletasks()

    def choose_image(self):
        path = filedialog.askopenfilename(
            title='选择模板图片',
            initialdir=str(PROJECT_DIR),
            filetypes=[('图片文件', '*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff'), ('所有文件', '*.*')],
        )
        if path:
            self.template_image_path = Path(path)
            self.image_path_var.set(str(self.template_image_path))
            if not self.template_name_var.get().strip():
                self.template_name_var.set(self.template_image_path.stem)

    def set_layout_controls_enabled(self, enabled: bool):
        state = 'normal' if enabled else 'disabled'
        readonly_state = 'readonly' if enabled else 'disabled'
        for widget in self.layout_widgets:
            try:
                if isinstance(widget, ttk.Combobox):
                    widget.configure(state=readonly_state)
                else:
                    widget.configure(state=state)
            except Exception:
                pass
        if hasattr(self, 'layout_note_var'):
            if enabled:
                self.layout_note_var.set('普通模板时可手动设置；用了预设后可忽略这里。')
            else:
                self.layout_note_var.set('当前已加载预设，题位结构已自动锁定，你不用改这里。')

    def apply_selected_preset(self):
        preset_name = self.preset_var.get().strip()
        if not preset_name:
            self.loaded_preset = None
            self.set_layout_controls_enabled(True)
            self.log('已清空预设，当前使用普通录入模式。')
            return
        preset_path = self.preset_files.get(preset_name)
        if not preset_path or not preset_path.exists():
            messagebox.showerror('错误', f'预设不存在：{preset_name}')
            return
        preset = json.loads(preset_path.read_text(encoding='utf-8'))
        self.loaded_preset = preset
        self.template_name_var.set(preset.get('display_name') or preset_name)
        self.mode_var.set(preset.get('mode', 'collector'))
        self.student_id_enabled_var.set(bool(preset.get('student_id_enabled', False)))
        self.student_id_digits_var.set(int(preset.get('student_id_digits', 4)))
        zone_count = len(preset.get('zones', [])) or 1
        self.zone_count_var.set(zone_count)
        first_zone_questions = len((preset.get('zones') or [{}])[0].get('questions', [])) or 1
        self.questions_per_zone_var.set(first_zone_questions)
        self.default_question_type_var.set(preset.get('default_question_type', 'single'))
        self.set_layout_controls_enabled(False)
        self.log(f'已加载预设：{preset_name}')

    def configure_question_types(self):
        total = int(self.questions_per_zone_var.get())
        default_type = self.default_question_type_var.get().strip() or 'single'
        current_multi = sorted([q for q, t in self.question_type_overrides.items() if t == 'multi'])
        initial = ','.join(map(str, current_multi)) if current_multi else ''
        answer = simpledialog.askstring(
            '题型设置',
            f'默认题型当前为 {default_type}。\n如有例外，请输入题号，逗号分隔，例如：11,12',
            initialvalue=initial,
            parent=self.root,
        )
        if answer is None:
            return
        overrides = {}
        nums = []
        for part in answer.replace('，', ',').split(','):
            part = part.strip()
            if not part:
                continue
            if not part.isdigit():
                messagebox.showerror('错误', f'题号格式不对：{part}')
                return
            qno = int(part)
            if qno < 1 or qno > total:
                messagebox.showerror('错误', f'题号超范围：{qno}')
                return
            nums.append(qno)
        for qno in range(1, total + 1):
            qtype = 'multi' if qno in nums else default_type
            if qtype != default_type:
                overrides[qno] = qtype
        self.question_type_overrides = overrides
        self.log(f'题型例外已设置：{sorted(nums) if nums else "无"}')

    def validate(self):
        name = self.template_name_var.get().strip()
        if not name:
            raise RuntimeError('请先填写模板名称。')
        if not self.template_image_path:
            path_text = self.image_path_var.get().strip()
            if path_text:
                self.template_image_path = Path(path_text)
        if not self.template_image_path or not self.template_image_path.exists():
            raise RuntimeError('请先选择有效的模板图片。')
        if self.loaded_preset:
            return
        if self.zone_count_var.get() <= 0 or self.questions_per_zone_var.get() <= 0:
            raise RuntimeError('答题区数量和每区题数都必须大于 0。')

    def question_type_for(self, qno: int) -> str:
        default_type = self.default_question_type_var.get().strip() or 'single'
        return self.question_type_overrides.get(qno, default_type)

    def build_zones(self):
        if self.loaded_preset and self.loaded_preset.get('zones'):
            return self.loaded_preset['zones']

        zone_count = int(self.zone_count_var.get())
        question_count = int(self.questions_per_zone_var.get())
        zones = []
        for zone_index in range(1, zone_count + 1):
            zone_name = f'zone{zone_index}'
            zone = {
                'zone_name': zone_name,
                'zone_label': f'答题区{zone_index}',
                'questions': [],
            }
            for qno in range(1, question_count + 1):
                zone['questions'].append({
                    'question_no_in_zone': qno,
                    'question_label': f'第{qno}题',
                    'question_type': self.question_type_for(qno),
                    'options': [{'key': key, 'label': key} for key in DEFAULT_OPTION_KEYS],
                })
            zones.append(zone)
        return zones

    def prepare_template_dir(self):
        self.validate()
        template_key = slugify(self.template_name_var.get())
        self.current_template_dir = TEMPLATES_DIR / template_key
        self.current_template_dir.mkdir(parents=True, exist_ok=True)
        ext = self.template_image_path.suffix.lower() or '.jpg'
        copied_template = self.current_template_dir / f'template{ext}'
        shutil.copy2(self.template_image_path, copied_template)
        self.template_image_path = copied_template
        return template_key

    def detect_markers(self):
        assert self.current_template_dir and self.template_image_path
        self.log('开始识别定位块...')
        payload = detect_corner_markers(self.template_image_path)
        debug_path = self.current_template_dir / 'template_marker_detection_debug.png'
        save_marker_debug_image(self.template_image_path, payload, debug_path)
        marker_json = self.current_template_dir / 'template_marker_detection.json'
        marker_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        self.marker_payload = payload
        self.marker_points = {k: {'x': v['center']['x'], 'y': v['center']['y']} for k, v in payload['markers'].items()}
        self.log(f'定位块识别完成：{marker_json.name}')

    def marker_diag(self) -> float:
        xs = [self.marker_points[name]['x'] for name in MARKER_ORDER]
        ys = [self.marker_points[name]['y'] for name in MARKER_ORDER]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        return math.hypot(width, height)

    def build_answer_tasks(self, zones):
        tasks = []
        for zone in zones:
            zone_name = zone['zone_name']
            zone_label = zone.get('zone_label') or zone_name
            for question in zone.get('questions', []):
                qno = question['question_no_in_zone']
                qlabel = question.get('question_label') or f'第{qno}题'
                for option in question.get('options', []):
                    tasks.append({
                        'zone_name': zone_name,
                        'zone_label': zone_label,
                        'question_no_in_zone': qno,
                        'question_label': qlabel,
                        'option_key': option['key'],
                        'option_label': option.get('label') or option['key'],
                    })
        return tasks

    def capture_answer_points(self):
        assert self.current_template_dir and self.template_image_path
        zones = self.build_zones()
        tasks = self.build_answer_tasks(zones)
        self.answer_points = self.open_capture_window('录入题位', tasks)
        diag = self.marker_diag()
        with Image.open(self.template_image_path) as img:
            output = {
                'template_image': {
                    'path': str(self.template_image_path),
                    'width': img.width,
                    'height': img.height,
                },
                'markers': self.marker_points,
                'marker_diag': round(diag, 4),
                'mode': self.mode_var.get().strip() or 'collector',
                'zones': [],
            }
            for zone in zones:
                zone_name = zone['zone_name']
                zone_data = {
                    'zone_name': zone_name,
                    'zone_label': zone.get('zone_label') or zone_name,
                    'questions': [],
                }
                for question in zone.get('questions', []):
                    qno = question['question_no_in_zone']
                    qkey = str(qno)
                    row = self.answer_points[zone_name][qkey]
                    q_data = {
                        'question_no_in_zone': qno,
                        'question_label': question.get('question_label') or f'第{qno}题',
                        'question_type': question.get('question_type', 'single'),
                        'options': {},
                    }
                    for option in question.get('options', []):
                        opt_key = option['key']
                        pt = row[opt_key]
                        signature = {}
                        for marker_name in MARKER_ORDER:
                            mx = self.marker_points[marker_name]['x']
                            my = self.marker_points[marker_name]['y']
                            dist = math.hypot(pt['x'] - mx, pt['y'] - my)
                            signature[marker_name] = round(dist / diag, 6)
                        q_data['options'][opt_key] = {
                            'label': option.get('label') or opt_key,
                            'template_pixel': pt,
                            'marker_signature': signature,
                        }
                    zone_data['questions'].append(q_data)
                output['zones'].append(zone_data)
        answer_path = self.current_template_dir / 'answer_zone_points.json'
        answer_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
        self.log(f'题位录入完成：{answer_path.name}')

    def capture_student_id_points(self):
        assert self.current_template_dir and self.template_image_path
        if not self.student_id_enabled_var.get():
            return None
        digit_count = int(self.student_id_digits_var.get())
        tasks = []
        for digit_pos in range(1, digit_count + 1):
            for option in DIGIT_OPTIONS:
                tasks.append({
                    'zone_name': 'student_id',
                    'zone_label': '学号区',
                    'question_no_in_zone': digit_pos,
                    'question_label': f'学号第{digit_pos}位',
                    'option_key': option,
                    'option_label': option,
                })
        self.student_points = self.open_capture_window('录入学号区', tasks)
        diag = self.marker_diag()
        with Image.open(self.template_image_path) as img:
            output = {
                'template_image': {
                    'path': str(self.template_image_path),
                    'width': img.width,
                    'height': img.height,
                },
                'markers': self.marker_points,
                'marker_diag': round(diag, 4),
                'zones': [
                    {
                        'zone_name': 'student_id',
                        'zone_label': '学号区',
                        'questions': [],
                    }
                ],
            }
            zone = output['zones'][0]
            for digit_pos in range(1, digit_count + 1):
                row = self.student_points['student_id'][str(digit_pos)]
                q_data = {
                    'question_no_in_zone': digit_pos,
                    'question_label': f'学号第{digit_pos}位',
                    'question_type': 'single',
                    'options': {},
                }
                for option in DIGIT_OPTIONS:
                    pt = row[option]
                    signature = {}
                    for marker_name in MARKER_ORDER:
                        mx = self.marker_points[marker_name]['x']
                        my = self.marker_points[marker_name]['y']
                        dist = math.hypot(pt['x'] - mx, pt['y'] - my)
                        signature[marker_name] = round(dist / diag, 6)
                    q_data['options'][option] = {
                        'label': option,
                        'template_pixel': pt,
                        'marker_signature': signature,
                    }
                zone['questions'].append(q_data)
        student_path = self.current_template_dir / 'student_id_points.json'
        student_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
        self.log(f'学号区录入完成：{student_path.name}')
        return student_path

    def open_capture_window(self, title: str, tasks: list[dict]):
        result = {}
        done = {'ok': False}
        image = Image.open(self.template_image_path).convert('RGB')
        img_w, img_h = image.size

        top = tk.Toplevel(self.root)
        top.title(title)
        top.geometry('1600x1020')
        top.minsize(1200, 800)
        top.grab_set()

        info_var = tk.StringVar()
        coord_var = tk.StringVar(value='坐标：-')
        index = {'value': 0}

        tk.Label(top, textvariable=info_var, font=('Microsoft YaHei', 11, 'bold')).pack(anchor='w', padx=10, pady=(10, 4))
        tk.Label(top, text='按提示依次点击。当前为原图显示，可用滚动条慢慢移动；BackSpace 撤销，S 保存，Esc 关闭。').pack(anchor='w', padx=10)
        tk.Label(top, textvariable=coord_var, fg='gray').pack(anchor='w', padx=10, pady=(0, 6))

        canvas_frame = tk.Frame(top)
        canvas_frame.pack(fill='both', expand=True, padx=10, pady=8)

        v_scroll = tk.Scrollbar(canvas_frame, orient='vertical')
        h_scroll = tk.Scrollbar(canvas_frame, orient='horizontal')

        canvas = tk.Canvas(
            canvas_frame,
            width=min(img_w, 1450),
            height=min(img_h, 820),
            bg='white',
            cursor='cross',
            xscrollcommand=h_scroll.set,
            yscrollcommand=v_scroll.set,
            scrollregion=(0, 0, img_w, img_h),
        )

        v_scroll.config(command=canvas.yview)
        h_scroll.config(command=canvas.xview)
        v_scroll.pack(side='right', fill='y')
        h_scroll.pack(side='bottom', fill='x')
        canvas.pack(side='left', fill='both', expand=True)

        tk_image = ImageTk.PhotoImage(image)
        canvas.create_image(0, 0, anchor='nw', image=tk_image)
        canvas.image = tk_image

        def current_task():
            return tasks[index['value']] if index['value'] < len(tasks) else None

        def redraw():
            canvas.delete('point')
            r = 6
            for marker_name, pt in self.marker_points.items():
                x = pt['x']
                y = pt['y']
                canvas.create_oval(x-r, y-r, x+r, y+r, outline='blue', width=2, tags='point')
                canvas.create_text(x+34, y, text=marker_name, fill='blue', tags='point')
            for zone_name, question_map in result.items():
                for qkey, option_map in question_map.items():
                    for option_key, pt in option_map.items():
                        x = pt['x']
                        y = pt['y']
                        canvas.create_oval(x-r, y-r, x+r, y+r, outline='red', width=2, tags='point')
                        canvas.create_text(x+30, y, text=f'{zone_name}-{qkey}-{option_key}', fill='red', tags='point')
            task = current_task()
            if task is None:
                info_var.set('全部采点完成，点保存即可。')
            else:
                info_var.set(
                    f"[{index['value'] + 1}/{len(tasks)}] 请点击：{task['zone_label']} / {task['question_label']} / {task['option_label']}"
                )

        def on_move(event):
            x = canvas.canvasx(event.x)
            y = canvas.canvasy(event.y)
            coord_var.set(f'坐标：({x:.1f}, {y:.1f})')

        def on_click(event):
            task = current_task()
            if task is None:
                return
            x = round(canvas.canvasx(event.x), 2)
            y = round(canvas.canvasy(event.y), 2)
            zone = result.setdefault(task['zone_name'], {})
            row = zone.setdefault(str(task['question_no_in_zone']), {})
            row[task['option_key']] = {'x': x, 'y': y}
            index['value'] += 1
            redraw()

        def undo(*_):
            if index['value'] <= 0:
                return
            index['value'] -= 1
            task = tasks[index['value']]
            row = result.get(task['zone_name'], {}).get(str(task['question_no_in_zone']), {})
            row.pop(task['option_key'], None)
            redraw()

        def save(*_):
            if index['value'] < len(tasks):
                messagebox.showerror('错误', '还有点位未采完。', parent=top)
                return
            done['ok'] = True
            top.destroy()

        btn = ttk.Frame(top)
        btn.pack(fill='x', padx=10, pady=(0, 10))
        ttk.Button(btn, text='撤销', command=undo).pack(side='left', padx=4)
        ttk.Button(btn, text='保存', command=save).pack(side='left', padx=4)
        ttk.Button(btn, text='关闭', command=top.destroy).pack(side='left', padx=4)

        canvas.bind('<Motion>', on_move)
        canvas.bind('<Button-1>', on_click)
        top.bind('<BackSpace>', undo)
        top.bind('s', save)
        top.bind('S', save)
        top.bind('<Escape>', lambda e: top.destroy())
        redraw()
        self.root.wait_window(top)

        if not done['ok']:
            raise RuntimeError(f'{title}未完成。')
        return result

    def register_template(self, template_key: str):
        assert self.current_template_dir
        entry = {
            'display_name': self.template_name_var.get().strip(),
            'mode': self.mode_var.get().strip() or 'collector',
            'template_image': str((self.current_template_dir / self.template_image_path.name).relative_to(PROJECT_DIR)),
            'marker_config': str((self.current_template_dir / 'template_marker_detection.json').relative_to(PROJECT_DIR)),
            'answer_config': str((self.current_template_dir / 'answer_zone_points.json').relative_to(PROJECT_DIR)),
            'student_id_config': str((self.current_template_dir / 'student_id_points.json').relative_to(PROJECT_DIR)) if self.student_id_enabled_var.get() else '',
        }
        upsert_template(PROJECT_DIR, template_key, entry, make_current=True)
        self.log('模板已写入 template_registry.json，并设为当前模板。')

    def run_capture(self):
        try:
            template_key = self.prepare_template_dir()
            ensure_template_registry(PROJECT_DIR)
            self.detect_markers()
            self.capture_answer_points()
            self.capture_student_id_points()
            self.register_template(template_key)
        except Exception as e:
            messagebox.showerror('录入失败', str(e), parent=self.root)
            self.log(f'录入失败：{e}')
            return
        messagebox.showinfo('成功', f'模板录入完成：{self.template_name_var.get().strip()}', parent=self.root)
        self.log('录入完成。')
        self.root.destroy()


def main():
    root = tk.Tk()
    app = TemplateCaptureWizard(root)
    root.mainloop()


if __name__ == '__main__':
    main()
