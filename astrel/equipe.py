"""Équipe d'agents : journal de passation, relais testeur puis relecteur, un seul écrivain
par fichier (20/10, plan P13 lot 1, demande de l'utilisateur : « Astrel fonctionne comme une
entreprise d'agents »).

Un MÉTIER = une fiche `config/metiers/<nom>.md` (lue par l'agent à son lancement) ; un AGENT
= une instance lancée par Astrel (`config/agents/<nom>.md`, définition courte). Les agents ne
s'appellent pas entre eux (`Agent` interdit) : Astrel reste le chef d'orchestre, le code
l'aide à enchaîner.

- Passation : outil MCP `passation` (`mcp_server.py`). Tout agent, même en LECTURE SEULE,
  ajoute une fiche au journal `runtime/equipe/<mission>/journal.md` (lu par les agents) et
  `fiches.jsonl` (fait foi : numérotation, lu par le cœur). Écrit par le serveur MCP
  lui-même, sous verrou de fichier : écrire là par Write serait hors du périmètre d'une
  mission (carte du garde-fou). Une fiche : 2 Ko au plus ; sans preuve, « non vérifié ».
- Relais : à la fin d'une tâche du codeur, le cœur (`core._on_task`) envoie à Astrel un
  message de source « equipe » (jamais dit) : lancer le testeur, puis le relecteur.
- Un seul écrivain : le cœur alerte (sans rien arrêter, comme `boucles.py`) quand deux
  agents vivants écrivent le même fichier.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

from . import config

try:  # plan P6 : fcntl n'existe pas sous Windows (verrou du process seulement)
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger("astrel.equipe")

EQUIPE_DIR = config.RUNTIME / "equipe"
METIERS_DIR = config.CONFIG_DIR / "metiers"
FICHE_MAX = 2048          # octets UTF-8 d'une fiche rendue (« 2 Ko au plus », plan P13)
FIELD_MAX = 1200          # caractères d'un champ, avant le contrôle de taille
NON_VERIFIE = "non vérifié"
DEFAULT_MISSION = "general"
# Décision de l'utilisateur (plan P13) : testeur puis relecteur après CHAQUE codeur. « 0 » coupe le relais.
APRES_CODEUR = os.environ.get("ASTREL_EQUIPE_APRES_CODEUR", "1").strip().lower() not in ("0", "non", "off", "false")

# Gabarit de passation (09/11, carte T87 : « passation plus carrée », idée reprise d'un dépôt tiers —
# chacun écrit SA fiche, ne recopie pas la précédente). Un contrat clair pour l'agent suivant :
# (clé, libellé affiché). `a` (sortie vers) est déjà l'en-tête de la fiche. `criteres` MESURABLES
# (comment on sait que c'est fini) et `preuve_requise` (ce qu'il faut montrer) sont le cœur du gabarit.
GABARIT: tuple[tuple[str, str], ...] = (
    ("demande", "Demande"),                   # ce qui est demandé à l'agent suivant
    ("fichiers", "Fichiers concernés"),        # les fichiers du lot (déjà porté par la fiche)
    ("criteres", "Critères d'acceptation"),    # MESURABLES : à quoi on voit que c'est fini
    ("preuve_requise", "Preuve requise"),      # ce qu'il faut montrer pour prouver « fini »
    ("a", "Sortie vers"),                      # à qui va la sortie (en-tête de la fiche)
)


def gabarit_champs() -> tuple[str, ...]:
    """Les champs du gabarit de passation, dans l'ordre (contrôle `tests/test_equipe.py`)."""
    return tuple(cle for cle, _ in GABARIT)


# Correction bornée (09/11, carte T87) : une correction corrige UNIQUEMENT les points listés, sans
# nouvelle fonctionnalité ; au-delà de CORRECTIONS_MAX allers-retours sur un même lot, le cœur arrête
# les relances automatiques et demande à l'utilisateur de trancher. Compteur dérivé du journal (l'état du
# lot fait foi : pas de compteur en mémoire à resynchroniser au redémarrage).
def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


