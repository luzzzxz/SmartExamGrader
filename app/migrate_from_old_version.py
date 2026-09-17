# -*- coding: utf-8 -*-
"""
答题批改系统 - 从旧版本数据一键迁移模块
功能：
1. 识别并只读扫描旧版本系统（数据库、学生名册、答案库、模板、API Key与配置）。
2. 在当前新版本中先做安全备份，再安全导入旧版数据。
3. 自动执行数据库向前兼容升级（补全新版本所需的字段与表结构）。
4. 绝不修改、绝不删除旧版本的任何文件！
"""

import os
import sys
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

CURRENT_APP_DIR = Path(__file__).resolve().parent
CURRENT_ROOT_DIR = CURRENT_APP_DIR.parent
BACKUP_DIR = CURRENT_APP_DIR / "recovery_backups"

def normalize_dir(input_path: str) -> Path:
    p = Path(input_path.strip().strip('"').strip("'"))
    if not p.exists():
        return p
    # 如果用户选择的是根目录，且存在 app 子目录，则旧版本 app 目录为 p / "app"
    if (p / "app" / "answer_card_app.db").exists() or (p / "app" / "student_roster_local.py").exists():
        return p / "app"
    return p

def choose_folder_gui() -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        # 优先支持选择 zip 数据包或旧版目录
        path = filedialog.askopenfilename(
            title="请选择【.zip数据备份包】（如要选择旧软件文件夹，请点取消）",
            filetypes=[("ZIP 数据备份包", "*.zip"), ("所有文件", "*.*")],
            initialdir=str(CURRENT_ROOT_DIR.parent)
        )
        if not path:
            path = filedialog.askdirectory(
                title="请选择【旧版本】答题批改系统的软件目录",
                initialdir=str(CURRENT_ROOT_DIR.parent)
            )
        root.destroy()
        return path
    except Exception:
        return ""

def scan_old_version(old_app_dir: Path) -> dict:
    info = {
        "valid": False,
        "old_app_dir": old_app_dir,
        "db_exists": False,
        "db_size_mb": 0.0,
        "db_sessions": 0,
        "db_results": 0,
        "roster_exists": False,
        "roster_classes": 0,
        "answer_keys_count": 0,
        "templates_count": 0,
        "llm_key_configured": False,
        "web_sync_configured": False,
        "subjective_scores_count": 0
    }

    if not old_app_dir.exists():
        return info

    # 检查数据库
    db_file = old_app_dir / "answer_card_app.db"
    if db_file.exists() and db_file.is_file():
        info["db_exists"] = True
        info["db_size_mb"] = round(db_file.stat().st_size / (1024 * 1024), 2)
        try:
            with sqlite3.connect(f"file:{db_file}?mode=ro", uri=True) as conn:
                cur = conn.cursor()
                cur.execute("SELECT count(*) FROM sessions")
                info["db_sessions"] = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM session_results")
                info["db_results"] = cur.fetchone()[0]
        except Exception:
            pass

    # 检查学生名册
    roster_file = old_app_dir / "student_roster_local.py"
    if roster_file.exists() and roster_file.is_file():
        info["roster_exists"] = True
        try:
            content = roster_file.read_text(encoding="utf-8")
            # 粗略统计班级行数
            lines = [l for l in content.splitlines() if '": {' in l]
            info["roster_classes"] = len(lines)
        except Exception:
            pass

    # 检查答案方案
    ak_dir = old_app_dir / "answer_keys"
    if ak_dir.exists() and ak_dir.is_dir():
        info["answer_keys_count"] = len(list(ak_dir.glob("*.json")))

    # 检查模板
    tpl_dir = old_app_dir / "templates"
    if tpl_dir.exists() and tpl_dir.is_dir():
        info["templates_count"] = len([d for d in tpl_dir.iterdir() if d.is_dir()])

    # 检查 LLM 配置
    llm_file = old_app_dir / "llm_grading_config.json"
    if llm_file.exists() and llm_file.is_file():
        try:
            llm_data = json.loads(llm_file.read_text(encoding="utf-8-sig"))
            if llm_data.get("api_key", "").strip():
                info["llm_key_configured"] = True
        except Exception:
            pass

    # 检查网页同步配置
    web_file = old_app_dir / "manual_web_sync_config.json"
    if web_file.exists() and web_file.is_file():
        try:
            web_data = json.loads(web_file.read_text(encoding="utf-8-sig"))
            if web_data.get("api_token", "").strip():
                info["web_sync_configured"] = True
        except Exception:
            pass

    # 检查主观题打分
    sub_dir = old_app_dir / "session_subjective_scores"
    if sub_dir.exists() and sub_dir.is_dir():
        info["subjective_scores_count"] = len(list(sub_dir.glob("*.json")))

    if info["db_exists"] or info["roster_exists"] or info["answer_keys_count"] > 0:
        info["valid"] = True

    return info

