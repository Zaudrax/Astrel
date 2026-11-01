"""Détecteur de boucles d'outils — ALERTE SEULE, jamais d'interruption.

Idée reprise d'hermes-agent (`agent/tool_guardrails.py`), réimplémentée : un
agent qui relance le même outil avec les mêmes arguments, ou qui reçoit la même
erreur plusieurs fois de suite, brûle l'abonnement sans avancer. Le cœur en est
prévenu (`check_*` rendent une alerte), il l'affiche dans le HUD et glisse un
message discret au cerveau pour qu'il change d'approche. Rien n'est coupé : un
faux positif ne coûte qu'une ligne.

Portée : chaque agent est suivi à part (« main » pour Astrel lui-même, l'id de
l'appel Agent pour un sous-agent). Une même alerte n'est donnée qu'une fois par
portée, jusqu'à ce que la portée soit remise à zéro (fin de tour pour « main »).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any

from . import config

MAIN = "main"
MAX_SCOPES = 32


@dataclass
class Alert:
    scope: str
    kind: str          # « repeat » ou « errors »
    tool: str
    count: int
    detail: str = ""


@dataclass
class _Scope:
    calls: deque = field(default_factory=lambda: deque(maxlen=max(config.LOOP_WINDOW, config.LOOP_REPEAT)))
    last_error: str = ""
    error_run: int = 0
    alerted: set = field(default_factory=set)


def _fingerprint(tool: str, tool_input: Any) -> str:
    try:
        raw = json.dumps(tool_input, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        raw = str(tool_input)
    return hashlib.sha1(f"{tool}\0{raw}".encode()).hexdigest()[:16]


def _normalize_error(text: str) -> str:
    """Deux erreurs « identiques » à un numéro, une heure ou un id près."""
    text = re.sub(r"\b[0-9a-f]{8,}\b", "#", text.lower())
    text = re.sub(r"\d+", "0", text)
    return " ".join(text.split())[:200]


class LoopGuard:
    def __init__(self, repeat: int | None = None, errors: int | None = None) -> None:
        self.repeat = repeat or config.LOOP_REPEAT
        self.errors = errors or config.LOOP_ERRORS
        self._scopes: OrderedDict[str, _Scope] = OrderedDict()
        self._lock = threading.Lock()

    def _scope(self, scope: str) -> _Scope:
        s = self._scopes.get(scope)
        if s is None:
            s = self._scopes[scope] = _Scope()
            while len(self._scopes) > MAX_SCOPES:
                self._scopes.popitem(last=False)
        else:
            self._scopes.move_to_end(scope)
        return s

    def check_call(self, tool: str, tool_input: Any, scope: str = MAIN) -> Alert | None:
        if not tool:
            return None
        fp = _fingerprint(tool, tool_input)
        with self._lock:
            s = self._scope(scope or MAIN)
            s.calls.append(fp)
            count = sum(1 for c in s.calls if c == fp)
            key = f"repeat:{fp}"
            if count >= self.repeat and key not in s.alerted:
                s.alerted.add(key)
                return Alert(scope or MAIN, "repeat", tool, count)
        return None

    def check_result(self, tool: str, is_error: bool, text: str, scope: str = MAIN) -> Alert | None:
        with self._lock:
            s = self._scope(scope or MAIN)
            if not is_error:
                s.last_error, s.error_run = "", 0
                return None
            norm = _normalize_error(text or "")
            if norm and norm == s.last_error:
                s.error_run += 1
            else:
                s.last_error, s.error_run = norm, 1
            key = f"errors:{norm}"
            if s.error_run >= self.errors and key not in s.alerted:
                s.alerted.add(key)
                return Alert(scope or MAIN, "errors", tool, s.error_run, detail=(text or "")[:160])
        return None

    def reset(self, scope: str = MAIN) -> None:
        with self._lock:
            self._scopes.pop(scope, None)


def describe(alert: Alert, agent_desc: str = "") -> tuple[str, str]:
    """(texte du HUD, message discret au cerveau)."""
    who = f"l'agent « {agent_desc} »" if alert.scope != MAIN else "Astrel"
    if alert.kind == "repeat":
        hud = f"Boucle possible : {who} a lancé {alert.count} fois {alert.tool} avec les mêmes arguments."
        what = f"a relancé {alert.count} fois l'outil {alert.tool} avec exactement les mêmes arguments"
    else:
        hud = f"Boucle possible : {who} a reçu {alert.count} fois la même erreur ({alert.tool})."
        what = f"a reçu {alert.count} fois de suite la même erreur de l'outil {alert.tool} : {alert.detail}"
    if alert.scope == MAIN:
        advice = ("Change d'approche (autre outil, autre méthode, ou demande à l'utilisateur) au lieu de "
                  "recommencer. Pour attendre qu'un build ou un fichier change, crée plutôt un réflexe de "
                  "surveillance (reflex_create, déclencheur check) qui ne te réveille qu'au bon moment.")
    else:
        advice = ("Si l'agent n'avance plus, envoie-lui une consigne (SendMessage) ou arrête-le (TaskStop) "
                  "et change d'approche.")
    nudge = (f"[Alerte automatique d'Astrel, the user n'a rien dit] {who[0].upper() + who[1:]} {what}. "
             f"{advice} N'en parle pas à voix haute sauf si ça change ce que tu dois dire à l'utilisateur.")
    return hud, nudge
