"""Agents dédiés d'Astrel et routage AUTOMATIQUE du modèle des sous-agents.

1. Définitions : `config/agents/*.md` (en-tête `clé: valeur`, puis la consigne).
   `write_agents()` en fait le JSON de `claude --agents <fichier>` : ces agents
   n'existent que pour le process d'Astrel, pas pour les autres sessions de
   l'utilisateur. Seule leur description (une ligne) coûte en permanence.
   Idées reprises d'ECC (everything-claude-code, MIT, © Affaan Mustafa) :
   filtre anti-bruit et preuve exigée des relectures, trois questions de
   « gateguard » avant d'écrire, boucle de vérification. Textes réécrits.

2. Routage : `route(tool_input)` est appelé par le hook PreToolUse
   `astrel/route_hook.py` sur chaque appel de l'outil Agent. Il peut RÉÉCRIRE
   le modèle demandé (`updatedInput`, vérifié en réel le 14/10 sur le CLI
   2.1.285 : un agent demandé en sonnet est parti en haiku). Règles :
   - TOUT agent qui écrit du code part en opus (règle de l'utilisateur) : le
     `codeur` toujours, un agent généraliste (general-purpose…) dès que sa
     consigne parle d'écrire ou de modifier du code, sauf mention explicite de
     lecture seule ;
   - un agent Astrel en lecture seule ne descend jamais sous le modèle de sa
     définition (relecteur, auditeur : sonnet) ; on ne rétrograde jamais un
     choix plus fort (qualité d'abord) ;
   - arrêt d'urgence posé (`runtime/ESTOP`) : aucun nouvel agent (refus) ;
   - mémoire du PC en blocage (plan P20, `astrel/memoire.py`, blocage activé) : refus aussi.
   Ce hook ne fait QUE router : la sécurité reste au hook de confirmation.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import re
import time
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger("astrel.agents")

AGENTS_DIR = config.CONFIG_DIR / "agents"
AGENTS_FILE = config.RUNTIME / "agents.json"
# Lu aussi par le journal d'audit (`astrel/audit.py`, plan P3) ; ASTREL_ROUTE_LOG : tests
# qui lancent le vrai hook dans un sous-processus (sinon de faux agents s'y mêlent).
ROUTE_LOG = Path(os.environ.get("ASTREL_ROUTE_LOG") or config.LOGS / "route.jsonl")

RANK = {"haiku": 0, "sonnet": 1, "opus": 2}
_LIST_KEYS = {"tools", "disallowedTools", "skills"}
_BOOL_KEYS = {"omitClaudeMd", "background"}
_INT_KEYS = {"maxTurns"}

# Agents intégrés au CLI qui n'écrivent jamais (pas d'Edit/Write).
BUILTIN_READ_ONLY = {"Explore", "Plan", "statusline-setup", "claude-code-guide"}

# Consigne qui parle d'écrire ou de modifier du code (français et anglais).
# Mieux vaut un faux positif (opus pour rien) qu'un agent qui code sous un
# modèle plus faible : l'erreur ne va que dans le sens de la qualité.
_CODE_RE = re.compile(
    r"\b(modifie[rsz]?|corrige[rsz]?|implémente[rsz]?|implement(s|ing)?|refactor\w*|répare[rsz]?|"
    r"développe[rsz]?|code[rz]|coding|patche[rsz]?|fix(es|ing)?|édite[rz]?|edit(s|ing)?|"
    r"réécri\w*|rewrite|écri(re|s|vez) (le |du |un |une |les |des )?(code|test|script|fonction|module|classe|fichier)|"
    r"ajout\w* (une |un |des |le |la )?(fonction|méthode|test|option|champ|endpoint|route|classe|module))\b",
    re.IGNORECASE,
)
# … sauf si la consigne interdit explicitement d'écrire.
_READ_ONLY_RE = re.compile(
    r"lecture seule|read[- ]only|ne (modifie|change|touche|écris) (rien|aucun)|sans (rien )?(modifier|toucher)|"
    r"n'écris (rien|aucun)|do not (modify|edit|change|write)|don't (modify|edit|change|write)",
    re.IGNORECASE,
)


# --- définitions ---

def _parse(text: str) -> tuple[dict[str, Any], str]:
    """En-tête `---` simple (une clé par ligne, listes séparées par des virgules)."""
    meta: dict[str, Any] = {}
    body = text
    if text.startswith("---"):
        head, sep, rest = text[3:].partition("\n---")
        if sep:
            body = rest.lstrip("\n")
            for line in head.splitlines():
                key, colon, value = line.partition(":")
                key, value = key.strip(), value.strip()
                if not colon or not key:
                    continue
                if key in _LIST_KEYS:
                    meta[key] = [v.strip() for v in value.strip("[]").split(",") if v.strip()]
                elif key in _BOOL_KEYS:
                    meta[key] = value.lower() in ("true", "oui", "yes", "1")
                elif key in _INT_KEYS:
                    meta[key] = int(value)
                else:
                    meta[key] = value
    return meta, body.strip()


def load_agents(folder: Path | None = None, efforts: bool = True) -> dict[str, dict[str, Any]]:
    """Les agents de `config/agents/`, au format de `claude --agents`. `efforts` : l'effort choisi
    par l'utilisateur pour un métier (`runtime/agent-effort.json`, plan P28) remplace celui de l'en-tête."""
    out: dict[str, dict[str, Any]] = {}
    for path in sorted((folder or AGENTS_DIR).glob("*.md")):
        try:
            meta, body = _parse(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("agent %s illisible : %s", path.name, exc)
            continue
        name = str(meta.pop("name", "") or path.stem)
        if not meta.get("description") or not body:
            log.warning("agent %s ignoré : description ou consigne vide", path.name)
            continue
        body = body.replace(METIERS_MARK, METIERS_DIR.as_posix())
        for fiche in missing_fiches(body):
            log.warning("agent %s : fiche de métier citée absente : %s", path.name, fiche)
        out[name] = {**meta, "prompt": body}
    if efforts:
        for name, effort in chosen_efforts().items():
            if name in out:
                out[name]["effort"] = effort
    return out


# Plan P13 (équipe d'agents) : la consigne longue d'un agent est sa FICHE DE MÉTIER,
# `config/metiers/<nom>.md`, qu'il lit au lancement (première ligne de sa définition). Seule la
# définition courte part dans le JSON `--agents` (budget de tests/test_budget.py). `{metiers}`
# dans une définition = le chemin absolu du dossier (l'agent peut travailler dans un autre projet).
METIERS_MARK = "{metiers}"
METIERS_DIR = config.CONFIG_DIR / "metiers"
_FICHE_RE = re.compile(re.escape(METIERS_DIR.as_posix()) + r"/([\w.-]+\.md)")


def cited_fiches(prompt: str) -> list[Path]:
    """Les fiches de métier citées par une consigne (chemin absolu déjà substitué)."""
    return [METIERS_DIR / name for name in dict.fromkeys(_FICHE_RE.findall(prompt))]


def missing_fiches(prompt: str) -> list[str]:
    return [p.name for p in cited_fiches(prompt) if not p.is_file()]


# Plan P12, lot 4 : serveurs MCP propres à certains agents, déclarés ICI et pas dans leur
# .md (le JSON de load_agents est au plafond de tests/test_budget.py). Un serveur d'agent
# n'est connecté que quand l'agent tourne : rien dans le contexte d'Astrel.
CONTEXT7_AGENTS = ("codeur",)


def context7_on() -> bool:
    """ASTREL_CONTEXT7 faux, ou « off » dans runtime/context7.txt : coupé (page Réglages :
    non, `hud/src` protégé et chaque réglage y exige son icône)."""
    if not config.CONTEXT7:
        return False
    try:
        return config.CONTEXT7_SWITCH_FILE.read_text(encoding="utf-8").strip().lower() not in ("off", "non", "0")
    except OSError:
        return True


def _agent_servers(agents: dict[str, dict[str, Any]]) -> None:
    if not context7_on():
        return
    # Format du CLI 2.1.285 : liste de noms ou d'objets {nom: config} ; une définition
    # en ligne n'est acceptée que d'une origine de confiance, dont `--agents` (flagSettings).
    server = {"context7": {"type": "http", "url": config.CONTEXT7_URL}}
    for name in CONTEXT7_AGENTS:
        if name in agents:
            agents[name] = {**agents[name], "mcpServers": [*agents[name].get("mcpServers", []), server]}


def write_agents() -> Path | None:
    """Écrit `runtime/agents.json` ; None s'il n'y a aucun agent. Ce qui est écrit est retenu
    (`mark_loaded` le note comme pris par le CLI quand il l'a vraiment reçu)."""
    global _written
    agents = load_agents()
    if not agents:
        return None
    _agent_servers(agents)
    AGENTS_FILE.write_text(json.dumps(agents, ensure_ascii=False, indent=2), encoding="utf-8")
    _written = agents
    return AGENTS_FILE


# --- effort de réflexion par métier (23/10, plan P28, demande de l'utilisateur) ---
# Un agent lancé garde l'effort de sa définition. Le choix de l'utilisateur pour un métier
# (fiche d'agent du HUD, commande `agent_effort`) est retenu dans `runtime/agent-effort.json`
# ({métier: niveau}, seulement ce qui diffère de l'en-tête) et appliqué par `load_agents`. Le CLI
# en marche reçoit les définitions À CHAUD par un nouvel `initialize {agents}` (`Brain.reload_agents`,
# sonde du lot 0 sur le CLI 2.1.285 : définitions remplacées, prompt et modèle du cerveau intacts),
# sinon au prochain démarrage du cerveau. L'effort réellement pris par un agent : non observé.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
EFFORT_FILE = config.RUNTIME / "agent-effort.json"
_written: dict[str, dict[str, Any]] | None = None  # dernières définitions écrites
_loaded: dict[str, str] | None = None              # effort par métier reçu par le CLI en marche
_effort_lock = threading.Lock()                    # lecture-modification-écriture de EFFORT_FILE


def chosen_efforts() -> dict[str, str]:
    """{métier: niveau} choisis par l'utilisateur ; fichier absent ou illisible : rien, niveau
    inconnu ignoré (le CLI accepte un niveau inconnu SANS le prendre : sonde du lot 0)."""
    try:
        data = json.loads(EFFORT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v in EFFORT_LEVELS}


def set_agent_effort(name: str, effort: str | None) -> str:
    """Retient l'effort d'un métier (« » ou le niveau de l'en-tête : retour à l'en-tête). Rend
    le niveau en vigueur. Lève ValueError (métier inconnu, niveau manquant (None) ou inconnu)
    sans rien écrire."""
    name = str(name or "").strip()
    defs = load_agents(efforts=False)
    if name not in defs:
        raise ValueError(f"agent inconnu : {name or '(vide)'}")
    if effort is None:
        raise ValueError(f"niveau d'effort manquant pour l'agent {name} (au choix : {', '.join(EFFORT_LEVELS)}, "
                         "ou « defaut » pour revenir à son en-tête)")
    effort = str(effort).strip().lower()
    if effort and effort not in EFFORT_LEVELS:
        raise ValueError(f"niveau d'effort inconnu : {effort} (au choix : {', '.join(EFFORT_LEVELS)})")
    default = str(defs[name].get("effort") or "")
    with _effort_lock:  # deux commandes en parallèle (executor du serveur) : pas de choix perdu
        chosen = chosen_efforts()
        if not effort or effort == default:
            chosen.pop(name, None)
        else:
            chosen[name] = effort
        EFFORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = EFFORT_FILE.with_name(EFFORT_FILE.name + ".tmp")
        tmp.write_text(json.dumps(chosen, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, EFFORT_FILE)
    log.info("effort de l'agent %s : %s", name, chosen.get(name, default) or "réglage du CLI")
    return chosen.get(name, default)


def mark_loaded() -> None:
    """Le CLI vient de recevoir les définitions écrites par `write_agents` (démarrage avec
    `--agents`, ou `initialize` accepté)."""
    global _loaded
    if _written is not None:
        _loaded = {n: str(a.get("effort") or "") for n, a in _written.items()}


def forget_loaded() -> None:
    """Cerveau sans agents d'Astrel (mode léger, autre fournisseur) : rien n'est pris."""
    global _loaded
    _loaded = None


def effort_at_launch(name: str) -> str:
    """Effort avec lequel part un agent de ce métier MAINTENANT : celui que le CLI en marche a
    reçu ; à défaut (cerveau pas encore démarré), celui de la définition. « » : agent du CLI."""
    if _loaded is not None:
        return _loaded.get(name, "")
    return str(load_agents().get(name, {}).get("effort") or "")


def effort_info(name: str) -> dict[str, Any]:
    """Pour la fiche d'agent (`task_detail`) : `effort_next` (prochains agents du métier),
    `effort_default` (en-tête), `settable` (métier d'Astrel), `applique` (le CLI en marche a
    déjà reçu `effort_next`), `haiku` (modèle sans effort), `levels`."""
    defs = load_agents(efforts=False)
    meta = defs.get(name)
    if meta is None:
        return {"settable": False, "effort_next": "", "effort_default": "", "applique": False,
                "haiku": False, "levels": list(EFFORT_LEVELS)}
    default = str(meta.get("effort") or "")
    nxt = chosen_efforts().get(name, default)
    return {"settable": True, "effort_next": nxt, "effort_default": default,
            "applique": _loaded is not None and _loaded.get(name, "") == nxt,
            "haiku": _family(str(meta.get("model") or "")) == "haiku", "levels": list(EFFORT_LEVELS)}


def _defined_models() -> dict[str, str]:
    try:
        data = json.loads(AGENTS_FILE.read_text(encoding="utf-8"))
        return {k: str(v.get("model") or "") for k, v in data.items() if isinstance(v, dict)}
    except (OSError, ValueError, AttributeError):
        return {name: str(a.get("model") or "") for name, a in load_agents().items()}


def _family(model: str) -> str:
    """« claude-opus-5-5 » ou « opus » → « opus » ; inconnu → ''."""
    low = (model or "").lower()
    return next((f for f in RANK if f in low), "")


# --- routage ---

def writes_code(tool_input: dict[str, Any]) -> bool:
    text = f"{tool_input.get('description', '')}\n{tool_input.get('prompt', '')}"
    return bool(_CODE_RE.search(text)) and not _READ_ONLY_RE.search(text)


def route(tool_input: dict[str, Any], estop: bool = False,
          defined: dict[str, str] | None = None, pression: str = "",
          quota: str = "") -> tuple[str, dict[str, Any] | None, str]:
    """Décision pour un appel de l'outil Agent : (« deny » | « allow » | « », entrée
    réécrite ou None, raison). « » = rien à dire, le hook se tait.
    `pression` : raison de refus de `memoire.refus` (plan P20, mémoire du PC en blocage), « » sinon.
    `quota` : raison de refus de `quota_arret.refus` (plan P34, arrêt des agents sur quota), « » sinon."""
    if estop:
        return ("deny", None, "Arrêt d'urgence posé par l'utilisateur : aucun nouvel agent tant qu'il ne l'a pas "
                              "levé (HUD ou ./run.sh --send \"estop off\"). Fais le travail toi-même ou attends.")
    if pression:
        return "deny", None, pression
    if quota:
        return "deny", None, quota
    defined = _defined_models() if defined is None else defined
    kind = str(tool_input.get("subagent_type") or "general-purpose")
    asked = _family(str(tool_input.get("model") or ""))
    wanted = ""
    why = ""
    if kind in defined:
        base = _family(defined[kind])
        if base == "opus":
            wanted, why = "opus", f"l'agent {kind} écrit du code : toujours opus"
        elif asked and base and RANK[asked] < RANK[base]:
            wanted, why = base, f"l'agent {kind} ne descend pas sous {base}"
    elif kind not in BUILTIN_READ_ONLY and asked != "opus" and writes_code(tool_input):
        wanted, why = "opus", "la consigne demande d'écrire ou de modifier du code : opus"
    if not wanted or wanted == asked:
        return "", None, ""
    return "allow", {**tool_input, "model": wanted}, why


def estop_on() -> bool:
    return config.ESTOP_FILE.exists()


def log_route(tool_input: dict[str, Any], decision: str, updated: dict[str, Any] | None, why: str) -> None:
    """Une ligne par appel d'agent : de quoi mesurer le routage (`logs/route.jsonl`)."""
    try:
        with ROUTE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "type": tool_input.get("subagent_type") or "general-purpose",
                "desc": str(tool_input.get("description") or "")[:80],
                "asked": tool_input.get("model") or "",
                "final": (updated or tool_input).get("model") or "",
                "decision": decision or "inchangé", "why": why,
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass
