"""Système non pris en charge (ni Linux, ni macOS, ni Windows) : chaque action
du bureau dit clairement qu'elle est indisponible. Le reste d'Astrel (Claude,
mémoire, HUD) n'en dépend pas."""
from __future__ import annotations

import json

from . import unavailable


def _no(what: str):
    def action(*_args, **_kwargs):
        raise unavailable(what)
    action.__name__ = what
    return action


open_url = _no("Ouvrir une adresse")
open_path = _no("Ouvrir un fichier")
open_app = _no("Lancer une application")
notify = _no("Notification")
set_volume = _no("Réglage du volume")
get_volume = _no("Lecture du volume")
set_brightness = _no("Réglage de la luminosité")
set_dark_mode = _no("Thème sombre")
lock_screen = _no("Verrouillage de session")
media_control = _no("Contrôle des médias")
clipboard_read = _no("Lecture du presse-papiers")
clipboard_write = _no("Écriture du presse-papiers")
wifi_toggle = _no("Wi-Fi")
power_action = _no("Alimentation")


def list_apps(filter_text: str = "") -> list[str]:
    return []


def windows_summary() -> str:
    return json.dumps({"disponible": False, "raison": "système non pris en charge"})
# 17/10 (plan P4, lot 8) : page « Réglages », groupe « Système ».
autostart_state = _no("Démarrage automatique")
set_autostart = _no("Démarrage automatique")
hotkeys_state = _no("Raccourcis clavier")
set_hotkeys = _no("Raccourcis clavier")