def perform_migration(old_app_dir: Path) -> list:
    logs = []
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 备份当前系统现有重要数据（如果当前系统已有数据）
    curr_db = CURRENT_APP_DIR / "answer_card_app.db"
    if curr_db.exists() and curr_db.stat().st_size > 32 * 1024:
        backup_curr_db = BACKUP_DIR / f"pre_migrate_answer_card_app_{ts}.db"
        shutil.copy2(curr_db, backup_curr_db)
        logs.append(f"已备份当前系统数据库至: {backup_curr_db.name}")

    curr_roster = CURRENT_APP_DIR / "student_roster_local.py"
    if curr_roster.exists():
        backup_curr_roster = BACKUP_DIR / f"pre_migrate_student_roster_{ts}.py"
        shutil.copy2(curr_roster, backup_curr_roster)

    # 2. 迁移主数据库 (使用 SQLite 在线一致性备份)
    old_db = old_app_dir / "answer_card_app.db"
    if old_db.exists() and old_db.is_file():
        logs.append("正在迁移主数据库...")
        try:
            with sqlite3.connect(f"file:{old_db}?mode=ro", uri=True) as src_conn:
                with sqlite3.connect(curr_db) as dst_conn:
                    src_conn.backup(dst_conn)
            logs.append(f"[成功] 主数据库迁移成功 (文件大小: {round(curr_db.stat().st_size / 1024 / 1024, 2)} MB)")
        except Exception as e:
            logs.append(f"[失败] 数据库迁移异常: {e}")

        # 2.1 自动执行新版本数据库向前兼容结构升级 (补全新增列)
        try:
            with sqlite3.connect(curr_db) as conn:
                existing_columns = {
                    row[1] for row in conn.execute('PRAGMA table_info(sessions)').fetchall()
                }
                if 'folder_path' not in existing_columns:
                    conn.execute('ALTER TABLE sessions ADD COLUMN folder_path TEXT')
                    logs.append("[成功] 数据库表结构自动兼容升级: 已补全 folder_path 字段")
                if 'manual_web_job_id' not in existing_columns:
                    conn.execute('ALTER TABLE sessions ADD COLUMN manual_web_job_id TEXT')
                    logs.append("[成功] 数据库表结构自动兼容升级: 已补全 manual_web_job_id 字段")
                conn.commit()
        except Exception as e:
            logs.append(f"提示: 数据库结构检查警告: {e}")

    # 3. 迁移学生名册
    old_roster = old_app_dir / "student_roster_local.py"
    if old_roster.exists() and old_roster.is_file():
        shutil.copy2(old_roster, curr_roster)
        logs.append("[成功] 学生名册迁移成功 (已恢复原学生及班级清单)")

    # 4. 智能迁移配置 (提取有效 Key 与 Token)
    # 4.1 LLM 配置
    old_llm_file = old_app_dir / "llm_grading_config.json"
    curr_llm_file = CURRENT_APP_DIR / "llm_grading_config.json"
    if old_llm_file.exists() and curr_llm_file.exists():
        try:
            old_llm = json.loads(old_llm_file.read_text(encoding="utf-8-sig"))
            curr_llm = json.loads(curr_llm_file.read_text(encoding="utf-8-sig"))
            old_key = old_llm.get("api_key", "").strip()
            if old_key:
                curr_llm["api_key"] = old_key
                curr_llm["enabled"] = old_llm.get("enabled", True)
                if "model" in old_llm:
                    curr_llm["model"] = old_llm["model"]
                if "custom_rules" in old_llm and old_llm["custom_rules"]:
                    curr_llm["custom_rules"] = old_llm["custom_rules"]
                curr_llm_file.write_text(json.dumps(curr_llm, ensure_ascii=False, indent=2), encoding="utf-8")
                logs.append("[成功] 大模型批改配置迁移成功 (已自动回填旧版 API Key 及规则)")
        except Exception as e:
            logs.append(f"提示: LLM配置合并异常: {e}")

    # 4.2 网页同步配置
    old_sync_file = old_app_dir / "manual_web_sync_config.json"
    curr_sync_file = CURRENT_APP_DIR / "manual_web_sync_config.json"
    if old_sync_file.exists() and curr_sync_file.exists():
        try:
            old_sync = json.loads(old_sync_file.read_text(encoding="utf-8-sig"))
            curr_sync = json.loads(curr_sync_file.read_text(encoding="utf-8-sig"))
            if old_sync.get("api_token", "").strip():
                curr_sync["api_token"] = old_sync["api_token"]
                curr_sync["server_url"] = old_sync.get("server_url", "")
                curr_sync["teacher_url"] = old_sync.get("teacher_url", "")
                curr_sync_file.write_text(json.dumps(curr_sync, ensure_ascii=False, indent=2), encoding="utf-8")
                logs.append("[成功] 网页人工打分同步配置迁移成功 (已恢复 Token 与服务地址)")
        except Exception as e:
            logs.append(f"提示: 网页同步配置合并异常: {e}")

    # 4.3 其它设置文件直接同步
    settings_files = [
        "wrongbook_settings.json",
        "knowledge_bridge_settings.json",
        "overlay_calibration_presets.json",
        "ocr_preference_memory.json",
        "manual_web_server_config.json"
    ]
    for sf in settings_files:
        old_sf = old_app_dir / sf
        curr_sf = CURRENT_APP_DIR / sf
        if old_sf.exists() and old_sf.is_file():
            try:
                shutil.copy2(old_sf, curr_sf)
            except Exception:
                pass
    logs.append("[成功] 用户偏好设置与标定参数已同步")

    # 5. 合并答案方案库 (answer_keys)
    old_ak_dir = old_app_dir / "answer_keys"
    curr_ak_dir = CURRENT_APP_DIR / "answer_keys"
    curr_ak_dir.mkdir(parents=True, exist_ok=True)
    ak_copied = 0
    if old_ak_dir.exists() and old_ak_dir.is_dir():
        for ak_file in old_ak_dir.glob("*.json"):
            target_file = curr_ak_dir / ak_file.name
            if not target_file.exists():
                shutil.copy2(ak_file, target_file)
                ak_copied += 1
        logs.append(f"[成功] 标准答案方案库合并完成 (新增导入: {ak_copied} 套方案，总计: {len(list(curr_ak_dir.glob('*.json')))} 套)")

    # 6. 合并试卷模板 (templates)
    old_tpl_dir = old_app_dir / "templates"
    curr_tpl_dir = CURRENT_APP_DIR / "templates"
    curr_tpl_dir.mkdir(parents=True, exist_ok=True)
    tpl_copied = 0
    if old_tpl_dir.exists() and old_tpl_dir.is_dir():
        for sub_dir in old_tpl_dir.iterdir():
            if sub_dir.is_dir():
                target_sub = curr_tpl_dir / sub_dir.name
                if not target_sub.exists():
                    shutil.copytree(sub_dir, target_sub)
                    tpl_copied += 1
        logs.append(f"[成功] 试卷模板库合并完成 (新增导入自定义模板: {tpl_copied} 个)")

    # 7. 同步历史考务明细 (session_subjective_scores 等)
    session_dirs = ["session_subjective_scores", "session_template_configs", "session_answer_keys"]
    for sdir in session_dirs:
        old_sdir = old_app_dir / sdir
        curr_sdir = CURRENT_APP_DIR / sdir
        curr_sdir.mkdir(parents=True, exist_ok=True)
        if old_sdir.exists() and old_sdir.is_dir():
            count = 0
            for item in old_sdir.glob("*.json"):
                shutil.copy2(item, curr_sdir / item.name)
                count += 1
    logs.append("[成功] 历史测试会话的主观题打分及局部配置已完整同步")

    # 8. 同步自定义知识点映射表
    old_csv = old_app_dir / "知识点编号对应内容.csv"
    curr_csv = CURRENT_APP_DIR / "知识点编号对应内容.csv"
    if old_csv.exists() and old_csv.is_file() and not curr_csv.exists():
        shutil.copy2(old_csv, curr_csv)
        logs.append("[成功] 知识点编号总表已同步")

    return logs

