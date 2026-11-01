"""Page « Capacités » du HUD : tout ce qu'Astrel sait faire (21/10, plan P22, demande de
l'utilisateur : « une page du HUD listant tout ce qu'Astrel sait faire […], générée plutôt que
figée »).

Le catalogue est GÉNÉRÉ à chaque appel, en lisant les sources là où elles sont définies
(il ne vieillit pas comme une page écrite à la main) :
- outils MCP des serveurs `astrel` et `connecteurs` : lus par AST (nom, première phrase du
  docstring), SANS importer les serveurs (pas d'effet de bord, pas de D-Bus) ;
- gravité de chaque outil MCP : `guard.classify` (MCP_POLICY, nom de l'outil, et
  `hooks/tool-policy.json` en lecture) ;
- outils natifs `graph.NATIVE_TOOLS`, capteurs `graph.SENSORS` (+ état vivant passé par le
  cœur), nombre d'appels `runtime/graph-usage.json` ;
- agents `config/agents/*.md` (`agents.load_agents` : description, modèle, lecture seule ou
  écriture ; jamais la consigne, qui porte des chemins) ;
- réflexes : règles de `config/reflexes.json` (nom, déclencheur, action, activé ; jamais le
  texte ni la consigne) et types permis lus dans `reflex.create_rule` ;
- connecteurs : configuré oui/non (`config.secret`, présence seulement, JAMAIS la valeur) ;
- modes d'usage `config.USAGE_MODES` et leurs phrases `core.USAGE_NOTICES` (AST, sans importer
  le cœur, qui importera ce module) ;
- raccourcis : en-tête de `hotkeys.sh` ; gestes : tableau du docstring de `senses/holo.py` ;
- sujets du Guide et groupes de la page Réglages (`settings.GROUPS`, AST) : liens « Ouvrir » ;
- skills et connecteurs claude.ai (22/10, lot 2, question 3 du plan : « oui, en lecture ») : leurs
  NOMS et leur état pour Astrel, ceux de la page Réglages (passés par le cœur,
  `reglages.value("skills"|"connectors")`) ; sans le cœur, lus sur le disque : dossiers de skills
  (`SKILL.md` présent, comme `settings._discover_skills`), `runtime/connecteurs-vus.json` (dernière
  découverte de `claudeai.py`), choix de `runtime/settings.json`. Aucun appel réseau ni au CLI,
  jamais le contenu d'un SKILL.md (texte tiers, non traduisible, chemins possibles).
Seul reste écrit ici : les phrases vocales d'exemple (citées « … », jamais traduites, règle du
CLAUDE.md), les noms des modes et des types de réflexe, et le classement des outils MCP en
groupes. `tests/test_capacites.py` échoue si une source gagne un élément sans groupe ou sans
traduction anglaise, si une phrase d'exemple n'est plus reconnue, ou si un raccourci disparaît.

Langue : le texte est écrit en français et traduit par le catalogue `astrel/locales/en.json`
(`i18n.tr`) quand `lang == "en"`. `python -m astrel.capacites --manquants` liste ce qu'il y
manque (JSON {texte: ""}).

Format rendu (le HUD le met en forme, tout en `textContent`) :
    {"ok": true, "lang": "fr", "groupes": [{"id", "titre", "lien"?, "entrees": [
        {"nom", "texte"?, "etat"?, "gravite"?, "usage"?, "lien"?}]}]}
Champ absent = vide (réponse plus courte). `gravite` : "critique" | "sensible" | "libre" (code,
mis en mots par le HUD). `lien` : {"type": "guide" | "reglages", "id": …}.
`sommaire()` rend les groupes sans leurs entrées (avec `n`), `describe(groupe=…)` un seul groupe :
pour rester sous 4096 octets par réponse quand le tout dépasse.
"""
from __future__ import annotations

import ast
import fnmatch
import json
import os
import re
from pathlib import Path
from typing import Any

from . import config, i18n

TEXTE_MAX = 120
NOM_REGLE_MAX = 80  # nom d'une règle de réflexe (écrit à la main, longueur libre)
TYPE_REGLE_MAX = 40  # type inconnu d'une règle, montré tel quel
# Un groupe rendu (avec l'enveloppe {"ok", "lang", "groupes"}) reste sous 4096 octets.
GROUPE_OCTETS_MAX = 3900
REGLES_RESTE = "Autres réflexes, non listés ici : demande à Astrel de les lister."
# Ligne « +N » des autres groupes à longueur non bornée (skills, connecteurs claude.ai).
RESTES = {
    "skills": "Autres skills, non listées ici : voir Réglages, Skills.",
    "claudeai": "Autres connecteurs, non listés ici : voir Réglages, Connecteurs claude.ai.",
}

