"""Configuration centrale d'Astrel.

Tout réglage se change par une variable d'environnement `ASTREL_*` (posée dans
`systemd/astrel.service` ou dans le shell). Les secrets vivent dans
`config/secrets.json` (ignoré par Git) ou dans l'environnement — jamais dans le
code.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

from .platform import LABEL as _SYSTEM_LABEL

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
RUNTIME = ROOT / "runtime"
LOGS = ROOT / "logs"
CONFIG_DIR = ROOT / "config"
SECRETS_FILE = CONFIG_DIR / "secrets.json"
HUD_DIR = ROOT / "hud"
# Plan P6 : la venv range Python dans Scripts\python.exe sous Windows.
VENV_PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
# 03/11 : mode UTF-8 de Python pour les programmes d'Astrel lancés sous Windows (sinon cp1252 par défaut).
UTF8_ARGS = ["-X", "utf8"] if sys.platform == "win32" else []


def python_command(script: Path) -> str:
    """Ligne « python script » d'un hook du CLI, lancée par un shell (bash, Git
    Bash sous Windows) : chemins en « / » et cités s'il le faut. Sous Linux, sans
    espace dans les chemins, identique à f"{VENV_PYTHON} {script}" (plan P6)."""
    return " ".join(shlex.quote(Path(p).as_posix()) for p in (VENV_PYTHON, script))


# 23/10 (plan P29, audit sécurité fiche 6, constat 1) : le hook du GARDE-FOU tourne en mode
# ISOLÉ. Avant, `python hook.py` chargeait `sitecustomize.py` et les `.pth` de la venv, le
# site de l'utilisateur, `PYTHON*` de l'environnement, et les `.pyc` de `astrel/__pycache__` :
# un fichier écrit là (libre en autonome) remplaçait le garde-fou dès l'appel suivant.
# -I : ni PYTHON*, ni site de l'utilisateur, ni dossier du script dans sys.path ;
# -S (Linux) : pas de `site` du tout (ni site-packages, ni .pth, ni sitecustomize) ; le hook
# n'importe que la bibliothèque standard et `astrel` (mesuré). Ailleurs, psutil (site-packages)
# sert à `platform/proc.py` : -S casserait le hook. pycache_prefix : les `__pycache__` du code
# sont IGNORÉS (lus comme écrits) au profit de ce dossier, dans `hooks/` (protégé) ; -B le
# rendrait sûr aussi mais recompilerait guard.py à chaque appel (≈ 240 ms contre 60 mesurés).
HOOK_PYCACHE_DIR = ROOT / "hooks" / "__pycache__" / "isole"


def hook_python_args() -> list[str]:
    """Options de l'interpréteur du hook du garde-fou (voir plus haut)."""
    args = ["-I"] + (["-S"] if sys.platform.startswith("linux") else [])
    # 03/11 (Windows) : mode UTF-8 ; sinon la commande à juger, lue sur l'entrée, est décodée en cp1252 et un
    # chemin accentué (« Téléchargements ») ne correspond plus à ce qu'il protège. -I ignore PYTHONUTF8 : option.
    if sys.platform == "win32":
        args += ["-X", "utf8"]
    return args + ["-X", f"pycache_prefix={HOOK_PYCACHE_DIR.as_posix()}"]


def hook_command(script: Path, *args: str, fail_closed: bool = True) -> str:
    """Ligne du hook du GARDE-FOU (`python_command` en mode isolé, + arguments). `fail_closed` :
    `|| exit 2`, car Claude Code prend un code de sortie autre que 0 et 2 pour une erreur NON
    bloquante : un interpréteur absent (venv effacée : 127) laissait passer l'action."""
    line = " ".join(shlex.quote(w) for w in hook_words(script, *args))
    return line + " || exit 2" if fail_closed else line


def hook_words(script: Path, *args: str) -> list[str]:
    """Mots de la ligne du hook du garde-fou (interpréteur isolé, script, arguments)."""
    return [Path(VENV_PYTHON).as_posix(), *hook_python_args(), Path(script).as_posix(), *args]


# Profil de l'utilisateur (plan P6, reste de la vague 2 de docs/PARTAGE.md) :
# prénom et système cités par l'invite système, dossier des projets par défaut.
# `config/profile.json` (personnel, non suivi ; modèle : profile.example.json).
PROFILE_FILE = ROOT / "config" / "profile.json"


def _profile() -> dict:
    try:
        data = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


PROFILE = _profile()
# Sans prénom au profil : « l'utilisateur », et « the user » dans l'invite anglaise (02/11, brain.personalize).
USER_NAME_DEFAULT = "l'utilisateur"
USER_NAME_DEFAULT_EN = "the user"
USER_NAME = str(PROFILE.get("nom") or "").strip() or USER_NAME_DEFAULT
SYSTEM_DESC = str(PROFILE.get("systeme") or "").strip() or _SYSTEM_LABEL
# 23/10 (plan P31, demande de l'utilisateur) : nom AFFICHÉ du produit (HUD, bulle, textes d'écran du cœur),
# « Astrel » par défaut. Valeur par défaut du réglage `assistant_name` (Réglages › Apparence) ;
# `ASTREL_ASSISTANT_NAME` l'impose. Lu partout par `i18n.assistant_name()` (une seule source). Les
# identifiants internes (astrel, ASTREL_*, services, dossiers) et l'alias d'éveil `hey_jarvis` restent.
ASSISTANT_NAME_DEFAULT = "Astrel"
ASSISTANT_NAME = os.environ.get("ASTREL_ASSISTANT_NAME", "").strip() or ASSISTANT_NAME_DEFAULT


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "oui", "on")


def secret(name: str) -> str:
    """Un secret : l'environnement d'abord, puis `config/secrets.json`."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        data = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get(name, "")).strip()


# --- Wake word ---
def oww_model(name: str) -> Path:
    """Chemin d'un modèle livré avec openWakeWord (wake word, VAD Silero)."""
    import openwakeword

    return Path(openwakeword.__file__).parent / "resources" / "models" / name


