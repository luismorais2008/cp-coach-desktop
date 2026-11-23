#!/usr/bin/env python3
"""CP Tools desktop and local accountability tools (not a security boundary)."""
import argparse
import base64
import binascii
import configparser
import ctypes
import fcntl
import glob
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import signal
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import warnings
import webbrowser
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('cp_tools_server', ROOT / 'cp-tools-server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
helper_spec = importlib.util.spec_from_loader('hosts_helper', __import__('importlib.machinery', fromlist=['SourceFileLoader']).SourceFileLoader('hosts_helper', str(ROOT / 'cp-coach-hosts-helper')))
helper = importlib.util.module_from_spec(helper_spec)
helper_spec.loader.exec_module(helper)
DOMAINS = set(helper.AI_DOMAINS)
NON_CHAT_HOSTS = helper.NON_CHAT_HOSTS - {'platform.deepseek.com', 'platform.openai.com'}
INSTALLED_HELPER = '/usr/local/lib/cp-coach/cp-coach-hosts-helper'
AGENT_NAMES = set('claude devin codex gemini aider aider-chat copilot opencode cursor-agent goose crush amp qwen qwen-code llm ollama chatgpt lm-studio cursor windsurf jan jan-desktop gpt4all gpt4all-chat msty anythingllm anythingllm-desktop cherry-studio cherry-studio-app chatbox chatbox-ce'.split()) | {'lm studio', 'cherry studio', 'lmstudio', 'cherrystudio'}
AGENT_PACKAGES = {('@anthropic-ai', 'claude-code'), ('@openai', 'codex'),
                  ('@google', 'gemini-cli'), ('@github', 'copilot'),
                  ('@sourcegraph', 'amp'), ('@qwen-code', 'qwen-code')}


def agent_match(executable, argv, appimage=''):
    def candidate(value):
        path = Path(value.lower())
        if path.name in AGENT_NAMES:
            return path.name
        if path.name in ('claude-desktop', 'chatgpt-desktop'):
            return path.name
        if re.fullmatch(r'(jan|lm[- _]?studio|gpt4all|msty|anythingllm|ollama|cherry[- _]?studio|chatbox)(?:[-_ ]v?\d[\w.\-]*)?\.appimage', path.name):
            return path.name
        if re.fullmatch(r'(claude|codex|copilot)(?:-linux-(?:x64|arm64)(?:-(?:musl|gnu))?)?', path.name):
            return path.name
        parts = path.parts
        if any(re.fullmatch(r'\.mount_(jan|lm[-_]?studio|gpt4all|msty|anythingllm|ollama|cherry[-_]?studio|chatbox)[a-z0-9]{6}', part) for part in parts):
            return 'AppImage de IA'
        if 'claude' in parts and any(parts[i:i + 2] == ('claude', 'versions') for i in range(len(parts) - 1)):
            return 'claude'
        for scope, package in AGENT_PACKAGES:
            if any(parts[i:i + 2] == (scope, package) for i in range(len(parts) - 1)):
                return package
        return None

    if not argv:
        return candidate(executable) or candidate(appimage)
    # VS Code's web IDE server is not the Devin CLI agent.
    if Path(argv[0]).name == 'devin' and len(argv) > 1 and argv[1] == 'serve-web':
        return None
    name = candidate(executable) or candidate(argv[0]) or candidate(appimage)
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


def process_record(path):
    pid = int(path.name)
    if pid == os.getpid() or path.stat().st_uid != os.getuid():
        return None
    status = (path / 'status').read_text()
    uids = next(line.split()[1:] for line in status.splitlines() if line.startswith('Uid:'))
    if any(int(uid) != os.getuid() for uid in uids):
        return None
    fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
    if fields[0] == 'Z':
        return None
    argv = (path / 'cmdline').read_bytes().decode(errors='replace').rstrip('\0').split('\0')
    try:
        executable = os.readlink(path / 'exe')
    except PermissionError:
        executable = ''
    appimage = ''
    try:
        appimage = next((entry[9:] for entry in (path / 'environ').read_bytes().decode(errors='replace').split('\0') if entry.startswith('APPIMAGE=')), '')
    except PermissionError:
        pass
    return dict(pid=pid, start=fields[19], ppid=int(fields[1]), executable=executable, argv=argv, appimage=appimage)


def process_info(path):
    record = process_record(path)
    if not record:
        return None
    name = agent_match(record['executable'], record['argv'], record['appimage'])
    if not name:
        return None
    return dict(pid=record['pid'], start=record['start'], process=name, cmdline=' '.join(record['argv'])[:400])


def process_tree(info):
    records = {}
    for path in Path('/proc').glob('[0-9]*'):
        try:
            record = process_record(path)
            if record:
                records[record['pid']] = record
        except (OSError, ValueError, StopIteration):
            pass
    root = records.get(info['pid'])
    if not root or root['start'] != info['start']:
        return []
    members = {info['pid']}
    while True:
        children = {pid for pid, record in records.items() if record['ppid'] in members}
        if children <= members:
            break
        members |= children
    return [dict(pid=pid, start=records[pid]['start'], process=info['process'], cmdline=' '.join(records[pid]['argv'])[:400], descendant=True) for pid in members if pid != info['pid']] + [info]


def ai_url(url):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower().rstrip('.')
        path = parsed.path.lower()
        if parsed.scheme not in ('http', 'https') or host in NON_CHAT_HOSTS:
            return False
        if any(host == domain or host.endswith('.' + domain) for domain in helper.GOOGLE_SEARCH_DOMAINS):
            return (path in ('/', '/search', '/webhp') and '50' in parse_qs(parsed.query).get('udm', [])) or any(path.startswith(prefix) for prefix in helper.GOOGLE_AI_PATHS)
        if any((host == domain or host.endswith('.' + domain)) and path.startswith(prefix) for domain, prefix in helper.EXTRA_AI_PATHS):
            return True
        if host == 'duckduckgo.com' or host.endswith('.duckduckgo.com'):
            return path.startswith('/duckchat') or any(value in ('chat', 'chat-legacy') for value in parse_qs(parsed.query).get('ia', []))
        if host == 'platform.deepseek.com' and any(path == prefix or path.startswith(prefix + '/') for prefix in ('/docs', '/api-docs', '/documentation')):
            return False
        if host == 'platform.openai.com':
            return path == '/playground' or path.startswith('/playground/')
        if host.endswith(('.openai.com', '.anthropic.com')):
            return any(host == domain or host.endswith('.' + domain) for domain in ('chat.openai.com', 'api.openai.com', 'api.anthropic.com', 'console.anthropic.com'))
        return any(host == domain or host.endswith('.' + domain) for domain in DOMAINS)
    except ValueError:
        return False


def history_domain(url):
    host = (urlsplit(url).hostname or '').lower().rstrip('.')
    labels = host.split('.')
    suffix = '.'.join(labels[-2:])
    if suffix in ('co.uk', 'com.cn', 'com.au', 'co.jp', 'com.br', 'com.hk', 'com.tw', 'co.in'):
        return '.'.join(labels[-3:])
    return suffix


def browser_family(browser):
    value = browser.lower()
    for token, name in (('firefox', 'Firefox'), ('brave', 'Brave'), ('edge', 'Edge'), ('vivaldi', 'Vivaldi'), ('opera', 'Opera'), ('google-chrome', 'Chrome'), ('chromium', 'Chromium'), ('chrome', 'Chrome')):
        if token in value:
            return name
    return browser.split(':')[0]


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
    firefox_roots = [home / '.mozilla/firefox', home / '.config/mozilla/firefox',
                     home / 'snap/firefox/common/.mozilla/firefox',
                     home / 'snap/firefox/common/.config/mozilla/firefox',
                     home / '.var/app/org.mozilla.firefox/.mozilla/firefox',
                     home / '.var/app/org.mozilla.firefox/.config/mozilla/firefox']
    profiles = set()
    for root in firefox_roots:
        profiles.update(path.parent for path in root.glob('*/places.sqlite'))
        for name in ('profiles.ini', 'installs.ini'):
            config = configparser.ConfigParser(interpolation=None)
            try:
                config.read(root / name)
                for section in config.values():
                    value = section.get('Path') or section.get('Default')
                    if value:
                        profile = Path(value).expanduser()
                        profiles.add(profile if profile.is_absolute() else root / profile)
            except (OSError, configparser.Error):
                pass
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            argv = (proc / 'cmdline').read_bytes().decode(errors='replace').rstrip('\0').split('\0')
            if Path(argv[0]).name.lower() not in ('firefox', 'firefox-bin', 'firefox-esr'):
                continue
            for i, arg in enumerate(argv):
                if arg.lower() in ('-profile', '--profile') and i + 1 < len(argv):
                    profile = Path(argv[i + 1])
                    profiles.add(profile if profile.is_absolute() else (proc / 'cwd').resolve() / profile)
                elif arg.lower().startswith(('-profile=', '--profile=')):
                    profile = Path(arg.split('=', 1)[1])
                    profiles.add(profile if profile.is_absolute() else (proc / 'cwd').resolve() / profile)
        except (OSError, IndexError):
            pass
    for profile in profiles:
        path = profile / 'places.sqlite'
        if path.is_file():
            found.add((str(path.resolve()), 'Firefox: ' + profile.name))
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


class NetworkWatcher:
    def __init__(self):
        self.paths = [Path('/etc/hosts'), helper.STRICT_STATE] + [path for path, _ in helper.policy_targets()]
        self.previous = self.signature()
        self.fd = -1
        self.watched = set()
        self.nft_monitor = None
        self.nft_retry = 0
        try:
            self.libc = ctypes.CDLL(None, use_errno=True)
            self.libc.inotify_init1.argtypes = [ctypes.c_int]
            self.libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        except (OSError, AttributeError):
            pass

    def signature(self):
        result = []
        for path in self.paths:
            try:
                info = path.stat()
                result.append((info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_mode, info.st_uid, info.st_gid))
            except OSError:
                result.append(None)
        return result

    def wait(self, timeout):
        if self.nft_monitor and self.nft_monitor.poll() is not None:
            self.nft_monitor.stdin.close()
            self.nft_monitor.stdout.close()
            self.nft_monitor = None
        if not self.nft_monitor and time.monotonic() >= self.nft_retry and helper.STRICT_STATE.exists() and Path(INSTALLED_HELPER).is_file() and shutil.which('sudo'):
            self.nft_retry = time.monotonic() + 5
            try:
                self.nft_monitor = subprocess.Popen(['sudo', '-n', INSTALLED_HELPER, 'watch'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            except OSError:
                pass
        if self.fd >= 0:
            for path in self.paths:
                if path.parent not in self.watched and path.parent.is_dir():
                    if self.libc.inotify_add_watch(self.fd, os.fsencode(path.parent), 0xFCE) >= 0:
                        self.watched.add(path.parent)
        descriptors = ([self.fd] if self.fd >= 0 else []) + ([self.nft_monitor.stdout] if self.nft_monitor else [])
        ready = select.select(descriptors, [], [], timeout)[0] if descriptors else []
        nft_changed = False
        if self.nft_monitor and self.nft_monitor.stdout in ready:
            nft_changed = bool(os.read(self.nft_monitor.stdout.fileno(), 65536))
        if self.fd >= 0:
            if self.fd in ready:
                try:
                    while os.read(self.fd, 65536):
                        pass
                except BlockingIOError:
                    pass
        elif not descriptors:
            threading.Event().wait(timeout)
        current = self.signature()
        changed = current != self.previous
        self.previous = current
        return changed or nft_changed

    def close(self):
        if self.nft_monitor:
            self.nft_monitor.stdin.close()
            try:
                self.nft_monitor.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            self.nft_monitor.stdout.close()
        if self.fd >= 0:
            os.close(self.fd)


class Blocker:
    def __init__(self):
        self.path = ROOT / 'cp-coach-blocker.json'
        self.lock = threading.RLock()
        self.network = {'active': False, 'reason': 'Ainda não verificado'}
        self.network_error = ''
        self.monitor_errors = []
        self.state = {'current': None, 'sessions': [], 'idle': {'mode': 'practice-ai', 'minutes': 120}, 'allowed': helper.validate_allowed(helper.DEFAULT_ALLOWED)}
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
        self.state.setdefault('allowed', helper.validate_allowed(helper.DEFAULT_ALLOWED))
        self.state.setdefault('idle', {'mode': 'practice-ai', 'minutes': 120})
        self.state['idle'].pop('strictness', None)
        current = self.state.get('current')
        if current:
            current.setdefault('strictness', 'web')
        if current and current.get('deadline') is None:
            current['minutes'] = current.get('minutes', 120)
            current['deadline'] = current['start'] + current['minutes'] * 60
        self.last_poll = 0
        self.last_process_scan = 0
        self.last_refresh = 0
        self.pending_processes = {}
        self.process_warnings = set()
        self.clients = set()
        self.shutdown = threading.Event()
        with self.lock:
            self.reconcile_emergencies(startup=True)
            if self.state['current']:
                self.poll()
            else:
                self.network_command('remove')
                self.reconcile_emergencies()
            self.rescan_finished()

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        with open(temporary, 'w') as target:
            json.dump(self.state, target, ensure_ascii=False, indent=2)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

    def network_command(self, action):
        current = self.state['current']
        strict = bool(current and current['mode'] != 'practice-ai' and current.get('strictness') == 'strict')
        allowed = current.get('allowed', self.state['allowed']) if strict else None
        if action == 'status':
            if strict or helper.STRICT_STATE.exists():
                try:
                    result = subprocess.run(['sudo', '-n', INSTALLED_HELPER, 'strict-status'], capture_output=True, text=True, timeout=5)
                    if result.returncode:
                        raise OSError(result.stderr.strip())
                    self.network = json.loads(result.stdout)
                    if strict and (self.network.get('allowed') != allowed or self.network.get('uid') != os.getuid()):
                        self.network.update(active=False, reason='A firewall não corresponde à lista desta sessão.')
                    elif not strict:
                        self.network['reason'] = 'Bloqueio residual de uma sessão anterior. ' + self.network.get('reason', '')
                except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                    self.network = {'active': False, 'nft': False, 'reason': str(error)}
                if not self.network['active'] and self.network_error:
                    self.network['reason'] += ' ' + self.network_error
                return self.network['active']
            try:
                hosts = helper.BLOCK in Path('/etc/hosts').read_text()
                policies = helper.policy_status()
                self.network = {'active': hosts and policies, 'hosts': hosts, 'policies': policies, 'reason': ''}
                if hosts != policies:
                    self.network['reason'] = 'Bloqueio incompleto: hosts ou políticas do navegador ausentes. Reinicia o navegador após a reposição.'
            except OSError as error:
                self.network = {'active': False, 'reason': str(error)}
            return self.network['active']
        errors = []
        if action == 'apply' and strict:
            action = 'apply-strict'
        commands = []
        if Path(INSTALLED_HELPER).exists() and shutil.which('sudo'):
            commands.append(['sudo', '-n', INSTALLED_HELPER, action])
        if Path(INSTALLED_HELPER).exists() and shutil.which('pkexec'):
            commands.append(['pkexec', INSTALLED_HELPER, action])
        for command in commands:
            try:
                payload = None
                if action in ('apply', 'apply-strict') and current and current['mode'] != 'practice-ai':
                    payload = dict(restriction={key: current[key] for key in ('id', 'start', 'deadline', 'mode')})
                    if strict:
                        payload['allowed'] = allowed
                result = subprocess.run(command, input=json.dumps(payload) if payload else None, capture_output=True, text=True, timeout=45)
                if result.returncode == 0:
                    self.network_error = ''
                    if payload:
                        current['restriction_recorded'] = True
                    self.network_command('status')
                    if action in ('apply-strict', 'refresh'):
                        self.last_refresh = time.monotonic()
                    return self.network['active'] == (action != 'remove')
                errors.append(result.stderr.strip() or 'Autorização recusada')
            except (OSError, subprocess.TimeoutExpired) as error:
                errors.append(str(error))
        self.network_command('status')
        self.network_error = '; '.join(errors) or 'Helper não instalado. Execute install.sh para ativar o bloqueio de rede.'
        self.network['reason'] = self.network_error
        return False

    def session_control(self, action='journal', payload=None):
        if not Path(INSTALLED_HELPER).is_file() or not shutil.which('sudo'):
            return None
        try:
            result = subprocess.run(['sudo', '-n', INSTALLED_HELPER, action], input=json.dumps(payload) if payload else None, capture_output=True, text=True, timeout=10)
            if result.returncode:
                self.monitor_errors = [result.stderr.strip() or 'Não foi possível ler o diário de emergência.']
                return None
            return json.loads(result.stdout) if action == 'journal' else True
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            self.monitor_errors = [str(error)]
            return None

    def reconcile_emergencies(self, startup=False):
        journal = self.session_control()
        seen = self.state.setdefault('journal_seen', [])
        changed = False
        for event in (journal or {}).get('events', []):
            if event['id'] in seen:
                continue
            current = self.state['current']
            session = current if current and current['id'] == event['session'] else next((s for s in self.state['sessions'] if s['id'] == event['session']), None)
            if session is None:
                session = dict(id=event['session'], mode=event.get('mode', 'practice-no-ai'), strictness='strict' if event.get('strict') else 'web', start=event['start'], deadline=event['deadline'], minutes=(event['deadline']-event['start'])/60, violations=[], history_rescans=[])
                self.state['sessions'].append(session)
            self.violation('saída de emergência', 'journal:' + event['id'], session=session, message=event['how'], event_time=event['time'])
            end = min(event['time'], session['deadline'])
            if session is current:
                self.finish('emergency', end_time=end)
            else:
                session.update(end=end, end_reason='emergency')
            seen.append(event['id'])
            changed = True
        self.state['journal_seen'] = seen[-256:]
        current = self.state['current']
        if startup and current and current['mode'] != 'practice-ai':
            heartbeat = current.get('heartbeat')
            now = time.time()
            gap = now - heartbeat if heartbeat else 0
            missing = not self.network_command('status')
            if missing or gap > 10:
                interval = f'Intervalo: {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(heartbeat or current["start"]))} — {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))}; {gap:.1f} s sem heartbeat.'
                self.violation('saída de emergência' if missing else 'monitorização interrompida', 'monitor-gap:' + str(heartbeat or current['start']), message=('Bloqueio ausente ao retomar. ' if missing else 'Bloqueio intacto; sessão continua. ') + interval, gap_start=heartbeat or current['start'], gap_end=now, gap_seconds=gap)
                if not missing:
                    current['broken'] = True
                current['heartbeat'] = now
                changed = True
            if missing:
                if current.get('restriction_recorded'):
                    self.session_control('interrupt', {'id': current['id']})
                self.finish('emergency')
        if changed:
            self.save()

    def violation(self, kind, key, session=None, **details):
        session = self.state['current'] if session is None else session
        if not any(item['key'] == key for item in session['violations']):
            session['violations'].append(dict(kind=kind, key=key, time=time.time(), **details))

    def scan(self, end):
        self.monitor_errors = []
        self.scan_history(self.state['current'], end)

    def scan_history(self, session, end):
        if session['mode'] == 'practice-ai':
            return
        sources = set(history_databases())
        sources.update((path, browser) for path, browser in session.get('history_sources', []))
        session['history_sources'] = sorted(sources)
        for path, browser in sorted(sources):
            try:
                for url, timestamp in sorted(history_visits(path, session['start'], end), key=lambda visit: visit[1]):
                    domain, family = history_domain(url), browser_family(browser)
                    duplicate = any(item['kind'] == 'histórico' and browser_family(item.get('browser', '')) == family and history_domain(item.get('url', '')) == domain and abs(item.get('visit_time', item['time']) - timestamp) < 120 for item in session['violations'])
                    if not duplicate:
                        self.violation('histórico', f'{path}:{timestamp}:{url}', session=session, browser=browser, domain=domain, url=url, visit_time=timestamp)
            except (OSError, sqlite3.Error) as error:
                self.monitor_errors.append(f'{browser}: {error}')

    def rescan_finished(self):
        now, changed = time.time(), False
        for session in self.state['sessions']:
            if session.get('end') is None:
                continue
            if 'history_rescans' not in session:
                session['history_rescans'] = [session['end'] + 15, session['end'] + 60]
                changed = True
            if any(deadline <= now for deadline in session['history_rescans']):
                self.scan_history(session, session['end'])
                session['history_rescans'] = [deadline for deadline in session['history_rescans'] if deadline > now]
                changed = True
        if changed:
            self.save()

    def signal_process(self, info, sig):
        path = Path('/proc') / str(info['pid'])
        inspect = process_record if info.get('descendant') else process_info
        current = inspect(path)
        if not current or current['start'] != info['start']:
            return False
        if hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'):
            fd = os.pidfd_open(info['pid'])
            try:
                current = inspect(path)
                if not current or current['start'] != info['start']:
                    return False
                signal.pidfd_send_signal(fd, sig)
            finally:
                os.close(fd)
        else:
            os.kill(info['pid'], sig)
        return True

    def process_warning(self, info, error):
        key = (info['pid'], info['start'], str(error))
        if key not in self.process_warnings:
            self.process_warnings.add(key)
            self.monitor_errors.append(f'Não foi possível terminar {info["pid"]}: {error}')

    def scan_processes(self):
        session = self.state['current']
        if not session or session['mode'] not in ('practice-no-ai', 'contest') or (session.get('deadline') and time.time() >= session['deadline']):
            self.pending_processes.clear()
            return
        now = time.monotonic()
        if now - self.last_process_scan >= 1:
            self.last_process_scan = now
            for path in Path('/proc').glob('[0-9]*'):
                try:
                    info = process_info(path)
                    if not info:
                        continue
                    key = f"process:{info['pid']}:{info['start']}"
                    if key not in self.pending_processes:
                        members = process_tree(info) or [info]
                        info['tree'] = [dict(member) for member in members if member['pid'] != info['pid']]
                        signalled = False
                        for member in members:
                            try:
                                signalled = self.signal_process(member, signal.SIGTERM) or signalled
                            except (FileNotFoundError, ProcessLookupError):
                                pass
                            except PermissionError as error:
                                self.process_warning(member, error)
                        if not signalled:
                            continue
                        self.violation('processo', key, **info, action='SIGTERM enviado', message='A terminar processo de IA.')
                        self.pending_processes[key] = (now + 3, info)
                except (OSError, ValueError) as error:
                    pass
        for key, (deadline, info) in list(self.pending_processes.items()):
            try:
                tracked = info.get('tree', []) + [info]
                for parent in list(tracked):
                    tracked += [member for member in process_tree(parent) if all(member['pid'] != old['pid'] for old in tracked)]
                info['tree'] = [dict(member) for member in tracked if member['pid'] != info['pid']]
                alive = []
                for member in tracked:
                    try:
                        current = (process_record if member.get('descendant') else process_info)(Path('/proc') / str(member['pid']))
                        if current and current['start'] == member['start']:
                            alive.append(member)
                    except (FileNotFoundError, ProcessLookupError):
                        pass
                if alive and now < deadline:
                    continue
                if alive:
                    for member in alive:
                        self.signal_process(member, signal.SIGKILL)
                    self.pending_processes[key] = (now + 1, info)
                    for item in session['violations']:
                        if item['key'] == key:
                            item.update(action='SIGKILL enviado', message='A verificar a saída de toda a árvore de processos.')
                    continue
                for item in session['violations']:
                    if item['key'] == key:
                        item.update(action='terminado', message='Processo de IA e descendentes terminados (saída verificada em /proc).')
                del self.pending_processes[key]
            except (FileNotFoundError, ProcessLookupError):
                continue
            except OSError as error:
                self.process_warning(info, error)

    def finish(self, reason='time', end_time=None):
        session = self.state['current']
        end = end_time if end_time is not None else min(time.time(), session.get('deadline') or time.time())
        self.scan(end)
        session['end'] = end
        session['end_reason'] = reason
        session['history_rescans'] = [end + 15, end + 60] if session['mode'] != 'practice-ai' else []
        self.state['sessions'].append(session)
        self.state['current'] = None
        self.pending_processes.clear()
        if session.get('restriction_recorded'):
            self.session_control('finish', {'id': session['id']})
            self.network_command('status')
        else:
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
            if session['mode'] == 'practice-ai':
                self.network_command('status')
                self.last_poll = time.monotonic()
                return
            self.check_network()
            self.scan(time.time())
            self.last_poll = time.monotonic()
            self.save()

    def check_network(self):
        self.reconcile_emergencies()
        if not self.state['current']:
            return
        if not Path(INSTALLED_HELPER).is_file():
            self.network_command('status')
            self.network['reason'] = 'Helper não instalado; bloqueio de rede indisponível. Execute install.sh.'
        elif not self.network_command('status'):
            self.violation('bloqueio', 'tamper:' + str(int(time.time() // 30)), message='Hosts ou políticas de navegador ausentes; tentativa de reposição.')
            self.network_command('apply')
        elif self.state['current'].get('strictness') == 'strict' and time.monotonic() - self.last_refresh >= 180:
            self.network_command('refresh')

    def run(self):
        watcher = NetworkWatcher()
        try:
            while not self.shutdown.is_set():
                changed = watcher.wait(1)
                if self.shutdown.is_set():
                    break
                try:
                    with self.lock:
                        current = self.state['current']
                        if current:
                            current['heartbeat'] = time.time()
                            if (current.get('deadline') and time.time() >= current['deadline']) or time.monotonic() - self.last_poll >= 5:
                                self.poll()
                            elif current['mode'] != 'practice-ai' and (changed or current.get('strictness') == 'strict'):
                                self.check_network()
                            self.scan_processes()
                            self.save()
                        self.rescan_finished()
                except Exception as error:
                    self.monitor_errors = [str(error)]
        finally:
            watcher.close()

    def status(self):
        with self.lock:
            self.network_command('status')
            return dict(self.state, network=self.network, monitor_errors=self.monitor_errors, now=time.time())

    @staticmethod
    def validate_options(mode, minutes):
        if mode not in ('practice-ai', 'practice-no-ai', 'contest'):
            raise ValueError('Modo inválido.')
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not 1 <= minutes <= 10080:
            raise ValueError('Duração: entre 1 e 10080 minutos.')

    def set_preferences(self, mode, minutes, strictness='strict'):
        with self.lock:
            if self.state['current']:
                raise ValueError('Termine a sessão atual primeiro.')
            self.validate_options(mode, minutes)
            self.validate_strictness(strictness)
            self.state['idle'] = dict(mode=mode, minutes=minutes)
            self.save()

    @staticmethod
    def validate_strictness(strictness):
        if strictness not in ('strict', 'web'):
            raise ValueError('Escolhe Só sites permitidos ou Pesquisa web permitida.')

    def set_allowed(self, value):
        with self.lock:
            if self.state['current']:
                raise ValueError('Termina a sessão atual antes de editar a lista permitida.')
            self.state['allowed'] = helper.validate_allowed(value)
            self.save()

    def start(self, mode, minutes, strictness='strict'):
        with self.lock:
            if self.state['current']:
                raise ValueError('Termine a sessão atual primeiro.')
            self.validate_options(mode, minutes)
            self.validate_strictness(strictness)
            self.state['idle'] = dict(mode=mode, minutes=minutes)
            now = time.time()
            self.state['current'] = dict(id=uuid.uuid4().hex, mode=mode, strictness=strictness, allowed=list(self.state['allowed']), start=now, heartbeat=now, minutes=minutes, deadline=now + minutes * 60, violations=[])
            self.save()
            self.network_command('remove' if mode == 'practice-ai' else 'apply')
            self.state['current']['heartbeat'] = time.time()
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
            self.finish('time' if session.get('deadline') and time.time() >= session['deadline'] else 'early' if session['mode'] == 'contest' else 'manual')


PANEL = (ROOT / 'cp-coach-panel.html').read_text()


class Handler(server.Handler):
    blocker = None
    native_downloads = False

    def do_GET(self):
        if not self.local():
            return self.send(403, b'{}')
        path = self.path.split('?')[0]
        if path == '/blocker/status':
            return self.send(200, json.dumps(self.blocker.status(), ensure_ascii=False).encode())
        if path in ('/', '/cp-tools.html'):
            page = Path(server.PAGE).read_text()
            page = page.replace('<title>CP Tools</title>', '<title>CP Coach — CP Tools</title>', 1)
            page = page.replace('  cur = tab;\n', '  cur = tab;\n  show(tab);\n', 1)
            if self.native_downloads or parse_qs(urlsplit(self.path).query).get('desktop') == ['1']:
                bridge = (ROOT / 'cp-coach-downloads.js').read_text()
                page = page.replace('</head>', '<script>' + bridge + '</script></head>')
            return self.send(200, page.replace('</body>', PANEL + '</body>').encode(), 'text/html; charset=utf-8')
        super().do_GET()

    def do_POST(self):
        if not self.local() or self.headers.get('X-CP-Tools') != '1':
            return self.send(403, b'{}')
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 32768:
                raise ValueError('Pedido inválido.')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('Esperado um objeto JSON.')
            if self.path == '/blocker/start':
                self.blocker.start(body.get('mode'), body.get('minutes', 120), body.get('strictness', 'strict'))
            elif self.path == '/blocker/preferences':
                self.blocker.set_preferences(body.get('mode'), body.get('minutes'), body.get('strictness', 'strict'))
            elif self.path == '/blocker/allowed':
                self.blocker.set_allowed(helper.DEFAULT_ALLOWED if body.get('reset') is True else body.get('allowed'))
            elif self.path == '/blocker/stop':
                self.blocker.stop(body.get('confirmation', ''))
            elif self.path in ('/blocker/background', '/blocker/foreground'):
                with self.blocker.lock:
                    if self.path.endswith('foreground'):
                        pid = body.get('pid')
                        if isinstance(pid, int) and Path('/proc', str(pid)).stat().st_uid == os.getuid():
                            self.blocker.clients.add(pid)
                    session = self.blocker.state['current']
                    if session:
                        event = 'background' if self.path.endswith('background') else 'resumed'
                        session.setdefault('lifecycle', []).append({'event': event, 'time': time.time()})
                        self.blocker.save()
            else:
                return self.send(404, b'{}')
            self.send(200, json.dumps(self.blocker.status(), ensure_ascii=False).encode())
        except (ValueError, TypeError) as error:
            self.send(400, json.dumps({'error': str(error)}, ensure_ascii=False).encode())


def browser_window(url):
    for name in ('chromium', 'chromium-browser', 'google-chrome', 'brave-browser', 'brave'):
        executable = shutil.which(name)
        if executable:
            subprocess.Popen([executable, '--no-first-run', '--no-default-browser-check', '--app=' + url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    webbrowser.open(url)


def activate_browser(url):
    for _ in range(50):
        result = subprocess.run(['wmctrl', '-a', 'CP Coach — CP Tools'], capture_output=True, timeout=2)
        if result.returncode == 0:
            return
        threading.Event().wait(.1)
    browser_window(url)


class DesktopAPI:
    def __init__(self):
        self._window = None
        self._save_lock = threading.Lock()

    def save_download(self, name, encoded):
        try:
            if not isinstance(name, str) or not isinstance(encoded, str) or len(encoded) > 48 * 1024 * 1024:
                raise ValueError('Transferência inválida ou demasiado grande (máximo 32 MiB).')
            content = base64.b64decode(encoded, validate=True)
            if len(content) > 32 * 1024 * 1024:
                raise ValueError('Transferência demasiado grande (máximo 32 MiB).')
            filename = ''.join(c for c in name.replace('\\', '/').split('/')[-1] if ord(c) >= 32).strip()
            if filename in ('', '.', '..'):
                filename = 'download'
            import webview
            directory = Path.home() / 'Downloads'
            if not directory.is_dir():
                directory = Path.home()
            with self._save_lock:
                selected = self._window.create_file_dialog(webview.FileDialog.SAVE, directory=str(directory), save_filename=filename)
                if not selected:
                    return {'cancelled': True}
                destination = Path(selected if isinstance(selected, str) else selected[0])
                destination.write_bytes(content)
            return {'saved': True, 'name': destination.name}
        except (OSError, ValueError, binascii.Error) as error:
            return {'error': str(error)}


class WindowInstance:
    def __init__(self):
        runtime = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}')
        if not os.environ.get('XDG_RUNTIME_DIR') and not runtime.exists():
            runtime = Path.home() / '.cache/cp-coach/runtime'
            runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = runtime.lstat()
        if not runtime.is_absolute() or not runtime.is_dir() or runtime.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError('XDG_RUNTIME_DIR tem de ser um diretório privado do teu utilizador.')
        os.environ.setdefault('XDG_RUNTIME_DIR', str(runtime))
        self.path = runtime / 'cp-coach-window.sock'
        self.lock = os.open(runtime / 'cp-coach-window.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        self.listener = None
        self.thread = None
        self.stopped = threading.Event()
        self.pending = threading.Event()
        self.callback = None
        self.activation = {}

    def acquire(self):
        for _ in range(50):
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.path.unlink(missing_ok=True)
                self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.listener.bind(str(self.path))
                self.path.chmod(0o600)
                self.listener.listen(4)
                self.listener.settimeout(.2)
                self.thread = threading.Thread(target=self.serve, daemon=True)
                self.thread.start()
                return True
            except BlockingIOError:
                if self.notify():
                    return False
                self.stopped.wait(.1)
        raise RuntimeError('A janela existente não respondeu. Tenta novamente.')

    def notify(self):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(1)
                client.connect(str(self.path))
                payload = {key: os.environ.get(key, '')[:1024] for key in ('DESKTOP_STARTUP_ID', 'XDG_ACTIVATION_TOKEN')}
                client.sendall(json.dumps(payload).encode() + b'\n')
                return client.recv(16) == b'ok\n'
        except OSError:
            return False

    def serve(self):
        while not self.stopped.is_set():
            try:
                client, _ = self.listener.accept()
                with client:
                    client.settimeout(1)
                    data = b''
                    while b'\n' not in data and len(data) <= 4096:
                        part = client.recv(1024)
                        if not part:
                            break
                        data += part
                    value = json.loads(data) if len(data) <= 4096 else None
                    if isinstance(value, dict) and all(isinstance(v, str) and len(v) <= 1024 for v in value.values()):
                        self.activation = value
                        self.pending.set()
                        if self.callback:
                            self.callback()
                        client.sendall(b'ok\n')
            except (OSError, ValueError):
                pass

    def on_activate(self, callback):
        self.callback = callback
        if self.pending.is_set():
            callback()

    def close(self):
        self.stopped.set()
        if self.listener:
            if self.thread:
                self.thread.join(timeout=2)
            self.listener.close()
            self.path.unlink(missing_ok=True)
        os.close(self.lock)


def native_window(url, instance=None):
    warnings.filterwarnings('ignore', category=SyntaxWarning, module=r'webview(\..*)?')
    import webview
    from gi.repository import GLib
    GLib.set_prgname('cp-coach')
    GLib.set_application_name('CP Coach')
    storage = Path.home() / '.local/share/cp-coach/webview'
    storage.mkdir(parents=True, exist_ok=True, mode=0o700)
    api = DesktopAPI()
    Handler.native_downloads = True
    api._window = webview.create_window('CP Coach — CP Tools', url, js_api=api, width=1280, height=900, localization={'global.saveFile': 'Guardar como'})
    if instance:
        def present():
            from gi.repository import Gtk
            native = api._window.native
            context = instance.activation
            startup = context.get('DESKTOP_STARTUP_ID') or context.get('XDG_ACTIVATION_TOKEN')
            if startup:
                native.set_startup_id(startup)
            if context.get('XDG_ACTIVATION_TOKEN'):
                os.environ['XDG_ACTIVATION_TOKEN'] = context['XDG_ACTIVATION_TOKEN']
            native.deiconify()
            native.show_all()
            timestamp = Gtk.get_current_event_time()
            match = re.search(r'_TIME(\d+)$', context.get('DESKTOP_STARTUP_ID', ''))
            if match:
                timestamp = int(match[1]) & 0xffffffff
            window = native.get_window()
            if window and hasattr(window, 'get_xid'):
                from gi.repository import GdkX11
                if not timestamp:
                    timestamp = GdkX11.x11_get_server_time(window)
                native.present_with_time(timestamp)
                if shutil.which('wmctrl'):
                    subprocess.run(['wmctrl', '-ia', hex(window.get_xid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            else:
                native.present_with_time(timestamp)
            return False
        instance.on_activate(lambda: GLib.idle_add(present))
    approved, confirming = False, False
    def confirm_close():
        nonlocal approved, confirming
        try:
            base = url.split('?', 1)[0].rstrip('/')
            with urlopen(base + '/blocker/status', timeout=4) as response:
                session = json.load(response)['current']
            if session and session['mode'] != 'practice-ai':
                if not api._window.create_confirmation_dialog('Sessão restrita em curso', 'Fechar a janela? A monitorização de histórico, processos e rede continua em segundo plano até a sessão terminar. Volta a abrir o CP Coach para consultares ou terminares a sessão.'):
                    return False
            if session:
                request = Request(base + '/blocker/background', data=b'{}', headers={'X-CP-Tools': '1', 'Content-Type': 'application/json'})
                with urlopen(request, timeout=4):
                    pass
        except (OSError, ValueError, KeyError):
            if not api._window.create_confirmation_dialog('Monitorização indisponível', 'Não foi possível confirmar a ligação ao monitor. Fechar a janela mesmo assim?'):
                return
        finally:
            confirming = False
        approved = True
        api._window.destroy()
    def closing():
        nonlocal confirming
        if approved:
            return True
        if not confirming:
            confirming = True
            threading.Thread(target=confirm_close, daemon=True).start()
        return False
    api._window.events.closing += closing
    webview.start(gui='gtk', private_mode=False, storage_path=str(storage), icon=str(ROOT / 'cp-coach.png'))


def monitor_paths():
    storage = Path.home() / '.local/share/cp-coach'
    storage.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = 'monitor-' + hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
    return storage / (name + '.lock'), storage / (name + '.json'), storage / (name + '.log')


def monitor(args):
    lock_path, address_path, _ = monitor_paths()
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        serve_monitor(args, address_path)


def serve_monitor(args, address_path):
    blocker = Blocker()
    blocker.clients = set()
    Handler.blocker = blocker
    httpd = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    threading.Thread(target=blocker.run, daemon=True).start()
    url = 'http://localhost:%d/' % httpd.server_port
    address_path.write_text(json.dumps({'url': url, 'pid': os.getpid()}))
    address_path.chmod(0o600)
    print('CP Coach em ' + url, flush=True)
    try:
        idle = time.monotonic()
        while True:
            blocker.shutdown.wait(1)
            with blocker.lock:
                blocker.clients = {pid for pid in blocker.clients if Path('/proc', str(pid)).exists()}
                pending = any(session.get('history_rescans') for session in blocker.state['sessions'])
                if blocker.state['current'] or blocker.clients or pending:
                    idle = time.monotonic()
                elif time.monotonic() - idle > 8:
                    break
    except KeyboardInterrupt:
        pass
    finally:
        blocker.shutdown.set()
        httpd.shutdown()
        httpd.server_close()
        address_path.unlink(missing_ok=True)


def ensure_monitor(args):
    _, address_path, log_path = monitor_paths()
    def running():
        try:
            address = json.loads(address_path.read_text())
            request = Request(address['url'] + 'blocker/foreground', data=json.dumps({'pid': os.getpid()}).encode(), headers={'X-CP-Tools': '1', 'Content-Type': 'application/json'})
            with urlopen(request, timeout=2) as response:
                json.load(response)
            return address['url']
        except (OSError, ValueError, KeyError):
            return None
    url = running()
    if url:
        return url
    with open(log_path, 'a') as log:
        subprocess.Popen([sys.executable, str(ROOT / 'cp-coach.py'), '--monitor', '--port', str(args.port)], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, close_fds=True)
    for _ in range(100):
        url = running()
        if url:
            return url
        threading.Event().wait(.1)
    raise RuntimeError('O monitor não iniciou. Consulta ' + str(log_path))


def main():
    parser = argparse.ArgumentParser(description='CP Coach Desktop')
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--serve-only', action='store_true')
    parser.add_argument('--monitor', action='store_true')
    parser.add_argument('--resume-only', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    if args.monitor:
        return monitor(args)
    if args.resume_only:
        instance = WindowInstance()
        try:
            if instance.notify():
                return
        finally:
            instance.close()
        return launch(args)
    instance = None
    if not args.serve_only:
        instance = WindowInstance()
        try:
            if not instance.acquire():
                return
            return launch(args, instance)
        finally:
            instance.close()
    return launch(args)


def launch(args, instance=None):
    if args.resume_only:
        state = ROOT / 'cp-coach-blocker.json'
        if not state.exists() or not json.loads(state.read_text()).get('current'):
            return
    url = ensure_monitor(args)
    print('CP Coach em ' + url, flush=True)
    if args.resume_only:
        return
    try:
        if not args.browser and not args.serve_only:
            try:
                native_window(url + '?desktop=1', instance)
                return
            except Exception as error:
                print('Janela nativa indisponível; a abrir navegador: ' + str(error), flush=True)
        if not args.serve_only:
            if not shutil.which('wmctrl'):
                raise RuntimeError('Instala wmctrl para levantar a janela do navegador, ou usa --serve-only.')
            browser_window(url)
            if instance:
                instance.on_activate(lambda: activate_browser(url))
        print('Ctrl+C fecha este lançador; uma sessão ativa continua no monitor.', flush=True)
        while True:
            threading.Event().wait(1)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