MCP_SOURCES = (
    ("astrel", config.ROOT / "astrel" / "mcp_server.py"),
    ("connecteurs", config.ROOT / "astrel" / "mcp_connectors.py"),
)
CORE_FILE = config.ROOT / "astrel" / "core.py"
SETTINGS_FILE = config.ROOT / "astrel" / "settings.py"
REFLEX_FILE = config.ROOT / "astrel" / "senses" / "reflex.py"
HOLO_FILE = config.ROOT / "astrel" / "senses" / "holo.py"
HOTKEYS_FILE = config.ROOT / "hotkeys.sh"
# Skills : mêmes dossiers que la page Réglages (`settings.USER_SKILLS`, `PROJECT_SKILLS`).
USER_SKILLS = Path.home() / ".claude" / "skills"
PROJECT_SKILLS = Path(".claude") / "skills"  # relatif à config.WORKDIR
SKILL_NOM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}")  # nom simple (« plugin:skill » permis)
SKILLS_MAX = 500
CLAUDEAI_PREFIX = "claude.ai "
CLAUDEAI_NOM_MAX = 100
# État pour Astrel (clé `skillOverrides` / `deniedMcpServers` des réglages du cerveau).
SKILLS_ETATS = {"on": "active", "name-only": "nom seul pour Astrel",
                "user-invocable-only": "à la demande seulement", "off": "coupée pour Astrel"}
CLAUDEAI_ETATS = {True: "actif pour Astrel", False: "bloqué pour Astrel"}

# ------------------------------------------------------------------ écrit à la main

# Groupes, dans l'ordre de la page : id → (titre, sujet du Guide relié ou "").
GROUPES: dict[str, tuple[str, str]] = {
    "voix": ("Ce que tu peux dire", "parler"),
    "modes": ("Modes d'usage", "parler"),
    "raccourcis": ("Raccourcis clavier", "parler"),
    "gestes": ("Gestes de la main (HOLO)", ""),
    "bureau": ("Bureau : voir et agir", ""),
    "memoire": ("Mémoire et historique", "memoire"),
    "rappels": ("Rappels", "rappels"),
    "hud": ("HUD, canaux et comptes rendus", "hud"),
    "partage": ("Images et WhatsApp", "whatsapp"),
    "connecteurs": ("Connecteurs", "connecteurs"),
    "claudeai": ("Connecteurs claude.ai", "connecteurs"),
    "capteurs": ("Capteurs", ""),
    "reflexes": ("Réflexes", "reflexes"),
    "reflexes_types": ("Réflexes : ce qu'on peut programmer", "reflexes"),
    "agents": ("Agents, plans et équipe", "agents"),
    "natifs": ("Outils de Claude Code", ""),
    "skills": ("Skills", ""),
    "guide": ("Sujets du Guide", ""),
    "reglages": ("Pages des Réglages", ""),
    "autres": ("Autres outils", ""),  # ne doit jamais apparaître (test) : outil MCP sans groupe
}

# Outil MCP → groupe (motif fnmatch, le premier qui va gagne). Un serveur entier par "serveur:*".
OUTILS_GROUPES: tuple[tuple[str, str], ...] = (
    ("connecteurs:*", "connecteurs"),
    ("memory_*", "memoire"), ("history_search", "memoire"), ("annuler_ecritures", "memoire"),  # 01/11
    ("reminder_*", "rappels"),
    ("reflex_*", "reflexes"),
    ("sensor_status", "capteurs"), ("set_sensor", "capteurs"),
    ("plan_*", "agents"), ("mission_start", "agents"), ("passation", "agents"), ("courrier_*", "agents"),  # plan P33
    ("channel_*", "hud"), ("post_report", "hud"), ("ask_choice", "hud"), ("set_voice_pause", "hud"),
    ("show_media", "hud"), ("todo_*", "hud"),  # plan P38 : todo par projet
    ("generate_image", "partage"), ("generate_video", "partage"), ("whatsapp_*", "partage"),
    ("screen_capture", "bureau"), ("list_screens", "bureau"), ("camera_capture", "bureau"), ("system_status", "bureau"),
    ("open_*", "bureau"), ("notify", "bureau"), ("set_volume", "bureau"), ("set_brightness", "bureau"),
    ("set_dark_mode", "bureau"), ("media_control", "bureau"), ("clipboard_*", "bureau"),
    ("lock_screen", "bureau"), ("wifi_toggle", "bureau"), ("power_action", "bureau"),
)

