"""Mises à jour (17/10, plan P4, lot 7 : page « Réglages »).

- Version : `astrel.__version__` et le commit en cours.
- « Vérifier » : `git fetch` (fil à part), puis le nombre de commits d'avance du dépôt.
- « Mettre à jour » : lance `update.py` (JAMAIS `update.sh`), détaché (il relance Astrel),
  SEULEMENT s'il y a un dépôt d'origine et si aucun fichier suivi n'est modifié ; sinon le bouton est grisé avec sa raison.
Rien n'est jamais poussé.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading
import time
from typing import Any, Callable

from . import __version__, config, hors_ligne

log = logging.getLogger("astrel.maj")

FETCH_TIMEOUT_S = 60.0
LOCAL_CACHE_S = 30.0


def _git(*args: str, timeout: float = 10.0) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", *args], cwd=str(config.ROOT), capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None


def _out(*args: str) -> str:
    p = _git(*args)
    return p.stdout.strip() if p is not None and p.returncode == 0 else ""


def clean_url(url: str) -> str:
    """Adresse du dépôt sans identifiants (« https://jeton@github.com/… »)."""
    return re.sub(r"^(\w+://)[^/@]+@", r"\1", url.strip())


class MiseAJour:
    def __init__(self, on_change: Callable[[], Any] | None = None) -> None:
        self.on_change = on_change
        self._lock = threading.Lock()
        self._local: dict | None = None
        self._local_at = 0.0
        self._checking = False
        self._remote: dict = {}         # {"behind": n, "at": t} ou {"error": "…", "at": t}
        self._launched = False

    # ------------------------------------------------------------------ état local

    def local(self) -> dict:
        """Commit, origine, fichiers suivis modifiés (gardé 30 s)."""
        with self._lock:
            if self._local is not None and time.monotonic() - self._local_at < LOCAL_CACHE_S:
                return dict(self._local)
        if not _out("rev-parse", "--is-inside-work-tree"):
            state = {"git": False}
        else:
            status = _git("status", "--porcelain", "--untracked-files=no")
            changed = len([x for x in (status.stdout if status else "").splitlines() if x.strip()])
            state = {"git": True, "commit": _out("rev-parse", "--short", "HEAD"),
                     "origin": clean_url(_out("remote", "get-url", "origin")),
                     "changed": changed if status is not None and status.returncode == 0 else -1}
        with self._lock:
            self._local, self._local_at = state, time.monotonic()
        return dict(state)

    def blocked(self, state: dict) -> str:
        """Raison de ne PAS toucher au dépôt (vide : permis)."""
        if hors_ligne.actif():  # 04/11 : git fetch et update.py parlent au dépôt distant
            return hors_ligne.REFUS
        if not state.get("git"):
            return "pas un dépôt git : mise à jour à la main"
        origin = str(state.get("origin") or "").lower()
        if not origin:
            return "aucun dépôt d'origine"
        return ""

    # ------------------------------------------------------------------ vue

    def view(self) -> dict:
        s = self.local()
        with self._lock:
            remote, checking, launched = dict(self._remote), self._checking, self._launched
        version = __version__ + (f" (commit {s['commit']})" if s.get("commit") else "")
        lines = [{"label": "Version", "text": version, "level": "info"}]
        if s.get("origin"):
            lines.append({"label": "Dépôt", "text": s["origin"], "level": "info"})
        blocked = self.blocked(s)
        if checking:
            lines.append({"label": "État", "text": "vérification…", "level": "info"})
        elif remote.get("error"):
            lines.append({"label": "État", "text": remote["error"], "level": "warn"})
        elif "behind" in remote:
            n = int(remote["behind"])
            lines.append({"label": "État", "level": "warn" if n else "ok",
                          "text": f"{n} nouveauté(s) disponible(s)" if n else "à jour"})
        elif not blocked:
            lines.append({"label": "État", "text": "pas encore vérifié", "level": "info"})
        if launched:
            lines.append({"label": "Mise à jour", "text": "lancée : Astrel va redémarrer (journal : logs/update.log)",
                          "level": "info"})
        reason = blocked
        if not reason and s.get("changed", 0):
            reason = (f"{s['changed']} fichier(s) suivi(s) modifié(s) ici : mise à jour à la main"
                      if s["changed"] > 0 else "état du dépôt illisible")
        if not reason and launched:
            reason = "mise à jour déjà lancée"
        if not reason and remote.get("behind", None) == 0:
            reason = "déjà à jour"
        actions = [{"id": "check", "label": "Vérifier", "disabled": bool(blocked or checking),
                    "reason": blocked or ("vérification en cours" if checking else "")},
                   {"id": "update", "label": "Mettre à jour", "disabled": bool(reason), "reason": reason,
                    "confirm": "Astrel se met à jour puis redémarre ; les tâches de fond en cours seront coupées."}]
        return {"value": {"lines": lines}, "actions": actions}

    # ------------------------------------------------------------------ actions

    def run(self, action: str, cmd: dict | None = None) -> dict:
        if action == "check":
            return self.check()
        if action == "update":
            return self.update()
        return {"ok": False, "error": f"action inconnue : {action or '(vide)'}"}

    def check(self, wait: bool = False) -> dict:
        reason = self.blocked(self.local())
        if reason:
            return {"ok": False, "error": reason}
        with self._lock:
            if self._checking:
                return {"ok": True, "text": "Vérification déjà en cours.", "level": "info"}
            self._checking = True
        t = threading.Thread(target=self._fetch, name="maj-fetch", daemon=True)
        t.start()
        if wait:
            t.join(FETCH_TIMEOUT_S + 5)
        return {"ok": True, "text": "Vérification lancée.", "level": "info"}

    def _fetch(self) -> None:
        p = _git("fetch", "--quiet", "origin", timeout=FETCH_TIMEOUT_S)
        if p is None or p.returncode != 0:
            remote = {"error": "dépôt injoignable (réseau ?)", "at": time.time()}
        else:
            raw = _out("rev-list", "--count", "HEAD..@{upstream}")
            remote = ({"behind": int(raw), "at": time.time()} if raw.isdigit()
                      else {"error": "aucune branche suivie sur le dépôt", "at": time.time()})
        with self._lock:
            self._remote, self._checking = remote, False
            self._local = None  # relu au prochain affichage
        log.info("mises à jour vérifiées : %s", remote.get("error") or f"{remote['behind']} en retard")
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                log.exception("page Réglages non republiée")

    def update(self) -> dict:
        with self._lock:
            self._local = None
        spec = next((a for a in self.view()["actions"] if a["id"] == "update"), {})
        if spec.get("disabled"):
            return {"ok": False, "error": spec.get("reason") or "mise à jour impossible"}
        log_file = config.ROOT / "logs" / "update.log"
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with log_file.open("a", encoding="utf-8") as out:
                # Détaché : update.py relance Astrel (service) ; il ne doit pas mourir avec lui.
                subprocess.Popen([sys.executable, str(config.ROOT / "update.py")], cwd=str(config.ROOT),
                                 stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        except OSError as exc:
            return {"ok": False, "error": f"mise à jour impossible à lancer : {exc}"}
        with self._lock:
            self._launched = True
        log.info("mise à jour lancée (update.py)")
        return {"ok": True, "level": "info", "text": "Mise à jour lancée : Astrel redémarre tout seul à la fin."}


maj = MiseAJour()


# Points d'entrée de la page « Réglages » (l'instance est relue à chaque appel : les tests la remplacent).
def view_maj() -> dict:
    return maj.view()


def run_maj(action: str, cmd: dict | None = None) -> dict:
    return maj.run(action, cmd)
