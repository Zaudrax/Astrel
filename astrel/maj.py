"""Mises à jour (17/10, plan P4, lot 7 : page « Réglages »).

- Version : fichier `VERSION` (10/11, `nouveautes.py` ; repli `astrel.__version__`) et le commit en cours.
- « Vérifier » : `git fetch` (fil à part), puis le nombre de commits d'avance du dépôt, la version
  (`VERSION`) et les notes (`NOUVEAUTES.md`) lues dans la branche suivie par `git show`, sans rien modifier.
- 10/11 : vérification AUTOMATIQUE (`tick_auto`, tick du cœur) une minute après le démarrage puis toutes
  les 24 h ; `ASTREL_MAJ_AUTO=0` la coupe, « rien ne sort » aussi (`blocked`). Évènement STICKY `maj_dispo`
  pour le HUD (`hud/src/maj.js`) : pastille, et fenêtre des nouveautés ouverte d'elle-même pour une
  version MAJEURE (une fois par version et par navigateur).
- « Mettre à jour » : lance `update.py` (JAMAIS `update.sh`), détaché (il relance Astrel),
  SEULEMENT s'il y a un dépôt d'origine et si aucun fichier suivi n'est modifié ; sinon le bouton est grisé avec sa raison.
  Rien ne s'installe sans ce clic.
Rien n'est jamais poussé.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from typing import Any, Callable

from . import __version__, config, hors_ligne, nouveautes
from . import events as events_mod
from .events import bus

log = logging.getLogger("astrel.maj")

events_mod.STICKY.add("maj_dispo")  # rejoué à chaque HUD qui se connecte

FETCH_TIMEOUT_S = 60.0
LOCAL_CACHE_S = 30.0
AUTO_PREMIER_S = 60.0          # première vérification automatique, après le démarrage
AUTO_PERIODE_S = 24 * 3600.0   # puis une par jour
VERSION_FILE = "VERSION"
NOTES_FILE = "NOUVEAUTES.md"


def version_locale() -> str:
    """Version installée : fichier `VERSION`, sinon `astrel.__version__`."""
    try:
        v = nouveautes.lire_version((config.ROOT / VERSION_FILE).read_text(encoding="utf-8"))
    except OSError:
        v = ""
    return v or __version__


def auto_actif() -> bool:
    return os.environ.get("ASTREL_MAJ_AUTO", "1").strip().lower() not in ("0", "false", "non", "off")


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
        self._remote: dict = {}         # {"behind": n, "version": "…", "notes": […], "at": t} ou {"error": "…", "at": t}
        self._launched = False
        self.auto = True                # 10/11 : vérification automatique (les tests la coupent)
        self._auto_next: float | None = None

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
        version = version_locale() + (f" (commit {s['commit']})" if s.get("commit") else "")
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
            nouvelle = str(remote.get("version") or "")
            if n and nouveautes.plus_recente(nouvelle, version_locale()):
                lines.append({"label": "Disponible", "level": "warn",
                              "text": (f"version {nouvelle} (majeure)"
                                       if nouveautes.est_majeure(nouvelle, version_locale())
                                       else f"version {nouvelle}")})
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
            if remote.get("behind"):
                # Version et notes de la branche suivie, lues sans rien toucher à la copie de travail.
                nouvelle = nouveautes.lire_version(_out("show", f"@{{upstream}}:{VERSION_FILE}"))
                remote["version"] = nouvelle
                if nouveautes.plus_recente(nouvelle, version_locale()):
                    remote["notes"] = nouveautes.depuis(_out("show", f"@{{upstream}}:{NOTES_FILE}"),
                                                        version_locale(), nouvelle)
        with self._lock:
            self._remote, self._checking = remote, False
            self._local = None  # relu au prochain affichage
        log.info("mises à jour vérifiées : %s", remote.get("error")
                 or f"{remote['behind']} en retard, version {remote.get('version') or '?'}")
        self.publish_dispo()
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                log.exception("page Réglages non republiée")

    # ------------------------------------------------------------------ annonce (HUD)

    def dispo(self) -> dict:
        """Charge de l'évènement `maj_dispo` : ce qu'il y a de neuf sur le dépôt, et si on peut l'installer."""
        with self._lock:
            remote, launched = dict(self._remote), self._launched
        locale = version_locale()
        behind = int(remote.get("behind") or 0)
        nouvelle = str(remote.get("version") or "")
        if not nouveautes.plus_recente(nouvelle, locale):
            nouvelle = ""
        spec = next((a for a in self.view()["actions"] if a["id"] == "update"), {})
        return {"disponible": behind > 0 and not launched, "behind": behind, "version_locale": locale,
                "version": nouvelle, "majeure": bool(nouvelle) and nouveautes.est_majeure(nouvelle, locale),
                "notes": list(remote.get("notes") or []) if nouvelle else [],
                "bloque": str(spec.get("reason") or "") if spec.get("disabled") else "", "lancee": launched}

    def publish_dispo(self) -> None:
        try:
            bus.publish("maj_dispo", **self.dispo())
        except Exception:
            log.exception("évènement maj_dispo non publié")

    def tick_auto(self, now: float | None = None) -> bool:
        """Tick du cœur : lance la vérification si son heure est venue (vrai si lancée). Bon marché sinon."""
        if not (self.auto and auto_actif()):
            return False
        now = time.monotonic() if now is None else now
        if self._auto_next is None:
            self._auto_next = now + AUTO_PREMIER_S
            return False
        if now < self._auto_next:
            return False
        self._auto_next = now + AUTO_PERIODE_S
        if self.blocked(self.local()):
            return False  # pas de dépôt d'origine, hors ligne… : on réessaiera demain
        return bool(self.check().get("ok"))

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
        self.publish_dispo()
        return {"ok": True, "level": "info", "text": "Mise à jour lancée : Astrel redémarre tout seul à la fin."}


maj = MiseAJour()


# Points d'entrée de la page « Réglages » (l'instance est relue à chaque appel : les tests la remplacent).
def view_maj() -> dict:
    return maj.view()


def run_maj(action: str, cmd: dict | None = None) -> dict:
    return maj.run(action, cmd)
