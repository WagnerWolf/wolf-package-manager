#!/usr/bin/env bash
set -euo pipefail
if (( EUID == 0 )); then
  echo 'Execute como usuário comum.' >&2; exit 1
fi
rm -f -- "${HOME}/.local/bin/wolf-package-manager" \
  "${HOME}/.local/share/applications/wolf-package-manager.desktop" \
  "${HOME}/.config/autostart/wolf-package-manager.desktop" \
  "${HOME}/.local/share/icons/hicolor/256x256/apps/wolf-package-manager.png"
rm -rf -- "${HOME}/.local/share/wolf-package-manager"
echo 'Removido. Preferências preservadas em ~/.config/wolf-package-manager.'
