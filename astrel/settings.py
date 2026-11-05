"""Réglages d'Astrel modifiables depuis le HUD (page « Réglages », 15/10).

Contrat : `docs/CONTRAT-REGLAGES-2026-10-15.md` (évènement `settings`, commandes
`settings_get` / `settings_set` / `settings_apply`). Le HUD est générique : il
dessine ce que décrit `describe()`.

- Valeur par DÉFAUT : lue à chaque appel dans `config` (donc ce que donnent les
  variables d'environnement ou le code), jamais figée à l'import.
- Choix de l'utilisateur : `runtime/settings.json` (`config.ASTREL_SETTINGS_FILE`),
  écriture atomique, seulement les valeurs qui diffèrent du défaut. Protégé par
  le garde-fou : Claude ne peut pas l'écrire, et le cœur refuse ces commandes
  quand elles viennent de lui.
- `locked` : une variable `ASTREL_*` impose la valeur (affichée grisée, refusée
  en écriture).
- `applies` : « hot » (lu en direct par le cœur), « brain » (au prochain
  démarrage du cerveau : `Brain.start`), « mcp » (après réécriture de
  `runtime/mcp.json` ET redémarrage du cerveau). `pending` : la valeur choisie
  diffère de celle prise au dernier démarrage concerné.

Ni l'autonomie, ni le mode Total, ni la mission, ni l'arrêt d'urgence ne sont
exposés ici (décision de l'utilisateur : ils ont leurs propres commandes protégées).

17/10 (plan P4) : genres `info` / `action` (valeur CALCULÉE par `view`, boutons
exécutés par `run`, commande `settings_action`), jamais écrits dans le fichier. Une
vue qui dépend du cœur (micro, voix) est branchée par `reglages.bind`. Un bouton
`word` exige le mot tapé (vérifié ici, pas seulement par le HUD).
Réglage `live` : valeur lue (`get`) et appliquée (`put`) ailleurs que dans le fichier
(micro, voix et seuil de réveil par le cœur, sortie du son par pactl) ; liste de choix
calculée (`options`).
"""
from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import config, i18n, icones, limites

log = logging.getLogger("astrel.settings")

GROUPS = (
    ("compte", "Compte Claude"),
    ("fournisseur", "Fournisseur du cerveau"),  # 20/10 (plan P11) : clé API Claude, OpenAI, Gemini…
    ("demarrage", "Premier lancement et diagnostic"),
    ("voix", "Voix, audio et langue"),
    ("capteurs", "Capteurs"),  # 23/10 (demande de l'utilisateur) : le panneau du HUD ne garde que WATCH
    ("initiatives", "Initiatives"),  # 24/10 (plan P32) : brief du matin, propositions d'Astrel
    ("apparence", "Apparence"),  # 21/10 (plan P17) : clair ou sombre, accent (Premium seul depuis P35)
    ("confidentialite", "Confidentialité et données"),
    ("maj", "Mises à jour"),
    ("systeme", "Système"),
    ("whatsapp", "WhatsApp"),
    ("navigateurs", "Navigateurs"),
    ("images", "Images IA"),  # 28/10 (plan P40 lot A4) : mode auto ou manuel, ordre des moteurs
    ("connecteurs", "Connecteurs claude.ai"),
    ("skills", "Skills"),
    ("plugins", "Plugins"),
    ("seuils", "Seuils d'alerte"),
    ("limite", "Limite de l'abonnement"),  # 19/10 (plan P8) : offre de modèle, reprise automatique
    ("cerveau", "Cerveau"),
    ("modele_local", "Modèle local"),  # 19/10 (plan P8) : Ollama, mode léger
    ("garde_fou", "Garde-fou"),  # 18/10 (plan P7) : levée du garde-fou
)

# Connecteurs claude.ai du compte : DÉCOUVERTS par `claude mcp list` (19/10, plan P10,
# `astrel/claudeai.py`), plus de liste écrite ici.
# Skills dont l'état est réglable par `skillOverrides` (sans effet sur les skills
# de plugins, « anthropic-skills:… »). 19/10 (demande de l'utilisateur) : la liste se
# DÉCOUVRE à chaque affichage (`_discover_skills`, dossiers `USER_SKILLS` et
# `config.WORKDIR / PROJECT_SKILLS`, lecture seule) ; ce tableau n'est plus qu'un
# REPLI pour les skills du CLI sans dossier (built-in-browser, simplify, loop…),
# celles de docs/ECONOMIE-TOKENS.md § 9.
KNOWN_SKILLS = (
    *(f"founder-{n}" for n in ("board", "brand", "cfo", "competitors", "consumer", "launch", "marketing",
                               "offer", "ops", "plan", "pricing")),
    "dataviz", "update-config", "schedule", "loop", "workflow-authoring", "skill-creator",
    "claude-api", "code-review", "run", "simplify", "security-review", "init",
    "built-in-browser", "chrome-browser", "computer-use", "docs", "morning", "import-memory",
    "keybindings-help", "fewer-permission-prompts",
)
TRI_STATES = ("on", "name-only", "off")
# Borne de RESSOURCES (un Chrome par navigateur de test), pas une liste à découvrir (plan P10).
MAX_TEST_BROWSERS = 3
USER_SKILLS = Path.home() / ".claude" / "skills"
PROJECT_SKILLS = Path(".claude") / "skills"  # relatif au dossier de travail du cerveau (config.WORKDIR)
# Nom de skill accepté : un nom de dossier simple (ni chemin, ni « .. », ni dossier caché).
SKILL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
MAX_SKILLS = 500  # garde-fou contre un dossier démesuré
INSTALLED_PLUGINS = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
EFFORTS = (("", "Réglage du CLI"), ("low", "Faible"), ("medium", "Moyen"), ("high", "Élevé"),
           ("xhigh", "Très élevé"), ("max", "Maximal"))
TRUE_WORDS = ("1", "true", "yes", "oui", "on", "vrai")
FALSE_WORDS = ("0", "false", "no", "non", "off", "faux")


class SettingError(ValueError):
    """Valeur refusée (type, bornes, réglage imposé) : `key` et un message en français."""

    def __init__(self, key: str, message: str) -> None:
        super().__init__(message)
        self.key = key
        self.message = message


# --------------------------------------------------------------------------- valeurs par défaut

