#!/usr/bin/env bash
# Retire Astrel du démarrage et des raccourcis. Ne supprime NI le code, NI la
# mémoire (vault/), NI les réglages (config/).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
systemctl --user disable --now astrel.service astrel-bubble.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/astrel.service" "$HOME/.config/systemd/user/astrel-bubble.service"
systemctl --user daemon-reload
rm -f "$HOME/.config/autostart/astrel-bubble.desktop" "$HOME/.local/share/applications/astrel-hud.desktop"
# --- icône du lanceur (22/10, plan P25 ; posée par install.py, install_icons) ---
ICONS="$HOME/.local/share/icons/hicolor"
rm -f "$ICONS"/*/apps/astrel-hud.png "$ICONS"/*/apps/astrel-hud.svg
if [ -d "$ICONS" ] && command -v gtk-update-icon-cache >/dev/null; then
  gtk-update-icon-cache -q -f -t "$ICONS" || true
fi
# --- fin de l'icône ---
pkill -f "[a]strel.bubble" || true
"$ROOT/hotkeys.sh" --remove || true
echo "Astrel retiré du démarrage. Code, mémoire et réglages conservés."
