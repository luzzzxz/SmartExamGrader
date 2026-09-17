# -*- coding: utf-8 -*-
"""
模板试卷红横线与答案校验工具 (独立启动器)
可直接双击运行，或在主程序【工具】菜单中调用。
"""

import os
import sys
from pathlib import Path

# 将项目目录与 app 加入 sys.path
root_dir = Path(__file__).resolve().parent
app_dir = root_dir / "app"
for p in [str(root_dir), str(app_dir)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from app.core.template_blank_checker import TemplateBlankCheckerWindow
except ImportError:
    try:
        from core.template_blank_checker import TemplateBlankCheckerWindow
    except ImportError:
        from template_blank_checker import TemplateBlankCheckerWindow

def main():
    initial_file = None
    if len(sys.argv) > 1:
        arg_path = sys.argv[1]
        if Path(arg_path).exists():
            initial_file = arg_path

    app = TemplateBlankCheckerWindow(master=None, initial_path=initial_file)
    app.root.mainloop()

if __name__ == '__main__':
    main()
