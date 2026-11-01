"""Les capteurs d'Astrel : EYES, WATCH, HOLO, FOCUS, REFLEX.

Chaque capteur a un état voulu (allumé / éteint, mémorisé entre deux
démarrages) et un état réel (« actif », « indisponible : pas de webcam »…).
Un capteur n'est qu'un interrupteur ici ; son moteur s'enregistre avec
`register()` et reçoit `start()` / `stop()`. Tant qu'aucun moteur n'est
enregistré, l'état réel dit honnêtement « à venir » : un interrupteur qui
s'allume sans rien faire serait un mensonge du HUD.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Callable, Protocol

from . import config
from .events import bus
from .graph import SENSORS, graph

log = logging.getLogger("astrel.sensors")

STATE_FILE = config.RUNTIME / "sensors.json"


class SensorEngine(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...


@dataclass
class _Sensor:
    name: str
    desc: str
    wanted: bool = False
    status: str = "à venir"
    engine: SensorEngine | None = None


class Sensors:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        saved = self._load()
        self._items = {n: _Sensor(n, d, wanted=bool(saved.get(n, False))) for n, d in SENSORS.items()}
        self._listeners: list[Callable[[str, bool], None]] = []

    def register(self, name: str, engine: SensorEngine) -> None:
        with self._lock:
            item = self._items[name]
            item.engine = engine
            item.status = "éteint"
        if item.wanted:
            self.set(name, True)
        else:
            self._publish()

    def on_change(self, fn: Callable[[str, bool], None]) -> None:
        self._listeners.append(fn)

    def set(self, name: str, on: bool) -> dict:
        name = name.upper()
        if name not in self._items:
            return {"ok": False, "error": f"capteur inconnu : {name}"}
        with self._lock:
            item = self._items[name]
            item.wanted = on
            engine = item.engine
        status = "à venir"
        if engine is not None:
            try:
                (engine.start if on else engine.stop)()
                status = "actif" if on else "éteint"
            except Exception as exc:
                log.warning("capteur %s : %s", name, exc)
                status = f"indisponible : {exc}"
        with self._lock:
            item.status = status
        self._save()
        self._publish()
        for fn in self._listeners:
            try:
                fn(name, on)
            except Exception:
                log.exception("écouteur de capteur en échec")
        return {"ok": True, "name": name, "on": on, "status": status}

    def is_on(self, name: str) -> bool:
        with self._lock:
            item = self._items.get(name.upper())
            return bool(item and item.wanted and item.status == "actif")

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [{"name": i.name, "desc": i.desc, "on": i.wanted, "status": i.status} for i in self._items.values()]

    def stop_all(self) -> None:
        for item in self._items.values():
            if item.engine is not None and item.status == "actif":
                try:
                    item.engine.stop()
                except Exception:
                    log.exception("arrêt du capteur %s", item.name)

    def _publish(self) -> None:
        snap = self.snapshot()
        bus.publish("sensors", items=snap)
        for s in snap:
            graph.set_sensor(s["name"], "on" if s["on"] and s["status"] == "actif" else ("wanted" if s["on"] else "off"))

    def _save(self) -> None:
        with self._lock:
            data = {n: i.wanted for n, i in self._items.items()}
        try:
            STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _load() -> dict:
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}


sensors = Sensors()