CORRECTIONS_MAX = _env_int("ASTREL_CORRECTIONS_MAX", 3)

_lock = threading.Lock()


def slug(mission: Any) -> str:
    """Nom de dossier sûr pour une mission (« P13 », « site Boutique ») ; vide → « general »."""
    text = unicodedata.normalize("NFKD", str(mission or "")).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-")
    return text or DEFAULT_MISSION


def _refus(raison: str) -> dict[str, Any]:
    """Réponse d'échec de l'outil MCP `passation` : lue par Claude, jamais affichée."""
    return {"ok": False, "error": raison}


def _field(value: Any) -> str:
    return str(value or "").strip()[:FIELD_MAX]


def _titres(value: Any) -> list[str]:
    """Améliorations : liste (ou lignes) de titres courts, 8 au plus, 120 caractères chacun."""
    if isinstance(value, str):
        value = value.split("\n")
    if not isinstance(value, (list, tuple)):
        return []
    return [" ".join(str(v).split())[:120] for v in value if str(v).strip()][:8]


def _files(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [v for v in re.split(r"[\n,]", value)]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v).strip()[:300] for v in value if str(v).strip()][:30]


def render(fiche: dict[str, Any]) -> str:
    """Bloc Markdown d'une fiche, tel qu'il est ajouté au journal."""
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(fiche["at"]))
    lines = [f"## Fiche {fiche['n']} · {fiche['de']} → {fiche['a']} · {when}", "",
             f"- Fait : {fiche['fait']}"]
    for key, label in (("a_tester", "À tester"), ("risques", "Risques")):
        if fiche.get(key):
            lines.append(f"- {label} : {fiche[key]}")
    if fiche.get("fichiers"):
        lines.append(f"- Fichiers : {', '.join(fiche['fichiers'])}")
    # Gabarit T87 : le contrat pour l'agent suivant (seulement les champs renseignés).
    for key, label in (("demande", "Demande"), ("criteres", "Critères d'acceptation"),
                       ("preuve_requise", "Preuve requise")):
        if fiche.get(key):
            lines.append(f"- {label} : {fiche[key]}")
    if fiche.get("ameliorations"):
        lines.append(f"- Améliorations (cartes de la todo) : {' ; '.join(fiche['ameliorations'])}")
    lines.append(f"- Preuves : {fiche['preuves']}")
    return "\n".join(lines) + "\n"


def _paths(mission: str) -> tuple[Path, Path, Path]:
    folder = EQUIPE_DIR / mission
    return folder / "journal.md", folder / "fiches.jsonl", folder / ".verrou"


def read(mission: Any) -> list[dict[str, Any]]:
    """Les fiches d'une mission, dans l'ordre (lignes illisibles ignorées)."""
    _, data, _ = _paths(slug(mission))
    out: list[dict[str, Any]] = []
    try:
        lines = data.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and isinstance(item.get("n"), int):
            out.append(item)
    return out


