"""Sources d'un tour (04/11/2026, carte T58 pas 3) : la liste FERMÉE des chaînes `source` de `Astrel.handle`,
`_begin_turn` et `Held`, et les groupes que le cœur, la relève, le silence et la file d'attente comparent.

`StrEnum` : une `Source` est égale à sa chaîne et a le même hash ; les fichiers déjà écrits (relève, reprise,
messages en attente, mode léger) gardent des chaînes, rien à migrer. Aucune dépendance : importable partout
sans boucle d'import. Une faute de frappe (« équipe ») est attrapée par `tests/test_sources.py`, qui relit le
code du paquet."""
from __future__ import annotations

from enum import StrEnum


class Source(StrEnum):
    VOIX = "voix"
    CLAVIER = "clavier"
    WHATSAPP = "whatsapp"      # plan P1 : traité comme le clavier, jamais dit sur le PC
    FOND = "fond"              # tour relancé seul par le CLI (tâche de fond finie)
    REFLEXE = "réflexe"
    RELEVE = "relève"          # relève de session (`relief.py`)
    MISSION = "mission"        # mission autonome (`mission.py`)
    PLAN = "plan"              # plans (`plans.py`), jamais dit
    EQUIPE = "equipe"          # relais de l'équipe d'agents (plan P13), SANS accent
    QUOTA = "quota"            # arrêt et reprise sur quota (plan P34)
    ANNONCE = "annonce"        # file d'attente (`Held`) seulement
    RAPPORT = "rapport"        # file d'attente (`Held`) seulement


S = Source
# Tours de l'utilisateur lui-même (voix, clavier, WhatsApp).
UTILISATEUR = (S.VOIX, S.CLAVIER, S.WHATSAPP)
# Tours qu'il n'a pas demandés : jamais dits d'office (`core._UNASKED`).
UNASKED = (S.FOND, S.REFLEXE, S.RELEVE, S.MISSION, S.PLAN, S.EQUIPE, S.QUOTA)
# Mode léger (plan P8) : messages automatiques qui demandent des outils, gelés jusqu'au retour sur Claude.
LOCAL_FROZEN = (S.REFLEXE, S.RELEVE, S.MISSION, S.PLAN, S.EQUIPE, S.QUOTA)
# Tours automatiques où « [SILENT] » tait tout (`silence.py`).
AUTO_SILENT = (S.FOND, S.REFLEXE, S.PLAN, S.EQUIPE, S.QUOTA)
# Gardés pendant une relève (`relief.py`).
HELD_RELEVE = (*UTILISATEUR, S.REFLEXE, S.EQUIPE, S.QUOTA)
# Notés avant l'envoi (`attente.py`) : ce que l'utilisateur a dit ou tapé sur le PC.
ATTENTE = (S.VOIX, S.CLAVIER)
