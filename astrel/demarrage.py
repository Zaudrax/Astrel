"""Premier lancement et diagnostic (17/10, plan P4, lot 4 : page « Réglages »).

- Liste de contrôle : `tools/check_install.py --json` lancé en SOUS-PROCESSUS (il peut
  mettre plusieurs secondes : `claude auth status`, paquets du système), dans un fil à
  part ; le résultat est gardé jusqu'à « Revérifier ».
- Premier lancement : vrai tant que `runtime/first-run-done` n'existe pas. 18/10 (plan
  P5) : la conversation passée (`runtime/session.json`) ne compte plus, un ami qui a
  déjà essayé `./run.sh --text` doit quand même voir le guide. « J'ai fini » ici, ou
  « Terminé » dans le guide, pose le marqueur.
- Diagnostic : le « rapport à coller » de check_install.py, déjà anonymisé (ni dossier
  personnel, ni nom d'utilisateur, ni e-mail), avec un bouton Copier dans le HUD.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from typing import Any, Callable

from . import config

log = logging.getLogger("astrel.demarrage")

CHECK_TIMEOUT_S = 90.0
LEVELS = {"OK": "ok", "MANQUE": "error", "ALERTE": "warn", "INFO": "info"}


def first_run() -> bool:
    return not config.FIRST_RUN_FILE.exists()


def mark_first_run_done() -> dict:
    """Pose le marqueur : la page « Réglages » et le guide ne s'ouvrent plus seuls."""
    try:
        config.FIRST_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.FIRST_RUN_FILE.write_text(time.strftime("%Y-%m-%d %H:%M:%S\n"), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"marqueur non écrit : {exc}"}
    log.info("premier lancement terminé (marqueur posé)")
    return {"ok": True}


class Demarrage:
    def __init__(self, on_change: Callable[[], Any] | None = None) -> None:
        self.on_change = on_change
        self._lock = threading.Lock()
        self._result: dict | None = None   # {"checks": [...], "bloquant": n, "rapport": "…"} ou {"error": "…"}
        self._at = 0.0
        self._running = False
        self._want_report = False

    # ------------------------------------------------------------------ contrôle

    def _command(self) -> list[str]:
        return [sys.executable, str(config.ROOT / "tools" / "check_install.py"), "--json"]

    def _fetch(self) -> dict:
        try:
            proc = subprocess.run(self._command(), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  timeout=CHECK_TIMEOUT_S, cwd=str(config.ROOT), encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return {"error": "le contrôle n'a pas fini à temps"}
        except OSError as exc:
            return {"error": f"contrôle impossible à lancer : {exc}"}
        try:
            data = json.loads(proc.stdout or "")
        except ValueError:
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("checks"), list):
            return {"error": "résultat du contrôle illisible"}
        return data

    def refresh(self, wait: bool = False) -> bool:
        """Lance le contrôle dans un fil à part ; False s'il tourne déjà."""
        with self._lock:
            if self._running:
                return False
            self._running = True
        t = threading.Thread(target=self._run, name="demarrage-controle", daemon=True)
        t.start()
        if wait:
            t.join(CHECK_TIMEOUT_S + 5)
        return True

    def _run(self) -> None:
        try:
            result = self._fetch()
        except Exception as exc:
            log.exception("contrôle de la machine")
            result = {"error": str(exc)}
        with self._lock:
            self._result, self._at, self._running = result, time.time(), False
        log.info("contrôle de la machine : %s", result.get("error") or f"{result.get('bloquant', 0)} bloquant(s)")
        self._changed()

    def _changed(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                log.exception("page Réglages non republiée")

    def _snapshot(self) -> tuple[dict | None, bool, bool]:
        with self._lock:
            result, running, want = self._result, self._running, self._want_report
        if result is None and not running:
            self.refresh()
            running = True
        return result, running, want

    def check_status(self, name: str) -> str | None:
        """Statut (OK, MANQUE, ALERTE, INFO) d'un contrôle déjà fait, None sinon ; ne
        relance rien (guide de démarrage, plan P5)."""
        with self._lock:
            result = self._result
        for c in (result or {}).get("checks") or []:
            if isinstance(c, dict) and c.get("name") == name:
                return str(c.get("status") or "")
        return None

    # ------------------------------------------------------------------ vues

    def view(self) -> dict:
        """Liste de contrôle (ligne `info`)."""
        result, running, _ = self._snapshot()
        lines: list[dict] = []
        if result is None:
            lines.append({"label": "Contrôle", "text": "en cours…", "level": "info"})
        elif result.get("error"):
            lines.append({"label": "Contrôle", "text": result["error"], "level": "warn"})
        else:
            for c in result["checks"]:
                if not isinstance(c, dict):
                    continue
                status = str(c.get("status") or "INFO")
                text = str(c.get("detail") or status.lower())
                if c.get("fix") and status != "OK":
                    text += f" → {c['fix']}"
                lines.append({"label": str(c.get("name") or "?"), "text": text, "level": LEVELS.get(status, "info")})
            n = int(result.get("bloquant") or 0)
            lines.insert(0, {"label": "Bilan", "level": "error" if n else "ok",
                             "text": f"{n} point(s) bloquant(s) à corriger" if n else "rien de bloquant"})
            if running:
                lines.insert(1, {"label": "Contrôle", "text": "nouvelle vérification en cours…", "level": "info"})
        actions = [{"id": "check", "label": "Revérifier", "disabled": running,
                    "reason": "vérification en cours" if running else ""}]
        if first_run():
            actions.append({"id": "done", "label": "J'ai fini, ne plus ouvrir au démarrage"})
        return {"value": {"lines": lines}, "actions": actions}

    def diagnostic_view(self) -> dict:
        """Rapport à coller (ligne `action` avec bloc copiable, après un clic)."""
        with self._lock:
            result, running, want = self._result, self._running, self._want_report
        value: dict = {"lines": []}
        if want and running:
            value["lines"].append({"label": "Rapport", "text": "en préparation…", "level": "info"})
        elif want and result and result.get("rapport"):
            value["block"] = str(result["rapport"])
        elif want and result and result.get("error"):
            value["lines"].append({"label": "Rapport", "text": result["error"], "level": "warn"})
        label = "Refaire le rapport" if want else "Préparer le rapport"
        return {"value": value, "actions": [{"id": "report", "label": label, "disabled": running,
                                             "reason": "vérification en cours" if running else ""}]}

    # ------------------------------------------------------------------ actions

    def run(self, action: str, cmd: dict | None = None) -> dict:
        if action == "check":
            self.refresh()
            return {"ok": True, "text": "Vérification lancée (quelques secondes).", "level": "info"}
        if action == "done":
            res = mark_first_run_done()
            if not res["ok"]:
                return res
            return {"ok": True, "text": "C'est noté : la page ne s'ouvrira plus seule.", "level": "ok"}
        return {"ok": False, "error": f"action inconnue : {action or '(vide)'}"}

    def run_diagnostic(self, action: str, cmd: dict | None = None) -> dict:
        if action != "report":
            return {"ok": False, "error": f"action inconnue : {action or '(vide)'}"}
        with self._lock:
            self._want_report = True
        self.refresh()
        return {"ok": True, "level": "info",
                "text": "Rapport en préparation : il s'affiche ici, sans donnée personnelle, avec un bouton Copier."}


demarrage = Demarrage()