# Phrases vocales d'exemple : (phrase citée, jamais traduite ; effet ; vérification du test).
# Vérifications : "mode:<mode>" = core.mode_phrase, "total:on|off" = core.total_phrase,
# "oui30" = core._wants_30, "veille" = Astrel.is_stop, "reveil" = eveil.trouve (nom affiché, posé par
# `describe`, plan P36), "alias" = modèle « hey_jarvis » (ancien mot, réglage `wake_legacy`).
PHRASES: tuple[tuple[str, str, str], ...] = (
    ("« Astrel »", "Réveille Astrel : il t'écoute.", "reveil"),
    ("« Hey Jarvis »", "Ancien mot d'éveil, accepté tant que le réglage le garde.", "alias"),
    ("« Mode silencieux »", "Il t'écoute et répond à l'écran, sans parler.", "mode:silencieux"),
    ("« Passe en dictée »", "Il transcrit ce que tu dis et le copie, sans rien envoyer à Claude.", "mode:dictee"),
    ("« Fin de dictée »", "Retour au mode conversation.", "mode:conversation"),
    ("« Mode autonome total »", "Plus aucune question, sauf pour le garde-fou (à la voix seulement).", "total:on"),
    ("« Fin du mode total »", "Retour au mode autonome habituel.", "total:off"),
    ("« Oui pour 30 minutes »", "Autorise ce type d'action pendant 30 minutes.", "oui30"),
    ("« À plus tard »", "Met Astrel en veille.", "veille"),
)

MODES_NOMS = {"conversation": "Conversation", "silencieux": "Silencieux", "dictee": "Dictée"}

# Types de réflexe (lus dans `reflex.create_rule`) → (nom, explication).
DECLENCHEURS = {
    "time": ("À heure fixe", "Chaque jour choisi, à l'heure dite."),
    "at": ("Une fois, à un instant donné", "Même si Astrel était éteint à l'heure dite."),
    "battery_below": ("Batterie faible", "Quand la batterie passe sous un seuil."),
    "disk_below": ("Disque presque plein", "Quand l'espace libre passe sous un seuil."),
    "presence_for": ("Présence prolongée", "Quand tu es devant l'écran depuis un moment (EYES)."),
    "check": ("Surveillance par script", "Fichier, site ou commande surveillés sans modèle : rien n'est dépensé avant."),
}
ACTIONS = {
    "announce": ("Astrel le dit", "Une phrase dite, sans Claude."),
    "ask": ("Astrel demande à Claude", "Une question posée à Claude, réponse dite."),
    "notify": ("Notification seule", "Une notification GNOME, sans voix."),
    # 24/10 (plan P32) : initiative du cœur (brief du matin), sans modèle, avec carte d'accord.
    "initiative": ("Astrel propose", "Une initiative du cœur (brief du matin), sans modèle ; il attend ton accord."),
}

# Morceaux d'état qui ne se traduisent pas (noms de modèles).
NON_TRADUITS = {"opus", "sonnet", "haiku", "inherit"}

# Touches de hotkeys.sh, en anglais.
TOUCHES_EN = (("Maj", "Shift"), ("Échap", "Esc"))


# ------------------------------------------------------------------ lecture des sources

_ABREGE = re.compile(r"(?:\bex|\betc|\bcf|\bp|\bvs|\bnb)\.$", re.I)
PHRASE_MIN = 40  # plus courte (« Chef de projet. »), la phrase suivante est gardée aussi


def premiere_phrase(doc: str, limite: int = TEXTE_MAX) -> str:
    """Première phrase d'un docstring, sur une ligne, sans `code`, plafonnée à `limite`.
    Pas de coupure dans une parenthèse ni après une abréviation (« ex. »)."""
    t = re.sub(r"\s+", " ", (doc or "").replace("`", "")).strip()
    for m in re.finditer(r"(?<=[.!?…])\s+(?=[A-ZÀ-Ý«(])", t):
        head = t[:m.start()]
        if head.count("(") > head.count(")") or _ABREGE.search(head) or len(head) < PHRASE_MIN:
            continue
        t = head
        break
    if len(t) > limite:
        cut = t[:limite - 1]
        cut = cut[:cut.rfind(" ")] if " " in cut else cut
        t = cut.rstrip(" ,;:(«.") + "…"
    return t