def deposer(de: Any, a: Any, fait: Any, a_tester: Any = "", risques: Any = "", fichiers: Any = None,
            preuves: Any = "", mission: Any = "", ameliorations: Any = None, demande: Any = "",
            criteres: Any = "", preuve_requise: Any = "") -> dict[str, Any]:
    """Ajoute une fiche de passation. Rend {ok, mission, fiche, journal} ou {ok: False, error}.
    `ameliorations` (28/10, carte T31) : titres courts (remarques basses, idées, défauts non
    bloquants) dont le cœur fait des cartes de la todo (`todo.suivre_fiches`). Gabarit T87
    (09/11) : `demande` (ce qui est demandé au suivant), `criteres` (d'acceptation, MESURABLES),
    `preuve_requise` (ce qu'il faut montrer pour prouver « fini ») — tous facultatifs."""
    de, a, fait = _field(de)[:60], _field(a)[:60], _field(fait)
    if not de or not a or not fait:
        return _refus("de, a et fait sont obligatoires (qui passe, à qui, ce qui est fait)")
    m = slug(mission)
    journal, data, verrou = _paths(m)
    fiche: dict[str, Any] = {"de": de, "a": a, "fait": fait, "a_tester": _field(a_tester),
                             "risques": _field(risques), "fichiers": _files(fichiers),
                             "preuves": _field(preuves) or NON_VERIFIE, "at": time.time()}
    for key, value in (("demande", demande), ("criteres", criteres), ("preuve_requise", preuve_requise)):
        champ = _field(value)
        if champ:
            fiche[key] = champ
    idees = _titres(ameliorations)
    if idees:
        fiche["ameliorations"] = idees
    with _lock:
        journal.parent.mkdir(parents=True, exist_ok=True)
        with open(verrou, "a+", encoding="utf-8") as lock_fh:
            if fcntl is not None:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)  # serveurs MCP de plusieurs sessions
            fiche["n"] = max((f["n"] for f in read(m)), default=0) + 1
            block = render(fiche)
            size = len(block.encode("utf-8"))
            if size > FICHE_MAX:
                return _refus(f"fiche trop longue ({size} octets, {FICHE_MAX} au plus) : "
                              "raccourcis, renvoie aux fichiers plutôt que de les citer")
            head = "" if journal.exists() else f"# Journal d'équipe : {m}\n\n"
            with journal.open("a", encoding="utf-8") as fh:
                fh.write(head + block + "\n")
            with data.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(fiche, ensure_ascii=False) + "\n")
    log.info("passation %s n°%d : %s → %s", m, fiche["n"], de, a)
    return {"ok": True, "mission": m, "fiche": fiche["n"], "journal": str(journal)}


def latest_from(de: str, since: float = 0.0) -> tuple[str, dict[str, Any]] | None:
    """Dernière fiche déposée par `de` (métier, sans casse) depuis `since`, toutes missions."""
    want = de.strip().lower()
    best: tuple[str, dict[str, Any]] | None = None
    try:
        folders = [p for p in EQUIPE_DIR.iterdir() if p.is_dir()]
    except OSError:
        return None
    for folder in folders:
        for fiche in read(folder.name):
            at = fiche.get("at") if isinstance(fiche.get("at"), (int, float)) else 0.0
            if str(fiche.get("de") or "").strip().lower() != want or at < since:
                continue
            if best is None or at >= best[1].get("at", 0.0):
                best = (folder.name, fiche)
    return best


