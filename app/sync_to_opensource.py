# -*- coding: utf-8 -*-
"""
SmartExamGrader 开源仓库同步与脱敏更新工具
用于将当前开发目录中的最新代码安全同步至 SmartExamGrader 开源仓库
"""

import os
import shutil
import re

SOURCE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
TARGET_REPO = r"d:\作业试卷批改系统\SmartExamGrader"

# 绝对排除的文件与目录（无论如何不复制）
EXCLUDE_DIRS = {
    '__pycache__', '.git', '.vscode', '.idea', 'runtime', 'pydeps314',
    'scratch', 'temp', 'tmp', 'exports', 'reports', 'output', 'wrong_book_output',
    '.web_cache', 'blank_recheck_dataset', 'recovery_backups', 'ocr_temp', '_scheme_assets'
}

EXCLUDE_FILE_PATTERNS = [
    re.compile(r'\.bak([._].*)?$', re.I),
    re.compile(r'\.orig$', re.I),
    re.compile(r'\.tmp$', re.I),
    re.compile(r'\.lock$', re.I),
    re.compile(r'\.deleted', re.I),
    re.compile(r'\.disabled', re.I),
    re.compile(r'\.db(-journal|-wal|-shm)?$', re.I),
    re.compile(r'\.zip$', re.I),
    re.compile(r'\.rar$', re.I),
    re.compile(r'\.7z$', re.I),
]

# 特殊脱敏保护文件（只保留仓库中的模板或执行动态替换）
SPECIAL_SENSITIVE_FILES = {
    'student_roster_local.py',
    'llm_grading_config.json',
    'manual_web_sync_config.json',
    'knowledge_bridge_settings.json',
    'answer_card_app.db',
}

REPLACE_PATTERNS = [
    (re.compile(r'https?://gj\.741212\.xyz', re.I), 'https://your-manual-grading-server.example.com'),
    (re.compile(r'https?://tk\.741212\.xyz', re.I), 'https://your-question-bank-server.example.com'),
    (re.compile(r'146\.56\.141\.254'), '192.0.2.1'),
    (re.compile(r'741212\.xyz', re.I), 'your-domain.com'),
]

def is_excluded_file(filename):
    for pat in EXCLUDE_FILE_PATTERNS:
        if pat.search(filename):
            return True
    return False

def copy_and_sanitize(src, dst):
    ext = os.path.splitext(src)[1].lower()
    text_exts = {'.py', '.json', '.md', '.bat', '.sh', '.txt', '.csv', '.html', '.js', '.css'}
    
    if ext in text_exts:
        try:
            with open(src, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            # 替换敏感私有信息
            for pat, repl in REPLACE_PATTERNS:
                content = pat.sub(repl, content)
            
            # 如果目标文件已存在且内容一致则不写
            if os.path.exists(dst):
                with open(dst, 'r', encoding='utf-8', errors='ignore') as f_dst:
                    if f_dst.read() == content:
                        return False
            
            with open(dst, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        except Exception:
            pass

    # 二进制直接复制
    if not os.path.exists(dst) or open(src, 'rb').read() != open(dst, 'rb').read():
        shutil.copy2(src, dst)
        return True
    return False

def sync():
    if not os.path.exists(TARGET_REPO):
        raise FileNotFoundError(f"目标开源仓库不存在: {TARGET_REPO}")

    changed_files = []
    
    # 1. 同步根目录关键文件
    root_items = [
        'answer_card_stable_bootstrap.py',
        'answer_card_cv_helper.py',
        '启动答题批改系统.bat',
        '从旧版本迁移数据.bat',
        '安装OCR环境.bat',
        '模板试卷红横线检测工具_GUI.py',
        '选择题套模板工具_GUI_不处理答案.py',
        '直批模板.dotx',
        '软件更新日志.md',
        'CHANGELOG.md',
        '另一台电脑安装说明.md',
        '请先阅读_备份包说明.md',
    ]

    for item in root_items:
        src = os.path.join(SOURCE_ROOT, item)
        dst = os.path.join(TARGET_REPO, item)
        if os.path.exists(src) and os.path.isfile(src):
            if copy_and_sanitize(src, dst):
                changed_files.append(item)

    # 2. 同步 app 目录
    app_src_dir = os.path.join(SOURCE_ROOT, 'app')
    app_dst_dir = os.path.join(TARGET_REPO, 'app')

    for root, dirs, files in os.walk(app_src_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith('session_') and d != 'incoming']
        rel_dir = os.path.relpath(root, app_src_dir)
        target_dir = app_dst_dir if rel_dir == '.' else os.path.join(app_dst_dir, rel_dir)
        os.makedirs(target_dir, exist_ok=True)

        for fn in files:
            if is_excluded_file(fn):
                continue
            if fn in SPECIAL_SENSITIVE_FILES:
                # 严格保留目标仓库已有脱敏版本，不覆盖生产私有文件
                continue
            
            src_file = os.path.join(root, fn)
            dst_file = os.path.join(target_dir, fn)

            if copy_and_sanitize(src_file, dst_file):
                changed_files.append(os.path.relpath(src_file, SOURCE_ROOT))

    # 3. 终审安全扫描
    patterns = [
        (re.compile(r'sk-(?:ws-)?[a-zA-Z0-9]{20,}'), 'API_KEY'),
        (re.compile(r'146\.56\.141\.254'), 'SERVER_IP'),
        (re.compile(r'741212\.xyz'), 'SERVER_DOMAIN'),
    ]
    for root, dirs, files in os.walk(TARGET_REPO):
        if '.git' in dirs:
            dirs.remove('.git')
        for fn in files:
            if fn.endswith(('.py', '.json', '.bat', '.sh', '.md', '.txt')):
                fp = os.path.join(root, fn)
                with open(fp, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    for p, label in patterns:
                        if p.search(content):
                            raise ValueError(f"安全扫描失败！在 {os.path.relpath(fp, TARGET_REPO)} 检测到敏感信息: {label}")

    return changed_files

if __name__ == '__main__':
    res = sync()
    if res:
        print(f"同步成功！共更新 {len(res)} 个文件：")
        for f in res[:10]:
            print("  +", f)
        if len(res) > 10:
            print(f"  ... 以及其他 {len(res) - 10} 个文件")
    else:
        print("所有文件均已是最新状态，无需同步。")