def outils_mcp(path: Path) -> list[tuple[str, str]]:
    """[(outil, première phrase)] d'un serveur MCP, lus par AST (aucun import du serveur)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return []
    out = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "tool"
               for d in node.decorator_list):
            out.append((node.name, premiere_phrase(ast.get_docstring(node) or "")))
    return out


def groupe_outil(serveur: str, outil: str) -> str:
    for motif, groupe in OUTILS_GROUPES:
        if fnmatch.fnmatchcase(f"{serveur}:{outil}" if ":" in motif else outil, motif):
            return groupe
    return "autres"


def _literal(path: Path, nom: str) -> Any:
    """Valeur littérale d'une affectation de module `nom = …` (AST, sans importer)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else \
            [node.target] if isinstance(node, ast.AnnAssign) and node.value is not None else []
        if any(isinstance(t, ast.Name) and t.id == nom for t in targets):
            try:
                return ast.literal_eval(node.value)
            except ValueError:
                return None
    return None


def types_reflexe() -> tuple[list[str], list[str]]:
    """(déclencheurs, actions) permis, lus dans `reflex.create_rule` (AST)."""
    try:
        tree = ast.parse(REFLEX_FILE.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return [], []
    quand: list[str] = []
    faire: list[str] = []
    for fn in tree.body:
        if not (isinstance(fn, ast.FunctionDef) and fn.name == "create_rule"):
            continue
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.NotIn)
                    and isinstance(node.comparators[0], ast.Tuple)):
                continue
            values = [e.value for e in node.comparators[0].elts if isinstance(e, ast.Constant)]
            left = ast.unparse(node.left)
            if "'when'" in left or '"when"' in left:
                quand = [str(v) for v in values]
            elif "'do'" in left or '"do"' in left:
                faire = [str(v) for v in values]
    return quand, faire


def raccourcis() -> list[tuple[str, str]]:
    """[(touches, effet)] de l'en-tête de hotkeys.sh (« #   Ctrl+Alt+J   parler maintenant »)."""
    try:
        lines = HOTKEYS_FILE.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):  # ValueError : UTF-8 invalide (UnicodeDecodeError)
        return []
    out = []
    for line in lines[1:]:
        if not line.startswith("#"):
            break
        m = re.match(r"#\s{2,}(\S+)\s{2,}(\S.*)$", line)
        if m:
            out.append((m.group(1), m.group(2).strip()))
    return out


def raccourcis_poses() -> list[str]:
    """Touches réellement posées par hotkeys.sh (`set_key … "<Control><Alt>m"`), à la française."""
    try:
        src = HOTKEYS_FILE.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    out = []
    for binding in re.findall(r'^set_key\s.*"(<[^"]+)"\s*$', src, re.M):
        mods = re.findall(r"<(\w+)>", binding)
        key = re.sub(r"<\w+>", "", binding)
        names = {"Shift": "Maj", "Control": "Ctrl", "Primary": "Ctrl"}
        key = {"Escape": "Échap"}.get(key, key.upper() if len(key) == 1 else key)
        out.append("+".join([*(names.get(m, m) for m in mods), key]))
    return out


def gestes() -> list[tuple[str, str]]:
    """[(geste, effet)] du tableau du docstring de senses/holo.py (AST : mediapipe n'est pas importé)."""
    try:
        doc = ast.get_docstring(ast.parse(HOLO_FILE.read_text(encoding="utf-8"))) or ""
    except (OSError, SyntaxError, ValueError):
        return []
    out = []
    for line in doc.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.strip().startswith("|") else []
        if len(cells) == 2 and not set(cells[0]) <= set("-: ") and cells[0] != "Geste":
            out.append((re.sub(r"\s+", " ", cells[0]), cells[1]))
    return out


