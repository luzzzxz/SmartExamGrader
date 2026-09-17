#!/usr/bin/env python3
import html
import base64
import hashlib
import hmac
import json
import mimetypes
import math
import threading
from functools import wraps
import os
import posixpath
import shutil
import secrets
import tempfile
import urllib.parse
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('MANUAL_WEB_DATA_DIR') or APP_DIR / 'manual_web_data')
CONFIG_PATH = Path(os.environ.get('MANUAL_WEB_CONFIG') or APP_DIR / 'manual_web_server_config.json')
HOST = os.environ.get('MANUAL_WEB_HOST', '0.0.0.0')
PORT = int(os.environ.get('MANUAL_WEB_PORT', '18765'))


def load_config():
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding='utf-8-sig'))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    token = os.environ.get('MANUAL_WEB_TOKEN') or ''
    data = {'api_token': token}
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return data


CONFIG = load_config()
API_TOKEN = str(CONFIG.get('api_token') or os.environ.get('MANUAL_WEB_TOKEN') or '')
SESSION_COOKIE = 'manual_teacher_session'


def save_config():
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2), encoding='utf-8')


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', str(password).encode('utf-8'), salt.encode('utf-8'), 200000)
    return f'pbkdf2_sha256${salt}${base64.urlsafe_b64encode(digest).decode("ascii")}'


def verify_password(password, stored):
    try:
        method, salt, digest = str(stored or '').split('$', 2)
        if method != 'pbkdf2_sha256':
            return False
        expected = hash_password(password, salt).split('$', 2)[2]
        return hmac.compare_digest(expected, digest)
    except Exception:
        return False


def ensure_teacher_config():
    changed = False
    if not CONFIG.get('session_secret'):
        CONFIG['session_secret'] = secrets.token_urlsafe(32)
        changed = True
    if not CONFIG.get('teacher_username'):
        CONFIG['teacher_username'] = os.environ.get('MANUAL_WEB_TEACHER_USER') or 'teacher'
        changed = True
    if not CONFIG.get('teacher_password_hash'):
        initial_password = (
            os.environ.get('MANUAL_WEB_TEACHER_PASSWORD')
            or CONFIG.get('teacher_password')
            or secrets.token_urlsafe(8)
        )
        CONFIG['teacher_password_hash'] = hash_password(initial_password)
        CONFIG['teacher_password_initial'] = initial_password
        changed = True
    if changed:
        save_config()


ensure_teacher_config()


