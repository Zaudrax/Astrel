"""Compte Claude de la machine (17/10, plan P4, lot 2 : page « Réglages », groupe « Compte »).

Astrel tourne sur l'abonnement Claude de la personne connectée à Claude Code. Ce
module ne fait que DEMANDER au CLI officiel :
- état : `claude auth status --json`, délai 15 s, gardé 30 s, lu HORS du fil du cœur
  (un fil à part, puis `on_change` republie la page) ; seuls cinq champs sont gardés
  (`FIELDS`), rien d'autre ;
- connexion : `claude auth login --claudeai`, stdin fermé, sortie jetée sauf la
  première adresse https (montrée en lien de secours : sous WSL2 le navigateur peut ne
  pas s'ouvrir) ; une seule à la fois, abandonnée au bout de 5 min, annulable ;
- déconnexion : `claude auth logout` (le HUD demande confirmation).

Ce module ne lit JAMAIS `~/.claude/.credentials.json` et ne voit aucun jeton. L'e-mail
n'est jamais écrit au journal (il ne part qu'au HUD, en local).
Non vérifié : une connexion réelle sans terminal (navigateur, retour local, WSL2), et
la reprise du cerveau après un changement de compte (à essayer avec l'ami).
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from typing import Any, Callable

from . import config

log = logging.getLogger("astrel.compte")

FIELDS = ("loggedIn", "authMethod", "subscriptionType", "email", "orgName")
STATUS_TIMEOUT_S = 15.0
CACHE_S = 30.0
LOGIN_TIMEOUT_S = 300.0
LOGOUT_TIMEOUT_S = 10.0
INSTALL_URL = "https://docs.claude.com/claude-code"
_URL = re.compile(r"https://[^\s\"'<>]+")
SUBSCRIPTIONS = {"max": "Max", "pro": "Pro", "team": "Team", "enterprise": "Enterprise", "free": "gratuit"}


class Compte:
    def __init__(self, on_change: Callable[[], Any] | None = None) -> None:
        self.on_change = on_change
        self._lock = threading.Lock()
        self._state: dict | None = None      # FIELDS, ou {"absent": True}, ou {"error": "…"}
        self._at = 0.0                       # time.monotonic() de la dernière lecture
        self._refreshing = False
        self._login: subprocess.Popen | None = None
        self._login_url = ""
        self._message: tuple[str, str] = ("", "")  # (texte, niveau) de la dernière connexion

    # ------------------------------------------------------------------ état

    def _fetch(self) -> dict:
        """Une lecture de `claude auth status --json` (bloquante, jusqu'à 15 s)."""
        try:
            proc = subprocess.run([config.CLAUDE_BIN, "auth", "status", "--json"], stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=STATUS_TIMEOUT_S,
                                  encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return {"absent": True}
        except subprocess.TimeoutExpired:
            return {"error": "Claude Code n'a pas répondu à temps"}
        except OSError as exc:
            return {"error": f"Claude Code injoignable ({exc.strerror or exc})"}
        try:
            data = json.loads(proc.stdout or "")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            # Non connecté : certaines versions sortent en erreur sans JSON.
            return {"error": "état illisible (version de Claude Code trop ancienne ? « claude update »)"}
        return {k: data.get(k) for k in FIELDS}

    def refresh(self, wait: bool = False) -> None:
        """Relit l'état dans un fil à part (un seul à la fois), puis prévient `on_change`."""
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True
        t = threading.Thread(target=self._refresh_now, name="compte-status", daemon=True)
        t.start()
        if wait:
            t.join(STATUS_TIMEOUT_S + 1)

    def _refresh_now(self) -> None:
        try:
            state = self._fetch()
        except Exception as exc:  # jamais de fil mort en silence
            log.exception("état du compte illisible")
            state = {"error": str(exc)}
        with self._lock:
            self._state, self._at, self._refreshing = state, time.monotonic(), False
        log.info("compte Claude : %s", "absent" if state.get("absent") else
                 ("connecté" if state.get("loggedIn") else "non connecté") if "error" not in state else state["error"])
        self._changed()

    def state(self) -> dict | None:
        """État gardé (None : pas encore lu). Une lecture de plus de 30 s est relancée en fond."""
        with self._lock:
            state, stale = self._state, time.monotonic() - self._at > CACHE_S
        if state is None or stale:
            self.refresh()
        return dict(state) if state is not None else None

    def _changed(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                log.exception("page Réglages non republiée")

    # ------------------------------------------------------------------ vue (page « Réglages »)

    def logging_in(self) -> bool:
        with self._lock:
            return self._login is not None

    def view(self) -> dict:
        """Ligne `info` du contrat (lines, link) et ses boutons (`actions`)."""
        s = self.state()
        lines: list[dict] = []
        actions: list[dict] = []
        link = None
        with self._lock:
            pending, url = self._login is not None, self._login_url
            message, level = self._message
        if s is None:
            lines.append({"label": "État", "text": "vérification…", "level": "info"})
        elif s.get("absent"):
            lines.append({"label": "Claude Code", "text": "introuvable sur cette machine", "level": "error"})
            lines.append({"label": "À faire", "text": "installe Claude Code, puis reviens ici pour te connecter",
                          "level": "info"})
            link = {"label": "Installer Claude Code", "url": INSTALL_URL}
        elif s.get("error"):
            lines.append({"label": "État", "text": s["error"], "level": "warn"})
        elif s.get("loggedIn"):
            lines.append({"label": "État", "text": "connecté", "level": "ok"})
            sub = str(s.get("subscriptionType") or "")
            if sub:
                lines.append({"label": "Abonnement", "text": SUBSCRIPTIONS.get(sub.lower(), sub), "level": "info"})
            for key, label in (("email", "E-mail"), ("orgName", "Organisation")):
                if s.get(key):
                    lines.append({"label": label, "text": str(s[key]), "level": "info"})
            method = s.get("authMethod")
            if method not in (None, "", "claude.ai"):
                # 20/10 (plan P11) : une clé API est un état NORMAL (même texte que tools/check_install.py).
                lines.append({"label": "Facturation", "level": "info",
                              "text": f"connecté par {method} : clé API, facturée à l'usage"})
        else:
            lines.append({"label": "État", "text": "non connecté", "level": "warn"})
        used = _fournisseur_line()
        if used:
            lines.append(used)
        if pending:
            lines.append({"label": "Connexion", "level": "info",
                          "text": "en cours : termine-la dans le navigateur (5 min au plus)"})
            if url:
                link = {"label": "Page de connexion (si le navigateur ne s'est pas ouvert)", "url": url}
            actions.append({"id": "cancel", "label": "Annuler la connexion"})
        elif message:
            lines.append({"label": "Dernière connexion", "text": message, "level": level})
        if s is not None and not s.get("absent") and not pending:
            if s.get("loggedIn"):
                actions.append({"id": "logout", "label": "Se déconnecter", "danger": True,
                                "confirm": "Astrel ne pourra plus répondre tant que personne ne se reconnecte."})
            else:
                actions.append({"id": "login", "label": "Se connecter"})
        actions.append({"id": "refresh", "label": "Revérifier"})
        out: dict = {"lines": lines}
        if link:
            out["link"] = link
        return {"value": out, "actions": actions}

    # ------------------------------------------------------------------ actions

    def run(self, action: str, cmd: dict | None = None) -> dict:
        if action == "refresh":
            self.refresh()
            return {"ok": True, "text": "Vérification lancée.", "level": "info"}
        if action == "login":
            return self.login()
        if action == "cancel":
            return self.cancel()
        if action == "logout":
            return self.logout()
        return {"ok": False, "error": f"action inconnue : {action or '(vide)'}"}

    def login(self) -> dict:
        with self._lock:
            if self._login is not None:
                return {"ok": False, "error": "une connexion est déjà en cours"}
            try:
                proc = subprocess.Popen([config.CLAUDE_BIN, "auth", "login", "--claudeai"], stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                        encoding="utf-8", errors="replace")
            except FileNotFoundError:
                return {"ok": False, "error": "Claude Code introuvable : installe-le d'abord.",
                        "link": {"label": "Installer Claude Code", "url": INSTALL_URL}}
            except OSError as exc:
                return {"ok": False, "error": f"connexion impossible à lancer : {exc}"}
            self._login, self._login_url, self._message = proc, "", ("", "")
        log.info("connexion au compte Claude lancée")
        threading.Thread(target=self._watch_login, args=(proc,), name="compte-login", daemon=True).start()
        self._changed()
        return {"ok": True, "level": "info",
                "text": "Le navigateur s'ouvre sur la page de connexion de Claude ; sinon, utilise le lien ci-dessus."}

    def _watch_login(self, proc: subprocess.Popen) -> None:
        timer = threading.Timer(LOGIN_TIMEOUT_S, self._expire, args=(proc,))
        timer.daemon = True
        timer.start()
        try:
            for line in proc.stdout or ():
                m = _URL.search(line)
                with self._lock:
                    found = bool(m) and self._login is proc and not self._login_url
                    if found:
                        self._login_url = m.group(0)
                if found:
                    self._changed()
                # le reste de la sortie est jeté (jamais au journal)
            code = proc.wait()
        finally:
            timer.cancel()
        with self._lock:
            mine = self._login is proc
            if mine:
                self._login, self._login_url = None, ""
                if self._message[0] == "":
                    self._message = (("connexion terminée", "ok") if code == 0
                                     else ("connexion non aboutie", "warn"))
        if mine:
            log.info("connexion au compte Claude finie (code %s)", code)
            self.refresh()

    def _expire(self, proc: subprocess.Popen) -> None:
        with self._lock:
            if self._login is not proc:
                return
            self._message = ("abandonnée au bout de 5 min", "warn")
        log.info("connexion au compte Claude abandonnée (délai)")
        _stop(proc)

    def cancel(self) -> dict:
        with self._lock:
            proc = self._login
            if proc is None:
                return {"ok": True, "text": "Aucune connexion en cours.", "level": "info"}
            self._message = ("annulée", "info")
        _stop(proc)
        return {"ok": True, "text": "Connexion annulée.", "level": "info"}

    def logout(self) -> dict:
        if self.logging_in():
            return {"ok": False, "error": "une connexion est en cours : annule-la d'abord"}
        try:
            proc = subprocess.run([config.CLAUDE_BIN, "auth", "logout"], stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=LOGOUT_TIMEOUT_S)
        except FileNotFoundError:
            return {"ok": False, "error": "Claude Code introuvable."}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "error": f"déconnexion impossible : {exc}"}
        log.info("déconnexion du compte Claude (code %s)", proc.returncode)
        with self._lock:
            self._message = ("", "")
        self.refresh()
        if proc.returncode != 0:
            return {"ok": False, "error": "Claude Code a refusé la déconnexion."}
        return {"ok": True, "level": "ok", "text": "Déconnecté. Astrel ne répondra plus avant une nouvelle connexion."}


def _fournisseur_line() -> dict | None:
    """Plan P11 : Astrel peut tourner sur autre chose que ce compte (clé API, autre fournisseur)."""
    from .fournisseurs import etat

    a = etat.actif
    if a.genre == "abonnement":
        return None
    return {"label": "Astrel utilise", "level": "info",
            "text": f"{a.libelle()} (Réglages › Fournisseur), pas ce compte"}


def _stop(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
    except OSError:
        pass


compte = Compte()