WAKE_MODEL_NAME = "hey_jarvis_v0.1.onnx"
# Mesuré le 26/09 sur la voix de l'utilisateur (micro USB) : l'ancien mot d'éveil passait 2 fois sur 7
# atteignaient 0,55 deux blocs de suite, les autres plafonnaient vers 0,4 sur
# un seul bloc. 0,4 × 1 bloc en attrape 6 sur 7, quel que soit le gain.
WAKE_THRESHOLD = _env_float("ASTREL_WAKE_THRESHOLD", 0.4)
# Nombre de blocs consécutifs au-dessus du seuil avant de déclencher.
WAKE_PATIENCE = 2
# Délai minimum entre deux réveils, pour éviter les doubles déclenchements.
WAKE_COOLDOWN_S = 2.0
# 25/10 (plan P36, demande de l'utilisateur : « Astrel ») : deux voies, la première qui parle gagne
# (`listener.WakeWord`). Voie TEXTE : le VAD ouvre une capture (pré-roll, durée maximale, fin au
# blanc), un fil à part la transcrit avec un Whisper dédié (`hotwords` = noms), `eveil.trouve` y
# cherche le nom affiché ou une variante (`WAKE_NAMES`, réglage `wake_names`). Écoute continue par
# reconnaissance vocale LOCALE, acceptée par l'utilisateur le 25/10 : rien ne sort de la machine, et
# aucune phrase entendue n'est journalisée (seulement le nom reconnu).
WAKE_TEXT = _env_bool("ASTREL_WAKE_TEXT", True)
WAKE_TEXT_MODEL = os.environ.get("ASTREL_WAKE_TEXT_MODEL", "base")
WAKE_NAMES = os.environ.get("ASTREL_WAKE_NAMES", "astral, astrelle, a strel, hey astrel, ok astrel")
WAKE_TEXT_PREROLL_S = 0.5       # son gardé avant le début de la parole (première syllabe)
WAKE_TEXT_MAX_S = 3.0           # capture la plus longue ; au-delà, on attend la phrase suivante
WAKE_TEXT_END_S = 0.4           # blanc qui clôt la capture
WAKE_TEXT_MIN_S = 0.25          # parole plus courte : un clic, pas un nom
WAKE_TEXT_MAX_AGE_S = 1.5       # résultat arrivé plus tard que ça après la capture : jeté
WAKE_TEXT_GAP_S = 0.3           # trou dans le flux (micro sourd pendant qu'Astrel parle) : capture vidée
WAKE_TEXT_NO_SPEECH = 0.6       # segment Whisper au-dessus : pris pour du bruit (hallucination)
# Alias de transition : le modèle `hey_jarvis` ci-dessus, INCHANGÉ (seuils mesurés). Réglage
# `wake_legacy` : actif chez l'utilisateur pendant l'essai d'« Astrel », coupé sur son accord (décision
# du 23/10 : l'ancien mot en repli).
WAKE_LEGACY = _env_bool("ASTREL_WAKE_LEGACY", True)
# 27/10 (plan P36 lot 6, demande de l'utilisateur : « lance le lot 6 ») : voie MODÈLE, un modèle
# openWakeWord ENTRAÎNÉ pour « Astrel » sur voix de synthèse (`tools/eveil_entrainement/`, venv à part
# `.venv-eveil`), processeur seul. Voie ADDITIONNELLE, ÉTEINTE par défaut tant que le banc
# (`tools/banc_eveil.py --voie modele|ou|confirme`) ne prouve pas qu'elle fait mieux. `ASTREL_WAKE_MODEL` :
#   off      : rien (défaut) ;
#   ou       : un réveil du modèle suffit, en plus des autres voies ;
#   confirme : la voie texte ne réveille que si le modèle a aussi entendu le nom (score ≥
#              WAKE_MODEL_CONFIRM_THRESHOLD dans les WAKE_MODEL_CONFIRM_S dernières secondes).
# Fichier absent : voie coupée, une ligne au journal. Les négatifs ACAV100M de l'entraînement sont
# sous licence CC BY-NC-SA 4.0 (`models/eveil/astrel.json`) : modèle à usage NON commercial.
WAKE_MODEL_MODE = os.environ.get("ASTREL_WAKE_MODEL", "off").strip().lower()
WAKE_MODEL_FILE = Path(os.environ.get("ASTREL_WAKE_MODEL_FILE", "") or MODELS / "eveil" / "astrel.onnx")
WAKE_MODEL_THRESHOLD = _env_float("ASTREL_WAKE_MODEL_THRESHOLD", 0.5)
WAKE_MODEL_PATIENCE = 2
WAKE_MODEL_CONFIRM_THRESHOLD = _env_float("ASTREL_WAKE_MODEL_CONFIRM_THRESHOLD", 0.15)
WAKE_MODEL_CONFIRM_S = 5.0

# --- Audio ---
# Source PipeWire ; vide = source par défaut. `./run.sh --mics` pour les noms.
# Le micro choisi depuis le HUD ou `--send "mic <nom>"` est retenu dans
# MIC_FILE ; ASTREL_MIC_SOURCE reste prioritaire.
MIC_FILE = RUNTIME / "mic.txt"


def _saved_mic() -> str:
    try:
        return MIC_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


MIC_SOURCE = os.environ.get("ASTREL_MIC_SOURCE") or _saved_mic()
# Gain logiciel. Mesuré le 26/09 sur le micro USB : ×4 fait saturer la voix de
# l'utilisateur de près et fait rater le mot de réveil ; ×2 le garde net. Re-mesurer.
MIC_GAIN = _env_float("ASTREL_MIC_GAIN", 2.0)
SAMPLE_RATE = 16000
# openWakeWord attend des blocs de 1280 échantillons (80 ms à 16 kHz).
FRAME_SAMPLES = 1280
FRAME_BYTES = FRAME_SAMPLES * 2
# Queue de son après la fin de la parole (latence PipeWire + haut-parleur) :
# le micro reste sourd encore ce temps-là, sinon Astrel se réentend.
ECHO_TAIL_S = 0.5
# Silence joué en tête de chaque ouverture du lecteur : une enceinte Bluetooth
# (la JBL, 26/09) se réveille en avalant le début du son, donc le premier mot.
OUTPUT_LEAD_S = float(os.environ.get("ASTREL_OUTPUT_LEAD_S", "0.4"))
# Retard estimé entre l'écriture d'un échantillon et sa sortie (PipeWire, et
# surtout une enceinte Bluetooth) : la pause de la voix recule d'autant avant de
# chercher le début du mot à redire, pour ne rien sauter à la reprise.
OUTPUT_LATENCY_S = float(os.environ.get("ASTREL_OUTPUT_LATENCY_S", "0.25"))
# Pause de la voix (bouton ⏸ du HUD, Ctrl+Alt+P, `--send "pause"`), retenue
# d'un démarrage à l'autre : une relance pendant l'absence de l'utilisateur ne doit
# pas faire parler Astrel dans une pièce vide.
PAUSE_FILE = RUNTIME / "voice-paused"

# --- Détection de fin de phrase (VAD Silero) ---
VAD_MODEL_NAME = "silero_vad.onnx"
# 0,5 coupait la voix de l'utilisateur en deux (probabilités entre 0,30 et 0,92).
VAD_SPEECH_THRESHOLD = _env_float("ASTREL_VAD_THRESHOLD", 0.35)
# Fin de phrase (30/09, l'utilisateur « coupé » en pleine réflexion, voir CLAUDE.md) :
# - un bloc ne compte comme SILENCE que sous (seuil − VAD_END_MARGIN), comme le
#   fait Silero lui-même (VADIterator, get_speech_timestamps : seuil − 0,15).
#   Avant, un seul seuil servait aux deux : une syllabe à 0,30 en pleine phrase
#   (seuil 0,39) comptait déjà comme du blanc ;
# - blanc toléré : VAD_SILENCE_S pour une phrase courte (« vas-y » reste
#   rapide), VAD_SILENCE_LONG_S dès que l'utilisateur parle depuis plus de
#   VAD_LONG_AFTER_S : quand il explique, il s'arrête pour réfléchir. 1,0 s pour
#   tout coupait une pause de 0,9 s (mesuré sur voix de synthèse + bruit).
VAD_END_MARGIN = _env_float("ASTREL_VAD_END_MARGIN", 0.15)
VAD_SILENCE_S = _env_float("ASTREL_VAD_SILENCE_S", 1.2)
VAD_SILENCE_LONG_S = _env_float("ASTREL_VAD_SILENCE_LONG_S", 2.0)
VAD_LONG_AFTER_S = _env_float("ASTREL_VAD_LONG_AFTER_S", 3.0)
VAD_INITIAL_TIMEOUT_S = 9.0
# Durée maximale d'une phrase, comptée depuis le DÉBUT DE LA PAROLE. C'était
# 45 s comptées depuis l'ouverture du micro, attente comprise : les longues
# explications de l'utilisateur (113 et 128 mots, 30/09 matin) étaient coupées net,
# d'où « j'ai un temps limité pour te parler ». Ce n'est plus qu'un garde-fou
# contre un bruit continu (télé) qui tiendrait l'écoute ouverte.
VAD_MAX_UTTERANCE_S = _env_float("ASTREL_VAD_MAX_UTTERANCE_S", 300.0)

# --- Reconnaissance vocale ---
STT_MODEL = os.environ.get("ASTREL_STT_MODEL", "small")
STT_DEVICE = os.environ.get("ASTREL_STT_DEVICE", "cuda")
STT_COMPUTE = os.environ.get("ASTREL_STT_COMPUTE", "float16")
STT_LANGUAGE = "fr"
# Lexique soufflé à Whisper : sans lui, un nom de projet peu courant est mal transcrit. Les noms
# des projets du dossier de travail y sont ajoutés automatiquement.
STT_HINTS = os.environ.get(
    "ASTREL_STT_HINTS",
    "Astrel, Claude, Claude Code, GitHub, commit, npm, Python, TypeScript, Raspberry Pi",
)