def after_codeur(entry: dict[str, Any], task: dict[str, Any], status: str) -> tuple[str, str] | None:
    """Fin d'une tâche : (message pour Astrel, ligne affichée) si c'était le codeur et qu'il a
    écrit ou passé la main ; None sinon (autre agent, échec, arrêt, relais coupé)."""
    if not APRES_CODEUR or str(entry.get("type") or "") != "codeur" or status not in ("", "completed"):
        return None
    files = [str(f) for f in entry.get("files") or []]
    found = latest_from("codeur", float(task.get("started") or 0.0) - 5)
    if not files and found is None:
        return None
    desc = str(task.get("desc") or "codeur")[:120]
    suite = ("Ne relance le codeur que pour un problème bloquant ou un défaut prouvé ; les remarques mineures vont "
             f"en cartes (`ameliorations`), sans nouveau tour. Au-delà de {CORRECTIONS_MAX} corrections sur le même "
             "lot, le cœur arrête les relances et demande à l'utilisateur de trancher.")
    if found is not None:
        mission, fiche = found
        journal, _, _ = _paths(mission)
        where = f"sur la fiche {fiche['n']} du journal {journal}"
        verif = verificateur_correction(mission, fiche, files)
        if verif:
            # 29/10 (économie de jetons, accord de l'utilisateur) : une CORRECTION est revérifiée par le métier
            # qui l'a demandée, sur ses propres points ; plus de seconde passe complète testeur + relecteur.
            # 09/11 (carte T87) : boucle bornée. Le rang de la correction est compté sur le journal ;
            # au-delà de CORRECTIONS_MAX, plus de relance, l'utilisateur tranche.
            rang = corrections_du_lot(mission, fiche, files)
            if rang > CORRECTIONS_MAX:
                return escalade_correction(mission, fiche, verif, rang)
            extra = hors_liste(mission, fiche, files)
            signal = ""
            if extra:
                listes = ", ".join(extra[:8]) + (" …" if len(extra) > 8 else "")
                signal = (f" ATTENTION : cette correction touche des fichiers hors du périmètre établi ({listes}) : "
                          "une correction corrige UNIQUEMENT les points listés, AUCUNE nouvelle fonctionnalité ; "
                          "fais-les vérifier et, si c'est un ajout non demandé, recadre le codeur.")
            shown = f"Équipe : {verif} seul sur la correction (fiche {fiche['n']}, {mission})."
            consigne = (f"[Équipe, message automatique du cœur] Le codeur a fini une CORRECTION (« {desc} », "
                        f"correction {rang} sur {CORRECTIONS_MAX} au plus). Lance seulement le {verif} {where} : il "
                        "vérifie que CHAQUE point listé de sa fiche précédente est corrigé et rien d'autre, relance "
                        f"les preuves du codeur, sans nouvelle passe complète, puis dépose sa passation (outil "
                        f"passation).{signal} {suite}")
            return consigne, shown
        shown = f"Équipe : testeur puis relecteur sur la fiche {fiche['n']} ({mission})."
    else:
        listed = ", ".join(files[:12]) + (" …" if len(files) > 12 else "")
        where = f"sur ses fichiers ({listed}) : il n'a pas déposé de passation, donne-leur sa demande"
        shown = "Équipe : testeur puis relecteur après le codeur."
    consigne = (f"[Équipe, message automatique du cœur] Le codeur a fini (« {desc} »). Lance le testeur {where}, "
                "puis, quand il a rendu, le relecteur sur la même fiche : un agent à la fois, chacun dépose sa "
                f"passation (outil passation). {suite}")
    return consigne, shown


VERIFICATEURS = ("testeur", "relecteur")


def verificateur_correction(mission: str, fiche: dict[str, Any], files: list[str]) -> str:
    """Métier (testeur ou relecteur) à relancer SEUL quand la fiche du codeur est une CORRECTION :
    en remontant le journal de la mission, une fiche de vérification sur des fichiers communs avant
    toute autre fiche du codeur (même lot) ; la dernière qui l'a demandée. Vide pour un premier
    passage, ou si le lot touche un fichier protégé par le garde-fou (cycle complet, toujours)."""
    mine = {str(f) for f in [*(fiche.get("fichiers") or []), *files]}
    if any(_protege(f) for f in mine):
        return ""
    for f in reversed([x for x in read(mission) if int(x.get("n") or 0) < int(fiche.get("n") or 0)]):
        de = str(f.get("de") or "").strip().lower()
        if de == "codeur":
            return ""  # deux fiches du codeur de suite : reprise d'un même passage, pas une correction
        if de in VERIFICATEURS and mine & {str(x) for x in f.get("fichiers") or []}:
            return de
    return ""


def _protege(path: str) -> bool:
    """Fichier du garde-fou ou code protégé (`guard`) : jamais de vérification allégée."""
    from . import guard
    try:
        return guard._protected(path) or guard._protected_code(path)
    except Exception:  # noqa: BLE001 — dans le doute, cycle complet
        return True


def _lot_fichiers(fiche: dict[str, Any], files: list[str]) -> set[str]:
    """Fichiers du lot courant : ceux de la fiche du codeur et ceux que l'agent a touchés."""
    return {str(f) for f in [*(fiche.get("fichiers") or []), *files]}