def _installed_plugins() -> list[str]:
    try:
        data = json.loads(INSTALLED_PLUGINS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    plugins = data.get("plugins") if isinstance(data, dict) else None
    return [str(p) for p in plugins] if isinstance(plugins, dict) else []


def _chosen_names(key: str) -> list[str]:
    """Noms déjà choisis pour un réglage « flags » dans runtime/settings.json (lecture brute)."""
    try:
        data = json.loads(config.ASTREL_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    values = data.get("values") if isinstance(data, dict) else None
    chosen = values.get(key) if isinstance(values, dict) else None
    return [str(n) for n in chosen] if isinstance(chosen, dict) else []


def _connectors_default() -> dict[str, bool]:
    """Découverts (ou derniers vus, `claudeai`) ∪ bloqués par défaut ; un nouveau venu est
    actif, comme dans le CLI. Tant qu'aucune découverte n'a jamais abouti (premier démarrage
    après la mise à jour), les noms déjà choisis par l'utilisateur sont gardés : sinon `_load` les
    oublierait et le cerveau réactiverait un connecteur qu'il avait bloqué."""
    from .claudeai import connecteurs

    denied = tuple(config.CLAUDE_DENIED_MCP_SERVERS)
    found = connecteurs.names() or _chosen_names("connectors")
    names = list(dict.fromkeys([*found, *denied]))
    return {n: n not in denied for n in names}


def _safe_skill_name(name: str) -> bool:
    return bool(SKILL_NAME.fullmatch(name)) and ".." not in name


def _discover_skills() -> list[str]:
    """Skills installées : sous-dossiers de `USER_SKILLS` et du `.claude/skills` du
    dossier de travail qui portent un SKILL.md. Lecture seule (aucun fichier ouvert,
    rien d'écrit), relue à chaque appel : une skill ajoutée ou retirée se voit au
    prochain affichage des Réglages, sans relance. Dossier absent ou illisible : rien."""
    found: list[str] = []
    for root in (USER_SKILLS, Path(config.WORKDIR) / PROJECT_SKILLS):
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for entry in entries:
            if len(found) >= MAX_SKILLS:
                return found
            name = entry.name
            if not _safe_skill_name(name) or name in found:
                continue
            try:
                if entry.is_dir() and (Path(entry.path) / "SKILL.md").is_file():
                    found.append(name)
            except OSError:
                continue
    return found


def _skills_default() -> dict[str, str]:
    """{nom: état} : skills découvertes + repli `KNOWN_SKILLS` + noms des réglages par
    défaut (`config.CLAUDE_SKILL_OVERRIDES`), triés ; « on » sauf état par défaut."""
    names = sorted({*_discover_skills(), *KNOWN_SKILLS, *config.CLAUDE_SKILL_OVERRIDES})
    out = {n: "on" for n in names}
    out.update(dict(config.CLAUDE_SKILL_OVERRIDES))
    return out


def _plugins_default() -> dict[str, bool]:
    disabled = tuple(config.CLAUDE_DISABLED_PLUGINS)
    names = list(dict.fromkeys([*disabled, *_installed_plugins()]))
    return {n: n not in disabled for n in names}


def _short(name: str) -> str:
    return name.removeprefix("claude.ai ")


def _browser_lock() -> str:
    return "" if config.BROWSER_ENABLED else "Navigateur coupé par ASTREL_BROWSER."


# --------------------------------------------------------------------------- définition

@dataclass
class Item:
    key: str
    group: str
    label: str
    help: str
    kind: str                      # bool, int, float, choice, text, flags, tri, info, action
    applies: str                   # hot, brain, mcp
    default: Callable[[], Any]
    env: str = ""                  # variable ASTREL_* qui impose la valeur
    lock: Callable[[], str] | None = None  # autre raison d'être grisé (vide : non)
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str = ""
    choices: tuple[tuple[str, str], ...] = ()
    extra: dict = field(default_factory=dict)
    # genres info/action : vue calculée {"value": {lines, block?, link?}, "actions": [...]}, et
    # exécution d'un bouton (action, commande) → {ok, text?, level?, block?, link?, error?}
    view: Callable[[], dict] | None = None
    run: Callable[[str, dict], dict] | None = None
    # réglage `live` (17/10) : jamais dans le fichier ; `get`/`put`/`options` ici ou par `bind`
    live: bool = False
    get: Callable[[], Any] | None = None
    put: Callable[[Any], dict | None] | None = None
    options: Callable[[], Any] | None = None

    def lock_reason(self) -> str:
        if self.env and os.environ.get(self.env) is not None:
            return f"Imposé par la variable {self.env} (à retirer de l'environnement d'Astrel pour le régler ici)."
        return self.lock() if self.lock else ""


PANEL_KINDS = ("info", "action")

# Aide par sujet (18/10, plan P5) : réglage → sujet du guide de démarrage
# (docs/GUIDE-DEMARRAGE.md, `<!-- id: … -->`). Décrit sous la clé `guide` (la clé
# `help` porte déjà le texte d'aide) ; le HUD en fait un bouton « ? ».
GUIDE_TOPICS = {
    "compte": "claude", "demarrage": "controle", "diagnostic": "depannage",
    "audio.mic": "micro", "audio.test": "micro", "audio.calibrage": "micro", "audio.output": "voix", "audio.voice": "voix",
    "audio.wake": "parler", "wake_legacy": "parler", "wake_names": "parler", "audio.followup": "parler", "audio.followup_s": "parler", "stt_language": "langue",
    "conversation_language": "langue",
    "confidentialite": "memoire", "history_keep_days": "memoire", "donnees": "memoire", "hors_ligne": "memoire",
    "systeme.autostart": "demarrage-auto", "systeme.bubble": "bulle", "systeme.hotkeys": "parler",
    "whatsapp": "whatsapp", "browser.main_visible": "agents", "browser.extra_visible": "agents",
    "browser.extra_count": "agents", "connectors": "connecteurs",
    "agent_alert_tokens": "agents", "agent_alert_s": "agents", "quota_alert_pct": "agents",
    "plan_mode": "mode-plan", "rappel": "memoire",
}
# Langue de la reconnaissance (Whisper) : les réponses restent en français.
LANGS = (("fr", "Français"), ("en", "Anglais"), ("es", "Espagnol"), ("de", "Allemand"), ("it", "Italien"))
# Langue de la conversation (02/11, demande de l'utilisateur) : `astrel/langue.py`.
CONVERSATION_LANGS = (("fr", "Français"), ("en", "Anglais"))
# Langue de l'interface (20/10, demande de l'utilisateur) : HUD, bulle, mini-chat (`astrel/i18n.py`).
UI_LANGS = (("fr", "Français"), ("en", "English"))
# Apparence (21/10, plan P17, demande de l'utilisateur) : HUD et bulle, à chaud (`astrel/theme.py`,
# liste dans config/themes.json). 25/10 (plan P35, demande de l'utilisateur) : thème Classique
# abandonné, Premium seul ; plus de réglage `theme` (voir LEGACY_KEYS), mode et accent gardés.
THEME_MODES = (("auto", "Automatique (suit le système)"), ("light", "Clair"), ("dark", "Sombre"))
THEME_ACCENTS = (("bleu", "Bleu"), ("graphite", "Graphite"), ("vert", "Vert"), ("orange", "Orange"))
# 22/10 (plan P23, demande de l'utilisateur : montrer Astrel à quelqu'un qui le découvre) : affichage
# du HUD. Avancé par défaut (l'écran d'avant) ; une PREMIÈRE installation écrit Simple
# (`install.initial_affichage`) : Simple chez un autre, Avancé chez l'utilisateur.
AFFICHAGES = (("simple", "Simple"), ("avance", "Avancé"))
# Relecture P17 : la bulle (`theme._clean`) lit ces réglages en minuscules ; la page Réglages
# aussi, sinon « LIGHT » dans settings.json donnait une bulle claire sous un HUD sombre.
LOWER_CHOICES = frozenset({"theme_mode", "theme_accent", "affichage"})
# 25/10 (plan P35) : clés d'anciens réglages, encore présentes dans settings.json : ignorées sans
# avertissement à la lecture (`Reglages._load`).
LEGACY_KEYS = frozenset({"theme"})
# 25/10 (plan P35, compatibilité) : une bulle lancée AVANT P35 relit `theme` dans settings.json et
# retombe sur son défaut Classique s'il manque. `_save` l'écrit donc encore, à « premium ». À retirer
# une fois la bulle relancée par l'utilisateur (la nouvelle bulle ne lit plus cette clé).
LEGACY_WRITE = {"theme": "premium"}
# 23/10 (plan P31) : nom affiché (réglage `assistant_name`, genre « text ») : court, une ligne.
ASSISTANT_NAME_MAX = 32
# 25/10 (plan P36) : variantes du nom qui réveillent (`eveil.noms`), séparées par des virgules.
WAKE_NAMES_MAX = 160
ZONES_MAX = 2000  # 04/11 : zones confidentielles (lot 3b du moteur local)


def _needs_core(key: str) -> Callable[[], str]:
    def reason() -> str:
        with reglages._lock:
            bound = key in reglages._bound
        return "" if bound else "Réglable quand Astrel tourne."
    return reason


def _output_lock() -> str:
    from . import audio
    return "" if audio.engine() == "pulse" else "Choix de la sortie : Linux (PipeWire/PulseAudio) seulement pour l'instant."


def _output_get() -> str:
    from . import audio
    return audio.default_sink()


def _output_put(value: str) -> dict:
    from . import audio
    return audio.set_default_sink(value)


def _output_options() -> list[tuple[str, str]]:
    from . import audio
    current = audio.default_sink()
    sinks = audio.list_sinks()
    out = [(s["name"], s["label"]) for s in sinks]
    if current and current not in {s["name"] for s in sinks}:
        out.insert(0, (current, current))
    return out or [("", "Aucune sortie vue")]


def _compte_view() -> dict:
    from .compte import compte
    return compte.view()


def _compte_run(action: str, cmd: dict) -> dict:
    from .compte import compte
    return compte.run(action, cmd)


# Groupe « Système » (17/10, plan P4, lot 8) : `platform.<système>.autostart_state` /
# `hotkeys_state`, gardés 2 s (une page décrite = plusieurs lectures). Indisponible
# (WSL2 sans systemd, hors GNOME, macOS, Windows) : réglage grisé avec la phrase.
_SYS_CACHE: dict[str, tuple[float, Any]] = {}


def _sys_state(name: str) -> Any:
    import time as _time

    hit = _SYS_CACHE.get(name)
    if hit and _time.monotonic() - hit[0] < 2.0:
        if isinstance(hit[1], Exception):
            raise hit[1]
        return hit[1]
    from . import platform
    try:
        value = getattr(platform.desktop_backend(), name)()
    except Exception as exc:
        _SYS_CACHE[name] = (_time.monotonic(), exc)
        raise
    _SYS_CACHE[name] = (_time.monotonic(), value)
    return value


def _sys_lock(name: str) -> Callable[[], str]:
    def reason() -> str:
        try:
            _sys_state(name)
        except Exception as exc:
            return str(exc)
        return ""
    return reason


def _sys_call(fn: str, *args: Any, **kwargs: Any) -> dict:
    from . import platform
    try:
        text = getattr(platform.desktop_backend(), fn)(*args, **kwargs)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        _SYS_CACHE.clear()
    log.info("système : %s", text)
    return {"ok": True}


def _module_call(module: str, name: str) -> Callable:
    """Fonction d'un module importé au premier appel (pas de cycle d'import)."""
    def call(*args):
        import importlib
        return getattr(importlib.import_module(f"{__package__}.{module}"), name)(*args)
    return call


def _demarrage(name: str) -> Callable:
    def call(*args):
        from .demarrage import demarrage
        return getattr(demarrage, name)(*args)
    return call


def _local(name: str) -> Callable:
    """Méthode du gestionnaire des modèles locaux (plan P8), relu à chaque appel (tests)."""
    def call(*args):
        from . import modeles_locaux
        return getattr(modeles_locaux.local, name)(*args)
    return call


def _initiatives_view() -> dict:
    """Plan P32 : les initiatives ne tournent qu'avec le capteur REFLEX (« Routines »)."""
    from .sensors import sensors

    if sensors.is_on("REFLEX"):
        line = {"label": "Routines", "text": "allumées : les initiatives peuvent partir", "level": "ok"}
    else:
        line = {"label": "Routines", "text": "éteintes : aucune initiative ne part (Réglages › Capteurs)",
                "level": "warn"}
    return {"value": {"lines": [line]}, "actions": []}


# 28/10 (plan P40 lot A4, demande de l'utilisateur : choix du moteur ET du modèle, en auto par défaut ou en
# manuel). Listes calculées depuis le catalogue `config/ia-web.json` (`ia_web`), jamais un nom
# inventé : en manuel, seuls les modèles PROPOSÉS (le défaut du site tant qu'ils ne sont pas relevés).
IMAGES_MODES = (("auto", "Automatique"), ("manuel", "Manuel"))
_IMAGES_PERMUTATIONS_MAX = 4  # au-delà, rotations seulement (pas 120 ordres dans une liste)


def _images_ordres() -> list[tuple[str, str]]:
    import itertools

    from . import ia_web
    noms = ia_web.services("image")
    if len(noms) <= _IMAGES_PERMUTATIONS_MAX:
        ordres = [list(p) for p in itertools.permutations(noms)]
    else:
        ordres = [noms[i:] + noms[:i] for i in range(len(noms))]
    return [(",".join(o), " → ".join(ia_web.libelle(s) for s in o)) for o in ordres]


def _images_ordre_defaut() -> str:
    from . import ia_web
    return ",".join(ia_web.services("image"))


def _images_manuels() -> list[tuple[str, str]]:
    from . import ia_web
    return [(f"{s}/{m['id']}", f"{ia_web.libelle(s)} · {m['libelle']}")
            for s in ia_web.services("image") for m in ia_web.modeles_proposes(s)]


def _images_manuel_defaut() -> str:
    choix = _images_manuels()
    return choix[0][0] if choix else "gemini/defaut"


def _video_choix() -> list[tuple[str, str]]:
    from . import videos_ia
    return videos_ia.choix_proposes()


def _video_choix_defaut() -> str:
    """Le premier proposé, soit le modèle par défaut du service (Veo 3.1 Lite, 8 s)."""
    choix = _video_choix()
    return choix[0][0] if choix else ""


def _quota_reprise_defaut() -> str:
    """27/10 : relance des agents arrêtés pour le quota, « auto » par défaut (demande de l'utilisateur) ;
    lu ici plutôt que dans `config` (variable `ASTREL_QUOTA_REPRISE`, valeur inconnue = auto)."""
    value = os.environ.get("ASTREL_QUOTA_REPRISE", "auto").strip().lower()
    return value if value in ("auto", "carte", "rien") else "auto"


# 28/10 (mission hud-boutons-t10) : les boutons « Lire aussitôt », « Mission » et « Éteindre » quittent
# la barre du HUD (décision de l'utilisateur) ; leurs accès passent dans les Réglages.
# Comptes rendus : le réglage reste dans runtime/reports.txt (`astrel/reports.py` fait foi, `ASTREL_REPORTS`
# prioritaire) ; jamais dans settings.json. `settings_set` est refusé à Claude (core._settings_command) et
# le serveur MCP n'expose toujours pas ce réglage.
REPORTS_MODES = (("demande", "À la demande"), ("direct", "Lus aussitôt"))


def _reports_mode() -> str:
    from .reports import reports
    return reports.mode


def _reports_put(value: str) -> dict:
    from .reports import reports
    return reports.set_mode(value)


def _hors_ligne_impose() -> bool:
    from . import hors_ligne
    return hors_ligne.impose()


def _hud_action(action_id: str, label: str, hud: str, danger: bool = False) -> Callable[[], dict]:
    """Ligne dont le bouton est exécuté par le HUD LUI-MÊME (`hud` : nom de la fonction branchée par
    hud/src/main.js, `onHudAction`), qui envoie sa commande habituelle (`mission start`, `quit`) avec ses
    droits habituels ; `settings_action` n'a rien à exécuter pour elle (pas de `run`)."""
    def view() -> dict:
        return {"value": {"lines": []},
                "actions": [{"id": action_id, "label": label, "hud": hud, **({"danger": True} if danger else {})}]}
    return view


ITEMS: dict[str, Item] = {i.key: i for i in (
    Item("compte", "compte", "Compte Claude",
         "L'abonnement Claude de cette machine, par la commande officielle de Claude Code ; "
         "Astrel ne voit ni ne garde aucun jeton.",
         "info", "hot", lambda: None, view=_compte_view, run=_compte_run),
    # 20/10 (plan P11) : fournisseur du cerveau. Le cœur refuse un choix impossible ou risqué
    # (`fournisseurs.refus_choix`) avant de l'écrire ; les clés passent par « Clés et passerelle ».
    Item("fournisseur", "fournisseur", "Fournisseur",
         "Qui fait tourner le cerveau : ton abonnement Claude, ta clé API Claude, ou un autre fournisseur par sa "
         "clé API, sans Claude (conversation seule, sans outil ni mémoire ; jamais le mode Sans confirmation ni la levée "
         "du garde-fou). Pris au prochain démarrage du cerveau ; un autre fournisseur repart d'une conversation neuve.",
         "choice", "brain", lambda: str(config.FOURNISSEUR), env="ASTREL_FOURNISSEUR",
         options=_module_call("fournisseurs", "choix")),
    Item("fournisseur.cles", "fournisseur", "Clés et passerelle",
         "Clés API gardées sur cette machine seulement (config/secrets.json), jamais affichées ; passerelle "
         "locale (LiteLLM, venv à part) pour les fournisseurs autres que Claude.",
         "action", "hot", lambda: None, view=_module_call("fournisseurs", "vue"), run=_module_call("fournisseurs", "run")),
    Item("depense_alerte_usd", "fournisseur", "Alerte de dépense",
         "Avec une clé API (facturée à l'usage) : au-delà de cette dépense estimée dans la journée, une alerte, "
         "jamais d'arrêt ; 0 : jamais.",
         "float", "hot", lambda: float(config.DEPENSE_ALERTE_USD), env="ASTREL_DEPENSE_ALERTE_USD",
         min=0, max=1000, step=1, unit="$"),
    Item("demarrage", "demarrage", "Ce qu'il faut pour qu'Astrel marche",
         "Contrôle de la machine (tools/check_install.py) : en rouge, à corriger d'abord ; en orange, une fonction manquera.",
         "info", "hot", lambda: None, view=_demarrage("view"), run=_demarrage("run")),
    Item("diagnostic", "demarrage", "Diagnostic à coller",
         "Rapport sans donnée personnelle (ni e-mail, ni nom d'utilisateur, ni dossier personnel) à envoyer avec ta question.",
         "action", "hot", lambda: None, view=_demarrage("diagnostic_view"), run=_demarrage("run_diagnostic")),
    Item("audio.mic", "voix", "Micro",
         "Micro qu'écoute Astrel ; « micro du système » suit le réglage de son du bureau.",
         "choice", "hot", lambda: "", live=True, lock=_needs_core("audio.mic")),
    # 02/11 (demande de l'utilisateur) : assistant pas à pas du HUD (calibrage.js), même mesure que --calibrate.
    Item("audio.calibrage", "voix", "Calibrage du micro",
         "Mesure le bruit de fond puis ta voix (tu dis « Astrel » trois fois), et propose le gain et les seuils "
         "de ce micro, à côté des réglages actuels. Rien n'est changé sans « Enregistrer ».",
         "action", "hot", lambda: None, lock=_needs_core("audio.calibrage")),
    Item("audio.output", "voix", "Haut-parleur",
         "Sortie du son de TOUT l'ordinateur (comme dans les réglages de son du bureau), par pactl.",
         "choice", "hot", lambda: "", live=True, lock=_output_lock,
         get=_output_get, put=_output_put, options=_output_options),
    Item("audio.voice", "voix", "Voix d'Astrel",
         "Voix en ligne gratuite (Edge) ; seules celles qui parlent la langue choisie plus bas sont proposées.",
         "choice", "hot", lambda: "", live=True, lock=_needs_core("audio.voice")),
    # 25/10 (plan P36) : le seuil ne vaut que pour l'ANCIEN mot (modèle audio hey_jarvis, seuils mesurés) ;
    # le nom affiché réveille par la voie texte (`listener.WakeWord`, `wake_names`). L'ancien mot d'éveil
    # ne s'écrit plus dans un texte d'écran : `i18n.nom` le changerait en « Hey Astrel ».
    Item("audio.wake", "voix", "Sensibilité de l'ancien mot d'éveil",
         "Seuil de l'ancien mot d'éveil (modèle audio hey_jarvis) pour ce micro : plus bas, il se réveille plus "
         "facilement, avec plus de faux réveils. Sans effet sur le réveil par le nom.",
         "float", "hot", lambda: float(config.WAKE_THRESHOLD), env="ASTREL_WAKE_THRESHOLD", live=True,
         lock=_needs_core("audio.wake"), min=0.05, max=0.95, step=0.05),
    Item("wake_legacy", "voix", "Accepter aussi l'ancien mot d'éveil",
         "Garde l'ancien mot d'éveil (modèle audio hey_jarvis) en plus du nom, le temps de la transition. "
         "Non : seul le nom réveille.",
         "bool", "hot", lambda: bool(config.WAKE_LEGACY), env="ASTREL_WAKE_LEGACY"),
    Item("wake_names", "voix", "Variantes du nom qui réveillent",
         "Mots séparés par des virgules, acceptés comme le nom au début d'une phrase (la reconnaissance vocale "
         "écrit souvent « astral » pour « Astrel »). Vide : les variantes par défaut.",
         "text", "hot", lambda: str(config.WAKE_NAMES), env="ASTREL_WAKE_NAMES", max=WAKE_NAMES_MAX),
    # 20/10 (demande de l'utilisateur) : fenêtre de suite, branchée par le cœur (`Astrel._set_followup`,
    # runtime/followup.txt fait foi, comme pour le menu du badge de mode). Durée retenue même coupée.
    Item("audio.followup", "voix", "Écoute prolongée après une réponse",
         "Après sa réponse, Astrel écoute encore quelques secondes : tu enchaînes sans redire « Astrel ». "
         "Non : une phrase à la fois.",
         "bool", "hot", lambda: config.FOLLOWUP_TIMEOUT_S > 0, live=True, lock=_needs_core("audio.followup")),
    Item("audio.followup_s", "voix", "Durée de l'écoute prolongée",
         "Secondes d'écoute après chaque réponse ; gardée quand l'écoute prolongée est coupée, pour la retrouver "
         "en la rallumant.",
         "int", "hot", lambda: int(config.FOLLOWUP_TIMEOUT_S), live=True, lock=_needs_core("audio.followup_s"),
         min=config.FOLLOWUP_SETTING_MIN_S, max=config.FOLLOWUP_SETTING_MAX_S, step=1, unit="s"),
    # 28/10 (hud-boutons-t10) : ancien interrupteur « Lire aussitôt » de la barre du HUD.
    Item("reports", "voix", "Comptes rendus des tâches de fond",
         "À la demande : un agent qui finit pose son compte rendu en silence, avec un bouton « Lire ». Lus "
         "aussitôt : il est dit dès qu'il arrive, jamais pendant que tu parles.",
         "choice", "hot", _reports_mode, env="ASTREL_REPORTS", live=True, choices=REPORTS_MODES,
         get=_reports_mode, put=_reports_put),
    Item("audio.test", "voix", "Essais",
         "Fait parler Astrel, ou écoute le micro 3 secondes et dit si le son arrive.",
         "action", "hot", lambda: None),
    # 23/10 (demande de l'utilisateur : panneau droit du HUD allégé, seul WATCH y reste) : tous les capteurs,
    # allumés ou éteints ici comme par la carte du panneau ou la voix (`sensors.set`, core.py).
    Item("capteurs", "capteurs", "Capteurs",
         "Présence (webcam), vue de l'écran, gestes, ne pas déranger, routines : allumer ou éteindre. "
         "Aussi à la voix (« allume les gestes »).",
         "action", "hot", lambda: None, lock=_needs_core("capteurs")),
    # 02/11 (demande de l'utilisateur) : conversation anglaise complète (`astrel/langue.py`) ; la langue de
    # l'interface est un autre réglage (`ui_language`).
    Item("conversation_language", "voix", "Langue de la conversation",
         "Langue dans laquelle Astrel t'écoute, te répond et parle : consignes de Claude, reconnaissance vocale, "
         "voix et commandes dites. Une session neuve démarre à ton message suivant (après les tâches de fond en "
         "cours). La langue de l'interface n'en dépend pas.",
         "choice", "hot", lambda: str(config.CONVERSATION_LANGUAGE), env="ASTREL_CONVERSATION_LANGUAGE",
         choices=CONVERSATION_LANGS),
    Item("stt_language", "voix", "Langue parlée",
         "Conversation en français : langue dans laquelle tu parles à Astrel (reconnaissance vocale), ses "
         "réponses restant en français. Conversation en anglais : toujours l'anglais.",
         "choice", "hot", lambda: str(config.STT_LANGUAGE), choices=LANGS),
    # 20/10 (demande de l'utilisateur) : langue de l'interface, appliquée à chaud (le HUD se recharge,
    # la bulle relit le fichier). La voix, les réponses et le prompt d'Astrel n'en dépendent pas.
    Item("ui_language", "voix", "Langue de l'interface",
         "Langue des textes du HUD, de la bulle et du mini-chat. La voix d'Astrel et ses réponses n'en "
         "dépendent pas.",
         "choice", "hot", lambda: "fr", env="ASTREL_UI_LANGUAGE", choices=UI_LANGS),
    # 21/10 (plan P17) : apparence du HUD et de la bulle, appliquée à chaud (le HUD sans
    # rechargement, la bulle relit le fichier). `display` : pastilles de couleur. 25/10 (plan P35) :
    # plus de choix du thème (Premium seul), `ASTREL_THEME` n'a plus d'effet.
    Item("theme_mode", "apparence", "Clair ou sombre",
         "Apparence du HUD, de la bulle et du mini-chat. Automatique : suit le réglage clair ou sombre du "
         "système.",
         "choice", "hot", lambda: "auto", choices=THEME_MODES),
    Item("theme_accent", "apparence", "Couleur d'accent",
         "Couleur des boutons principaux, des liens et de la bulle.",
         "choice", "hot", lambda: "bleu", choices=THEME_ACCENTS, extra={"display": "swatches"}),
    # 22/10 (plan P23) : appliqué sans rechargement (hud/src/apparence.js), aussi par l'interrupteur
    # « Simple » de la barre du HUD. Les signaux de sécurité (Sans confirmation, garde-fou levé,
    # Stop tout) restent visibles dans les deux.
    Item("affichage", "apparence", "Affichage",
         "Simple : l'écran centré sur la conversation, pour montrer Astrel (ni panneau du cerveau, ni "
         "capteurs, ni onglets de projets). Avancé : tout. Avant une démo : « Nouvelle conversation », et "
         "rétablis le garde-fou s'il est levé.",
         "choice", "hot", lambda: "avance", choices=AFFICHAGES),
    # 23/10 (plan P31, demande de l'utilisateur) : nom affiché du produit, « Astrel » par défaut. Lu par le HUD
    # (évènement `settings`, la page se recharge) et la bulle (`i18n.assistant_name`, relu à chaque
    # affichage). Depuis le 25/10 (plan P36), c'est aussi le mot d'éveil (voie texte) ; pas les identifiants.
    Item("assistant_name", "apparence", "Nom de l'assistant",
         "Nom affiché dans le HUD, la bulle et les messages d'écran ; c'est aussi le mot d'éveil, dit au début "
         "d'une phrase. Vide : le nom par défaut.",
         "text", "hot", lambda: config.ASSISTANT_NAME, env="ASTREL_ASSISTANT_NAME", max=ASSISTANT_NAME_MAX),
    # 04/11 (lot 2 du moteur local) : mode « rien ne sort ». Changé par le cœur seulement
    # (`_hors_ligne_switch` : refusé pendant une tâche de Claude et à Claude), jamais écrit directement.
    Item("hors_ligne", "confidentialite", "Rien ne sort (hors ligne)",
         "Claude est arrêté et tes demandes vont au modèle local (Ollama, sur la machine) ; la voix passe par "
         "Piper ; WhatsApp, images et vidéos IA, veille, mises à jour et connecteurs sont coupés. Refusé tant "
         "qu'une tâche de Claude tourne (« Stop tout » d'abord). Dis aussi « passe hors ligne » ou « repasse en "
         "ligne ».",
         "bool", "hot", lambda: _hors_ligne_impose(), env="ASTREL_HORS_LIGNE"),
    # 04/11 (lot 3b du moteur local) : dossiers dont rien ne part chez Claude sans ta carte (le routeur garde
    # une demande qui les cite ; le garde-fou demande avant que Claude y lise). Lu par `guard.zones_confidentielles`.
    Item("zones_confidentielles", "confidentialite", "Zones confidentielles",
         "Dossiers (chemins absolus séparés par « ; ») dont rien ne part chez Claude sans ton accord : une "
         "demande qui les cite va au modèle local ou te demande à l'écran, et Claude ne peut y lire sans une "
         "question. Vide : aucune zone.",
         "text", "hot", lambda: "", max=ZONES_MAX),
    Item("confidentialite", "confidentialite", "Ce qui part de cette machine",
         "Ce qu'Astrel envoie, à qui, et ce qui reste ici.",
         "info", "hot", lambda: None, view=_module_call("confidentialite", "view")),
    Item("history_keep_days", "confidentialite", "Garder l'historique",
         "Jours gardés dans l'index des conversations d'Astrel (runtime/history.db) ; 0 : tout garder.",
         "int", "hot", lambda: 0, min=0, max=3650, step=1, unit="jours"),
    Item("donnees", "confidentialite", "Mémoire et historique",
         "Exporter la mémoire (zip dans ~/Astrel-export) ; effacer l'historique ou la mémoire (mot à taper).",
         "action", "hot", lambda: None, view=_module_call("confidentialite", "data_view"),
         run=_module_call("confidentialite", "run")),
    Item("maj", "maj", "Version d'Astrel",
         "Vérifie s'il y a du nouveau sur le dépôt et met Astrel à jour (update.py) ; rien n'est jamais envoyé.",
         "info", "hot", lambda: None, view=_module_call("maj", "view_maj"), run=_module_call("maj", "run_maj")),
    Item("systeme.autostart", "systeme", "Démarrer avec la session",
         "Astrel se lance à l'ouverture de ta session (service systemd de l'utilisateur) ; il n'est ni arrêté ni lancé maintenant.",
         "bool", "hot", lambda: False, live=True, lock=_sys_lock("autostart_state"),
         get=lambda: bool(_sys_state("autostart_state")["service"]), put=lambda v: _sys_call("set_autostart", v)),
    Item("systeme.bubble", "systeme", "Bulle au démarrage",
         "La bulle flottante s'ouvre aussi avec ta session.",
         "bool", "hot", lambda: False, live=True, lock=_sys_lock("autostart_state"),
         get=lambda: bool(_sys_state("autostart_state")["bubble"]), put=lambda v: _sys_call("set_autostart", True, bubble=v)),
    Item("systeme.hotkeys", "systeme", "Raccourcis clavier",
         "Ctrl+Alt+J parler, Ctrl+Alt+M micro, Ctrl+Alt+X interrompre, Ctrl+Alt+P pause de la voix, Ctrl+Alt+H le HUD (GNOME).",
         "bool", "hot", lambda: False, live=True, lock=_sys_lock("hotkeys_state"),
         get=lambda: bool(_sys_state("hotkeys_state")), put=lambda v: _sys_call("set_hotkeys", v)),
    # 28/10 (hud-boutons-t10) : ancien bouton « Éteindre » de la barre ; même boîte de confirmation, même
    # commande `quit` envoyée par le HUD. « Astrel » est affiché sous le nom choisi (`i18n.nom`).
    Item("systeme.eteindre", "systeme", "Éteindre Astrel",
         "Arrête Astrel et libère la mémoire et la carte graphique jusqu'à ce que tu le rallumes (clic droit sur "
         "la bulle, ou systemctl --user start astrel).",
         "action", "hot", lambda: None, view=_hud_action("eteindre", "Éteindre", "power", danger=True)),
    Item("whatsapp", "whatsapp", "WhatsApp",
         "Ton chat « message à soi-même » : Astrel y répond quand tu t'absentes.",
         "info", "hot", lambda: None),
    Item("browser.main_visible", "navigateurs", "Navigateur principal visible",
         "Fenêtre Chrome du navigateur « playwright », connecté à tes sites ; non : il travaille sans fenêtre.",
         "bool", "mcp", lambda: not config.BROWSER_HEADLESS, env="ASTREL_BROWSER_HEADLESS", lock=_browser_lock),
    Item("browser.extra_visible", "navigateurs", "Navigateurs de test visibles",
         "Navigateurs jetables des sous-agents (playwright2…) ; non : invisibles, rien ne surgit à l'écran.",
         "bool", "mcp", lambda: False, lock=_browser_lock),
    Item("browser.extra_count", "navigateurs", "Navigateurs de test",
         "Nombre de navigateurs jetables, un par sous-agent qui ouvre des pages.",
         "int", "mcp", lambda: int(config.BROWSER_EXTRA), env="ASTREL_BROWSER_EXTRA", lock=_browser_lock,
         min=0, max=MAX_TEST_BROWSERS, step=1),
    # 28/10 (plan P40 lot A4) : lus à chaque demande d'image par `images_ia` (à chaud).
    Item("images_mode", "images", "Choix du moteur",
         "Automatique : les moteurs dans l'ordre ci-dessous, et les comptes dans leur ordre ; un compte épuisé "
         "est sauté jusqu'à sa remise à zéro. Manuel : le moteur et le modèle choisis plus bas seulement ; s'il "
         "est épuisé, un autre compte du même moteur, jamais un autre moteur sans ton clic.",
         "choice", "hot", lambda: "auto", choices=IMAGES_MODES),
    Item("images_ordre", "images", "Ordre des moteurs",
         "Mode automatique : le premier moteur est essayé d'abord, le suivant quand tous ses comptes sont épuisés.",
         "choice", "hot", _images_ordre_defaut, options=_images_ordres),
    Item("images_manuel", "images", "Moteur et modèle en manuel",
         "Mode manuel : le moteur et le modèle utilisés pour chaque image. Seuls les modèles relevés sur le site "
         "sont proposés.",
         "choice", "hot", _images_manuel_defaut, options=_images_manuels),
    # 28/10 (plan P41 lot B3) : lu à chaque demande de vidéo par `videos_ia` (à chaud).
    Item("video_choix", "images", "Vidéo : modèle et durée",
         "Modèle et durée de chaque vidéo, avec son coût en crédits gratuits. Seules les durées que le modèle "
         "propose sur le site sont listées.",
         "choice", "hot", _video_choix_defaut, options=_video_choix),
    Item("connectors","connecteurs", "Connecteurs claude.ai",
         "Coché : actif pour Astrel ; décoché : bloqué, ses outils ne pèsent plus sur chaque tour.",
         "flags", "brain", _connectors_default, env="ASTREL_DENIED_MCP_SERVERS"),
    Item("skills", "skills", "Skills",
         "Actif : décrite au modèle ; nom seul : appelable sans sa description ; coupé : invisible.",
         "tri", "brain", _skills_default, env="ASTREL_SKILL_OVERRIDES"),
    Item("plugins", "plugins", "Plugins de Claude Code",
         "Décoché : coupé pour Astrel seul (caveman réécrirait chaque réponse en style télégraphique).",
         "flags", "brain", _plugins_default, env="ASTREL_DISABLED_PLUGINS"),
    Item("agent_alert_tokens", "seuils", "Alerte agent : tokens de travail",
         "Une tâche de fond ou un sous-agent qui écrit plus que ça (entrée hors cache, création de cache et "
         "sortie ; le cache relu ne compte pas) déclenche une alerte, jamais un arrêt.",
         "int", "hot", lambda: int(getattr(config, "AGENT_ALERT_TOKENS", 1_000_000)),
         env="ASTREL_AGENT_ALERT_TOKENS", min=100_000, max=100_000_000, step=100_000, unit="tokens"),
    Item("agent_alert_s", "seuils", "Alerte agent : durée",
         "Une tâche de fond qui tourne plus longtemps que ça déclenche une alerte, jamais un arrêt.",
         "int", "hot", lambda: int(round(float(getattr(config, "AGENT_ALERT_S", 3600)))),
         env="ASTREL_AGENT_ALERT_S", min=60, max=86_400, step=60, unit="s"),
    Item("quota_alert_pct", "seuils", "Alerte quota",
         "Remplissage de la fenêtre de 5 h à partir duquel Astrel fait écrire le point de reprise des chantiers.",
         "int", "hot", lambda: int(round(float(getattr(config, "QUOTA_ALERT_PCT", 85)))),
         env="ASTREL_QUOTA_ALERT_PCT", min=50, max=100, step=5, unit="%"),
    # 19/10 (plan P8, partage avec un abonnement modeste) : Astrel PROPOSE, l'utilisateur choisit.
    Item("quota_offer", "limite", "Proposer un modèle plus économe",
         "Quand la limite approche, une carte propose de passer d'Opus à Sonnet, de Sonnet à Haiku, ou de Haiku "
         "au modèle local (s'il est choisi plus bas) ; rien ne change sans ton clic.",
         "choice", "hot", lambda: str(config.QUOTA_OFFER), env="ASTREL_QUOTA_OFFER",
         choices=(("propose", "Proposer"), ("off", "Ne pas proposer"))),
    Item("quota_offer_pct", "limite", "Seuil de la proposition",
         "Remplissage de la fenêtre d'utilisation la plus pleine à partir duquel la carte apparaît, une fois par fenêtre.",
         "int", "hot", lambda: int(round(float(config.QUOTA_OFFER_PCT))), env="ASTREL_QUOTA_OFFER_PCT",
         min=50, max=99, step=5, unit="%"),
    Item("reprise_auto", "limite", "Reprise automatique",
         "Limite atteinte : à l'heure où elle se lève, Astrel reprend seul les chantiers en cours (annulée si tu "
         "reprends la main avant). Non : il te prévient seulement.",
         "bool", "hot", lambda: bool(config.REPRISE_AUTO), env="ASTREL_REPRISE_AUTO"),
    # 22/10 (demande de l'utilisateur : le compteur restait figé quand il utilisait Claude Code ailleurs).
    Item("quota_refresh", "limite", "Suivre le quota hors d'Astrel",
         "Toutes les 10 min au repos, Astrel relit l'utilisation de ton abonnement (commande /usage du CLI, "
         "sans jeton), pour compter aussi Claude Code utilisé ailleurs. Non : seulement pendant ses échanges.",
         "bool", "hot", lambda: bool(config.QUOTA_REFRESH), env="ASTREL_QUOTA_REFRESH"),
    # 24/10 (plan P34, demande de l'utilisateur) : arrêt automatique des agents sur quota (`astrel/quota_arret.py`).
    Item("quota_arret", "limite", "Agents près de la limite",
         "Fenêtre de 5 h presque pleine : au seuil de note, les agents en cours notent leur état ; au seuil "
         "d'arrêt, Astrel les arrête proprement ; à la levée de la limite, il les relance ou te le propose "
         "(réglage « Agents arrêtés pour le quota »).",
         "choice", "hot", lambda: str(config.QUOTA_ARRET), env="ASTREL_QUOTA_ARRET",
         choices=(("arret", "Noter puis arrêter"), ("note", "Noter seulement"), ("off", "Ne rien faire"))),
    Item("quota_note_pct", "limite", "Seuil de note",
         "Remplissage de la fenêtre de 5 h à partir duquel les agents en cours notent leur état.",
         "int", "hot", lambda: int(round(float(config.QUOTA_NOTE_PCT))), env="ASTREL_QUOTA_NOTE_PCT",
         min=50, max=99, step=1, unit="%"),
    Item("quota_stop_pct", "limite", "Seuil d'arrêt",
         "Remplissage de la fenêtre de 5 h à partir duquel les agents sont arrêtés (toujours au-dessus du seuil "
         "de note).",
         "int", "hot", lambda: int(round(float(config.QUOTA_STOP_PCT))), env="ASTREL_QUOTA_STOP_PCT",
         min=51, max=100, step=1, unit="%"),
    # 27/10 (demande de l'utilisateur : « la relance des agents arrêtés après le quota doit être automatique »).
    Item("quota_reprise", "limite", "Agents arrêtés pour le quota",
         "Quand la limite de 5 h se lève et que la fenêtre est revenue bien sous le seuil de note (60 % au plus), "
         "Astrel relance tout seul les "
         "agents arrêtés pour le quota, l'un après l'autre s'ils touchent les mêmes fichiers. Carte : il te demande "
         "d'abord. Rien : il les note seulement.",
         "choice", "hot", lambda: _quota_reprise_defaut(), env="ASTREL_QUOTA_REPRISE",
         choices=(("auto", "Relancer tout seul"), ("carte", "Me demander sur une carte"), ("rien", "Ne rien relancer"))),
    Item("brain_effort", "cerveau", "Effort de réflexion",
         "Effort du cerveau vocal seul ; le code lourd part de toute façon dans des sous-agents.",
         # 23/10 (plan P28) : appliqué À CHAUD par le cœur (`Astrel._apply_effort`, `Brain.set_effort`).
         "choice", "hot", lambda: str(config.CLAUDE_EFFORT or ""), env="ASTREL_CLAUDE_EFFORT", choices=EFFORTS),
    Item("session_idle_reset_s", "cerveau", "Oubli après inactivité",
         "Après ce temps sans échange, la conversation repart à neuf (0 : jamais).",
         "int", "hot", lambda: int(round(float(config.SESSION_IDLE_RESET_S))),
         env="ASTREL_SESSION_RESET_S", min=0, max=86_400, step=300, unit="s"),
    Item("context_relief_tokens", "cerveau", "Relève de session",
         "Contexte au-delà duquel Astrel, au repos, sauvegarde les chantiers et repart d'une session neuve (0 : jamais).",
         "int", "hot", lambda: int(config.CONTEXT_RELIEF_TOKENS),
         env="ASTREL_CONTEXT_RELIEF_TOKENS", min=0, max=1_000_000, step=10_000, unit="tokens"),
    Item("plan_mode", "cerveau", "Mode plan",
         "Manuel : un plan de chantier attend ta validation (page « Plans ») avant tout codeur ; "
         "automatique : validé dès qu'il est déposé.",
         "choice", "hot", lambda: str(config.PLAN_MODE), env="ASTREL_PLAN_MODE",
         choices=(("manual", "Manuel"), ("auto", "Automatique"))),
    Item("rappel", "cerveau", "Rappel de la mémoire",
         "À chaque message, Astrel joint les notes du coffre qui en parlent : noms et deux extraits courts, "
         "noms seuls, ou rien.",
         "choice", "hot", lambda: str(config.RAPPEL_MODE), env="ASTREL_RAPPEL",
         choices=(("extraits", "Noms et extraits"), ("noms", "Noms seuls"), ("off", "Coupé"))),
    # 19/10 (plan P8) : modèle local de secours (Ollama). Le cœur branche « Mode léger » (`reglages.bind`).
    # 04/11 (lot 3 du moteur local, docs/chantiers/HYBRIDE-QWEN.md) : qui répond à tes demandes (`routeur.decider`).
    Item("moteur_mode", "modele_local", "Qui répond",
         "Automatique : la cybersécurité et les données sensibles au modèle local, le reste à Claude. Local : le "
         "modèle local, Claude proposé quand il faut des outils. Claude : Claude, avec une carte avant d'envoyer "
         "une donnée sensible. Hybride : le modèle local d'abord, Claude proposé pour le code et les actions. "
         "« en local : » ou « avec Claude : » en tête d'une demande l'emporte toujours.",
         "choice", "hot", lambda: "auto", env="ASTREL_MOTEUR_MODE",
         choices=(("auto", "Automatique"), ("local", "Modèle local"), ("claude", "Claude"),
                  ("hybride", "Hybride (local d'abord)"))),
    Item("local.mode", "modele_local", "Mode léger",
         "Astrel discute avec le modèle local, SANS aucun outil (ni fichiers, ni mémoire, ni agents) ; la "
         "conversation repart à neuf. Retour sur Claude automatique quand la limite se lève.",
         "action", "hot", lambda: None, lock=_needs_core("local.mode")),
    Item("local.materiel", "modele_local", "Ce PC et Ollama",
         "Matériel détecté (carte graphique, mémoire, disque) : seuls les modèles qui y tiennent sont proposés. "
         "Astrel n'installe pas Ollama lui-même.",
         "info", "hot", lambda: None, view=_local("materiel_view"), run=_local("run")),
    Item("local.modeles", "modele_local", "Modèles locaux",
         "Du plus petit au plus gros, ceux qui tiennent sur ce PC : rapide (carte graphique), mixte ou lent. "
         "Place sur le disque vérifiée avant chaque téléchargement.",
         "action", "hot", lambda: None, view=_local("models_view"), run=_local("run")),
    # 05/11 (lot 5 du moteur local) : liste blanche des commandes du modèle local (config/commandes-locales.json,
    # protégée par le garde-fou) ; écrite par le cœur sur un clic du HUD seulement (`commandes_locales.run`).
    Item("local.commandes", "modele_local", "Commandes autorisées",
         "Commandes que le modèle local peut lancer par leur identifiant, et rien d'autre (argv en liste, dossier "
         "dans tes projets, délai et sortie bornés). Ouvert seulement après le banc du modèle local, jamais en mode "
         "Sans confirmation ni sous la levée du garde-fou. Seulement d'ici, jamais par Claude.",
         "action", "hot", lambda: None, view=_module_call("commandes_locales", "view"),
         run=_module_call("commandes_locales", "run")),
    # 24/10 (plan P32, demande de l'utilisateur) : Astrel PROPOSE de lui-même, toujours avec ton accord,
    # sans t'interrompre ; un interrupteur par type, plafond de fréquence, heures calmes.
    Item("initiatives_routines", "initiatives", "État",
         "Les initiatives passent par les routines (capteur REFLEX) : rien ne part tant qu'elles sont éteintes.",
         "info", "hot", lambda: None, view=_initiatives_view),
    Item("initiatives", "initiatives", "Initiatives d'Astrel",
         "Astrel propose de lui-même (brief du matin, échéances…) par un compte rendu et une carte ; il "
         "n'agit qu'après ton oui. Jamais quand tu parles, ni en « Ne pas déranger ».",
         "bool", "hot", lambda: bool(config.INITIATIVES), env="ASTREL_INITIATIVES"),
    Item("initiatives_brief", "initiatives", "Brief du matin",
         "Dès 7 h 30 (ou à l'allumage du PC si plus tard) : date, agenda du jour, rappels, comptes rendus à "
         "lire. Sans modèle : aucun jeton.",
         "bool", "hot", lambda: bool(config.INITIATIVES_BRIEF), env="ASTREL_INITIATIVES_BRIEF"),
    Item("initiatives_quota", "initiatives", "Quota de l'abonnement",
         "Le remplissage de ta limite d'utilisation, dans le brief.",
         "bool", "hot", lambda: bool(config.INITIATIVES_QUOTA), env="ASTREL_INITIATIVES_QUOTA"),
    Item("initiatives_echeances", "initiatives", "Échéances",
         "Veille sécurité du lundi et bilan du dimanche : rappelés dans le brief, et leurs routines suivent "
         "le plafond et les heures calmes.",
         "bool", "hot", lambda: bool(config.INITIATIVES_ECHEANCES), env="ASTREL_INITIATIVES_ECHEANCES"),
    Item("initiatives_courrier", "initiatives", "Courrier important",
         "Deux passages par jour ouvré (9 h 30, 14 h) : Astrel lit tes mails non lus par le connecteur "
         "Gmail et résume les importants (coûte un échange). Il ne supprime, n'envoie ni ne range rien : "
         "une étiquette n'est posée qu'après ton oui.",
         "bool", "hot", lambda: bool(config.INITIATIVES_COURRIER), env="ASTREL_INITIATIVES_COURRIER"),
    Item("initiatives_max_jour", "initiatives", "Propositions par jour",
         "Au plus ce nombre de propositions par jour (et deux par type), espacées d'au moins 1 h 30. "
         "Seule une alerte de faille exploitée passe outre.",
         "int", "hot", lambda: int(config.INITIATIVES_MAX_JOUR), env="ASTREL_INITIATIVES_MAX_JOUR",
         min=1, max=12, step=1),
    Item("initiatives_calme", "initiatives", "Heures calmes",
         "Aucune proposition pendant ces heures ; elle attend la fin de la plage.",
         "choice", "hot", lambda: str(config.INITIATIVES_CALME), env="ASTREL_INITIATIVES_CALME",
         choices=(("22-7", "De 22 h à 7 h"), ("21-8", "De 21 h à 8 h"), ("23-6", "De 23 h à 6 h"),
                  ("off", "Aucune"))),
    # 18/10 (plan P7) : interrupteur sans durée, branché par le cœur (`reglages.bind`) ; pas un
    # réglage `bool` : rien n'est écrit dans runtime/settings.json, retombe à « en place » à la relance.
    # 24/10 (plan P29 lot B, demande de l'utilisateur) : cases de confirmation. Résumé et boutons (Tout
    # décocher, Tout cocher, Valeurs par défaut), puis une grille de cases par groupe ; branchés par
    # le cœur (`reglages.bind`) qui seul les change, pour l'utilisateur depuis le HUD ; jamais dans
    # runtime/settings.json (fichier à part, signé : `cases_confirmation`).
    Item("confirmations", "garde_fou", "Confirmations demandées",
         "Case cochée : Astrel te demande avant cette action. Décochée : elle passe sans carte. Tout "
         "décoché : plus aucune carte. « Stop tout » arrête toujours tout. Seulement d'ici, jamais par Claude.",
         "action", "hot", lambda: None),
    *(Item(f"confirmations.{gid}", "garde_fou", title, "Cochée : carte de confirmation ; décochée : sans carte.",
           "flags", "hot",
           (lambda gid=gid: {c.id: c.coche for c in limites.cases_du_groupe(gid)}), live=True,
           extra={"labels": {c.id: c.texte for c in limites.cases_du_groupe(gid)}})
      for gid, title in limites.GROUPES_CASES),
    # 28/10 (hud-boutons-t10) : ancien bouton « Mission » de la barre ; ouvre la fenêtre de mission du HUD,
    # qui envoie `mission start` (refusé à Claude, `totalgate.loosens_guard`). Bandeau violet inchangé.
    Item("mission", "garde_fou", "Mission autonome",
         "Astrel enchaîne seul les tours vers un objectif vérifiable, dans un périmètre et un budget que tu "
         "fixes. Un bandeau violet la montre, avec Pause et Arrêter. Lancée d'ici seulement, jamais par Claude.",
         "action", "hot", lambda: None, view=_hud_action("lancer", "Lancer une mission", "mission_start")),
    Item("garde_fou_levee", "garde_fou", "Levée du garde-fou pour travailler sur le code d'Astrel",
         "Levé : Claude modifie sans question le code du HUD, de la bulle, de WhatsApp et du garde-fou. "
         "Le reste de dangereux reste demandé. Seulement d'ici, jamais par Claude ; « Stop tout » le rétablit.",
         "action", "hot", lambda: None),
)}


# --------------------------------------------------------------------------- validation

def _bool(item: Item, raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.strip().lower() in TRUE_WORDS + FALSE_WORDS:
        return raw.strip().lower() in TRUE_WORDS
    raise SettingError(item.key, "il faut oui ou non.")


def _number(item: Item, raw: Any) -> int | float:
    value: Any = raw
    if isinstance(raw, str):
        try:
            value = float(raw.strip().replace(",", ".").replace(" ", ""))
        except ValueError:
            raise SettingError(item.key, "il faut un nombre.") from None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):  # NaN, inf
        raise SettingError(item.key, "il faut un nombre.")
    if item.kind == "int":
        if float(value) != int(value):
            raise SettingError(item.key, "il faut un nombre entier.")
        value = int(value)
    else:
        value = float(value)
    unit = f" {item.unit}" if item.unit else ""
    if item.min is not None and value < item.min:
        raise SettingError(item.key, f"au moins {item.min:g}{unit}.")
    if item.max is not None and value > item.max:
        raise SettingError(item.key, f"au plus {item.max:g}{unit}.")
    return value


def _text(item: Item, raw: Any) -> str:
    """Texte d'une ligne (23/10, plan P31) : vide = valeur par défaut ; ni retour à la ligne, ni caractère
    de contrôle, ni chevron ; `item.max` caractères au plus."""
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raise SettingError(item.key, "il faut un texte.")
    value = raw.strip()
    if not value:
        return str(item.default())
    if any(c in "<>" or ord(c) < 32 or ord(c) == 127 for c in value):
        raise SettingError(item.key, "une seule ligne, sans chevrons < >.")
    if item.max is not None and len(value) > item.max:
        raise SettingError(item.key, f"au plus {item.max:g} caractères.")
    return " ".join(value.split())


def _choices(item: Item) -> list[tuple[str, str]]:
    """Choix d'une liste : calculés (`options`, branchés ou non), sinon fixes."""
    fn = reglages._hook(item.key, "options") if item.live else item.options
    if fn is None:
        return list(item.choices)
    try:
        return [(str(v), str(lbl)) for v, lbl in (fn() or ())]
    except Exception as exc:
        log.warning("choix de %s illisibles : %s", item.key, exc)
        return []


def _choice(item: Item, raw: Any) -> str:
    allowed = [v for v, _ in _choices(item)]
    value = "" if raw is None else str(raw).strip()
    if item.key in LOWER_CHOICES:
        value = value.lower()
    if value not in allowed:
        raise SettingError(item.key, "valeur inconnue (au choix : " + ", ".join(v or "« réglage du CLI »" for v in allowed) + ").")
    return value


def _mapping(item: Item, raw: Any, current: dict) -> dict:
    """Objet {nom: valeur} : noms connus seulement ; complété par la valeur actuelle
    (le HUD envoie l'objet COMPLET, un objet partiel ne change que ses noms)."""
    if not isinstance(raw, dict):
        raise SettingError(item.key, "il faut un objet {nom: valeur}.")
    out = dict(current)
    for name, v in raw.items():
        if name not in current:
            raise SettingError(item.key, f"« {name} » inconnu.")
        if item.kind == "flags":
            if not isinstance(v, bool):
                raise SettingError(item.key, f"« {name} » : il faut vrai ou faux.")
        else:
            # Un état posé ailleurs (« user-invocable-only ») revient tel quel : accepté.
            if v not in TRI_STATES and not (v in config.SKILL_STATES and v == current.get(name)):
                raise SettingError(item.key, f"« {name} » : état inconnu (on, name-only ou off).")
        out[name] = v
    return out


def validate(key: str, raw: Any, current: Any = None) -> Any:
    item = ITEMS.get(key)
    if item is None:
        raise SettingError(key, f"réglage inconnu : {key or '(vide)'}.")
    if item.kind in PANEL_KINDS:
        raise SettingError(key, "lecture seule : passe par ses boutons.")
    if item.kind == "bool":
        return _bool(item, raw)
    if item.kind in ("int", "float"):
        return _number(item, raw)
    if item.kind == "choice":
        return _choice(item, raw)
    if item.kind == "text":
        value = _text(item, raw)
        if key == "zones_confidentielles" and value:  # 04/11 : des dossiers absolus, rien d'autre
            for part in value.split(";"):
                if part.strip() and not os.path.isabs(os.path.expanduser(part.strip())):
                    raise SettingError(key, f"chemin absolu attendu : {part.strip()}")
        return value
    return _mapping(item, raw, current if isinstance(current, dict) else item.default())


# --------------------------------------------------------------------------- état

class Reglages:
    """Choix de l'utilisateur (fichier) + valeurs prises au dernier démarrage (mémoire)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._chosen: dict[str, Any] | None = None   # lu au premier usage
        self._stamp: tuple = ()                       # (inode, date, taille) du fichier lu
        self._unreadable = ""                         # raison si le fichier est illisible (écriture refusée)
        self._applied: dict[str, Any] = {}           # valeur prise au dernier démarrage concerné
        self._bound: dict[str, dict] = {}             # vues et actions branchées par le cœur (`bind`)

    def reset(self) -> None:
        """Oublie tout (tests) : le fichier sera relu."""
        with self._lock:
            self._chosen, self._stamp, self._applied, self._bound = None, (), {}, {}
            self._unreadable = ""

    # --- fichier

    @staticmethod
    def _file_stamp() -> tuple:
        try:
            st = config.ASTREL_SETTINGS_FILE.stat()
        except OSError:
            return ()
        return (st.st_ino, st.st_mtime_ns, st.st_size)

    def _load(self) -> dict[str, Any]:
        # Relu si le fichier a changé (relecture P17) : settings.json édité à la main, la bulle le
        # suivait (`theme.current`) mais le HUD recevait l'ancien choix gardé en mémoire.
        stamp = self._file_stamp()
        if self._chosen is not None and stamp == self._stamp:
            return self._chosen
        self._stamp = stamp
        path = config.ASTREL_SETTINGS_FILE
        chosen: dict[str, Any] = {}
        try:
            text = path.read_text(encoding="utf-8")
            data = json.loads(text) if text.strip() else {}
            if not isinstance(data, dict) or not isinstance(data.get("values", {}), dict):
                raise ValueError("forme inattendue")
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError) as exc:
            # 21/10 (fiche P17-5) : un fichier illisible (édition à la main interrompue) n'est
            # JAMAIS réécrit avec un contenu réduit : `set` refuse tant qu'il l'est, et les
            # dernières valeurs valides restent en vigueur (défauts s'il n'y en a jamais eu).
            self._unreadable = str(exc) or type(exc).__name__
            if self._chosen is not None:
                log.warning("réglages illisibles (%s) : dernières valeurs valides gardées", exc)
                return self._chosen
            log.warning("réglages illisibles (%s) : valeurs par défaut", exc)
            self._chosen = {}
            return self._chosen
        self._unreadable = ""
        values = data.get("values")
        for key, raw in (values or {}).items() if isinstance(values, dict) else ():
            if key in LEGACY_KEYS:
                continue  # ancien réglage (plan P35), sans avertissement
            try:
                item = ITEMS.get(key)
                if item is not None and item.kind in ("flags", "tri") and isinstance(raw, dict):
                    # Un plugin ou connecteur choisi puis disparu : on oublie ce nom, pas tout le choix.
                    known = item.default()  # une seule lecture des dossiers de skills
                    raw = {n: v for n, v in raw.items() if n in known}
                chosen[key] = validate(key, raw)
            except SettingError as exc:
                log.warning("réglage %s ignoré : %s", key, exc.message)
        self._chosen = chosen
        return chosen

    def _save(self) -> None:
        path = config.ASTREL_SETTINGS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        values = {**(self._chosen or {}), **LEGACY_WRITE}  # compatibilité bulle d'avant P35 (LEGACY_WRITE)
        tmp.write_text(json.dumps({"version": 1, "values": values}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)  # jamais de fichier à moitié écrit après une coupure
        self._stamp = self._file_stamp()  # notre propre écriture : pas de relecture

    # --- lecture

    def locked(self, key: str) -> bool:
        return bool(ITEMS[key].lock_reason())

    def value(self, key: str) -> Any:
        """Valeur en vigueur : imposée par l'environnement, sinon choisie, sinon par défaut."""
        item = ITEMS[key]
        if item.kind in PANEL_KINDS:
            return self.view(key).get("value")
        default = item.default()
        if item.lock_reason():
            return default
        if item.live:
            get = self._hook(key, "get")
            try:
                return get() if get else default
            except Exception as exc:
                log.warning("réglage %s illisible : %s", key, exc)
                return default
        with self._lock:
            chosen = self._load().get(key)
        if chosen is None:
            return default
        if isinstance(default, dict):
            return {**default, **{k: v for k, v in chosen.items() if k in default}}
        return copy.deepcopy(chosen)

    def pending(self, key: str) -> bool:
        item = ITEMS[key]
        if item.applies == "hot":
            return False
        with self._lock:
            if key not in self._applied:
                return False
            applied = self._applied[key]
        current = self.value(key)
        if isinstance(applied, dict) and isinstance(current, dict):
            # 19/10 (plan P10) : un nom apparu depuis (connecteur ou skill découvert en fond)
            # a été pris avec sa valeur par défaut ; un nom disparu ne compte plus.
            default = item.default()
            return any(v != applied.get(k, default.get(k)) for k, v in current.items())
        return applied != current

    def restart_needed(self) -> bool:
        return any(self.pending(k) for k in ITEMS)

    # --- genres info / action (17/10, plan P4)

    def bind(self, key: str, **hooks: Callable | None) -> None:
        """Branche ce qui dépend du cœur (micro, voix…) : `view`/`run` d'une ligne
        info/action, `get`/`put`/`options` d'un réglage `live`."""
        unknown = set(hooks) - {"view", "run", "get", "put", "options"}
        if unknown:
            raise ValueError(f"crochets inconnus : {sorted(unknown)}")
        with self._lock:
            self._bound[key] = dict(hooks)

    def _hook(self, key: str, name: str) -> Callable | None:
        with self._lock:
            bound = self._bound.get(key, {}).get(name)
        return bound or getattr(ITEMS[key], name)

    def view(self, key: str) -> dict:
        """Vue d'une ligne info/action ; une erreur s'affiche dans la ligne, jamais ne casse la page."""
        fn = self._hook(key, "view")
        if fn is None:
            return {"value": {"lines": [{"label": "État", "text": "indisponible ici", "level": "info"}]}, "actions": []}
        try:
            out = fn() or {}
        except Exception as exc:
            log.exception("vue du réglage %s", key)
            out = {"value": {"lines": [{"label": "Erreur", "text": str(exc) or type(exc).__name__, "level": "error"}]}}
        return {"value": out.get("value") or {"lines": []}, "actions": list(out.get("actions") or [])}

    def action(self, key: str, action: str, cmd: dict | None = None) -> dict:
        """Bouton d'une ligne info/action : seulement un bouton AFFICHÉ et non grisé ; mot tapé
        exigé s'il en demande un. Rend {ok, text?, level?, block?, link?, error?}."""
        cmd = cmd or {}
        item = ITEMS.get(key)
        if item is None or item.kind not in PANEL_KINDS:
            return {"ok": False, "error": f"aucune action pour « {key or '(vide)'} »."}
        reason = item.lock_reason()
        if reason:
            return {"ok": False, "error": reason}
        run = self._hook(key, "run")
        spec = next((a for a in self.view(key)["actions"] if a.get("id") == action), None)
        if run is None or spec is None:
            return {"ok": False, "error": "action indisponible pour l'instant (la page s'est peut-être mise à jour)."}
        if spec.get("disabled"):
            return {"ok": False, "error": spec.get("reason") or "action indisponible."}
        word = str(spec.get("word") or "")
        # 20/10 : en anglais, le HUD montre le mot traduit (« lever » → « lift ») : les deux valent.
        typed = str(cmd.get("confirm") or "").strip().lower()
        if word and typed not in (word.lower(), i18n.tr(word, "en").lower()):
            return {"ok": False, "error": f"rien n'est fait : il faut taper « {word} » pour confirmer."}
        try:
            out = run(action, cmd) or {}
        except Exception as exc:
            log.exception("action %s/%s", key, action)
            return {"ok": False, "error": str(exc) or type(exc).__name__}
        log.info("action %s/%s : %s", key, action, "faite" if out.get("ok", True) else "refusée")
        return {"ok": True, **out} if "ok" not in out else out

    # --- écriture

    def set(self, key: str, raw: Any) -> Any:
        """Valide et retient un choix. Lève `SettingError` (rien n'est changé)."""
        item = ITEMS.get(key)
        if item is None:
            raise SettingError(key, f"réglage inconnu : {key or '(vide)'}.")
        reason = item.lock_reason()
        if reason:
            raise SettingError(key, reason)
        if item.live:
            value = validate(key, raw, self.value(key))
            put = self._hook(key, "put")
            res = put(value) if put else {"ok": False, "error": "réglable quand Astrel tourne."}
            if isinstance(res, dict) and res.get("ok") is False:
                raise SettingError(key, str(res.get("error") or "refusé."))
            log.info("réglage %s = %s", key, json.dumps(value, ensure_ascii=False))
            return value
        with self._lock:
            value = validate(key, raw, self.value(key))
            chosen = self._load()
            if self._unreadable:
                log.warning("réglage %s non enregistré : settings.json illisible (%s)", key, self._unreadable)
                raise SettingError(key, "réglage non enregistré : le fichier settings.json est illisible et n'a "
                                        "pas été écrasé ; corrige-le ou supprime-le, puis recommence.")
            before = copy.deepcopy(chosen)
            if value == item.default():
                chosen.pop(key, None)  # le défaut suit le code et l'environnement
            else:
                chosen[key] = value
            try:
                self._save()
            except OSError as exc:
                self._chosen = before
                raise SettingError(key, f"réglage non enregistré : {exc}") from exc
        log.info("réglage %s = %s", key, json.dumps(value, ensure_ascii=False))
        return value

    def mark_applied(self, applies: str) -> None:
        """Le cerveau (« brain ») ou `mcp.json` (« mcp ») vient de prendre les valeurs
        en vigueur : elles ne sont plus « en attente ». Publie si ça change l'affichage."""
        before = self.restart_needed()
        with self._lock:
            for key, item in ITEMS.items():
                if item.applies == applies:
                    self._applied[key] = copy.deepcopy(self.value(key))
        if before != self.restart_needed() or before:
            self.publish()

    # --- description pour le HUD

    def describe(self) -> dict:
        groups = []
        for gid, title in GROUPS:
            items = []
            for key, item in ITEMS.items():
                if item.group != gid:
                    continue
                reason = item.lock_reason()
                if item.kind in PANEL_KINDS:
                    v = self.view(key)
                    items.append({"key": key, "label": item.label, "help": item.help, "kind": item.kind,
                                  "value": v["value"], "default": None, "applies": "hot", "pending": False,
                                  "locked": bool(reason), "lock_reason": reason, "actions": v["actions"]})
                    continue
                d: dict[str, Any] = {
                    "key": key, "label": item.label, "help": item.help, "kind": item.kind,
                    "value": self.value(key), "default": item.default(), "applies": item.applies,
                    "pending": self.pending(key), "locked": bool(reason), "lock_reason": reason,
                }
                if item.kind in ("int", "float"):
                    d.update(min=item.min, max=item.max, step=item.step, unit=item.unit)
                elif item.kind == "choice":
                    d["choices"] = [{"value": v, "label": lbl} for v, lbl in _choices(item)]
                elif item.kind == "text":
                    d["max"] = item.max
                if item.extra:
                    d.update(item.extra)  # 21/10 (plan P17) : `display` (cartes, pastilles)
                elif item.kind in ("flags", "tri"):
                    d["labels"] = {n: _short(n) for n in d["value"]}
                    # 19/10 (plan P10) : icône locale du service, sinon pastille à lettre.
                    d["icons"] = {n: icones.for_name(n) for n in d["value"]}
                items.append(d)
            for d in items:
                if key_topic := GUIDE_TOPICS.get(d["key"]):
                    d["guide"] = key_topic
            group: dict[str, Any] = {"id": gid, "title": title, "items": items}
            if gid in self._open_groups():
                group["open"] = True
            groups.append(group)
        from .demarrage import first_run
        return {"restart_needed": any(i["pending"] for g in groups for i in g["items"]), "groups": groups,
                "first_run": first_run()}

    def _open_groups(self) -> tuple[str, ...]:
        from .demarrage import first_run
        return ("compte", "demarrage") if first_run() else ("compte",)

    def publish(self) -> dict:
        from .events import bus

        payload = self.describe()
        bus.publish("settings", **payload)
        return payload


reglages = Reglages()


def _republish() -> None:
    reglages.publish()


def _wire() -> None:
    from . import compte as _compte
    from . import demarrage as _demarrage_mod
    from . import maj as _maj
    from . import modeles_locaux as _modeles_locaux
    _compte.compte.on_change = _republish
    _demarrage_mod.demarrage.on_change = _republish
    _maj.maj.on_change = _republish
    _modeles_locaux.local.on_change = _republish  # progression d'un téléchargement (plan P8)
    from . import fournisseurs as _fournisseurs
    _fournisseurs.etat.on_change = _republish  # installation de la passerelle (plan P11)
    from . import claudeai as _claudeai
    _claudeai.connecteurs.on_change = _republish  # connecteurs claude.ai découverts en fond (plan P10)


_wire()