# --- Synthèse vocale ---
# Moteur : « gemini[:voix] » (Charon par défaut, en ligne), « edge[:voix] »
# (Microsoft, en ligne, sans clé), « piper[:nom] » (local, secours). Si le moteur en ligne échoue, repli automatique sur
# TTS_FALLBACK : la voix ne doit jamais se taire pour une panne réseau.
# La voix choisie depuis le HUD ou `--send "voice <spec>"` est retenue dans
# VOICE_FILE et survit aux relances ; ASTREL_TTS_ENGINE reste prioritaire.
VOICE_FILE = RUNTIME / "voice.txt"


def _saved_voice() -> str:
    try:
        return VOICE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


TTS_ENGINE = os.environ.get("ASTREL_TTS_ENGINE") or _saved_voice() or "edge:fr-FR-RemyMultilingualNeural"
TTS_FALLBACK = os.environ.get("ASTREL_TTS_FALLBACK", "piper:fr_FR-upmc-medium")
TTS_SPEED = _env_float("ASTREL_TTS_SPEED", 1.0)
# 02/11 (licence) : Piper (GPL-3.0) est un PROGRAMME SÉPARÉ, lancé en sous-processus et jamais importé.
# Vide = le `piper` installé à côté du Python d'Astrel (paquet piper-tts), sinon celui du PATH.
PIPER_CMD = os.environ.get("ASTREL_PIPER_CMD", "")
# 02/11 (demande de l'utilisateur) : conversation en ANGLAIS (réglage `conversation_language`, `astrel/langue.py`).
# Sa voix est retenue à part (VOICE_EN_FILE) ; par défaut Andrew, voix américaine NON multilingue : une voix
# multilingue change de langue pour toute la phrase sur un mot étranger (Rémy, 26/09), et un nom de
# projet français ferait basculer Andrew. Secours local : Piper « joe » (jeu de données CC0, affiné depuis la voix
# « lessac », données sous licence de recherche non commerciale : voir NOTICE).
CONVERSATION_LANGUAGE = "fr"
VOICE_EN_FILE = RUNTIME / "voice-en.txt"
TTS_ENGINE_EN_DEFAULT = "edge:en-US-AndrewNeural"
TTS_FALLBACK_EN = os.environ.get("ASTREL_TTS_FALLBACK_EN", "piper:en_US-joe-medium")
GEMINI_TTS_MODEL = os.environ.get("ASTREL_GEMINI_TTS_MODEL", "gemini-3.8-flash-tts")
# Consigne de diction envoyée avec chaque phrase (style Gemini).
GEMINI_TTS_STYLE = os.environ.get(
    "ASTREL_GEMINI_TTS_STYLE",
    "calme, posé, légèrement chaleureux, comme un majordome britannique qui parle français",
)

# --- Cerveau (Claude Code en mode headless, sur l'abonnement) ---
def _claude_bin() -> str:
    """03/11 (CI Windows) : sous Windows, `claude` est un claude.exe ou un claude.cmd (npm) ; sans son chemin
    complet, Popen ne trouve pas un .cmd (« fichier introuvable » au lancement du cerveau)."""
    choisi = os.environ.get("ASTREL_CLAUDE_BIN")
    if choisi:
        return choisi
    if sys.platform == "win32":
        import shutil
        return shutil.which("claude") or "claude"
    return "claude"


CLAUDE_BIN = _claude_bin()
# Dossier des projets : ASTREL_WORKDIR (posé par le service), sinon le profil,
# sinon ~/Projets (même défaut que `install.py --projets`).
WORKDIR = Path(os.environ.get("ASTREL_WORKDIR") or str(PROFILE.get("projets") or "").strip()
               or str(Path.home() / "Projets")).expanduser()
# Modèle principal du cerveau : alias du CLI (jamais d'identifiant daté, l'alias
# suit la dernière version). Le choix du HUD ou de `--send "model <alias>"` est
# retenu dans MODEL_FILE ; ASTREL_CLAUDE_MODEL reste prioritaire. Sonnet par
# défaut : Opus se choisit à la main pour une discussion ardue, car changer de
# modèle fait relire toute la conversation sans cache.
CLAUDE_MODELS = {
    "sonnet": ("Sonnet", "le travail courant, bon rapport qualité-prix"),
    "opus": ("Opus", "le plus fort, pour les discussions ardues ; coûte plus"),
    "haiku": ("Haiku", "rapide et léger, pour les demandes simples"),
}
MODEL_FILE = RUNTIME / "model.txt"


def _saved_model() -> str:
    try:
        saved = MODEL_FILE.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""
    # Un fichier abîmé ne doit pas empêcher le cerveau de démarrer.
    return saved if saved in CLAUDE_MODELS else ""


CLAUDE_MODEL = os.environ.get("ASTREL_CLAUDE_MODEL") or _saved_model() or "sonnet"
# Plugins de Claude Code à couper pour Astrel seul (ex. « caveman » réécrirait
# chaque réponse en style télégraphique). N'affecte pas les autres sessions.
CLAUDE_DISABLED_PLUGINS = tuple(
    p for p in os.environ.get("ASTREL_DISABLED_PLUGINS", "caveman@synced").split(",") if p
)
# Connecteurs claude.ai jamais connectés pour Astrel (clé `deniedMcpServers` des
# réglages passés au CLI ; sous-agents compris, ils partagent ses serveurs MCP).
# Chacun ajoute ses noms d'outils à CHAQUE tour (Vercel : 236 outils). Mesuré le
# 15/10 : 373 outils claude.ai différés → 45, ≈ 2 000 tokens chargés en moins
# plus la liste des 328 noms retirés. Gardés (demande de l'utilisateur) : Gmail,
# Google Drive, Dropbox. Noms exacts de `claude mcp list` ; pour réactiver un
# connecteur, l'ôter d'ici (ou ASTREL_DENIED_MCP_SERVERS="" pour tous) et
# relancer Astrel. Voir docs/ECONOMIE-TOKENS.md.
CLAUDE_DENIED_MCP_SERVERS = tuple(
    s.strip() for s in os.environ.get(
        "ASTREL_DENIED_MCP_SERVERS",
        "claude.ai Vercel,claude.ai Figma,claude.ai Canva,claude.ai Claude Docs",
    ).split(",") if s.strip()
)
# Skills listées au modèle, pour Astrel seul (clé `skillOverrides` des réglages
# passés au CLI, sous-agents compris ; schéma lu dans le CLI 2.1.285, valeurs
# « on », « name-only » = nom sans description, « user-invocable-only », « off »).
# Sans effet sur les skills de plugins. Mesuré le 15/10 (get_context_usage, sonnet) :
# la liste des skills pesait 7 311 tokens par contexte, dont 2 074 pour les 11
# founder-* (servies le 13/10 aux faux clients Boutique : gardées par leur NOM).
# « off » : ce qui ne peut pas marcher dans le CLI d'Astrel (outils de l'appli
# de bureau, connecteur Claude Docs bloqué, brief claude.ai, raccourcis, invites
# de permission). Gardées entières : docx, pdf, xlsx, pptx, google-workspace,
# claude-api, code-review, deep-research, run, simplify… Pour réactiver une skill,
# l'ôter d'ici ; ASTREL_SKILL_OVERRIDES="nom:état,…" remplace toute la liste
# ("" = aucune). Voir docs/ECONOMIE-TOKENS.md § 9.
_SKILL_OVERRIDES_DEFAULT = ",".join(
    [f"founder-{n}:name-only" for n in (
        "board", "brand", "cfo", "competitors", "consumer", "launch", "marketing",
        "offer", "ops", "plan", "pricing")]
    + [f"{n}:off" for n in (
        "built-in-browser", "chrome-browser", "computer-use", "docs", "morning",
        "import-memory", "keybindings-help", "fewer-permission-prompts")]
    + [f"{n}:name-only" for n in (
        "dataviz", "update-config", "schedule", "loop", "workflow-authoring", "skill-creator")]
)
SKILL_STATES = ("on", "name-only", "user-invocable-only", "off")