def make_session_cookie(username):
    username = str(username or '')
    signature = hmac.new(
        str(CONFIG.get('session_secret') or '').encode('utf-8'),
        username.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    raw = f'{username}:{signature}'.encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii')


def verify_session_cookie(value):
    try:
        raw = base64.urlsafe_b64decode(str(value or '').encode('ascii')).decode('utf-8')
        username, signature = raw.rsplit(':', 1)
        if username != str(CONFIG.get('teacher_username') or ''):
            return False
        expected = hmac.new(
            str(CONFIG.get('session_secret') or '').encode('utf-8'),
            username.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


def safe_id(value):
    text = ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in str(value or '').strip())
    return text.strip('._-') or 'job'


def job_dir(job_id):
    return DATA_DIR / 'jobs' / safe_id(job_id)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except Exception:
        return default


# The VPS installer intentionally deploys this single standalone script.
DATA_LOCK = threading.RLock()
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_FILES = 20000


def locked_data(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with DATA_LOCK:
            return function(*args, **kwargs)
    return wrapped


def write_json(path, data):
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_scores(path, default):
    try:
        payload = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return default
    if not isinstance(payload, dict) or not isinstance(payload.get('scores', {}), dict):
        raise ValueError('成绩文件损坏，请恢复备份后重试')
    if any(not isinstance(v, dict) for v in payload.get('scores', {}).values()):
        raise ValueError('成绩记录损坏，请恢复备份后重试')
    return payload



@locked_data
def list_jobs():
    root = DATA_DIR / 'jobs'
    if not root.exists():
        return []
    rows = []
    for folder in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        manifest = read_json(folder / 'manifest.json', {})
        if manifest:
            scores = read_scores(folder / 'scores.json', {'scores': {}})
            total = len(manifest.get('tasks') or [])
            done = sum(len(v or {}) for v in (scores.get('scores') or {}).values())
            rows.append({
                'job_id': folder.name,
                'job_name': manifest.get('job_name') or folder.name,
                'session_name': manifest.get('session_name') or '',
                'template_name': manifest.get('template_name') or '',
                'total_tasks': total,
                'done_tasks': done,
                'created_at': manifest.get('created_at') or '',
                'updated_at': scores.get('updated_at') or '',
            })
    return rows


class Handler(BaseHTTPRequestHandler):
    server_version = 'ManualGradingWeb/1.0'

    def log_message(self, fmt, *args):
        print(datetime.now().isoformat(timespec='seconds'), self.client_address[0],
              self.command, urllib.parse.urlsplit(self.path).path)

    def token_from_request(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return self.headers.get('X-Manual-Grading-Token') or (query.get('token') or [''])[0]

    def cookie_from_request(self, name):
        raw = self.headers.get('Cookie') or ''
        for item in raw.split(';'):
            if '=' not in item:
                continue
            key, value = item.strip().split('=', 1)
            if key == name:
                return urllib.parse.unquote(value)
        return ''

    def check_token_auth(self):
        if not API_TOKEN:
            return False
        return hmac.compare_digest(self.token_from_request().encode('utf-8'), API_TOKEN.encode('utf-8'))

    def check_session_auth(self):
        return verify_session_cookie(self.cookie_from_request(SESSION_COOKIE))

    def check_auth(self):
        return self.check_token_auth() or self.check_session_auth()

    def send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_html(self, body, status=200):
        raw = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def redirect(self, target):
        self.send_response(302)
        self.send_header('Location', target)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def deny_if_needed(self):
        if self.check_auth():
            return False
        self.send_json({'ok': False, 'error': '未登录或未授权'}, 401)
        return True

    def require_page_login(self):
        if self.check_auth():
            return False
        next_url = urllib.parse.quote(self.path or '/')
        self.redirect(f'/login?next={next_url}')
        return True

    def page_login(self, error=''):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        next_url = html.escape((query.get('next') or ['/'])[0])
        error_html = f"<div class='err'>{html.escape(error)}</div>" if error else ''
        body = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>教师登录</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:linear-gradient(135deg,#f3eadf,#fff7ed);font-family:"Microsoft YaHei",sans-serif;color:#2b2b2b}}
.card{{width:min(420px,calc(100vw - 32px));background:#fff;border-radius:18px;box-shadow:0 16px 42px #0002;padding:28px;box-sizing:border-box}}
h1{{margin:0 0 18px;font-size:28px}} label{{display:block;margin:12px 0 6px;font-weight:bold}} input{{width:100%;box-sizing:border-box;border:1px solid #d8c7b8;border-radius:12px;padding:12px;font-size:18px}}
button{{width:100%;margin-top:20px;border:0;border-radius:14px;padding:13px;background:#d93622;color:white;font-size:19px;font-weight:bold}} .err{{background:#fff0ef;color:#b42318;border-radius:10px;padding:10px;margin-bottom:12px}}
.tip{{color:#777;font-size:14px;line-height:1.6;margin-top:14px}}
</style></head><body><form class="card" method="post" action="/login">
<h1>教师登录</h1>{error_html}
<input type="hidden" name="next" value="{next_url}">
<label>用户名</label><input name="username" autocomplete="username" autofocus>
<label>密码</label><input name="password" type="password" autocomplete="current-password">
<button type="submit">登录</button>
<div class="tip">登录后可以查看本地主程序上传的待人工批改任务。</div>
</form></body></html>"""
        self.send_html(body)

    def read_body(self, limit):
        if self.headers.get('Transfer-Encoding'):
            raise ValueError('不支持分块请求，请提供 Content-Length')
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            raise ValueError('无效的 Content-Length')
        length = int(lengths[0])
        if length <= 0 or length > limit:
            raise ValueError('请求为空或超过大小限制')
        self.connection.settimeout(30)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError('请求内容不完整')
        return raw

    def handle_login(self):
        raw = self.read_body(16384).decode('utf-8', errors='replace')
        data = urllib.parse.parse_qs(raw)
        username = (data.get('username') or [''])[0]
        password = (data.get('password') or [''])[0]
        next_url = (data.get('next') or ['/'])[0] or '/'
        if (
            username == str(CONFIG.get('teacher_username') or '')
            and verify_password(password, CONFIG.get('teacher_password_hash'))
        ):
            self.send_response(302)
            self.send_header('Set-Cookie', f'{SESSION_COOKIE}={urllib.parse.quote(make_session_cookie(username))}; Path=/; HttpOnly; SameSite=Lax')
            self.send_header('Location', next_url if next_url.startswith('/') and not next_url.startswith('//') and not any(c in next_url for c in ('\\', '\r', '\n')) else '/')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        self.page_login('用户名或密码不正确')

    def do_GET(self):
        try:
            self.dispatch_get()
        except (ValueError, KeyError, TypeError):
            self.send_json({'ok': False, 'error': '任务数据损坏，请检查服务端备份'}, 500)
        except OSError:
            self.send_json({'ok': False, 'error': '任务文件读取失败，请重试'}, 500)

    def dispatch_get(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip('/') or '/'
        if path == '/login':
            return self.page_login()
        if path == '/logout':
            self.send_response(302)
            self.send_header('Set-Cookie', f'{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax')
            self.send_header('Location', '/login')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if path == '/':
            if self.require_page_login():
                return
            return self.page_index()
        if path.startswith('/job/'):
            if self.require_page_login():
                return
            return self.page_job(path.split('/', 2)[2])
        if path == '/api/jobs':
            if self.deny_if_needed():
                return
            return self.send_json({'ok': True, 'jobs': list_jobs()})
        if path.startswith('/api/jobs/'):
            if self.deny_if_needed():
                return
            return self.api_get_job(path)
        self.send_json({'ok': False, 'error': 'not found'}, 404)

    def do_POST(self):
        try:
            self.dispatch_post()
        except (ValueError, KeyError, TypeError, zipfile.BadZipFile):
            self.send_json({'ok': False, 'error': '请求或任务数据无效，请检查后重试'}, 400)
        except TimeoutError:
            self.send_json({'ok': False, 'error': '请求超时'}, 408)
        except OSError:
            self.send_json({'ok': False, 'error': '文件读写失败，未确认保存，请重试'}, 500)

    def dispatch_post(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip('/') or '/'
        if path == '/login':
            return self.handle_login()
        if self.deny_if_needed():
            return
        if path == '/api/upload':
            return self.api_upload()
        if path == '/api/jobs/delete-all':
            return self.api_delete_all_jobs()
        if path.startswith('/api/jobs/') and path.endswith('/score'):
            return self.api_save_score(path)
        if path.startswith('/api/jobs/') and path.endswith('/regrade_part'):
            return self.api_regrade_part(path)
        if path.startswith('/api/jobs/') and path.endswith('/delete'):
            return self.api_delete_job(path)
        self.send_json({'ok': False, 'error': 'not found'}, 404)

    def page_index(self):
        rows = list_jobs()
        items = []
        for row in rows:
            raw_job_id = str(row['job_id'])
            job_id = html.escape(raw_job_id)
            name = html.escape(row['job_name'])
            meta = html.escape(f"{row.get('template_name', '')}  {row.get('created_at', '')}")
            items.append(
                f"<div class='job-row'>"
                f"<a class='job' href='/job/{job_id}'>"
                f"<b>{name}</b><span>{row['done_tasks']}/{row['total_tasks']} 已打分</span><small>{meta}</small></a>"
                f"<button class='danger delete-one' onclick='deleteJob({json.dumps(raw_job_id)}, {json.dumps(str(row['job_name']), ensure_ascii=False)})'>删除</button>"
                f"</div>"
            )
        body = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>网页人工打分</title>
<style>
body{{margin:0;background:#f4f0e8;font-family:"Microsoft YaHei",sans-serif;color:#2b2b2b}}
.wrap{{max-width:920px;margin:0 auto;padding:24px}}
h1{{font-size:28px;margin:0 0 18px}}
.job-row{{display:grid;grid-template-columns:minmax(0,1fr) 86px;gap:10px;align-items:stretch;margin:12px 0}}
.job{{display:block;background:#fff;border:1px solid #e0d6c8;border-radius:14px;padding:16px;text-decoration:none;color:#222;box-shadow:0 6px 18px #0000000d}}
.job b{{display:block;font-size:20px;margin-bottom:8px}} .job span{{margin-right:14px;color:#b33b26}} .job small{{color:#777}}
.empty{{background:#fff;border-radius:14px;padding:24px;color:#777}} .logout{{float:right;color:#8f2f1f;text-decoration:none;font-size:14px;margin-top:8px}}
.toolbar{{display:flex;gap:10px;align-items:center;margin:0 0 14px;flex-wrap:wrap}}
.danger{{border:0;border-radius:12px;background:#d93622;color:white;padding:10px 14px;font-size:16px;font-weight:bold}}
.delete-one{{height:auto}}
@media(max-width:640px){{.job-row{{grid-template-columns:1fr}}.delete-one{{min-height:42px}}}}
</style></head><body><div class="wrap"><a class="logout" href="/logout">退出登录</a><h1>网页人工打分</h1><div class="toolbar"><button class="danger" onclick="clearAllJobs()">清空全部任务</button></div>{''.join(items) if items else '<div class="empty">还没有上传任务。</div>'}</div>
<script>
async function deleteJob(jobId, jobName){{
  if(!confirm('确定删除这个任务吗？\\n\\n' + (jobName || jobId))) return;
  const r = await fetch('/api/jobs/' + encodeURIComponent(jobId) + '/delete', {{method:'POST'}});
  const data = await r.json();
  if(!data.ok){{ alert(data.error || '删除失败'); return; }}
  location.reload();
}}
async function clearAllJobs(){{
  if(!confirm('确定删除网页上所有已上传的人工批改任务吗？删除后手机端将看不到这些内容。')) return;
  const r = await fetch('/api/jobs/delete-all', {{method:'POST'}});
  const data = await r.json();
  if(!data.ok){{ alert(data.error || '删除失败'); return; }}
  alert('已删除 ' + (data.deleted_count || 0) + ' 个任务');
  location.reload();
}}
</script></body></html>"""
        self.send_html(body)

    @locked_data
    def page_job(self, job_id):
        job_id = safe_id(job_id)
        folder = job_dir(job_id)
        manifest = read_json(folder / 'manifest.json', {})
        if not manifest:
            self.send_html('<h1>任务不存在</h1>', 404)
            return
        title = html.escape(manifest.get('job_name') or job_id)
        body = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
body{{margin:0;background:#eee7dc;font-family:"Microsoft YaHei",sans-serif;color:#222}}
.top{{position:sticky;top:0;background:#8f2f1f;color:#fff;padding:9px 12px;z-index:3}}
.top b{{font-size:18px}} .top span{{margin-left:12px;font-size:14px}}
.main{{padding:10px;box-sizing:border-box;max-width:1180px;margin:0 auto}}
.workbar{{background:#fff;border-radius:14px;box-shadow:0 5px 18px #0001;padding:10px 12px;margin-bottom:10px}}
.label{{font-size:20px;font-weight:bold;margin-bottom:4px}} .meta{{color:#555;line-height:1.45;font-size:14px}}
.gradeArea{{display:grid;grid-template-columns:minmax(0,1fr) 150px;gap:12px;align-items:start}}
.image{{background:#fff;border-radius:14px;box-shadow:0 5px 18px #0001;padding:10px;min-height:62vh;text-align:center;overflow:auto}}
.image img{{max-width:100%;max-height:72vh;object-fit:contain}}
.answer{{margin:8px auto 0;padding:9px 16px;background:#fff7df;border-radius:10px;color:#8a3b00;font-size:22px;font-weight:bold;text-align:center;max-width:760px}}
.scoreDock{{position:sticky;top:88px;background:#fff8f2;border:1px solid #ead7c6;border-radius:14px;padding:10px;text-align:center;box-shadow:0 5px 18px #0001}}
.scores{{display:flex;flex-direction:column;gap:10px;align-items:stretch}}
.scores button{{font-size:30px;margin:0;padding:14px 0;border:0;border-radius:12px;background:#d93b27;color:#fff;width:100%}}
.nav button{{font-size:15px;margin:8px 0 0;padding:8px 10px;border:0;border-radius:10px;background:#333;color:#fff;width:100%}}
.nav button.danger{{background:#d93622}}
.saved{{display:block;color:#15803d;font-weight:bold;margin-top:8px;min-height:20px}}
@media(max-width:760px){{.main{{padding:8px}}.gradeArea{{grid-template-columns:minmax(0,1fr) 92px;gap:8px}}.image{{min-height:44vh}}.image img{{max-height:54vh}}.answer{{font-size:18px;padding:8px}}.scoreDock{{top:70px;padding:8px}}.scores button{{font-size:24px;padding:12px 0}}.nav button{{font-size:13px;padding:7px 4px}}}}
</style></head><body>
<div class="top"><b>{title}</b><span id="progress"></span></div>
<div class="main"><div class="workbar">
<div class="label" id="label"></div><div class="meta" id="meta"></div>
</div><div class="gradeArea"><div class="image"><img id="crop" alt="题图"><div class="answer" id="answer"></div></div>
<div class="scoreDock"><div><span class="scores" id="scores"></span><span class="saved" id="saved"></span></div>
<div class="nav"><button onclick="prevTask()">上一空</button><button onclick="nextTask()">下一空</button><button onclick="nextPending()">下一个未打</button><button class="danger" onclick="deleteCurrentJob()">删除当前任务</button></div>
</div></div></div></div>
<script>
const jobId={json.dumps(job_id)};
let manifest=null, scores={{}}, tasks=[], idx=0, completionShown=false;
async function api(path, opt={{}}){{opt.headers=opt.headers||{{}}; const r=await fetch(path,opt); if(r.status===401){{location.href='/login?next='+encodeURIComponent(location.pathname); return {{}};}} return await r.json();}}
function rec(t){{return ((scores[t.entry_key]||{{}})[t.part_id]||{{}});}}
function done(t){{return rec(t).score!==undefined && rec(t).score!==null;}}
function orderedTasks(rawTasks){{
  const partOrder=new Map((manifest.parts||[]).map((p,i)=>[String(p.part_id||''),i]));
  const entryOrder=new Map((manifest.entries||[]).map((e,i)=>[String(e.entry_key||''),i]));
  return (rawTasks||[]).map((task,index)=>({{task,index}})).sort((a,b)=>{{
    const ap=partOrder.has(String(a.task.part_id||''))?partOrder.get(String(a.task.part_id||'')):Number.MAX_SAFE_INTEGER;
    const bp=partOrder.has(String(b.task.part_id||''))?partOrder.get(String(b.task.part_id||'')):Number.MAX_SAFE_INTEGER;
    if(ap!==bp)return ap-bp;
    const ae=entryOrder.has(String(a.task.entry_key||''))?entryOrder.get(String(a.task.entry_key||'')):Number.MAX_SAFE_INTEGER;
    const be=entryOrder.has(String(b.task.entry_key||''))?entryOrder.get(String(b.task.entry_key||'')):Number.MAX_SAFE_INTEGER;
    if(ae!==be)return ae-be;
    return a.index-b.index;
  }}).map(row=>row.task);
}}
async function load(){{const data=await api(`/api/jobs/${{jobId}}`); manifest=data.manifest; scores=data.scores||{{}}; tasks=orderedTasks(manifest.tasks||[]); idx=Math.max(0,tasks.findIndex(t=>!done(t))); render();}}
function render(){{if(!tasks.length)return; const t=tasks[idx], r=rec(t); document.getElementById('progress').innerText=`${{idx+1}}/${{tasks.length}}  已打 ${{tasks.filter(done).length}}`; document.getElementById('crop').src=`/api/jobs/${{jobId}}/image/${{t.image}}`; document.getElementById('label').innerText=t.label||t.part_id; document.getElementById('meta').innerText=`学生：${{t.score_id||''}} ${{t.student_name||''}}　文件：${{t.file||''}}　当前：${{r.score ?? t.current_score ?? '未打'}} / ${{t.max_score}}`; document.getElementById('answer').innerText=t.expected_answer?`答案：${{t.expected_answer}}`:'答案：未设置'; document.getElementById('scores').replaceChildren(...(t.score_choices||[0,1]).filter(v=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<=t.max_score).map(v=>{{const button=document.createElement('button'); button.textContent=String(v); button.onclick=()=>saveScore(v); return button;}})); document.getElementById('saved').innerText='';}}
function allDone(){{return tasks.length>0 && tasks.every(done);}}
function showCompleteIfNeeded(){{if(allDone() && !completionShown){{completionShown=true; setTimeout(()=>alert('人工打分已全部完成，可以回到本地点击“获取网页打分”。'),80);}}}}
let saving=false;
async function saveScore(v){{if(saving||!tasks.length)return; saving=true; try{{const savedIndex=idx; const t=tasks[idx]; const result=await api(`/api/jobs/${{jobId}}/score`,{{method:'POST',body:JSON.stringify({{job_revision:manifest.revision,entry_key:t.entry_key,part_id:t.part_id,score:v,max_score:t.max_score,label:t.label,score_id:t.score_id,student_name:t.student_name,file:t.file}}),headers:{{'Content-Type':'application/json'}}}}); if(!result.ok)throw new Error(result.error||'保存失败，请重新登录或重试'); scores[t.entry_key]=scores[t.entry_key]||{{}}; scores[t.entry_key][t.part_id]={{score:v,max_score:t.max_score,label:t.label,score_id:t.score_id,student_name:t.student_name,file:t.file}}; if(idx!==savedIndex){{render(); return;}} document.getElementById('saved').innerText='已保存'; if(allDone()){{render(); showCompleteIfNeeded(); return;}} nextPending(true);}}catch(error){{document.getElementById('saved').innerText='保存失败，请重试'; alert(error.message);}}finally{{saving=false;}}}}
function nextTask(){{idx=Math.min(tasks.length-1,idx+1);render();}} function prevTask(){{idx=Math.max(0,idx-1);render();}}
function nextPending(fromCurrent=false){{let start=fromCurrent?idx+1:idx; for(let i=start;i<tasks.length;i++){{if(!done(tasks[i])){{idx=i;render();return;}}}} if(allDone()){{showCompleteIfNeeded();return;}} if(fromCurrent){{nextTask();}} else alert('已经没有未打分的空了');}}
async function deleteCurrentJob(){{if(!confirm('确定删除当前任务吗？删除后手机端将看不到这次上传内容。'))return; const data=await api(`/api/jobs/${{jobId}}/delete`,{{method:'POST'}}); if(data.ok){{alert('已删除当前任务'); location.href='/';}} else alert(data.error||'删除失败');}}
document.addEventListener('keydown', (e)=>{{if(!tasks.length)return; if(e.ctrlKey||e.metaKey||e.altKey)return; const t=tasks[idx]; const choices=(t.score_choices||[0,1]).map(v=>String(v)); if(choices.includes(e.key)){{e.preventDefault(); saveScore(Number(e.key)); return;}} if(e.key==='ArrowRight'||e.key==='Enter'){{e.preventDefault(); nextPending(false); return;}} if(e.key==='ArrowLeft'){{e.preventDefault(); prevTask(); return;}} if(e.key==='ArrowDown'){{e.preventDefault(); nextTask(); return;}} if(e.key==='ArrowUp'){{e.preventDefault(); prevTask(); return;}}}});
load();
</script></body></html>"""
        self.send_html(body)

    @locked_data
    def api_get_job(self, path):
        parts = path.split('/')
        if len(parts) >= 5 and parts[-1] == 'scores':
            job_id = parts[3]
            scores = read_scores(job_dir(job_id) / 'scores.json', {'scores': {}})
            return self.send_json({'ok': True, **scores})
        if len(parts) >= 6 and parts[4] == 'image':
            job_id = parts[3]
            rel = '/'.join(parts[5:])
            return self.send_job_image(job_id, rel)
        job_id = parts[3] if len(parts) > 3 else ''
        folder = job_dir(job_id)
        manifest = read_json(folder / 'manifest.json', {})
        scores = read_scores(folder / 'scores.json', {'scores': {}})
        if not manifest:
            return self.send_json({'ok': False, 'error': 'job not found'}, 404)
        return self.send_json({'ok': True, 'manifest': manifest, 'scores': scores.get('scores') or {}})

    @locked_data
    def send_job_image(self, job_id, rel):
        folder = job_dir(job_id)
        safe_rel = posixpath.normpath('/' + urllib.parse.unquote(rel)).lstrip('/')
        target = (folder / safe_rel).resolve()
        if (not target.is_relative_to(folder.resolve()) or not target.is_file()
                or target.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tif', '.tiff'}):
            return self.send_json({'ok': False, 'error': 'image not found'}, 404)
        raw = target.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    @locked_data
    def api_upload(self):
        raw = self.read_body(MAX_UPLOAD_BYTES)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)
        del raw
        try:
            with zipfile.ZipFile(tmp_path, 'r') as zf:
                infos = zf.infolist()
                if len(infos) > MAX_ARCHIVE_FILES or sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
                    raise ValueError('任务包解压后超过限制')
                seen = set()
                for info in infos:
                    name = info.filename
                    pieces = name.rstrip('/').split('/')
                    if (not name or name.startswith('/') or '\\' in name or ':' in name
                            or any(p in ('', '.', '..') or p.rstrip(' .') != p for p in pieces)
                            or name.casefold() in seen or (info.external_attr >> 16) & 0o170000 == 0o120000):
                        raise ValueError('任务包包含非法路径或重复文件')
                    seen.add(name.casefold())
                if zf.getinfo('manifest.json').file_size > 16 * 1024 * 1024:
                    raise ValueError('任务清单过大')
                manifest = json.loads(zf.read('manifest.json').decode('utf-8-sig'))
                if not isinstance(manifest, dict) or not manifest.get('job_id'):
                    raise ValueError('任务缺少 ID')
                job_id = safe_id(manifest['job_id'])
                if job_id != manifest['job_id']:
                    raise ValueError('任务 ID 无效')
                tasks = manifest.get('tasks')
                if not isinstance(tasks, list) or not tasks:
                    raise ValueError('任务清单为空或无效')
                valid_pairs = set()
                for task in tasks:
                    if not isinstance(task, dict) or not task.get('entry_key') or not task.get('part_id'):
                        raise ValueError('空位 ID 无效')
                    pair = (str(task['entry_key']), str(task['part_id']))
                    maximum = float(task.get('max_score'))
                    if pair in valid_pairs or not math.isfinite(maximum) or maximum < 0:
                        raise ValueError('空位重复或满分无效')
                    if task.get('image') not in zf.namelist():
                        raise ValueError('任务图片缺失')
                    valid_pairs.add(pair)
                folder = job_dir(job_id)
                old_manifest = read_json(folder / 'manifest.json', {})
                if old_manifest and any(old_manifest.get(key) != manifest.get(key)
                                        for key in ('session_id', 'template_key', 'grading_context')):
                    raise ValueError('任务ID已绑定另一测试或答案版本，请使用新的任务ID')
                old_scores = read_scores(folder / 'scores.json', {'scores': {}})
                old_tasks = {(str(t.get('entry_key')), str(t.get('part_id'))): t
                             for t in old_manifest.get('tasks', [])}
                new_tasks = {(str(t.get('entry_key')), str(t.get('part_id'))): t for t in tasks}
                preserved = {'version': 1, 'job_id': job_id, 'scores': {},
                             'updated_at': old_scores.get('updated_at') or ''}
                image_matches = {}
                for entry_key, records in old_scores.get('scores', {}).items():
                    for part_id, record in records.items():
                        pair = (str(entry_key), str(part_id))
                        previous_task = old_tasks.get(pair, {})
                        current_task = new_tasks.get(pair, {})
                        image_name = current_task.get('image') or ''
                        if image_name and image_name not in image_matches:
                            old_image = folder / image_name
                            image_matches[image_name] = (old_image.is_file()
                                and old_image.resolve().is_relative_to(folder.resolve())
                                and hashlib.sha256(old_image.read_bytes()).digest()
                                == hashlib.sha256(zf.read(image_name)).digest())
                        if pair in valid_pairs and previous_task and all(
                                previous_task.get(key) == current_task.get(key)
                                for key in ('expected_answer', 'max_score', 'image', 'score_id', 'student_name')) and image_matches.get(image_name):
                            preserved['scores'].setdefault(entry_key, {})[part_id] = record
                # Verify/extract completely before replacing the existing job.
                manifest['revision'] = secrets.token_hex(16)
                with tempfile.TemporaryDirectory(prefix='.upload-', dir=DATA_DIR) as staging:
                    stage = Path(staging) / 'new'
                    stage.mkdir()
                    for info in infos:
                        target = stage / info.filename
                        if not target.resolve().is_relative_to(stage.resolve()):
                            raise ValueError('任务文件越界')
                        if info.is_dir():
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(info) as source, target.open('wb') as output:
                                shutil.copyfileobj(source, output, 1024 * 1024)
                    write_json(stage / 'manifest.json', manifest)
                    write_json(stage / 'scores.json', preserved)
                    folder.parent.mkdir(parents=True, exist_ok=True)
                    # Keep rollback outside staging so a failed restore cannot delete it.
                    backup = DATA_DIR / ('.previous-' + job_id + '-' + secrets.token_hex(8))
                    existed = folder.exists()
                    if existed:
                        folder.rename(backup)
                    try:
                        stage.rename(folder)
                    except OSError:
                        if existed:
                            backup.rename(folder)
                        raise
                    if existed:
                        shutil.rmtree(backup)
            scheme = 'https' if self.headers.get('X-Forwarded-Proto') == 'https' else 'http'
            host = self.headers.get('Host') or f'127.0.0.1:{PORT}'
            return self.send_json({'ok': True, 'job_id': job_id, 'job_url': f'{scheme}://{host}/job/{job_id}'})
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    @locked_data
    def api_save_score(self, path):
        parts = path.split('/')
        job_id = parts[3] if len(parts) > 3 else ''
        folder = job_dir(job_id)
        if not (folder / 'manifest.json').exists():
            return self.send_json({'ok': False, 'error': 'job not found'}, 404)
        data = json.loads(self.read_body(65536).decode('utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('评分请求必须为对象')
        entry_key = str(data.get('entry_key') or '')
        part_id = str(data.get('part_id') or '')
        manifest = read_json(folder / 'manifest.json', {})
        if manifest.get('revision') and data.get('job_revision') != manifest['revision']:
            return self.send_json({'ok': False, 'error': '任务已重新上传，请刷新页面后再评分'}, 409)
        task = next((t for t in manifest.get('tasks', [])
                     if str(t.get('entry_key')) == entry_key and str(t.get('part_id')) == part_id), None)
        if task is None:
            return self.send_json({'ok': False, 'error': '空位不属于当前任务'}, 400)
        score = data.get('score')
        if isinstance(score, bool) or score is None:
            raise ValueError('分数无效')
        score, maximum = float(score), float(task['max_score'])
        if not math.isfinite(score) or not math.isfinite(maximum) or not 0 <= score <= maximum:
            return self.send_json({'ok': False, 'error': '分数必须在 0 和题目满分之间'}, 400)
        scores_path = folder / 'scores.json'
        payload = read_scores(scores_path, {'version': 1, 'job_id': job_id, 'scores': {}})
        entry_scores = payload.setdefault('scores', {}).setdefault(entry_key, {})
        entry_scores[part_id] = {
            'score': score, 'max_score': maximum,
            **{key: task.get(key) or '' for key in ('label', 'score_id', 'student_name', 'file')},
            'manual_graded': True,
            'updated_at': datetime.now().isoformat(timespec='seconds'),
        }
        payload['updated_at'] = datetime.now().isoformat(timespec='seconds')
        write_json(scores_path, payload)
        self.send_json({'ok': True})

    @locked_data
    def api_regrade_part(self, path):
        parts = path.split('/')
        job_id = parts[3] if len(parts) > 3 else ''
        folder = job_dir(job_id)
        manifest_path = folder / 'manifest.json'
        if not manifest_path.exists():
            return self.send_json({'ok': False, 'error': 'job not found'}, 404)
        data = json.loads(self.read_body(65536).decode('utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('请求必须为对象')
        part_id = str(data.get('part_id') or '').strip()
        new_expected_answer = str(data.get('new_expected_answer') or '').strip()
        manifest = read_json(manifest_path, {})
        updated_count = 0
        for task in manifest.get('tasks', []):
            if str(task.get('part_id')) == part_id:
                task['expected_answer'] = new_expected_answer
                updated_count += 1
        write_json(manifest_path, manifest)
        return self.send_json({
            'ok': True,
            'job_id': job_id,
            'part_id': part_id,
            'updated_tasks': updated_count,
            'new_expected_answer': new_expected_answer,
        })

    @locked_data
    def api_delete_job(self, path):
        parts = path.split('/')
        job_id = parts[3] if len(parts) > 3 else ''
        folder = job_dir(job_id)
        if folder.exists():
            shutil.rmtree(folder)
        self.send_json({'ok': True, 'deleted': safe_id(job_id)})

    @locked_data
    def api_delete_all_jobs(self):
        root = DATA_DIR / 'jobs'
        deleted = 0
        if root.exists():
            for folder in root.iterdir():
                if folder.is_dir():
                    shutil.rmtree(folder)
                    deleted += 1
        self.send_json({'ok': True, 'deleted_count': deleted})


if __name__ == '__main__':
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f'Manual grading web server: http://{HOST}:{PORT}')
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
