# -*- coding: utf-8 -*-
"""
答题批改系统 - 全量业务数据备份包导出与导入管理模块
功能：
1. 导出轻量业务数据包 (*.zip)：含主数据库一致性快照、学生名册、答案方案、试卷模板、大模型与服务凭证、会话打分明细。
2. 预览与校验业务数据包 (*.zip)。
3. 导入业务数据包：自动创建本地回滚快照、数据库向前结构兼容升级、智能回填凭证并合并模板答案。
"""

import os
import sys
import json
import shutil
import sqlite3
import zipfile
import tempfile
from datetime import datetime
from pathlib import Path

BACKUP_MANIFEST_NAME = "manifest.json"

def get_db_stats(db_path: Path) -> dict:
    stats = {"exists": False, "size_mb": 0.0, "sessions": 0, "results": 0}
    if db_path.exists() and db_path.is_file():
        stats["exists"] = True
        stats["size_mb"] = round(db_path.stat().st_size / (1024 * 1024), 2)
        conn = None
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            stats["sessions"] = conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
            stats["results"] = conn.execute("SELECT count(*) FROM session_results").fetchone()[0]
        except Exception:
            pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
    return stats

def export_data_package(target_zip_path: str, app_dir: Path) -> dict:
    """
    将 app_dir 中的业务数据导出为 zip 压缩包
    """
    target_path = Path(target_zip_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        "format": "answer_card_data_package",
        "version": "1.0",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "db_stats": {},
        "classes_count": 0,
        "answer_keys_count": 0,
        "templates_count": 0,
        "session_scores_count": 0,
        "has_llm_key": False
    }

    db_path = app_dir / "answer_card_app.db"
    manifest["db_stats"] = get_db_stats(db_path)

    roster_path = app_dir / "student_roster_local.py"
    if roster_path.exists():
        try:
            content = roster_path.read_text(encoding="utf-8")
            manifest["classes_count"] = len([l for l in content.splitlines() if '": {' in l])
        except Exception:
            pass

    ak_dir = app_dir / "answer_keys"
    if ak_dir.exists():
        manifest["answer_keys_count"] = len(list(ak_dir.glob("*.json")))

    tpl_dir = app_dir / "templates"
    if tpl_dir.exists():
        manifest["templates_count"] = len([d for d in tpl_dir.iterdir() if d.is_dir()])

    sub_dir = app_dir / "session_subjective_scores"
    if sub_dir.exists():
        manifest["session_scores_count"] = len(list(sub_dir.glob("*.json")))

    llm_path = app_dir / "llm_grading_config.json"
    if llm_path.exists():
        try:
            llm_cfg = json.loads(llm_path.read_text(encoding="utf-8-sig"))
            if llm_cfg.get("api_key", "").strip():
                manifest["has_llm_key"] = True
        except Exception:
            pass

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir) / "pkg"
        temp_root.mkdir(parents=True, exist_ok=True)

        # 1. 写入 manifest.json
        (temp_root / BACKUP_MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 2. 数据库一致性在线快照
        if db_path.exists():
            pkg_db = temp_root / "answer_card_app.db"
            src_conn = None
            dst_conn = None
            try:
                src_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                dst_conn = sqlite3.connect(pkg_db)
                src_conn.backup(dst_conn)
            finally:
                if dst_conn:
                    try:
                        dst_conn.close()
                    except Exception:
                        pass
                if src_conn:
                    try:
                        src_conn.close()
                    except Exception:
                        pass

        # 3. 学生名册
        if roster_path.exists():
            shutil.copy2(roster_path, temp_root / "student_roster_local.py")

        # 4. 配置文件
        cfg_root = temp_root / "configs"
        cfg_root.mkdir(parents=True, exist_ok=True)
        config_names = [
            "llm_grading_config.json",
            "manual_web_sync_config.json",
            "manual_web_server_config.json",
            "wrongbook_settings.json",
            "knowledge_bridge_settings.json",
            "overlay_calibration_presets.json",
            "ocr_preference_memory.json",
            "enhanced_gui_settings.json"
        ]
        for cfg in config_names:
            src_cfg = app_dir / cfg
            if src_cfg.exists() and src_cfg.is_file():
                shutil.copy2(src_cfg, cfg_root / cfg)

        # 5. 标准答案库 (只复制标准 .json)
        if ak_dir.exists():
            pkg_ak = temp_root / "answer_keys"
            pkg_ak.mkdir(parents=True, exist_ok=True)
            for jf in ak_dir.glob("*.json"):
                shutil.copy2(jf, pkg_ak / jf.name)

        # 6. 试卷模板库
        if tpl_dir.exists():
            pkg_tpl = temp_root / "templates"
            pkg_tpl.mkdir(parents=True, exist_ok=True)
            for sdir in tpl_dir.iterdir():
                if sdir.is_dir():
                    dst_sdir = pkg_tpl / sdir.name
                    dst_sdir.mkdir(parents=True, exist_ok=True)
                    for f in sdir.iterdir():
                        if f.is_file() and not any(k in f.name.lower() for k in ["backup", ".bak", "debug", "preview", "tmp", "temp"]):
                            shutil.copy2(f, dst_sdir / f.name)

        # 7. 历史各测试会话考务打分明细
        sdata_root = temp_root / "session_data"
        sdata_root.mkdir(parents=True, exist_ok=True)
        for sdir_name in ["session_subjective_scores", "session_template_configs", "session_answer_keys"]:
            src_sdir = app_dir / sdir_name
            if src_sdir.exists():
                dst_sdir = sdata_root / sdir_name
                dst_sdir.mkdir(parents=True, exist_ok=True)
                for f in src_sdir.glob("*.json"):
                    shutil.copy2(f, dst_sdir / f.name)

        # 8. 教师评卷素材与知识点表
        t_assets = app_dir / "teacher_mark_assets"
        if t_assets.exists():
            pkg_ta = temp_root / "teacher_mark_assets"
            pkg_ta.mkdir(parents=True, exist_ok=True)
            for png in t_assets.glob("*.png"):
                shutil.copy2(png, pkg_ta / png.name)

        csv_file = app_dir / "知识点编号对应内容.csv"
        if csv_file.exists():
            shutil.copy2(csv_file, temp_root / "知识点编号对应内容.csv")

        # 压缩生成目标 zip
        if target_path.exists():
            target_path.unlink()

        with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for root, dirs, files in os.walk(temp_root):
                for f in files:
                    full_p = Path(root) / f
                    rel_p = full_p.relative_to(temp_root)
                    zf.write(full_p, arcname=str(rel_p).replace("\\", "/"))

    manifest["package_path"] = str(target_path)
    manifest["package_size_mb"] = round(target_path.stat().st_size / (1024 * 1024), 2)
    return manifest

def inspect_data_package(zip_path: str) -> dict:
    """
    检查并解析备份包信息
    """
    zp = Path(zip_path)
    res = {
        "valid": False,
        "zip_path": str(zp),
        "created_at": "",
        "sessions": 0,
        "results": 0,
        "classes_count": 0,
        "answer_keys_count": 0,
        "templates_count": 0,
        "has_llm_key": False,
        "size_mb": round(zp.stat().st_size / (1024 * 1024), 2) if zp.exists() else 0.0
    }

    if not zp.exists() or not zipfile.is_zipfile(zp):
        return res

    with zipfile.ZipFile(zp, "r") as zf:
        namelist = zf.namelist()

        manifest_entries = [n for n in namelist if n.endswith(BACKUP_MANIFEST_NAME)]
        if manifest_entries:
            try:
                data = json.loads(zf.read(manifest_entries[0]).decode("utf-8-sig"))
                res["valid"] = True
                res["created_at"] = data.get("created_at", "")
                db_stats = data.get("db_stats", {})
                res["sessions"] = db_stats.get("sessions", 0)
                res["results"] = db_stats.get("results", 0)
                res["classes_count"] = data.get("classes_count", 0)
                res["answer_keys_count"] = data.get("answer_keys_count", 0)
                res["templates_count"] = data.get("templates_count", 0)
                res["has_llm_key"] = data.get("has_llm_key", False)
                return res
            except Exception:
                pass

        if any(n.endswith("answer_card_app.db") for n in namelist):
            res["valid"] = True
            res["created_at"] = "标准备份包(自动识别)"
            res["answer_keys_count"] = len([n for n in namelist if "answer_keys/" in n and n.endswith(".json")])
            res["templates_count"] = len(set([n.split("templates/")[1].split("/")[0] for n in namelist if "templates/" in n and "/" in n.split("templates/")[1]]))
            return res

    return res

def import_data_package(zip_path: str, app_dir: Path) -> list:
    """
    从 zip 备份包中安全还原业务数据到 app_dir
    """
    zp = Path(zip_path)
    if not zp.exists():
        raise FileNotFoundError(f"备份包不存在：{zip_path}")

    logs = []
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    recovery_dir = app_dir / "recovery_backups"
    recovery_dir.mkdir(parents=True, exist_ok=True)

    # 1. 自动安全备份当前已有数据
    curr_db = app_dir / "answer_card_app.db"
    if curr_db.exists() and curr_db.stat().st_size > 32 * 1024:
        backup_curr_db = recovery_dir / f"pre_import_answer_card_app_{ts}.db"
        shutil.copy2(curr_db, backup_curr_db)
        logs.append(f"已备份当前系统数据库至: recovery_backups/{backup_curr_db.name}")

    curr_roster = app_dir / "student_roster_local.py"
    if curr_roster.exists():
        backup_curr_roster = recovery_dir / f"pre_import_student_roster_{ts}.py"
        shutil.copy2(curr_roster, backup_curr_roster)

    # 2. 解包还原
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        with zipfile.ZipFile(zp, "r") as zf:
            zf.extractall(temp_root)

        content_root = temp_root
        if not (content_root / "answer_card_app.db").exists() and not (content_root / BACKUP_MANIFEST_NAME).exists():
            subdirs = [d for d in content_root.iterdir() if d.is_dir()]
            if subdirs and ((subdirs[0] / "answer_card_app.db").exists() or (subdirs[0] / BACKUP_MANIFEST_NAME).exists()):
                content_root = subdirs[0]

        # 2.1 还原主数据库
        pkg_db = content_root / "answer_card_app.db"
        if pkg_db.exists():
            src_conn = None
            dst_conn = None
            try:
                src_conn = sqlite3.connect(f"file:{pkg_db}?mode=ro", uri=True)
                dst_conn = sqlite3.connect(curr_db)
                src_conn.backup(dst_conn)
            finally:
                if dst_conn:
                    try:
                        dst_conn.close()
                    except Exception:
                        pass
                if src_conn:
                    try:
                        src_conn.close()
                    except Exception:
                        pass

            logs.append(f"[成功] 核心业务数据库导入成功 ({round(curr_db.stat().st_size / 1024 / 1024, 2)} MB)")

            # 执行向前结构兼容检查（补全可能的新增字段）
            conn = None
            try:
                conn = sqlite3.connect(curr_db)
                cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
                if 'folder_path' not in cols:
                    conn.execute("ALTER TABLE sessions ADD COLUMN folder_path TEXT")
                    logs.append("[成功] 数据库结构自动向前兼容: 补全 folder_path 字段")
                if 'manual_web_job_id' not in cols:
                    conn.execute("ALTER TABLE sessions ADD COLUMN manual_web_job_id TEXT")
                    logs.append("[成功] 数据库结构自动向前兼容: 补全 manual_web_job_id 字段")
                conn.commit()
            except Exception as e:
                logs.append(f"[提示] 数据库结构检查: {e}")
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

        # 2.2 还原学生名册
        pkg_roster = content_root / "student_roster_local.py"
        if pkg_roster.exists():
            shutil.copy2(pkg_roster, curr_roster)
            logs.append("[成功] 学生班级名册导入成功 (已恢复真实名单)")

        # 2.3 还原配置文件
        pkg_configs = content_root / "configs"
        if pkg_configs.exists() and pkg_configs.is_dir():
            for cfg_f in pkg_configs.iterdir():
                if cfg_f.is_file():
                    target_cfg = app_dir / cfg_f.name
                    if cfg_f.name == "llm_grading_config.json" and target_cfg.exists():
                        try:
                            pkg_llm = json.loads(cfg_f.read_text(encoding="utf-8-sig"))
                            cur_llm = json.loads(target_cfg.read_text(encoding="utf-8-sig"))
                            if pkg_llm.get("api_key", "").strip():
                                cur_llm["api_key"] = pkg_llm["api_key"]
                                cur_llm["enabled"] = pkg_llm.get("enabled", True)
                                if "model" in pkg_llm:
                                    cur_llm["model"] = pkg_llm["model"]
                                if "custom_rules" in pkg_llm and pkg_llm["custom_rules"]:
                                    cur_llm["custom_rules"] = pkg_llm["custom_rules"]
                                target_cfg.write_text(json.dumps(cur_llm, ensure_ascii=False, indent=2), encoding="utf-8")
                                logs.append("[成功] 大模型批改配置已回填 API Key")
                                continue
                        except Exception:
                            pass
                    shutil.copy2(cfg_f, target_cfg)
            logs.append("[成功] 核心运行配置与偏好设置已同步")

        # 2.4 增量合并标准答案库
        pkg_ak = content_root / "answer_keys"
        if pkg_ak.exists() and pkg_ak.is_dir():
            curr_ak = app_dir / "answer_keys"
            curr_ak.mkdir(parents=True, exist_ok=True)
            ak_added = 0
            for jf in pkg_ak.glob("*.json"):
                t_file = curr_ak / jf.name
                if not t_file.exists():
                    shutil.copy2(jf, t_file)
                    ak_added += 1
            logs.append(f"[成功] 标准答案方案库合并完成 (新增导入 {ak_added} 套方案)")

        # 2.5 增量合并试卷模板
        pkg_tpl = content_root / "templates"
        if pkg_tpl.exists() and pkg_tpl.is_dir():
            curr_tpl = app_dir / "templates"
            curr_tpl.mkdir(parents=True, exist_ok=True)
            tpl_added = 0
            for sdir in pkg_tpl.iterdir():
                if sdir.is_dir():
                    t_sdir = curr_tpl / sdir.name
                    if not t_sdir.exists():
                        shutil.copytree(sdir, t_sdir)
                        tpl_added += 1
            logs.append(f"[成功] 试卷模板库合并完成 (新增导入 {tpl_added} 个自定义模板)")

        # 2.6 恢复历史各测试会话考务明细
        pkg_sdata = content_root / "session_data"
        if pkg_sdata.exists() and pkg_sdata.is_dir():
            for sname in ["session_subjective_scores", "session_template_configs", "session_answer_keys"]:
                src_s = pkg_sdata / sname
                dst_s = app_dir / sname
                dst_s.mkdir(parents=True, exist_ok=True)
                if src_s.exists():
                    for item in src_s.glob("*.json"):
                        shutil.copy2(item, dst_s / item.name)
            logs.append("[成功] 历史各场测试会话的主观打分明细已恢复")

        # 2.7 教师素材与知识点表
        pkg_ta = content_root / "teacher_mark_assets"
        if pkg_ta.exists():
            curr_ta = app_dir / "teacher_mark_assets"
            curr_ta.mkdir(parents=True, exist_ok=True)
            for png in pkg_ta.glob("*.png"):
                t_png = curr_ta / png.name
                if not t_png.exists():
                    shutil.copy2(png, t_png)

        pkg_csv = content_root / "知识点编号对应内容.csv"
        if pkg_csv.exists() and not (app_dir / "知识点编号对应内容.csv").exists():
            shutil.copy2(pkg_csv, app_dir / "知识点编号对应内容.csv")
            logs.append("[成功] 知识点对照表已同步")

    return logs
