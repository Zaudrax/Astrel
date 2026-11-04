"""Tours automatiques silencieux (15/10, HERMES D6, D7, A8 ; agent-coeur 8 et 50).

Un tour que l'utilisateur n'a pas demandé (réflexe `ask`, fin d'une tâche de fond) qui
n'a rien à dire répond `[SILENT]` : Astrel ne dit ni n'affiche rien. Le marqueur
est retiré partout où il apparaît (jamais lu « silent » à voix haute) ; pendant le
flux, un début de texte qui pourrait encore DEVENIR le marqueur (« [SIL ») est
retenu, pour ne jamais afficher puis retirer un morceau. Un tour de l'utilisateur n'est
pas filtré : il reçoit toujours une réponse visible.
"""
from __future__ import annotations

import re

from . import sources

MARKER = "[SILENT]"
# Tours concernés : `Astrel._turn_source`. « plan » (15/10, `plans.py`) : révision ou
# question sur un plan, la réponse va dans le fil du plan (`plan_reply`), pas dans le chat.
# « equipe » (20/10, plan P13) : relais de l'équipe d'agents envoyé par le cœur (`equipe.py`).
AUTO_SOURCES = sources.AUTO_SILENT  # « quota » : plan P34 ; 04/11 (T58) : `astrel/sources.py`
_MARKER_RE = re.compile(re.escape(MARKER), re.IGNORECASE)


def could_become_marker(text: str) -> bool:
    """Le texte (blancs de tête ignorés) est-il vide ou un début du marqueur ?"""
    return MARKER.startswith(text.lstrip().upper())


def take_marker(text: str) -> tuple[bool, str]:
    """Le texte commence-t-il (blancs de tête ignorés) par le marqueur ? Rend aussi la suite."""
    head = text.lstrip()
    if head.upper().startswith(MARKER):
        return True, head[len(MARKER):]
    return False, text


def drop_marker(text: str) -> str:
    """Le texte sans aucun marqueur (au milieu d'une phrase aussi)."""
    return _MARKER_RE.sub("", text)


def strip_marker(text: str) -> str:
    """Le texte sans aucun marqueur, blancs de bord retirés (vide = rien à dire)."""
    return drop_marker(text).strip()
