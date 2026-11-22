#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_USER="${SUDO_USER:-$(id -un)}"
if [[ "$INSTALL_USER" == root || ! "$INSTALL_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*\$?$ ]]; then
    echo 'Execute como utilizador normal (com acesso a sudo), não como root.' >&2
    exit 1
fi
USER_HOME="$(getent passwd "$INSTALL_USER" | cut -d: -f6)"
as_user() {
    if [[ $(id -u) == 0 ]]; then
        sudo -H -u "$INSTALL_USER" "$@"
    else
        "$@"
    fi
}
sudo -n true 2>/dev/null || sudo -v
PYTHON="$(command -v python3)"
if command -v apt-get >/dev/null; then
    if sudo apt-get update; then
        WEBKIT=()
        if apt-cache show gir1.2-webkit2-4.1 >/dev/null 2>&1; then
            WEBKIT=(gir1.2-webkit2-4.1)
        else
            WEBKIT=(gir1.2-webkit2-4.0)
        fi
        sudo apt-get install -y python3 python3-gi python3-gi-cairo python3-venv gir1.2-gtk-3.0 "${WEBKIT[@]}" || echo 'Dependências GTK indisponíveis; será usado o navegador.' >&2
    else
        echo 'Não foi possível atualizar o apt; a continuar com as dependências existentes.' >&2
    fi
    PYTHON=/usr/bin/python3
fi
if as_user "$PYTHON" -m venv --system-site-packages "$APP_DIR/.venv" &&
   as_user "$APP_DIR/.venv/bin/python" -m pip install --upgrade 'pywebview>=6.2,<7'; then
    PYTHON="$APP_DIR/.venv/bin/python"
else
    echo 'pywebview recente indisponível; a aplicação pode usar --browser.' >&2
fi
if ! command -v visudo >/dev/null; then
    echo 'visudo não encontrado. Instale sudo antes de configurar o bloqueador.' >&2
    exit 1
fi
sudo install -d -o root -g root -m 755 /usr/local/lib/cp-coach
sudo install -o root -g root -m 755 "$APP_DIR/cp-coach-hosts-helper" /usr/local/lib/cp-coach/cp-coach-hosts-helper
RULE="$(mktemp)"
trap 'rm -f -- "$RULE"' EXIT
printf '%s ALL=(root) NOPASSWD: /usr/local/lib/cp-coach/cp-coach-hosts-helper apply, /usr/local/lib/cp-coach/cp-coach-hosts-helper remove, /usr/local/lib/cp-coach/cp-coach-hosts-helper status\n' "$INSTALL_USER" > "$RULE"
sudo visudo -cf "$RULE"
sudo install -o root -g root -m 440 "$RULE" /etc/sudoers.d/cp-coach
sudo visudo -cf /etc/sudoers.d/cp-coach
as_user "$PYTHON" - "$APP_DIR" "$USER_HOME" "$PYTHON" <<'PY'
from pathlib import Path
import sys
app, home, python = sys.argv[1:]
def quoted(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'
icons = Path(home) / '.local/share/icons/hicolor/scalable/apps'
icons.mkdir(parents=True, exist_ok=True)
(icons / 'cp-coach.svg').write_bytes((Path(app) / 'cp-coach.svg').read_bytes())
(icons / 'cp-coach.svg').chmod(0o644)
destination = Path(home) / '.local/share/applications'
destination.mkdir(parents=True, exist_ok=True)
desktop = destination / 'cp-coach.desktop'
desktop.write_text('[Desktop Entry]\nType=Application\nName=CP Coach\nComment=CP Tools e controlo local de IA\nExec=' + quoted(python) + ' ' + quoted(str(Path(app) / 'cp-coach.py')) + '\nIcon=cp-coach\nStartupWMClass=cp-coach\nTerminal=false\nCategories=Education;Development;\n', encoding='utf-8')
desktop.chmod(0o644)
PY
echo "Instalação concluída. Abra CP Coach no menu ou execute: $PYTHON \"$APP_DIR/cp-coach.py\""
