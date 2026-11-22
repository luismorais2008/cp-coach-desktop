#!/usr/bin/env bash
set -uo pipefail
INSTALL_USER="${SUDO_USER:-$(id -un)}"
USER_HOME="$(getent passwd "$INSTALL_USER" | cut -d: -f6)"
if ! /usr/bin/python3 -I - <<'PY'
from pathlib import Path
import sys
for path in Path('/proc').glob('[0-9]*/cmdline'):
    try:
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
            print('Não é possível desinstalar: o CP Coach está em execução. Feche todas as janelas/servidores e tente novamente. Nada foi alterado.', file=sys.stderr)
            sys.exit(1)
    except (OSError, ValueError):
        pass
PY
then
    exit 1
fi
if ! sudo -n true 2>/dev/null && ! sudo -v; then
    echo 'Falha: não foi possível obter autorização administrativa. Nada foi alterado. Tente novamente com acesso a sudo.' >&2
    exit 1
fi
HELPER=/usr/local/lib/cp-coach/cp-coach-hosts-helper
DESKTOP="$USER_HOME/.local/share/applications/cp-coach.desktop"
ICONS="$USER_HOME/.local/share/icons/hicolor"
has_block() {
    /usr/bin/python3 -I -c 'from pathlib import Path; import sys; sys.exit(not any(line.strip() in ("# BEGIN cp-coach", "# END cp-coach") for line in Path("/etc/hosts").read_text().splitlines()))'
}
if [[ ! -e "$HELPER" && ! -L "$HELPER" && ! -e "$DESKTOP" && ! -e "$ICONS/scalable/apps/cp-coach.svg" && ! -e "$ICONS/256x256/apps/cp-coach.png" ]] && ! has_block && ! sudo test -e /etc/sudoers.d/cp-coach; then
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
if has_block; then
    attempt 'Reparação do bloco em /etc/hosts' sudo /usr/bin/python3 -I - <<'PY'
import fcntl, os, stat, tempfile
from pathlib import Path
domains = '''chatgpt.com chat.openai.com openai.com api.openai.com claude.ai anthropic.com gemini.google.com bard.google.com aistudio.google.com copilot.microsoft.com perplexity.ai chat.deepseek.com deepseek.com poe.com grok.com x.ai meta.ai chat.mistral.ai huggingface.co phind.com you.com character.ai api.githubcopilot.com copilot-proxy.githubusercontent.com codeium.com windsurf.com cursor.com cursor.sh tabnine.com blackbox.ai api.anthropic.com console.anthropic.com statsig.anthropic.com claude.com devin.ai app.devin.ai api.devin.ai cognition.ai server.codeium.com inference.codeium.com generativelanguage.googleapis.com cloudcode-pa.googleapis.com api.deepseek.com api.mistral.ai api.x.ai api.groq.com groq.com openrouter.ai api.together.xyz together.ai ollama.com api.individual.githubcopilot.com api.business.githubcopilot.com api.enterprise.githubcopilot.com copilot-telemetry.githubusercontent.com api2.cursor.sh api.cursor.com ampcode.com opencode.ai api.moonshot.ai dashscope.aliyuncs.com'''.split()
domains += '''z.ai chat.z.ai chatglm.cn kimi.com kimi.moonshot.cn chat.qwen.ai qwen.ai tongyi.aliyun.com doubao.com yiyan.baidu.com pi.ai lmarena.ai chat.lmsys.org genspark.ai manus.im notebooklm.google.com t3.chat monica.im duck.ai felo.ai chat.minimax.io hailuoai.com'''.split()
entries = {f'{ip} {domain}' for domain in domains + ['www.' + d for d in domains] for ip in ('0.0.0.0', '::')}
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
        elif not inside or line.strip() not in entries:
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
attempt 'Remoção da regra sudo' sudo rm -f -- /etc/sudoers.d/cp-coach
attempt 'Remoção do helper' sudo rm -f -- "$HELPER"
if [[ -d /usr/local/lib/cp-coach ]]; then
    attempt 'Remoção da pasta do helper' sudo rmdir -- /usr/local/lib/cp-coach
fi
attempt 'Remoção do atalho e ícones' rm -f -- "$DESKTOP" "$ICONS/scalable/apps/cp-coach.svg" "$ICONS/256x256/apps/cp-coach.png"
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
