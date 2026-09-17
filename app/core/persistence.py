"""Crash-safe JSON replacement for local scores, snapshots and settings."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import shutil


def atomic_write_json(path, data):
    # Serialize before touching the destination; invalid data must not erase it.
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', newline='\n',
            dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp', delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_score_payload(path, default):
    """Only a missing file is empty; never silently overwrite corrupt scores."""
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as error:
        raise ValueError(f'成绩文件读取失败，已停止覆盖，请检查或恢复备份：{path}') from error
    if not isinstance(payload, dict) or not isinstance(payload.get('scores', {}), dict):
        raise ValueError(f'成绩文件结构无效，已停止覆盖：{path}')
    if any(not isinstance(records, dict) for records in payload.get('scores', {}).values()):
        raise ValueError(f'成绩记录结构无效，已停止覆盖：{path}')
    return payload


def replace_file_batch(staged_pairs):
    """Replace a generated file set, rolling back earlier files on I/O failure."""
    pairs = [(Path(source), Path(target)) for source, target in staged_pairs]
    if not pairs:
        return
    folder = Path(tempfile.mkdtemp(prefix='.export-backup-', dir=pairs[0][1].parent))
    keep_backup = False
    try:
        backups = {}
        for index, (_, target) in enumerate(pairs):
            backups[target] = None
            if target.exists():
                backup = Path(folder) / str(index)
                shutil.copy2(target, backup)
                backups[target] = backup
        replaced = []
        try:
            for source, target in pairs:
                os.replace(source, target)
                replaced.append(target)
        except Exception as original_error:
            rollback_errors = []
            for target in reversed(replaced):
                try:
                    if backups[target] is None:
                        target.unlink(missing_ok=True)
                    else:
                        os.replace(backups[target], target)
                except Exception as exc:
                    rollback_errors.append(str(exc))
            if rollback_errors:
                keep_backup = True
                raise OSError(f'输出替换及回退失败，原文件备份保留于 {folder}：{rollback_errors}') from original_error
            raise
    finally:
        if not keep_backup:
            shutil.rmtree(folder)
