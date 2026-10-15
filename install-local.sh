#!/usr/bin/env bash
set -euo pipefail
if (( EUID == 0 )); then
  echo 'Execute como usuário comum (sem sudo).' >&2
  exit 1
fi
for cmd in python3 checkupdates yay pacman sudo script; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Dependência ausente: $cmd" >&2
    echo 'Para verificar dependências oficiais: sudo pacman -S --needed pyside6 pacman-contrib util-linux' >&2
    exit 1
  fi
done
/usr/bin/python3 -c 'import PySide6' || {
  echo 'PySide6 ausente. Instale: sudo pacman -S --needed pyside6' >&2
  exit 1
}
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
target="${HOME}/.local/share/wolf-package-manager"
if [[ "$(realpath -m "$source_dir")" == "$(realpath -m "$target")" ]]; then
  echo 'Execute o instalador a partir da pasta original, não de dentro do destino.' >&2
  exit 1
fi
mkdir -p -- "$target" "${HOME}/.local/bin" "${HOME}/.local/share/applications" "${HOME}/.config/autostart"
cp -r -- "$source_dir/wolfpkg" "$target/"
chmod 700 -- "$target/wolfpkg/askpass.sh"
cp -- "$source_dir/main.py" "$target/main.py"
mkdir -p "${HOME}/.local/share/icons/hicolor/256x256/apps"
cp -- "$source_dir/wolfpkg/assets/wolf-logo.png" "${HOME}/.local/share/icons/hicolor/256x256/apps/wolf-package-manager.png"
cat > "${HOME}/.local/bin/wolf-package-manager" <<'SCRIPT'
#!/usr/bin/env bash
exec /usr/bin/python3 "${HOME}/.local/share/wolf-package-manager/main.py" "$@"
SCRIPT
chmod +x "${HOME}/.local/bin/wolf-package-manager"
cat > "${HOME}/.local/share/applications/wolf-package-manager.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Wolf Package Manager
GenericName=Gerenciador de pacotes
Comment=Pesquisar, instalar e atualizar pacotes Arch, Chaotic-AUR e AUR
Exec=wolf-package-manager
Icon=wolf-package-manager
Categories=System;PackageManager;Qt;
Terminal=false
StartupNotify=true
DESKTOP
cat > "${HOME}/.config/autostart/wolf-package-manager.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Wolf Package Manager (Monitor)
Comment=Monitorar atualizações no tray do KDE
Exec=wolf-package-manager --tray
Icon=wolf-package-manager
X-KDE-autostart-after=panel
Terminal=false
DESKTOP
# Substituir o comando da entrada desktop pelo caminho absoluto do launcher, evitando PATH incompleto.
sed -i "s|^Exec=wolf-package-manager$|Exec=${HOME}/.local/bin/wolf-package-manager|" \
  "${HOME}/.local/share/applications/wolf-package-manager.desktop"
sed -i "s|^Exec=wolf-package-manager --tray$|Exec=${HOME}/.local/bin/wolf-package-manager --tray|" \
  "${HOME}/.config/autostart/wolf-package-manager.desktop"
echo 'Wolf Package Manager instalado para o usuário atual.'
echo "Execute: ${HOME}/.local/bin/wolf-package-manager"
echo 'Autostart de sessão adicionado. Reinicie a sessão KDE para validar o início automático.'
echo 'O Octopi não foi removido nem desabilitado.'
echo 'Se a versão antiga estiver aberta no tray, encerre-a antes de executar a nova.'
