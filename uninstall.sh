#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_USER="${SUDO_USER:-$(id -un)}"
USER_HOME="$(getent passwd "$INSTALL_USER" | cut -d: -f6)"
echo 'Feche o CP Coach antes de desinstalar; uma aplicação em execução pode repor o bloqueio.'
if [[ -f /usr/local/lib/cp-coach/cp-coach-hosts-helper ]]; then
    sudo /usr/local/lib/cp-coach/cp-coach-hosts-helper remove
else
    sudo "$APP_DIR/cp-coach-hosts-helper" remove
fi
sudo rm -f -- /etc/sudoers.d/cp-coach /usr/local/lib/cp-coach/cp-coach-hosts-helper
if [[ -d /usr/local/lib/cp-coach ]]; then
    sudo rmdir -- /usr/local/lib/cp-coach
fi
rm -f -- "$USER_HOME/.local/share/applications/cp-coach.desktop" "$USER_HOME/.local/share/icons/hicolor/scalable/apps/cp-coach.svg"
echo 'Desinstalado: bloco de rede, helper, regra sudo e atalho removidos. Aplicação, dados e dependências mantidos.'
