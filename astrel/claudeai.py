"""Connecteurs claude.ai du compte, DÉCOUVERTS (19/10, plan P10, demande de l'utilisateur :
la page « Réglages » ne doit plus tenir une liste écrite à la main).

- découverte : `claude mcp list` (CLI officiel, lecture seule), lancé depuis un dossier
  temporaire VIDE (pas de `.mcp.json` de projet), stdin fermé, délai 20 s ; seules les
  lignes `claude.ai <Nom>: <url> - <statut>` sont gardées, toute autre ligne est ignorée
  (le format n'est pas un contrat). Mesuré le 19/10 : 11 connecteurs, ≈ 5,5 s ; les
  connecteurs bloqués pour Astrel (`deniedMcpServers`) y figurent quand même, et ceux qui
  attendent une connexion (« Needs authentication ») aussi : Astrel ne la crée pas.
- cache : en mémoire, relu en fond au-delà de `TTL_S` (un seul fil à la fois, puis
  `on_change` republie la page), et `runtime/connecteurs-vus.json` = dernier succès,
  relu au démarrage. `names()` ne bloque JAMAIS (la page est décrite à chaque publication).
- échec (CLI absent, délai, sortie sans connecteur) : le dernier résultat vu reste,
  jamais une liste réduite (sinon `Reglages._load` oublierait les choix de l'utilisateur).
Ne lance jamais `claude mcp add|remove` (critiques pour le garde-fou).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable

from . import config, icones

log = logging.getLogger("astrel.claudeai")

PREFIX = "claude.ai "
TTL_S = 600.0
TIMEOUT_S = 20.0
# « claude.ai Google Drive: https://… - ✔ Connected » : le nom s'arrête au premier « : ».
_LINE = re.compile(r"^(claude\.ai [^:\n]{1,100}):\s+\S")


SEEN_FILE = config.RUNTIME / "connecteurs-vus.json"


def parse(text: str) -> list[str]:
    """Noms des connecteurs claude.ai d'une sortie de `claude mcp list`, sans doublon,
    triés (l'ordre du CLI suit la fin des contrôles de santé : il change d'une fois à l'autre)."""
    found = {m.group(1).strip() for line in (text or "").splitlines() if (m := _LINE.match(line.strip()))}
    return sorted(found, key=str.casefold)


def discover() -> list[str] | None:
    """Une lecture bloquante (jusqu'à `TIMEOUT_S`) ; None si elle échoue ou ne trouve rien."""
    try:
        with tempfile.TemporaryDirectory(prefix="astrel-mcp-list-") as tmp:
            proc = subprocess.run([config.CLAUDE_BIN, "mcp", "list"], cwd=tmp, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=TIMEOUT_S,
                                  encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("connecteurs claude.ai non relus : %s", exc)
        return None
    names = parse(proc.stdout)
    if not names:
        log.warning("connecteurs claude.ai : aucun dans la sortie de « claude mcp list » (code %s), "
                    "dernière liste gardée", proc.returncode)
        return None
    return names


class Connecteurs:
    def __init__(self, on_change: Callable[[], Any] | None = None) -> None:
        self.on_change = on_change
        self._lock = threading.Lock()
        self._names: list[str] | None = None   # None : fichier pas encore relu
        self._at = 0.0                          # time.monotonic() de la dernière découverte (0 : jamais)
        self._thread: threading.Thread | None = None  # découverte en cours

    def _read_seen(self) -> list[str]:
        try:
            data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        names = data.get("names") if isinstance(data, dict) else None
        return [str(n) for n in names if isinstance(n, str) and n.startswith(PREFIX)] if isinstance(names, list) else []

    def _write_seen(self, names: list[str]) -> None:
        path = SEEN_FILE
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps({"names": names, "at": time.time()}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("connecteurs vus non enregistrés : %s", exc)

    def names(self) -> list[str]:
        """Dernière liste connue (mémoire, sinon fichier ; [] si aucune découverte n'a jamais
        abouti). Au-delà de `TTL_S`, relance une découverte en fond. Ne bloque jamais."""
        with self._lock:
            if self._names is None:
                self._names = self._read_seen()
            names, stale = list(self._names), time.monotonic() - self._at > TTL_S or not self._at
        if stale:
            self.refresh()
        return names

    def refresh(self, wait: bool = False) -> None:
        """Relit `claude mcp list` dans un fil à part (un seul à la fois ; `wait` attend
        aussi celui qui tournait déjà)."""
        with self._lock:
            t = self._thread
            if t is None:
                t = self._thread = threading.Thread(target=self._refresh_now, name="connecteurs-claudeai",
                                                    daemon=True)
                t.start()
        if wait:
            t.join(TIMEOUT_S + 2)

    def _refresh_now(self) -> None:
        changed, found = False, None
        try:
            try:
                found = discover()
            except Exception:  # jamais de fil mort en silence
                log.exception("connecteurs claude.ai non relus")
                found = None
            with self._lock:
                # Échec : on ne réessaie qu'après TTL_S (pas à chaque publication de la page).
                self._at = time.monotonic()
                if self._names is None:
                    self._names = self._read_seen()
                changed = bool(found) and found != self._names
                if changed:
                    self._names = list(found)
            if changed:
                self._write_seen(found)
                log.info("connecteurs claude.ai : %d découverts", len(found))
            with self._lock:
                known = list(self._names or [])
        finally:
            with self._lock:
                self._thread = None
        if changed:
            self._publish()
        # 19/10 (demande de l'utilisateur) : l'icône d'un connecteur nouveau arrive d'elle-même,
        # cherchée une fois en fond (`icones.fetch_missing`), puis la page est republiée.
        try:
            arrived = icones.fetch_missing(known)
        except Exception:
            log.exception("icônes des connecteurs non récupérées")
            arrived = False
        if arrived:
            self._publish()

    def _publish(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                log.exception("page Réglages non republiée")


connecteurs = Connecteurs()