def _skill_overrides(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in raw.split(","):
        name, _, state = item.strip().rpartition(":")
        if name and state in SKILL_STATES:
            out[name.strip()] = state
    return out


CLAUDE_SKILL_OVERRIDES = _skill_overrides(os.environ.get("ASTREL_SKILL_OVERRIDES", _SKILL_OVERRIDES_DEFAULT))
# Effort de réflexion du cerveau vocal SEUL (les réglages de l'utilisateur sont
# à « high », trop coûteux pour de la conversation : le code lourd part de toute
# façon dans des sous-agents). Vide = laisser le réglage du CLI.
CLAUDE_EFFORT = os.environ.get("ASTREL_CLAUDE_EFFORT", "high")
BRAIN_RESTART_DELAY_S = 2.0
# 04/11 (carte T46, `astrel/cycle_cerveau.py`) : cerveau tombé = relance avec recul ; le premier palier est
# BRAIN_RESTART_DELAY_S. Un process qui a vécu RELANCE_STABLE_S repart au premier palier. Messages arrivés pendant la
# relance : gardés RELANCE_GARDE_S ou RELANCE_GARDE_AUTO_S (réflexes, équipe…), puis « non envoyé ».
BRAIN_RESTART_BACKOFF_S = (5.0, 15.0, 60.0)
RELANCE_STABLE_S = 600.0
RELANCE_GARDE_S = 60.0
RELANCE_GARDE_AUTO_S = 600.0
# Claude Code déconnecté : `claude auth status` relu toutes les AUTH_SONDE_S (jamais .credentials.json).
AUTH_SONDE_S = 30.0
# Connecté selon `auth status` mais toujours refusé : une relance au plus toutes les AUTH_ESSAI_S.
AUTH_ESSAI_S = 300.0
# Une tâche qui ne produit plus rien pendant ce temps est abandonnée.
BRAIN_TURN_TIMEOUT_S = 900.0

# --- Confirmation des actions sensibles ---
CONFIRM_SOCKET = RUNTIME / "confirm.sock"
# Durée maximale d'un échange complet vu du hook : fin de la phrase en cours,
# question, deux essais d'écoute, attente du HUD. Au-delà, le hook REFUSE.
# 14/10 (demande de l'utilisateur : une confirmation reste jusqu'à sa réponse
# explicite) : 100 s → 10 min. La question reste à l'écran (HUD, bulle) après
# les deux essais à l'oral, et « oui » / « non » dits plus tard comptent encore.
# 15/10 (demande : sans réponse, ne PAS compter ça comme un refus, attendre qu'il réponde) :
# 10 min → 12 h. Une question reste à l'écran jusqu'à sa décision ; le délai n'est plus
# qu'un filet de sécurité contre un hook oublié.
CONFIRM_REQUEST_TIMEOUT_S = _env_float("ASTREL_CONFIRM_REQUEST_TIMEOUT_S", 43200.0)
# Attente d'un clic (HUD, bulle) ou d'un « oui » dit plus tard, une fois les
# essais à l'oral passés ; marge laissée au hook pour recevoir la réponse.
CONFIRM_TIMEOUT_S = max(5.0, CONFIRM_REQUEST_TIMEOUT_S - 60.0)
# Mode « Autonome TOTAL » (15/10) : gardé EN MÉMOIRE seulement ; ce journal en
# ajout seul liste ce qui est passé sans question grâce à lui.
TOTAL_LOG = LOGS / "autonome-total.log"
# Levée du garde-fou (18/10, plan P7) : en mémoire, éteinte au démarrage ; ce journal en
# ajout seul (0600) note son activation, sa coupure et ce qui est passé grâce à elle.
LIFT_LOG = LOGS / "garde-fou-leve.log"
# Journal d'audit unifié (`audit.py`) : protégé en écriture par le garde-fou depuis le 18/10.
AUDIT_LOG = LOGS / "audit.jsonl"
SETTINGS_FILE = RUNTIME / "claude-settings.json"
MCP_CONFIG_FILE = RUNTIME / "mcp.json"
# Navigateur piloté par Claude (serveur MCP Playwright, version fixée dans
# mcp-servers/). Profil DÉDIÉ : il ne touche jamais au Chrome de l'utilisateur ;
# il peut s'y connecter une fois à ses sites, la session est gardée.
BROWSER_ENABLED = _env_bool("ASTREL_BROWSER", True)
BROWSER_HEADLESS = _env_bool("ASTREL_BROWSER_HEADLESS", False)
BROWSER_PROFILE = RUNTIME / "browser-profile"
# Navigateurs de test en plus (`playwright2`, `playwright3`…), profil en mémoire.
# Les sous-agents partagent les serveurs MCP du cerveau : avec un seul
# navigateur, un agent fermait la page de test d'un autre (constaté le 26/09).
BROWSER_EXTRA = int(os.environ.get("ASTREL_BROWSER_EXTRA", "3"))
HOOK_SCRIPT = ROOT / "hooks" / "permission_hook.py"
# Hook de ROUTAGE des sous-agents (modèle choisi d'office, `astrel/agents.py`),
# à côté du garde-fou, jamais à sa place.
ROUTE_HOOK_SCRIPT = ROOT / "astrel" / "route_hook.py"
# rtk (plan P12, lot 2, essai) : compacte la sortie de quelques commandes Bash simples
# (`astrel/rtk_hook.py`, à côté du garde-fou, sans décision). Hook déclaré seulement si
# le binaire est là ; coupé par « off » dans RTK_SWITCH_FILE, sans relance.
RTK_HOOK_SCRIPT = ROOT / "astrel" / "rtk_hook.py"
RTK_BIN = Path(os.environ.get("ASTREL_RTK_BIN") or ROOT / ".rtk" / "rtk")
RTK_SWITCH_FILE = RUNTIME / "rtk.txt"
# Hook Stop « annonce sans action » (`hooks/stop_hook.py`, 15/10) : relance UNE fois (2 au plus)
# un tour de l'utilisateur fini sur « je vais maintenant… » sans action.
STOP_HOOK_SCRIPT = ROOT / "hooks" / "stop_hook.py"
# Rappel automatique de la mémoire à chaque message (idée 5, docs/RAPPEL-MEMOIRE.md).
MEMORY_HOOK_SCRIPT = ROOT / "hooks" / "memory_hook.py"
# 01/11 : suivi des outils (`hooks/suivi_outils.py`) : instantané avant écriture (`instantanes.py`,
# « annule ce que tu viens de faire ») et indice d'échec Debian. Sans décision, à côté du garde-fou.
SUIVI_HOOK_SCRIPT = ROOT / "hooks" / "suivi_outils.py"
SUIVI_MATCHER = "Write|Edit|MultiEdit|NotebookEdit|Bash"

# --- Économie de tokens (docs/ECONOMIE-TOKENS.md) ---
# Arrêt d'urgence : tant que ce fichier existe, ni réflexe, ni relève, ni
# nouvel agent, et un tour lancé seul par le CLI est interrompu. Posé et levé
# par l'utilisateur (HUD « ⛔ Stop tout », `--send estop` / `--send "estop off"`).
ESTOP_FILE = RUNTIME / "ESTOP"
# Mission autonome bornée (`astrel/mission.py`) : objectif, critère de fin,
# interdits, budget. Lancée et arrêtée par l'utilisateur seul (HUD, CLI), protégée
# par le garde-fou comme le mode : Claude ne peut ni la lancer ni l'élargir.
MISSION_FILE = RUNTIME / "mission.json"
# 03/11 : prompt système du cerveau, passé au CLI par fichier (cmd.exe de Windows : ligne ≤ 8 191 caractères).
PROMPT_FILE = RUNTIME / "prompt-systeme.txt"
MISSION_MAX_TURNS = int(_env_float("ASTREL_MISSION_MAX_TURNS", 20))
MISSION_MAX_MINUTES = _env_float("ASTREL_MISSION_MAX_MINUTES", 60.0)
MISSION_MAX_TOKENS = int(_env_float("ASTREL_MISSION_MAX_TOKENS", 3_000_000))
MISSION_MAX_ATTEMPTS = int(_env_float("ASTREL_MISSION_MAX_ATTEMPTS", 3))
# 02/11 (plan P43) : « FINIE » démentie par la vérification au plus N fois, puis la mission s'arrête.
MISSION_MAX_VERIFS = int(_env_float("ASTREL_MISSION_MAX_VERIFS", 3))
# La mission s'arrête d'elle-même si la fenêtre de 5 h de l'abonnement dépasse ce niveau.
MISSION_QUOTA_STOP = _env_float("ASTREL_MISSION_QUOTA_STOP", 0.90)
# Relève automatique : quand le contexte du cerveau dépasse ce nombre de tokens
# ET que tout est calme depuis RELIEF_IDLE_S, Astrel fait mettre à jour la note
# CHANTIERS-EN-COURS puis repart d'une session neuve. 0 = jamais.
CONTEXT_RELIEF_TOKENS = int(_env_float("ASTREL_CONTEXT_RELIEF_TOKENS", 150_000))
RELIEF_IDLE_S = _env_float("ASTREL_RELIEF_IDLE_S", 120.0)
# Délai maximal du tour de sauvegarde avant d'abandonner la relève.
RELIEF_SAVE_TIMEOUT_S = _env_float("ASTREL_RELIEF_SAVE_TIMEOUT_S", 600.0)
# Après une relève abandonnée (l'utilisateur a parlé, agent lancé…), on réessaie après ce délai.
RELIEF_RETRY_S = _env_float("ASTREL_RELIEF_RETRY_S", 900.0)
# Sauvegarde finie : attente maximale d'un instant calme (bruit capté, Astrel qui parle) avant
# d'abandonner la relève (19/10 : un bruit à 2 s de la fin l'annulait pour 15 min).
RELIEF_CALM_WAIT_S = _env_float("ASTREL_RELIEF_CALM_WAIT_S", 120.0)
RELIEF_AMORCE_FILE = RUNTIME / "relief-amorce"
# Démarrage d'Astrel (19/10, demande de l'utilisateur) : si la conversation à reprendre dépasse ce
# contexte, session neuve avec l'amorce de la relève au lieu de `--resume`. 0 = toujours reprendre.
BOOT_FRESH_TOKENS = int(_env_float("ASTREL_BOOT_FRESH_TOKENS", 80_000))
# Anti-boucle : au plus une session neuve de démarrage par ce délai (crash en boucle).
BOOT_FRESH_COOLDOWN_S = _env_float("ASTREL_BOOT_FRESH_COOLDOWN_S", 600.0)
BOOT_FRESH_FILE = RUNTIME / "boot-fresh.json"
# Relève IMMÉDIATE (19/10, plan P9, demande de l'utilisateur) : agents et tâches de fond ne la
# retardent plus ; ils sont arrêtés, notés dans RELIEF_REPRISE_FILE, et relancés dans la
# session neuve. Faux : ancien comportement (attendre RELIEF_IDLE_S de calme sans agent).
RELIEF_IMMEDIATE = _env_bool("ASTREL_RELIEF_IMMEDIATE", True)
# Attente maximale, par agent, qu'une écriture de fichier en cours (Write, Edit…) finisse avant l'arrêt.
RELIEF_STOP_WAIT_S = _env_float("ASTREL_RELIEF_STOP_WAIT_S", 20.0)
# Attente maximale que toutes les tâches arrêtées aient disparu ; au-delà, la sauvegarde part
# quand même (la session neuve coupera les retardataires, déjà notées dans la liste).
RELIEF_STOP_MAX_S = _env_float("ASTREL_RELIEF_STOP_MAX_S", 60.0)
# Liste des agents interrompus par la relève (fait foi) : effacée quand l'amorce est lue.
RELIEF_REPRISE_FILE = RUNTIME / "relief-reprise.json"
# Un même agent (même consigne) n'est pas relancé plus de ce nombre de fois de suite par la relève.
RELIEF_MAX_RELAUNCH = int(_env_float("ASTREL_RELIEF_MAX_RELAUNCH", 2))
# Taille visée de la note CHANTIERS-EN-COURS (relue à chaque session neuve) :
# au-delà, la sauvegarde et l'amorce demandent de la resserrer (rien n'est coupé).
RELIEF_NOTE_MAX_BYTES = int(_env_float("ASTREL_RELIEF_NOTE_MAX_BYTES", 8_000))
# Alerte « agent qui s'emballe » (lue par le cœur, idée 10, ALERTE seule) : tokens
# cumulés d'une tâche de fond ou d'un sous-agent, et durée en secondes.
# 04/11 (T50) : tokens de TRAVAIL (entrée hors cache + création de cache + sortie), plus le total relu :
# sur 14 jours, 21 % des sous-agents passaient 5 M à cause du seul cache relu ; 1 M de travail = 1,6 %.
AGENT_ALERT_TOKENS = int(_env_float("ASTREL_AGENT_ALERT_TOKENS", 1_000_000))
AGENT_ALERT_S = _env_float("ASTREL_AGENT_ALERT_S", 3600.0)
# --- Fiabilité (01/11, idée « gardée pour plus tard » n° 5) : ALERTES seules, rien n'est arrêté ---
# Enfants du cœur (cerveau, passerelle, worker images/vidéo) tués avec lui (PR_SET_PDEATHSIG, Linux).
PDEATHSIG = _env_bool("ASTREL_PDEATHSIG", True)
# Messages de l'utilisateur reçus mais pas encore traités : retrouvés au démarrage suivant (`attente.py`).
ATTENTE_FILE = RUNTIME / "messages-en-attente.json"
ATTENTE_MAX_AGE_S = _env_float("ASTREL_ATTENTE_MAX_AGE_S", 24 * 3600.0)
# Boucles figées (`gel.py`) : délai sans battement avant l'alerte, par boucle ; une conversation
# (écoute de 300 s au plus, transcription, réponse dite) a son propre délai, compté depuis le
# dernier battement. `ASTREL_GEL=0` coupe la surveillance.
GEL_ON = _env_bool("ASTREL_GEL", True)
GEL_COEUR_S = _env_float("ASTREL_GEL_COEUR_S", 120.0)
GEL_CERVEAU_S = _env_float("ASTREL_GEL_CERVEAU_S", 180.0)
GEL_TACHES_S = _env_float("ASTREL_GEL_TACHES_S", 300.0)
GEL_CONVERSATION_S = _env_float("ASTREL_GEL_CONVERSATION_S", 900.0)
GEL_PERIODE_S = 5.0
# Filet si même le chien de garde ne tourne plus (GIL pris) : piles de tous les fils dans ce
# journal (faulthandler), sans rien arrêter.
GEL_LOG = LOGS / "gel.log"
GEL_GIL_S = _env_float("ASTREL_GEL_GIL_S", 600.0)
# Tâche de fond muette (sous-agent sans aucun évènement) : alerte au-delà de ce délai, plus long
# quand un outil de l'agent est en cours (une longue commande est normale).
TACHE_MUETTE_S = _env_float("ASTREL_TACHE_MUETTE_S", 600.0)
TACHE_MUETTE_OUTIL_S = _env_float("ASTREL_TACHE_MUETTE_OUTIL_S", 1800.0)
# Remplissage de la fenêtre de 5 h (en %) à partir duquel le cœur fait écrire le
# point de reprise des chantiers (`Astrel._guard_quota`).
QUOTA_ALERT_PCT = _env_float("ASTREL_QUOTA_ALERT_PCT", 85.0)
# Réglages choisis dans la page « Réglages » du HUD (`astrel/settings.py`, 15/10).
# Protégé par le garde-fou ; une variable ASTREL_* posée prime toujours.
ASTREL_SETTINGS_FILE = RUNTIME / "settings.json"
# Premier lancement (17/10, plan P4) : marqueur posé par « J'ai fini » dans la page
# « Réglages » ou par « Terminé » du guide (18/10, plan P5 : seul ce fichier compte).
FIRST_RUN_FILE = RUNTIME / "first-run-done"
# Guide de démarrage interactif (18/10, plan P5) : source unique en Markdown, progression.
GUIDE_FILE = ROOT / "docs" / "GUIDE-DEMARRAGE.md"
GUIDE_STATE_FILE = RUNTIME / "guide.json"
# Export de la mémoire depuis la page « Réglages » (17/10, plan P4) : un zip du coffre ici.
EXPORT_DIR = Path(os.environ.get("ASTREL_EXPORT_DIR") or Path.home() / "Astrel-export")
# Mode plan (`astrel/plans.py`, 15/10) : plans déposés par le planificateur
# (P<n>.json + P<n>.md), relus et validés par l'utilisateur dans la page « Plans » du
# HUD. « manual » (défaut) : rien ne part avant sa validation ; « auto » : validé
# dès le dépôt. Réglable à chaud (page « Réglages », clé `plan_mode`).
PLANS_DIR = RUNTIME / "plans"
PLAN_MODE = os.environ.get("ASTREL_PLAN_MODE", "manual").strip().lower()
if PLAN_MODE not in ("manual", "auto"):
    PLAN_MODE = "manual"
# Rappel automatique de la mémoire (`astrel/rappel.py`, hook UserPromptSubmit) :
# « extraits » (noms + 2 extraits courts, défaut, plan P3), « noms » (noms seuls,
# comme au 15/10), « off ». Lu par le hook à chaque message (page « Réglages », clé `rappel`).
RAPPEL_MODE = os.environ.get("ASTREL_RAPPEL", "extraits").strip().lower()
if RAPPEL_MODE not in ("off", "noms", "extraits"):
    RAPPEL_MODE = "extraits"
# Reprise après coupure (`astrel/reprise.py`, idée 6) : état de la limite atteinte
# (heure de réinitialisation, reprise à lancer, réflexes gelés), délai du réflexe
# de reprise après la réinitialisation, et rejeu d'un tour sur panne passagère.
REPRISE_FILE = RUNTIME / "reprise.json"
REPRISE_DELAY_S = _env_float("ASTREL_REPRISE_DELAY_S", 60.0)
# Offre de modèle (19/10, plan P8) : remplissage de la fenêtre (en %) à partir duquel
# Astrel PROPOSE un modèle plus économe, sur une carte ; rien n'est changé sans clic.
QUOTA_OFFER_PCT = _env_float("ASTREL_QUOTA_OFFER_PCT", 80.0)
QUOTA_OFFER = os.environ.get("ASTREL_QUOTA_OFFER", "propose").strip().lower()
if QUOTA_OFFER not in ("propose", "off"):
    QUOTA_OFFER = "propose"
# Reprise automatique à l'heure de réinitialisation (réglage `reprise_auto`, oui par défaut).
REPRISE_AUTO = _env_bool("ASTREL_REPRISE_AUTO", True)
# Initiatives d'Astrel (24/10, plan P32, `astrel/initiatives.py`) : il PROPOSE (brief du matin,
# quota, échéances, courrier), jamais sans accord ; réglages du groupe « Initiatives ».
INITIATIVES = _env_bool("ASTREL_INITIATIVES", True)
INITIATIVES_BRIEF = _env_bool("ASTREL_INITIATIVES_BRIEF", True)
INITIATIVES_QUOTA = _env_bool("ASTREL_INITIATIVES_QUOTA", True)
INITIATIVES_ECHEANCES = _env_bool("ASTREL_INITIATIVES_ECHEANCES", True)
INITIATIVES_COURRIER = _env_bool("ASTREL_INITIATIVES_COURRIER", False)  # lot 4 (Gmail) : OFF
INITIATIVES_MAX_JOUR = max(1, min(12, int(_env_float("ASTREL_INITIATIVES_MAX_JOUR", 4))))
INITIATIVES_CALME = os.environ.get("ASTREL_INITIATIVES_CALME", "22-7").strip().lower()
if INITIATIVES_CALME not in ("22-7", "21-8", "23-6", "off"):
    INITIATIVES_CALME = "22-7"
# Relevé du quota hors des tours (22/10, `astrel/quota_releve.py`, réglage `quota_refresh`).
QUOTA_REFRESH = _env_bool("ASTREL_QUOTA_REFRESH", True)
# Arrêt automatique des agents sur quota (24/10, plan P34, `astrel/quota_arret.py`) : fenêtre de 5 h
# à QUOTA_NOTE_PCT → les sous-agents notent leur état ; à QUOTA_STOP_PCT → arrêtés proprement (mode
# « arret »), relance proposée à la levée. Réglages `quota_arret` (off/note/arret), seuils, à chaud.
QUOTA_ARRET = os.environ.get("ASTREL_QUOTA_ARRET", "arret").strip().lower()
if QUOTA_ARRET not in ("off", "note", "arret"):
    QUOTA_ARRET = "arret"
QUOTA_NOTE_PCT = _env_float("ASTREL_QUOTA_NOTE_PCT", 95.0)
QUOTA_STOP_PCT = _env_float("ASTREL_QUOTA_STOP_PCT", 98.0)
QUOTA_ACK_WAIT_S = _env_float("ASTREL_QUOTA_ACK_WAIT_S", 60.0)  # attente de l'accusé (passation) d'un agent
# 26/10 (plan P39) : délai de grâce d'un agent sans accusé mais OCCUPÉ (Bash… en cours) à la fin de
# l'attente : arrêté au plus tard à QUOTA_ACK_WAIT_S + QUOTA_GRACE_S, jamais au-delà de QUOTA_GRACE_PCT
# (fenêtre de 5 h lue à ce niveau ou plus : arrêt aussitôt) ni sur un relevé périmé.
QUOTA_GRACE_S = _env_float("ASTREL_QUOTA_GRACE_S", 180.0)
QUOTA_GRACE_PCT = _env_float("ASTREL_QUOTA_GRACE_PCT", 99.0)
QUOTA_ARRET_FILE = RUNTIME / "quota-arret.json"   # phase, agents arrêtés, reprise (fait foi)
QUOTA_ORDRE_FILE = RUNTIME / "quota-ordre.json"   # ordre lu par le hook (absent : rien à dire)
QUOTA_ORDRE_DIR = RUNTIME / "quota-ordre.d"       # un marqueur par agent et par phase
QUOTA_HOOK_SCRIPT = ROOT / "astrel" / "quota_hook.py"
# context7 (plan P12, lot 4) : documentation à jour des bibliothèques, serveur MCP HTTP sans clé,
# pour l'agent `codeur` SEUL (`agents.write_agents`), jamais dans runtime/mcp.json ni pour Astrel.
CONTEXT7 = _env_bool("ASTREL_CONTEXT7", True)
CONTEXT7_URL = "https://mcp.context7.com/mcp"
CONTEXT7_SWITCH_FILE = RUNTIME / "context7.txt"  # « off » : coupé au prochain démarrage du cerveau
# Modèle local (19/10, plan P8, `astrel/modeles_locaux.py`) : Ollama sur cette machine.
# Modèle choisi, mode léger en cours ; jamais de clé, jamais d'installation d'Ollama par Astrel.
LOCAL_MODEL_FILE = RUNTIME / "local-model.json"
OLLAMA_URL = (os.environ.get("ASTREL_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
# Registre public d'Ollama : taille exacte d'un modèle lue dans son manifeste (vide : jamais lu).
OLLAMA_REGISTRY = os.environ.get("ASTREL_OLLAMA_REGISTRY", "https://registry.ollama.ai").rstrip("/")
# Contexte du modèle dérivé (`/api/create`) : à mesurer, Ollama coupe sinon à son défaut.
LOCAL_NUM_CTX = int(_env_float("ASTREL_LOCAL_NUM_CTX", 32768))
# Retour sur Claude quand l'heure de réinitialisation de la limite est inconnue à l'entrée en
# mode léger (aucun évènement de quota reçu) : 5 h, la plus courte fenêtre de l'abonnement.
LOCAL_RETURN_FALLBACK_S = _env_float("ASTREL_LOCAL_RETURN_FALLBACK_S", 5 * 3600.0)
# Fournisseur du cerveau (20/10, plan P11, `astrel/fournisseurs.py`) : abonnement Claude (défaut),
# clé API Claude, ou un fournisseur à clé (OpenAI, Gemini, OpenRouter, Grok) derrière une passerelle
# locale.
# Clés dans `config/secrets.json` (0600) ; le choix dans les Réglages (`fournisseur`).
CLE_API_SCRIPT = ROOT / "astrel" / "cle_api.py"  # `apiKeyHelper` du CLI : imprime la clé, rien d'autre
PASSERELLE_SCRIPT = ROOT / "astrel" / "passerelle_serveur.py"
# Venv À PART de la passerelle (LiteLLM, ~270 Mo) : à côté du code, donc sous /data chez l'utilisateur.
PASSERELLE_VENV = Path(os.environ.get("ASTREL_PASSERELLE_VENV") or (ROOT / ".venv-passerelle")).expanduser()
PASSERELLE_LITELLM = os.environ.get("ASTREL_PASSERELLE_LITELLM", "litellm==1.105.1")  # version FIXÉE
PASSERELLE_JETON_FILE = RUNTIME / "passerelle-jeton"  # jeton local, nouveau à chaque lancement, 0600
DEPENSE_FILE = RUNTIME / "fournisseur-depense.json"  # dépense estimée du jour (alerte seule)
# Alerte de dépense (clé API) : par jour, en dollars ; 0 : jamais. ALERTE seule, jamais d'arrêt.
DEPENSE_ALERTE_USD = _env_float("ASTREL_DEPENSE_ALERTE_USD", 5.0)
FOURNISSEUR = os.environ.get("ASTREL_FOURNISSEUR", "abonnement")  # réglage `fournisseur` par défaut
# Outils (Bash, fichiers, MCP, agents) pour un fournisseur autre que Claude, en mode PRUDENT seulement.
# FAUX par défaut depuis le banc d'essai du 20/10 (tools/banc_passerelle.py) : le seul vrai modèle
# essayable ici (qwen2.5-coder:7b) écrit ses appels d'outil en texte au lieu de les faire ; GPT et
# Gemini n'ont pas pu être essayés sans clé. Faux = conversation seule, comme le mode léger.
PASSERELLE_OUTILS = _env_bool("ASTREL_PASSERELLE_OUTILS", False)
RETRY_DELAYS_S = (15.0, 30.0, 60.0)  # au plus 3 rejeux, seulement si rien n'a été dit ni exécuté
# Détecteur de boucles (ALERTE seule) : même outil + mêmes arguments N fois
# dans les LOOP_WINDOW derniers appels d'un même agent, ou N erreurs identiques de suite.
LOOP_REPEAT = int(_env_float("ASTREL_LOOP_REPEAT", 3))
LOOP_ERRORS = int(_env_float("ASTREL_LOOP_ERRORS", 3))
LOOP_WINDOW = int(_env_float("ASTREL_LOOP_WINDOW", 12))
# Historique des conversations (recherche plein texte, outil MCP history_search).
TRANSCRIPTS_DIR = Path(os.environ.get(
    "ASTREL_TRANSCRIPTS_DIR",
    str(Path.home() / ".claude" / "projects" / ("-" + str(WORKDIR).strip("/").replace("/", "-"))),
))
HISTORY_DB = RUNTIME / "history.db"

# --- Session ---
# Le contexte est renvoyé à chaque tour : une session sans fin coûte de plus en
# plus. Après ce temps d'inactivité, le cerveau repart d'un contexte vide.
SESSION_IDLE_RESET_S = _env_float("ASTREL_SESSION_RESET_S", 7200)
# Dernière session (id + heure du dernier échange) : une relance d'Astrel la
# reprend (`--resume`) si elle a moins de SESSION_IDLE_RESET_S, au lieu de tout oublier.
SESSION_FILE = RUNTIME / "session.json"
# Fenêtre d'écoute après une réponse, pour enchaîner sans redire le mot de réveil.
# Réglable à chaud (14/10, HUD ou `--send "followup N"`, 0 à 60 s), retenu dans
# FOLLOWUP_FILE : 0 = une phrase à la fois, le micro se ferme après chaque réponse.
FOLLOWUP_TIMEOUT_S = 8.0
FOLLOWUP_FILE = RUNTIME / "followup.txt"
FOLLOWUP_CHOICES = (0.0, 8.0, 20.0)
# 20/10 (demande de l'utilisateur) : aussi dans Réglages › Voix (interrupteur + durée de 1 à 30 s).
# FOLLOWUP_FILE garde son format (un nombre, 0 = coupé) ; la dernière durée NON NULLE est
# retenue à côté, pour la retrouver quand on rallume l'écoute prolongée.
FOLLOWUP_LAST_FILE = RUNTIME / "followup-last.txt"
FOLLOWUP_SETTING_MIN_S, FOLLOWUP_SETTING_MAX_S = 1, 30
# Modes d'usage (14/10) : conversation (voix), silencieux (réponse à l'écran,
# jamais dite), dictée (transcrit et copié, rien n'est envoyé à Claude).
USAGE_FILE = RUNTIME / "usage-mode.txt"
USAGE_MODES = ("conversation", "silencieux", "dictee")
# En dictée, l'écoute reste ouverte tant que l'utilisateur parle ; après ce silence,
# elle se ferme (le mode reste « dictée » : « Astrel » ou ◉ la rouvre).
DICTATION_IDLE_S = _env_float("ASTREL_DICTATION_IDLE_S", 45.0)

# --- État partagé et contrôle ---
STATE_FILE = RUNTIME / "state.json"
CONTROL_SOCKET = RUNTIME / "control.sock"

# --- Serveur du HUD (bus d'événements) ---
# Lié à 127.0.0.1 UNIQUEMENT, avec un jeton aléatoire à chaque démarrage : une
# commande reçue ici vaut une commande vocale, elle ne doit venir que de la
# machine, jamais 0.0.0.0.
HUD_HOST = "127.0.0.1"
HUD_PORT = int(os.environ.get("ASTREL_HUD_PORT", "4719"))
HUD_TOKEN_FILE = RUNTIME / "hud-token"
# 15/10 : clé STABLE qui signe les cookies du HUD (la page se reconnecte seule après
# une relance). Le serveur la range à côté de HUD_TOKEN_FILE (`server.session_key_file`).
HUD_SESSION_KEY_FILE = RUNTIME / "hud-session-key"
# Profil Chrome de la fenêtre du HUD (`./hud.sh`) : il garde le cookie de session,
# protégé par le garde-fou en lecture et en écriture comme la clé.
HUD_PROFILE_DIR = RUNTIME / "hud-profile"
LEVEL_HZ = 30
# Canaux du HUD, un fil de discussion par projet (`astrel/channels.py`) : la
# liste est dans CHANNELS_FILE (relue dès qu'elle change), le canal ouvert est
# retenu dans CHANNEL_FILE. La session Claude, elle, reste une seule conversation.
CHANNELS_FILE = CONFIG_DIR / "channels.json"
CHANNEL_FILE = RUNTIME / "channel.txt"
# Un tour relancé seul par le CLI va dans le canal de la tâche de fond finie
# depuis moins de ce délai ; au-delà, dans le canal par défaut.
CHANNEL_BACKGROUND_WINDOW_S = 60.0
# Comptes rendus des tâches de fond (`astrel/reports.py`) : « demande » (par
# défaut, 30/09, demande de l'utilisateur) = affichés en silence dans le HUD avec un
# bouton 🔊, lus quand il clique ; « direct » = lus aussitôt, comme avant.
# Choix du HUD ou de `--send "reports demande|direct"`, retenu dans REPORTS_FILE ;
# ASTREL_REPORTS reste prioritaire.
REPORTS_FILE = RUNTIME / "reports.txt"
# Ce que l'utilisateur n'a pas demandé (compte rendu de tâche de fond, réflexe,
# rappel) n'est JAMAIS dit pendant qu'il parle, qu'on l'écoute ou qu'on
# transcrit sa phrase, ni dans les SPEECH_GRACE_S suivant sa dernière parole
# entendue (`astrel/floor.py`, 13/10) : c'est mis en file, puis dit d'un bloc.
SPEECH_GRACE_S = _env_float("ASTREL_SPEECH_GRACE_S", 3.0)

# --- WhatsApp (plan P1, 17/10, `astrel/whatsapp.py`, docs/WHATSAPP.md) ---
# Pont Node (Baileys, version FIXÉE dans whatsapp/package.json), processus ENFANT
# du cœur, dialogue par tubes. Chat « message à soi-même » de l'utilisateur seulement.
WHATSAPP_BRIDGE_DIR = ROOT / "whatsapp"
# Identifiants du compte relié (ils valent le compte) : dossier 0700, ignoré par git.
WHATSAPP_AUTH_DIR = RUNTIME / "whatsapp-auth"
# Notes vocales téléchargées par le pont (option A2), effacées dès la transcription.
WHATSAPP_MEDIA_DIR = RUNTIME / "whatsapp-media"
# Option (relié, actif), mode « absent », identifiants de messages déjà vus.
WHATSAPP_STATE_FILE = RUNTIME / "whatsapp.json"
WHATSAPP_MAX_AGE_S = 180.0       # plus vieux : reçu pendant qu'Astrel était éteint, ignoré
WHATSAPP_SEEN_MAX = 500
WHATSAPP_CHUNK_CHARS = 3500      # un message WhatsApp au plus ; au-delà, découpé
WHATSAPP_PREFIX = "🔷"           # marque chaque réponse ; un message entrant ainsi marqué est ignoré
# 19/10 (demande de l'utilisateur) : 🤖 remplacé par 🔷. L'ancienne marque reste ignorée en
# entrée (messages déjà envoyés, revenus en écho) : jamais relue comme une demande.
WHATSAPP_IGNORED_PREFIXES = (WHATSAPP_PREFIX, "🤖")
WHATSAPP_SEND_GAP_S = _env_float("ASTREL_WHATSAPP_SEND_GAP_S", 1.5)  # pas de rafale
WHATSAPP_RESTART_DELAYS_S = (5.0, 15.0, 60.0, 300.0, 900.0)  # relance du pont, attente croissante
# Envoi d'un fichier à l'utilisateur dans son chat à soi (17/10, outil MCP `whatsapp_send_file`,
# demande de l'utilisateur : « envoie-moi les deux PDF par WhatsApp »). Seulement un
# fichier ordinaire de SES dossiers, jamais un secret ; copié par le cœur dans
# WHATSAPP_MEDIA_DIR (`out-…`), le seul dossier d'où le pont accepte d'envoyer.
WHATSAPP_FILE_ROOTS = tuple(Path(p) for p in os.environ.get("ASTREL_WHATSAPP_FILE_ROOTS", str(WORKDIR))
                            .split(os.pathsep) if p)
WHATSAPP_FILE_MAX_BYTES = 25 * 1024 * 1024  # même plafond dans whatsapp/bridge.mjs
WHATSAPP_FILE_TYPES = {  # extension → type MIME (liste fermée ; même liste dans whatsapp/filter.mjs)
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
}

# --- Images IA par le navigateur (plan P14, 21/10, `astrel/images_ia.py`, `imagegen/`) ---
# Demande de l'utilisateur : générer une image sur Gemini (ChatGPT au lot 5) dans un Chrome SANS
# fenêtre, rythme humain, crédit gratuit seulement. Worker Node jetable, version FIXÉE.
IMAGES_WORKER_DIR = ROOT / "imagegen"
IMAGES_RUNTIME = RUNTIME / "imagegen"
# Un profil Chrome PAR compte (`<alias>/`), jamais celui du navigateur « playwright ».
IMAGES_PROFILS = IMAGES_RUNTIME / "profils"
IMAGES_ETAT_FILE = IMAGES_RUNTIME / "etat.json"   # quotas, épuisements, compteur du jour
IMAGES_DEBUG_DIR = IMAGES_RUNTIME / "debug"       # captures d'une page changée, d'un captcha
IMAGES_LOG = LOGS / "images.log"                  # épuisements et premier succès suivant (reset à mesurer)
IMAGES_SORTIE = Path(os.environ.get("ASTREL_IMAGES_SORTIE", str(WORKDIR / "Images-IA")))
# Plafond PAR MOTEUR et par compte (consigne du 21/10, remplace « 10 par jour »),
# d'après les quotas gratuits relevés sur le web : Gemini 20 images par jour (valeur prudente,
# remise à zéro à minuit) ; ChatGPT gratuit 3 images sur une fenêtre de 24 h qui part de la
# PREMIÈRE image (lot 5). Réglables par variable ; page Réglages au lot 6.
# Bing Image Creator (décision du 21/10, lots suivants) : 15 générations RAPIDES par
# jour, chacune rend 4 variantes ; au-delà c'est très lent, donc 15 sert de plafond.
IMAGES_PLAFONDS = {
    "gemini": max(1, min(100, int(_env_float("ASTREL_IMAGES_PLAFOND_GEMINI", 20)))),
    "chatgpt": max(1, min(100, int(_env_float("ASTREL_IMAGES_PLAFOND_CHATGPT", 3)))),
    "bing": max(1, min(100, int(_env_float("ASTREL_IMAGES_PLAFOND_BING", 15)))),
}
# « jour » : compteur remis à zéro à minuit (heure locale) ; « 24h » : 24 h après la première image.
IMAGES_FENETRES = {"gemini": "jour", "chatgpt": "24h", "bing": "jour"}
IMAGES_DELAI_S = max(30.0, min(300.0, _env_float("ASTREL_IMAGES_DELAI_S", 120.0)))
# Comptes « alias:moteur » (Q5 : ImWeek AMV d'abord, alias « amw »). Jamais l'adresse.
IMAGES_COMPTES = tuple(
    tuple(p.strip().split(":", 1)) for p in os.environ.get("ASTREL_IMAGES_COMPTES", "amw:gemini").split(",")
    if ":" in p and p.strip())
IMAGES_FILE_MAX = 5                                # images en attente au plus

# --- Phrases fixes ---
# Dite au démarrage avec le nom affiché (25/10, plan P36 : `i18n.nom`, « Astrel est prêt. »).
SAY_READY = "Astrel est prêt."
SAY_NOT_UNDERSTOOD = "Je n'ai pas compris."
SAY_ERROR = "J'ai rencontré un problème."

# Formules de fin de conversation : Astrel repasse en veille sans rien exécuter.
STOP_WORDS = (
    "stop astrel", "arrête astrel", "arrete astrel", "astrel stop",
    "au revoir", "à plus tard", "a plus tard", "bonne journée", "bonne journee",
    "bonne soirée", "bonne soiree", "bonne nuit", "salut astrel",
    "c'est tout pour le moment", "c'est tout pour l'instant", "c'est bon pour moi",
    "ce sera tout", "merci c'est tout", "merci c'est bon", "rien d'autre",
    "tu peux te couper", "tu peux dormir", "coupe-toi", "mets-toi en veille",
    "on arrête là", "on arrete la", "j'ai fini", "terminé merci", "termine merci",
    "arrête-toi", "arrete-toi", "arrête toi", "arrete toi",
    "arrête d'écouter", "arrete d'ecouter", "arrête de m'écouter", "arrete de m'ecouter",
    "arrêter d'écouter", "arreter d'ecouter", "arrêter de m'écouter", "arreter de m'ecouter",
)
# Formules de fin reconnues seulement en FIN de phrase : « tu peux arrêter » au
# milieu (« tu peux arrêter la musique ») est une demande, pas un congé. Manquait
# le 26/09 : « C'est gentil, tu peux t'arrêter maintenant » relançait l'écoute.
STOP_ENDINGS_RE = (
    r"(?:^|\b(?:tu peux|vous pouvez)\s+(?:maintenant\s+)?)"
    r"(?:t'arr[eê]ter|arr[eê]ter|te taire|dormir|te reposer|arr[eê]ter d'[eé]couter)"
    r"(?:\s+(?:là|la|maintenant|pour (?:le moment|l'instant|ce soir|aujourd'hui)|merci|astrel|jarvis))*$"
    r"|^(?:(?:ok|bon|merci|c'est bon|c'est gentil)[\s,]*)*(?:stop|arr[eê]te|c'est bon|merci|merci astrel)$"
)
GOODBYES = ("À plus tard.", "Je me mets en veille.", "D'accord, à tout à l'heure.", "Je reste dans le coin.")
# Mots des réponses aux confirmations (oui, non, indécis) : dans `guard.py` (fichier protégé) depuis le 02/11,
# plus ici (`guard.YES_WORDS_FR`, `YES_WORDS_EN`, `NO_WORDS`, `UNDECIDED_WORDS`).

for _d in (RUNTIME, LOGS, CONFIG_DIR):
    _d.mkdir(parents=True, exist_ok=True)
