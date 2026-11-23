#!/usr/bin/env bash
set -uo pipefail
INSTALL_USER="${SUDO_USER:-$(id -un)}"
USER_HOME="$(getent passwd "$INSTALL_USER" | cut -d: -f6)"
PURGE=0
if [[ "${1:-}" == --purge && $# == 1 ]]; then PURGE=1
elif (($#)); then echo 'Uso: uninstall.sh [--purge]' >&2; exit 1; fi
if ! sudo -n true 2>/dev/null && ! sudo -v; then
    echo 'Falha: não foi possível obter autorização administrativa. Nada foi alterado. Tente novamente com acesso a sudo.' >&2
    exit 1
fi
if ! sudo /usr/bin/python3 -I - "$(id -u "$INSTALL_USER")" <<'PY'
from pathlib import Path
import fcntl, json, os, signal, stat, sys, tempfile, time, uuid
state_path = Path('/var/lib/cp-coach/restriction.json')
journal_path = state_path.with_name('emergency-journal.json')
for path in (state_path, journal_path):
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
                raise RuntimeError('Estado de emergência inseguro: ' + str(item))
uid = int(sys.argv[1])
for path in Path('/proc').glob('[0-9]*/cmdline'):
    try:
        if path.stat().st_uid != uid:
            continue
        args = path.read_bytes().decode(errors='replace').rstrip('\0').split('\0')
        script = args[0] if args else ''
        if args and Path(args[0]).name.startswith('python'):
            iterator = iter(args[1:])
            script = ''
            for arg in iterator:
                if arg in ('-c', '-m'):
                    break
                if arg in ('-W', '-X'):
                    next(iterator, None)
                elif not arg.startswith('-'):
                    script = arg
                    break
        if Path(script).name == 'cp-coach.py':
            os.kill(int(path.parent.name), signal.SIGTERM)
    except (OSError, ValueError):
        pass
fd = os.open('/run/cp-coach-hosts.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
with os.fdopen(fd, 'r+') as lock:
    info = os.fstat(lock.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError('Ficheiro de bloqueio inseguro')
    fcntl.flock(lock, fcntl.LOCK_EX)
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if time.time() < state['deadline']:
            print('AVISO: restrição ativa. Desinstalar conta como saída de emergência e fica no diário.', flush=True)
            events = json.loads(journal_path.read_text()) if journal_path.exists() else []
            events.append(dict(id=uuid.uuid4().hex, session=state['id'], uid=state['uid'], start=state['start'], deadline=state['deadline'], mode=state.get('mode', 'practice-no-ai'), strict=state['strict'], time=time.time(), how='uninstall'))
            fd, name = tempfile.mkstemp(prefix='.cp-coach-', dir=journal_path.parent)
            try:
                with os.fdopen(fd, 'w') as target:
                    os.fchmod(target.fileno(), 0o600)
                    target.write(json.dumps(events[-256:]))
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(name, journal_path)
            finally:
                if os.path.exists(name): os.unlink(name)
        state_path.unlink()
PY
then
    exit 1
fi
HELPER=/usr/local/lib/cp-coach/cp-coach-hosts-helper
DESKTOP="$USER_HOME/.local/share/applications/cp-coach.desktop"
AUTOSTART="$USER_HOME/.config/autostart/cp-coach-monitor.desktop"
ICONS="$USER_HOME/.local/share/icons/hicolor"
has_block() {
    /usr/bin/python3 -I -c 'from pathlib import Path; import sys; sys.exit(not any(line.strip() in ("# BEGIN cp-coach", "# END cp-coach") for line in Path("/etc/hosts").read_text().splitlines()))'
}
has_firewall() {
    command -v nft >/dev/null && sudo nft list table inet cp_coach >/dev/null 2>&1
}
if [[ "$PURGE" == 0 && ! -e "$HELPER" && ! -L "$HELPER" && ! -e "$DESKTOP" && ! -e "$AUTOSTART" && ! -e "$ICONS/scalable/apps/cp-coach.svg" && ! -e "$ICONS/256x256/apps/cp-coach.png" ]] && ! has_block && ! sudo test -e /etc/sudoers.d/cp-coach && ! sudo test -e /var/lib/cp-coach/browser-policies.json && ! sudo test -e /run/cp-coach-strict.json && ! has_firewall; then
    echo 'Nada para desinstalar'
    exit 0
fi
failures=()
attempt() {
    local label="$1"
    shift
    if ! "$@"; then failures+=("$label"); fi
}
if [[ -e "$HELPER" || -L "$HELPER" ]]; then
    if /usr/bin/python3 -I - "$HELPER" <<'PY'
import os, stat, sys
from pathlib import Path
path = Path(sys.argv[1])
for item in (path, *path.parents):
    info = os.lstat(item)
    if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
        sys.exit(1)
if not stat.S_ISREG(os.lstat(path).st_mode):
    sys.exit(1)
PY
    then
        attempt 'Remoção do bloco pelo helper' sudo "$HELPER" remove
    else
        failures+=('Helper instalado inseguro; não foi executado')
    fi
fi
if has_firewall; then
    attempt 'Remoção da firewall nftables' sudo nft delete table inet cp_coach
fi
if ! has_firewall; then
    attempt 'Remoção do estado da firewall' sudo rm -f -- /run/cp-coach-strict.json
fi
if has_block; then
    attempt 'Reparação do bloco em /etc/hosts' sudo /usr/bin/python3 -I - <<'PY'
import fcntl, ipaddress, os, stat, tempfile
from pathlib import Path
domains = '''chatgpt.com chat.openai.com openai.com api.openai.com claude.ai anthropic.com gemini.google.com bard.google.com aistudio.google.com copilot.microsoft.com perplexity.ai chat.deepseek.com deepseek.com poe.com grok.com x.ai meta.ai chat.mistral.ai huggingface.co phind.com you.com character.ai api.githubcopilot.com copilot-proxy.githubusercontent.com codeium.com windsurf.com cursor.com cursor.sh tabnine.com blackbox.ai api.anthropic.com console.anthropic.com statsig.anthropic.com claude.com devin.ai app.devin.ai api.devin.ai cognition.ai server.codeium.com inference.codeium.com generativelanguage.googleapis.com cloudcode-pa.googleapis.com api.deepseek.com api.mistral.ai api.x.ai api.groq.com groq.com openrouter.ai api.together.xyz together.ai ollama.com api.individual.githubcopilot.com api.business.githubcopilot.com api.enterprise.githubcopilot.com copilot-telemetry.githubusercontent.com api2.cursor.sh api.cursor.com ampcode.com opencode.ai api.moonshot.ai dashscope.aliyuncs.com'''.split()
domains += '''z.ai chat.z.ai chatglm.cn kimi.com kimi.moonshot.cn chat.qwen.ai qwen.ai tongyi.aliyun.com doubao.com yiyan.baidu.com pi.ai lmarena.ai chat.lmsys.org genspark.ai manus.im notebooklm.google.com t3.chat monica.im duck.ai felo.ai chat.minimax.io hailuoai.com'''.split()
domains += '''duckduckgo.com duckchat.com qwenlm.ai chat.qwenlm.ai qianwen.com chat.qianwen.com dashscope-intl.aliyuncs.com'''.split()
domains += '''deepai.org search.brave.com ai-search.brave.com ai-chat.bsg.brave.com leo.brave.com iask.ai andisearch.com komo.ai alice.yandex.ru alice.yandex.com yandexgpt.yandex.ru aistudio.yandex.ru gpt.yandex.ru 300.ya.ru sydney.bing.com edgeservices.bing.com'''.split()
domains += '''duck.com duck.co duckgo.com ddg.gg ddg.co duckduckgo.ca duckduckgo.co duckduckgo.co.uk duckduckgo.com.mx duckduckgo.com.tw duckduckgo.de duckduckgo.nl duckduckgo.org duckduckgo.pl duckduckgo.se duckduckgo.uk duckduckgo.ai safe.duckduckgo.com start.duckduckgo.com html.duckduckgo.com lite.duckduckgo.com'''.split()
domains += '''links.duckduckgo.com heck.ai chat.akash.network hf.space gradio.live chatbot.app aichatting.net unlimitedai.org metaso.cn exa.ai morphic.sh arena.ai'''.split()
domains += '''pollinations.ai text.pollinations.ai image.pollinations.ai gen.pollinations.ai enter.pollinations.ai venice.ai api.venice.ai kagi.com models.github.ai models.inference.ai.azure.com router.huggingface.co api-inference.huggingface.co api.cerebras.ai inference.cerebras.ai cerebras.ai api.fireworks.ai fireworks.ai api.deepinfra.com deepinfra.com api.cohere.ai api.cohere.com cohere.ai cohere.com api.together.ai together.xyz'''.split()
entries = {f'{ip} {domain}' for domain in domains + ['www.' + d for d in domains] for ip in ('0.0.0.0', '::')}
def noai_entry(line):
    fields = line.split()
    if len(fields) != 2 or fields[1] != 'noai.duckduckgo.com':
        return False
    try:
        ipaddress.ip_address(fields[0])
        return True
    except ValueError:
        return False
fd = os.open('/run/cp-coach-hosts.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
with os.fdopen(fd, 'r+') as lock:
    info = os.fstat(lock.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError('Ficheiro de bloqueio inseguro em /run/cp-coach-hosts.lock')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    info = os.lstat('/etc/hosts')
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError('/etc/hosts não é um ficheiro regular')
    result, inside = [], False
    for line in Path('/etc/hosts').read_text().splitlines(keepends=True):
        if line.strip() == '# BEGIN cp-coach':
            inside = True
        elif line.strip() == '# END cp-coach':
            inside = False
        elif not inside or (line.strip() not in entries and not noai_entry(line)):
            result.append(line)
    fd, name = tempfile.mkstemp(prefix='.cp-coach-', dir='/etc')
    try:
        with os.fdopen(fd, 'w') as target:
            os.fchmod(target.fileno(), stat.S_IMODE(info.st_mode))
            os.fchown(target.fileno(), info.st_uid, info.st_gid)
            target.write(''.join(result))
            target.flush()
            os.fsync(target.fileno())
        os.replace(name, '/etc/hosts')
    finally:
        if os.path.exists(name):
            os.unlink(name)
PY
fi
if sudo test -e /var/lib/cp-coach/browser-policies.json; then
    attempt 'Reposição das políticas dos navegadores' sudo /usr/bin/python3 -I - <<'PY'
import base64, fcntl, json, os, stat, tempfile
from pathlib import Path
state_path = Path('/var/lib/cp-coach/browser-policies.json')
chrome = ['/etc/opt/chrome', '/etc/chromium', '/etc/chromium-browser', '/etc/brave', '/etc/opt/brave', '/etc/opt/edge', '/etc/opt/chrome_for_testing', '/var/snap/chromium/current']
allowed = {folder + '/policies/managed/cp-coach.json' for folder in chrome} | {'/etc/firefox/policies/policies.json', '/usr/lib/firefox/distribution/policies.json', '/usr/lib/firefox-esr/distribution/policies.json', '/opt/firefox/distribution/policies.json', '/var/snap/firefox/common/policies/policies.json'}
def safe(path):
    for parent in path.parents:
        info = parent.stat()
        if info.st_uid or info.st_mode & 0o022 or not stat.S_ISDIR(info.st_mode):
            raise ValueError('Diretório de políticas inseguro: ' + str(parent))
        if parent.is_symlink():
            if parent.lstat().st_uid:
                raise ValueError('Ligação insegura: ' + str(parent))
            safe(parent.resolve() / '.cp-coach-check')
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if info.st_uid or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode):
            raise ValueError('Ficheiro de políticas inseguro: ' + str(path))
lock = os.open('/run/cp-coach-hosts.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
with os.fdopen(lock, 'r+') as stream:
    info = os.fstat(stream.fileno())
    if info.st_uid or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode):
        raise ValueError('Lock inseguro')
    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    safe(state_path)
    state = json.loads(state_path.read_text())
    if set(state) - allowed:
        raise ValueError('Backup contém caminhos de políticas desconhecidos; nada foi alterado')
    for filename, original in state.items():
        path = Path(filename)
        safe(path)
        if original is None:
            path.unlink(missing_ok=True)
        else:
            fd, name = tempfile.mkstemp(prefix='.cp-coach-', dir=path.parent)
            try:
                with os.fdopen(fd, 'wb') as target:
                    os.fchmod(target.fileno(), original['mode'])
                    os.fchown(target.fileno(), original['uid'], original['gid'])
                    target.write(base64.b64decode(original['content'], validate=True))
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(name, path)
            finally:
                if os.path.exists(name): os.unlink(name)
    state_path.unlink()
PY
fi
attempt 'Remoção da regra sudo' sudo rm -f -- /etc/sudoers.d/cp-coach
attempt 'Remoção do helper' sudo rm -f -- "$HELPER"
if [[ -d /usr/local/lib/cp-coach ]]; then
    attempt 'Remoção da pasta do helper' sudo rmdir -- /usr/local/lib/cp-coach
fi
attempt 'Remoção do atalho, arranque automático e ícones' rm -f -- "$DESKTOP" "$AUTOSTART" "$ICONS/scalable/apps/cp-coach.svg" "$ICONS/256x256/apps/cp-coach.png"
if command -v gtk-update-icon-cache >/dev/null; then
    gtk-update-icon-cache -f -t "$ICONS" >/dev/null 2>&1 || true
fi
if ((${#failures[@]})); then
    printf 'Desinstalação com falhas:\n' >&2
    printf ' - %s\n' "${failures[@]}" >&2
    echo 'Recuperação: feche o CP Coach, verifique as permissões e tente novamente. Se necessário, repare apenas o bloco entre os marcadores cp-coach em /etc/hosts como administrador.' >&2
    exit 1
fi
echo 'Desinstalado: bloco, helper, regra sudo, atalho e ícones removidos. Dados e dependências mantidos.'
if ((PURGE)); then
    sudo rm -f -- /var/lib/cp-coach/emergency-journal.json || exit 1
    echo 'Diário de emergência apagado (--purge).'
else
    echo 'Diário de emergência mantido em /var/lib/cp-coach (máximo 256 eventos).'
fi
