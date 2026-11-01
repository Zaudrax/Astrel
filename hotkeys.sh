#!/usr/bin/env bash
# Raccourcis clavier globaux GNOME (réglages utilisateur, aucun sudo) :
#   Ctrl+Alt+J       parler maintenant (sans dire « Astrel »)
#   Ctrl+Alt+M       couper / réactiver le micro
#   Ctrl+Alt+X       interrompre Astrel
#   Ctrl+Alt+P       mettre la voix en pause / la reprendre (le travail continue)
#   Ctrl+Alt+H       ouvrir le HUD
# `./hotkeys.sh --remove` pour les retirer.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SCHEMA=org.gnome.settings-daemon.plugins.media-keys
BASE=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings
KEYS=(astrel-wake astrel-toggle astrel-stop astrel-pause astrel-hud)

current=$(gsettings get "$SCHEMA" custom-keybindings)
paths=()
for k in "${KEYS[@]}"; do paths+=("$BASE/$k/"); done

edit_list() {  # $1 = add|remove
  python3 - "$1" "$current" "${paths[@]}" <<'PY'
import ast, sys
mode, cur, *paths = sys.argv[1:]
lst = ast.literal_eval(cur.replace("@as ", "")) if cur.strip() not in ("@as []", "[]") else []
if mode == "add":
    lst += [p for p in paths if p not in lst]
else:
    lst = [p for p in lst if p not in paths]
print(str(lst))
PY
}

if [[ "${1:-}" == "--remove" ]]; then
  gsettings set "$SCHEMA" custom-keybindings "$(edit_list remove)"
  echo "Raccourcis retirés."
  exit 0
fi

gsettings set "$SCHEMA" custom-keybindings "$(edit_list add)"
set_key() {
  local s="$SCHEMA.custom-keybinding:$BASE/$1/"
  gsettings set "$s" name "$2"; gsettings set "$s" command "$3"; gsettings set "$s" binding "$4"
}
set_key astrel-wake   "Astrel : parler"          "$ROOT/run.sh --send wake"   "<Control><Alt>j"
set_key astrel-toggle "Astrel : micro"           "$ROOT/run.sh --send toggle" "<Control><Alt>m"
set_key astrel-stop   "Astrel : interrompre"     "$ROOT/run.sh --send stop"   "<Control><Alt>x"
set_key astrel-pause  "Astrel : pause de la voix" "$ROOT/run.sh --send 'pause toggle'" "<Control><Alt>p"
set_key astrel-hud    "Astrel : HUD"             "$ROOT/hud.sh"               "<Control><Alt>h"
echo "Raccourcis posés : Ctrl+Alt+J parler · Ctrl+Alt+M micro · Ctrl+Alt+X interrompre · Ctrl+Alt+P pause de la voix · Ctrl+Alt+H HUD"
