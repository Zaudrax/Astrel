"""FOCUS : ne pas déranger.

Allumé : les bannières de notification GNOME sont masquées (elles restent dans
la liste), Astrel ne salue plus, et ses annonces non urgentes (réflexes) se
taisent — elles s'affichent seulement sur le HUD. Éteint : tout revient comme
avant, y compris le réglage GNOME d'origine.
"""
from __future__ import annotations

import subprocess

from ..events import bus

SCHEMA = "org.gnome.desktop.notifications"
KEY = "show-banners"


class Focus:
    def __init__(self) -> None:
        self._previous: str | None = None

    def start(self) -> None:
        self._previous = subprocess.run(["gsettings", "get", SCHEMA, KEY], capture_output=True, text=True).stdout.strip()
        subprocess.run(["gsettings", "set", SCHEMA, KEY, "false"], check=True, capture_output=True)
        bus.publish("notice", level="info", text="Mode concentration : je ne parle plus que pour l'urgent.")

    def stop(self) -> None:
        restore = self._previous if self._previous in ("true", "false") else "true"
        subprocess.run(["gsettings", "set", SCHEMA, KEY, restore], capture_output=True)
