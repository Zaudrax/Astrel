"""Fournisseurs d'IA par CLI officiel (plan P27) : Codex, Grok Build. Voir `base.py`.

`fournisseurs.py` (catalogue, choix, refus) les nomme par leur id ; `brain.py` lance une
`SessionCli` à la place du process `claude` (`lancer`)."""
from __future__ import annotations

from pathlib import Path

from .base import (MODE_CONVERSATION, MODE_OUTILS, MODELE_DEFAUT, Capacites, Detection, FournisseurCli,
                   SessionCli)
from .codex import Codex
from .grok import Grok

CLIS: dict[str, FournisseurCli] = {c.id: c for c in (Codex(), Grok())}

__all__ = ["CLIS", "MODE_CONVERSATION", "MODE_OUTILS", "MODELE_DEFAUT", "Capacites", "Detection",
           "FournisseurCli", "SessionCli", "get", "lancer", "outils_possibles"]


def get(fid: str) -> FournisseurCli | None:
    return CLIS.get(fid)


def outils_possibles(fid: str) -> bool:
    """Outils permis par CONSTRUCTION : le refus du hook a été prouvé au lot 0 pour ce CLI."""
    c = CLIS.get(fid)
    return bool(c and c.capacites.prouve and c.capacites.hook_refus)


def lancer(fid: str, modele: str, outils: bool, session_id: str, consigne: str, base_env: dict,
           cwd: Path) -> SessionCli:
    """Session du cerveau pour le CLI `fid`. `outils` faux (ou hook non prouvé) : conversation
    seule (hook qui refuse tout, bac à sable en lecture, dossier vide, aucun MCP)."""
    c = CLIS[fid]
    mode = MODE_OUTILS if outils and outils_possibles(fid) else MODE_CONVERSATION
    return SessionCli(c, modele, mode, session_id=session_id, consigne=consigne, base_env=base_env, cwd=cwd)
