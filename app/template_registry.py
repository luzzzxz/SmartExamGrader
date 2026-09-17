import json
import re
from pathlib import Path

REGISTRY_FILENAME = 'template_registry.json'
DEFAULT_TEMPLATE_KEY = 'default'


def registry_path(project_dir: Path) -> Path:
    return Path(project_dir) / REGISTRY_FILENAME


def slugify(value: str) -> str:
    text = (value or '').strip().lower()
    text = re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '_', text)
    text = re.sub(r'_+', '_', text).strip('_')
    return text or 'template'


def _default_entry() -> dict:
    return {
        'display_name': '默认阅卷模板',
        'mode': 'grader',
        'template_image': 'incoming/template_with_markers/1.jpg',
        'marker_config': 'template_marker_detection.json',
        'answer_config': 'answer_zone_points.json',
        'student_id_config': 'student_id_points.json',
    }


def _normalize_manifest(manifest: dict) -> dict:
    if not isinstance(manifest, dict):
        manifest = {}
    templates = manifest.get('templates') or {}
    if DEFAULT_TEMPLATE_KEY not in templates:
        templates[DEFAULT_TEMPLATE_KEY] = _default_entry()
    manifest['templates'] = templates
    current = manifest.get('current_template')
    if not current or current not in templates:
        manifest['current_template'] = DEFAULT_TEMPLATE_KEY
    return manifest


def ensure_template_registry(project_dir: Path) -> dict:
    project_dir = Path(project_dir)
    path = registry_path(project_dir)
    if path.exists():
        manifest = json.loads(path.read_text(encoding='utf-8'))
    else:
        manifest = {
            'current_template': DEFAULT_TEMPLATE_KEY,
            'templates': {
                DEFAULT_TEMPLATE_KEY: _default_entry(),
            },
        }
    manifest = _normalize_manifest(manifest)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def resolve_template_paths(project_dir: Path, entry: dict) -> dict:
    project_dir = Path(project_dir)

    def _p(key: str, default: str = '') -> Path:
        value = entry.get(key) or default
        return project_dir / value if value else project_dir / '__missing__'

    return {
        'template_image': _p('template_image'),
        'marker_config': _p('marker_config', 'template_marker_detection.json'),
        'answer_config': _p('answer_config', 'answer_zone_points.json'),
        'student_id_config': _p('student_id_config', 'student_id_points.json'),
    }


def save_manifest(project_dir: Path, manifest: dict) -> dict:
    path = registry_path(project_dir)
    manifest = _normalize_manifest(manifest)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def set_current_template(project_dir: Path, template_key: str) -> dict:
    manifest = ensure_template_registry(project_dir)
    if template_key not in manifest['templates']:
        raise KeyError(f'模板不存在: {template_key}')
    manifest['current_template'] = template_key
    return save_manifest(project_dir, manifest)


def upsert_template(project_dir: Path, template_key: str, entry: dict, make_current: bool = True) -> dict:
    manifest = ensure_template_registry(project_dir)
    manifest['templates'][template_key] = entry
    if make_current:
        manifest['current_template'] = template_key
    return save_manifest(project_dir, manifest)
