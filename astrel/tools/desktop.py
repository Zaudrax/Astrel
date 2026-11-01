"""Actions sur le bureau : ouvrir, notifier, volume, luminosité, thème, médias,
presse-papiers, verrouillage, alimentation, Wi-Fi.

Façade : le travail est fait par le moteur du système (`astrel/platform/`) —
Linux (GNOME, code d'origine), macOS, Windows, ou un moteur « indisponible ».
Les noms et les phrases rendues ne changent pas pour les appelants
(`mcp_server.py`, `core.py`).

Règles (tous systèmes) :
- jamais de `shell=True` : chaque commande est une liste d'arguments, un nom
  d'application ou une URL ne peut donc pas injecter une commande ;
- chaque fonction rend une phrase courte, lisible par Claude et prononçable ;
- ce qui n'existe pas sur la machine est dit franchement.
"""
from __future__ import annotations

from ..platform import desktop_backend

_impl = desktop_backend()

open_url = _impl.open_url
open_path = _impl.open_path
open_app = _impl.open_app
list_apps = _impl.list_apps
notify = _impl.notify
set_volume = _impl.set_volume
get_volume = _impl.get_volume
set_brightness = _impl.set_brightness
set_dark_mode = _impl.set_dark_mode
lock_screen = _impl.lock_screen
media_control = _impl.media_control
clipboard_read = _impl.clipboard_read
clipboard_write = _impl.clipboard_write
wifi_toggle = _impl.wifi_toggle
power_action = _impl.power_action
windows_summary = _impl.windows_summary
