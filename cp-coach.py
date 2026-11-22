#!/usr/bin/env python3
"""CP Tools desktop and local accountability tools (not a security boundary)."""
import argparse
import glob
import importlib.util
import json
import os
from pathlib import Path
import signal
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from urllib.parse import urlsplit
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
ICON = ROOT / 'cp-coach.svg'
spec = importlib.util.spec_from_file_location('cp_tools_server', ROOT / 'cp-tools-server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
helper_spec = importlib.util.spec_from_loader('hosts_helper', __import__('importlib.machinery', fromlist=['SourceFileLoader']).SourceFileLoader('hosts_helper', str(ROOT / 'cp-coach-hosts-helper')))
helper = importlib.util.module_from_spec(helper_spec)
helper_spec.loader.exec_module(helper)
DOMAINS = set(helper.DOMAINS)
INSTALLED_HELPER = '/usr/local/lib/cp-coach/cp-coach-hosts-helper'
AGENT_NAMES = set('claude devin codex gemini aider aider-chat copilot opencode cursor-agent goose crush amp qwen qwen-code llm ollama chatgpt lm-studio cursor windsurf jan'.split()) | {'lm studio'}
AGENT_PACKAGES = {('@anthropic-ai', 'claude-code'), ('@openai', 'codex'),
                  ('@google', 'gemini-cli'), ('@github', 'copilot'),
                  ('@sourcegraph', 'amp'), ('@qwen-code', 'qwen-code')}


def agent_match(executable, argv):
    def candidate(value):
        path = Path(value.lower())
        if path.name in AGENT_NAMES:
            return path.name
        if path.name in ('claude-desktop', 'chatgpt-desktop'):
            return path.name
        parts = path.parts
        for scope, package in AGENT_PACKAGES:
            if any(parts[i:i + 2] == (scope, package) for i in range(len(parts) - 1)):
                return package
        return None

    if not argv:
        return candidate(executable)
    # VS Code's web IDE server is not the Devin CLI agent.
    if Path(argv[0]).name == 'devin' and len(argv) > 1 and argv[1] == 'serve-web':
        return None
    name = candidate(executable) or candidate(argv[0])
    if name:
        return name
    interpreter = Path(executable or argv[0]).name.lower()
    if interpreter in ('node', 'nodejs', 'bash', 'sh', 'dash', 'zsh', 'python', 'python3') or interpreter.startswith('python3.'):
        if len(argv) > 1 and not argv[1].startswith('-'):
            return candidate(argv[1])
        if interpreter.startswith('python') and len(argv) > 2 and argv[1] == '-m' and argv[2] in ('aider', 'llm', 'goose'):
            return argv[2]
    if Path(argv[0]).name == 'gh' and len(argv) > 1 and argv[1] == 'copilot':
        return 'gh copilot'
    return None


def process_info(path):
    pid = int(path.name)
    if pid == os.getpid() or path.stat().st_uid != os.getuid():
        return None
    fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
    if fields[0] == 'Z':
        return None
    argv = (path / 'cmdline').read_bytes().decode(errors='replace').rstrip('\0').split('\0')
    executable = os.readlink(path / 'exe')
    name = agent_match(executable, argv)
    if not name:
        return None
    return dict(pid=pid, start=fields[19], process=name, cmdline=' '.join(argv)[:400])


def ai_url(url):
    try:
        host = (urlsplit(url).hostname or '').lower().rstrip('.')
        return any(host == domain or host.endswith('.' + domain) for domain in DOMAINS)
    except ValueError:
        return False


def history_databases():
    home = Path.home()
    roots = [home / '.config', home / 'snap/chromium/common/chromium',
             home / 'snap/brave/common', home / '.var/app']
    names = ('google-chrome', 'chromium', 'BraveSoftware', 'microsoft-edge', 'vivaldi', 'opera')
    found = set()
    for root in roots:
        if root.exists():
            for path in root.glob('**/History'):
                if any(name.lower() in str(path).lower() for name in names) and path.is_file():
                    found.add((str(path), 'Chromium: ' + str(path.parent)))
    for pattern in ('.mozilla/firefox/*/places.sqlite', 'snap/firefox/common/.mozilla/firefox/*/places.sqlite', '.var/app/org.mozilla.firefox/.mozilla/firefox/*/places.sqlite'):
        for path in home.glob(pattern):
            found.add((str(path), 'Firefox: ' + path.parent.name))
    return sorted(found)


def history_visits(path, start, end):
    with tempfile.TemporaryDirectory(prefix='cp-coach-history-') as temporary:
        copy = Path(temporary) / Path(path).name
        shutil.copy2(path, copy)
        for suffix in ('-wal', '-shm'):
            if os.path.exists(path + suffix):
                shutil.copy2(path + suffix, str(copy) + suffix)
        with sqlite3.connect(str(copy), timeout=1) as db:
            if copy.name == 'places.sqlite':
                rows = db.execute('SELECT p.url, v.visit_date / 1000000.0 FROM moz_places p JOIN moz_historyvisits v ON p.id=v.place_id WHERE v.visit_date BETWEEN ? AND ?', (int(start * 1e6), int(end * 1e6)))
            else:
                rows = db.execute('SELECT u.url, v.visit_time / 1000000.0 - 11644473600 FROM urls u JOIN visits v ON u.id=v.url WHERE v.visit_time BETWEEN ? AND ?', (int((start + 11644473600) * 1e6), int((end + 11644473600) * 1e6)))
            return [(url, timestamp) for url, timestamp in rows if ai_url(url)]


class Blocker:
    def __init__(self):
        self.path = ROOT / 'cp-coach-blocker.json'
        self.lock = threading.RLock()
        self.network = {'active': False, 'reason': 'Ainda não verificado'}
        self.monitor_errors = []
        self.state = {'current': None, 'sessions': []}
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
        self.last_poll = 0
        self.last_process_scan = 0
        self.pending_processes = {}
        self.shutdown = threading.Event()
        with self.lock:
            if self.state['current']:
                self.poll()
            else:
                self.network_command('remove')

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        with open(temporary, 'w') as target:
            json.dump(self.state, target, ensure_ascii=False, indent=2)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

    def network_command(self, action):
        if action == 'status':
            try:
                self.network = {'active': helper.BLOCK in Path('/etc/hosts').read_text(), 'reason': ''}
            except OSError as error:
                self.network = {'active': False, 'reason': str(error)}
            return self.network['active']
        errors = []
        commands = []
        if Path(INSTALLED_HELPER).exists() and shutil.which('sudo'):
            commands.append(['sudo', '-n', INSTALLED_HELPER, action])
        if Path(INSTALLED_HELPER).exists() and shutil.which('pkexec'):
            commands.append(['pkexec', INSTALLED_HELPER, action])
        for command in commands:
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=45)
                if result.returncode == 0:
                    self.network_command('status')
                    return self.network['active'] == (action == 'apply')
                errors.append(result.stderr.strip() or 'Autorização recusada')
            except (OSError, subprocess.TimeoutExpired) as error:
                errors.append(str(error))
        self.network_command('status')
        self.network['reason'] = '; '.join(errors) or 'Helper não instalado. Execute install.sh para ativar o bloqueio de rede.'
        return False

    def violation(self, kind, key, **details):
        session = self.state['current']
        if not any(item['key'] == key for item in session['violations']):
            session['violations'].append(dict(kind=kind, key=key, time=time.time(), **details))

    def scan(self, end):
        session = self.state['current']
        self.monitor_errors = []
        for path, browser in history_databases():
            try:
                for url, timestamp in history_visits(path, session['start'], end):
                    self.violation('histórico', f'{path}:{timestamp}:{url}', browser=browser, url=url, visit_time=timestamp)
            except (OSError, sqlite3.Error) as error:
                self.monitor_errors.append(f'{browser}: {error}')
    def signal_process(self, info, sig):
        path = Path('/proc') / str(info['pid'])
        current = process_info(path)
        if not current or current['start'] != info['start']:
            return False
        if hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'):
            fd = os.pidfd_open(info['pid'])
            try:
                current = process_info(path)
                if not current or current['start'] != info['start']:
                    return False
                signal.pidfd_send_signal(fd, sig)
            finally:
                os.close(fd)
        else:
            os.kill(info['pid'], sig)
        return True

    def scan_processes(self):
        session = self.state['current']
        if not session or session['mode'] not in ('practice-no-ai', 'contest') or (session.get('deadline') and time.time() >= session['deadline']):
            self.pending_processes.clear()
            return
        now = time.monotonic()
        if now - self.last_process_scan >= 5:
            self.last_process_scan = now
            for path in Path('/proc').glob('[0-9]*'):
                try:
                    info = process_info(path)
                    if not info:
                        continue
                    key = f"process:{info['pid']}:{info['start']}"
                    if key not in self.pending_processes and self.signal_process(info, signal.SIGTERM):
                        self.violation('processo', key, **info, action='SIGTERM enviado', message='A terminar processo de IA.')
                        self.pending_processes[key] = (now + 3, info)
                except (OSError, ValueError) as error:
                    if isinstance(error, PermissionError):
                        self.monitor_errors.append(f'Não foi possível terminar {path.name}: {error}')
        for key, (deadline, info) in list(self.pending_processes.items()):
            try:
                current = process_info(Path('/proc') / str(info['pid']))
                alive = current and current['start'] == info['start']
                if alive and now < deadline:
                    continue
                if alive:
                    self.signal_process(info, signal.SIGKILL)
                for item in session['violations']:
                    if item['key'] == key:
                        item.update(action='terminado', message='Processo de IA terminado' + (' (SIGKILL enviado).' if alive else '.'))
                del self.pending_processes[key]
            except (FileNotFoundError, ProcessLookupError):
                for item in session['violations']:
                    if item['key'] == key:
                        item.update(action='terminado', message='Processo de IA terminado.')
                del self.pending_processes[key]
            except OSError as error:
                self.monitor_errors.append(f'Falha ao terminar processo {info["pid"]}: {error}')
                del self.pending_processes[key]

    def finish(self):
        session = self.state['current']
        end = min(time.time(), session.get('deadline') or time.time())
        self.scan(end)
        session['end'] = end
        self.state['sessions'].append(session)
        self.state['current'] = None
        self.pending_processes.clear()
        self.network_command('remove')
        self.save()

    def poll(self):
        with self.lock:
            session = self.state['current']
            if not session:
                return
            if session.get('deadline') and time.time() >= session['deadline']:
                self.finish()
                return
            if not self.network_command('status'):
                self.violation('bloqueio', 'tamper:' + str(int(time.time() // 30)), message='Bloco de rede ausente; tentativa de reposição.')
                self.network_command('apply')
            self.scan(time.time())
            self.last_poll = time.monotonic()
            self.save()

    def run(self):
        while not self.shutdown.wait(1):
            try:
                with self.lock:
                    current = self.state['current']
                    if current:
                        if (current.get('deadline') and time.time() >= current['deadline']) or time.monotonic() - self.last_poll >= 30:
                            self.poll()
                        self.scan_processes()
                        self.save()
            except Exception as error:
                self.monitor_errors = [str(error)]

    def status(self):
        with self.lock:
            return dict(self.state, network=self.network, monitor_errors=self.monitor_errors, now=time.time())

    def start(self, mode, minutes):
        with self.lock:
            if self.state['current']:
                raise ValueError('Termine a sessão atual primeiro.')
            if mode not in ('practice-ai', 'practice-no-ai', 'contest'):
                raise ValueError('Modo inválido.')
            if mode == 'practice-ai':
                self.network_command('remove')
                return
            if mode == 'contest' and (not isinstance(minutes, (int, float)) or not 1 <= minutes <= 10080):
                raise ValueError('Duração: entre 1 e 10080 minutos.')
            now = time.time()
            self.state['current'] = dict(id=uuid.uuid4().hex, mode=mode, start=now, deadline=now + minutes * 60 if mode == 'contest' else None, violations=[])
            self.save()
            self.network_command('apply')
            self.last_poll = time.monotonic()
            self.save()

    def stop(self, confirmation):
        with self.lock:
            session = self.state['current']
            if not session:
                self.network_command('remove')
                return
            if session['mode'] == 'contest' and time.time() < session['deadline']:
                if confirmation != 'TERMINAR CONTEST':
                    raise ValueError('Escreva TERMINAR CONTEST para terminar antecipadamente (fica registado como violação).')
                self.violation('fim antecipado', 'early-end', message='Contest terminado antecipadamente pelo utilizador.')
            self.finish()


PANEL = r'''
<style>#cp-blocker{position:fixed;bottom:12px;right:12px;width:350px;max-width:90vw;max-height:70vh;overflow:auto;z-index:2147483647;background:#132238;color:#eef5ff;border:1px solid #6584ad;border-radius:12px;padding:14px;font:14px system-ui;box-shadow:0 5px 30px #0008}#cp-blocker button,#cp-blocker select,#cp-blocker input{font:inherit;margin:4px;padding:5px;max-width:95%}#cp-blocker summary{cursor:pointer}#cp-blocker pre{white-space:pre-wrap;font:12px system-ui}</style>
<section id="cp-blocker"><strong>CP Coach · Controlo de IA</strong><div id="cb-status">A carregar…</div><select id="cb-mode"><option value="practice-ai">Prática com IA</option><option value="practice-no-ai">Prática sem IA</option><option value="contest">Contest</option></select><label>Minutos <input id="cb-min" type="number" min="1" max="10080" value="120" style="width:70px"></label><div><button id="cb-start">Iniciar</button><button id="cb-stop">Terminar sessão</button></div><div id="cb-error" role="alert"></div><details><summary>Sessões e violações recentes</summary><pre id="cb-history"></pre></details><small>Bloqueio local de apoio, não infalível. Reinicie o navegador após ativar. Sem captura de ecrã.</small></section>
<script>(()=>{let current;const $=id=>document.getElementById(id);const modes={'practice-ai':'Prática com IA','practice-no-ai':'Prática sem IA',contest:'Contest'};async function refresh(){try{let s=await(await fetch('/blocker/status')).json();current=s.current;let left=current?.deadline?Math.max(0,Math.ceil((current.deadline-Date.now()/1000)/60))+' min restantes':'';$('cb-status').textContent=(modes[current?.mode||'practice-ai'])+' '+left+' · Rede: '+(s.network.active?'bloqueada':'não bloqueada')+(s.network.reason?' — '+s.network.reason:'');$('cb-start').disabled=!!current;$('cb-history').textContent=[...(current?[current]:[]),...s.sessions.slice(-8).reverse()].map(x=>modes[x.mode]+' · '+new Date(x.start*1000).toLocaleString('pt-PT')+'\n'+x.violations.map(v=>'⚠ '+new Date((v.visit_time||v.time)*1000).toLocaleString('pt-PT')+' '+v.kind+' '+(v.url||v.process||v.message||'')).join('\n')).join('\n\n')+(s.monitor_errors.length?'\nAvisos: '+s.monitor_errors.join('\n'):'')}catch(e){$('cb-error').textContent=String(e)}}async function action(path,body){$('cb-error').textContent='';try{let r=await fetch('/blocker/'+path,{method:'POST',headers:{'Content-Type':'application/json','X-CP-Tools':'1'},body:JSON.stringify(body)});let data=await r.json();if(!r.ok)throw Error(data.error);await refresh()}catch(e){$('cb-error').textContent=e.message}}$('cb-start').onclick=()=>action('start',{mode:$('cb-mode').value,minutes:Number($('cb-min').value)});$('cb-stop').onclick=()=>{let confirmation='';if(current?.mode==='contest'&&current.deadline>Date.now()/1000){confirmation=prompt('Terminar antecipadamente é uma violação. Escreva TERMINAR CONTEST:');if(confirmation===null)return}action('stop',{confirmation})};refresh();setInterval(refresh,3000)})();</script>
'''


class Handler(server.Handler):
    blocker = None

    def do_GET(self):
        if not self.local():
            return self.send(403, b'{}')
        path = self.path.split('?')[0]
        if path == '/blocker/status':
            return self.send(200, json.dumps(self.blocker.status(), ensure_ascii=False).encode())
        if path == '/icon.svg':
            return self.send(200, ICON.read_bytes(), 'image/svg+xml')
        if path in ('/', '/cp-tools.html'):
            page = Path(server.PAGE).read_text().replace('</head>', '<link rel="icon" type="image/svg+xml" href="/icon.svg"></head>', 1)
            return self.send(200, page.replace('</body>', PANEL + '</body>').encode(), 'text/html; charset=utf-8')
        super().do_GET()

    def do_POST(self):
        if not self.local() or self.headers.get('X-CP-Tools') != '1':
            return self.send(403, b'{}')
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 4096:
                raise ValueError('Pedido inválido.')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('Esperado um objeto JSON.')
            if self.path == '/blocker/start':
                self.blocker.start(body.get('mode'), body.get('minutes', 120))
            elif self.path == '/blocker/stop':
                self.blocker.stop(body.get('confirmation', ''))
            else:
                return self.send(404, b'{}')
            self.send(200, json.dumps(self.blocker.status(), ensure_ascii=False).encode())
        except (ValueError, TypeError) as error:
            self.send(400, json.dumps({'error': str(error)}, ensure_ascii=False).encode())


def browser_window(url):
    for name in ('chromium', 'chromium-browser', 'google-chrome', 'brave-browser', 'brave'):
        executable = shutil.which(name)
        if executable:
            subprocess.Popen([executable, '--app=' + url, '--class=cp-coach'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    webbrowser.open(url)


def native_window(url):
    try:
        from gi.repository import GLib
        GLib.set_prgname('cp-coach')
    except ImportError:
        pass
    try:
        from qtpy.QtGui import QGuiApplication
        QGuiApplication.setDesktopFileName('cp-coach')
    except ImportError:
        pass
    import webview
    storage = Path.home() / '.local/share/cp-coach/webview'
    storage.mkdir(parents=True, exist_ok=True, mode=0o700)
    webview.create_window('CP Coach — CP Tools', url, width=1280, height=900)
    webview.start(private_mode=False, storage_path=str(storage), icon=str(ICON))


def main():
    parser = argparse.ArgumentParser(description='CP Coach Desktop')
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--serve-only', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    venv = ROOT / '.venv'
    if not args.browser and not args.serve_only and (venv / 'bin/python').exists() and Path(sys.prefix).resolve() != venv.resolve():
        python = str(venv / 'bin/python')
        os.execv(python, [python, str(ROOT / 'cp-coach.py'), *sys.argv[1:]])
    blocker = Blocker()
    Handler.blocker = blocker
    httpd = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    threading.Thread(target=blocker.run, daemon=True).start()
    url = 'http://localhost:%d/' % httpd.server_port
    print('CP Coach em ' + url, flush=True)
    try:
        if not args.browser and not args.serve_only:
            try:
                native_window(url)
                return
            except Exception as error:
                print('Janela nativa indisponível; a abrir navegador: ' + str(error), flush=True)
        if not args.serve_only:
            browser_window(url)
        print('Ctrl+C para parar. Uma sessão ativa mantém o bloqueio.', flush=True)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        blocker.shutdown.set()
        httpd.shutdown()
        httpd.server_close()


if __name__ == '__main__':
    main()