def _regles() -> list[dict]:
    """Règles des réflexes, LUES (jamais écrites : `reflex.load_rules` recrée le fichier s'il manque)."""
    from .senses import reflex
    try:
        rules = json.loads(Path(reflex.RULES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        rules = reflex.DEFAULT_RULES
    return [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else []


def _court(text: str, limite: int) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= limite else t[:limite - 1].rstrip() + "…"


def _type_regle(part: dict, noms: dict[str, tuple[str, str]]) -> str:
    """Nom du déclencheur ou de l'action d'une règle ; type inconnu montré court, autre chose « ? »."""
    kind = part.get("type")
    if isinstance(kind, str) and kind in noms:
        return noms[kind][0]
    return _court(kind, TYPE_REGLE_MAX) if isinstance(kind, str) and kind.strip() else "?"


def _gravite(serveur: str, outil: str) -> str:
    from . import guard
    try:
        v = guard.classify(f"mcp__{serveur}__{outil}", {})
    except Exception:  # un garde-fou illisible ne casse pas la page : gravité inconnue
        return ""
    return (v.level or "sensible") if v.needs_confirm else "libre"


def _usage_disque() -> dict[str, int]:
    from .graph import Graph
    return Graph._load_usage()


def _capteurs_disque() -> list[dict]:
    """Sans le cœur : seul l'état VOULU (runtime/sensors.json)."""
    from . import sensors
    try:
        saved = json.loads(Path(sensors.STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return [{"name": n, "on": bool(saved.get(n)), "status": ""} for n in sensors.SENSORS]


def _etat_capteur(item: dict) -> str:
    if not item.get("on"):
        return "éteint"
    status = str(item.get("status") or "")
    if status in ("", "actif"):
        return "actif" if status else "allumé"
    if status == "à venir":
        return "à venir"
    return "allumé, indisponible"  # jamais le détail (une exception peut citer un chemin)


def _choisis(cle: str) -> dict:
    """Choix bruts de la page Réglages pour `cle` (runtime/settings.json, lecture seule)."""
    try:
        data = json.loads(Path(config.ASTREL_SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    values = data.get("values") if isinstance(data, dict) else None
    choix = values.get(cle) if isinstance(values, dict) else None
    return choix if isinstance(choix, dict) else {}


def _skill_nom_sur(nom: Any) -> bool:
    return isinstance(nom, str) and bool(SKILL_NOM.fullmatch(nom)) and ".." not in nom


def _skills_disque() -> dict[str, str]:
    """Sans le cœur : {skill: état} comme la page Réglages (dossiers + défauts + choix), noms seuls."""
    noms: set[str] = set()
    for root in (USER_SKILLS, Path(config.WORKDIR) / PROJECT_SKILLS):
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for entry in entries[:SKILLS_MAX]:
            try:
                if _skill_nom_sur(entry.name) and entry.is_dir() and (Path(entry.path) / "SKILL.md").is_file():
                    noms.add(entry.name)
            except OSError:
                continue
    etats = {n: "on" for n in noms}
    etats.update(dict(config.CLAUDE_SKILL_OVERRIDES))
    if os.environ.get("ASTREL_SKILL_OVERRIDES") is None:  # sinon imposé par l'environnement
        etats.update({k: v for k, v in _choisis("skills").items() if k in etats and v in SKILLS_ETATS})
    return etats


def _claudeai_disque() -> dict[str, bool]:
    """Sans le cœur : {connecteur: actif pour Astrel} (dernière découverte + bloqués + choix)."""
    from . import claudeai
    try:
        data = json.loads(Path(claudeai.SEEN_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        data = {}
    vus = data.get("names") if isinstance(data, dict) else None
    noms = [n for n in vus if isinstance(n, str)] if isinstance(vus, list) else []
    refuses = tuple(config.CLAUDE_DENIED_MCP_SERVERS)
    etats = {n: n not in refuses for n in dict.fromkeys([*noms, *refuses])}
    if os.environ.get("ASTREL_DENIED_MCP_SERVERS") is None:
        etats.update({k: v for k, v in _choisis("connectors").items() if k in etats and isinstance(v, bool)})
    return etats


def _usage_claudeai(nom: str, usage: dict[str, int]) -> int:
    """Appels des outils d'un connecteur : serveur `claude_ai_<Nom>` du CLI (« mcp:claude_ai_Gmail:… »)."""
    prefixe = "mcp:" + re.sub(r"[^A-Za-z0-9_-]", "_", nom) + ":"
    return sum(int(v) for k, v in usage.items() if k.startswith(prefixe) and isinstance(v, int))


# ------------------------------------------------------------------ construction

def _e(nom: str, texte: str = "", etat: str = "", gravite: str = "", usage: int = 0,
       lien: dict | None = None, traduire_nom: bool = False) -> dict:
    e: dict[str, Any] = {"nom": nom}
    if texte:
        e["texte"] = texte
    if etat:
        e["etat"] = etat
    if gravite:
        e["gravite"] = gravite
    if usage:
        e["usage"] = int(usage)
    if lien:
        e["lien"] = lien
    if traduire_nom:
        e["_tn"] = True
    return e


def _construire(capteurs: list[dict] | None, usage: dict[str, int] | None, lang: str,
                skills: dict[str, str] | None = None, claudeai: dict[str, bool] | None = None,
                ) -> dict[str, list[dict]]:
    """Entrées en FRANÇAIS, par groupe (ordre de GROUPES)."""
    from . import agents as agents_mod
    from .graph import NATIVE_TOOLS, SENSORS
    from .guide import Guide
    from .tools import connector

    usage = _usage_disque() if usage is None else usage
    g: dict[str, list[dict]] = {k: [] for k in GROUPES}

    # 25/10 (plan P36) : le mot d'éveil est le nom affiché, quel qu'il soit.
    g["voix"] = [_e(f"« {i18n.assistant_name()} »" if v == "reveil" else phrase, effet) for phrase, effet, v in PHRASES]

    notices = _literal(CORE_FILE, "USAGE_NOTICES") or {}
    for mode in config.USAGE_MODES:
        g["modes"].append(_e(MODES_NOMS.get(mode, mode), notices.get(mode, ""), traduire_nom=True))

    g["raccourcis"] = [_e(touches, effet[:1].upper() + effet[1:]) for touches, effet in raccourcis()]
    g["gestes"] = [_e(geste, effet[:1].upper() + effet[1:], traduire_nom=True) for geste, effet in gestes()]

    for serveur, path in MCP_SOURCES:
        for outil, phrase in outils_mcp(path):
            g[groupe_outil(serveur, outil)].append(
                _e(outil, phrase, gravite=_gravite(serveur, outil), usage=usage.get(f"mcp:{serveur}:{outil}", 0)))

    etats = connector.status()
    connecteurs = [_e(nom, etat="configuré" if etats.get(nom, {}).get("actif") else "non configuré",
                      traduire_nom=True) for nom in connector.CONNECTORS]
    g["connecteurs"] = connecteurs + g["connecteurs"]

    # Connecteurs claude.ai : nom et état pour Astrel seulement (jamais d'URL ni de statut du CLI).
    for nom, actif in (claudeai if claudeai is not None else _claudeai_disque()).items():
        if not (isinstance(nom, str) and nom.startswith(CLAUDEAI_PREFIX)):
            continue
        court = _court(nom.removeprefix(CLAUDEAI_PREFIX), CLAUDEAI_NOM_MAX)
        if court and "/" not in court:
            g["claudeai"].append(_e(court, etat=CLAUDEAI_ETATS[bool(actif)], usage=_usage_claudeai(nom, usage)))
    g["claudeai"].sort(key=lambda e: e["nom"].casefold())

    vivants = {str(c.get("name", "")).upper(): c for c in (capteurs if capteurs is not None else _capteurs_disque())}
    capt = [_e(nom, desc, etat=_etat_capteur(vivants.get(nom, {}))) for nom, desc in SENSORS.items()]
    g["capteurs"] = capt + g["capteurs"]

    regles = []
    for r in _regles():
        when, do = r.get("when"), r.get("do")
        if not isinstance(when, dict) or not isinstance(do, dict):
            continue  # règle malformée (écrite à la main) : ignorée, la page reste
        nom = r.get("name") if isinstance(r.get("name"), str) and r.get("name").strip() else r.get("id")
        if not isinstance(nom, str) or not nom.strip():
            continue
        e = _e(_court(nom, NOM_REGLE_MAX), f"{_type_regle(when, DECLENCHEURS)} · {_type_regle(do, ACTIONS)}",
               etat="activé" if r.get("enabled") else "désactivé")
        e["_regle"] = True
        regles.append(e)
    g["reflexes"] = regles + g["reflexes"]
    quand, faire = types_reflexe()
    g["reflexes_types"] = [
        *(_e(*DECLENCHEURS.get(t, (t, "")), traduire_nom=True) for t in quand),
        *(_e(*ACTIONS.get(t, (t, "")), traduire_nom=True) for t in faire),
    ]

    equipe = []
    for nom, meta in agents_mod.load_agents().items():
        tools = meta.get("tools") or []
        interdits = set(meta.get("disallowedTools") or [])
        ecrit = ({"Write", "Edit"} & set(tools)) if tools else not ({"Write", "Edit"} <= interdits)
        modele = str(meta.get("model") or "")
        role = "écriture" if ecrit else "lecture seule"
        equipe.append(_e(nom, premiere_phrase(str(meta.get("description") or "")),
                         etat=f"{modele} · {role}" if modele else role))
    g["agents"] = equipe + g["agents"]

    g["natifs"] = [_e(nom, desc, usage=usage.get(f"tool:{nom}", 0)) for nom, desc in NATIVE_TOOLS.items()]

    # Skills : noms (identifiants, jamais traduits) et état pour Astrel ; pas le texte du SKILL.md.
    sk = skills if skills is not None else _skills_disque()
    g["skills"] = [_e(nom, etat=SKILLS_ETATS.get(etat, SKILLS_ETATS["on"]))
                   for nom, etat in sorted(sk.items(), key=lambda kv: str(kv[0]).casefold())
                   if _skill_nom_sur(nom)][:SKILLS_MAX]
    for e in g["skills"] + g["claudeai"]:
        e["_regle"] = True  # nombre non borné : coupé au-delà de GROUPE_OCTETS_MAX, ligne « +N »

    # Titres du Guide : déjà dans la langue demandée (docs/GUIDE-DEMARRAGE.en.md), pas de `tr`.
    g["guide"] = [_e(t["titre"], lien={"type": "guide", "id": t["id"]})
                  for t in Guide().source(lang).get("topics", [])]
    g["reglages"] = [_e(label, lien={"type": "reglages", "id": gid}, traduire_nom=True)
                     for gid, label in _literal(SETTINGS_FILE, "GROUPS") or ()]
    return g


def _tr(text: str, lang: str) -> str:
    # 23/10 (plan P31) : en français aussi, pour le nom affiché (« Réveille Astrel ») dans les textes
    # connus du catalogue ; une règle écrite par l'utilisateur ou Claude (inconnue) reste telle quelle.
    return i18n.tr(text, lang) if text else text


def _etat_tr(etat: str, lang: str) -> str:
    return " · ".join(p if p in NON_TRADUITS else _tr(p, lang) for p in etat.split(" · "))


def _rendre(gid: str, entrees: list[dict], lang: str) -> dict:
    titre, sujet = GROUPES[gid]
    out: dict[str, Any] = {"id": gid, "titre": _tr(titre, lang)}
    if sujet:
        out["lien"] = {"type": "guide", "id": sujet}
    rendues = []
    for e in entrees:
        e = dict(e)
        tn = e.pop("_tn", False)
        if lang == "en":
            if tn:
                e["nom"] = _tr(e["nom"], lang)
            elif gid == "raccourcis":
                for fr, en in TOUCHES_EN:
                    e["nom"] = e["nom"].replace(fr, en)
            if "texte" in e:
                e["texte"] = _tr(e["texte"], lang)
            if "etat" in e:
                e["etat"] = _etat_tr(e["etat"], lang)
        else:  # 23/10 (plan P31) : nom affiché, mêmes champs (jamais l'ancien mot d'éveil ni un identifiant)
            if tn:
                e["nom"] = _tr(e["nom"], lang)
            if "texte" in e:
                e["texte"] = _tr(e["texte"], lang)
        e.pop("_regle", None)
        rendues.append(e)
    out["entrees"] = rendues
    # Règles écrites par l'utilisateur ou Claude : leur nombre n'est pas borné. Les dernières sont
    # retirées tant que le groupe dépasse GROUPE_OCTETS_MAX, une ligne « +N » le dit.
    regles = [i for i, e in enumerate(entrees) if e.get("_regle")]
    if regles and _octets(out) > GROUPE_OCTETS_MAX:
        reste = {"nom": "", "texte": _tr(RESTES.get(gid, REGLES_RESTE), lang)}
        out["entrees"] = rendues[:regles[-1] + 1] + [reste] + rendues[regles[-1] + 1:]
        cachees = 0
        while cachees < len(regles) and _octets(out) > GROUPE_OCTETS_MAX:
            del out["entrees"][len(regles) - 1 - cachees]
            cachees += 1
            reste["nom"] = f"+{cachees}"
    return out


def _octets(groupe: dict) -> int:
    return len(json.dumps(groupe, ensure_ascii=False).encode())


def _lang(lang: str | None) -> str:
    return lang if lang in i18n.LANGS else i18n.lang()


def describe(lang: str | None = None, groupe: str | None = None, capteurs: list[dict] | None = None,
             usage: dict[str, int] | None = None, skills: dict[str, str] | None = None,
             claudeai: dict[str, bool] | None = None) -> dict:
    """Le catalogue (ou le seul `groupe`), dans la langue `lang` (sinon celle de l'interface).

    `capteurs` : `sensors.snapshot()` du cœur (état vivant) ; absent = état voulu sur disque.
    `usage` : compteurs du graphe (`graph._usage`) ; absent = runtime/graph-usage.json.
    `skills`, `claudeai` : `reglages.value("skills")` / `("connectors")` du cœur ; absents = disque."""
    lang = _lang(lang)
    if groupe is not None and groupe not in GROUPES:
        return {"ok": False, "error": _tr("Groupe inconnu.", lang)}
    g = _construire(capteurs, usage, lang, skills, claudeai)
    ids = [groupe] if groupe is not None else [k for k in GROUPES if g[k]]
    return {"ok": True, "lang": lang, "groupes": [_rendre(k, g[k], lang) for k in ids]}


def sommaire(lang: str | None = None, capteurs: list[dict] | None = None,
             usage: dict[str, int] | None = None, skills: dict[str, str] | None = None,
             claudeai: dict[str, bool] | None = None) -> dict:
    """Les groupes sans leurs entrées (`n` = nombre d'entrées) : le HUD demande ensuite chacun."""
    full = describe(lang, capteurs=capteurs, usage=usage, skills=skills, claudeai=claudeai)
    return {"ok": True, "lang": full["lang"], "groupes": [
        {**{k: v for k, v in grp.items() if k != "entrees"}, "n": len(grp["entrees"])} for grp in full["groupes"]]}


def textes_a_traduire() -> list[str]:
    """Textes français de la page qui doivent avoir leur entrée dans astrel/locales/en.json."""
    g = _construire(None, {}, "fr")
    out: list[str] = []

    def add(s: str) -> None:
        if s and s not in out:
            out.append(s)

    add("Groupe inconnu.")
    add(REGLES_RESTE)
    for s in RESTES.values():
        add(s)
    for gid in ("skills", "claudeai"):  # titres même si la machine n'en a aucun
        add(GROUPES[gid][0])
    for s in (*SKILLS_ETATS.values(), *CLAUDEAI_ETATS.values()):
        add(s)
    for gid, entrees in g.items():
        if entrees:
            add(GROUPES[gid][0])
        for e in entrees:
            if e.get("_tn"):
                add(e["nom"])
            add(e.get("texte", ""))
            for p in e.get("etat", "").split(" · "):
                if p not in NON_TRADUITS:
                    add(p)
    # États possibles qui ne sont pas forcément affichés sur cette machine.
    for s in ("éteint", "actif", "allumé", "à venir", "allumé, indisponible", "configuré", "non configuré",
              "activé", "désactivé", "écriture", "lecture seule"):
        add(s)
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Catalogue « Capacités » (plan P22).")
    ap.add_argument("--lang", default="fr")
    ap.add_argument("--manquants", action="store_true", help="textes sans traduction anglaise, en JSON")
    ap.add_argument("--taille", action="store_true", help="octets par groupe et au total")
    args = ap.parse_args(argv)
    if args.manquants:
        missing = {t: "" for t in textes_a_traduire() if not i18n.covered(t)}
        print(json.dumps(missing, ensure_ascii=False, indent=1))
        return 1 if missing else 0
    data = describe(args.lang)
    if args.taille:
        for grp in data["groupes"]:
            n = len(json.dumps(grp, ensure_ascii=False).encode())
            print(f"{grp['id']:16} {len(grp['entrees']):4} entrées {n:6} octets")
        total = len(json.dumps(data, ensure_ascii=False).encode())
        print(f"{'total':16} {sum(len(x['entrees']) for x in data['groupes']):4} entrées {total:6} octets")
        return 0
    print(json.dumps(data, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
