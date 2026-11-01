"""Levée du garde-fou : ce qui est surveillé pendant qu'elle est active (18/10, plan P7).

Demande de l'utilisateur : un interrupteur des Réglages du HUD, sans durée, pour faire
travailler Claude sur le code d'Astrel sans une carte par fichier. Qui peut la
lever ou la rétablir : `totalgate` (fichier protégé). L'état lui-même est dans le
cœur (`core.Astrel.set_lift`), en mémoire seulement.

Ici, la surveillance :
- à l'activation, empreinte sha256 des fichiers levables (`guard.LIFTABLE`, sans
  `node_modules`) et de ceux du contrôle de santé (`doctor.GUARD_FILES`) ;
- toutes les `CHECK_S` secondes, la liste des fichiers changés depuis, publiée au
  bandeau du HUD ;
- toutes les `REMIND_S` secondes, un rappel discret (jamais dit) ;
- à la coupure, un bilan : relire, puis `./run.sh doctor --accept` dans le
  terminal de l'utilisateur (jamais lancé par Astrel : le doctor le refuse à Claude).

La surveillance DÉTECTE, elle n'empêche rien : un garde-fou réécrit pendant la
levée le reste après elle.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable

from . import config

log = logging.getLogger("astrel.levee")

CHECK_S = 60.0
REMIND_S = 20 * 60.0
SKIP_DIRS = {"node_modules", "__pycache__", ".git"}


def watched() -> list[Path]:
    """Fichiers surveillés : levables (dossiers parcourus) et fichiers du garde-fou."""
    from . import doctor, guard

    files: set[Path] = set()
    for root in [Path(p) for p in guard.LIFTABLE] + [Path(p) for p in doctor.GUARD_FILES]:
        if root.is_dir():
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                files.update(Path(dirpath) / f for f in filenames)
        else:
            files.add(root)  # absent aussi : son apparition compte
    return sorted(files)


def fingerprint(files: list[Path]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in files:
        try:
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            out[str(p)] = "absent"
    return out


def _rel(path: str) -> str:
    try:
        return str(Path(path).relative_to(config.ROOT))
    except ValueError:
        return path


def diff(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Fichiers (chemins relatifs au dossier d'Astrel) ajoutés, modifiés ou disparus."""
    keys = set(before) | set(after)
    return sorted(_rel(k) for k in keys if before.get(k, "absent") != after.get(k, "absent"))


def summary(files: list[str], limit: int = 8) -> str:
    """Bilan dit à la coupure (affiché, jamais dit)."""
    if not files:
        return "Garde-fou rétabli. Aucun fichier du garde-fou ni du HUD n'a changé pendant la levée."
    shown = ", ".join(files[:limit]) + (f" et {len(files) - limit} autre(s)" if len(files) > limit else "")
    return (f"Garde-fou rétabli. Modifiés pendant la levée : {shown}. Relis-les, puis lance "
            "« ./run.sh doctor --accept » dans ton terminal (Astrel ne le fait jamais).")


class Watch:
    """Surveillance d'une levée : `start()` prend l'empreinte, un fil la compare toutes
    les `check_s` secondes (`on_change(fichiers)` si la liste change) et rappelle toutes
    les `remind_s` secondes (`on_remind()`) ; `stop()` rend la liste finale."""

    def __init__(self, on_change: Callable[[list[str]], None] | None = None,
                 on_remind: Callable[[], None] | None = None,
                 check_s: float = CHECK_S, remind_s: float = REMIND_S) -> None:
        self.on_change = on_change
        self.on_remind = on_remind
        self.check_s = check_s
        self.remind_s = remind_s
        self.before: dict[str, str] = {}
        self.changed: list[str] = []
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.before = fingerprint(watched())
        self._thread = threading.Thread(target=self._run, name="levee", daemon=True)
        self._thread.start()

    def check(self) -> list[str]:
        with self._lock:
            files = sorted(set(watched()) | {Path(k) for k in self.before})
            now = diff(self.before, fingerprint(files))
            moved = now != self.changed
            self.changed = now
        if moved and self.on_change is not None:
            try:
                self.on_change(now)
            except Exception:
                log.exception("levée : publication des fichiers changés")
        return now

    def _run(self) -> None:
        next_remind = time.monotonic() + self.remind_s
        while not self._stop.wait(self.check_s):
            try:
                self.check()
            except Exception:
                log.exception("levée : contrôle des fichiers")
            if time.monotonic() >= next_remind and not self._stop.is_set():
                next_remind = time.monotonic() + self.remind_s
                if self.on_remind is not None:
                    try:
                        self.on_remind()
                    except Exception:
                        log.exception("levée : rappel")

    def stop(self) -> list[str]:
        """Arrête la surveillance et rend les fichiers changés depuis la levée."""
        self._stop.set()
        self.on_change = None  # plus de publication : le cœur publie lui-même la coupure
        try:
            return self.check()
        except Exception:
            log.exception("levée : dernier contrôle")
            return list(self.changed)