def corrections_du_lot(mission: str, fiche: dict[str, Any], files: list[str]) -> int:
    """Rang de la correction courante sur ce lot (09/11, carte T87). Compte les fiches du CODEUR dont
    les fichiers recoupent le lot, jusqu'à la fiche courante incluse, moins le premier passage. Le
    journal (`fiches.jsonl`) fait foi : 0 = premier passage (pas une correction), 1 = 1re correction…"""
    mine = _lot_fichiers(fiche, files)
    n = int(fiche.get("n") or 0)
    passes = sum(1 for f in read(mission)
                 if int(f.get("n") or 0) <= n and str(f.get("de") or "").strip().lower() == "codeur"
                 and mine & {str(x) for x in f.get("fichiers") or []})
    return max(passes - 1, 0)


def hors_liste(mission: str, fiche: dict[str, Any], files: list[str]) -> list[str]:
    """Fichiers de la correction jamais vus dans une fiche ANTÉRIEURE de la mission (09/11, carte T87) :
    une correction ne doit toucher que le périmètre déjà établi, pas ajouter une fonctionnalité. Liste
    triée des fichiers hors périmètre (vide si la correction reste dans le lot connu)."""
    n = int(fiche.get("n") or 0)
    connus: set[str] = set()
    for f in read(mission):
        if int(f.get("n") or 0) < n:
            connus |= {str(x) for x in f.get("fichiers") or []}
    return sorted(f for f in _lot_fichiers(fiche, files) if f not in connus)


def escalade_correction(mission: str, fiche: dict[str, Any], verif: str, rang: int) -> tuple[str, str]:
    """Au-delà de CORRECTIONS_MAX corrections sur un lot (09/11, carte T87) : plus de relance
    automatique, l'utilisateur tranche. (message pour Astrel, ligne affichée)."""
    shown = (f"Équipe : {CORRECTIONS_MAX} corrections sur le lot (fiche {fiche['n']}, {mission}), "
             "à l'utilisateur de trancher.")
    consigne = (f"[Équipe, message automatique du cœur] Le lot de la fiche {fiche['n']} ({mission}) en est à "
                f"{rang} corrections, au-delà du maximum de {CORRECTIONS_MAX} : la boucle de correction est bornée. "
                f"NE RELANCE AUCUN agent. Demande à l'utilisateur de trancher (post_report « decision ») en citant les "
                f"points de la dernière fiche du {verif} encore ouverts, et attends sa décision.")
    return consigne, shown


class Owners:
    """Un seul écrivain par fichier : alerte quand un agent écrit un fichier qu'un AUTRE agent
    encore vivant a déjà écrit. Une alerte par fichier et par paire d'agents."""

    def __init__(self) -> None:
        self._seen: set[tuple[str, frozenset[str]]] = set()

    def check(self, book: dict[str, dict], live: set[str], scope: str, path: str) -> list[str]:
        """Rend les portées (ids d'appel Agent) des autres écrivains vivants de `path` (vide sinon)."""
        others = [k for k, e in book.items()
                  if k != scope and k in live and path in (e.get("files") or [])]
        fresh = [k for k in others if (path, frozenset((k, scope))) not in self._seen]
        for k in fresh:
            self._seen.add((path, frozenset((k, scope))))
        if len(self._seen) > 512:
            self._seen.clear()
        return fresh


def owners_alert(path: str, kinds: list[str]) -> tuple[str, str]:
    """(ligne du HUD, message discret pour Astrel) d'un fichier écrit par plusieurs agents."""
    name = Path(path).name or path
    who = " et ".join(kinds)
    hud = (f"Deux agents écrivent le même fichier : {name} ({who}). Un seul écrivain par fichier ; "
           "rien n'est arrêté.")
    nudge = (f"[Alerte automatique d'Astrel] {who} ont écrit le même fichier {path} en même temps. Un seul "
             "écrivain par fichier : garde celui que la table des propriétaires du plan désigne, et "
             "arrête ou recadre l'autre.")
    return hud, nudge