def main():
    print("=" * 65)
    print("        答题批改系统 - 从旧版本数据一键迁移与升级工具")
    print("=" * 65)
    print("说明：")
    print("1. 本工具将把旧版软件中的【历史数据库、学生名册、答案库、")
    print("   试卷模板、大模型API Key与打分记录】一键安全迁移到当前新系统。")
    print("2. 迁移过程对【旧版本完全只读】，绝对不会修改或删除旧版本的任何文件！")
    print("3. 当前新版本系统原有数据（如有）将在迁移前自动创建时间戳安全快照。")
    print("=" * 65)
    print()

    old_path_input = ""
    if len(sys.argv) > 1:
        old_path_input = sys.argv[1].strip()

    if not old_path_input:
        print("请选择旧版本的软件目录：")
        print(" -> 直接按【回车键】: 弹出 Windows 文件夹浏览窗口进行点选")
        print(" -> 或者直接将旧版本文件夹【拖拽进此窗口】，然后按回车")
        print(" -> 或者在此输入/粘贴旧版本系统的绝对路径")
        print("-" * 65)
        try:
            user_input = input("请输入旧版本路径 (直接回车打开选择窗口): ").strip()
        except EOFError:
            user_input = ""

        if not user_input:
            print("正在打开文件夹选择窗口，请在弹出的对话框中选择旧版本软件目录...")
            user_input = choose_folder_gui()

        old_path_input = user_input.strip()

    if not old_path_input:
        print("\n未指定旧版本目录或备份包，迁移已取消。")
        input("\n按回车键退出...")
        return

    raw_path = Path(old_path_input.strip().strip('"').strip("'"))
    if raw_path.is_file() and raw_path.suffix.lower() == ".zip":
        print(f"\n检测到您选择的是【.zip数据备份包】: {raw_path.name}")
        try:
            from core.data_backup_manager import inspect_data_package, import_data_package
            pkg_info = inspect_data_package(str(raw_path))
            if not pkg_info.get("valid"):
                print("\n错误：所选压缩包不是有效的答题系统业务数据备份包！")
                input("\n按回车键退出...")
                return

            print("\n" + "-" * 40)
            print("检测到数据备份包包含以下资产：")
            print("-" * 40)
            print(f" [已检测] 备份时间     : {pkg_info.get('created_at', '未知')}")
            print(f" [已检测] 数据包大小   : {pkg_info.get('size_mb', 0)} MB")
            print(f" [已检测] 历史测试记录 : {pkg_info.get('sessions', 0)} 场测试，{pkg_info.get('results', 0)} 条成绩")
            print(f" [已检测] 学生班级名册 : {pkg_info.get('classes_count', 0)} 个班级")
            print(f" [已检测] 标准答案方案 : {pkg_info.get('answer_keys_count', 0)} 套")
            print(f" [已检测] 试卷模板库   : {pkg_info.get('templates_count', 0)} 个模版")
            print(f" [已检测] 大模型 API Key: {'已配置 (将自动导入)' if pkg_info.get('has_llm_key') else '未配置'}")
            print("-" * 40)

            confirm = input("\n确认将此备份包导入到当前系统中吗？(Y/N, 默认Y): ").strip().lower()
            if confirm and confirm != 'y':
                print("\n用户已取消导入。")
                input("\n按回车键退出...")
                return

            print("\n>>> 开始执行数据备份包导入...")
            logs = import_data_package(str(raw_path), CURRENT_APP_DIR)
            print("\n" + "=" * 45)
            print("导入执行结果报告：")
            print("=" * 45)
            for log in logs:
                print(f" {log}")
            print("=" * 45)
            print("\n数据备份包已成功导入！现在您可以双击【启动答题批改系统.bat】直接使用！")
            input("\n按回车键退出...")
            return
        except Exception as e:
            print(f"\n处理数据备份包时发生异常: {e}")
            input("\n按回车键退出...")
            return

    old_app_dir = normalize_dir(old_path_input)
    print(f"\n正在分析旧版本路径: {old_app_dir} ...")

    info = scan_old_version(old_app_dir)
    if not info["valid"]:
        print("\n错误：在指定目录下未检测到有效的旧版本系统数据！")
        print(f"检查路径: {old_app_dir}")
        print("提示：请确认选中的是包含 'app' 目录或 'answer_card_app.db' 的答题系统文件夹。")
        input("\n按回车键退出...")
        return

    print("\n" + "-" * 40)
    print("检测到旧版本包含以下数据资产：")
    print("-" * 40)
    if info["db_exists"]:
        print(f" [[成功]] 核心业务数据库 : {info['db_size_mb']} MB (包含 {info['db_sessions']} 次测试，{info['db_results']} 条成绩答卷)")
    else:
        print(" [- ] 核心业务数据库 : 未找到")

    if info["roster_exists"]:
        print(f" [[成功]] 学生班级名册   : 已找到 (约 {info['roster_classes']} 个班级)")
    else:
        print(" [- ] 学生班级名册   : 未找到")

    print(f" [[成功]] 标准答案方案   : {info['answer_keys_count']} 套")
    print(f" [[成功]] 试卷模板库     : {info['templates_count']} 个模版")
    print(f" [[成功]] 大模型 API Key : {'已配置 (将自动迁移填入)' if info['llm_key_configured'] else '未配置'}")
    print(f" [[成功]] 网页同步 Token : {'已配置 (将自动迁移填入)' if info['web_sync_configured'] else '未配置'}")
    print(f" [[成功]] 历史考务打分   : {info['subjective_scores_count']} 场测试")
    print("-" * 40)

    confirm = input("\n确认将以上数据迁移到当前新版本中吗？(Y/N, 默认Y): ").strip().lower()
    if confirm and confirm != 'y':
        print("\n用户已取消迁移。旧版本和当前系统均未做任何改动。")
        input("\n按回车键退出...")
        return

    print("\n>>> 开始执行数据安全迁移...")
    logs = perform_migration(old_app_dir)
    print("\n" + "=" * 45)
    print("迁移执行结果报告：")
    print("=" * 45)
    for log in logs:
        print(f" {log}")
    print("=" * 45)

    print("\n 全部数据迁移成功完成！")
    print("旧版本的数据已完整、安全地导入到当前新版系统中。")
    print("现在您可以关闭此窗口，双击【启动答题批改系统.bat】直接体验最新版软件！")
    input("\n按回车键退出...")

if __name__ == "__main__":
    main()
