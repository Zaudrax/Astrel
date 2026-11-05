"""Décide si une action de Claude Code doit être confirmée par l'utilisateur.

Deux niveaux de gravité, deux modes :

| Gravité  | Exemples                                                   | « autonome » | « prudent » |
|----------|------------------------------------------------------------|--------------|-------------|
| critique | sudo, disques, extinction, `git push`, `rm -rf` hors des    | confirmé     | confirmé    |
|          | projets, ENVOYER un mail, SUPPRIMER, payer, déployer        |              |             |
| sensible | commit, écriture hors des projets, outil de connecteur      | libre        | confirmé    |
|          | (étiqueter, archiver, brouillon), tâche planifiée           |              |             |

Le mode « autonome » est le défaut depuis le 26/09/2026, à la demande
explicite de l'utilisateur (« je veux juste qu'il fasse les choses
automatiquement » — la confirmation de chaque étiquette Gmail rendait les
longues tâches impossibles). Il se change sur le HUD ou par
`./run.sh --send "autonomy prudent"` — JAMAIS par Claude : un modèle qui
pourrait desserrer son propre garde-fou n'aurait pas de garde-fou.

Une autorisation « pour 30 minutes » (bouton du HUD, ou « oui à tout » à
l'oral) couvre ensuite le même type d'action sans reposer la question.

14/10 (autorisation explicite de l'utilisateur) : les confirmations revenaient en
mode autonome. Mesuré dans `logs/astrel.log` : 45 questions « action sur le
garde-fou » pour de simples LECTURES (`sed -n … guard.py`, `pytest
tests/test_guard.py`, et même `repetition_guard.py` d'un autre dépôt), 29
« envoi d'un e-mail » pour le mot « mail » dans un texte ou un nom de fichier,
des « push » dans un message de commit. Toutes critiques, donc posées même en
autonome, et sans « 30 min » possible pour le garde-fou. Désormais : nom exact
des fichiers protégés, lecture seule permise, mots d'envoi ou d'extinction
cherchés en position de COMMANDE, hors du corps des heredocs de données et
des messages de commit. L'arrêt d'urgence (`runtime/ESTOP`, `--send estop`) et
la mission (`runtime/mission.json`, `--send mission`) sont protégés comme le mode,
et le jeton du HUD (`runtime/hud-token`) ne se LIT pas : avec lui, n'importe quel
programme parlerait au HUD comme l'utilisateur (lever l'arrêt, répondre « oui »).
Le cœur, lui, refuse ces commandes quand elles viennent d'un descendant du
process Claude (`state.ControlServer`), et repose `runtime/ESTOP` s'il disparaît.

⚠️ Décision prise par le HOOK, un process séparé que Claude ne peut ni modifier
ni contourner — `--dangerously-skip-permissions` coupe seulement les invites
graphiques du CLI, qu'aucun écran ne pourrait afficher en mode headless.
Limite connue : c'est une analyse de TEXTE. Un script écrit puis lancé
(`python outil.py`) qui toucherait ces fichiers sans les nommer n'est pas vu.

15/10 (autorisation explicite de l'utilisateur, docs/DURCISSEMENT-GARDE-FOU.md) :
- « oui pour 30 min » ne couvre plus TOUTE une commande composée d'après sa
  seule étiquette la plus grave : une autorisation par étiquette, et toutes les
  étiquettes à confirmer d'une commande doivent être couvertes (`Verdict.keys`) ;
- les planificateurs (systemd-run, at, batch, crontab) sont CRITIQUES : ils
  lancent un programme hors du cgroup du service, donc hors de la vue du cœur ;
- le dossier courant de Claude (`cwd`, transmis par le hook) sert à résoudre
  les chemins relatifs ;
- `Verdict.guard` marque ce qui touche au garde-fou lui-même : même le mode
  « Autonome TOTAL » du cœur le fait confirmer ;
- un motif de recherche qui contient « hud-token » (`grep "…hud-token…" f`)
  n'est plus pris pour une lecture du jeton ;
- le code qui DÉCIDE qui active Total (`totalgate.py`), qui reconnaît Claude
  (`state.py`), qui authentifie le HUD (`server.py`), le JS/HTML du HUD et la
  bulle (`bubble.py`, `minichat.py` : clics Oui/Non) sont protégés en écriture
  (`PROTECTED_CODE`, `Verdict.guard`) : sinon Claude se donnait Total en les
  réécrivant puis en relançant Astrel. `core.py` reste libre (choix de l'utilisateur).

15/10, partie B (autorisation explicite de l'utilisateur, docs/DURCISSEMENT-GARDE-FOU.md) :
les commandes Bash ne sont plus jugées par des motifs cherchés dans le texte brut,
mais par une ANALYSE STRUCTURÉE (`_analyze`) : découpage en commandes simples
(; && || | sous-shells, $(…), heredocs), mots lus comme le shell les lit,
programme réellement lancé retrouvé derrière sudo/env/nohup/xargs…, `cd` suivis,
chemins normalisés (~, $HOME, relatifs, `..`, jokers). Conséquences :
- suppression récursive (rm -r sous toutes ses formes, find -delete, -exec rm,
  xargs rm, rsync --delete, rimraf) : confirmée dès que la cible n'est pas
  STRICTEMENT dans un projet autorisé, ou qu'on ne peut pas la connaître ;
- écritures (cp, mv, tee, ln, install, dd, sed -i, curl -o, > >>) comparées aux
  fichiers sensibles du dossier personnel (`SENSITIVE_HOME`) : critiques ;
- un mot dangereux qui n'est qu'un ARGUMENT (grep sudo f, un message de commit,
  echo) ne déclenche plus rien ; une syntaxe illisible est confirmée.

15/10, mission autonome exposée (autorisation explicite de l'utilisateur) : pendant une
mission, écrire ou supprimer hors de son périmètre est critique, dans un de ses
dossiers interdits est une action sur le garde-fou (`mission_fence`, `_judge_fence`).

16/10, mode plan (autorisation explicite de l'utilisateur : un plan ne se valide QUE depuis
le HUD, jamais par un agent) : `runtime/plans/` (P<n>.json fait foi, statut compris)
est protégé comme `mission.json`. Créer, modifier ou supprimer un plan par Write/Edit
ou par Bash est une action sur le garde-fou ; le lire reste libre (le codeur lit
P<n>.md). Seuls les outils MCP `plan_submit`/`plan_reply`/`plan_list` y écrivent, par
le cœur (`astrel/plans.py`). `--send plan_validate|plan_reject|plan_revise|plan_comment`
est protégé comme `--send estop` (le cœur les refuse déjà à Claude).

17/10, WhatsApp (autorisation explicite de l'utilisateur, docs/WHATSAPP.md) : les
identifiants (`runtime/whatsapp-auth/`, ils valent le compte) et les notes vocales
téléchargées (`runtime/whatsapp-media/`) ne se LISENT pas, comme le jeton du HUD
(`SECRET_PATHS`) ; `runtime/whatsapp-auth/` et `runtime/whatsapp.json` (mode
« absent ») sont protégés en écriture comme `mission.json` (`PROTECTED_PATHS`) ; le
code du pont (`whatsapp/`) et `astrel/whatsapp.py` comme `server.py`
(`PROTECTED_CODE`, lecture libre). Seul le cœur (et le pont, son enfant) y écrit.

18/10, levée du garde-fou (plan P7, demande de l'utilisateur : travailler sur le code de
Astrel sans une carte par fichier) : ÉCRIRE ou ÉDITER un fichier de `LIFTABLE` (code
du garde-fou, de la bulle, de WhatsApp, du HUD) est noté `guard="dev"` au lieu de
`True` (toujours vrai : les appelants ne changent pas). `Verdict.lift` est vrai
seulement si TOUT ce qui est critique dans l'action est de ce genre (« git push &&
cp x hud/src/main.js » : non) et en mode autonome. Le cœur ne l'accorde sans question
que si l'utilisateur a levé le garde-fou dans les Réglages du HUD (`totalgate.lift_grants`).
Restent demandés : totalgate.py, state.py, server.py, secrets, fichiers d'état et
journaux protégés, suppressions, mission, plans, et toute commande Bash qui CITE
guard.py, confirm.py ou le hook (texte brut : outil d'édition seulement).

23/10, limites par cases à cocher (plan P29, demande de l'utilisateur) : chaque danger porte sa ou
ses CATÉGORIES (`CATS`, `_add(cat=)`, `Verdict.cats`, catalogue dans `limites.py`). Le cœur
n'accorde sans question en Total ou en levée que si TOUTES sont cochées dans la colonne
(`totalgate`). `guard=True` = `limites.PLAFOND` (jamais libre) ; `guard="dev"` = famille du
fichier levable. Aucun verdict ne change (`needs_confirm`, `guard`, `lift`, clés) : seules
les catégories s'ajoutent. `limites.py` est dans `PROTECTED_CODE`.

24/10 (P29 lot B, demande de l'utilisateur : « tout doit être décochable ») : le plafond est découpé.
Une étiquette `guard=True` porte le marqueur `PLAFOND` PLUS sa catégorie précise (`_path_cat`,
`_ceiling_label_cats` : identifiants de Claude, secrets, recherche large, journaux, état, code qui
décide, citation, dossier interdit de mission), chacune avec sa case de confirmation
(`limites.CASES`, Réglages › Garde-fou). Le cœur ne montre pas de carte si toutes les catégories
de l'action sont décochées (`totalgate.confirmation_free`) ; Total et levée inchangés.

01/11 (accord explicite de l'utilisateur, docs/DURCISSEMENT-GARDE-FOU.md) : protégés en plus, les
instantanés avant écriture (`runtime/instantanes/`, code `instantanes.py`), la série de refus du
disjoncteur (`REFUS_DIR`, lue par `hooks/permission_hook.py`) et les messages de l'utilisateur en attente
(`runtime/messages-en-attente.json`, `attente.py`). Aucun verdict desserré.

04/11 (lot de sécurité A, accord explicite de l'utilisateur ; audits du 03/11) : protégés en plus la fermeture
des imports des hooks Bash/Write, les scripts cités par les réglages du CLI et leurs imports directs, le
doctor, la preuve et la levée (`PROTECTED_CODE`) ; `runtime/mcp.json` (secret), le profil et le micro
(`PROTECTED_PATHS`). Outils MCP : `browser_run_code_unsafe` refusé en dur, un outil absent d'un serveur
connu jugé sur son nom (plus jamais libre d'office). Sources audio : critiques ; retrait du marqueur
`ASTREL_BRAIN` : garde-fou ; détachement hors cgroup du service : question. Windows : formes `\\\\?\\`,
flux NTFS, point final, 8.3, casse ramenées au chemin (`_win_forme`). Aucun verdict desserré.

04/11 (secrets du compte, autorisation explicite de l'utilisateur) : clés SSH et GPG, jetons du cloud (aws, gcloud,
azure, kube, docker, gh, npm, pypi), .netrc, coffres, trousseaux, mots de passe et cookies des navigateurs, par
système (`SecretsCompte`, `_secrets_compte`). Les LIRE (Read, Grep, toute commande qui les cite, joker, lecteur
récursif sur un dossier parent, programme lancé dans ~/.ssh, CLI qui sort un jeton) : plafond, case « secrets du
compte » ; les modifier ou les supprimer : critique. Aucun verdict desserré.

05/11 (agents imbriqués, autorisation explicite de l'utilisateur) : lancer un AUTRE agent IA (claude -p, codex, gemini,
aider…, sous toutes ses formes : chemin, lien, paquet npm, node cli.js, python -m, code d'un autre langage, SDK,
tmux/screen/terminal, cmd/PowerShell, variables CLAUDE_CONFIG_DIR/CLAUDE_CODE_*/CLAUDECODE) : plafond, case
« agent imbriqué » (`L_AGENT_IMBRIQUE`, `_judge_agent`). Aide, version et gestion du CLI claude restent libres.
tmux, screen et les terminaux font désormais juger la commande qu'ils lancent. Aucun verdict desserré.
"""
from __future__ import annotations

import contextvars
import dataclasses
import fnmatch
import getpass
import glob as _glob_mod  # 04/11 : jokers développés sur le disque (secrets du compte)
import json
import os
import re
import sys
import tempfile
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config, limites

CRITICAL = "critique"
SENSITIVE = "sensible"

MODE_FILE = config.RUNTIME / "autonomy.json"
GRANTS_FILE = config.RUNTIME / "grants.json"
MODES = ("autonome", "prudent")
DEFAULT_MODE = os.environ.get("ASTREL_AUTONOMY", "autonome")
GRANT_S = 30 * 60


@dataclass
class Verdict:
    needs_confirm: bool
    desc: str = ""
    key: str = ""        # ce qu'une autorisation « pour 30 min » couvre (la plus grave)
    level: str = ""
    # 15/10 : TOUTES les autorisations nécessaires (une par étiquette à confirmer) ;
    # vide si l'action ne peut pas être autorisée pour 30 min (garde-fou).
    keys: tuple[str, ...] = ()
    guard: bool = False  # touche au garde-fou lui-même : demandé même en « Autonome TOTAL »
    # (clé, gravité, étiquette) de chaque danger trouvé, avant le filtre du mode.
    items: tuple[tuple[str, str, str], ...] = field(default=(), repr=False)
    # 18/10 (plan P7) : tout ce qui est critique ici est une écriture d'un fichier de
    # `LIFTABLE` ; accordé sans question seulement pendant une levée du garde-fou.
    lift: bool = False
    # 23/10 (plan P29) : catégories (cases des limites de Total et de la levée, `limites.py`) des
    # étiquettes qui demandent, au mode donné ; `limites.PLAFOND` pour le garde-fou lui-même.
    cats: tuple[str, ...] = ()
    # (gravité, catégories) de chaque danger trouvé, avant le filtre du mode (`assess` choisit).
    cat_items: tuple[tuple[str, tuple[str, ...]], ...] = field(default=(), repr=False)


# Étiquettes prononcées des dangers d'une commande Bash (une autorisation « 30 min »
# vaut pour une étiquette). Ne pas les reformuler à la légère : les autorisations en
# cours (`runtime/grants.json`) portent ces textes.
L_ADMIN = "une commande administrateur"
L_POWER = "une extinction ou un redémarrage de l'ordinateur"
L_SESSION = "une action sur la session"
L_DISK = "une opération sur les disques"
L_DISK_WRITE = "une écriture directe sur un disque"
L_DESTROY = "une destruction de données"
L_PUSH = "une publication git (push)"
L_GIT_ERASE = "une opération git qui efface du travail"
L_DOWNLOAD = "l'exécution d'un script téléchargé"
L_GITHUB = "une action publique sur GitHub"
L_PUBLISH = "une publication de paquet"
L_MAIL = "l'envoi d'un e-mail"
L_RESET = "une remise à zéro des réglages"
L_SCHEDULE = "une tâche planifiée"
L_GIT = "une opération git"
L_SERVICE = "l'arrêt d'un service"
L_PACKAGES = "une installation de paquets"
L_PERMS = "un changement de permissions"
L_KILL = "l'arrêt forcé de processus"
L_DOCKER = "une suppression Docker"
L_REMOTE = "une connexion à une autre machine"
L_RM = "une suppression récursive de fichiers"
L_RM_OUTSIDE = "une suppression récursive de fichiers hors de tes projets"
L_RM_GUARD_DIR = "une suppression récursive dans un dossier du garde-fou"
L_RM_REMOTE = "une suppression récursive sur une autre machine"
L_WRITE_GUARD_DIR = "une modification de fichiers dans un dossier du garde-fou"
L_RM_REMOTE_WIDE = "une suppression récursive large sur une autre machine"
L_GUARD = "une action sur le garde-fou d'Astrel"
L_CLAUDE_ID = "une action sur le compte ou les serveurs MCP de Claude Code"  # 19/10 (audit)
_CLAUDE_CRITICAL = {("auth", "login"), ("auth", "logout"), ("mcp", "add"), ("mcp", "add-json"), ("mcp", "remove"),
                    # 19/10 (relecture) : du code lancé à chaque session, comme un serveur MCP
                    ("mcp", "add-from-claude-desktop"), ("plugin", "install"), ("plugin", "i"),
                    ("plugins", "install"), ("plugins", "i"), ("marketplace", "add")}
# `claude update|upgrade|install` : remplace le CLI lui-même (en tête, hors `-p` : un prompt).
_CLAUDE_SELF = {"update", "upgrade", "install"}
_CLAUDE_JS = re.compile(r"(?:^|/)claude-code/cli\.m?js$")          # node …/claude-code/cli.js
_CLAUDE_BIN = re.compile(r"(?:^|/)claude/versions/[^/]+$")         # ~/.local/share/claude/versions/2.1.285


def _claude_cli(args: list, out: list) -> None:
    # 19/10 (relecture) : tous les CANDIDATS comptent, `echo auth logout | xargs claude`
    # donne « auth » et « logout » comme valeurs d'un même mot.
    a = [c for x in args for c in x.cands]
    if "--help" in a or "-h" in a:
        return  # `claude auth login --help` : affiche l'aide, ne fait rien
    ops = _operands(a)
    if ("setup-token" in ops or any(pair in _CLAUDE_CRITICAL for pair in zip(ops, ops[1:]))
            or (not {"-p", "--print"} & set(a) and _CLAUDE_SELF & set(ops[:2]))):
        _add(out, L_CLAUDE_ID, guard=True)


# 05/11 (trou confirmé, autorisation explicite de l'utilisateur) : `claude -p "…"` lancé par le Bash de Claude passait
# SANS question, et ce CLI imbriqué ne charge pas le hook d'Astrel (posé par le seul `--settings` du cerveau) : il
# agissait sans garde-fou. Lancer un AUTRE agent IA qui exécute des outils = plafond (ni Total, ni levée, ni « oui
# pour 30 min »), case `agent_imbrique`. Libres : aide et version ; `claude doctor`, `auth status`, `mcp list|get`,
# `plugin list` (ce qui change le compte reste jugé par `_claude_cli`). Le CLI local du moteur Qwen et les CLI des
# fournisseurs sont lancés par le CŒUR, jamais par ce Bash : non concernés.
L_AGENT_IMBRIQUE = "le lancement d'un autre agent IA sans le garde-fou (claude, codex, gemini…)"
_AGENTS = frozenset({
    "claude", "claude-code", "codex", "gemini", "gemini-cli", "aider", "aider-chat", "opencode", "opencode-ai",
    "goose", "cursor-agent", "amp", "grok", "grok-cli", "qwen", "qwen-code", "crush", "openhands", "openhands-ai",
    "copilot", "auggie", "droid", "plandex", "kilocode", "cline", "kiro-cli", "gptme", "interpreter",
    "open-interpreter", "kimi", "qodo"})
_AGENT_NOMS = "|".join(sorted(map(re.escape, _AGENTS), key=len, reverse=True))
# Paquets npm des CLI d'agents, dans un chemin ou un nom (`node …/@openai/codex/bin/codex.js`, `npm:@…`).
_AGENT_PAQUET = re.compile(r"(?:^|[/:\\])(?:@anthropic-ai[/\\]claude-(?:code|agent-sdk)|@openai[/\\]codex"
                           r"|@google[/\\]gemini-cli|@qwen-code[/\\]qwen-code|opencode-ai|@sourcegraph[/\\]amp"
                           r"|@charmland[/\\]crush|@vibe-kit[/\\]grok-cli|@github[/\\]copilot|@augmentcode[/\\]auggie"
                           r"|@kilocode[/\\]cli|@factory[/\\]cli|@cline[/\\]cli)(?:[/@\\]|$)")
_AGENT_MODULES = {"aider", "openhands", "interpreter", "gptme"}  # python -m aider
_AGENT_SUFFIXE = re.compile(r"\.(?:exe|cmd|bat|ps1|com)$")
# Un nom de CLI d'agent en position de COMMANDE dans un texte qu'on ne lit pas comme du shell (cmd /c, PowerShell,
# wsl, expect, shell illisible, programme calculé) : début, après un séparateur ou une citation, chemin compris.
_AGENT_TEXTE = re.compile(r"(?:^|[\s;&|(`'\"])(?:[^\s;&|()`'\"]*[/\\])?(?:" + _AGENT_NOMS + r")(?:\.exe|\.cmd|\.ps1)?"
                          r"(?=$|[\s;&|)`'\"])", re.I)
# Code d'un autre langage (python -c, heredoc, node -e, awk) : un CLI d'agent lancé (`subprocess.run(["claude",
# …])`, `os.system("codex exec …")`, `spawn('gemini')`, Tcl `exec claude`, perl `` `claude -p` ``) ou le SDK
# d'agent (`claude_agent_sdk`, qui lance le CLI).
_AGENT_LANCE = re.compile(r"subprocess|Popen|\bsystem\b|\bexec\w*|\bspawn\w*|popen|child_process|\bCommand\b"
                          r"|Process\.Start|ProcessBuilder|Open3|\bpty\b|shell\s*=")
# Nom cité en PREMIER argument d'une fonction qui lance un process (`run(["claude"`, `system("codex …`,
# `spawn('gemini'`) ou d'une liste affectée (`cmd = ["claude", …]`) ; pas une valeur (`{service: 'gemini'}`,
# `site === 'gemini'`, `args=("codex", "outils")`, `for f in ("codex", …)` : rejeu du 05/11).
_AGENT_CHAINE = re.compile(r"(?:\b(?:run|call|Popen|check_output|check_call|system|popen|getoutput|getstatusoutput"
                           r"|spawn\w*|exec\w*|create_subprocess_\w+)\s*\(\s*\[?|=\s*\[)\s*[\"'`](?:[^\"'`\s]*[/\\])?(?:"
                           + _AGENT_NOMS + r")(?:\.exe|\.cmd)?(?=[\"'`\s])")
_PAS_UN_CHEMIN = re.compile(r"[\s'\"();]")
_AGENT_NU = re.compile(r"\b(?:exec|spawn|system)\s+[\"']?(?:[^\s;\"']*/)?(?:" + _AGENT_NOMS + r")(?=[\s;\"']|$)")
_AGENT_SDK = re.compile(r"claude_agent_sdk|claude_code_sdk|@anthropic-ai/claude-(?:agent-sdk|code)(?![\w-])"
                        r"|@openai/codex-sdk|codex_sdk")
# Variables d'un CLI de Claude posées pour LANCER (`CLAUDE_CONFIG_DIR=/tmp/c python agent.py`, `export
# CLAUDE_CODE_…`, `unset CLAUDECODE`) : le programme qui suit lance un CLI.
_VAR_AGENT = re.compile(r"^(?:CLAUDE_CONFIG_DIR|CLAUDECODE|CLAUDE_CODE_\w+)$")
# Options propres aux CLI d'agents : données à n'importe quel programme (binaire renommé, script qui les passe au
# CLI), sauf aux lecteurs de texte qui les CHERCHENT (`grep -- --dangerously-skip-permissions …`).
_AGENT_DRAPEAUX = {"--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
                   "--dangerously-bypass-approvals-and-sandbox", "--permission-mode", "--append-system-prompt",
                   "--mcp-config", "--strict-mcp-config", "--allowedTools", "--disallowedTools", "--yolo"}
_CHERCHEURS = {"grep", "egrep", "fgrep", "rg", "ag", "ack", "git", "echo", "printf", "sed", "awk", "gawk", "mawk",
               "jq", "yq", "less", "more", "head", "tail", "cat", "bat", "batcat", "man", "wc", "sort", "uniq", "cut",
               "tr", "diff", "cmp", "tee", "test", "[", "true", ":", "fd", "fdfind", "find", "locate", "which", "type",
               "whereis", "ls", "stat", "file", "strings", "column", "nl", "fold", "comm", "tac", "rev", "read"}
_CLAUDE_AIDE = {"--help", "-h", "--version", "-v", "-V"}
_AGENT_AIDE = {"--help", "--version"}
# Sous-commandes de gestion du CLI claude (aucun agent lancé ; `_claude_cli` juge celles qui changent le compte).
_CLAUDE_GESTION = {"doctor", "auth", "mcp", "plugin", "plugins", "marketplace", "setup-token", "update", "upgrade",
                   "install", "config", "migrate-installer"}
_PROC_EXE = re.compile(r"^/proc/[^/]+/(?:exe|fd/\d+)$")  # le binaire d'un process (le cerveau : /proc/$PPID/exe)
_QUI_EST: dict[str, str] = {}  # nom nu → vrai chemin (PATH), lu une fois par process


def _nom_agent(c: str) -> str:
    """05/11 : nom du CLI d'agent que désigne ce mot de commande (« » : aucun). Nom (Windows : .exe, .cmd…),
    paquet npm (`@anthropic-ai/claude-code@latest`), binaire natif (`…/claude/versions/2.1.285`), chemin
    d'un paquet d'agent, et VRAI chemin d'un lien (`ln -s ~/.local/bin/claude /tmp/x` puis `/tmp/x`)."""
    if _PROC_EXE.match(c):
        return "agent"  # /proc/$PPID/exe : le binaire du cerveau lui-même (pid inconnu compris)
    if not c or _DYN in c:
        return ""
    if _CLAUDE_BIN.search(c) or _CLAUDE_JS.search(c):
        return "claude"
    if _AGENT_PAQUET.search(c):
        return "claude" if "claude" in c else "agent"
    n = _AGENT_SUFFIXE.sub("", c.replace("\\", "/").rsplit("/", 1)[-1].lower())
    if "@" in n[1:]:
        n = n[:n.index("@", 1)]  # npx claude-code@latest
    if n in _AGENTS:
        return n
    if _PROC_EXE.match(c):
        return "agent"
    vrai = _vrai_chemin(c)
    if vrai and vrai != c and (_CLAUDE_BIN.search(vrai) or _CLAUDE_JS.search(vrai) or _AGENT_PAQUET.search(vrai)
                               or _AGENT_SUFFIXE.sub("", vrai.rsplit("/", 1)[-1].lower()) in _AGENTS):
        return "claude" if "claude" in vrai else "agent"
    return ""


def _vrai_chemin(c: str) -> str:
    """Vrai chemin d'un programme (lien suivi) ; nom nu cherché dans PATH. « » si introuvable."""
    try:
        if "/" in c or "\\" in c:
            return os.path.realpath(os.path.expanduser(c))
        if c not in _QUI_EST:
            import shutil

            w = shutil.which(c)
            _QUI_EST[c] = os.path.realpath(w) if w else ""
        return _QUI_EST[c]
    except (OSError, ValueError):
        return ""


def _agent_sans_outils(nom: str, args: list) -> bool:
    """05/11 : ce lancement d'un CLI d'agent ne fait-il que s'afficher (aide, version) ou gérer le CLI claude
    (doctor, auth, mcp sauf `serve`, plugin…) ? Un mot inconnu ou calculé : non (un agent peut tourner)."""
    avant = []
    for x in args:
        if x.text == "--" and not x.dyn:
            break
        avant.append(x)
    lits = {x.text for x in avant if not x.dyn and len(set(x.cands)) == 1}
    claude = nom in ("claude", "claude-code")
    if lits & (_CLAUDE_AIDE if claude else _AGENT_AIDE):
        return True
    if not claude:
        return len(args) == 1 and not args[0].dyn and args[0].cands in (["-h"], ["-V"])
    if not args or args[0].dyn or not set(args[0].cands) <= _CLAUDE_GESTION:
        return False
    if "mcp" in args[0].cands:  # `claude mcp serve` : les outils du CLI (Bash, Edit…) servis sans garde-fou
        return len(args) < 2 or (not args[1].dyn and "serve" not in args[1].cands)
    return True


def _agent(out: list) -> None:
    _add(out, L_AGENT_IMBRIQUE, guard=True, cat=limites.AGENT_IMBRIQUE)


def _drapeau_agent(args: list) -> bool:
    """05/11 : une option propre aux CLI d'agents (`--dangerously-skip-permissions`, `--mcp-config=…`)."""
    return any(c in _AGENT_DRAPEAUX or c.split("=", 1)[0] in _AGENT_DRAPEAUX for x in args for c in x.cands)


def _agent_dans_code(text: str) -> bool:
    """05/11 : du code d'un autre langage qui lance un CLI d'agent ou charge son SDK."""
    return bool(_AGENT_SDK.search(text) or _AGENT_NU.search(text)
                or (_AGENT_LANCE.search(text) and _AGENT_CHAINE.search(text)))
# Doutes de l'analyse : toujours confirmés, jamais couverts par « oui pour 30 min »
# (la question suivante peut cacher tout autre chose).
D_SYNTAX = "une commande que le garde-fou ne sait pas analyser"
D_PROGRAM = "une commande dont le programme est calculé"
D_RM = "une suppression récursive dont la cible est incertaine"
D_WRITE = "une écriture vers une destination incertaine"
D_STDIN = "l'exécution d'un code produit par une autre commande"
# 04/11 (lot de sécurité A, accord explicite de l'utilisateur ; audits du 03/11) :
# - la voix confirme : une source audio créée ou choisie par Claude (pactl load-module, pw-loopback…)
#   ferait entendre un « oui » qu'il fabrique (audit 3, H1) ;
# - hors cgroup du service, « vient de Claude » repose sur la descendance et le marqueur `ASTREL_BRAIN`
#   (`state.from_claude`) : le retirer est une action sur le garde-fou, se détacher (setsid, nohup,
#   disown) une question quand le cgroup ne le rattrape pas (audit 3, H3).
L_AUDIO = "une modification des sources audio du micro"
L_BRAIN_MARK = "un retrait du marqueur qui reconnaît les process de Claude"
L_DETACH = "le lancement d'un process détaché de Claude (setsid, nohup, disown)"
# 04/11 (lot 3b du moteur local, autorisation de l'utilisateur) : ce que Claude lit part chez Anthropic. Une lecture
# (Read, Grep, Glob, une commande qui cite le chemin) dans une ZONE CONFIDENTIELLE de l'utilisateur (Réglages ›
# Confidentialité, `zones_confidentielles` de runtime/settings.json, écrit par le cœur seul) est demandée.
L_ZONE = "une lecture dans une zone confidentielle"
# 04/11 (secrets du compte, autorisation explicite de l'utilisateur ; trou confirmé le 04/11) : seuls les secrets
# d'Astrel et `~/.claude/.credentials.json` étaient protégés ; `cat ~/.ssh/id_rsa`, `~/.aws/credentials`,
# `~/.netrc`, les mots de passe et cookies des navigateurs se lisaient sans question, et une injection (page
# web, mail) pouvait les faire lire puis envoyer. Lire un secret du COMPTE (`_secrets_compte`, par système) :
# plafond (ni Total ni « oui pour 30 min »), case « secrets du compte » ; le modifier : critique.
L_SECRET_UTILISATEUR = "la lecture d'un secret de ton compte (clé, mot de passe, jeton)"
L_SECRET_UTILISATEUR_MODIF = "une modification d'un secret de ton compte (clé, mot de passe, jeton)"

# Code d'un AUTRE langage (python -c, heredoc lu par python, awk…) : illisible pour
# l'analyse du shell, on y cherche seulement ces motifs-là. Un `sudo` caché dans un
# script envoyé à un interpréteur reste un `sudo`, un téléchargement exécuté aussi.
_RAW_CODE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(sudo|doas|pkexec|su\s+-)\b"), L_ADMIN),
    (re.compile(r"\b(mkfs|fdisk|parted|wipefs|dd\s+if=)"), L_DISK),
    (re.compile(r">\s*/dev/(sd|nvme|mmcblk)"), L_DISK_WRITE),
    (re.compile(r"(curl|wget)[^|;]*\|\s*(sudo\s+)?(ba|z|s)?sh\b"), L_DOWNLOAD),
]

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# Outils sans effet de bord hors de la session, ou dont chaque sous-action repasse
# de toute façon par ce hook (un sous-agent `Task` qui lance Bash est contrôlé).
SAFE_TOOLS = {
    "Read", "Grep", "Glob", "LS", "NotebookRead", "TodoWrite", "BashOutput",
    "WebSearch", "WebFetch", "ToolSearch", "Skill", "Task", "Agent", "TaskStop",
    "Monitor", "ListAgents", "SendMessage", "PushNotification", "EnterWorktree", "ExitWorktree",
}

# Outils natifs à effet durable ou public.
NATIVE_POLICY = {
    "CronCreate": ("une tâche planifiée dans le cloud", SENSITIVE),
    "CronDelete": ("la suppression d'une tâche planifiée", SENSITIVE),
    "RemoteTrigger": ("le déclenchement d'une tâche à distance", SENSITIVE),
    "Workflow": ("un travail qui lance de nombreux agents", SENSITIVE),
    "Artifact": ("la publication d'une page", SENSITIVE),
    "ArtifactData": ("une modification de données en ligne", SENSITIVE),
    "ArtifactComments": ("un commentaire sur une page en ligne", SENSITIVE),
    "DesignSync": ("une synchronisation de maquettes", SENSITIVE),
    "ScheduleWakeup": ("un réveil programmé", SENSITIVE),
}

# Serveurs MCP connus : outil → gravité ("" = libre, relu comme sans effet extérieur). Un outil d'un
# serveur INCONNU (connecteurs claude.ai…) est classé d'après son NOM (`_mcp_level`).
# 04/11 (lot A, audit 2 T44-B, accord de l'utilisateur) : avant, un outil ABSENT de la liste d'un serveur
# connu était LIBRE dans les deux modes (le navigateur principal, connecté aux comptes de l'utilisateur,
# était plus libre que les navigateurs jetables). Désormais il est jugé sur son nom, comme sur un
# serveur inconnu (sensible au moins) ; chaque outil de `mcp_server.py` et `mcp_connectors.py` a sa
# classe ici (`tests/test_securite_lot_a.py` le vérifie). Rien n'est desserré : "" seulement pour ce
# qui était déjà libre ; en prudent, voir l'écran, la webcam ou le presse-papiers, ouvrir une adresse,
# un fichier ou une app, allumer un capteur, générer une image ou une vidéo, envoyer un fichier sur
# WhatsApp (chat avec soi-même), réactiver un réflexe, verrouiller, retirer un canal : demandés.
MCP_POLICY: dict[str, dict[str, str]] = {
    "astrel": {
        # voir
        "screen_capture": SENSITIVE, "list_screens": "", "camera_capture": SENSITIVE, "system_status": "",
        # agir sur le bureau (`open_path` d'un fichier qui lance du code : critique, `_mcp_input_level`)
        "open_url": SENSITIVE, "open_app": SENSITIVE, "open_path": SENSITIVE, "notify": "",
        "set_volume": "", "set_brightness": "", "set_dark_mode": "", "media_control": "",
        "clipboard_read": SENSITIVE, "clipboard_write": SENSITIVE, "lock_screen": SENSITIVE,
        "power_action": CRITICAL, "wifi_toggle": CRITICAL,
        # mémoire et historique (filtrés par `tools/memory.py`), « annule » (rejugé par `instantanes.py`)
        "memory_search": "", "memory_read": "", "memory_write": "", "memory_list": "", "memory_versions": "",
        "annuler_ecritures": "", "memory_review": "", "memory_propose": "", "history_search": "",
        # rappels, capteurs, HUD (affichage seul ; le cœur refuse à Claude ce qui desserre)
        "reminder_create": "", "reminder_list": "", "reminder_delete": SENSITIVE,
        "sensor_status": "", "set_sensor": SENSITIVE, "set_voice_pause": "", "post_report": "", "show_media": "",
        "ask_choice": "", "mission_start": "",
        # crédits des comptes en ligne, envoi par WhatsApp (chat avec soi-même, filtré par le cœur)
        "generate_image": SENSITIVE, "generate_video": SENSITIVE, "whatsapp_send_file": SENSITIVE,
        # plans, todo, équipe, courrier, canaux : écrits par le cœur (plans protégés), données seulement
        "plan_submit": "", "plan_reply": "", "plan_list": "",
        "todo_add": "", "todo_list": "", "todo_move": "", "todo_edit": "", "todo_done": "", "todo_drop": "",
        "passation": "", "courrier_ecrire": "", "courrier_lire": "",
        "channel_list": "", "channel_create": "", "channel_edit": "", "channel_delete": SENSITIVE,
        # réflexes (une vérification « command » est rejugée par `senses/checks.py`)
        "reflex_list": "", "reflex_create": SENSITIVE, "reflex_enable": SENSITIVE, "reflex_delete": SENSITIVE,
    },
    # Serveur « connecteurs » (`mcp_connectors.py`) : avant le 04/11, inconnu donc sensible par son nom ;
    # la même gravité, écrite.
    "connecteurs": {
        "connectors_status": SENSITIVE, "calendar_upcoming": SENSITIVE, "calendar_create_event": SENSITIVE,
        "calendar_list_calendars": SENSITIVE, "transport_search_stop": SENSITIVE,
        "transport_departures": SENSITIVE, "transport_journey": SENSITIVE, "translate_text": SENSITIVE,
        "translate_usage": SENSITIVE,
    },
    # Navigateur principal (profil persistant) ET navigateurs jetables `playwright2..4` (`_mcp_table`) :
    # la même table ; un outil absent est jugé sur son nom (sensible au moins).
    "playwright": {
        "browser_file_upload": SENSITIVE, "browser_evaluate": SENSITIVE, "browser_fill_form": SENSITIVE,
        "browser_handle_dialog": SENSITIVE, "browser_drag": SENSITIVE,
    },
    # 05/11 (lot 5 du moteur local, accord explicite de l'utilisateur) : serveur du CLI LOCAL seul (`mcp_local.py`).
    # Libres ICI parce que `refused` (`_refus_outil_local`) a déjà refusé, sans carte, tout ce qui sort de la
    # liste blanche de l'utilisateur (id inconnu, argument ajouté) et tout appel hors du CLI local ; une proposition
    # n'écrit rien (l'utilisateur l'applique au HUD, le cœur rejuge le fichier).
    "astrel_local": {"liste_commandes": "", "commande_autorisee": "", "proposer_modification": ""},
    # 05/11 (lot 7 partie A du moteur local, autorisation de l'utilisateur pour ce chantier) : outils web du CLI LOCAL
    # (`mcp_web_local.py`). Lecture web = sensible (libre en autonome, demandée en prudent) ; le contrôle des
    # adresses (vues dans le tour, jamais le réseau privé, DNS vérifié à la connexion) est dans le serveur.
    "web_local": {"recherche_web": SENSITIVE, "lire_page": SENSITIVE},
}
# 05/11 (lot 7 partie A) : le navigateur DÉDIÉ au local (`mcp_web_local.declaration_playwright`) prend la table
# du navigateur principal, comme `playwright2..4` (aucun n'est plus libre que l'autre).
MCP_POLICY["playwright_local"] = MCP_POLICY["playwright"]
_PLAYWRIGHT = re.compile(r"playwright\d*")
# 04/11 (lot A, audit 2 T44-A) : `browser_run_code_unsafe` (@playwright/mcp, capacité « core », activée par
# défaut) exécute du JavaScript dans le PROCESS du serveur, « RCE-equivalent » selon ses auteurs : REFUSÉ
# d'office sur tout serveur (`refused`, avant toute carte), en dur ici en plus de `hooks/tool-policy.json`
# (un fichier de politique remplacé ou illisible ne le rouvre pas).
REFUSED_MCP_TOOLS = frozenset({"browser_run_code_unsafe"})
# 05/11 (lot 5 du moteur local, accord explicite de l'utilisateur) : liste blanche des commandes du modèle local
# (écrite par l'utilisateur seul, Réglages du HUD) et propositions de modification (écrites par `mcp_local.py`) :
# dans `PROTECTED_PATHS` ; `_refus_outil_local` les juge. Dossier du CLI local : `outils_local.CONFIG_CLI`.
COMMANDES_LOCALES_FILE = config.CONFIG_DIR / "commandes-locales.json"
PROPOSITIONS_LOCAL_DIR = config.RUNTIME / "propositions-local"
LOCAL_CLI_DIR = config.RUNTIME / "outils-local"
# 05/11 (essais réels du moteur local, accord de l'utilisateur pour ce chantier) : pids des CLI locaux en marche, écrits
# par le CŒUR seul (`outils_local.PIDS_CLI`, même chemin) ; protégé (`PROTECTED_PATHS`). Voir `_cli_local_reel`.
LOCAL_CLI_PIDS_FILE = config.RUNTIME / "outils-local-pids.json"
_ID_COMMANDE_LOCALE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,39}$")  # même motif que `commandes_locales.ID`
_ENTREES_LOCALES = {"liste_commandes": (set(), set()), "commande_autorisee": ({"id"}, {"id"}),
                    "proposer_modification": ({"fichier"}, {"fichier", "contenu", "ancien", "nouveau", "raison"})}
# `open_path` d'un fichier qui LANCE du code (lanceur, script, exécutable) : critique (xdg-open hors du
# cgroup du service, le programme lancé ne passerait plus pour Claude).
_OPEN_CODE_SUFFIXES = frozenset({
    ".desktop", ".sh", ".bash", ".zsh", ".fish", ".csh", ".ksh", ".py", ".pyw", ".pyz", ".pl", ".rb", ".php",
    ".js", ".mjs", ".cjs", ".jar", ".appimage", ".run", ".bin", ".exe", ".bat", ".cmd", ".com", ".ps1",
    ".psm1", ".vbs", ".vbe", ".wsf", ".msi", ".msix", ".lnk", ".url", ".hta", ".reg", ".scr", ".cpl",
    ".command", ".app", ".scpt", ".applescript", ".workflow", ".pkg", ".dmg", ".deb", ".rpm", ".flatpakref"})

# Verbes d'un outil de connecteur qui rendent l'action IRRÉVERSIBLE ou publique.
# Tout le reste (lire, chercher, étiqueter, archiver, brouillon, créer un
# événement…) est « sensible » : libre en mode autonome.
CRITICAL_VERBS = {
    "send", "forward", "reply", "delete", "remove", "purge", "destroy", "erase",
    "pay", "purchase", "buy", "checkout", "transfer", "refund", "charge",
    "deploy", "deployment", "deployments", "redeploy", "promote", "rollback", "publish", "release",
    "share", "invite", "unshare", "revoke", "cancel", "unsubscribe", "post",
}
# Ce qui ressemble à un verbe critique mais ne l'est pas.
HARMLESS_WITH_CRITICAL_VERB = {"delete_draft", "remove_label", "remove_labels"}
# Gestion des étiquettes Gmail (15/10, idée 2, injection par mail) : renommer,
# masquer (visibilité HIDE) ou supprimer une étiquette peut faire disparaître des
# mails de la vue de l'utilisateur. Historique réel des conversations au 15/10 : 0 appel
# de l'une comme de l'autre. create_label reste SENSIBLE : 5 créations le 26/09
# pour le tri (Travail, Administratif…), rangement normal ; label_thread/
# label_message aussi (47 appels de tri).
CRITICAL_MCP_TOOLS = {"update_label", "delete_label"}

# Écriture libre dans les projets ; ailleurs dans le dossier personnel :
# sensible ; dans les dossiers système ou les secrets : critique.
# 03/11 (portage) : le dossier temporaire de l'utilisateur est %TEMP% sous Windows et /var/folders/…/T sous
# macOS (TMPDIR posé par le système), pas /tmp. Linux : /tmp SEUL (TMPDIR y est libre de viser n'importe où ;
# le prendre élargirait en silence la zone où Claude écrit sans question).
TEMP_ROOTS = [Path("/tmp")] + ([Path(tempfile.gettempdir()).resolve()]
                               if os.name == "nt" or sys.platform == "darwin" else [])
ALLOWED_WRITE_ROOTS = [config.WORKDIR.resolve(), config.ROOT.resolve(), *TEMP_ROOTS]
# 15/10, partie B : fichiers du dossier personnel qui lancent du code ou ouvrent un
# accès (démarrage du shell et de la session, unités systemd, ~/.local/bin dans le
# PATH, réglages et hooks de Claude Code…). Y écrire, par Write/Edit ou par Bash
# (cp, mv, tee, ln, install, dd, sed -i, curl -o, > >>), est CRITIQUE.
# `~/.claude/projects` (mémoire et conversations de Claude) reste libre.
SENSITIVE_HOME = [Path.home() / p for p in (
    ".ssh", ".gnupg", ".bashrc", ".bash_profile", ".bash_login", ".bash_logout", ".bash_aliases", ".profile",
    ".zshrc", ".zshenv", ".zprofile", ".zlogin", ".zlogout", ".inputrc", ".xprofile", ".xinitrc", ".xsessionrc",
    ".pam_environment", ".config/environment.d", ".config/autostart", ".config/systemd", ".local/share/systemd",
    ".config/fish", ".local/bin", "bin", ".local/share/applications", ".gitconfig", ".config/git", ".netrc",
    ".npmrc", ".pypirc", ".docker/config.json", ".local/share/keyrings", ".password-store", ".config/crontab",
    ".claude.json", ".claude/settings.json", ".claude/settings.local.json", ".claude/CLAUDE.md", ".claude/hooks",
    ".claude/agents", ".claude/commands", ".claude/skills", ".claude/plugins", ".claude/.credentials.json",
    ".claude/keybindings.json",
)]
_UNIX_SYSTEM = ("/etc", "/usr", "/boot", "/var", "/opt", "/bin", "/sbin", "/lib")
# 03/11/2026 (portage Windows, autorisé par l'utilisateur) : sous Windows, une écriture système valait « sensible »
# (libre en autonome) ; même verdict que /etc désormais. Dossiers du système et des programmes ; dossier
# « Démarrage » (lance du code à l'ouverture de session, comme ~/.config/autostart) ; profils PowerShell
# (comme ~/.bashrc). Et « /etc »… écrit sans lecteur (« /etc/hosts » : Write de Claude Code, Git Bash) :
# critique sur chaque lecteur où il peut tomber (dossier courant, projets, Astrel, profil, système).
if os.name == "nt":
    _env = os.environ.get
    _LECTEURS = sorted({Path(p).drive.upper() for p in (os.getcwd(), config.WORKDIR, config.ROOT, Path.home(),
                                                        _env("SystemDrive", "C:") + "\\") if Path(p).drive})
    WINDOWS_SYSTEM = [Path(_env(v) or defaut) for v, defaut in (
        ("SystemRoot", "C:/Windows"), ("ProgramFiles", "C:/Program Files"),
        ("ProgramFiles(x86)", "C:/Program Files (x86)"), ("ProgramW6432", "C:/Program Files"),
        ("ProgramData", "C:/ProgramData"))] + [Path(d + r) for d in _LECTEURS for r in _UNIX_SYSTEM]
    SENSITIVE_HOME += [Path(_env("APPDATA") or Path.home() / "AppData" / "Roaming")
                       / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup",
                       Path.home() / "Documents" / "WindowsPowerShell", Path.home() / "Documents" / "PowerShell"]
else:
    WINDOWS_SYSTEM = []


# 04/11 (secrets du compte, voir `L_SECRET_UTILISATEUR`) : la liste UNIQUE des secrets du compte, par système.
@dataclass(frozen=True)
class SecretsCompte:
    """Chemins absolus, chacun sous sa forme écrite ET résolue (dossier personnel ou `~/.ssh` derrière un lien)."""
    ssh: tuple[Path, ...] = ()          # ~/.ssh : tout, sauf `_SSH_LIBRES` et les *.pub
    dossiers: tuple[Path, ...] = ()     # secrets en entier (trousseaux, coffres, jetons du cloud)
    fichiers: tuple[Path, ...] = ()     # fichiers secrets (.netrc, .aws/credentials…)
    navigateurs: tuple[Path, ...] = ()  # profils Chromium : `_NOMS_PARTOUT` dedans ; racines pour les parcours
    mozilla: tuple[Path, ...] = ()      # profils Firefox et Thunderbird : en plus `_NOMS_MOZILLA`

    @property
    def racines(self) -> tuple[Path, ...]:
        return self.ssh + self.dossiers + self.fichiers + self.navigateurs + self.mozilla


_SSH_LIBRES = ("known_hosts", "known_hosts.old", "known_hosts2", "config", "config.d", "authorized_keys",
               "authorized_keys2")
_SSH_CLES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ecdsa_sk", "id_ed25519", "id_ed25519_sk", "id_xmss")
# Secrets par leur NOM, où qu'ils soient : fichiers des navigateurs Chromium et des applications Electron
# (mots de passe, cookies de session, clé qui les chiffre), clés et cookies de Firefox, coffres KeePass,
# trousseaux macOS, clés PuTTY. Sous Windows sans casse (`_fn_nom`).
_NOMS_PARTOUT = ("Login Data", "Login Data-journal", "Login Data For Account", "Login Data For Account-journal",
                 "Cookies", "Cookies-journal", "Extension Cookies", "Extension Cookies-journal",
                 "Safe Browsing Cookies", "Safe Browsing Cookies-journal", "Web Data", "Web Data-journal",
                 "Local State", "key3.db", "key4.db", "cookies.sqlite", "cookies.sqlite-wal", "cookies.sqlite-shm",
                 "signons.sqlite", "*.kdbx", "*.keychain", "*.keychain-db", "*.ppk")
_NOMS_MOZILLA = ("logins.json", "logins-backup.json", "signedInUser.json")  # sous un profil Mozilla seulement
# Un nom représentatif par règle de nom, pour un joker dont le dossier est inconnu (`cat $X/*`).
_NOMS_TYPES = ("Login Data", "Cookies", "Web Data", "Local State", "key4.db", "cookies.sqlite", "x.kdbx",
               "x.keychain-db", "x.ppk")


def _secrets_compte(systeme: str, home: Path, env=None) -> SecretsCompte:
    """Secrets du compte pour `systeme` (« linux », « darwin », « nt ») ; `env` : variables (APPDATA…).
    `home` peut être un `PureWindowsPath` (tests : Windows simulé, comparaisons sans casse comme sous Windows)."""
    env = os.environ if env is None else env
    P = type(home)

    def h(*ps: str) -> list[Path]:
        return [home / p for p in ps]

    ssh = h(".ssh")
    dossiers = h(".gnupg/private-keys-v1.d", ".password-store", ".aws", ".azure", ".config/gcloud")
    fichiers = h(".gnupg/secring.gpg", ".kube/config", ".docker/config.json", ".netrc", ".git-credentials",
                 ".config/git/credentials", ".npmrc", ".pypirc", ".config/gh/hosts.yml", ".cargo/credentials",
                 ".cargo/credentials.toml", ".gem/credentials")
    nav: list[Path] = []
    moz: list[Path] = []
    if systeme == "nt":
        roaming = P(env.get("APPDATA") or home / "AppData" / "Roaming")
        local = P(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
        dossiers += [roaming / "Microsoft" / "Credentials", local / "Microsoft" / "Credentials",
                     roaming / "Microsoft" / "Protect", roaming / "Microsoft" / "Vault", local / "Microsoft" / "Vault",
                     roaming / "gcloud", roaming / "gnupg" / "private-keys-v1.d"]
        fichiers += [home / "_netrc", roaming / "GitHub CLI" / "hosts.yml", roaming / "gnupg" / "secring.gpg"]
        nav += [local / "Google" / "Chrome" / "User Data", local / "Chromium" / "User Data",
                local / "BraveSoftware" / "Brave-Browser" / "User Data", local / "Microsoft" / "Edge" / "User Data",
                local / "Vivaldi" / "User Data", roaming / "Opera Software"]
        moz += [roaming / "Mozilla", roaming / "Thunderbird"]
    elif systeme == "darwin":
        lib = home / "Library"
        sup = lib / "Application Support"
        dossiers += [lib / "Keychains", lib / "Cookies",
                     lib / "Containers" / "com.apple.Safari" / "Data" / "Library" / "Cookies"]
        nav += [sup / "Google" / "Chrome", sup / "Chromium", sup / "BraveSoftware", sup / "Microsoft Edge",
                sup / "Vivaldi", sup / "com.operasoftware.Opera"]
        moz += [sup / "Firefox", lib / "Thunderbird"]
    else:  # Linux et autres Unix
        dossiers += h(".local/share/keyrings", ".local/share/kwalletd", ".pki")
        nav += h(".config/google-chrome", ".config/google-chrome-beta", ".config/google-chrome-unstable",
                 ".config/chromium", ".config/BraveSoftware", ".config/microsoft-edge", ".config/vivaldi",
                 ".config/opera", "snap/chromium/common/chromium", ".var/app/com.google.Chrome",
                 ".var/app/org.chromium.Chromium", ".var/app/com.brave.Browser", ".var/app/com.microsoft.Edge")
        moz += h(".mozilla", "snap/firefox/common/.mozilla", ".var/app/org.mozilla.firefox/.mozilla", ".thunderbird",
                 "snap/thunderbird/common/.thunderbird", ".var/app/org.mozilla.Thunderbird/.thunderbird")

    def deux(ps: list[Path]) -> tuple[Path, ...]:
        out: list[Path] = []
        for p in ps:
            if not isinstance(p, Path):  # chemin « pur » d'un autre système (tests) : tel quel
                out.append(p)
                continue
            out.append(Path(os.path.normpath(p)))
            try:
                out.append(p.resolve())
            except (OSError, RuntimeError, ValueError):
                pass
        return tuple(dict.fromkeys(out))

    return SecretsCompte(deux(ssh), deux(dossiers), deux(fichiers), deux(nav), deux(moz))


_SYSTEME = "nt" if os.name == "nt" else ("darwin" if sys.platform == "darwin" else "linux")
_US = _secrets_compte(_SYSTEME, Path.home())
CRITICAL_WRITE_ROOTS = [Path(p).expanduser() for p in (
    *_UNIX_SYSTEM, "~/.ssh", "~/.gnupg", "~/.config/systemd", "~/.local/share/keyrings",
)] + WINDOWS_SYSTEM + SENSITIVE_HOME + list(_US.racines)  # 04/11 : + les secrets du compte (écriture critique)


# 24/10 (P29 lot B, demande de l'utilisateur) : cases de confirmation (Réglages › Garde-fou), écrites et
# SIGNÉES par le cœur seul (`limites.ecrire_cases`), clé HMAC (secret) et journal en ajout seul.
CONFIRM_CASES_FILE = config.RUNTIME / limites.FICHIER_CASES
CONFIRM_KEY_FILE = config.RUNTIME / limites.FICHIER_CLE
CONFIRM_CASES_LOG = config.AUDIT_LOG.parent / limites.JOURNAL_CASES
# 25/10 (audit p33-p34, M1 à M3) : arrêt des agents sur quota. Ordre, état et marques écrits par le
# cœur seul (`quota_arret.py`) et le hook des sous-agents (marques) ; clé HMAC de l'état (secret,
# `quota_arret.FICHIER_CLE`). Le code ne compte pas sur cette protection (garde-fou levable).
QUOTA_KEY_FILE = config.QUOTA_ARRET_FILE.with_name("quota-cle")

# Le garde-fou lui-même : Claude ne doit pouvoir ni changer le mode, ni se
# donner une autorisation, ni lever l'arrêt d'urgence, ni lancer ou élargir une
# mission, ni réécrire le hook — par Write/Edit, par Bash ou par le canal de
# contrôle. MODIFIER ces fichiers est TOUJOURS critique ; les LIRE est libre.
PROTECTED_PATHS = [p.resolve() for p in (
    CONFIRM_CASES_FILE, CONFIRM_KEY_FILE, CONFIRM_CASES_LOG,  # 24/10 (P29 lot B)
    MODE_FILE, GRANTS_FILE, config.ROOT / "astrel" / "guard.py", config.ROOT / "astrel" / "confirm.py",
    config.ROOT / "hooks", config.SETTINGS_FILE, config.CONFIRM_SOCKET, config.CONTROL_SOCKET,
    config.ESTOP_FILE, config.MISSION_FILE, config.HUD_TOKEN_FILE, config.HUD_SESSION_KEY_FILE, config.HUD_PROFILE_DIR,
    config.TOTAL_LOG,  # journal du mode « Autonome TOTAL » : en ajout seul, par le cœur
    config.ASTREL_SETTINGS_FILE,  # page « Réglages » du HUD (15/10) : écrite par le cœur seul
    config.PLANS_DIR,  # mode plan (16/10) : statut « validé » écrit par le cœur seul, sur clic du HUD
    # WhatsApp (17/10) : identifiants écrits par le pont seul, mode « absent » par le cœur seul
    config.WHATSAPP_AUTH_DIR, config.WHATSAPP_STATE_FILE,
    # 18/10 (plan P7) : journal de la levée du garde-fou et journal d'audit, en ajout seul par le cœur
    config.LIFT_LOG, config.AUDIT_LOG,
    # 22/10 (plan P27, accord de l'utilisateur) : configuration des fournisseurs CLI (hook, MCP) et
    # leur connexion ; écrite par le cœur seul, un modèle ne doit pas pouvoir retirer son hook.
    config.RUNTIME / "cli",
    # 25/10 (audit p33-p34, M1 à M3) : arrêt des agents sur quota (voir `QUOTA_KEY_FILE`).
    config.QUOTA_ARRET_FILE, config.QUOTA_ORDRE_FILE, config.QUOTA_ORDRE_DIR, QUOTA_KEY_FILE,
    # 01/11 (accord de l'utilisateur) : instantanés avant écriture (`instantanes.py` : un lot forgé ferait
    # rétablir n'importe quoi) et série de refus du disjoncteur (`hooks/permission_hook.py` : Claude ne
    # la remet pas à zéro). Écrits par les hooks seuls ; les LIRE reste libre.
    config.RUNTIME / "instantanes", config.RUNTIME / "garde-refus",
    # 01/11 (accord de l'utilisateur) : messages de l'utilisateur non traités avant un arrêt (`attente.py`, entrées
    # signées) : rejoués au démarrage comme venant de lui ; écrits et effacés par le cœur seul.
    config.ATTENTE_FILE,
    # 04/11 (lot A, accord de l'utilisateur ; audit 1 F2, audit 3 H1 et M3) : le profil donne le dossier des
    # projets (`config.WORKDIR`, donc où Claude écrit et supprime sans question, relu par le hook à chaque
    # appel) ; le micro choisi, ses seuils et le micro coupé (persistant, rallumé par l'utilisateur seul) : la
    # voix de l'utilisateur confirme et active Total. Écrits par le cœur ou l'installateur ; les LIRE reste libre.
    config.PROFILE_FILE, config.MIC_FILE, config.CONFIG_DIR / "mic_profiles.json", config.RUNTIME / "mic-muted",
    # 05/11 (lot 5 du moteur local, accord explicite de l'utilisateur) : commandes que le modèle local peut lancer
    # (écrites par le cœur sur un clic des Réglages du HUD) et ses propositions de modification (écrites par
    # `mcp_local.py`, appliquées par le cœur sur un clic). Les LIRE reste libre.
    COMMANDES_LOCALES_FILE, PROPOSITIONS_LOCAL_DIR,
    # 05/11 (essais réels du moteur local, accord de l'utilisateur pour ce chantier) : pids des CLI locaux, lus par le
    # hook pour laisser le modèle LOCAL lire une zone confidentielle ; un pid ajouté par Claude l'y laisserait lire.
    LOCAL_CLI_PIDS_FILE,
)]
REFUS_DIR = config.RUNTIME / "garde-refus"  # 01/11 : série de refus du disjoncteur (`hooks/permission_hook.py`)
# 18/10 (plan P7) : ce qu'une LEVÉE du garde-fou (Réglages du HUD, l'utilisateur seul) laisse
# ÉCRIRE ou ÉDITER sans question : le code sur lequel l'utilisateur fait travailler Claude.
# Jamais `totalgate.py`, `state.py`, `ipc.py`, `platform/proc.py`, `server.py` (qui décide,
# qui est Claude, qui est le HUD), ni les fichiers d'état et journaux de `PROTECTED_PATHS`,
# ni une suppression.
LIFTABLE = [p.resolve() for p in (
    config.ROOT / "astrel" / "guard.py", config.ROOT / "astrel" / "confirm.py", config.HOOK_SCRIPT,
    config.ROOT / "hooks" / "permission_hook_cli.py",  # 22/10 (plan P27) : le hook des fournisseurs CLI
    config.ROOT / "astrel" / "bubble.py", config.ROOT / "astrel" / "minichat.py",
    config.ROOT / "astrel" / "whatsapp.py", config.WHATSAPP_BRIDGE_DIR,
    config.HUD_DIR / "src", config.HUD_DIR / "dist", config.HUD_DIR / "index.html",
)]
# 21/10 : noms du garde-fou qui sont dans `LIFTABLE` (levables), tels que `PROTECTED_BASH` les capte.
_LIFTABLE_NAMES = frozenset({"guard.py", "confirm.py", "permission_hook", "permission_hook.py"})
# Suppression ou déplacement cités dans le texte : jamais levés, même pour un fichier levable.
_DELETES_TEXT = re.compile(r"(?<![\w./-])(?:rm|rmdir|unlink|shred|mv|truncate|find|rsync|git\s+(?:rm|clean|checkout|reset|restore))(?![\w-])"
                           r"|os\.(?:remove|unlink|rename|replace)|shutil\.(?:rmtree|move)|\.unlink\(|rmtree")
# Noms EXACTS (« repetition_guard.py » ou « test_guard.py » ne sont pas « guard.py »).
# 15/10 (lectures libres) : `totalgate.py` n'y est plus. Il est jugé avec le reste de
# `PROTECTED_CODE`, commande par commande (`_judge_cited`), toujours par son nom d'où
# qu'il soit cité : sinon un `grep … totalgate.py; ss -l` demandait.
_PROTECTED_NAMES = (r"autonomy\.json|grants\.json|mission\.json|guard\.py|confirm\.py|permission_hook(?:\.py)?"
                    r"|claude-settings\.json|confirm\.sock|control\.sock|ESTOP|hud-token|hud-session-key|hud-profile"
                    r"|autonome-total\.log|runtime/settings\.json|whatsapp\.json"
                    r"|garde-fou-leve\.log|audit\.jsonl"  # 18/10 (plan P7)
                    r"|confirmations\.json|confirmations\.log"  # 24/10 (P29 lot B) : cases et leur journal
                    # 25/10 (audit p33-p34) : arrêt sur quota (ordre, marques, état, clé) ; empreinte du
                    # garde-fou et politique d'outils (`hooks/`), aussi cités dans du code (perl -e, awk…).
                    r"|quota-ordre\.json|quota-ordre\.d|quota-arret\.json|quota-cle|empreinte\.json|tool-policy\.json"
                    r"|messages-en-attente\.json"  # 01/11 : messages de l'utilisateur en attente (`attente.py`)
                    r"|commandes-locales\.json|propositions-local")  # 05/11 : lot 5 du moteur local
# 16/10 : `runtime/plans` n'est plus cherché dans le texte brut de la commande (faux
# positifs : message de commit, `echo "runtime/plans" > /tmp/n`, `cp runtime/plans/P3.md
# /tmp/`, `tests/plans/P1.md`). Il est jugé commande par commande (`_plans_like`) :
# écriture, suppression, programme qui cite un plan sans seulement le lire.
# 18/10 (plan P7) : `settings_action` (lever ou rétablir le garde-fou, délier WhatsApp…).
_GUARD_COMMANDS = (r"autonomy|confirm|estop|stop-tout|stoptout|stop_tout|mission|total"
                   r"|plan_validate|plan_reject|plan_revise|plan_comment"  # 16/10 : commandes du HUD seul
                   r"|settings_action")
PROTECTED_BASH = re.compile(
    r"(?<![\w.-])(?:" + _PROTECTED_NAMES + r")(?![\w-])"
    r"|--send\s+[\"']?(?:" + _GUARD_COMMANDS + r")\b"
    r"|[\"']cmd[\"']\s*:\s*[\"'](?:" + _GUARD_COMMANDS + r")\b",
    re.I if os.name == "nt" else 0,  # 04/11 (lot A) : NTFS ignore la casse (« ASTREL\GUARD.PY »)
)
# Ce qui ne se LIT même pas : le jeton du HUD donne la main sur tout le HUD ; la clé
# de session (15/10) signe ses cookies, elle permettrait d'en fabriquer ; le profil
# Chrome du HUD garde le cookie lui-même. 17/10 : les identifiants WhatsApp valent le
# compte (lire, envoyer au nom de l'utilisateur), ses notes vocales sont privées.
# 19/10 (audit, accord de l'utilisateur) : les identifiants de Claude Code (jeton OAuth de
# l'abonnement) n'étaient protégés qu'en écriture (`SENSITIVE_HOME`). Aucun code de
# Astrel ne les lit (`compte.py` passe par `claude auth status`, le cœur n'est pas
# soumis au hook).
CLAUDE_CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
SECRET_PATHS = [config.HUD_TOKEN_FILE.resolve(), config.HUD_SESSION_KEY_FILE.resolve(),
                config.HUD_PROFILE_DIR.resolve(),
                config.WHATSAPP_AUTH_DIR.resolve(), config.WHATSAPP_MEDIA_DIR.resolve(),
                CLAUDE_CREDENTIALS.resolve(),
                # 20/10 (accord de l'utilisateur, P11) : `config/secrets.json` porte les clés API des
                # fournisseurs d'IA et les secrets des connecteurs ; le cœur et les serveurs MCP le
                # lisent hors du hook, le Bash de Claude n'en a pas besoin.
                config.SECRETS_FILE.resolve(),
                # 22/10 (plan P27, accord de l'utilisateur) : connexion des fournisseurs CLI (Codex, Grok
                # Build) à l'abonnement de l'utilisateur ; lue par le CLI lui-même, hors du hook.
                (config.RUNTIME / "cli").resolve(),
                # 24/10 (P29 lot B) : clé qui signe les cases de confirmation (la lire = pouvoir en forger).
                CONFIRM_KEY_FILE.resolve(),
                # 25/10 (audit p33-p34, M2) : clé qui signe l'état de l'arrêt sur quota.
                QUOTA_KEY_FILE.resolve(),
                # 27/10 (plan P40 A0, accord de l'utilisateur) : profils Chrome des comptes d'images et de
                # vidéos IA (cookies de session Google, OpenAI) ; lus par le worker du cœur, hors du hook.
                config.IMAGES_PROFILS.resolve(),
                # 04/11 (lot A, audit 2 F3, accord de l'utilisateur) : configuration MCP du CLI, relue à CHAQUE
                # démarrage du cerveau sans être réécrite (remise à zéro, changement de modèle) : elle
                # déclare des commandes lancées par le CLI hors de tout appel d'outil, et porte le jeton de
                # Home Assistant et les identifiants des connecteurs. Lecture = secret ; écriture = plafond
                # (comme `.mcp.json`, `CEILING_WRITE`). Écrite par le cœur seul (`mcp_config.write`).
                config.MCP_CONFIG_FILE.resolve()]
SECRET_BASH = re.compile(r"(?<![\w.-])(?:hud-token|hud-session-key|hud-profile|whatsapp-auth|whatsapp-media"
                         r"|\.credentials\.json|secrets\.json|runtime/cli|confirmations-cle|quota-cle"
                         r"|imagegen/profils"
                         r"|runtime/mcp\.json)(?![\w-])",  # 04/11 : le nom seul (« mcp.json ») sert ailleurs
                         re.I if os.name == "nt" else 0)  # 04/11 : NTFS ignore la casse
L_SECRET = ("la lecture d'un secret d'Astrel (jeton du HUD, identifiants ou médias WhatsApp, "
            "identifiants de Claude Code, clés API des fournisseurs)")
# 23/10 (plan P29, fiche 3 du relecteur ; trou ANCIEN) : ÉCRIRE dans un secret, ou dans les
# identifiants et réglages de Claude Code, est au PLAFOND (question même en Total, ni levée, ni
# « oui pour 30 min ») comme `limites.PLAFOND_TEXTES` l'annonce. Avant : Write/Edit de
# `config/secrets.json` et de `runtime/whatsapp-media/` étaient LIBRES (racine du projet dans
# `ALLOWED_WRITE_ROOTS`), et `~/.claude.json`, `.credentials.json`, `~/.claude/settings.json`
# seulement « écriture hors projets » (libre en Total). Les autres secrets (jeton du HUD,
# whatsapp-auth, runtime/cli) étaient déjà dans `PROTECTED_PATHS`. Ces CIBLES seulement : le
# reste de `config/` et de `runtime/` reste libre. Écrits par le cœur, le pont WhatsApp ou le
# CLI lui-même, jamais par un outil de Claude (hors du hook).
CLAUDE_SETTINGS_WRITE = [(Path.home() / p).resolve() for p in (".claude.json", ".claude/settings.json")] + [
    CLAUDE_CREDENTIALS.resolve()]
# 23/10 (plan P29, audit sécurité fiche 6, constat 2) : réglages et serveurs MCP du PROJET où
# tourne le CLI (dossier de travail, `cwd` du cerveau) et du dossier d'Astrel. Un
# `.claude/settings(.local).json` peut déclarer des hooks ou `disableAllHooks` (relus à chaud),
# un `.mcp.json` des serveurs. Avant : libres (dans un projet). Plafond, comme ceux du dossier
# personnel ; les autres projets et le reste de `.claude/` (agents, commandes) restent libres.
_PROJECT_ROOTS = list(dict.fromkeys(p.resolve() for p in (config.WORKDIR, config.ROOT)))
PROJECT_CLAUDE_DIRS = [d / ".claude" for d in _PROJECT_ROOTS]
PROJECT_CLAUDE_WRITE = [d / ".claude" / n for d in _PROJECT_ROOTS for n in ("settings.json", "settings.local.json")] \
    + [d / ".mcp.json" for d in _PROJECT_ROOTS]
CEILING_WRITE = list(dict.fromkeys([*SECRET_PATHS, *CLAUDE_SETTINGS_WRITE, *PROJECT_CLAUDE_WRITE]))
# 23/10 (plan P29, audit fiche 6, constat 1) : code que l'interpréteur charge À CÔTÉ du code
# protégé : la venv entière (interpréteur, pyvenv.cfg, site-packages : sitecustomize.py, .pth)
# et le bytecode du paquet `astrel` (un .pyc forgé passe pour son source, guard.py compris).
# Écrire ici : plafond. SUPPRIMER reste libre (nettoyer un cache, recréer la venv) : le
# bytecode est recompilé, et le hook refuse si la venv manque (`config.hook_command`). Le
# hook lui-même tourne en mode isolé (-I, -S, pycache_prefix) et ne lit ni l'un ni l'autre ;
# le cœur, les serveurs MCP et les autres hooks, si.
VENV_DIR = (config.ROOT / ".venv").resolve()
_ASTREL_PKG = (config.ROOT / "astrel").resolve()
_PYC = (".pyc", ".pyo")


def _loaded_code(p: Path) -> bool:
    """`p` (absolu, normalisé) est-il dans la venv, ou du bytecode du paquet `astrel` ?"""
    if _under(p, [VENV_DIR]):
        return True
    return _ASTREL_PKG in p.parents and ("__pycache__" in p.relative_to(_ASTREL_PKG).parts or p.suffix in _PYC)


def _ceiling_write_label(p: Path) -> str | None:
    """Étiquette (plafond) d'une écriture dans `p` (absolu, normalisé), ou None. Étiquettes déjà
    traduites (`locales/en.json`) : aucun texte d'écran nouveau."""
    if _under(p, CLAUDE_SETTINGS_WRITE) or _under(p, PROJECT_CLAUDE_WRITE) or p in PROJECT_CLAUDE_DIRS:
        return L_CLAUDE_ID
    if _under(p, CEILING_WRITE) or _loaded_code(p):
        return f"une modification de {p.name}, fichier protégé d'Astrel"
    return None


def _ceiling_write_cat(p: Path) -> str:
    """24/10 (P29 lot B) : catégorie (case) d'une écriture au plafond dans `p` (voir ci-dessus)."""
    if _under(p, CLAUDE_SETTINGS_WRITE) or _under(p, PROJECT_CLAUDE_WRITE) or p in PROJECT_CLAUDE_DIRS:
        return limites.IDENTIFIANTS_CLAUDE
    if _under(p, SECRET_PATHS):
        return limites.SECRETS
    return limites.CODE_IDENTITE  # venv, bytecode de `astrel`


def _ceiling_cat(target: str) -> str:
    """Comme `_ceiling_write_cat`, sur le chemin résolu puis NON résolu (lien de la venv)."""
    p = _resolve(target)
    if _ceiling_write_label(p):
        return _ceiling_write_cat(p)
    return _ceiling_write_cat(Path(os.path.normpath(_base() / os.path.expanduser(_msys(target.strip().strip("'\""))))))


def _ceiling_raw(c: str, ctx) -> str | None:
    """Comme `_ceiling_write_label`, sur le chemin NON résolu de `c` (liens symboliques gardés) :
    `.venv/bin/python3` est un lien vers /usr/bin, le remplacer (`ln -sf`, `cp --remove-destination`)
    change l'interpréteur du hook alors que le chemin résolu tombe hors de la venv."""
    if not c or _DYN in c or _GLOB.search(c) or c.startswith("-"):
        return None
    c = os.path.expanduser(_msys(c))
    for b in ([Path("/")] if c.startswith("/") else (ctx.cwds or [])):
        raw = Path(os.path.normpath(Path(b) / c))
        label = _ceiling_write_label(raw)
        if label and raw.resolve() != raw:  # sans lien en chemin : le chemin résolu suffit (étiquettes inchangées)
            return label
    return None


# 23/10 (plan P29, audit fiche 6, constat 3) : `~/.claude.json` et les réglages de Claude Code
# (les siens, ceux du projet, `.mcp.json`) moins protégés que `.credentials.json` : un lien dur
# (`ln ~/.claude/settings.json /tmp/s`, puis écrire dans /tmp/s), `python3 -c "open(…, 'w')"`,
# `truncate`, `rm`, `chmod` passaient (libres, ou critiques donc accordés par Total). Désormais
# tout programme qui les CITE sans seulement les lire (lecteurs sûrs, `cp` qui lit sa source),
# ou du code qui les cite et peut écrire : plafond. `.claude.json` est reconnu par son NOM, où
# qu'il soit (`Path.home() / ".claude.json"` dans un script).
_CLAUDE_NAMED = ".claude.json"
_VENV_BIN = re.compile(r"(?:^|/)\.venv/bin(?:/|$)")  # limite connue : du code qui remplace l'interpréteur
_CEILING_CITED_NAMES = (_CLAUDE_NAMED, "settings.json", "settings.local.json", ".mcp.json")


def _cited_ceiling(texts: list[str], ctx, code: bool = False) -> str:
    """Étiquette (plafond) si ces textes (mots d'une commande, code d'un script) citent un
    réglage de Claude Code ou, pour du `code`, un fichier de la venv ou du bytecode de `astrel`.
    Le bytecode et la venv ne comptent pas pour une commande : `rm -rf astrel/__pycache__` et
    `.venv/bin/pip install` restent libres."""
    if ctx.remote:
        return ""
    for t in texts:
        if _FOUND in t:  # « {} » d'un find, entrée d'un xargs : jugés avec leurs filtres (`_claude_found`)
            continue
        words = _PATH_WORD.findall(t)
        for w in words:
            if not (any(n in w for n in _CEILING_CITED_NAMES)
                    or (code and (".venv" in w or "__pycache__" in w or w.endswith(_PYC)))):
                continue
            if w.rsplit("/", 1)[-1] == _CLAUDE_NAMED:
                return L_CLAUDE_ID
            if _DYN in w or _VENV_BIN.search(w):  # `.venv/bin/python` cité dans un texte (README, aide)
                continue
            for p in _paths(w, ctx) or []:
                label = _ceiling_write_label(p) or (_ceiling_raw(w, ctx) if code else None)
                if label and (code or not _loaded_code(p)) and not _under(p, SECRET_PATHS):
                    return label  # les secrets : déjà jugés par `_judge_secret` (lecture comprise)
    return ""


def _reaches_secret(c: str, cwds: list | None, glob: bool, secrets: list | None = None) -> bool:
    """15/10 : un argument (valeur développée) qui PEUT désigner le jeton, la clé de
    session ou le profil du HUD sans les nommer : joker (`glob`), partie inconnue
    (`_DYN`, prise comme un joker), accolades ou guillemets déjà développés. Début
    inconnu (`$X/…`) ou dossier courant inconnu : n'importe quel dossier.
    04/11 : `secrets` = autre liste concrète (secrets du compte, `_cibles_compte`)."""
    cibles = SECRET_PATHS if secrets is None else secrets
    if c.startswith("-"):
        c = c.split("=", 1)[1] if "=" in c else ""  # --fichier=…
    if not c or "://" in c:
        return False
    pat = c if glob else re.sub(r"([*?\[])", r"[\1]", c)  # joker cité : un caractère comme un autre
    pat = _msys(os.path.expanduser(pat))  # 03/11 : « /c/Users/… » de Git Bash
    unknown_start = c.startswith(_DYN)  # « $X/… » : X peut être n'importe quel dossier

    def match(name: str, part: str) -> bool:
        # 19/10 : un joker du shell (bash, zsh) ne désigne pas un fichier caché sauf s'il
        # commence par « . » (`ls ~/.claude/*` ne lit pas `.credentials.json`) ; une
        # partie inconnue (`$X`), si.
        if name.startswith(".") and not part.startswith((".", _DYN)):
            return False
        return _fn_nom(name, part.replace(_DYN, "*"))

    if unknown_start or (not _abs_txt(pat) and cwds is None):
        rest = [x for x in pat.split("/")[1 if unknown_start else 0:] if x not in ("", ".")]
        return any(len(rest) >= len(s.parts) - k and rest
                   and all(match(x, y) for x, y in zip(s.parts[k:], rest))
                   for s in cibles for k in range(1, len(s.parts)))
    for b in ([_racine()] if _abs_txt(pat) else cwds):
        parts = Path(os.path.normpath(Path(b) / pat)).parts
        # 01/11 (relecture, accord de l'utilisateur) : `cat w/creds.json` avec `w` → runtime/whatsapp-auth
        # passait (chemin jugé sans résoudre les liens). En PLUS du chemin écrit : la partie fixe
        # (avant tout joker ou partie inconnue) résolue sur le disque, le reste tel quel.
        for cand in (parts, _resolved_parts(parts)):
            if cand and any(len(cand) >= len(s.parts) and all(match(x, y) for x, y in zip(s.parts, cand))
                            for s in cibles):
                return True
    return False


def _resolved_parts(parts: tuple) -> tuple:
    """`parts` avec sa partie FIXE (avant le premier joker ou morceau inconnu) résolue (liens
    suivis) ; () si rien ne change ou en cas d'erreur (le chemin écrit reste jugé de toute façon)."""
    k = next((i for i, x in enumerate(parts) if _DYN in x or _GLOB.search(x)), len(parts))
    if k <= 1:
        return ()
    try:
        fixe = Path(*parts[:k]).resolve()
    except (OSError, RuntimeError, ValueError):
        return ()
    out = fixe.parts + tuple(parts[k:])
    return out if out != tuple(parts) else ()


def _judge_secret(xs: list, ctx, out: list, prog: str = "") -> None:
    if ctx.remote:
        return
    pattern = -1  # grep/rg/sed : le MOTIF peut citer le nom en clair (comme `_reads_secret`)
    if prog in _PATTERN_FIRST:
        texts = [x.text for x in xs]
        flags = [k for k, t in enumerate(texts[:-1]) if t in ("-e", "--regexp", "--expression")]
        pattern = flags[0] + 1 if flags else next((k for k, t in enumerate(texts) if not t.startswith("-")), -1)
    for k, x in enumerate(xs):
        if k == pattern and not x.glob and not x.dyn:
            continue
        if any(_reaches_secret(c, ctx.cwds, x.glob) for c in x.cands):
            _add(out, L_SECRET, guard=True)
            return


# 19/10 (relecture, accord de l'utilisateur) : le dossier `~/.claude` ENTIER contient
# `.credentials.json`. `grep -r accessToken ~/.claude`, `cp -r ~/.claude /tmp/x`,
# `tar cf - ~/.claude`, `find ~/.claude -exec cat {} +`, `cd ~/.claude && grep -r . .`
# le lisaient sans le nommer. Tout programme qui vise le dossier lui-même (ou y tourne et
# le parcourt) demande, comme une lecture du secret ; seules les simples listes restent
# libres. Ses sous-dossiers (`projects`, `skills`…) et fichiers nommés restent libres.
CLAUDE_DIR = CLAUDE_CREDENTIALS.parent
_CLAUDE_DIRS = {CLAUDE_DIR, CLAUDE_DIR.resolve()}
_DIR_LISTERS = {"ls", "stat", "du", "find", "test", "[", "[[", "cd", "pushd", "echo", "printf", "realpath",
                "readlink", "dirname", "basename", "tree", "file", "mkdir", "exa", "eza", "lsd", "namei", "df"}
_FIND_NAME_FILES = {"-fprint", "-fprint0", "-fprintf", "-fls"}
_GREPS = {"grep", "egrep", "fgrep"}
_RECURSIVE_SEARCH = {"rg", "ag", "ack", "ack-grep"}  # parcourent le dossier courant sans chemin donné


def _dir_part_match(name: str, part: str) -> bool:
    # comme `_reaches_secret` : un joker ne désigne un nom caché que s'il commence par « . »
    if name.startswith(".") and not part.startswith((".", _DYN)):
        return False
    return _fn_nom(name, part.replace(_DYN, "*"))


# 03/11/2026 (portage Windows, autorisé par l'utilisateur ; « jokers encore Unix » de la première CI) : NTFS ignore la
# casse (« RUNTIME/Hud-Token » est le jeton), un chemin « C:/… » est absolu, « /x » vise le lecteur courant.
_WIN_ABS = re.compile(r"[A-Za-z]:[\\/]")


def _fn_nom(name: str, part: str) -> bool:
    if os.name == "nt":
        return fnmatch.fnmatchcase(name.lower(), part.lower())
    return fnmatch.fnmatchcase(name, part)


def _abs_txt(pat: str) -> bool:
    return pat.startswith("/") or (os.name == "nt" and bool(_WIN_ABS.match(pat)))


def _racine() -> Path:
    return Path(os.path.abspath(os.sep)) if os.name == "nt" else Path("/")


def _designates_claude_dir(c: str, bases: list | None, glob: bool, dirs=_CLAUDE_DIRS) -> bool:
    """L'argument `c` peut-il désigner EXACTEMENT `~/.claude` (joker, `..`, `$X/.claude` compris) ?
    22/10 (P27) : `dirs` = autres dossiers (ancêtres d'un secret)."""
    if c.startswith("-"):
        c = c.split("=", 1)[1] if "=" in c else ""  # --directory=…
    if not c or "://" in c:
        return False
    pat = _msys(os.path.expanduser(c if glob else re.sub(r"([*?\[])", r"[\1]", c)))  # 03/11 : Git Bash
    unknown_start = c.startswith(_DYN)
    for d in dirs:
        if unknown_start or (not _abs_txt(pat) and bases is None):
            rest = [x for x in pat.split("/")[1 if unknown_start else 0:] if x not in ("", ".")]
            if rest and ".." not in rest and len(rest) < len(d.parts) and all(
                    _dir_part_match(x, y) for x, y in zip(d.parts[-len(rest):], rest)):
                return True
            continue
        for b in ([_racine()] if _abs_txt(pat) else bases):
            parts = Path(os.path.normpath(Path(b) / pat)).parts
            if len(parts) == len(d.parts) and all(_dir_part_match(x, y) for x, y in zip(d.parts, parts)):
                return True
    return False


def _judge_claude_dir(prog: str, xs: list, ctx, out: list) -> None:
    """Carte « lecture d'un secret » pour un programme qui vise tout `~/.claude` (voir plus haut).
    `prog` vide : programme inconnu."""
    if ctx.remote:
        return
    a = [x.text for x in xs]
    if prog in _DIR_LISTERS and not (prog == "find" and _FIND_NAME_FILES & set(a)):
        return
    # Dedans (`cd ~/.claude`) : une recherche récursive sans chemin, un interpréteur (son code
    # peut lister le dossier) parcourent le dossier courant.
    if any(c in _CLAUDE_DIRS for c in ctx.cwds or []) and (
            prog in _RECURSIVE_SEARCH or (prog and _INTERP.fullmatch(prog)) or (prog in _GREPS and any(
                t in ("--recursive", "--dereference-recursive", "--directories=recurse")
                or re.fullmatch(r"-[a-zA-Z]*[rR][a-zA-Z]*", t) for t in a))):
        _add(out, L_SECRET, guard=True, cat=_L.IDENTIFIANTS_CLAUDE)  # 24/10 (P29 lot B) : tout ~/.claude
        return
    pattern = -1  # motif de grep/rg (`grep -r .claude docs/`) : pas un chemin
    if prog in _PATTERN_FIRST | _RECURSIVE_SEARCH:
        flags = [k for k, t in enumerate(a[:-1]) if t in ("-e", "--regexp", "--expression")]
        pattern = flags[0] + 1 if flags else next((k for k, t in enumerate(a) if not t.startswith("-")), -1)
    bases, skip = ctx.cwds, set()
    if prog in ("tar", "bsdtar"):  # `tar -C ~ -czf x .claude` : les opérands suivent -C
        for k, t in enumerate(a):
            d = a[k + 1] if t in ("-C", "--directory") and k + 1 < len(a) else (
                t.split("=", 1)[1] if t.startswith("--directory=") else None)
            if d is not None:
                skip.add(k + 1 if t in ("-C", "--directory") else k)
                ps = None if (_DYN in d or _FOUND in d) else _paths(d, ctx)  # `-C $d` : dossier inconnu
                bases = None if ps is None or bases is None else bases + ps
    for k, x in enumerate(xs):
        if k in skip or (k == pattern and not x.glob and not x.dyn):
            continue
        for c in x.cands:
            if _FOUND in c:  # « {} » d'un find, entrée d'un xargs : les fichiers de ces dossiers
                hit = any(r in _CLAUDE_DIRS for r in ctx.found or [])
            else:
                hit = _designates_claude_dir(c, bases, x.glob)
            if hit:
                _add(out, L_SECRET, guard=True, cat=_L.IDENTIFIANTS_CLAUDE)  # 24/10 (P29 lot B)
                return


# 22/10 (plan P27, fiche 3 du relecteur, accord de l'utilisateur) : un dossier ANCÊTRE d'un secret
# (`runtime`, `config`, la racine du projet, `~`, `~/Projets`, `/`…) PARCOURU par un lecteur
# récursif lit le secret sans le nommer : `grep -r x runtime`, `grep -rn token .` depuis la
# racine, `rg token`, `find runtime -name auth.json -exec cat {} +`, `find … -print0 | xargs -0
# cat`, `cat $(find runtime -name auth.json)`, `cp -r runtime /tmp/x`, `tar czf /tmp/c.tgz config`.
# Carte « lecture d'un secret », comme pour `~/.claude`. Restent libres : un sous-dossier sans
# secret (`grep -r x astrel/ tests`), les simples listes (ls -R, du, tree, find sans -exec,
# `rg --files`, `find … -exec ls`), un fichier nommé. Un dossier inconnu (`cd $X`, `find $X`)
# parcouru en entier demande aussi.
_SECRET_ANCESTORS = frozenset(a for s in (*SECRET_PATHS, CLAUDE_CREDENTIALS, config.SECRETS_FILE,
                                          config.RUNTIME / "cli") for a in s.parents)
_SEARCHERS = {"rg", "ag", "ack", "ack-grep", "ugrep", "ug", "pt", "sift"}  # parcourent toujours les dossiers
_GREP_LONG_VAL = {"--regexp", "--file", "--max-count", "--after-context", "--before-context", "--context",
                  "--directories", "--devices", "--label", "--include", "--exclude", "--exclude-dir",
                  "--exclude-from", "--binary-files", "--group-separator"}
_RG_LONG_VAL = _GREP_LONG_VAL | {"--glob", "--iglob", "--type", "--type-not", "--threads", "--max-columns",
                                 "--encoding", "--replace", "--max-depth", "--max-filesize", "--sort", "--sortr",
                                 "--type-add", "--pre", "--pre-glob", "--colors", "--color", "--ignore-file",
                                 "--path-separator", "--context-separator", "--engine", "--ignore",
                                 "--ignore-dir", "--depth", "--path-to-ignore", "--file-type", "--file-extension",
                                 "--include-dir"}
# programme → (options courtes suivies d'une valeur, options longues suivies d'une valeur)
_TREE_OPTS = {"grep": ("efmABCdD", _GREP_LONG_VAL), "rg": ("efgtTmABCjMErd", _RG_LONG_VAL),
              "cp": ("tS", {"--target-directory", "--suffix"}), "rsync": ("efBT", {"--rsh", "--exclude",
                                                                                     "--include", "--filter"}),
              "scp": ("cFiJlmoPS", set()), "zip": ("bntx", set()), "diff": ("CUIxXSFL", {"--label"}),
              "tar": ("fCbTXHIKNLVg", {"--file", "--directory", "--files-from", "--exclude-from", "--exclude"})}


def _opts(a: list[str], short_val: str, long_val: set) -> tuple[set, list[tuple[str, str]], list[int]]:
    """(lettres et options longues vues, (option, valeur), indices des opérandes)."""
    flags: set = set()
    vals: list[tuple[str, str]] = []
    ops: list[int] = []
    i = 0
    while i < len(a):
        t = a[i]
        if t == "--":
            ops += range(i + 1, len(a))
            break
        if t.startswith("--"):
            name, eq, v = t.partition("=")
            flags.add(name)
            if eq:
                vals.append((name, v))
            elif name in long_val and i + 1 < len(a):
                vals.append((name, a[i + 1]))
                i += 1
        elif t.startswith("-") and len(t) > 1:
            for k, ch in enumerate(t[1:]):
                flags.add(ch)
                if ch in short_val:
                    v = t[k + 2:]
                    if not v and i + 1 < len(a):
                        v = a[i + 1]
                        i += 1
                    vals.append((ch, v))
                    break
        else:
            ops.append(i)
        i += 1
    return flags, vals, ops


def _tree_sources(prog: str, a: list[str]) -> tuple[list[int], bool, list[str]] | None:
    """Ce qu'un lecteur RÉCURSIF parcourt : (indices des dossiers ou fichiers lus, « sans chemin =
    le dossier courant », valeurs de `-C`) ; None : pas un lecteur récursif."""
    if prog in ("egrep", "fgrep"):
        prog = "grep"
    if prog in _SEARCHERS:
        short_val, long_val = _TREE_OPTS["rg" if prog == "rg" else "grep"]
        if prog != "rg":
            short_val += "gGtOMNp"
            long_val = _RG_LONG_VAL
    elif prog == "bsdtar":
        short_val, long_val = _TREE_OPTS["tar"]
    elif prog in _TREE_OPTS:
        short_val, long_val = _TREE_OPTS[prog]
    elif prog in ("7z", "7za", "7zz"):
        short_val, long_val = "", set()
    else:
        return None
    if prog in ("tar", "bsdtar") and a and re.fullmatch(r"[A-Za-z]+", a[0]):
        a = ["-" + a[0]] + a[1:]  # forme ancienne : `tar czf x.tgz dossier`
    flags, vals, ops = _opts(a, short_val, long_val)
    if prog in ("grep", *_SEARCHERS):
        if prog == "grep" and not ({"r", "R", "--recursive", "--dereference-recursive"} & flags or any(
                n in ("d", "--directories") and v == "recurse" for n, v in vals)):
            return None
        if "--files" in flags:  # `rg --files` : des noms seulement
            return None
        if not {"e", "f", "--regexp", "--file"} & flags:
            ops = ops[1:]  # le premier opérande est le motif
        return ops, True, [], _tree_filters(prog, vals)
    if prog == "diff":
        return (ops, False, [], _NO_FILTER) if {"r", "--recursive"} & flags else None
    if prog in ("cp", "rsync", "scp"):
        rec = {"cp": {"r", "R", "a", "--recursive", "--archive"}, "rsync": {"r", "a", "--recursive", "--archive"},
               "scp": {"r"}}[prog]
        if not rec & flags:
            return None
        to_dir = any(n in ("t", "--target-directory") for n, _v in vals)
        return (ops if to_dir else ops[:-1]), False, [], (_tree_filters(prog, vals) if prog == "rsync" else _NO_FILTER)
    if prog == "zip":
        recursive = {"r", "R", "--recurse-paths", "--recurse-patterns"} & flags
        return (ops[1:], False, [], _NO_FILTER) if recursive else None
    if prog in ("7z", "7za", "7zz"):
        return (ops[2:], False, [], _NO_FILTER) if a and a[0] == "a" else None
    # tar : seulement en création ; `-C d` change le dossier des opérandes qui suivent
    if not {"c", "r", "u", "A", "--create", "--append", "--update", "--catenate", "--concatenate"} & flags:
        return None
    return ops, False, [v for n, v in vals if n in ("C", "--directory")], _tree_filters(prog, vals)


# Filtres de noms d'un grep -r / rg qui écartent un secret À COUP SÛR (le rejeu du corpus : la
# plupart des `grep -rn … .` depuis la racine ou le dossier des projets portent `--include=*.py`
# ou `--exclude-dir=runtime`). (noms inclus, noms exclus, dossiers exclus). Un dossier secret
# (runtime/cli, profil du HUD, WhatsApp) a des fichiers aux noms inconnus : seul un filtre
# « code seulement » (`--include=*.py`, `rg -t py`) ou un dossier exclu l'écarte ; on admet
# qu'un fichier de code n'y porte pas de secret (jetons : json, toml, sans extension, sqlite).
_NO_FILTER: tuple = ((), (), ())
_CODE_EXTS = frozenset({
    "py", "pyi", "js", "mjs", "cjs", "jsx", "ts", "tsx", "mts", "cts", "vue", "svelte", "astro", "css", "scss",
    "sass", "less", "html", "htm", "md", "mdx", "rst", "php", "rb", "go", "rs", "java", "kt", "kts", "swift", "c",
    "h", "cc", "cpp", "hpp", "cs", "sh", "bash", "zsh", "fish", "lua", "pl", "dart", "scala", "sql", "svg"})
_RG_CODE_TYPES = {"python": "py", "typescript": "ts", "markdown": "md", "rust": "rs", "ruby": "rb",
                  "kotlin": "kt", "cpp": "cpp", "web": "html"}


def _code_glob(g: str) -> bool:
    """`*.py`, `test_*.py`, `SKILL.md` : seulement des fichiers de code ou de texte d'une de ces extensions."""
    m = re.fullmatch(r"[^/\[\]]*\.(\w+)", g)
    return bool(m) and m.group(1) in _CODE_EXTS


def _excluded_name(g: str) -> str | None:
    """`!runtime`, `runtime/`, `runtime/**`, `**/config/*` : ce nom exclu à toute profondeur ; None sinon."""
    g = re.sub(r"(?:/\*\*?)?/?$", "", re.sub(r"^(?:\*\*/)+", "", g))
    return g if g and "/" not in g else None


def _tree_filters(prog: str, vals: list[tuple[str, str]]) -> tuple:
    inc: list[str] = []
    exc: list[str] = []
    exd: list[str] = []
    for n, v in vals:
        if prog in ("rsync", "tar", "bsdtar"):  # leur --include n'est pas une liste blanche
            if n in ("--include", "--include-from", "--filter", "--exclude-from") or (prog == "rsync" and n == "f"):
                return _NO_FILTER  # `--include=config/secrets.json --exclude=config` : la règle d'avant gagne
            name = None if (n != "--exclude" or v.startswith("/")) else _excluded_name(v)  # `/x` : ancré
            if name:
                exd.append(name)
                exc.append(name)
        elif n == "--include":
            inc.append(v)
        elif n == "--exclude":
            exc.append(v)
        elif n == "--exclude-dir":
            exd.append(v)
        elif prog == "rg" and n in ("t", "--type"):
            ext = v if v in _CODE_EXTS else _RG_CODE_TYPES.get(v)
            inc.append(f"*.{ext}" if ext else "*")
        elif prog == "rg" and n in ("g", "--glob", "--iglob"):
            if v.startswith("!"):
                name = _excluded_name(v[1:])
                if name:  # `!runtime`, `!runtime/**`, `!**/config/` : ce nom, partout
                    exd.append(name)
                    exc.append(name)
            else:
                last = v.rsplit("/", 1)[-1]
                inc.append(last if _code_glob(last) else "*")
    return tuple(inc), tuple(exc), tuple(exd)


_SECRET_FILES = frozenset(p.resolve() for p in (config.HUD_TOKEN_FILE, config.HUD_SESSION_KEY_FILE,
                                                CLAUDE_CREDENTIALS, config.SECRETS_FILE,
                                                config.MCP_CONFIG_FILE))  # 04/11 (lot A)
_SECRET_ALL = tuple(dict.fromkeys([*SECRET_PATHS, CLAUDE_CREDENTIALS]))


def _secret_skipped(s: Path, rel: tuple | None, filt: tuple) -> bool:
    """Le secret `s` (chemin `rel` sous le dossier parcouru, None : inconnu) est-il écarté par les filtres ?"""
    inc, exc, exd = filt
    is_file = s in _SECRET_FILES
    if rel is not None and any(fnmatch.fnmatchcase(d, g) for d in (rel[:-1] if is_file else rel) for g in exd):
        return True
    if not is_file:
        return bool(inc) and all(_code_glob(g) for g in inc)
    if inc:  # inclus ET exclus : l'ordre décide chez grep ; dans le doute, lu
        return not any(fnmatch.fnmatchcase(s.name, g) for g in inc)
    return any(fnmatch.fnmatchcase(s.name, g) for g in exc)


def _scope_reaches_secret(p: Path, pattern: str | None, filt: tuple) -> bool:
    """Parcourir `p` (motif `pattern` du premier niveau) lit-il un secret malgré les filtres ?"""
    for s in _SECRET_ALL:
        if p == s or s in p.parents:
            return True  # le secret lui-même (lien symbolique résolu), ou dedans
        if p not in s.parents:
            continue
        rel = s.relative_to(p).parts
        if pattern is not None and not _dir_part_match(rel[0], pattern):
            continue
        if not _secret_skipped(s, rel, filt):
            return True
    return False


def _sub_paths(text: str, ctx) -> tuple[str, list] | None:
    """Ce que `$(…)` écrit, quand ce sont des chemins connus : ("found", racines) pour find, ls,
    grep -l… (des fichiers sous ces racines) ; ("text", chemins) pour echo, printf. None : inconnu."""
    if ctx.depth > _MAX_DEPTH:
        return None
    try:
        items = _Parser(_lex(text)).parse()
    except (_Unparsable, RecursionError):
        return None
    if len(items) != 1:
        return None
    node = items[0][0]
    stages = node[1] if node[0] == "pipe" else [node]
    if not stages or stages[0][0] != "cmd":
        return None
    _changed, feed = _eval_node(stages[0], _deeper(ctx), [], None)  # le premier étage donne les noms
    if feed and feed[0] == "found" and feed[1] is not None:
        return "found", feed[1]
    if feed and feed[0] == "text" and len(stages) == 1:
        try:
            words = shlex.split(feed[1] or "")[:_MAX_CANDS]
        except ValueError:
            return None
        paths: list = []
        for w in words:
            ps = _paths(w, ctx) if w else None
            if ps is None:
                return None
            paths += ps
        return "text", paths
    return None


def _reaches_tree(roots: list | None) -> bool:
    """Des racines (find, xargs) qui contiennent un secret (inconnues : non, comme pour `~/.claude`)."""
    return any(r in _SECRET_ANCESTORS or _under(r, SECRET_PATHS) for r in roots or [])


_NO_CONTENT = {"rm", "rmdir", "unlink", "shred", "chmod", "chown", "chgrp", "touch", "wc"}  # jugés ailleurs


_WIDE = limites.SECRETS_LARGES  # 24/10 (P29 lot B) : case « recherche récursive… », cochée par défaut


def _judge_secret_tree(prog: str, xs: list, ctx, out: list) -> None:
    """Carte « lecture d'un secret » pour un lecteur récursif sur un ancêtre d'un secret (voir plus haut)."""
    if ctx.remote or prog in _DIR_LISTERS or prog in _NO_CONTENT:
        return
    subs: dict[int, tuple[str, list]] = {}  # opérande fait d'un seul `$(…)` connu
    for k, x in enumerate(xs):  # « {} » d'un find -exec, entrée d'un xargs ; `cat $(find runtime …)`
        if any(_FOUND in c for c in x.cands) and _reaches_tree(ctx.found):
            _add(out, L_SECRET, guard=True, cat=_L.SECRETS)  # fichiers choisis par un find : ciblé
            return
        if x.dyn and len(x.subs) == 1 and x.cands == [_DYN]:
            got = _sub_paths(x.subs[0], ctx)
            if got is not None:
                subs[k] = got
                if got[0] == "found" and _reaches_tree(got[1]):
                    _add(out, L_SECRET, guard=True, cat=_L.SECRETS)
                    return
    # 24/10 (P29 lot B) : une RECHERCHE (grep -r, rg…) est « large » (case cochée par défaut) ;
    # copier, archiver, comparer un dossier qui contient un secret emporte le secret : « secrets ».
    wide = _WIDE if prog in ("grep", "egrep", "fgrep", *_SEARCHERS) else _L.SECRETS
    a = [x.text for x in xs]
    tree = _tree_sources(prog, a)
    if tree is None:
        return
    ops, in_cwd, c_dirs, filt = tree
    if any(len(x.cands) > 1 or x.dyn for x in xs):
        filt = _NO_FILTER  # `--include={*.py,auth.json}` : un mot, plusieurs valeurs ; filtres non crus
    bases = ctx.cwds
    for d in c_dirs:
        ps = None if (_DYN in d or _FOUND in d) else _paths(d, ctx)
        bases = None if ps is None or bases is None else bases + ps
    if in_cwd and not ops:  # `grep -r motif`, `rg motif` : le dossier courant
        if any(_scope_reaches_secret(c, None, filt) for c in ctx.cwds or []):
            _add(out, L_SECRET, guard=True, cat=wide)
        return
    for k in ops:
        if k >= len(xs):
            continue
        x = xs[k]
        if k in subs:  # `grep -rn x $(grep -rl y app)` : les dossiers ou fichiers écrits par `$(…)`
            if any(_scope_reaches_secret(p, None, filt) for p in subs[k][1]):
                _add(out, L_SECRET, guard=True, cat=wide)
                return
            continue
        for c in x.cands:
            if _FOUND in c:
                continue
            scopes = None
            if _DYN not in c and bases is not None:
                bctx = ctx.child(cwds=bases)
                if x.glob:
                    scopes = _scopes(c, bctx)
                else:  # joker cité : un nom comme un autre
                    ps = _paths(c, bctx)
                    scopes = None if ps is None else [(p, None) for p in ps]
            if scopes is None:  # partie inconnue (`$X/runtime`) : le nom suffit
                hit = (not all(_secret_skipped(s, None, filt) for s in _SECRET_ALL)
                       and _designates_claude_dir(c, bases, x.glob, _SECRET_ANCESTORS))
            else:
                hit = any(_scope_reaches_secret(p, pat, filt) for p, pat in scopes)
            if hit:
                _add(out, L_SECRET, guard=True, cat=wide)
                return


# --- 04/11 : secrets du COMPTE (`SecretsCompte`, `L_SECRET_UTILISATEUR`) ---
# Lire = plafond : Read/NotebookRead du fichier, Grep (et tout lecteur récursif : grep -r, rg, cp -r, tar, zip,
# rsync…) sur lui ou sur un dossier qui le contient, toute commande qui le cite (cat, base64, cp, curl -F @…,
# script en ligne), joker qui l'atteint (développé sur le disque), lien vers lui, programme lancé DANS ~/.ssh,
# commandes qui sortent un secret (`_imprime_secret`). Restent libres : les noms (ls, stat, find sans -exec,
# Glob), known_hosts, config, authorized_keys, *.pub, ssh/sftp/ssh-add qui UTILISENT la clé, `ssh-keygen -l/-y`.

def secret_du_compte(p: Path, us: SecretsCompte | None = None) -> bool:
    """`p` (absolu, normalisé, liens résolus) est-il un secret du compte ?"""
    us = us or _US
    if _under(p, us.dossiers) or _under(p, us.fichiers):
        return True
    nom = p.name
    if any(_fn_nom(nom, m) for m in _NOMS_PARTOUT):
        return True
    for s in us.ssh:
        if s in p.parents:
            rel = p.parts[len(s.parts):]
            return not (any(_fn_nom(rel[0], n) for n in _SSH_LIBRES) or _fn_nom(rel[-1], "*.pub"))
    return any(m in p.parents for m in us.mozilla) and any(_fn_nom(nom, n) for n in _NOMS_MOZILLA)


def _cibles_compte() -> list[Path]:
    """Secrets du compte en chemins CONCRETS, pour un mot à partie inconnue (`$H/.ssh/id_rsa`) : dossiers et
    fichiers de la liste, clés usuelles de ~/.ssh et les autres fichiers secrets qui y sont."""
    us = _US
    out = list(us.dossiers) + list(us.fichiers)
    for s in us.ssh:
        out += [s / n for n in _SSH_CLES]
        try:
            with os.scandir(s) as it:
                noms = [e.name for k, e in zip(range(500), it)]
        except OSError:
            noms = []
        out += [s / n for n in noms if secret_du_compte(s / n)]
    return list(dict.fromkeys(out))


def _ancetres_compte() -> frozenset:
    return frozenset(a for r in _US.racines for a in (r, *r.parents))


def _compte_saute(r: Path, rel: tuple, filt: tuple, is_file: bool) -> bool:
    """Comme `_secret_skipped` : la racine `r` (chemin `rel` sous le dossier parcouru) écartée par les filtres ?"""
    inc, exc, exd = filt
    if any(fnmatch.fnmatchcase(d, g) for d in (rel[:-1] if is_file else rel) for g in exd):
        return True
    if not is_file:
        return bool(inc) and all(_code_glob(g) for g in inc)
    if inc:
        return not any(fnmatch.fnmatchcase(r.name, g) for g in inc)
    return any(fnmatch.fnmatchcase(r.name, g) for g in exc)


def _parcours_compte(p: Path, filt: tuple = _NO_FILTER) -> bool:
    """Parcourir RÉCURSIVEMENT `p` (dossier ou fichier) lit-il un secret du compte malgré les filtres ?"""
    us = _US
    if secret_du_compte(p):
        return True
    for r in us.racines:
        if r == p or r in p.parents:  # dans ~/.ssh ou un profil : un dossier qui existe peut contenir des secrets
            if _is_dir(p):
                return True
            continue
        if p in r.parents and not _compte_saute(r, r.parts[len(p.parts):], filt, r in us.fichiers):
            return True
    return False


_JOKER_MAX = 5000  # chemins lus au plus pour développer un joker (au-delà : son dossier fixe, parcouru)


def _developpe(c: str, bases: list) -> list[Path] | None:
    """Ce que le joker `c` désigne sur le disque, liens résolus ; None : trop de chemins."""
    pat = _msys(os.path.expanduser(c))
    out: list[Path] = []
    for b in ([None] if _abs_txt(pat) else bases):
        full = pat if b is None else os.path.join(_glob_mod.escape(str(b)), pat)
        try:
            for k, r in enumerate(_glob_mod.iglob(full)):
                if k >= _JOKER_MAX:
                    return None
                out.append(Path(r).resolve())
        except (OSError, RuntimeError, ValueError, re.error):
            return None
    return out


def _joker_compte(c: str, bases: list, recursif: bool, filt: tuple = _NO_FILTER) -> bool:
    """Le joker `c` (dossier connu) atteint-il un secret du compte : sur le disque, ou une clé usuelle absente ?"""
    trouve = _developpe(c, bases)
    if trouve is None:  # trop de chemins : le dossier avant le joker, parcouru
        parts = c.split("/")
        k = next((i for i, x in enumerate(parts) if _GLOB.search(x)), len(parts))
        head = "/".join(parts[:k]) or ("/" if c.startswith("/") else ".")
        return any(_parcours_compte(p) for p in _paths(head, _Ctx(cwds=list(bases))) or [])
    if any(_parcours_compte(p, filt) if recursif else secret_du_compte(p) for p in trouve):
        return True
    return _reaches_secret(c, bases, True, _cibles_compte())


def _inconnu_compte(c: str, cwds: list | None, glob: bool) -> bool:
    """Mot à partie inconnue (`$H/.ssh/id_rsa`, `cat $X/Cookies`) ou dossier courant inconnu : les secrets
    concrets, et les noms secrets où qu'ils soient (dernier morceau)."""
    if _reaches_secret(c, cwds, glob, _cibles_compte()):
        return True
    last = c.rstrip("/").rsplit("/", 1)[-1]
    if not glob:
        last = re.sub(r"([*?\[])", r"[\1]", last)
    if not last.replace(_DYN, "") or (_DYN in c and "/" not in c):  # `cat $F`, `curl -r $B-$E` : pas un chemin
        return False
    return any(_dir_part_match(n, last.replace(_DYN, "*")) for n in _NOMS_TYPES)


def _marques_compte() -> tuple[str, ...]:
    """Morceaux de texte sans lesquels un mot ne peut pas nommer un secret du compte (tri rapide)."""
    noms = {r.name for r in _US.racines if r.name} | {"Login Data", "Cookies", "Web Data", "Local State", "key3.db",
                                                      "key4.db", "cookies.sqlite", "signons", "logins",
                                                      "signedInUser", ".kdbx", ".keychain", ".ppk", "id_"}
    return tuple(n.lower() for n in noms)


def _cite_compte(c: str, glob: bool, ctx, decoupe: bool = True, entier: bool = True) -> bool:
    """La valeur `c` d'un mot peut-elle désigner un secret du compte ? `decoupe` : aussi les chemins écrits
    DANS le mot (`f=@~/.ssh/id_rsa` de curl, `if=…` de dd, code d'awk, de python -c) ; `entier` : le mot
    lui-même est un chemin possible (pas pour un texte de code)."""
    if not c:
        return False
    whole = c.split("=", 1)[1] if c.startswith("-") and "=" in c else ("" if c.startswith("-") else c)
    if not entier or len(whole) > 4096 or "\n" in whole:
        whole = ""
    if whole and "://" not in whole:
        if _DYN in whole or (ctx.cwds is None and not _abs_txt(_msys(os.path.expanduser(whole)))):
            if _inconnu_compte(whole, ctx.cwds, glob):
                return True
        elif glob and _GLOB.search(whole):
            if _joker_compte(whole, ctx.cwds, False):
                return True
        elif any(secret_du_compte(p) for p in _paths(whole, ctx) or []):
            return True
    if not decoupe:
        return False
    marques = _marques_compte()
    for w in _PATH_WORD.findall(c):
        w = w.lstrip("@<")
        if w == whole or _DYN in w or "/" not in w and "\\" not in w or not any(m in w.lower() for m in marques):
            continue
        w = re.sub(r"^(?:\$HOME|\$\{HOME\}|%USERPROFILE%)(?=[\\/])", lambda _m: str(Path.home()), w)
        for piece in [w] + [x for x in w.split(":")[1:] if "/" in x]:  # `-v ~/.ssh:/root/.ssh`, `HEAD:chemin`
            if any(secret_du_compte(p) for p in _paths(piece, ctx) or []):
                return True
    return False


# Programmes qui ne LISENT pas le contenu des fichiers cités (noms, tailles, droits, empreintes) ou qui
# UTILISENT une clé sans la sortir (ssh, sftp, ssh-add ; scp : ses opérandes restent lus) ; suppressions :
# `_rm` ; shells, eval, trap : leur code est jugé à part.
_COMPTE_LIBRES = _DIR_LISTERS | _NO_CONTENT | {
    "fd", "fdfind", "ssh", "sftp", "ssh-add", "ssh-copy-id", "md5sum", "sha1sum", "sha224sum", "sha256sum",
    "sha384sum", "sha512sum", "b2sum", "cksum", "popd", "eval", "trap", "true", "false", ":", "export", "local",
    "declare", "typeset", "readonly", "unset", "set", "type", "which", "command"} - {"shred"}
_COMPTE_MODIFIE = {"shred", "truncate"}
_SSH_DANS_MOT = re.compile(r"(?:^|[\s=;|&'\"/])ssh\s")
_SSH_KEYGEN_LIT = set("lyeLFRBHYs")  # empreinte, clé publique, certificat, known_hosts, signature : libres


def _imprime_secret(prog: str, a: list[str]) -> bool:
    """Commande qui SORT un secret du compte sans nommer de fichier (trousseau, coffre, jeton d'un CLI)."""
    ops = _operands(a)
    s = set(a)
    if prog in ("gpg", "gpg2"):
        return bool(s & {"--export-secret-keys", "--export-secret-subkeys"})
    if prog == "secret-tool":
        return ops[:1] in (["lookup"], ["search"])
    if prog == "security":  # macOS
        return ops[:1] in (["dump-keychain"], ["export"]) or (
            ops[:1] in (["find-generic-password"], ["find-internet-password"]) and bool(s & {"-w", "-g"}))
    if prog == "pass":
        return bool(ops) and ops[0] not in ("ls", "list", "find", "search", "init", "insert", "generate", "git",
                                            "help", "version", "rm", "mv", "cp", "edit")
    if prog == "gh":
        return ops[:2] == ["auth", "token"] or (ops[:2] == ["auth", "status"] and bool(s & {"-t", "--show-token"}))
    if prog == "gcloud":
        return any(o in ("print-access-token", "print-identity-token", "print-refresh-token") for o in ops)
    if prog == "aws":
        return ops[:2] in (["configure", "export-credentials"], ["ecr", "get-login-password"]) or (
            ops[:2] == ["configure", "get"] and any(re.search(r"secret|token|access_key", o) for o in ops[2:]))
    if prog == "az":
        return ops[:2] == ["account", "get-access-token"]
    if prog == "kubectl":
        return ops[:2] == ["config", "view"] and bool(s & {"--raw", "--flatten"})
    if prog == "git":
        return ops[:2] in (["credential", "fill"], ["credential-store", "get"], ["credential-cache", "get"])
    if prog.startswith("docker-credential-"):
        return ops[:1] == ["get"]
    if prog == "keepassxc-cli":
        return ops[:1] in (["show"], ["export"], ["clip"])
    if prog == "op":  # 1Password
        return ops[:1] in (["read"], ["inject"]) or ops[:2] == ["item", "get"]
    if prog == "bw":  # Bitwarden
        return ops[:1] in (["get"], ["export"])
    return False


def _judge_compte(prog: str, xs: list, ctx, out: list) -> None:
    """Lecture (plafond) ou modification (critique) d'un secret du compte par un programme et ses mots.
    `prog` vide : programme inconnu ; « < » : fichier lu par une redirection d'entrée."""
    if ctx.remote:
        return
    a = [x.text for x in xs]

    def lu() -> None:
        _add(out, L_SECRET_UTILISATEUR, guard=True, cat=_L.SECRETS_UTILISATEUR)

    if prog and _imprime_secret(prog, a):
        lu()
        return
    # lancé DANS ~/.ssh, un profil, un coffre : un interpréteur ou un programme inconnu lit ce dossier
    dedans = [r for r in _US.racines if r not in _US.fichiers]
    if (not prog or _INTERP.fullmatch(prog)) and any(_under(c, dedans) for c in ctx.cwds or []):
        lu()
        return
    _judge_compte_arbre(prog, xs, ctx, out)
    if prog in _COMPTE_LIBRES and not (prog == "find" and _FIND_NAME_FILES & set(a)):
        return
    if prog == "ssh-keygen":
        flags = {ch for t in a if t.startswith("-") and not t.startswith("--") for ch in t[1:]}
        if not flags & _SSH_KEYGEN_LIT and any(_cite_compte(c, x.glob, ctx) for x in xs for c in x.cands):
            _add(out, L_SECRET_UTILISATEUR_MODIF)  # -p, -c, génération sur -f : la clé réécrite
        return
    if prog == "git":
        xs = _without_commit_message(xs)
        a = [x.text for x in xs]
    pattern = -1  # motif de grep/rg (`grep -rn "\.ssh/id_rsa" docs`) : pas un chemin
    if prog in _GREPS | _SEARCHERS:
        flags_ = [k for k, t in enumerate(a[:-1]) if t in ("-e", "--regexp", "--expression")]
        pattern = flags_[0] + 1 if flags_ else next((k for k, t in enumerate(a) if not t.startswith("-")), -1)
    skip: set[int] = set()
    if prog == "scp":  # valeurs d'options (`-i clé`, `-F config`) : la clé UTILISÉE ; les opérandes restent lus
        skip = {k + 1 for k, t in enumerate(a) if len(t) == 2 and t[0] == "-" and t[1] in _COPY_ARG["scp"]}
    shell = prog in _SHELLS  # `bash -c '…'` : son code est jugé à part (`_code`), pas découpé ici
    for k, x in enumerate(xs):
        if k in skip or (k == pattern and not x.glob and not x.dyn):
            continue
        for c in x.cands:
            if _FOUND in c:  # « {} » d'un find -exec, liste donnée à xargs : les fichiers de ces dossiers
                hit = any(_parcours_compte(r) for r in ctx.found or [])
            else:  # une commande ssh dans un mot (`rsync -e 'ssh -i clé'`, `core.sshCommand=ssh -i clé`) UTILISE la clé
                hit = _cite_compte(c, x.glob, ctx, decoupe=not shell and not _SSH_DANS_MOT.search(c))
            if hit:
                if prog in _COMPTE_MODIFIE:
                    _add(out, L_SECRET_UTILISATEUR_MODIF)
                else:
                    lu()
                return
    # un lien (symbolique ou dur) vers un dossier qui CONTIENT un secret du compte se lit ensuite sans le nommer
    if prog == "ln" or (prog == "cp" and any(t in ("--link", "--symbolic-link")
                                             or re.fullmatch(r"-[A-Za-z]*[ls][A-Za-z]*", t) for t in a)):
        for x in xs:
            for c in x.cands:
                if c and not c.startswith("-") and _DYN not in c and not x.glob and any(
                        _parcours_compte(p) for p in _paths(c, ctx) or []):
                    lu()
                    return


def _judge_compte_arbre(prog: str, xs: list, ctx, out: list) -> None:
    """Lecteur RÉCURSIF (grep -r, rg, cp -r, tar, zip, rsync, scp -r…) sur un secret du compte ou un dossier
    qui en contient un (`grep -r x ~`, `tar czf /tmp/s.tgz ~/.ssh`, `cd ~ && rg token`) : plafond."""
    if prog in _DIR_LISTERS or prog in _NO_CONTENT:
        return
    tree = _tree_sources(prog, [x.text for x in xs])
    if tree is None:
        return
    ops, in_cwd, c_dirs, filt = tree
    if any(len(x.cands) > 1 or x.dyn for x in xs):
        filt = _NO_FILTER
    bases = ctx.cwds
    for d in c_dirs:
        ps = None if (_DYN in d or _FOUND in d) else _paths(d, ctx)
        bases = None if ps is None or bases is None else bases + ps
    hit = False
    if in_cwd and not ops:  # `grep -r motif`, `rg motif` : le dossier courant
        hit = any(_parcours_compte(c, filt) for c in ctx.cwds or [])
    for k in ops:
        if hit or k >= len(xs):
            break
        x = xs[k]
        for c in x.cands:
            if _FOUND in c:
                continue
            if _DYN in c or bases is None:
                hit = _designates_claude_dir(c, bases, x.glob, _ancetres_compte()) or _inconnu_compte(c, bases,
                                                                                                    x.glob)
            elif x.glob and _GLOB.search(c):
                hit = _joker_compte(c, bases, True, filt)
            else:
                hit = any(_parcours_compte(p, filt) for p in _paths(c, ctx.child(cwds=bases)) or [])
            if hit:
                break
    if hit:
        _add(out, L_SECRET_UTILISATEUR, guard=True, cat=_L.SECRETS_UTILISATEUR)


# Noms des secrets du compte dans du CODE (python -c, heredoc, node -e, awk), où un chemin se construit sans
# s'écrire en entier (`Path.home() / ".ssh" / "id_rsa"`).
_CODE_COMPTE = re.compile(
    r"(?<![\w.-])(?:id_(?:rsa|dsa|ecdsa|ed25519|xmss)(?:_sk)?(?!\.pub)(?![\w-])|\.git-credentials|_?\.?netrc(?![\w-])"
    r"|\.pypirc|private-keys-v1\.d|secring\.gpg|application_default_credentials\.json|access_tokens\.db"
    r"|credentials\.db|\.aws[\\/]credentials|key[34]\.db|logins\.json|cookies\.sqlite|signedInUser\.json"
    r"|Login Data|\.password-store|share[\\/]keyrings|kwalletd|Keychains)"
    r"|[\w-]\.(?:kdbx|keychain-db|ppk)(?![\w-])", re.I if os.name == "nt" else 0)


def _code_compte(code: str | None, ctx, out: list) -> None:
    """Code d'un autre langage qui nomme un secret du compte (chemin écrit, ou nom) : plafond."""
    if not code or ctx.remote:
        return
    if _CODE_COMPTE.search(code) or _cite_compte(code, False, ctx, entier=False):
        _add(out, L_SECRET_UTILISATEUR, guard=True, cat=_L.SECRETS_UTILISATEUR)


def _lecture_compte(tool_name: str, tool_input: dict) -> Verdict | None:
    """Read/NotebookRead d'un secret du compte, Grep sur lui ou sur un dossier qui en contient un : plafond.
    Glob et LS ne rendent que des noms : libres, comme `ls ~/.ssh`."""
    if tool_name in ("Read", "NotebookRead"):
        brut = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        hit = bool(brut) and secret_du_compte(_resolve(brut))
    elif tool_name == "Grep":
        brut = str(tool_input.get("path") or "") or str(_base())
        vals = []
        if tool_input.get("glob"):
            vals.append(("g", str(tool_input.get("glob"))))
        if tool_input.get("type"):
            vals.append(("t", str(tool_input.get("type"))))
        hit = _parcours_compte(_resolve(brut), _tree_filters("rg", vals))
    else:
        return None
    if not hit:
        return None
    return _single(True, L_SECRET_UTILISATEUR, "", CRITICAL, guard=True, cats=(_L.PLAFOND, _L.SECRETS_UTILISATEUR))


def _protected(raw_path: str) -> bool:
    return bool(raw_path) and _under(_resolve(raw_path), PROTECTED_PATHS)


# 16/10, plans : un dossier `runtime/plans` (le vrai, ou le même vu d'un autre dossier
# courant : le hook ne transmet pas toujours celui de Claude), et ce qu'il contient.
# `tests/plans` n'en est pas un.
_PLANS_REAL = config.PLANS_DIR.resolve()
_PLANS_PARTS = ("runtime", "plans")
# Mot à partie inconnue (`$D/runtime/plans/P3.json`, `$D/plans/P3.json`) ou texte de code
# (`"runtime" + "/plans/P3.json"`) : le chemin ne se résout pas, le texte compte.
_PLANS_TEXT = re.compile(r"(?<![\w.-])runtime/plans(?![\w-])|(?<![\w.-])(?<![\w.-]/)plans/P\d+(?!\w)")


def _plans_like(p: Path, pattern: str | None = None) -> bool:
    """`p` (ou ce que le joker `pattern` y désigne) est-il dans un dossier des plans ?"""
    parts = p.parts
    if p == _PLANS_REAL or _PLANS_REAL in p.parents:
        return True
    if any(parts[i:i + 2] == _PLANS_PARTS for i in range(len(parts) - 1)):
        return True
    return pattern is not None and p.name == "runtime" and fnmatch.fnmatchcase("plans", pattern)


def _cited_plans(texts: list[str], ctx, in_cwd: bool = False) -> bool:
    """Ces textes (mots d'une commande, code d'un script) désignent-ils un plan ?
    `in_cwd` : lancé DANS un dossier des plans, tout compte."""
    if ctx.remote:
        return False
    if in_cwd and any(_plans_like(c) for c in ctx.cwds or []):
        return True
    for t in texts:
        if _FOUND in t:
            if any(_plans_like(r) for r in ctx.found or []):
                return True
            continue
        words = _PATH_WORD.findall(t)
        words += [w.rsplit(":", 1)[1] for w in words if ":" in w and "://" not in w]
        for w in words:
            if "plans" not in w:
                continue
            if _DYN in w:
                if _PLANS_TEXT.search(w.replace(_DYN, "/")):
                    return True
                head, _sep, _base_ = w.rpartition("/")
                if _DYN in head or not _sep:
                    continue
                w = head + "/x"
            for p in _paths(w, ctx) or [None]:
                if p is None or _plans_like(p):
                    return True
    return False


def _without_commit_message(args: list) -> list:
    """Arguments de `git commit` sans le texte du message (`-m`, `--message`, `-am`) :
    il n'écrit aucun fichier. Ses `$(…)` sont jugés à part, comme toute substitution."""
    a = [x.text for x in args]
    i = 0
    while i < len(a) and a[i].startswith("-"):
        i += 2 if a[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") else 1
    if i >= len(a) or a[i] != "commit":
        return args
    keep, skip = list(args[:i + 1]), False
    for k in range(i + 1, len(a)):
        t = a[k]
        if skip:
            skip = False
        elif t == "--":
            keep.extend(args[k:])
            break
        elif t in ("-m", "--message"):
            skip = True
        elif t.startswith("--message="):
            continue
        elif re.fullmatch(r"-[A-Za-z]*m.*", t):  # grappe d'options courtes : « -am msg », « -mmsg »
            skip = t.endswith("m") and t.index("m") == len(t) - 1
        else:
            keep.append(args[k])
    return keep


# 15/10 (décision de l'utilisateur) : le code qui décide QUI peut activer « Autonome
# TOTAL », desserrer le garde-fou ou répondre à une confirmation. Réécrit puis
# relancé, il donnerait Total à Claude. Écrire, éditer, rediriger vers ces
# chemins : confirmé même en autonome et en Total (`guard=True`). Lecture libre.
# Volontairement court (moins de questions pendant le développement d'Astrel) :
# PAS `core.py`, `brain.py`, les tests ni `run.sh`. `hud/dist` régénéré par
# `npm run build` ne pose pas de question (la commande ne cite aucun de ces chemins).
PROTECTED_CODE = [p.resolve() for p in (
    config.ROOT / "astrel" / "totalgate.py",  # qui active Total, desserre le garde-fou, lève l'ESTOP
    config.ROOT / "astrel" / "state.py",      # « la commande vient de Claude » (from_claude, canal de contrôle)
    # 19/10 (plan P6) : state.py et confirm.py s'en remettent à eux pour le pid du client
    # (SO_PEERCRED, table TCP, jeton) et l'ascendance des process (cgroup, marqueur).
    config.ROOT / "astrel" / "ipc.py", config.ROOT / "astrel" / "platform" / "proc.py",
    config.ROOT / "astrel" / "server.py",     # authentification du HUD, `_via`/`_from_claude` des connexions
    config.ROOT / "astrel" / "bubble.py",     # envoie les clics Oui/Non de la carte de confirmation
    config.ROOT / "astrel" / "minichat.py",   # compose la réponse « confirm » de la bulle
    config.HUD_DIR / "src", config.HUD_DIR / "dist", config.HUD_DIR / "index.html",  # interrupteur Total, Oui/Non
    # WhatsApp (17/10) : le pont lit les identifiants et n'écrit qu'au numéro de l'utilisateur ;
    # `astrel/whatsapp.py` filtre l'entrée (liste blanche) et répond aux confirmations.
    config.WHATSAPP_BRIDGE_DIR, config.ROOT / "astrel" / "whatsapp.py",
    # 23/10 (plan P29) : les limites de Total et de la levée (catalogue, décision) : plafond.
    config.ROOT / "astrel" / "limites.py",
    # 24/10 (P29 lot B) : garde en mémoire et relit les cases de confirmation.
    config.ROOT / "astrel" / "cases_confirmation.py",
    # 25/10 (audit p33-p34, demande de l'utilisateur) : le dossier des hooks (empreinte, politique d'outils,
    # hooks) déjà dans `PROTECTED_PATHS` ; ici pour qu'un script en ligne (python -c, perl -e, ruby -e)
    # qui le cite et peut écrire demande aussi, chemin résolu depuis le dossier courant.
    config.ROOT / "hooks",
    # 01/11 (accord de l'utilisateur) : décide ce que « annule ce que tu viens de faire » copie et RÉÉCRIT
    # hors du hook (outil MCP, terminal) ; importé par `hooks/suivi_outils.py`.
    config.ROOT / "astrel" / "instantanes.py",
    # 04/11 (lot de sécurité A, accord explicite de l'utilisateur ; audits du 03/11, F1, F4, F5) : un hook est un
    # NOUVEAU process à chaque appel d'outil, un module qu'il charge réécrit vaut dès l'appel suivant.
    # (a) Fermeture COMPLÈTE des imports des hooks PreToolUse qui voient Bash et Write (`permission_hook.py`,
    #     `suivi_outils.py`), calculée par `tests/test_securite_lot_a.py` (sous-process isolé + lecture des
    #     imports, même tardifs) : le paquet, `config.py` (chemins de toutes les protections), le bus
    #     d'événements (importé par `state.py`), `platform/` (choix du système, process, fichiers).
    config.ROOT / "astrel" / "__init__.py", config.ROOT / "astrel" / "config.py",
    config.ROOT / "astrel" / "events.py", config.ROOT / "astrel" / "platform" / "__init__.py",
    config.ROOT / "astrel" / "platform" / "materiel.py", config.ROOT / "astrel" / "platform" / "systeme.py",
    # (b) Autres scripts cités par les réglages du CLI (`brain.write_settings`) et leurs imports DIRECTS :
    #     routage (son `updatedInput` s'applique APRÈS le jugement du garde-fou), rtk (préfixe ajouté à des
    #     commandes déjà jugées) et son binaire, quota, `apiKeyHelper`, rappel de la mémoire (son contexte
    #     est injecté à chaque message de l'utilisateur). `hooks/` l'est déjà.
    config.ROOT / "astrel" / "route_hook.py", config.ROOT / "astrel" / "rtk_hook.py",
    config.ROOT / "astrel" / "quota_hook.py", config.ROOT / "astrel" / "cle_api.py",
    config.ROOT / ".rtk", config.RTK_BIN,
    config.ROOT / "astrel" / "agents.py", config.ROOT / "astrel" / "memoire.py",
    config.ROOT / "astrel" / "quota_arret.py", config.ROOT / "astrel" / "rappel.py",
    # (c) Contrôles de sécurité : l'empreinte et le contrôle de santé (`doctor.py`, qui retient ce que l'utilisateur
    #     accepte), la preuve de fin de mission (fin de la barrière d'écriture), la surveillance de la levée.
    #     PAS `core.py` ni `astrel/coeur/` (choix de l'utilisateur, docs/DURCISSEMENT-GARDE-FOU.md).
    config.ROOT / "astrel" / "doctor.py", config.ROOT / "astrel" / "preuve.py",
    config.ROOT / "astrel" / "levee.py",
)]
_PATH_WORD = re.compile(r"[^\s;|&<>()'\"`=,]+")
_CD = re.compile(r"(?:^|[;&|(\s])(?:cd|pushd)\s+([^\s;&|)]+)")


def _protected_code(raw_path: str) -> bool:
    return bool(raw_path) and _under(_resolve(raw_path), PROTECTED_CODE)


_CODE_NAMES = {p.name for p in PROTECTED_CODE}
_CODE_NAMES_CI = {n.lower() for n in _CODE_NAMES}  # 04/11 (lot A) : NTFS ignore la casse
_NAMED_ANYWHERE = {"totalgate.py"}  # protégé par son NOM, quel que soit le dossier (`sed -i … totalgate.py`)
# 04/11 (lot A) : noms protégés depuis ce jour et COURANTS dans tout projet Python : cités derrière un dossier
# INCONNU (`touch "$PKG/__init__.py"`), ils ne demandent pas (comme avant le 04/11 ; une écriture vers `$PKG/…`
# reste un doute, `_judge_write`). Résolus dans un dossier connu, ils restent jugés comme les autres.
_GENERIC_CODE_NAMES = frozenset({"__init__.py", "config.py", "events.py", "agents.py", "doctor.py", "rtk", ".rtk"})


def _cites_code_name(w: str) -> bool:
    """Le mot contient-il le nom d'un fichier de `PROTECTED_CODE` (sans casse sous Windows) ?"""
    if _WINDOWS:
        w = w.lower()
        return any(n in w for n in _CODE_NAMES_CI)
    return any(n in w for n in _CODE_NAMES)


def _bash_protected_code(cmd: str) -> str:
    """Nom du fichier de `PROTECTED_CODE` que la commande cite (vide sinon).
    Chaque mot qui ressemble à l'un d'eux est résolu depuis le dossier courant
    de Claude ET depuis chaque `cd`/`pushd` de la commande (`cd astrel && sed -i
    … state.py`). Analyse de texte : un script qui écrit ces fichiers sans les
    nommer n'est pas vu (limite connue du garde-fou).
    15/10 : ne sert plus qu'en REPLI, quand l'analyse structurée échoue (syntaxe
    illisible) ; sinon chaque commande est jugée à part (`_judge_cited`)."""
    words = [w for w in _PATH_WORD.findall(cmd) if _cites_code_name(w)]
    if not words:
        return ""
    for w in words:
        if w.rsplit("/", 1)[-1] in _NAMED_ANYWHERE:
            return w.rsplit("/", 1)[-1]
    bases = [_base()]
    for m in list(_CD.finditer(cmd))[:8]:
        # 23/10 (audit P29) : os.path.expanduser ne lève pas sur « ~0.27 » (`httpx~=0.27`), Path.expanduser si.
        d = Path(os.path.expanduser(_msys(m.group(1).strip("'\""))))
        bases += [d.resolve()] if d.is_absolute() else [(b / d).resolve() for b in bases]
    for w in words:
        p = Path(os.path.expanduser(_msys(w)))
        for b in ([Path("/")] if p.is_absolute() else bases):
            full = (b / p).resolve()
            if _under(full, PROTECTED_CODE):
                return full.name
    return ""


# --- mission autonome (15/10, autorisation explicite de l'utilisateur, docs/LOT-HUD-AUTONOMIE.md) ---
# Pendant une mission active (`runtime/mission.json`, fichier protégé, écrit par le
# cœur), toute ÉCRITURE, tout déplacement ou toute SUPPRESSION hors du périmètre
# déclaré (dossiers de la mission, + /tmp) est demandé, même en autonome : critique,
# couvert par « oui pour 30 min », accordé par Total (journalisé). Dans un dossier
# INTERDIT à la mission : action sur le garde-fou, demandée même en Total. Les
# lectures restent libres. Mission en pause : la barrière tient jusqu'à la fin du
# tour de mission en cours (`in_turn`), puis tombe (l'utilisateur a repris la main).
L_MISSION_OUT = "une écriture hors du périmètre de la mission"
L_MISSION_FORBIDDEN = "une écriture dans un dossier interdit à la mission"
# Write/Edit et Bash partagent la même autorisation « 30 min » (clé au format Bash).
K_MISSION_OUT = f"bash:{L_MISSION_OUT}"
_MISSION: contextvars.ContextVar = contextvars.ContextVar("guard_mission", default=None)


def mission_fence() -> tuple[list[Path], list[Path]] | None:
    """(périmètre, dossiers interdits) de la mission en cours, chemins absolus
    normalisés ; None sans mission sous barrière. Périmètre vide (mission d'avant le
    15/10) : seuls les dossiers interdits comptent."""
    try:
        data = json.loads(config.MISSION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    status = data.get("status")
    if status != "active" and not (status == "paused" and data.get("in_turn") is True):
        return None

    def paths(key: str) -> list[Path]:
        raw = data.get(key)
        out = []
        for v in raw if isinstance(raw, list) else []:
            p = Path(str(v)).expanduser()
            if p.is_absolute():
                out.append(p.resolve())
        return out

    scope, forbidden = paths("scope"), paths("forbidden_paths")
    if scope:
        scope += [t.resolve() for t in TEMP_ROOTS]
    return (scope, forbidden) if scope or forbidden else None


def zones_confidentielles() -> list[Path]:
    """04/11 (lot 3b) : zones confidentielles de l'utilisateur (réglage `zones_confidentielles` de
    runtime/settings.json, dossiers absolus séparés par « ; », écrit par le cœur seul), chemins résolus.
    Fichier illisible ou réglage vide : aucune zone (rien ne change par rapport à avant ce réglage)."""
    try:
        data = json.loads(config.ASTREL_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    values = data.get("values") if isinstance(data, dict) else None
    raw = values.get("zones_confidentielles") if isinstance(values, dict) else None
    out: list[Path] = []
    for part in str(raw or "").split(";"):
        p = Path(part.strip()).expanduser()
        if part.strip() and p.is_absolute():
            try:
                out.append(p.resolve())
            except (OSError, RuntimeError):
                continue
    return out


# Programmes qui parcourent un dossier entier : cités sur un dossier qui CONTIENT une zone, ils la lisent.
_PARCOURT = {"grep", "egrep", "fgrep", "rg", "ag", "ack", "find", "fd", "fdfind", "tar", "zip", "7z", "rsync",
             "cp", "scp", "du", "tree", "locate", "git"}


def _zone_lue(tool_name: str, tool_input: dict) -> Verdict | None:
    """Lecture par Claude dans une zone confidentielle (04/11, lot 3b) : verdict à demander, sinon None.
    Durcissement seulement : sans zone déclarée, rien ne change."""
    zs = zones_confidentielles()
    if not zs:
        return None
    if _cli_local_reel():
        return None  # 05/11 : le modèle LOCAL lit la zone, rien ne sort (le reste du verdict s'applique)

    def touche(p: Path, recursif: bool) -> bool:
        return _under(p, zs) or (recursif and any(_under(z, [p]) for z in zs))

    cibles: list[tuple[str, bool]] = []
    if tool_name in ("Read", "NotebookRead"):
        cibles.append((str(tool_input.get("file_path") or tool_input.get("notebook_path") or ""), False))
    elif tool_name in ("Grep", "Glob", "LS"):
        base = str(tool_input.get("path") or "") or str(_base())
        cibles.append((base, True))
        pattern = str(tool_input.get("pattern") or "") if tool_name == "Glob" else ""
        # 04/11 (CI Windows) : un motif absolu « C:\…\zone/**/*.md » vise aussi une zone
        if pattern.startswith(("/", "~")) or os.path.isabs(pattern) or re.match(r"^[A-Za-z]:[\\/]", pattern):
            cibles.append((pattern.split("*", 1)[0] or "/", True))
    elif tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))
        try:
            mots = shlex.split(cmd, posix=True)
        except ValueError:
            mots = cmd.split()
        recursif = any(Path(m).name in _PARCOURT for m in mots) or bool(re.search(r"(?:^|\s)-[a-zA-Z]*[rR]", cmd))
        if _under(_base(), zs):
            cibles.append((str(_base()), False))
        for m in mots:
            for morceau in m.split("=")[-1:] if m.startswith("-") else [m]:
                if morceau.startswith(("/", "~", "./", "../")) or "/" in morceau \
                        or re.match(r"^[A-Za-z]:[\\/]", morceau) or "\\" in morceau:  # 04/11 : 'C:\…' cité
                    cibles.append((morceau.split("*", 1)[0] or morceau, recursif))
    else:
        return None
    for brut, recursif in cibles:
        if not brut:
            continue
        try:
            p = _resolve(brut)
        except (OSError, ValueError, RuntimeError):
            continue
        if touche(p, recursif):
            return _single(True, L_ZONE, "zone:lecture", CRITICAL, cats=(_L.PLAFOND, _L.SECRETS))
    return None


def _judge_fence(p: Path, out: list, pattern: str | None = None, contains: bool = True) -> None:
    """Écriture ou suppression de `p` (ou de ce que le joker `pattern` y désigne)
    pendant une mission : hors du périmètre, ou dans un dossier interdit.
    `contains` faux : seul `p` lui-même compte (dossier de travail d'un programme),
    pas les dossiers interdits qu'il contient."""
    fence = _MISSION.get()
    if not fence:
        return
    scope, forbidden = fence
    # Interdit : dedans, ou un dossier qui le contient (`cp -r x parent/`, `rm -rf parent`).
    if _under(p, forbidden) or (contains and any(_hits(p, pattern, f) for f in forbidden)):
        _add(out, L_MISSION_FORBIDDEN, guard=True)
        return
    if scope and not _under(p, scope):
        _add(out, L_MISSION_OUT)


# Pendant une mission seulement (hors mission, aucun verdict ne change) : programmes qui
# modifient des fichiers sans passer par une « écriture » que l'analyse suit déjà. Leurs
# chemins cités (ou leur dossier de travail) passent par la barrière ; un chemin qu'on ne
# sait pas résoudre est demandé.
_FENCE_OPERANDS = {"mkdir", "rmdir", "chmod", "chown", "chgrp", "chattr", "setfacl", "truncate", "shred",
                   "mkfifo", "mknod", "rename", "prename", "ed", "red", "ex", "patch", "split", "csplit",
                   "gzip", "gunzip", "bzip2", "bunzip2", "xz", "unxz", "lzma", "unlzma", "zstd", "unzstd",
                   "compress", "uncompress", "pigz", "unpigz", "lz4", "brotli"}
_COMPRESSORS_READ = {"-c", "--stdout", "--to-stdout", "-l", "--list", "-t", "--test"}
_GIT_READ = {"status", "log", "diff", "show", "ls-files", "ls-tree", "ls-remote", "grep", "blame", "rev-parse",
             "describe", "shortlog", "cat-file", "rev-list", "name-rev", "show-ref", "for-each-ref",
             "merge-base", "check-ignore", "count-objects", "whatchanged", "version", "help", "var", "diff-tree",
             "diff-files", "diff-index", "range-diff", "cherry", "show-branch", "verify-commit", "verify-tag"}
_PKG_TOOLS = {"npm", "pnpm", "yarn", "bun"}
_PKG_READ = {"view", "info", "show", "ls", "list", "ll", "la", "outdated", "search", "why", "explain", "help",
             "config", "get", "doctor", "-v", "--version", "version", "audit", "whoami", "ping", "prefix", "root"}
_PKG_DIR_OPTS = {"--prefix", "-C", "--cwd", "--dir", "--cwd-dir"}


_GIT_LIST_WRITE = {"-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C", "--copy", "-u", "--set-upstream-to",
                   "--unset-upstream", "-f", "--force", "--edit-description", "-s", "-a", "--annotate", "-F"}


def _git_lists(sub: str, rest: list[str]) -> bool:
    """Sous-commande git qui ne fait que lister ou lire (pendant une mission)."""
    ops = [t for t in rest if not t.startswith("-")]
    if sub in ("branch", "tag"):
        bad = _GIT_LIST_WRITE - ({"-a"} if sub == "branch" else set())  # branch -a : toutes ; tag -a : annotée
        return not ops and not any(t.split("=", 1)[0] in bad for t in rest) or any(t in ("-l", "--list") for t in rest)
    if sub == "remote":
        return not rest or rest[0] in ("-v", "--verbose", "show", "get-url")
    if sub in ("stash", "worktree", "notes", "submodule"):
        return bool(ops) and ops[0] in ("list", "show", "status", "summary")
    if sub == "config":
        return any(t.startswith(("--get", "--list", "-l", "--show")) for t in rest) or (len(ops) == 1 and len(rest) == 1)
    if sub == "symbolic-ref":
        return len(ops) <= 1
    if sub == "reflog":
        return not ops or ops[0] in ("show", "exists")
    return False


def _fence_xs(xs: list, ctx: "_Ctx", out: list) -> None:
    for x in xs:
        for c in x.cands or [""]:
            if not c:
                continue
            scopes = None if (_DYN in c or _FOUND in c) else _scopes(c, ctx)
            if scopes is None:
                _add(out, L_MISSION_OUT)  # chemin impossible à connaître : demandé pendant la mission
                continue
            for p, pattern in scopes:
                if pattern is None and _DEV_FREE.match(p.as_posix()):
                    continue  # /dev/null…
                _judge_fence(p, out, pattern)


def _fence_cwd(ctx: "_Ctx", out: list, contains: bool = False) -> None:
    """Le programme écrit DANS son dossier de travail : il doit être dans le périmètre.
    `contains` : il peut réécrire tout ce dossier (git clean, reset --hard), dossiers
    interdits compris."""
    if ctx.cwds is None:
        _add(out, L_MISSION_OUT)
        return
    for b in ctx.cwds:
        _judge_fence(Path(b), out, "*", contains=contains)


def _fence_program(prog: str, args: list, ctx: "_Ctx", out: list) -> None:
    """Pendant une mission : les programmes qui modifient des fichiers ou un dossier."""
    if not _MISSION.get() or ctx.remote:
        return
    a = [x.text for x in args]
    plain = [x for x in args if not x.text.startswith("-") or x.dyn]
    if prog in _FENCE_OPERANDS:
        if prog in ("gzip", "gunzip", "bzip2", "bunzip2", "xz", "unxz", "zstd", "unzstd", "lzma", "unlzma",
                    "pigz", "unpigz", "lz4", "brotli") and any(t in _COMPRESSORS_READ or re.fullmatch(r"-[a-z]*[clt][a-z]*", t)
                                                               for t in a):
            return  # vers la sortie standard, liste, test : rien n'est remplacé
        _fence_xs(plain, ctx, out)
        if prog == "patch":
            dirs = [args[i + 1] for i, t in enumerate(a[:-1]) if t in ("-d", "--directory")]
            dirs += [_lit(t.split("=", 1)[1]) for t in a if t.startswith("--directory=")]
            dirs += [_lit(t[2:]) for t in a if t.startswith("-d") and len(t) > 2]
            if dirs:
                _fence_xs([_lit(c.rstrip("/") + "/x") for d in dirs for c in d.cands], ctx, out)
            else:
                _fence_cwd(ctx, out)
    elif prog in ("awk", "gawk") and any(t in ("inplace", "-iinplace", "--include=inplace") for t in a):
        _fence_xs(plain, ctx, out)
    elif prog == "ln":
        return  # jugé dans `_copy` (cible du lien)
    elif prog == "git":
        i, dirs, sub = 0, [], ""
        while i < len(args):
            t = a[i]
            if t == "-C" and i + 1 < len(args):
                dirs.append(args[i + 1])
                i += 2
                continue
            if t in ("-c", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(args):
                if t != "-c":
                    dirs.append(args[i + 1])
                i += 2
                continue
            if t.startswith(("--git-dir=", "--work-tree=")):
                dirs.append(_lit(t.split("=", 1)[1]))
            elif not t.startswith("-"):
                sub = t
                break
            i += 1
        if not sub or sub in _GIT_READ or _git_lists(sub, a[i + 1:]):
            return  # lecture, ou simple liste (`git branch -a`, `git remote -v`…)
        repo = ctx
        if dirs:
            repo = ctx.child(seen=list(ctx.seen))
            for d in dirs:
                _cd([d], repo)  # dossier inconnu : cwds None, donc demandé
        rest = a[i + 1:]
        whole = sub == "clean" or (sub in ("reset", "checkout", "switch") and any(
            t in ("--hard", "-f", "--force", "--merge", "--keep") for t in rest))
        _fence_cwd(repo, out, contains=whole)
        if sub in ("restore", "checkout", "rm", "mv", "clean", "stash", "apply", "add", "reset") and repo.cwds:
            # chemins cités (`-- dossier`, `.`) : jugés en entier, dossiers interdits compris
            _fence_xs([x for x in args[i + 1:] if not x.text.startswith("-")], repo, out)
        if sub == "clone":  # le dossier créé
            ops = [x for x in args[i + 1:] if not x.text.startswith("-")]
            if len(ops) >= 2:
                _fence_xs(ops[1:2], ctx, out)
    elif prog in _PKG_TOOLS:
        sub = next((t for t in a if not t.startswith("-")), "")
        if sub in _PKG_READ:
            return
        dirs = [args[i + 1] for i, t in enumerate(a[:-1]) if t in _PKG_DIR_OPTS]
        dirs += [_lit(t.split("=", 1)[1]) for t in a if t.split("=", 1)[0] in _PKG_DIR_OPTS and "=" in t]
        if dirs:
            _fence_xs([_lit(c.rstrip("/") + "/x") for d in dirs for c in d.cands], ctx, out)
        else:
            _fence_cwd(ctx, out)


# --- réglages persistants (lus par le hook à CHAQUE appel) ---

def get_mode() -> str:
    try:
        mode = json.loads(MODE_FILE.read_text(encoding="utf-8")).get("mode", DEFAULT_MODE)
    except (OSError, ValueError):
        mode = DEFAULT_MODE
    return mode if mode in MODES else "autonome"


def set_mode(mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"mode attendu : {', '.join(MODES)}")
    MODE_FILE.write_text(json.dumps({"mode": mode}), encoding="utf-8")
    return mode


def _grants() -> dict[str, float]:
    try:
        data = json.loads(GRANTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    now = time.time()
    out: dict[str, float] = {}
    for k, v in (data.items() if isinstance(data, dict) else ()):
        try:
            if float(v) > now:
                out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def grant(key: str, seconds: float = GRANT_S) -> None:
    """Autorise ce type d'action sans nouvelle question pendant `seconds`."""
    if not key:
        return
    data = _grants()
    data[key] = time.time() + seconds
    GRANTS_FILE.write_text(json.dumps(data), encoding="utf-8")


def granted(key: str) -> bool:
    return bool(key) and key in _grants()


def grants_list() -> list[dict]:
    """Autorisations « 30 min » en cours (affichées dans le HUD, révocables)."""
    return [{"key": k, "until": v} for k, v in sorted(_grants().items(), key=lambda kv: kv[1])]


def revoke(key: str = "") -> int:
    """Retire une autorisation (toutes si `key` est vide). Appelé par le HUD ou
    la CLI seulement : resserrer est toujours permis, mais la commande passe
    par le canal de contrôle, protégé contre Claude comme le reste."""
    data = _grants()
    removed = len(data) if not key else int(key in data)
    data = {} if not key else {k: v for k, v in data.items() if k != key}
    GRANTS_FILE.write_text(json.dumps(data), encoding="utf-8")
    return removed


# --- évaluation ---

def _under(p: Path, roots: list[Path]) -> bool:
    return any(p == r or r in p.parents for r in roots)


# 15/10 : dossier courant de Claude (`cwd` du hook) pendant un `classify` ;
# à défaut, le dossier de travail d'Astrel.
_CWD: contextvars.ContextVar[str] = contextvars.ContextVar("guard_cwd", default="")


def _base() -> Path:
    cwd = _CWD.get()
    if cwd:
        p = Path(_msys(cwd))
        if absolu(p):
            return p if p.is_absolute() else Path(os.path.abspath(p))
    return config.WORKDIR


def absolu(p: str | os.PathLike) -> bool:
    """Chemin absolu. 03/11 (Windows) : aussi « /etc », « \\tmp » (enraciné, sans lecteur : lecteur courant) ;
    avant, un `cwd` « /etc » était ignoré et « hosts » jugé dans les projets (libre)."""
    q = Path(p)
    return q.is_absolute() or (os.name == "nt" and bool(q.root))


# 03/11/2026 (portage Windows, lot 3, autorisé par l'utilisateur) : le Bash de Claude Code sous Windows est
# Git Bash, qui écrit « /c/Users/… » (aussi « /cygdrive/c/… », « /mnt/c/… ») ; Python sous Windows le lirait
# « \\c\\Users… » sur le disque courant, et un fichier protégé passerait pour un autre. Ramené à « C:/Users/… ».
_MSYS = re.compile(r"^/(?:cygdrive/|mnt/)?([A-Za-z])(?=/|$)")
# 04/11 (lot de sécurité A, accord de l'utilisateur ; audit 3, H4) : Windows lit un même fichier sous d'autres
# formes que Python ne ramène pas à son chemin : préfixes Win32 `\\?\`, `\\.\`, `\\?\UNC\` (gardés par
# `ntpath.realpath`, et `\\?\C:` n'est pas le lecteur `C:` pour `PureWindowsPath`), partage administratif
# local (`\\localhost\C$\…`), flux NTFS (`guard.py::$DATA` EST le fichier, `guard.py:x` un flux à côté),
# point ou espace final (retirés par Windows), noms courts 8.3 (`ASTREL~1`). Ramenés à la forme normale
# AVANT tout jugement (`_win_forme`, appelée par `_msys`) ; une forme qui ne se ramène pas à un lecteur
# (volume, `GLOBALROOT`, disque physique, autre partage administratif) est INCERTAINE (`_win_douteux`) :
# écrire = doute du garde-fou, lire = lecture d'un secret. La casse : comparaisons de `WindowsPath`, et
# noms cités sans casse (`_cites_code_name`, `PROTECTED_BASH`, `SECRET_BASH`). Simulé partout par les
# tests (`tests/test_securite_lot_a.py`), vrai Windows : `tests/test_guard_windows.py` (CI).
_WINDOWS = os.name == "nt"
_WIN_PREFIXE_UNC = re.compile(r"^[\\/]{2}[?.][\\/]UNC[\\/]", re.I)
_WIN_PREFIXE_LECTEUR = re.compile(r"^[\\/]{2}[?.][\\/](?=[A-Za-z]:(?:[\\/]|$))")
_WIN_PREFIXE = re.compile(r"^[\\/]{2}[?.](?:[\\/]|$)")  # ce qui reste après les deux premiers : incertain
_WIN_PARTAGE_LOCAL = re.compile(r"^[\\/]{2}(?:localhost|127\.0\.0\.1|\[?::1\]?|\.)[\\/]([A-Za-z])\$(?=[\\/]|$)", re.I)
_WIN_PARTAGE_ADMIN = re.compile(r"^[\\/]{2}[^\\/]+[\\/][A-Za-z]\$(?:[\\/]|$)")
_WIN_LECTEUR = re.compile(r"^[A-Za-z]:")
_WIN_COURT = re.compile(r"~\d")  # nom court 8.3 possible (`PROGRA~1`, `ASTREL~1.PY`)


def _nom_long(s: str) -> str:
    """Noms courts 8.3 développés (Windows : `GetLongPathNameW` sur la plus longue partie qui existe) ; la
    suite, qui n'existe pas, ne peut désigner aucun fichier existant. Ailleurs, ou en cas d'échec : tel quel."""
    if os.name != "nt":
        return s
    try:
        import ctypes

        tete, queue = s, ""
        for _ in range(64):
            buf = ctypes.create_unicode_buffer(32768)
            n = ctypes.windll.kernel32.GetLongPathNameW(tete, buf, len(buf))  # type: ignore[attr-defined]
            if 0 < n < len(buf):
                return buf.value + queue
            coupe = max(tete.rfind("\\"), tete.rfind("/"))
            if coupe <= 2:
                return s
            tete, queue = tete[:coupe], tete[coupe:] + queue
    except (OSError, AttributeError, ValueError):
        pass
    return s


def _win_forme(raw: str) -> str:
    """Forme normale Win32 d'un chemin (voir plus haut) : préfixes retirés, partage administratif local
    ramené à son lecteur, flux NTFS et points ou espaces finaux retirés de chaque nom, noms 8.3 développés.
    Une adresse (« :// ») ou un texte sans chemin reste tel quel."""
    s = raw
    if not s or "://" in s:
        return s
    m = _WIN_PREFIXE_UNC.match(s)
    if m:
        s = "\\\\" + s[m.end():]
    else:
        m = _WIN_PREFIXE_LECTEUR.match(s)
        if m:
            s = s[m.end():]
    m = _WIN_PARTAGE_LOCAL.match(s)
    if m:
        s = m.group(1).upper() + ":" + s[m.end():]
    morceaux = re.split(r"([\\/]+)", s)
    noms = [k for k, x in enumerate(morceaux) if x and not re.fullmatch(r"[\\/]+", x)]
    absolu_ = bool(_WIN_LECTEUR.match(s)) or s[:1] in ("\\", "/")
    for rang, k in enumerate(noms):
        nom = morceaux[k]
        lecteur = ""
        if k == 0 and _WIN_LECTEUR.match(nom):
            lecteur, nom = nom[:2], nom[2:]
        if ":" in nom and (absolu_ or lecteur or rang == len(noms) - 1):
            nom = nom.split(":", 1)[0]  # « guard.py::$DATA », « guard.py:flux » : le fichier lui-même
        if nom not in (".", ".."):
            nom = nom.rstrip(". ") or nom  # « guard.py. », « guard.py  » : Windows retire la fin
        morceaux[k] = lecteur + nom
    s = "".join(morceaux)
    if _WIN_COURT.search(s):
        s = _nom_long(s)
    return s


def _win_douteux(raw: str) -> bool:
    """04/11 : une forme Windows qui ne se ramène pas à un lecteur connu (volume, `GLOBALROOT`, disque physique,
    partage administratif d'une autre machine ou sous un autre nom) : ce qu'elle désigne est INCONNU."""
    if not _WINDOWS or not raw:
        return False
    s = _win_forme(str(raw).strip().strip("'\""))
    return bool(_WIN_PREFIXE.match(s) or _WIN_PARTAGE_ADMIN.match(s))


def _win_sans_prefixe(c: str) -> str:
    """04/11 (CI Windows) : `\\\\?\\C:\\…`, `//?/C:/…`, `\\\\.\\C:\\…`, `\\\\?\\UNC\\…` ramenés à leur forme normale
    AVANT d'y chercher un joker : le « ? » du préfixe n'en est pas un. Avant, `echo x > '\\\\?\\C:\\…\\ipc.py'`
    était lu comme un motif du dossier courant (aucun « / » à couper) : écriture libre d'un fichier protégé.
    Hors Windows, ou sans ce préfixe : tel quel (une forme incertaine est jugée à part, `_win_douteux`)."""
    if _WINDOWS and (_WIN_PREFIXE_UNC.match(c) or _WIN_PREFIXE_LECTEUR.match(c)):
        return _win_forme(c)
    return c


def _msys(raw: str) -> str:
    if not _WINDOWS:
        return raw
    raw = _win_forme(raw)  # 04/11 (lot A) : \\?\, flux NTFS, point final, noms 8.3
    if raw == "/tmp" or raw.startswith("/tmp/"):  # Git Bash monte /tmp sur %TEMP% (profil de l'utilisateur)
        return Path(tempfile.gettempdir()).as_posix() + raw[4:]
    m = _MSYS.match(raw)
    return m.group(1).upper() + ":" + (raw[m.end():] or "/") if m else raw


def _resolve(raw: str) -> Path:
    p = Path(_msys(raw.strip().strip("'\""))).expanduser()
    return (_base() / p).resolve() if not p.is_absolute() else p.resolve()


def _write_level(raw_path: str) -> str | None:
    if not raw_path:
        return None
    return _path_level(_resolve(raw_path))


def _path_level(p: Path) -> str | None:
    """Gravité d'une écriture dans le chemin ABSOLU et normalisé `p`."""
    if secret_du_compte(p) or _under(p, list(_US.racines)):  # 04/11 : même dans un projet (coffre .kdbx…)
        return CRITICAL
    if _under(p, [r.resolve() for r in ALLOWED_WRITE_ROOTS]):
        return None
    if _under(p, [r.resolve() for r in CRITICAL_WRITE_ROOTS]):
        return CRITICAL
    return SENSITIVE


def _bash_redirect_targets(cmd: str) -> list[str]:
    # « 2>&1 » et « >/dev/null » ne sont pas des écritures de fichier.
    targets = re.findall(r"(?<![0-9&])>>?\s*([^\s;|&>]+)", cmd)
    return [t for t in targets if not t.startswith("&") and t != "/dev/null"]


def _mcp_table(server: str) -> dict[str, str] | None:
    """04/11 (lot A) : table de `MCP_POLICY` du serveur ; les navigateurs jetables `playwright2..4` prennent
    celle du navigateur principal (aucun n'est plus libre que l'autre). None : serveur inconnu."""
    if server in MCP_POLICY:
        return MCP_POLICY[server]
    if _PLAYWRIGHT.fullmatch(server):
        return MCP_POLICY["playwright"]
    return None


def _mcp_input_level(server: str, tool: str, tool_input: dict, level: str | None) -> str | None:
    """04/11 (lot A) : gravité relevée d'après l'ENTRÉE de l'outil : `open_path` d'un lanceur, d'un script
    ou d'un exécutable (xdg-open : le programme lancé sort du cgroup du service). Jamais abaissée."""
    if server == "astrel" and tool == "open_path" and level != CRITICAL:
        path = tool_input.get("path") if isinstance(tool_input, dict) else None
        if not isinstance(path, str) or not path.strip():
            return CRITICAL
        p = Path(os.path.expanduser(path.strip()))
        try:
            runs = p.suffix.lower() in _OPEN_CODE_SUFFIXES or (p.is_file() and os.access(p, os.X_OK))
        except (OSError, ValueError):
            runs = True
        if runs:
            return CRITICAL
    return level


def _mcp_level(tool: str) -> str:
    if tool in CRITICAL_MCP_TOOLS:
        return CRITICAL
    if tool in HARMLESS_WITH_CRITICAL_VERB:
        return SENSITIVE
    words = set(re.split(r"[_\-\s]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", tool).lower()))
    return CRITICAL if words & CRITICAL_VERBS else SENSITIVE


# --- lecture du texte d'une commande Bash ---

# Lecture seule : ce qui ne peut rien écrire ni rien lancer d'autre.
_READ_ONLY = {
    "cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "wc", "ls", "ll", "stat", "file",
    "diff", "cmp", "nl", "cut", "tr", "column", "echo", "printf", "pwd", "cd", "true", "false", "test", "[",
    "basename", "dirname", "realpath", "readlink", "du", "df", "md5sum", "sha1sum", "sha256sum", "jq",
    "which", "type", "date", "od", "strings", "sort", "uniq", "sed", "find", "git", "pytest", "read",
}
_SHELL_WORDS = {"for", "in", "do", "done", "if", "then", "else", "elif", "fi", "while", "until", "{", "}", "(", ")"}
_GIT_READ = {"status", "diff", "log", "show", "blame", "grep", "ls-files", "rev-parse", "describe", "shortlog"}
_FIND_WRITE = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}
_SYSTEM_BIN = re.compile(r"(?:/usr(?:/local)?)?/s?bin/[^/]+")


# --- sed (19/10, relecture) : une expression ne suffisait pas (`$!w f`, `sed e`, `s|a|b|e`
# passaient). Le script est lu commande par commande, comme GNU sed. Dans le doute
# (syntaxe inconnue) : None, donc ni lisible ni sûr.
_SED_SIMPLE = set("=dDgGhHnNpPxzF")
_SED_LONG = ("in-place", "expression", "file", "line-length", "null-data", "zero-terminated", "quiet", "silent",
             "sandbox", "posix", "regexp-extended", "separate", "unbuffered", "follow-symlinks", "debug",
             "help", "version")


def _sed_delim(s: str, i: int, d: str) -> int | None:
    """Fin (après le délimiteur `d`) d'un motif ou d'un remplacement commencé en `i`."""
    while i < len(s):
        if s[i] == "\\":
            i += 2
        elif s[i] == d:
            return i + 1
        elif s[i] == "\n" and d != "\n":
            return None
        else:
            i += 1
    return None


def _sed_addr(s: str, i: int, second: bool = False) -> int | None:
    """Saute une adresse (`3`, `$`, `/re/I`, `\\%re%`, `1~2`, `+3`, `~4`) ; rend la suite."""
    n = len(s)
    if i < n and (s[i].isdigit() or (second and s[i] in "+~")):
        i += 1
        while i < n and (s[i].isdigit() or s[i] == "~"):
            i += 1
        return i
    if i < n and s[i] == "$":
        return i + 1
    if i < n and s[i] in "/\\":
        if s[i] == "\\":
            if i + 1 >= n or s[i + 1] in "\n\\":
                return None
            i += 1
        j = _sed_delim(s, i + 1, s[i])
        if j is None:
            return None
        while j < n and s[j] in "IM":
            j += 1
        return j
    return i


def _sed_line(s: str, i: int) -> int:
    """Fin de la ligne commencée en `i` (texte de a/i/c, fichier de w/r, commande de e).
    Arrêtée au PREMIER saut de ligne, même précédé de « \\ » : la ligne suivante est
    relue comme des commandes (au pire une question de trop, jamais une commande cachée)."""
    j = s.find("\n", i)
    return len(s) if j < 0 else j


def _sed_scan(s: str, flat: bool = False) -> list[tuple[str, str]] | None:
    """Commandes de ce script sed qui ne font pas que LIRE et afficher : ("e", commande),
    ("w", fichier), ("r", fichier). Liste vide : lisible. None : syntaxe non reconnue."""
    found: list[tuple[str, str]] = []
    n, i = len(s), 0
    while i < n:
        c = s[i]
        if c in " \t\n;}":
            i += 1
            continue
        if c == "#":
            i = _sed_line(s, i)
            continue
        j = _sed_addr(s, i)
        if j is None:
            return None
        if j != i:
            while j < n and s[j] in " \t":
                j += 1
            if j < n and s[j] == ",":
                j = _sed_addr(s, j + 1, second=True)
                if j is None:
                    return None
        while j < n and s[j] in " \t!":
            j += 1
        if j >= n:
            return None  # adresse sans commande
        c, i = s[j], j + 1
        if c in _SED_SIMPLE or c == "{":
            continue
        if c in "qQlL":  # code de sortie, largeur
            while i < n and (s[i] in " \t" or s[i].isdigit()):
                i += 1
        elif c in "aic":
            # texte jusqu'à la fin de la ligne pour sed ; `flat` (ligne aplatie par `_plain`, sauts
            # de ligne changés en « ; ») : arrêté aussi au premier « ; », lu plus court.
            m = re.compile(r"[\n;]" if flat else r"\n").search(s, i)
            i = m.start() if m else n
        elif c in ":btTv":
            # nom d'étiquette : arrêté au premier blanc ou « ; » (lu PLUS court que sed au pire)
            while i < n and s[i] in " \t":
                i += 1
            while i < n and s[i] not in " \t\n;}":
                i += 1
        elif c in "sy":
            if i >= n or s[i] in "\n\\":
                return None
            d = s[i]
            i = _sed_delim(s, i + 1, d)
            i = _sed_delim(s, i, d) if i is not None else None
            if i is None:
                return None
            if c == "s":
                while i < n and s[i] in "gpiImMe0123456789":
                    if s[i] == "e":
                        found.append(("e", ""))
                    i += 1
                if i < n and s[i] == "w":
                    end = _sed_line(s, i + 1)
                    found.append(("w", s[i + 1:end].strip()))
                    i = end
        elif c in "ewWrR":
            end = _sed_line(s, i)
            found.append(("e" if c == "e" else "w" if c in "wW" else "r", s[i:end].strip()))
            i = end
        else:
            return None
        while i < n and s[i] in " \t":
            i += 1
        if i < n and s[i] not in "\n;}#":
            return None
    return found


def _sed_args(texts: list[str]) -> tuple[bool, list[tuple[int | None, str]], list[int], bool]:
    """Arguments de sed : (réécrit sur place, [(indice de l'argument ou None, script)],
    indices des fichiers, opaque). Opaque : script dans un fichier (-f) ou option longue
    inconnue. Les options longues abrégées comptent (`--i` = --in-place, comme getopt)."""
    inplace, opaque, scripts, files, script, opts, i = False, False, [], [], False, True, 0
    while i < len(texts):
        t = texts[i]
        if opts and t == "--":
            opts = False
        elif opts and t.startswith("--"):
            name, eq, val = t[2:].partition("=")
            full = [o for o in _SED_LONG if o.startswith(name)] if name else []
            full = [name] if name in _SED_LONG else full
            if len(full) != 1 or full[0] == "file":
                opaque = True
                script = script or (len(full) == 1)
                i += 0 if eq or len(full) != 1 else 1
            elif full[0] == "in-place":
                inplace = True
            elif full[0] in ("expression", "line-length"):
                if not eq:
                    i += 1
                    val = texts[i] if i < len(texts) else ""
                if full[0] == "expression":
                    scripts.append((None if eq else i, val))
                    script = True
        elif opts and t.startswith("-") and len(t) > 1:
            body = t[1:]
            for k, ch in enumerate(body):
                if ch == "i":
                    inplace = True
                    break  # le reste : suffixe de sauvegarde
                if ch in "efl":
                    if not body[k + 1:]:
                        i += 1
                    if ch == "f":
                        opaque = script = True
                    elif ch == "e":
                        scripts.append((None, body[k + 1:]) if body[k + 1:]
                                       else (i, texts[i] if i < len(texts) else ""))
                        script = True
                    break
        elif not script:
            scripts.append((i, t))
            script = True  # premier mot : le script
        else:
            files.append(i)
        i += 1
    return inplace, scripts, files, opaque


def _sed_read_only(args: list[str]) -> bool:
    """sed qui ne fait que LIRE et afficher : ni -i, ni -f, ni e/w/W/r/R (drapeaux de s/// compris)."""
    inplace, scripts, _files, opaque = _sed_args(args)
    # Un morceau inconnu (`_DYN`, « $(…) ») peut contenir « w f » ou « e » : jamais lisible.
    return not (inplace or opaque) and all(_DYN not in script and _sed_scan(script, flat=True) == []
                                           for _k, script in scripts)


# Lecteur de page (less) : -o/-O/--log-file recopient l'entrée dans un fichier, -k/--lesskey*
# chargent des commandes, « +cmd » en lance une au départ. Options longues abrégeables.
_LESS_LONG = ("log-file", "lesskey-file", "lesskey-src", "lesskey-content")


def _less_read_only(args: list[str]) -> bool:
    for a in args:
        if a == "--":
            break
        if a.startswith("+"):
            return False
        if a.startswith("--"):
            name = a[2:].split("=", 1)[0].lower()
            if name and any(o.startswith(name) or name.startswith(o) for o in _LESS_LONG):
                return False
        elif a.startswith("-") and set(a[1:]) & set("oOk"):
            return False
    return True


def _segment_read_only(words: list[str]) -> bool:
    if words[:1] == ["for"]:
        return True  # en-tête de boucle : « for f in a b » ne lance rien (« case » oui : refusé)
    if words and re.fullmatch(r"[A-Za-z_]\w*=.*", words[0], re.S):
        return False  # LESSOPEN=, GIT_EXTERNAL_DIFF=… : une variable peut lancer un programme
    while words and words[0] in _SHELL_WORDS:
        words = words[1:]
    if words[:1] == ["timeout"]:
        words = words[1:]
        while words and words[0].startswith("-"):
            words = words[1:]
        words = words[1:]  # la durée
    if not words:
        return True
    prog = words[0].rsplit("/", 1)[-1]
    args = words[1:]
    if re.fullmatch(r"python3?(\.\d+)?", prog):
        # `python -m pytest …` (les tests) et `python -m py_compile|compileall` seulement.
        return len(args) >= 2 and args[0] == "-m" and args[1] in ("pytest", "py_compile", "compileall")
    if prog not in _READ_ONLY:
        return False
    if "/" in words[0] and prog != "pytest" and not _SYSTEM_BIN.fullmatch(words[0]):
        return False  # 19/10 : « ./cat », « /tmp/ls » : un script qui porte le nom d'un lecteur
    short = [a[1:] for a in args if a.startswith("-") and not a.startswith("--")]
    if prog == "sed":
        # -i (même groupé : « -ni », abrégé : « --i »), -f, et e/w/W/r/R dans le script (19/10 : lu par `_sed_scan`).
        return _sed_read_only(args)
    if prog in ("less", "more"):
        return _less_read_only(args)
    if prog == "find":
        return not any(a in _FIND_WRITE for a in args)
    if prog == "sort":
        return not (any("o" in f for f in short) or any(a.startswith(("--output", "--compress-program"))
                                                        for a in args))
    if prog == "rg":
        return not any(a.startswith("--pre") for a in args)  # --pre lance un programme sur chaque fichier
    if prog == "uniq":
        return len([a for a in args if not a.startswith("-")]) < 2
    if prog == "git":
        rest = list(args)
        while rest and rest[0] in ("-C", "--no-pager", "--git-dir", "--work-tree"):
            rest = rest[2:] if rest[0] in ("-C", "--git-dir", "--work-tree") else rest[1:]
        # « -c core.fsmonitor=… », « grep -O<prog> » : git lancerait un programme.
        return (bool(rest) and rest[0] in _GIT_READ
                and not any(a.startswith(("--output", "-O", "--open-files-in-pager", "--ext-diff", "--textconv"))
                            for a in rest))
    return True


def _read_only(cmd: str) -> bool:
    """La commande ne fait que LIRE (afficher, chercher, comparer, tester) :
    aucune redirection vers un fichier, aucune substitution, aucun heredoc,
    chaque commande de la chaîne dans une liste connue. Dans le doute : non."""
    plain = _plain_no_write(cmd)
    if plain is None:
        return False
    segments = _segments(plain)
    if segments is None:
        return False
    return all(_segment_read_only(seg) for seg in segments)


def _plain_no_write(cmd: str) -> str | None:
    """`_plain(cmd)` si la commande n'a ni substitution, ni heredoc, ni eval/exec/source/
    xargs/tee, ni redirection vers un fichier ; None sinon."""
    if re.search(r"\$\(|`|<\(|>\(|<<|\beval\b|\bexec\b|\bsource\b|\bxargs\b|\btee\b", cmd):
        return None
    if _bash_redirect_targets(cmd) or re.search(r"&>\s*(?!/dev/null)[^\s&]", cmd):
        return None
    # « 2>&1 », « >/dev/null » : pas des écritures, et « & » n'y sépare rien.
    plain = _plain(cmd)
    if ">" in plain:
        return None  # « 1>fichier », « >|fichier » : une écriture que la recherche des cibles ne voit pas
    return plain


def _plain(cmd: str) -> str:
    return re.sub(r"\d?>&\d|&>>?\s*/dev/null|\d?>>?\s*/dev/null", " ", _strip_comments(cmd)).replace("\n", " ; ")


def _strip_comments(cmd: str) -> str:
    """19/10 : retire les commentaires comme le shell, AVANT que les fins de ligne
    deviennent des « ; ». Avant, `shlex` voyait un commentaire à chaque « # » (même dans
    « x#y ») et le prolongeait jusqu'à la fin de TOUTE la commande : « # note⏎python3
    astrel/confirm.py » passait pour une lecture seule. Un « # » ne commence un
    commentaire qu'en début de mot, hors guillemets et non échappé."""
    out: list[str] = []
    quote, word_start, i = "", True, 0
    while i < len(cmd):
        ch = cmd[i]
        if quote:
            out.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(cmd):
                out.append(cmd[i + 1])
                i += 1
            elif ch == quote:
                quote = ""
        elif ch == "\\" and i + 1 < len(cmd):
            out.append(cmd[i:i + 2])
            i += 1
            word_start = False
        elif ch == "#" and word_start:
            end = cmd.find("\n", i)
            if end < 0:
                break
            i = end
            continue
        else:
            out.append(ch)
            if ch in "'\"":
                quote = ch
            word_start = ch in " \t\n;&|()<>"
        i += 1
    return "".join(out)


_TOKENS_MAX = 100_000  # 22/10 (P27) : `shlex` allonge chaque mot caractère par caractère (quadratique)


def _tokens(plain: str) -> list[str] | None:
    # 22/10 (P27, testeur) : 1 Mo de commande prenait 25 s ici. Au-delà, « illisible » : chaque
    # appelant en conclut le plus prudent (pas en lecture seule) et l'analyse structurée juge.
    if len(plain) > _TOKENS_MAX:
        return None
    try:
        lexer = shlex.shlex(plain, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""  # 19/10 : déjà retirés par `_strip_comments`
        return list(lexer)
    except ValueError:
        return None


def _segments(plain: str) -> list[list[str]] | None:
    """Les commandes simples de la chaîne, en mots (None si illisible)."""
    tokens = _tokens(plain)
    if tokens is None:
        return None
    out: list[list[str]] = []
    segment: list[str] = []
    for tok in tokens + [";"]:
        if tok and set(tok) <= set(";&|"):
            if segment:
                out.append(segment)
            segment = []
        else:
            segment.append(tok)
    return out


def _pipelines(plain: str) -> list[list[list[str]]] | None:
    """Les tuyaux de la chaîne (`a | b`), chacun en commandes simples ; None si illisible."""
    tokens = _tokens(plain)
    if tokens is None:
        return None
    out: list[list[list[str]]] = []
    pipe: list[list[str]] = []
    segment: list[str] = []
    for tok in tokens + [";"]:
        if not (tok and set(tok) <= set(";&|")):
            segment.append(tok)
            continue
        if tok not in ("|", "|&", ";", "&", "&&", "||", ";;"):
            return None
        if segment:
            pipe.append(segment)
        segment = []
        if tok not in ("|", "|&"):
            if pipe:
                out.append(pipe)
            pipe = []
    return out


# --- noms du garde-fou cités pour être LUS (19/10, « assouplis la règle », l'utilisateur) ---
# Avant : un nom de `PROTECTED_BASH` (guard.py, confirm.py, hooks/…, ESTOP) cité dans une
# commande pas ENTIÈREMENT en lecture seule = « une action sur le garde-fou », même levé
# (vu : « ls -l hooks/empreinte.json; ls -lt … astrel/confirm.py; ./run.sh --doctor »).
# Désormais la commande est jugée comme si elle ne le citait pas (analyse structurée) si
# CHAQUE commande qui le cite est un lecteur sûr de `_CITE_SAFE` (sans sed -i, git
# checkout…) et si rien ne fait passer le nom ailleurs : ni substitution, heredoc, eval,
# xargs, tee ni redirection d'écriture (`_plain_no_write`), ni tuyau depuis ou vers autre
# chose qu'un lecteur sûr, ni « $ » dans une autre commande ($_, variable de boucle), ni
# changement d'état du shell (variable, export, hash, fonction, `.`), ni outil qui écrit
# des fichiers qu'il ne nomme pas (patch, git apply…). Dans le doute : la carte reste.
_CITE_SAFE = {"cat", "head", "tail", "grep", "egrep", "fgrep", "rg", "wc", "ls", "stat", "file", "diff", "cmp",
              "nl", "cut", "tr", "column", "echo", "basename", "dirname", "realpath", "readlink", "du", "md5sum",
              "sha1sum", "sha256sum", "od", "strings", "sort", "uniq", "sed", "git", "test", "[", "true", "false",
              "pwd"}
_SHELL_STATE = {"export", "declare", "typeset", "local", "readonly", "set", "shopt", "alias", "enable", "builtin",
                "command", "hash", ".", "source", "eval", "exec", "trap", "function", "coproc", "read", "mapfile",
                "readarray", "let", "getopts"}
_OPAQUE_WRITERS = {"patch", "rsync", "tar", "unzip", "cpio", "bsdtar", "7z", "dpkg-source"}
# 19/10 (relecture) : une liste NOIRE de sous-commandes git (checkout, reset…) laissait passer
# checkout-index -a -f, read-tree -u, sparse-checkout, bisect, update-ref, rm, mv, difftool…
# qui réécrivent des fichiers suivis sans les nommer. Liste BLANCHE : le reste redemande.
_GIT_INERT = {"status", "diff", "log", "show", "blame", "grep", "ls-files", "ls-tree", "rev-parse", "describe",
              "shortlog", "cat-file", "rev-list", "name-rev", "show-ref", "for-each-ref", "merge-base",
              "diff-tree", "diff-files", "diff-index", "whatchanged", "count-objects", "check-ignore",
              "show-branch", "cherry", "range-diff", "version", "branch"}
_GIT_GLOBAL_INERT = {"--no-pager", "-P", "--no-optional-locks", "--literal-pathspecs", "--no-replace-objects",
                     "--bare"}
_GIT_RUNS = ("--output", "-O", "--open-files-in-pager", "--ext-diff", "--textconv", "--filters")
_GIT_BRANCH_WRITE = {"--delete", "--move", "--copy", "--force", "--set-upstream-to", "--unset-upstream",
                     "--edit-description", "--track", "--no-track", "--create-reflog", "--recurse-submodules"}
_GIT_BRANCH_FILTERS = {"-l", "--list", "--contains", "--no-contains", "--merged", "--no-merged", "--points-at",
                       "--sort", "--format"}
# Un lanceur de commandes qui cite git ou un outil opaque (`env git …`, `bash -c 'git …'`).
_OPAQUE_IN_TEXT = re.compile(r"(?:^|[\s;&|(`'\"/])(?:git|" + "|".join(map(re.escape, _OPAQUE_WRITERS))
                             + r")(?=[\s;&|)`'\"]|$)")


def _git_inert(args: list[str]) -> bool:
    """`git …` qui ne fait que lire : options globales connues, sous-commande de `_GIT_INERT`,
    rien qui lance un programme ; `branch` seulement pour lister."""
    i = 0
    while i < len(args):
        t = args[i]
        if t in ("-C", "--git-dir", "--work-tree"):
            i += 2
        elif t.startswith(("--git-dir=", "--work-tree=")) or t in _GIT_GLOBAL_INERT:
            i += 1
        else:
            break
    if i >= len(args) or args[i] not in _GIT_INERT:
        return False  # -c, --exec-path, alias, sous-commande qui écrit : demandé
    rest = args[i + 1:]
    if any(a.startswith(_GIT_RUNS) for a in rest):
        return False
    if args[i] == "branch":
        if any(a.split("=", 1)[0] in _GIT_BRANCH_WRITE or (re.match(r"-[^-]", a) and set(a[1:]) & set("dDmMcCfut"))
               for a in rest):
            return False
        return not [a for a in rest if not a.startswith("-")] or any(
            a.split("=", 1)[0] in _GIT_BRANCH_FILTERS for a in rest)
    return True


def _strip_shell_words(words: list[str]) -> list[str]:
    while words and words[0] in _SHELL_WORDS and words[0] != "for":
        words = words[1:]
    return words


def _safe_reader(words: list[str]) -> bool:
    """Commande simple qui ne peut que LIRE ce qu'elle cite et l'afficher."""
    words = _strip_shell_words(words)
    if not words:
        return True
    if "/" in words[0] and not _SYSTEM_BIN.fullmatch(words[0]):
        return False
    prog = words[0].rsplit("/", 1)[-1]
    if prog not in _CITE_SAFE or not _segment_read_only(words):
        return False
    return not (prog == "file" and any(a in ("-C", "--compile") for a in words[1:]))


def _inert(words: list[str]) -> bool:
    """Commande simple qui ne cite pas le garde-fou : ne doit ni changer l'état du shell,
    ni recevoir un nom par « $ » (sauf lecteur sûr), ni écrire des fichiers qu'elle ne
    nomme pas. Le reste (programme lancé, écritures nommées) : analyse structurée."""
    words = _strip_shell_words(words)
    if not words:
        return True
    w0 = words[0]
    if re.match(r"[A-Za-z_]\w*(?:\[[^\]]*\])?\+?=", w0) or "(" in w0 or ")" in w0 or w0 in _SHELL_STATE:
        return False
    prog = w0.rsplit("/", 1)[-1]
    if prog in _OPAQUE_WRITERS or (prog == "printf" and "-v" in words[1:]):
        return False
    if prog == "git":
        if not _git_inert(words[1:]):
            return False
    elif any(_OPAQUE_IN_TEXT.search(w) for w in words[1:]):
        return False  # env/nice/timeout git…, bash -c 'git …', sh -c 'patch …' : la carte reste
    return _safe_reader(words) or not any("$" in w for w in words)


def _cites_only_to_read(cmd: str) -> bool:
    """La commande cite des noms de `PROTECTED_BASH` seulement pour les LIRE (voir plus haut)."""
    plain = _plain_no_write(cmd)
    pipes = _pipelines(plain) if plain is not None else None
    if not pipes:
        return False
    cited = False
    for pipe in pipes:
        if any(PROTECTED_BASH.search(" ".join(seg)) for seg in pipe):
            cited = True
            if not all(_safe_reader(seg) for seg in pipe):
                return False
        elif not all(_inert(seg) for seg in pipe):
            return False
    return cited  # nom vu dans le texte brut mais dans aucune commande lue : doute


_PATTERN_FIRST = {"grep", "egrep", "fgrep", "rg", "sed"}


def _reads_secret(cmd: str, read_only: bool) -> bool:
    """La commande nomme-t-elle le jeton du HUD comme FICHIER ? 15/10 : un
    `grep "hud_url\\|hud-token" cli.py` (motif de recherche) déclenchait « lecture
    du jeton ». Seul ce cas est relâché, et seulement pour une commande en
    lecture seule : le MOTIF d'un grep/rg/sed (premier argument, ou celui de
    -e) peut citer le nom ; tout autre mot qui le contient reste une lecture."""
    if not SECRET_BASH.search(cmd):
        return False
    if not read_only:
        return True
    segments = _segments(_plain(cmd))
    if segments is None:
        return True
    for words in segments:
        prog = words[0].rsplit("/", 1)[-1] if words else ""
        args = words[1:]
        patterns: set[int] = set()
        if prog in _PATTERN_FIRST:
            explicit = False
            for i, a in enumerate(args):
                if a in ("-e", "--regexp", "--expression") and i + 1 < len(args):
                    patterns.add(i + 1)
                    explicit = True
                elif a.startswith(("--regexp=", "--expression=")):
                    patterns.add(i)
                    explicit = True
            if not explicit:
                skip = False
                for i, a in enumerate(args):
                    if skip:
                        skip = False
                        continue
                    if a in ("-f", "--file", "-m", "-A", "-B", "-C", "--max-count", "-g", "--glob", "-t", "--type"):
                        skip = True  # option suivie d'une valeur (un -f FICHIER n'est pas un motif)
                        continue
                    if a.startswith("-"):
                        continue
                    patterns.add(i)
                    break
        for i, a in enumerate(args):
            if i not in patterns and SECRET_BASH.search(a):
                return True
        if words and SECRET_BASH.search(words[0]):
            return True
    return False


# ======================================================================
# 15/10, partie B : ANALYSE STRUCTURÉE des commandes Bash
# (docs/DURCISSEMENT-GARDE-FOU.md). Avant : des motifs dans le texte brut
# (« grep sudo f » passait pour une commande administrateur ; `rm -r -f ~`,
# `cd ~ && rm -rf Documents` ou `rm -rf "$HOME"` passaient). Ici : lecteur de
# mots du shell (`_lex`), découpage en commandes (`_Parser`), puis évaluation
# commande par commande (`_eval_*`) avec le dossier courant et les variables
# connues. Ce qui ne se lit pas, ou dont la cible ne se connaît pas, est confirmé.
# Tout est dans ce fichier exprès : il est protégé, un module à part ne le serait pas.
# ======================================================================

class _Unparsable(Exception):
    """Syntaxe que l'analyse ne sait pas lire : la commande sera confirmée."""


_DYN = "\x00"           # morceau inconnu ($VAR inconnue, $(…))
_FOUND = "\x1dFOUND"    # « {} » de find -exec, entrée de xargs venue d'un find
_MAX_DEPTH = 8          # chaînes exécutées dans des chaînes (bash -c "eval '…'")
_MAX_CANDS = 64         # valeurs possibles d'un mot ({a,b}, for f in …)

_OPS = ("&&", "||", ";;&", ";;", ";&", "|&", "&>>", "&>", ">>", ">|", ">&", "<<<", "<<-", "<<", "<>", "<&",
        ">", "<", "|", "&", ";", "(", ")")
_REDIRS = {"&>>", "&>", ">>", ">|", ">&", "<<<", "<<-", "<<", "<>", "<&", ">", "<"}
_META = set(" \t\r\n;&|<>()")
_GLOB = re.compile(r"[*?]|\[[^\]]+\]")  # « [ » seul (test) n'est pas un joker


class _W:
    """Un mot du shell, en morceaux : ("lit", texte, cité), ("var", nom ou None,
    cité), ("sub", commande, cité) pour $(…) et `…`, ("psub", commande, False)
    pour <(…) et >(…)."""
    __slots__ = ("parts",)

    def __init__(self, parts: list):
        self.parts = parts

    def keyword(self) -> str | None:
        """Le texte du mot s'il peut être un mot réservé (un seul morceau, non cité)."""
        p = self.parts
        return p[0][1] if len(p) == 1 and p[0][0] == "lit" and not p[0][2] else None

    def assign(self) -> tuple[str, "_W"] | None:
        """(nom, valeur) pour « NOM=valeur », sinon None."""
        p = self.parts
        if not p or p[0][0] != "lit" or p[0][2]:
            return None
        m = re.match(r"([A-Za-z_]\w*)\+?=", p[0][1])
        if not m:
            return None
        rest = p[0][1][m.end():]
        return m.group(1), _W(([("lit", rest, False)] if rest else []) + p[1:])


# --- lecture des mots ---

def _close_backtick(s: str, i: int) -> int:
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == "`":
            return i
        i += 1
    raise _Unparsable("`")


def _skip_dquote(s: str, i: int) -> int:
    """Index après le guillemet double fermant (`i` : juste après l'ouvrant)."""
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
        elif c == '"':
            return i + 1
        elif s.startswith("$(", i):
            i = _close_paren(s, i + 2) + 1
        elif c == "`":
            i = _close_backtick(s, i + 1) + 1
        else:
            i += 1
    raise _Unparsable('"')


def _close_paren(s: str, i: int) -> int:
    """Index de la parenthèse qui ferme celle ouverte juste avant `i`."""
    depth = 1
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                raise _Unparsable("'")
            i = j + 1
            continue
        if c == '"':
            i = _skip_dquote(s, i + 1)
            continue
        if c == "`":
            i = _close_backtick(s, i + 1) + 1
            continue
        if s.startswith("<<", i) and not s.startswith("<<<", i):
            # $(cat <<'EOF' … EOF) : le corps du heredoc peut contenir ' ( ) sans
            # signification ; on saute jusqu'à son délimiteur (7/10, faux positif
            # sur un message de commit avec apostrophe ou parenthèse).
            i = _skip_heredoc_body(s, i + 2)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise _Unparsable(")")


def _skip_heredoc_body(s: str, i: int) -> int:
    """`s[i]` suit un « << » : lit le délimiteur, saute la fin de ligne et le corps."""
    if i < len(s) and s[i] == "-":
        i += 1
    while i < len(s) and s[i] in " \t":
        i += 1
    m = re.compile(r"""(['"]?)([A-Za-z_]\w*)\1""").match(s, i)
    if not m:
        return i  # << VAR : délimiteur dynamique, laissé à l'analyse normale
    delim, i = m.group(2), m.end()
    nl = s.find("\n", i)
    if nl < 0:
        return len(s)
    i = nl + 1
    while i < len(s):
        nl = s.find("\n", i)
        line = s[i:] if nl < 0 else s[i:nl]
        if line.lstrip("\t") == delim or line == delim:
            return len(s) if nl < 0 else nl + 1
        i = len(s) if nl < 0 else nl + 1
    return len(s)


def _close_brace(s: str, i: int) -> int:
    depth = 1
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            i = _skip_dquote(s, i + 1)
            continue
        if s.startswith("$(", i):
            i = _close_paren(s, i + 2) + 1
            continue
        if s.startswith("${", i):
            depth += 1
            i += 2
            continue
        if c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise _Unparsable("}")


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f",
            "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}


def _unescape(s: str) -> str:
    """Échappements de $'…', printf et echo -e (\\n, \\x73, \\163, \\u0073) :
    `$'\\x73udo'` est un `sudo`."""
    out: list[str] = []
    i = 0
    while i < len(s):
        if s[i] != "\\" or i + 1 >= len(s):
            out.append(s[i])
            i += 1
            continue
        rest = s[i + 1:]
        m = re.match(r"x([0-9a-fA-F]{1,2})|u([0-9a-fA-F]{1,4})|U([0-9a-fA-F]{1,8})", rest)
        if m:
            try:
                out.append(chr(int(next(g for g in m.groups() if g), 16)))
            except (ValueError, OverflowError):
                out.append("?")
            i += 1 + m.end()
            continue
        m = re.match(r"0?([0-7]{1,3})", rest)
        if m:
            out.append(chr(int(m.group(1), 8) & 0xFF))
            i += 1 + m.end()
            continue
        out.append(_ESCAPES.get(rest[0], "\\" + rest[0]))
        i += 2
    return "".join(out)


def _spill_subs(text: str, parts: list, quoted: bool) -> None:
    """Ajoute un morceau ("sub", …) pour chaque $(…) ou `…` de `text` (ceux-là
    s'exécutent même dans une arithmétique ou un ${x:-…})."""
    k = 0
    while True:
        hits = [h for h in (text.find("$(", k), text.find("`", k)) if h >= 0]
        if not hits:
            return
        h = min(hits)
        if text[h] == "$":
            stop = _close_paren(text, h + 2)
            parts.append(("sub", text[h + 2:stop], quoted))
        else:
            stop = _close_backtick(text, h + 1)
            parts.append(("sub", text[h + 1:stop], quoted))
        k = stop + 1


def _lex_dollar(s: str, i: int, parts: list, quoted: bool) -> int:
    """`s[i]` vaut « $ » : ajoute le morceau, rend l'index qui suit."""
    n = len(s)
    if s.startswith("$((", i):  # arithmétique : une valeur inconnue, mais ses $(…) s'exécutent
        end = _close_paren(s, i + 2)
        _spill_subs(s[i + 3:end], parts, quoted)
        parts.append(("var", None, quoted))
        return end + 1
    if s.startswith("$(", i):
        end = _close_paren(s, i + 2)
        parts.append(("sub", s[i + 2:end], quoted))
        return end + 1
    if s.startswith("${", i):
        end = _close_brace(s, i + 2)
        inner = s[i + 2:end]
        derived = re.fullmatch(r"([A-Za-z_]\w*|\d)[%#/:^,].*", inner, re.S)
        if re.fullmatch(r"[A-Za-z_]\w*|\d+|@", inner):
            parts.append(("var", inner, quoted))
        elif "$(" in inner or "`" in inner:
            # ${x:-$(date)}, ${x:=$(cmd)} : le $(…) s'exécute, la valeur reste inconnue (8/10).
            _spill_subs(inner, parts, quoted)
            parts.append(("var", None, quoted))
        elif derived:
            parts.append(("dvar", derived.group(1), quoted))  # ${f%.bin}, ${x:-…} : tirée de f
        else:
            parts.append(("var", None, quoted))  # ${#x}, ${x[@]} : inconnue
        return end + 1
    m = re.compile(r"[A-Za-z_]\w*").match(s, i + 1)
    if m:
        parts.append(("var", m.group(0), quoted))
        return m.end()
    if i + 1 < n and s[i + 1] in "123456789@":
        parts.append(("var", s[i + 1], quoted))  # paramètres d'une fonction ou d'un script
        return i + 2
    if i + 1 < n and s[i + 1] in "0*#?$!-":
        parts.append(("var", None, quoted))
        return i + 2
    if not quoted and s.startswith("$'", i):
        j, raw = i + 2, []
        while j < n:
            if s[j] == "\\" and j + 1 < n:
                raw.append(s[j:j + 2])
                j += 2
                continue
            if s[j] == "'":
                parts.append(("lit", _unescape("".join(raw)), True))
                return j + 1
            raw.append(s[j])
            j += 1
        raise _Unparsable("$'")
    if not quoted and s.startswith('$"', i):
        return _lex_dquote(s, i + 2, parts)
    parts.append(("lit", "$", True))
    return i + 1


def _lex_dquote(s: str, i: int, parts: list) -> int:
    buf: list[str] = []
    while i < len(s):
        c = s[i]
        if c == '"':
            parts.append(("lit", "".join(buf), True))
            return i + 1
        if c == "\\" and i + 1 < len(s) and s[i + 1] in '$`"\\\n':
            if s[i + 1] != "\n":
                buf.append(s[i + 1])
            i += 2
            continue
        if c == "$" or c == "`":
            if buf:
                parts.append(("lit", "".join(buf), True))
                buf = []
            if c == "$":
                i = _lex_dollar(s, i, parts, True)
            else:
                j = _close_backtick(s, i + 1)
                parts.append(("sub", s[i + 1:j].replace("\\`", "`"), True))
                i = j + 1
            continue
        buf.append(c)
        i += 1
    raise _Unparsable('"')


def _lex_word(s: str, i: int) -> tuple[_W, int]:
    parts: list = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            parts.append(("lit", "".join(buf), False))
            buf.clear()

    n = len(s)
    while i < n:
        c = s[i]
        if c in _META:
            break
        if c == "\\":
            if i + 1 < n and s[i + 1] == "\n":
                i += 2
                continue
            flush()
            if i + 1 < n:
                parts.append(("lit", s[i + 1], True))
            i += 2
        elif c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                raise _Unparsable("'")
            flush()
            parts.append(("lit", s[i + 1:j], True))
            i = j + 1
        elif c == '"':
            flush()
            i = _lex_dquote(s, i + 1, parts)
        elif c == "`":
            j = _close_backtick(s, i + 1)
            flush()
            parts.append(("sub", s[i + 1:j].replace("\\`", "`"), False))
            i = j + 1
        elif c == "$":
            flush()
            i = _lex_dollar(s, i, parts, False)
        else:
            buf.append(c)
            i += 1
    flush()
    return _W(parts), i


def _read_heredoc(s: str, i: int, hd: dict) -> int:
    body: list[str] = []
    while i < len(s):
        j = s.find("\n", i)
        line = s[i:] if j < 0 else s[i:j]
        i = len(s) if j < 0 else j + 1
        if (line.lstrip("\t") if hd["strip"] else line) == hd["delim"]:
            break
        body.append(line)
    hd["body"] = "\n".join(body)
    return i


def _lex_redir(s: str, i: int, op: str, fd: str, toks: list, pending: list) -> int:
    n = len(s)
    while i < n and s[i] in " \t":
        i += 1
    if op in (">&", "<&"):
        m = re.compile(r"\d+-?|-").match(s, i)
        if m and (m.end() >= n or s[m.end()] in _META):
            toks.append(("redir", "dup", fd, None, None))  # 2>&1, >&-
            return m.end()
    if s.startswith(("<(", ">("), i):
        end = _close_paren(s, i + 2)
        toks.append(("redir", op, fd, _W([("psub", s[i + 2:end], False)]), None))
        return end + 1
    if i >= n or s[i] in _META:
        raise _Unparsable(op)
    w, i = _lex_word(s, i)
    if op in ("<<", "<<-"):
        if any(p[0] != "lit" for p in w.parts):
            raise _Unparsable(op)
        hd = {"delim": "".join(p[1] for p in w.parts), "quoted": any(p[2] for p in w.parts),
              "strip": op == "<<-", "body": None}
        pending.append(hd)
        toks.append(("redir", op, fd, None, hd))
    else:
        toks.append(("redir", op, fd, w, None))
    return i


def _lex(s: str) -> list[tuple]:
    """Jetons : ("w", _W), ("op", texte), ("redir", op, fd, cible _W, heredoc)."""
    toks: list[tuple] = []
    pending: list[dict] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if s.startswith("\\\n", i):
            i += 2
        elif c in " \t\r":
            i += 1
        elif c == "\n":
            toks.append(("op", "\n"))
            i += 1
            for hd in pending:
                i = _read_heredoc(s, i, hd)
            pending = []
        elif c == "#":
            j = s.find("\n", i)
            i = n if j < 0 else j
        elif s.startswith(("<(", ">("), i):
            end = _close_paren(s, i + 2)
            toks.append(("w", _W([("psub", s[i + 2:end], False)])))
            i = end + 1
        else:
            op = next((o for o in _OPS if s.startswith(o, i)), None)
            if op is None:
                w, i = _lex_word(s, i)
                fd = w.keyword()
                if fd and fd.isdigit() and i < n and s[i] in "<>":  # 2>fichier
                    op = next(o for o in _OPS if s.startswith(o, i))
                    i = _lex_redir(s, i + len(op), op, fd, toks, pending)
                else:
                    toks.append(("w", w))
            else:
                i += len(op)
                if op in _REDIRS:
                    i = _lex_redir(s, i, op, "", toks, pending)
                else:
                    toks.append(("op", op))
    for hd in pending:
        hd["body"] = ""
    return toks


# --- découpage en commandes ---
# Nœuds : ("cmd", mots, redirections), ("pipe", étages), ("sub", suite, redirs)
# pour ( … ) et case, ("group", suite, redirs) pour { … }, ("func", nom, corps) ;
# une suite est une liste de (nœud, séparateur qui suit).

_TRANSPARENT = {"if", "then", "elif", "else", "fi", "while", "until", "do", "done", "!"}
_ENDS = {";", "&", "&&", "||", "\n", ")", ";;", ";&", ";;&", "|", "|&"}


class _Parser:
    def __init__(self, toks: list[tuple]):
        self.toks = toks
        self.i = 0
        self.subwords: list[_W] = []  # mots sautés (arithmétique, tableau, case) dont les $(…) s'exécutent

    def peek(self, k: int = 0):
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def is_op(self, tok, *ops) -> bool:
        return tok is not None and tok[0] == "op" and (not ops or tok[1] in ops)

    def kw(self, tok) -> str | None:
        return tok[1].keyword() if tok is not None and tok[0] == "w" else None

    def parse(self) -> list:
        items = self.seq((), ())
        if self.peek() is not None:
            raise _Unparsable(str(self.peek()[1]))
        return items

    def seq(self, stop_ops, stop_words) -> list:
        items: list = []
        while True:
            while self.is_op(self.peek(), "\n", ";", "&"):
                self.i += 1
            tok = self.peek()
            if tok is None or (stop_ops and self.is_op(tok, *stop_ops)) or self.kw(tok) in stop_words:
                return items
            if self.is_op(tok) and tok[1] != "(":
                raise _Unparsable(tok[1])
            node = self.pipeline(stop_words)
            conn = None
            if self.is_op(self.peek(), ";", "&", "&&", "||", "\n"):
                conn = self.peek()[1]
                self.i += 1
                if conn in ("&&", "||"):
                    while self.is_op(self.peek(), "\n"):
                        self.i += 1
            items.append((node, conn))

    def pipeline(self, stop_words) -> tuple:
        stages = [self.command(stop_words)]
        while self.is_op(self.peek(), "|", "|&"):
            self.i += 1
            while self.is_op(self.peek(), "\n"):
                self.i += 1
            stages.append(self.command(stop_words))
        return ("pipe", stages) if len(stages) > 1 else stages[0]

    def redirs(self) -> list:
        out = []
        while self.peek() is not None and self.peek()[0] == "redir":
            out.append(self.peek())
            self.i += 1
        return out

    def skip_parens(self) -> None:
        depth = 0
        while self.peek() is not None:
            tok = self.peek()
            self.i += 1
            if tok[0] == "w":
                self.subwords.append(tok[1])  # a=( $(cmd) ), for (( i=$(cmd) )) : le $(…) s'exécute
            if self.is_op(tok, "("):
                depth += 1
            elif self.is_op(tok, ")"):
                depth -= 1
                if depth == 0:
                    return
        raise _Unparsable("((")

    def command(self, stop_words) -> tuple:
        while (self.kw(self.peek()) in _TRANSPARENT
               or (self.kw(self.peek()) == "time" and self.is_op(self.peek(1), "("))):
            self.i += 1  # « time ( … ) » : le mot-clé du shell, pas /usr/bin/time
        tok = self.peek()
        if tok is None or self.is_op(tok, *_ENDS) or self.kw(tok) in stop_words:
            return ("cmd", [], [])  # « fi », « done » seuls
        if self.is_op(tok, "("):
            if self.is_op(self.peek(1), "("):  # (( arithmétique ))
                self.skip_parens()
                return ("cmd", [], self.redirs())
            self.i += 1
            body = self.seq((")",), ())
            if not self.is_op(self.peek(), ")"):
                raise _Unparsable("(")
            self.i += 1
            return ("sub", body, self.redirs())
        k = self.kw(tok)
        if k == "{":
            self.i += 1
            body = self.seq((), ("}",))
            if self.kw(self.peek()) != "}":
                raise _Unparsable("{")
            self.i += 1
            return ("group", body, self.redirs())
        if k == "case":
            return self.case()
        if k == "function":
            name = self.kw(self.peek(1)) or ""
            self.i += 2
            if self.is_op(self.peek(), "(") and self.is_op(self.peek(1), ")"):
                self.i += 2
            while self.is_op(self.peek(), "\n"):
                self.i += 1
            return ("func", name, self.command(stop_words))
        words: list[_W] = []
        redirs: list = []
        while True:
            tok = self.peek()
            if tok is None:
                break
            if tok[0] == "w":
                words.append(tok[1])
                self.i += 1
            elif tok[0] == "redir":
                redirs.append(tok)
                self.i += 1
            elif self.is_op(tok, "("):
                if len(words) == 1 and self.is_op(self.peek(1), ")"):  # nom() { … }
                    self.i += 2
                    while self.is_op(self.peek(), "\n"):
                        self.i += 1
                    return ("func", words[0].keyword() or "", self.command(stop_words))
                assign = words[-1].assign() if words else None
                if (assign and not assign[1].parts) or (words and words[0].keyword() == "for"):
                    self.skip_parens()  # tab=(a b c), for ((i=0; …))
                    continue
                raise _Unparsable("(")
            else:
                break
        return ("cmd", words, redirs)

    def case(self) -> tuple:
        self.i += 1
        if self.peek() is None or self.peek()[0] != "w":
            raise _Unparsable("case")
        self.subwords.append(self.peek()[1])  # case $(cmd) in : le $(…) s'exécute
        self.i += 1
        while self.is_op(self.peek(), "\n"):
            self.i += 1
        if self.kw(self.peek()) != "in":
            raise _Unparsable("case")
        self.i += 1
        items: list = []
        while True:
            while self.is_op(self.peek(), "\n", ";"):
                self.i += 1
            tok = self.peek()
            if tok is None:
                raise _Unparsable("esac")
            if self.kw(tok) == "esac":
                self.i += 1
                break
            if self.is_op(tok, "("):
                self.i += 1
            while not self.is_op(self.peek(), ")"):  # motifs a|b)
                if self.peek() is None or not (self.peek()[0] == "w" or self.is_op(self.peek(), "|")):
                    raise _Unparsable("case")
                if self.peek()[0] == "w":
                    self.subwords.append(self.peek()[1])  # motif $(cmd)) : s'exécute aussi
                self.i += 1
            self.i += 1
            items += self.seq((";;", ";&", ";;&"), ("esac",))
            if self.is_op(self.peek(), ";;", ";&", ";;&"):
                self.i += 1
        return ("sub", items, self.redirs())


# --- évaluation ---

@dataclass
class _Ctx:
    cwds: list | None                 # dossiers courants possibles (None : inconnu)
    env: dict = field(default_factory=dict)       # nom → [(valeur, joker)] ou None (inconnue)
    seen: list = field(default_factory=list)      # tous les dossiers courants vus (cd -, popd)
    written: dict = field(default_factory=dict)   # fichier écrit plus tôt dans la commande → (genre, texte)
    depth: int = 0
    errexit: bool = False             # set -e : un cd raté arrête tout
    found: list | None = None         # racines du find en cours (pour « {} »)
    funcs: dict = field(default_factory=dict)     # fonctions définies dans la commande → corps
    remote: bool = False              # commande lancée sur une autre machine (ssh hôte '…')
    budget: list = field(default_factory=lambda: [0])  # compteur partagé d'appels de fonction (plafond)
    launch: bool = False              # variable lanceuse posée pour la commande en cours (`PAGER=… git log`)
    env_unknown: bool = False         # environnement changé par une voie non suivie (eval, fonction, git config…)
    dirs_stable: bool = False         # la commande ne supprime ni ne déplace rien : un dossier existant le reste
    # 23/10 (audit P29, constat 4) : (filtres, racines affichées) de la liste qui alimente xargs
    # (`find logs -name x | xargs rm`, `grep -rl … | xargs sed -i`), pour `_find_guard_hits`.
    found_spec: tuple | None = None

    def child(self, **kw) -> "_Ctx":
        return dataclasses.replace(self, env=dict(self.env), **kw)


@dataclass
class _X:
    """Un mot développé : ses valeurs possibles."""
    cands: list
    dyn: bool = False      # contient une partie inconnue (_DYN)
    glob: bool = False     # joker non cité
    split: bool = False    # inconnue NON citée : peut devenir plusieurs mots, options comprises
    subs: tuple = ()       # commandes de $(…) / <(…) contenues
    src: str = ""          # texte à relire comme du shell (bash -c, eval) : inconnues gardées en $NOM

    @property
    def text(self) -> str:
        return self.cands[0] if self.cands else ""


def _lit(text: str) -> _X:
    return _X([text], src=text)


def _braces(s: str) -> list[str]:
    """Développement des accolades (marques \\x01 \\x02 \\x03 pour { } , non cités)."""
    i = s.find("\x01")
    while i >= 0:
        depth, commas, j = 0, [], i
        while j < len(s):
            if s[j] == "\x01":
                depth += 1
            elif s[j] == "\x02":
                depth -= 1
                if depth == 0:
                    break
            elif s[j] == "\x03" and depth == 1:
                commas.append(j)
            j += 1
        if j >= len(s):
            return [s]
        pre, post = s[:i], s[j + 1:]
        if commas:
            bounds = [i] + commas + [j]
            out: list[str] = []
            for a, b in zip(bounds, bounds[1:]):
                out += _braces(pre + s[a + 1:b] + post)
                if len(out) > _MAX_CANDS:
                    break
            return out
        m = re.fullmatch(r"(-?\d+)\.\.(-?\d+)", s[i + 1:j])
        if m and abs(int(m.group(2)) - int(m.group(1))) < _MAX_CANDS:
            a, b = int(m.group(1)), int(m.group(2))
            step = 1 if b >= a else -1
            out = []
            for k in range(a, b + step, step):  # plafonné : « {1..60}{1..60}… » exploserait (9/10)
                out += _braces(pre + str(k) + post)
                if len(out) > _MAX_CANDS:
                    return out[:_MAX_CANDS]
            return out
        i = s.find("\x01", i + 1)
    return [s]


def _var_value(name: str | None, ctx: _Ctx) -> list | None:
    if name is None:
        return None
    if name in ctx.env:
        return ctx.env[name]
    if name == "HOME":
        return [(str(Path.home()), False)]
    if name == "PWD":
        return [(str(c), False) for c in ctx.cwds] if ctx.cwds else None
    if name in ("USER", "LOGNAME"):
        return [(getpass.getuser(), False)]
    if name == "TMPDIR":
        return [(os.environ.get("TMPDIR") or "/tmp", False)]
    return None


def _sub_value(text: str, ctx: _Ctx) -> list | None:
    """Valeur de $(…) quand elle se devine : $(pwd), $(mktemp) (un nom neuf dans /tmp)."""
    try:
        toks = _lex(text.strip())
    except _Unparsable:
        toks = []
    if 2 <= len(toks) <= 3 and all(t[0] == "w" for t in toks) and toks[0][1].keyword() in ("basename", "dirname"):
        prog = toks[0][1].keyword()
        x = _expand(toks[1][1], ctx)
        suffix = _expand(toks[2][1], ctx).text if len(toks) == 3 else ""
        res = []
        for c in x.cands:
            c = c.rstrip("/") or "/"
            r = os.path.basename(c) if prog == "basename" else (os.path.dirname(c) or ".")
            if suffix and r.endswith(suffix) and r != suffix:
                r = r[:-len(suffix)]
            res.append((r, False))
        return res
    try:
        a = shlex.split(text)
    except ValueError:
        return None
    if a in (["pwd"], ["pwd", "-P"], ["pwd", "-L"]):
        return _var_value("PWD", ctx)
    # 23/10 (audit P29, constat 4) : `$(echo astrel/totalgate.py)`, `$(printf %s …)`, `$(printf …/ES)TOP` :
    # UN mot littéral (ni $, ni `, ni joker, ni \, ni %) recopié tel quel ; la cible n'est plus un doute.
    if not any(ch in text for ch in "$`*?[\\~{") and a and a[0] in ("echo", "printf"):  # ~ et {} : restent un doute
        rest = a[1:]
        if a[0] == "echo" and rest[:1] == ["-n"]:
            rest = rest[1:]
        elif a[0] == "printf" and len(rest) == 2 and rest[0] == "%s":
            rest = rest[1:]
        if len(rest) == 1 and rest[0] and not rest[0].startswith("-") and (a[0] == "echo" or "%" not in rest[0]):
            return [(rest[0], False)]
    if not a or a[0] != "mktemp":
        return None
    base, tmpl, it = os.environ.get("TMPDIR") or "/tmp", "", iter(a[1:])
    for x in it:
        if x in ("-p", "--tmpdir"):
            base = next(it, base)
        elif x.startswith("--tmpdir="):
            base = x.split("=", 1)[1]
        elif not x.startswith("-"):
            tmpl = x
    if "/" in tmpl:
        base = os.path.dirname(tmpl)
    if not base or any(ch in base for ch in "$`") or ".." in base.split("/"):
        return None
    return [(base.rstrip("/") + "/tmp.mktemp", False)]


def _expand(w: _W, ctx: _Ctx) -> _X:
    outs = [("", False)]
    dyn = split = False
    subs: list[str] = []
    src: list[str] = []
    for kind, val, quoted in w.parts:
        if kind == "lit":
            src.append(val)
            g = (not quoted) and bool(_GLOB.search(val))
            if not quoted:
                val = val.replace("{", "\x01").replace("}", "\x02").replace(",", "\x03")
            outs = [(o + val, og or g) for o, og in outs]
            continue
        if kind == "psub":
            subs.append(val)
            src.append("/dev/fd/63")
            outs = [(o + "/dev/fd/63", og) for o, og in outs]
            continue
        if kind == "sub":
            subs.append(val)
            vals = _sub_value(val, ctx)
        else:
            vals = _var_value(val, ctx)
            if kind == "dvar" and vals is not None:
                vals = [(v + _DYN, g) for v, g in vals]
        if vals is not None and any(_DYN in v for v, _g in vals):
            dyn = True  # valeur en partie inconnue (« off_$X.json ») : la partie connue sert
            split = split or not quoted
        if vals is None:
            dyn = True
            split = split or not quoted
            src.append("$(" + val + ")" if kind == "sub" else "${" + (val or "_inconnue_") + "}")
            outs = [(o + _DYN, og) for o, og in outs]
        else:
            src.append(vals[0][0] if vals else "")
            outs = [(o + v, og or vg) for o, og in outs for v, vg in vals]
        if len(outs) > _MAX_CANDS:
            return _X([_DYN], True, False, True, tuple(subs), "".join(src))
    cands: list[str] = []
    for o, _g in outs:
        cands += [e.replace("\x01", "{").replace("\x02", "}").replace("\x03", ",") for e in _braces(o)]
    if len(cands) > _MAX_CANDS:
        return _X([_DYN], True, False, True, tuple(subs), "".join(src))
    return _X(cands or [""], dyn, any(g for _o, g in outs), split, tuple(subs), "".join(src))


def _env_value(w: _W, ctx: _Ctx) -> list | None:
    x = _expand(w, ctx)
    return None if x.cands == [_DYN] else [(c, x.glob) for c in x.cands]


_PROC_CWD = re.compile(r"/proc/(self|thread-self|\d+)/(cwd|root)(/|$)")


def _paths(c: str, ctx: _Ctx) -> list[Path] | None:
    """Chemins absolus normalisés d'un argument, un par dossier courant possible
    (None : impossible à connaître)."""
    if _PROC_CWD.search(c):
        return None  # /proc/self/cwd pointe vers le dossier du SHELL, pas celui du hook (3/10)
    if _win_douteux(c):
        return None  # 04/11 (lot A) : `\\?\Volume{…}\…`, `\\machine\C$\…` : ce qui est désigné est inconnu
    c = _msys(c)  # 03/11 : Git Bash (« /c/Users/… ») sous Windows
    if c.startswith("~"):
        head, sep, tail = c.partition("/")
        if head == "~":
            c = str(Path.home()) + sep + tail
        elif head == "~+":
            return [(b / tail).resolve() for b in ctx.cwds] if ctx.cwds else None
        elif head == "~-":
            return None
        else:
            e = os.path.expanduser(head)
            if e != head:
                c = e + sep + tail
    p = Path(c)
    if p.is_absolute():
        return [p.resolve()]
    if not ctx.cwds:
        return None
    return [(b / p).resolve() for b in ctx.cwds]


def _scopes(c: str, ctx: _Ctx) -> list[tuple[Path, str | None]] | None:
    """(dossier, motif) visés : avec un joker, le dossier qui le contient et le
    motif du premier niveau (« .* » peut viser le dossier parent)."""
    if _win_douteux(c):
        return None  # 04/11 (CI Windows) : `\\?\Volume{…}\…` : inconnu (son « ? » n'est pas un joker)
    c = _win_sans_prefixe(c)  # 04/11 (CI Windows) : le « ? » de `\\?\C:\…` n'est pas un joker
    if not _GLOB.search(c):
        ps = _paths(c, ctx)
        return None if ps is None else [(p, None) for p in ps]
    parts = c.split("/")
    k = next(i for i, part in enumerate(parts) if _GLOB.search(part))
    if ".." in parts[k + 1:]:
        return None  # « */../../x » : où le « .. » retombe après le joker est inconnu (2/10)
    head = "/".join(parts[:k]) or ("/" if c.startswith("/") else ".")
    ps = _paths(head, ctx)
    if ps is None:
        return None
    if parts[k].startswith(".") and fnmatch.fnmatchcase("..", parts[k]):
        return [(p.parent, None) for p in ps]  # le shell ne fait correspondre « .. » qu'à un motif en « . »
    return [(p, parts[k]) for p in ps]


def _hits(p: Path, pattern: str | None, q: Path) -> bool:
    """`q` est-il STRICTEMENT dans ce que vise (p, motif) ?"""
    if p not in q.parents:
        return False
    return pattern is None or _fn_nom(q.relative_to(p).parts[0], pattern)  # 03/11 : sans casse sous Windows


# --- 23/10 (plan P29, demande de l'utilisateur) : catégorie de chaque étiquette ---
# Chaque danger porte la ou les catégories (cases des limites de Total et de la levée,
# `limites.py`) qui le rendent libre. Accordé seulement si TOUTES sont cochées. Une étiquette
# du garde-fou (`guard=True`) est au PLAFOND ; une écriture levable (`guard="dev"`) porte la
# famille de son fichier. Étiquette sans catégorie connue : `_INCONNUE`, toujours demandée
# (`tests/test_limites.py` vérifie que chaque L_*/D_* en a une).
_L = limites
CATS: dict[str, tuple[str, ...]] = {
    L_ADMIN: (_L.ADMINISTRATION,), L_PACKAGES: (_L.ADMINISTRATION,), L_PERMS: (_L.ADMINISTRATION,),
    L_SERVICE: (_L.ADMINISTRATION,), L_KILL: (_L.ADMINISTRATION,),
    L_POWER: (_L.EXTINCTION,), L_SESSION: (_L.EXTINCTION,),
    L_DISK: (_L.DISQUES,), L_DISK_WRITE: (_L.DISQUES,),
    L_DESTROY: (_L.SUPPRESSIONS,), L_DOCKER: (_L.SUPPRESSIONS,), L_RM: (_L.SUPPRESSIONS,),
    L_RM_OUTSIDE: (_L.SUPPRESSIONS,), L_RM_GUARD_DIR: (_L.SUPPRESSIONS,), L_RM_REMOTE: (_L.SUPPRESSIONS,),
    L_RM_REMOTE_WIDE: (_L.SUPPRESSIONS,),
    L_PUSH: (_L.GIT,), L_GIT_ERASE: (_L.GIT,), L_GIT: (_L.GIT_COURANT,),
    L_DOWNLOAD: (_L.EXTERIEUR,), L_REMOTE: (_L.EXTERIEUR,),
    L_GITHUB: (_L.DEPLOIEMENTS,), L_PUBLISH: (_L.DEPLOIEMENTS,),
    L_MAIL: (_L.ENVOIS,), L_RESET: (_L.REMISE_A_ZERO,), L_SCHEDULE: (_L.TACHES,),
    L_MISSION_OUT: (_L.MISSION,),
    L_WRITE_GUARD_DIR: (_L.DOUTES,),
    D_SYNTAX: (_L.DOUTES,), D_PROGRAM: (_L.DOUTES,), D_STDIN: (_L.DOUTES,),
    D_RM: (_L.DOUTES, _L.SUPPRESSIONS), D_WRITE: (_L.DOUTES, _L.ECRITURE),
    # Toujours posées avec `guard=True` : plafond.
    L_GUARD: (_L.PLAFOND,), L_SECRET: (_L.PLAFOND,), L_CLAUDE_ID: (_L.PLAFOND,),
    L_MISSION_FORBIDDEN: (_L.PLAFOND,),
    # 04/11 (lot A) : sources audio (critique), process détaché hors cgroup (question), retrait du marqueur
    # (critique ; là où il décide, `guard=True` et la catégorie « code qui décide », `_brain_mark`).
    L_AUDIO: (_L.ADMINISTRATION,), L_DETACH: (_L.DOUTES,), L_BRAIN_MARK: (_L.ADMINISTRATION,),
    # 04/11 (lot 3b) : zone confidentielle = un secret déclaré par l'utilisateur (case « secrets » seule, jamais Total).
    L_ZONE: (_L.PLAFOND,),
    # 04/11 : secret du compte lu (plafond, case « secrets du compte ») ; modifié (critique, écriture).
    L_SECRET_UTILISATEUR: (_L.PLAFOND,), L_SECRET_UTILISATEUR_MODIF: (_L.ECRITURE,),
    L_AGENT_IMBRIQUE: (_L.PLAFOND,),  # 05/11 : toujours `guard=True`, case « agent imbriqué »
}
_INCONNUE = "inconnue"  # absente du catalogue : `limites.decide` la demande toujours
# Outils natifs (`NATIVE_POLICY`) et outils MCP nommés.
_NATIVE_CATS = {"CronCreate": _L.TACHES, "CronDelete": _L.TACHES, "RemoteTrigger": _L.TACHES,
                "ScheduleWakeup": _L.TACHES, "Workflow": _L.OUTILS, "Artifact": _L.DEPLOIEMENTS,
                "ArtifactData": _L.OUTILS, "ArtifactComments": _L.ENVOIS, "DesignSync": _L.OUTILS}
_MCP_TOOL_CATS = {"power_action": _L.EXTINCTION, "wifi_toggle": _L.EXTINCTION, "update_label": _L.SUPPRESSIONS,
                  "delete_label": _L.SUPPRESSIONS, "reminder_delete": _L.TACHES, "reflex_create": _L.TACHES,
                  "reflex_delete": _L.TACHES}
_VERB_CATS = {
    **dict.fromkeys(("send", "forward", "reply", "post"), _L.ENVOIS),
    **dict.fromkeys(("delete", "remove", "purge", "destroy", "erase"), _L.SUPPRESSIONS),
    **dict.fromkeys(("pay", "purchase", "buy", "checkout", "transfer", "refund", "charge"), _L.PAIEMENTS),
    **dict.fromkeys(("deploy", "deployment", "deployments", "redeploy", "promote", "rollback", "publish", "release"),
                    _L.DEPLOIEMENTS),
    **dict.fromkeys(("share", "invite", "unshare", "revoke", "cancel", "unsubscribe"), _L.PARTAGE),
}
# Familles de fichiers levables (`LIFTABLE`) : case de la levée (et de Total pour HUD, bulle, WhatsApp).
_LIFT_FAMILIES = {p.resolve(): fam for p, fam in (
    (config.ROOT / "astrel" / "guard.py", _L.CODE_GARDE_FOU), (config.ROOT / "astrel" / "confirm.py", _L.CODE_GARDE_FOU),
    (config.HOOK_SCRIPT, _L.CODE_GARDE_FOU), (config.ROOT / "hooks" / "permission_hook_cli.py", _L.CODE_GARDE_FOU),
    (config.ROOT / "astrel" / "bubble.py", _L.FICHIERS_BULLE), (config.ROOT / "astrel" / "minichat.py", _L.FICHIERS_BULLE),
    (config.ROOT / "astrel" / "whatsapp.py", _L.FICHIERS_WHATSAPP), (config.WHATSAPP_BRIDGE_DIR, _L.FICHIERS_WHATSAPP),
    (config.HUD_DIR / "src", _L.FICHIERS_HUD), (config.HUD_DIR / "dist", _L.FICHIERS_HUD),
    (config.HUD_DIR / "index.html", _L.FICHIERS_HUD),
)}


def _familles(p: Path | None) -> tuple[str, ...]:
    """Familles des fichiers levables que vise `p` (le fichier, ou un dossier qui en contient)."""
    if p is None:
        return ()
    return tuple(sorted({f for r, f in _LIFT_FAMILIES.items() if r == p or r in p.parents or p in r.parents}))


def _hits_familles(paths) -> tuple[str, ...]:
    return tuple(sorted({f for q in paths for f in _familles(q)}))


# --- 24/10 (P29 lot B, demande de l'utilisateur : « tout doit être décochable ») : le plafond découpé
# en catégories, chacune sa case de confirmation (`limites.CASES`). Une étiquette du garde-fou
# (`guard=True`) porte le marqueur `PLAFOND` (Total et levée ne l'accordent jamais) PLUS la
# catégorie précise de ce qu'elle touche ; seules les cases décochées la libèrent.
_JOURNAUX = [p.resolve() for p in (config.TOTAL_LOG, config.LIFT_LOG, config.AUDIT_LOG, CONFIRM_CASES_LOG)]
_HOOKS_DIR = (config.ROOT / "hooks").resolve()
_HOOK_DATA = {"empreinte.json", "tool-policy.json"}  # données du doctor et de la politique : « code qui décide »


def _path_cat(p: Path) -> str:
    """Catégorie (case de confirmation) d'un fichier protégé ou du plafond d'écriture."""
    if _under(p, SECRET_PATHS):
        return _L.SECRETS
    if _under(p, CLAUDE_SETTINGS_WRITE) or _under(p, PROJECT_CLAUDE_WRITE) or p in PROJECT_CLAUDE_DIRS \
            or p in _CLAUDE_DIRS:
        return _L.IDENTIFIANTS_CLAUDE
    if _under(p, _JOURNAUX):
        return _L.JOURNAUX
    if p == _HOOKS_DIR or _HOOKS_DIR in p.parents:
        return _L.CODE_IDENTITE if p.name in _HOOK_DATA else _L.CODE_GARDE_FOU
    for r, fam in _LIFT_FAMILIES.items():
        if r == p or r in p.parents:
            return fam
    if _under(p, PROTECTED_CODE) or _loaded_code(p):
        return _L.CODE_IDENTITE
    return _L.ETAT_GARDE_FOU  # autonomie, autorisations, ESTOP, mission, plans, réglages, sockets…


def _path_cats(p: Path | None, pattern: str | None = None, deletion: bool = False) -> tuple[str, ...]:
    """Catégories de ce que vise `p` (le fichier, ou les fichiers protégés qu'il contient ou que le
    joker `pattern` y désigne) ; + suppressions pour une suppression. `p` inconnu : état."""
    if p is None:
        cats = {_L.ETAT_GARDE_FOU}
    else:
        prot = [*_protected_all(), *CEILING_WRITE]
        below = [q for q in prot if _hits(p, pattern, q)] if pattern is not None or not _under(p, prot) else []
        cats = {_path_cat(q) for q in below} or {_path_cat(p)}
    if deletion:
        cats.add(_L.SUPPRESSIONS)
    return tuple(sorted(cats))


_CODE_LABEL = re.compile(r"^une modification de (.+), fichier protégé d'Astrel$")


def _name_cat(name: str) -> str:
    """Catégorie d'un fichier protégé connu par son NOM seul (cité dans un texte)."""
    return _NAME_CATS.get(name.rsplit("/", 1)[-1], _L.CODE_IDENTITE)


def _ceiling_label_cats(label: str) -> tuple[str, ...]:
    """Catégorie précise d'une étiquette du garde-fou posée sans catégorie (voir `_add`)."""
    if label == L_SECRET:
        return (_L.SECRETS,)
    if label == L_SECRET_UTILISATEUR:  # 04/11
        return (_L.SECRETS_UTILISATEUR,)
    if label == L_AGENT_IMBRIQUE:  # 05/11
        return (_L.AGENT_IMBRIQUE,)
    if label == L_CLAUDE_ID:
        return (_L.IDENTIFIANTS_CLAUDE,)
    if label == L_MISSION_FORBIDDEN:
        return (_L.MISSION_INTERDIT,)
    if label in (D_WRITE, D_RM, D_SYNTAX, D_PROGRAM, D_STDIN):
        return (_L.DOUTES,)
    m = _CODE_LABEL.match(label)
    if m:
        return (_name_cat(m.group(1)),)
    return (_L.ETAT_GARDE_FOU,)  # « une action sur le garde-fou », « une modification du garde-fou »


# Noms des fichiers protégés → catégorie (les derniers l'emportent : l'état d'Astrel sur les
# réglages de Claude Code pour « settings.json », le code et les secrets sur le reste).
_NAME_CATS: dict[str, str] = {
    **{p.name: _path_cat(p) for p in (*CLAUDE_SETTINGS_WRITE, *PROJECT_CLAUDE_WRITE)},
    **{p.name: _path_cat(p) for p in PROTECTED_PATHS},
    **{p.name: _path_cat(p) for p in (*PROTECTED_CODE, *LIFTABLE, *SECRET_PATHS)},
    "permission_hook": _L.CODE_GARDE_FOU, "permission_hook.py": _L.CODE_GARDE_FOU,
}


def _label_cats(label: str) -> tuple[str, ...]:
    """Catégories d'une étiquette posée sans catégorie explicite : constante (`CATS`), ou texte
    construit (« une écriture dans X, hors de tes projets »…)."""
    cats = CATS.get(label)
    if cats:
        return cats
    if label.startswith("une écriture dans ") and label.endswith(
            (", hors de tes projets", ", qui contient un fichier sensible")):
        return (_L.ECRITURE,)
    if label.startswith("une suppression de ") and label.endswith(", hors de tes projets"):
        return (_L.SUPPRESSIONS,)
    return (_INCONNUE,)


def _mcp_cats(tool: str) -> tuple[str, ...]:
    """Catégories d'un outil MCP qui demande, d'après son nom (comme `_mcp_level`)."""
    if tool in _MCP_TOOL_CATS:
        return (_MCP_TOOL_CATS[tool],)
    if tool in HARMLESS_WITH_CRITICAL_VERB:
        return (_L.OUTILS,)
    words = set(re.split(r"[_\-\s]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", tool).lower()))
    return tuple(sorted({_VERB_CATS[w] for w in words if w in _VERB_CATS})) or (_L.OUTILS,)


def _add(out: list, label: str, level: str = CRITICAL, grantable: bool = True, guard: bool = False,
         cat: str | tuple[str, ...] | None = None) -> None:
    """(étiquette, gravité, autorisable 30 min, garde-fou, catégories) d'un danger trouvé.
    23/10 (plan P29) : `guard=True` = plafond, quoi que dise `cat` ; `guard="dev"` = famille(s)
    du fichier levable (`cat`, sinon plafond) ; sinon `cat`, ou la catégorie de l'étiquette."""
    if isinstance(cat, str):
        cat = (cat,)
    if guard is True:
        # 24/10 (P29 lot B) : marqueur plafond + catégorie précise (case de confirmation) : `cat`
        # donné par l'appelant (chemin connu), sinon déduite de l'étiquette.
        cats: tuple[str, ...] = (_L.PLAFOND,) + tuple(
            c for c in (tuple(cat) if cat else _ceiling_label_cats(label)) if c != _L.PLAFOND)
    elif guard:
        cats = tuple(cat) if cat and all(c in _L.FAMILLES for c in cat) else (_L.PLAFOND,)
    else:
        cats = tuple(cat) if cat else _label_cats(label)
    out.append((label, level, grantable, guard, cats))


def _doubt(out: list, label: str) -> None:
    _add(out, label, CRITICAL, grantable=False)


def _raw_code(text: str, out: list) -> None:
    for pattern, label in _RAW_CODE:
        if pattern.search(text):
            _add(out, label)
    if BRAIN_VAR in text:  # 04/11 (lot A) : `os.environ.pop("ASTREL_BRAIN")`, `env=…` sans le marqueur
        _brain_mark(out)
    if _agent_dans_code(text):  # 05/11 : `subprocess.run(["claude", "-p", …])`, SDK d'agent
        _agent(out)


def _analyze(text: str, ctx: _Ctx, out: list) -> None:
    """Analyse un texte de shell et ajoute à `out` les dangers trouvés."""
    if ctx.depth > _MAX_DEPTH:
        _doubt(out, D_SYNTAX)
        return
    try:
        parser = _Parser(_lex(text))
        items = parser.parse()
    except (_Unparsable, RecursionError):
        _doubt(out, D_SYNTAX)
        _raw_code(text, out)
        if not ctx.remote:
            _cited_fallback(text, out)
        if _AGENT_TEXTE.search(text):  # 05/11 : un CLI d'agent dans un shell illisible
            _agent(out)
        return
    _eval_seq(items, ctx, out)
    for w in parser.subwords:  # $(…) d'une arithmétique, d'un tableau ou d'un case
        for kind, val, _q in w.parts:
            if kind in ("sub", "psub"):
                _analyze(val, _deeper(ctx), out)


def _deeper(ctx: _Ctx, **kw) -> _Ctx:
    return ctx.child(depth=ctx.depth + 1, **kw)


def _is_dir(p: Path) -> bool:
    try:
        return p.is_dir()
    except OSError:
        return False


def _union(a: list | None, b: list | None) -> list | None:
    if a is None or b is None:
        return None
    return list(dict.fromkeys(a + b))


_NO_FALLBACK = object()


def _eval_seq(items: list, ctx: _Ctx, out: list) -> None:
    """Commandes l'une après l'autre. Après un `cd`, la suite tourne dans le nouveau
    dossier ; si le `cd` peut échouer sans tout arrêter (« cd x; rm -rf * »), elle
    peut aussi tourner dans l'ancien : les deux sont jugés."""
    fallback = _NO_FALLBACK
    for k, (node, conn) in enumerate(items):
        before = ctx.cwds
        changed, _feed = _eval_node(node, ctx, out, None)
        if changed and not ctx.errexit:
            if ctx.dirs_stable and ctx.cwds and all(_is_dir(p) for p in ctx.cwds):
                # 22/10 (« il faut assouplir », l'utilisateur) : un `cd` vers un dossier qui EXISTE (et que la
                # commande ne supprime ni ne déplace) ne rate pas : la suite ne tourne pas dans l'ancien
                # dossier. Avant, un agent resté dans `runtime/plans` voyait une carte à chaque commande,
                # même `cd /…/Astrel; node --test …`.
                pass
            elif conn == "&&":  # cd raté : le reste de la chaîne && n'est pas lancé
                fallback = before if fallback is _NO_FALLBACK else _union(fallback, before)
            elif conn == "||" and k + 1 < len(items) and _is_exit(items[k + 1][0]):
                pass  # « cd x || exit »
            else:
                ctx.cwds = _union(ctx.cwds, before)
        if conn not in ("&&", "||") and fallback is not _NO_FALLBACK:
            ctx.cwds = _union(ctx.cwds, fallback)
            fallback = _NO_FALLBACK


def _is_exit(node: tuple) -> bool:
    return node[0] == "cmd" and bool(node[1]) and node[1][0].keyword() in ("exit", "return")


def _eval_node(node: tuple, ctx: _Ctx, out: list, feed) -> tuple[bool, tuple | None]:
    """Rend (le dossier courant a changé, ce que la commande écrit sur sa sortie)."""
    kind = node[0]
    if kind == "cmd":
        return _eval_cmd(node[1], node[2], ctx, out, feed)
    if kind == "func":  # définie, pas lancée : son corps est jugé à chaque appel
        if node[1]:
            ctx.funcs[node[1]] = node[2]
        return False, None
    if kind == "pipe":
        for stage in node[1]:  # chaque étage tourne dans un sous-shell
            _changed, feed = _eval_node(stage, ctx.child(), out, feed)
        return False, None
    _eval_seq(node[1], ctx.child() if kind == "sub" else ctx, out)
    _eval_writes(node[2], ctx, out, None)
    return False, ("pipe", None)


# Variables dont la VALEUR est une commande lancée par le programme qui suit.
_CODE_VARS = {"GIT_SSH_COMMAND", "GIT_SSH", "GIT_EXTERNAL_DIFF", "GIT_PAGER", "PAGER", "LESSOPEN", "LESSCLOSE",
              "EDITOR", "VISUAL", "GIT_EDITOR", "GIT_SEQUENCE_EDITOR", "PROMPT_COMMAND", "BASH_ENV", "ENV",
              "GIT_ASKPASS", "SSH_ASKPASS", "SUDO_ASKPASS", "MANPAGER", "SYSTEMD_PAGER", "BAT_PAGER", "BROWSER"}
# 15/10 (variables lanceuses) : variables qui font lancer ou charger un AUTRE programme
# par celui qui suit (pager, éditeur, préprocesseur, chemin de recherche, bibliothèques,
# configuration lue ailleurs). Présentes (préfixe, `env`, `export` ou affectation plus tôt
# dans la ligne), un « lecteur » n'est plus sûr : il passerait le fichier cité à ce
# programme. Un fichier protégé cité demande alors (`_judge_cited`).
_LAUNCH_VARS = _CODE_VARS | {
    "PATH", "SHELL", "HOME", "XDG_CONFIG_HOME", "LESS", "LESSKEY", "LESSKEYIN", "MANOPT", "GCONV_PATH",
    "PYTHONSTARTUP", "PYTHONPATH", "PYTHONHOME", "PERL5OPT", "PERL5LIB", "RUBYOPT", "NODE_OPTIONS",
    "GIT_DIR", "GIT_EXEC_PATH", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS"}
_LAUNCH_PREFIXES = ("LD_", "GIT_CONFIG_", "BASH_FUNC_")


def _launch_var(name: str) -> bool:
    return name in _LAUNCH_VARS or name.startswith(_LAUNCH_PREFIXES)


def _judge_assign(cands: list, ctx: "_Ctx", out: list) -> None:
    """Une VALEUR de variable qui cite un fichier protégé : question. Bien des programmes
    écrivent dans le fichier nommé par une variable (trace, historique, journal…)."""
    name = _cited_code(list(cands), ctx)
    if name:
        _add(out, _label_code(name), guard=True)


def _eval_cmd(words: list, redirs: list, ctx: _Ctx, out: list, feed) -> tuple[bool, tuple | None]:
    # Ce qui s'exécute d'abord : les $(…) des mots, des cibles, et du corps des heredocs non cités.
    for w in words + [r[3] for r in redirs if r[3] is not None]:
        for kind, val, _q in w.parts:
            if kind in ("sub", "psub"):
                _analyze(val, _deeper(ctx), out)
    stdin = feed
    for _t, op, fd, target, hd in redirs:
        if hd is None and op in ("<", "<>") and target is not None:  # 04/11 : `ssh h < ~/.ssh/id_rsa`, `3< f`
            _judge_compte("<", [_expand(target, ctx)], ctx, out)
        if hd is not None:
            if not hd["quoted"]:
                _body_subs(hd["body"] or "", ctx, out)
            stdin = ("text", hd["body"] or "")
        elif op == "<<<":
            x = _expand(target, ctx)
            stdin = ("pipe", None) if x.dyn else ("text", x.text)
        elif op == "<" and fd in ("", "0"):
            x = _expand(target, ctx)
            _judge_secret([x], ctx, out)
            stdin = _psub_feed(x) or _file_feed(x, ctx)
    i = 0
    assigns = []
    while i < len(words) and words[i].assign() is not None:
        assigns.append(words[i].assign())
        i += 1
    for name, val in assigns:
        x = _expand(val, ctx)
        if name == BRAIN_VAR:  # 04/11 (lot A) : « ASTREL_BRAIN= prog », réaffectation (variable déjà exportée)
            _brain_mark(out)
        if _VAR_AGENT.match(name) and (words[i:] or name == "CLAUDECODE"):
            _agent(out)  # 05/11 : `CLAUDE_CONFIG_DIR=/tmp/c prog`, `CLAUDECODE= …` (exportée par le CLI)
        if name in _CODE_VARS:
            _analyze(x.src.lstrip("|"), _deeper(ctx), out)
        if words[i:] or _launch_var(name):  # « F=…; grep x "$F" » : non exportée, simple valeur
            _judge_assign(x.cands, ctx, out)
    changed, outfeed, prog, args = False, ("pipe", None), "", []
    if words[i:] and words[i].keyword() in ("for", "select"):
        _for(words[i:], ctx)
    elif words[i:]:
        saved = ctx.launch  # « PAGER=… git log f » : ne vaut que pour cette commande
        ctx.launch = saved or any(_launch_var(name) for name, _val in assigns)
        try:
            changed, outfeed, prog, args = _run([_expand(w, ctx) for w in words[i:]], ctx, out, stdin)
        finally:
            ctx.launch = saved
    else:
        for name, val in assigns:
            ctx.env[name] = _env_value(val, ctx)
    downloaded = outfeed is not None and outfeed[0] == "dl"  # curl … > f.sh ; bash f.sh
    _eval_writes(redirs, ctx, out, _content(prog, args, stdin), downloaded)
    return changed, outfeed


def _for(words: list, ctx: _Ctx) -> None:
    """« for NOM in a b c » : NOM prend ces valeurs dans la boucle."""
    name = words[1].keyword() if len(words) > 1 else None
    if not name:
        return
    if len(words) > 2 and words[2].keyword() == "in":
        vals = [_expand(w, ctx) for w in words[3:]]
        ctx.env[name] = [(c, x.glob) for x in vals for c in x.cands][:_MAX_CANDS]
    else:
        ctx.env[name] = None


def _body_subs(body: str, ctx: _Ctx, out: list) -> None:
    """Heredoc non cité : ses $(…) et `…` s'exécutent."""
    i = 0
    try:
        while True:
            found = [k for k in (body.find("$(", i), body.find("`", i)) if k >= 0]
            if not found:
                return
            j = min(found)
            if body.startswith("$((", j):
                i = _close_paren(body, j + 2) + 1
            elif body[j] == "$":
                end = _close_paren(body, j + 2)
                _analyze(body[j + 2:end], _deeper(ctx), out)
                i = end + 1
            else:
                end = _close_backtick(body, j + 1)
                _analyze(body[j + 1:end], _deeper(ctx), out)
                i = end + 1
    except _Unparsable:
        _doubt(out, D_SYNTAX)


def _psub_feed(x: _X) -> tuple | None:
    """`<(commande)` lu comme un fichier : un téléchargement, ou une sortie inconnue."""
    if x.subs and x.text == "/dev/fd/63":
        return ("dl", None) if _downloads(x) else ("pipe", None)
    return None


def _file_feed(x: _X, ctx: _Ctx) -> tuple:
    """Entrée lue dans un fichier : son contenu s'il a été écrit plus tôt dans la commande."""
    if not x.dyn:
        for c in x.cands:
            for p in _paths(c, ctx) or []:
                if str(p) in ctx.written:
                    return ctx.written[str(p)]
    return ("file", None)


def _content(prog: str, args: list, stdin) -> str | None:
    """Ce que la commande écrit sur sa sortie, quand on le sait (echo, printf, cat <<EOF)."""
    if prog in ("echo", "printf"):
        if any(x.dyn for x in args):
            return None
        words = [x.text for x in args]
        if prog == "echo":
            while words and re.fullmatch(r"-[neE]+", words[0]):
                words = words[1:]
        raw = " ".join(words).replace("%s", " ")
        plain = _unescape(raw)
        return raw if plain == raw else raw + "\n" + plain
    if prog == "cat" and not [x for x in args if not x.text.startswith("-")] and stdin and stdin[0] == "text":
        return stdin[1]
    return None


def _eval_writes(redirs: list, ctx: _Ctx, out: list, content: str | None, downloaded: bool = False) -> None:
    for _t, op, _fd, target, _hd in redirs:
        if target is None or op in ("<", "<<<", "<&", "dup"):
            continue
        if all(p[0] == "psub" for p in target.parts):
            continue  # > >(tee …) : un tuyau, pas un fichier
        x = _expand(target, ctx)
        _judge_write(x, ctx, out)
        what = ("dl", None) if downloaded else ("text", content) if content is not None else ("file", None)
        _remember(x, ctx, what, append=op in (">>", "&>>"))


def _remember(x: _X, ctx: _Ctx, what: tuple, append: bool = False) -> None:
    """Note ce qu'on écrit dans un fichier : s'il est lancé plus loin dans la même
    commande (`cat > s.sh <<EOF … ; bash s.sh`), son contenu sera analysé."""
    if x.dyn or len(x.cands) != 1:
        return
    for p in _paths(x.text, ctx) or []:
        old = ctx.written.get(str(p))
        if append and old and old[0] == "text" and what[0] == "text":
            what = ("text", old[1] + "\n" + what[1])
        ctx.written[str(p)] = what


def _exec_file(x: _X, mode: str, ctx: _Ctx, out: list, args: list = ()) -> None:
    """Un fichier lancé (bash f, ./f, source f, python f) : analysé s'il a été écrit
    plus tôt dans la même commande ; un script déjà sur le disque n'est pas lu."""
    if x.dyn:
        return
    for c in x.cands:
        for p in _paths(c, ctx) or []:
            kind, text = ctx.written.get(str(p), (None, None))
            if kind == "dl":
                _add(out, L_DOWNLOAD)
            elif kind == "text" and text is not None:
                m = mode
                if m == "auto":
                    first = text.lstrip().split("\n", 1)[0]
                    m = "code" if first.startswith("#!") and not re.search(r"\b(ba|z|da|k)?sh\b", first) else "sh"
                if m == "sh":
                    _analyze(text, _bind(ctx, list(args)), out)
                else:
                    _raw_code(text, out)


def _downloads(x: _X) -> bool:
    return any(re.search(r"(?<![\w-])(curl|wget)(?![\w-])", s) for s in x.subs)


def _code(xs: list, mode: str, ctx: _Ctx, out: list) -> None:
    """Des arguments qui sont du CODE : du shell (bash -c, eval, trap, ssh hôte '…')
    relu comme tel, ou un autre langage (python -c, awk) où l'on cherche `_RAW_CODE`."""
    if any(_downloads(x) for x in xs):
        _add(out, L_DOWNLOAD)
    text = " ".join(x.src for x in xs)
    if mode == "sh":
        _analyze(text, _deeper(ctx), out)
    else:
        _raw_code(text, out)


def _consume(stdin, mode: str, ctx: _Ctx, out: list) -> None:
    """Un shell ou un interpréteur qui lit son code sur l'entrée standard."""
    if stdin is None:
        return
    kind, val = stdin
    if kind == "dl":
        _add(out, L_DOWNLOAD)
    elif kind == "text":
        if mode == "sh":
            _analyze(val, _deeper(ctx), out)
        else:
            _raw_code(val, out)
    elif kind == "pipe":
        _doubt(out, D_STDIN)  # base64 -d | sh, ls | bash… : du code qu'on ne voit pas
    # « file » : un script déjà sur le disque (non lu, limite connue) ; « found » : des noms de fichiers


def _with_cwd(ctx: _Ctx, x: _X) -> _Ctx:
    if x.dyn:
        return ctx.child(cwds=None)
    new: list = []
    for c in x.cands:
        ps = _paths(c, ctx)
        if ps is None:
            return ctx.child(cwds=None)
        new += ps
    return ctx.child(cwds=new or None)


# --- programmes qui en lancent un autre ---
# préfixe → (options courtes suivies d'une valeur, options longues suivies d'une valeur,
#            mots à sauter avant le programme : durée de timeout, priorité de chrt…)
_PREFIXES: dict[str, tuple[str, set, int]] = {
    "sudo": ("ugCDhprtTU", {"--user", "--group", "--chdir", "--host", "--prompt", "--role", "--type",
                            "--close-from", "--command-timeout", "--other-user"}, 0),
    "doas": ("uC", set(), 0),
    "pkexec": ("", {"--user"}, 0),
    "run0": ("ugD", {"--user", "--group", "--chdir", "--description", "--unit", "--property", "--slice",
                     "--nice", "--setenv", "--machine"}, 0),
    "env": ("uCS", {"--unset", "--chdir", "--split-string"}, 0),
    "command": ("", set(), 0), "builtin": ("", set(), 0), "exec": ("a", set(), 0), "nohup": ("", set(), 0),
    "setsid": ("", set(), 0), "nice": ("n", {"--adjustment"}, 0),
    "ionice": ("cnp", {"--class", "--classdata", "--pid"}, 0), "chrt": ("", set(), 1),
    "taskset": ("", set(), 1), "stdbuf": ("ioe", {"--input", "--output", "--error"}, 0),
    "timeout": ("sk", {"--signal", "--kill-after"}, 1), "time": ("fo", {"--format", "--output"}, 0),
    "unbuffer": ("", set(), 0), "chronic": ("", set(), 0), "catchsegv": ("", set(), 0),
    "strace": ("oepPsuEabIX", set(), 0), "ltrace": ("oepPsuEabn", set(), 0),
    "systemd-inhibit": ("", {"--what", "--who", "--why", "--mode"}, 0), "torsocks": ("", set(), 0),
    "proxychains": ("f", set(), 0), "proxychains4": ("f", set(), 0), "firejail": ("", set(), 0),
    "xvfb-run": ("nsfepw", {"--server-num", "--server-args", "--error-file", "--auth-file",
                            "--xauth-protocol", "--wait"}, 0),
    "dbus-run-session": ("", {"--config-file", "--dbus-daemon"}, 0),
    "npx": ("p", {"--package"}, 0), "bunx": ("", set(), 0), "busybox": ("", set(), 0),
    # 05/11 (agents imbriqués) : `pnpx @anthropic-ai/claude-code`, `wsl claude -p …` (Windows : le Linux de WSL)
    "pnpx": ("", set(), 0), "wsl": ("du", {"--distribution", "--user", "--cd", "--shell-type"}, 0),
    "flock": ("wEc", {"--wait", "--timeout", "--conflict-exit-code", "--command"}, 1),
}


# 04/11 (lot A, audit 3 H3) : marqueur d'environnement du cerveau (même nom que `platform.proc.BRAIN_VAR`,
# vérifié par les tests ; pas d'import : le garde-fou ne dépend que de `config` et `limites`).
BRAIN_VAR = "ASTREL_BRAIN"
_CGROUP: list[bool] = []
# 04/11 (CI Windows, accord de l'utilisateur) : nom du Job Object du cerveau = ce préfixe + la valeur du marqueur
# (même règle que `platform.proc.nom_du_job`, vérifiée par les tests ; pas d'import, comme `BRAIN_VAR`).
JOB_PREFIXE = "Local\\astrel-cerveau-"
_JOB: list[bool] = []


def _brain_mark(out: list) -> None:
    """Le marqueur du cerveau retiré, vidé ou remplacé : un process ainsi lancé passerait pour l'utilisateur là où
    la descendance et le marqueur font foi (`state.from_claude`). Toujours critique ; action sur le garde-fou
    (plafond) là où le marqueur DÉCIDE (aucun confinement : macOS, Linux hors service, Windows hors du Job
    Object). Là où un confinement reconnaît Claude sans le marqueur (Linux en service : cgroup ; Windows : Job
    Object du cerveau) : critique autorisable (rejeu du corpus : `env -u … -u ASTREL_BRAIN` pour lancer les
    tests du dépôt public)."""
    if _marqueur_compte():
        _add(out, L_BRAIN_MARK, guard=True, cat=_L.CODE_IDENTITE)
    else:
        _add(out, L_BRAIN_MARK)


def _marqueur_compte() -> bool:
    """04/11 : le marqueur DÉCIDE-t-il (`state.from_claude`) ? Même règle partout : oui, sauf là où un
    confinement reconnaît Claude sans lui, vu depuis le hook lui-même (l'équivalent de `state.confinement_actif`
    du cœur) : Linux, le cgroup du service (`_cgroup_fiable`) ; Windows, le Job Object du cerveau
    (`_job_fiable`). macOS et tout autre système : aucun confinement, le marqueur décide. Avant : Windows
    rendait toujours « non », même cerveau hors du Job Object (rattachement raté) où le marqueur décide."""
    if sys.platform.startswith("linux"):
        return not _cgroup_fiable()
    if sys.platform == "win32":
        return not _job_fiable()
    return True


def _job_fiable() -> bool:
    """04/11 (CI Windows) : le hook est-il DANS le Job Object du cerveau ? Le cœur le nomme d'après la valeur
    du marqueur (`platform.proc._job`) ; le hook, descendant du CLI dont il hérite l'environnement (que Claude
    ne change pas), l'ouvre par ce nom (droit de lecture seul, `proc` n'en donne pas d'autre) et demande s'il
    y est. Oui : tout ce que Claude lance y naît sans pouvoir en sortir, et `state.from_claude` le reconnaît
    sans le marqueur. Non ou illisible (rattachement raté, hook hors d'Astrel, autre système) : non, le marqueur
    décide. Lu une fois par process (le hook en est un par appel)."""
    if not _JOB:
        _JOB.append(sys.platform == "win32" and _dans_le_job(os.environ.get(BRAIN_VAR, "")))
    return _JOB[0]


def _dans_le_job(jeton: str) -> bool:
    """Windows : ce process est-il dans le Job Object nommé `JOB_PREFIXE + jeton` ? Faux dans le doute."""
    if not re.fullmatch(r"[0-9a-f]{32}", jeton or ""):
        return False  # le marqueur du cœur : 32 chiffres hexadécimaux (`proc.BRAIN_TOKEN`)
    try:
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k.OpenJobObjectW.restype = wintypes.HANDLE
        k.OpenJobObjectW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.IsProcessInJob.restype = wintypes.BOOL
        k.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        h = k.OpenJobObjectW(0x0004, False, JOB_PREFIXE + jeton)  # JOB_OBJECT_QUERY
        if not h:
            return False
        try:
            dedans = wintypes.BOOL()
            return bool(k.IsProcessInJob(k.GetCurrentProcess(), h, ctypes.byref(dedans))) and bool(dedans.value)
        finally:
            k.CloseHandle(h)
    except (OSError, AttributeError, ValueError, TypeError):
        return False


def _cgroup_fiable() -> bool:
    """04/11 (lot A) : un process DÉTACHÉ (setsid, nohup, disown) reste-t-il reconnu comme venant de Claude ?
    Oui sous Linux quand le cœur tourne en service (le hook, son descendant, est dans le cgroup du service :
    `state.from_claude` le lit), sous Windows (Job Object du cerveau, sinon le marqueur, qui survit au
    détachement) et sous macOS (le marqueur `ASTREL_BRAIN` survit au détachement ; le retirer est jugé à part,
    `_marqueur_compte`). Non sous Linux hors service (`./run.sh` d'un terminal) : seule la descendance compte.
    Lu une fois par process (le hook en est un par appel)."""
    if not _CGROUP:
        if not sys.platform.startswith("linux"):
            _CGROUP.append(True)
        else:
            unit = os.environ.get("ASTREL_SERVICE_UNIT", "astrel.service")
            _CGROUP.append(bool(unit) and unit in _own_cgroup().split("/"))
    return _CGROUP[0]


def _own_cgroup() -> str:
    """Cgroup v2 du process (« /user.slice/…/astrel.service »), vide si illisible."""
    try:
        lines = Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return next((ln[3:].strip() for ln in lines if ln.startswith("0::")), "")


def _prefix(prog: str, args: list, ctx: _Ctx, out: list):
    """Rend (arguments du programme lancé, contexte) ou None s'il n'en lance pas."""
    short_arg, long_arg, extra = _PREFIXES[prog]
    if prog in ("sudo", "doas", "pkexec", "run0"):
        _add(out, L_ADMIN)
    if prog in ("setsid", "nohup") and not _cgroup_fiable():
        _add(out, L_DETACH)  # 04/11 (lot A) : hors cgroup du service, il ne descend plus de Claude
    i = 0
    while i < len(args):
        x = args[i]
        t = x.text
        if t == "--":
            i += 1
            break
        if prog == "env" and t == "-" and not x.dyn:  # « env - prog » = env -i (avant : pris pour le programme)
            _brain_mark(out)
            i += 1
            continue
        if not t.startswith("-") or len(t) < 2 or x.dyn:
            break
        name, val = t, None
        if t.startswith("--"):
            name, eq, v = t.partition("=")
            if eq:
                val = _lit(v)
            elif name in long_arg:
                val = args[i + 1] if i + 1 < len(args) else _lit("")
                i += 1
        else:
            body = t[1:]
            if prog == "command" and set(body) & {"v", "V"}:
                return None  # command -v : cherche, ne lance rien
            k = next((k for k, ch in enumerate(body) if ch in short_arg), None)
            if (prog == "env" and "i" in body[:k]) or (prog == "exec" and "c" in body[:k]):
                _brain_mark(out)  # 04/11 (lot A) : env -i, exec -c : environnement vidé, marqueur compris
            if k is not None:
                name = "-" + body[k]
                if body[k + 1:]:
                    val = _lit(body[k + 1:])
                else:
                    val = args[i + 1] if i + 1 < len(args) else _lit("")
                    i += 1
        i += 1
        if prog == "env" and name == "--ignore-environment":
            _brain_mark(out)
        if prog == "env" and name in ("-u", "--unset") and val is not None and (val.dyn or BRAIN_VAR in val.cands):
            _brain_mark(out)  # 04/11 (lot A) : env -u ASTREL_BRAIN, ou un nom calculé
        if prog == "env" and name in ("-u", "--unset") and val is not None and any(_VAR_AGENT.match(c)
                                                                                    for c in val.cands):
            _agent(out)  # 05/11 : env -u CLAUDECODE prog (le CLI imbriqué ne se reconnaît plus comme tel)
        if val is None:
            continue
        if (prog in ("sudo", "run0") and name in ("-D", "--chdir")) or (prog == "env" and name in ("-C", "--chdir")):
            ctx = _with_cwd(ctx, val)
        elif prog == "env" and name in ("-S", "--split-string"):
            try:
                split = [_lit(w) for w in shlex.split(val.text)]
            except ValueError:
                _doubt(out, D_SYNTAX)
                return None
            args = args[:i] + split + args[i:]
        elif prog == "flock" and name in ("-c", "--command"):
            _code([val], "sh", ctx, out)
            return None
        elif prog == "time" and name in ("-o", "--output"):
            _judge_write(val, ctx, out)
        elif prog == "ionice" and name in ("-p", "--pid"):
            return None
    while i < len(args) and (m := re.match(r"([A-Za-z_]\w*)=", args[i].text)):
        if m.group(1) == BRAIN_VAR:  # 04/11 (lot A) : env ASTREL_BRAIN=autre prog
            _brain_mark(out)
        if _VAR_AGENT.match(m.group(1)):  # 05/11 : env CLAUDE_CONFIG_DIR=/tmp/c prog
            _agent(out)
        if _launch_var(m.group(1)):  # sudo/env NOM=valeur programme
            ctx = ctx.child(launch=True)
        if m.group(1) in _CODE_VARS:
            _analyze(args[i].src.partition("=")[2].lstrip("|"), _deeper(ctx), out)
        _judge_assign([c[m.end():] for c in args[i].cands], ctx, out)
        i += 1
    return args[i + extra:], ctx


def _su(args: list, ctx: _Ctx, out: list):
    _add(out, L_ADMIN)
    for i, x in enumerate(args):
        if x.text in ("-c", "--command") and i + 1 < len(args):
            _code([args[i + 1]], "sh", ctx, out)
        elif x.text.startswith("--command="):
            _code([_lit(x.text.split("=", 1)[1])], "sh", ctx, out)
    return None


def _watch(args: list, ctx: _Ctx, out: list):
    i, direct = 0, False
    while i < len(args) and args[i].text.startswith("-"):
        t = args[i].text
        direct = direct or t in ("-x", "--exec")
        i += 2 if t in ("-n", "--interval", "-q", "--equexit") else 1
    if direct:
        return args[i:], ctx
    if args[i:]:
        _code(args[i:], "sh", ctx, out)
    return None


def _xargs(args: list, ctx: _Ctx, stdin) -> tuple[list, _Ctx]:
    """xargs lance son programme avec les mots lus sur l'entrée : ceux d'un echo ou
    d'un heredoc sont connus, ceux d'un find sont « les fichiers trouvés »."""
    i, repl, from_file = 0, None, False
    while i < len(args):
        t = args[i].text
        if t == "--":
            i += 1
            break
        if t.startswith("--"):
            name, eq, _v = t.partition("=")
            if name == "--replace":
                repl = _v if eq else "{}"
            elif name == "--arg-file":
                from_file = True
                i += 0 if eq else 1
            elif not eq and name in ("--delimiter", "--max-args", "--max-procs", "--max-chars", "--max-lines",
                                     "--eof", "--process-slot-var"):
                i += 1
            i += 1
            continue
        if t.startswith("-") and len(t) > 1:
            body = t[1:]
            for k, ch in enumerate(body):
                if ch == "i":
                    repl = body[k + 1:] or "{}"
                    break
                if ch in "le":
                    break  # valeur facultative collée
                if ch in "aEdILnPs":
                    val = body[k + 1:]
                    if not val:
                        val = args[i + 1].text if i + 1 < len(args) else ""
                        i += 1
                    if ch == "I":
                        repl = val
                    from_file = from_file or ch == "a"
                    break
            i += 1
            continue
        break
    inner = list(args[i:])
    if not inner:
        return [], ctx  # xargs seul : echo
    if stdin and stdin[0] == "found" and not from_file:
        item = _X([_FOUND])
        # 23/10 (audit P29, constat 4) : filtres du find ; liste sans filtre de nom (grep -rl, ls,
        # git ls-files) : tout ce qui est sous ses dossiers.
        spec = stdin[2] if len(stdin) > 2 else (
            ([], [(None, r) for r in stdin[1]]) if stdin[1] is not None and not ctx.remote else None)
        ctx = ctx.child(found=stdin[1], found_spec=spec)
    elif stdin and stdin[0] == "text" and not from_file:
        try:
            item = _X(shlex.split(stdin[1])[:_MAX_CANDS] or [""])
        except ValueError:
            item = _X([_DYN], True)
    else:
        item = _X([_DYN], True)
    if repl:
        inner = [_sub_token(x, repl, item) if repl in x.text else x for x in inner]
    else:
        inner.append(item)
    return inner, ctx


def _sub_token(x: _X, token: str, item: _X) -> _X:
    """Remplace `token` (le `{}` d'un xargs -I, d'un find -exec) par `item` DANS les
    candidats ET dans le texte source relu comme shell (1/10 : `sh -c 'rm -rf {}'`)."""
    cands = [c.replace(token, it) for c in x.cands for it in item.cands][:_MAX_CANDS]
    return _X(cands, x.dyn or item.dyn, x.glob, x.split, x.subs, x.src.replace(token, item.text))


def _bind(ctx: _Ctx, args: list) -> _Ctx:
    """Paramètres $1…$9 et $@ d'une fonction ou d'un script."""
    c = _deeper(ctx)
    for k in range(1, 10):
        c.env[str(k)] = [(v, args[k - 1].glob) for v in args[k - 1].cands] if k <= len(args) else [("", False)]
    c.env["@"] = [(v, args[0].glob) for v in args[0].cands] if len(args) == 1 else (None if args else [("", False)])
    return c


_MAX_CALLS = 2000  # appels de fonction cumulés : une fonction qui s'appelle en boucle exploserait (9/10)


def _call(body: tuple, args: list, ctx: _Ctx, out: list, stdin) -> None:
    ctx.budget[0] += 1
    if ctx.depth > _MAX_DEPTH or ctx.budget[0] > _MAX_CALLS:
        _doubt(out, D_SYNTAX)
        return
    _eval_node(body, _bind(ctx, args), out, stdin)


_LAUNCH_VAL = {  # lanceurs : options qui mangent le mot suivant, avant le programme réel (6/10)
    "unshare": {"-S", "-G", "-R", "--map-user", "--map-group", "--wd", "--root", "--setgroups", "--map-users",
                "--map-groups"},
    "setpriv": {"--reuid", "--regid", "--groups", "--inh-caps", "--ambient-caps", "--bounding-set",
                "--securebits", "--pdeathsig", "--selinux-label", "--apparmor-profile", "--landlock-access",
                "--landlock-rule"},
    "nsenter": {"-t", "--target", "-S", "--setuid", "-G", "--setgid", "-w", "--wd", "-r", "--root"},
    "runuser": {"-u", "--user", "-g", "--group", "-G", "--supp-group", "--pty"},
    "chroot": {"--userspec", "--groups"},
}
_LAUNCH_ADMIN = {"chroot", "nsenter", "runuser", "unshare"}


def _launcher(prog: str, args: list, ctx: _Ctx, out: list):
    """Programmes qui en lancent un autre après des options et parfois un argument de
    position (6/10) : unshare, setpriv, nsenter, runuser, chroot, parallel, script, coproc."""
    if prog in _LAUNCH_ADMIN:
        _add(out, L_ADMIN)
    if prog == "coproc":
        # coproc NOM commande  OU  coproc commande : sauter un éventuel NOM d'identifiant.
        named = (len(args) > 1 and not args[0].dyn and re.fullmatch(r"[A-Za-z_]\w*", args[0].text)
                 and not args[1].text.startswith("-"))
        return (args[1:] if named else args), ctx
    if prog == "parallel":
        i = 0
        while i < len(args) and args[i].text.startswith("-") and args[i].text != "--":
            i += 1
        if i < len(args) and args[i].text == "--":
            i += 1
        cmd, data = [], []
        seen = False
        for x in args[i:]:
            if x.text in (":::", "::::", ":::+", "::::+"):
                seen = True
                continue
            (data if seen else cmd).append(x)
        if cmd:
            item = _X([c for x in data for c in x.cands][:_MAX_CANDS] or [_DYN], any(x.dyn for x in data) or not data)
            _run(cmd + [item], ctx.child(), out, None)
        return None
    if prog == "script":
        for i, x in enumerate(args):
            if x.text in ("-c", "--command") and i + 1 < len(args):
                _code([args[i + 1]], "sh", ctx, out)
                return None
            if x.text.startswith("-") and not x.text.startswith("--") and "c" in x.text[1:]:
                k = x.text.index("c")
                val = _lit(x.text[k + 1:]) if x.text[k + 1:] else (args[i + 1] if i + 1 < len(args) else _lit(""))
                _code([val], "sh", ctx, out)
                return None
        return None  # script FICHIER : le fichier journal est dans le cwd
    with_arg = _LAUNCH_VAL.get(prog, set())
    i = 0
    if prog == "chroot":  # chroot NEWROOT COMMANDE : sauter le nouveau dossier racine
        while i < len(args) and args[i].text.startswith("-"):
            i += 2 if args[i].text in with_arg else 1
        i += 1
    else:
        while i < len(args):
            t = args[i].text
            if t == "--":
                i += 1
                break
            if not t.startswith("-") or len(t) < 2:
                break
            name = t.split("=", 1)[0]
            i += 2 if (name in with_arg and "=" not in t) else 1
    return args[i:], ctx


_WRAPPERS = {name: (lambda name: lambda args, ctx, out: _prefix(name, args, ctx, out))(name) for name in _PREFIXES}
_WRAPPERS.update({"su": _su, "watch": _watch})
_WRAPPERS.update({name: (lambda name: lambda args, ctx, out: _launcher(name, args, ctx, out))(name)
                  for name in ("unshare", "setpriv", "nsenter", "runuser", "chroot", "parallel", "script", "coproc")})


def _run(argv: list, ctx: _Ctx, out: list, stdin) -> tuple[bool, tuple | None, str, list]:
    """Retrouve le programme réellement lancé derrière les préfixes, puis le juge.
    Rend (dossier courant changé, sortie, programme, arguments)."""
    for _ in range(16):
        if not argv:
            return False, ("pipe", None), "", []
        w0 = argv[0]
        names = [c.rsplit("/", 1)[-1] for c in w0.cands]
        if (w0.glob or any(_DYN in n or _GLOB.search(n) for n in names)
                or (w0.dyn and not all("/" in c for c in w0.cands))):
            _doubt(out, D_PROGRAM)  # $CMD, $(echo rm), /usr/bin/su?o ; « $DIR/python » reste lisible
            # 05/11 : `$(which claude) -p …`, `$(ls ~/.local/share/claude/versions/*) …`, `$X --dangerously-…`
            calc = " ".join((w0.src, *w0.subs))
            if (_AGENT_TEXTE.search(calc) or "claude/versions" in calc or any(_PROC_EXE.match(c) for c in w0.cands)
                    or _drapeau_agent(argv[1:])):
                _agent(out)
            _judge_cited("", argv[1:], ctx, out, stdin)  # programme inconnu : un fichier protégé cité = question
            _judge_claude_dir("", argv[1:], ctx, out)  # 19/10 (relecture) : `$CMD ~/.claude`
            _judge_compte("", argv[1:], ctx, out)  # 04/11 : `$CMD ~/.ssh/id_rsa`
            _judge_ceiling_cited("", argv[1:], ctx, out)  # 23/10 (audit P29) : `$CMD ~/.claude.json`
            return False, ("pipe", None), "", []
        progs = list(dict.fromkeys(names))
        if len(progs) == 1 and progs[0] in ctx.funcs and "/" not in w0.text:
            _call(ctx.funcs[progs[0]], argv[1:], ctx, out, stdin)
            ctx.env_unknown = True  # ce qu'elle a exporté n'est pas suivi : variable lanceuse possible
            return False, ("pipe", None), progs[0], argv[1:]
        if len(progs) > 1:  # {su,}do, boucle sur des noms de programmes
            for c in w0.cands:
                _run([_lit(c)] + argv[1:], ctx.child(), out, stdin)
            return False, ("pipe", None), "", []
        prog = progs[0]
        if prog == "xargs":
            argv, ctx = _xargs(argv[1:], ctx, stdin)
            stdin = None
            continue
        wrap = _WRAPPERS.get(prog)
        if wrap is None and prog.lower().endswith(".exe"):
            wrap = _WRAPPERS.get(prog[:-4].lower())  # 05/11 : `wsl.exe claude`, `env.exe …` (Git Bash)
        if wrap is None:
            break
        res = wrap(argv[1:], ctx, out)
        if res is None:
            return False, ("pipe", None), prog, argv[1:]
        argv, ctx = res
    else:
        _doubt(out, D_SYNTAX)
        return False, ("pipe", None), "", []
    changed, feed = _program(prog, argv[0], argv[1:], ctx, out, stdin)
    return changed, feed, prog, argv[1:]


# --- programmes ---

_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "mksh", "ash", "rbash", "fish"}
_INTERP = re.compile(r"(?:python|pypy)[\d.]*|node(?:js)?|perl[\d.]*|ruby[\d.]*|php[\d.]*|lua[\d.]*"
                     r"|Rscript|osascript|tclsh[\d.]*")
_CODE_FLAGS = {"python": "c", "pypy": "c", "node": "ep", "perl": "eE", "ruby": "e", "php": "r"}
_LINT_FLAGS = {"php": "l", "node": "c", "perl": "c", "ruby": "c"}  # vérifier la syntaxe : rien n'est lancé
_MAILERS = {"mail", "mailx", "sendmail", "mutt", "msmtp", "neomutt", "s-nail"}
_DISK_PROGS = {"fdisk", "sfdisk", "cfdisk", "gdisk", "sgdisk", "parted", "wipefs", "blkdiscard"}
_PACKAGERS = {"apt", "apt-get", "dpkg", "dnf", "pacman", "snap", "flatpak"}
_POWER = {"poweroff", "reboot", "suspend", "hibernate", "halt", "kexec", "hybrid-sleep", "suspend-then-hibernate",
          "soft-reboot"}


def _operands(a: list[str], with_arg=()) -> list[str]:
    """Mots qui ne sont pas des options (une option de `with_arg` mange le mot suivant)."""
    res, skip = [], False
    for t in a:
        if skip:
            skip = False
        elif t.startswith("-") and len(t) > 1:
            skip = t in with_arg
        else:
            res.append(t)
    return res


def _program(prog: str, w0: _X, args: list, ctx: _Ctx, out: list, stdin) -> tuple[bool, tuple | None]:
    a = [x.text for x in args]
    if "/" in w0.text:
        _exec_file(w0, "auto", ctx, out, args)  # ./script.sh écrit plus tôt dans la commande
    _judge_secret(args, ctx, out, prog)
    _judge_claude_dir(prog, args, ctx, out)  # 19/10 (relecture) : tout le dossier ~/.claude
    _judge_secret_tree(prog, args, ctx, out)  # 22/10 (P27) : lecteur récursif sur un ancêtre d'un secret
    _judge_compte(prog, args, ctx, out)  # 04/11 : secrets du compte (clés SSH, jetons, navigateurs…)
    _judge_cited(prog, args, ctx, out, stdin)
    _judge_ceiling_cited(prog, args, ctx, out)  # 23/10 (audit P29, constat 3) : réglages de Claude Code
    # 19/10 (relecture) : le CLI natif lancé par son chemin (`~/.local/share/claude/versions/2.1.285`)
    _labels("claude" if _CLAUDE_BIN.search(w0.text) else prog, args, a, ctx, out)
    _judge_agent(prog, w0, args, ctx, out)  # 05/11 : un autre agent IA (claude -p, codex, gemini…) sans garde-fou
    _fence_program(prog, args, ctx, out)  # mission en cours : programmes qui modifient des fichiers
    if prog in ("cd", "pushd"):
        return _cd(args, ctx), None
    if prog == "popd":
        ctx.cwds = list(dict.fromkeys(ctx.seen)) or ctx.cwds
        return True, None
    if prog in ("export", "local", "declare", "typeset", "readonly"):
        # 04/11 (lot A) : `export ASTREL_BRAIN=`, `export -n ASTREL_BRAIN`, `local ASTREL_BRAIN` (une locale
        # sans -x masque la variable exportée pour les programmes lancés ensuite) : le marqueur change.
        if any(re.match(re.escape(BRAIN_VAR) + r"(?!\w)", c) for x in args for c in x.cands):
            _brain_mark(out)
        if "CLAUDECODE" in a and ("-n" in a or prog == "local"):
            _agent(out)  # 05/11 : export -n CLAUDECODE, local CLAUDECODE (le CLI imbriqué ne se reconnaît plus)
        for x in args:
            m = re.match(r"([A-Za-z_]\w*)\+?=", x.text)
            if m and _VAR_AGENT.match(m.group(1)) and prog in ("export", "declare", "typeset") and (
                    prog == "export" or any(re.fullmatch(r"-\w*x\w*", t) for t in a)):
                _agent(out)  # 05/11 : export CLAUDE_CONFIG_DIR=…, export CLAUDE_CODE_… (pour un CLI lancé ensuite)
            if m:
                if m.group(1) in _CODE_VARS:  # export PAGER='…' : la valeur sera lancée plus loin
                    _analyze(x.src.partition("=")[2].lstrip("|"), _deeper(ctx), out)
                _judge_assign([c[m.end():] for c in x.cands], ctx, out)
                ctx.env[m.group(1)] = [(c[m.end():], x.glob) for c in x.cands]
            elif x.dyn or (not x.text.startswith("-") and "=" in x.text):
                ctx.env_unknown = True  # nom calculé : quelle variable est posée ?
            elif re.fullmatch(r"[A-Za-z_]\w*", x.text) and ctx.env.get(x.text):
                _judge_assign([v for v, _g in ctx.env[x.text]], ctx, out)  # X=… ; export X
    elif prog == "printf" and any(t.startswith("-v") for t in a):
        ctx.env_unknown = True  # printf -v NOM : une variable posée sans affectation visible
    elif prog == "unset":
        if BRAIN_VAR in a or any(x.dyn for x in args):  # 04/11 (lot A) : unset ASTREL_BRAIN, ou un nom calculé
            _brain_mark(out)
        if any(_VAR_AGENT.match(t) for t in a):  # 05/11 : unset CLAUDECODE (avant de lancer un CLI imbriqué)
            _agent(out)
        for t in a:
            ctx.env.pop(t, None)
    elif prog == "disown":
        if not _cgroup_fiable():
            _add(out, L_DETACH)  # 04/11 (lot A) : hors cgroup du service, le job ne descend plus de Claude
    elif prog == "read":
        for t in _operands(a, {"-p", "-d", "-n", "-N", "-t", "-u", "-a", "-i"}):
            ctx.env[t] = None
    elif prog == "set":
        if any(re.fullmatch(r"-[a-zA-Z]*e[a-zA-Z]*", t) for t in a) or "errexit" in a:
            ctx.errexit = True
    elif prog == "trap":
        if args and not a[0].startswith("-"):
            _code(args[:1], "sh", ctx, out)
    elif prog == "eval":
        _code(args, "sh", ctx, out)
        ctx.env_unknown = True  # ses affectations ne sont pas reportées ici
    elif prog in ("source", "."):
        ctx.env_unknown = True
        if args and _psub_feed(args[0]):
            _consume(_psub_feed(args[0]), "sh", ctx, out)  # source <(curl …)
        elif args:
            _exec_file(args[0], "sh", ctx, out, args[1:])
    elif prog in _SHELLS:
        _shell(args, ctx, out, stdin)
    elif _INTERP.fullmatch(prog):
        _interp(prog, args, ctx, out, stdin)
    elif prog in ("awk", "gawk", "mawk", "nawk"):
        _awk(args, ctx, out)
    elif prog == "ssh":
        _ssh(args, ctx, out, stdin)
    elif prog in ("rm", "rimraf", "unlink"):
        _rm(prog, args, ctx, out)
    elif prog == "find":
        return False, _find(args, ctx, out)
    elif prog in ("fd", "fdfind"):
        _fd(args, ctx, out)
    elif prog in ("cp", "mv", "install", "ln", "scp", "rsync"):
        _copy(prog, args, ctx, out)
    elif prog == "tee":
        for x in args:
            if not x.text.startswith("-"):
                _judge_write(x, ctx, out)
                _remember(x, ctx, stdin if stdin and stdin[0] in ("text", "dl") else ("file", None))
        return False, stdin or ("pipe", None)
    elif prog == "dd":
        for x in args:
            if x.text.startswith("of="):
                _judge_write(_X([c[3:] for c in x.cands], x.dyn, x.glob), ctx, out)
    elif prog in ("tar", "bsdtar"):
        _extract(args, ctx, out, long_opt={"-C", "--directory"}, short_opt="")
    elif prog in ("unzip",):
        _extract(args, ctx, out, long_opt={"-d"}, short_opt="d")
    elif prog == "touch":
        for x in args:
            if not x.text.startswith("-"):
                _judge_write(x, ctx, out)
    elif prog == "sort":
        for i, x in enumerate(args):
            if x.text in ("-o", "--output") and i + 1 < len(args):
                _judge_write(args[i + 1], ctx, out)
            elif x.text.startswith("--output="):
                _judge_write(_lit(x.text.split("=", 1)[1]), ctx, out)
            elif x.text.startswith("-o") and len(x.text) > 2:
                _judge_write(_lit(x.text[2:]), ctx, out)
    elif prog == "sed":
        _sed(args, ctx, out)
    elif prog in ("curl", "wget"):
        return False, _download(prog, args, ctx, out)
    elif prog in ("echo", "printf"):
        text = _content(prog, args, None)
        return False, ("text", text) if text is not None else ("pipe", None)
    listed = _lists_files(prog, a, args, ctx)
    if listed is not None:  # grep -rl, ls, git ls-files… | xargs : des fichiers de ces dossiers
        return False, ("found", listed)
    elif prog == "cat":
        files = [x for x in args if not x.text.startswith("-")]
        if not files:
            return False, stdin or ("pipe", None)
        if len(files) == 1:
            fed = _file_feed(files[0], ctx)
            return False, fed if fed[0] in ("text", "dl") else ("pipe", None)
    return False, ("pipe", None)


def _lists_files(prog: str, a: list[str], args: list, ctx: _Ctx) -> list | None:
    """Dossiers d'où viennent les noms de fichiers qu'écrit la commande (None : pas une liste de fichiers)."""
    paths = None
    if prog in ("grep", "egrep", "fgrep", "rg") and (
            any(re.fullmatch(r"-[a-zA-Z]*[lL][a-zA-Z]*", t) for t in a)
            or {"--files-with-matches", "--files-without-match", "--files"} & set(a)):
        with_arg = {"-e", "-f", "-m", "-A", "-B", "-C", "--regexp", "--file", "--max-count", "-g", "--glob",
                    "-t", "--type", "-T", "--type-not"}
        ops = [x for x, t in zip(args, a) if t in _operands(a, with_arg)]
        explicit = any(t in ("-e", "-f", "--regexp", "--file") or t.startswith(("--regexp=", "--file=")) for t in a)
        paths = ops if (explicit or "--files" in a) else ops[1:]
    elif prog == "ls":
        paths = [x for x in args if not x.text.startswith("-")]
    elif prog == "git":
        ops = _operands(a, {"-C", "-c"})
        if (ops[:1] == ["ls-files"] or (ops[:1] == ["diff"] and "--name-only" in a)
                or (ops[:1] == ["grep"] and "-l" in a)):
            paths = []
    if paths is None:
        return None
    return _find_roots(paths or [_lit(".")], ctx)


def _dbus_power(a: list[str]) -> bool:
    """Appel D-Bus à login1 pour éteindre/redémarrer (Reboot, PowerOff…)."""
    joined = " ".join(a)
    return "login1" in joined and any(m in a or m in joined for m in
                                      ("PowerOff", "Reboot", "Halt", "Suspend", "Hibernate", "PowerOffWithFlags",
                                       "RebootWithFlags"))


def _labels(prog: str, args: list, a: list[str], ctx: _Ctx, out: list) -> None:
    """Dangers propres au programme lancé, d'après ses ARGUMENTS réels."""
    ops = _operands(a)
    sub = ops[0] if ops else ""
    if prog in ("shutdown", "reboot", "poweroff", "halt", "telinit", "init") or prog.endswith("/init"):
        if prog not in ("init", "telinit") or any(d in a for d in ("0", "6", "1", "S", "s")):
            _add(out, L_POWER)
    elif prog == "sudoedit":  # édite un fichier en root
        _add(out, L_ADMIN)
    elif prog == "gnome-session-quit":
        if "--logout" in a:
            _add(out, L_SESSION)
        else:
            _add(out, L_POWER)  # --power-off, --reboot
    elif prog in ("busctl", "gdbus", "dbus-send", "qdbus") and _dbus_power(a):
        _add(out, L_POWER)
    elif prog == "systemctl":
        ops = _operands(a, {"-H", "--host", "-M", "--machine", "-t", "--type", "-p", "--property", "-s", "--signal",
                            "-n", "--lines", "-o", "--output", "--state", "--root", "--kill-whom", "--what"})
        sub = ops[0] if ops else ""
        if sub in _POWER:
            _add(out, L_POWER)
        elif sub in ("stop", "disable", "mask"):
            _add(out, L_SERVICE, SENSITIVE)
        elif sub in ("enable", "link", "start", "reenable") and any("/" in o for o in ops[1:]):
            _add(out, L_SCHEDULE)  # active une unité d'un CHEMIN arbitraire : lance du code hors du cgroup
    elif prog == "loginctl":
        if sub.startswith(("terminate", "kill")) or sub in ("poweroff", "reboot"):
            _add(out, L_SESSION)
    elif prog.startswith("mkfs") or prog in _DISK_PROGS:
        _add(out, L_DISK)
    elif prog in ("truncate", "shred"):
        _add(out, L_DESTROY)
    elif prog == "git":
        _git(args, a, ctx, out)
    elif prog == "gh":
        ops = _operands(a, {"-R", "--repo"})
        pair = tuple(ops[:2])
        if pair in (("pr", "create"), ("pr", "merge"), ("pr", "close"), ("repo", "delete"), ("repo", "create"),
                    ("issue", "create"), ("issue", "close"), ("release", "create"), ("release", "delete"),
                    ("release", "upload"), ("release", "edit")):
            _add(out, L_GITHUB)
    elif (prog in ("npm", "pnpm", "yarn") and sub == "publish") or (prog == "twine" and sub == "upload"):
        _add(out, L_PUBLISH)
    elif prog in _MAILERS:
        _add(out, L_MAIL)
    elif (prog == "gsettings" and "reset-recursively" in a) or (prog == "dconf" and sub == "reset" and "-f" in a):
        _add(out, L_RESET)
    # 15/10 : un programme lancé par systemd-run, at ou cron tourne HORS du cgroup du
    # service : le cœur ne le reconnaît plus comme venant de Claude (`state.from_claude`).
    elif prog == "systemd-run" or prog == "batch":
        _add(out, L_SCHEDULE)
    elif prog in ("busctl", "gdbus", "dbus-send", "qdbus") and any("StartTransientUnit" in t for t in a):
        _add(out, L_SCHEDULE)
    elif prog == "crontab":
        rest = list(a)
        while "-u" in rest:  # crontab -u utilisateur -l
            k = rest.index("-u")
            del rest[k:k + 2]
        if rest != ["-l"]:
            _add(out, L_SCHEDULE)
    elif prog == "at":
        if not any(t in ("-l", "-d", "-r", "-c") for t in a):  # lister ou retirer : libre
            _add(out, L_SCHEDULE)
    elif prog in ("claude", "claude-code") or prog.startswith("claude-code@"):
        # 19/10 (audit, accord de l'utilisateur) : changer de compte, créer un jeton longue durée,
        # ajouter ou retirer un serveur MCP (du code lancé à chaque session, réglages dans
        # ~/.claude.json), c'est toucher à ce qui garde Astrel : jamais accordé par Total,
        # « oui pour 30 min » ni la levée. Cherché dans TOUS les opérandes (une option à
        # valeur, `--model opus`, ne décale rien). `npx @anthropic-ai/claude-code` → claude-code.
        _claude_cli(args, out)
    elif prog.startswith("node") and _INTERP.fullmatch(prog) and any(_CLAUDE_JS.search(t) for t in a):
        # 19/10 (relecture) : `node …/@anthropic-ai/claude-code/cli.js auth logout`
        _claude_cli(args[next(k for k, t in enumerate(a) if _CLAUDE_JS.search(t)) + 1:], out)
    elif (prog in ("pnpm", "yarn") and sub in ("dlx", "exec")) or (prog == "npm" and sub in ("exec", "x")):
        # 19/10 (relecture) : `pnpm dlx`, `yarn dlx`, `npm exec` lancent un programme comme npx :
        # il est jugé comme s'il était lancé seul (`pnpm dlx @anthropic-ai/claude-code auth logout`).
        rest, i = args[a.index(sub) + 1:], 0
        while i < len(rest) and rest[i].text.startswith("-") and not rest[i].dyn:
            t = rest[i].text
            i += 2 if t in ("-p", "--package", "-w", "--workspace") else 1
            if t == "--":
                break
        if rest[i:]:
            _run(list(rest[i:]), ctx.child(), out, None)
    elif prog in _PACKAGERS and sub in ("install", "remove", "purge", "upgrade"):
        _add(out, L_PACKAGES, SENSITIVE)
    elif prog == "chmod":
        recursive = any(t == "--recursive" or re.fullmatch(r"-[a-zA-Z]*R[a-zA-Z]*", t) for t in a)
        if recursive or (ops and re.fullmatch(r"[0-7]{3,4}", ops[0]) and any(o.startswith("/") for o in ops[1:])):
            _add(out, L_PERMS, SENSITIVE)
    elif prog == "chown":
        _add(out, L_PERMS, SENSITIVE)
    elif prog in ("kill", "pkill", "killall"):
        if any(t in ("-9", "-KILL", "-SIGKILL") for t in a) or any(
                t in ("-s", "--signal") and nxt in ("9", "KILL", "SIGKILL") for t, nxt in zip(a, a[1:])):
            _add(out, L_KILL, SENSITIVE)
    elif prog == "docker":
        ops = _operands(a, {"-H", "--host", "--context", "-c", "--config", "-l", "--log-level"})
        if ops[:2] == ["system", "prune"] or ops[:1] in (["rm"], ["rmi"]) or ops[:2] == ["volume", "rm"]:
            _add(out, L_DOCKER, SENSITIVE)
    if prog in ("ssh", "scp", "rsync", "sftp") and any("@" in t for t in a):
        _add(out, L_REMOTE, SENSITIVE)
    if _audio_source(prog, a):
        _add(out, L_AUDIO)


# 04/11 (lot A, audit 3 H1) : créer, charger ou choisir une source audio, ou y brancher un autre flux, change
# ce que le micro d'Astrel entend (la voix de l'utilisateur confirme, active Total). Lister reste libre.
_PACTL_SOURCE = {"load-module", "set-default-source", "move-source-output", "set-source-port"}
_PW_CLI_SOURCE = {"create-node", "cn", "load-module", "lm", "create-link", "cl", "set-param", "s"}
_PW_LINK_READ = {"-o", "--output", "-i", "--input", "-l", "--links", "-m", "--monitor", "-I", "--id", "-v",
                 "--verbose", "-h", "--help", "--version"}
_AUDIO_LOOPBACKS = {"pw-loopback", "pa-loopback"}


def _audio_source(prog: str, a: list[str]) -> bool:
    if "--help" in a or "-h" in a:
        return False
    if prog in ("pactl", "pacmd"):
        ops = _operands(a, {"-s", "--server", "-n", "--client-name", "-f", "--format"})
        return bool(ops) and ops[0] in _PACTL_SOURCE
    if prog in _AUDIO_LOOPBACKS:
        return True
    if prog == "pw-cli":
        ops = _operands(a, {"-r", "--remote", "-n", "--name"})
        return bool(ops) and ops[0] in _PW_CLI_SOURCE
    if prog == "pw-link":
        return not all(t in _PW_LINK_READ for t in a)  # relier ou délier des ports
    if prog == "pw-metadata":
        return any("audio.source" in t for t in a) and len(_operands(a, {"-r", "--remote", "-n", "--name"})) >= 3
    if prog == "wpctl":
        ops = _operands(a)
        return bool(ops) and ops[0] in ("set-default", "set-profile", "set-route")
    return False


# --- 05/11 (agents imbriqués, autorisation explicite de l'utilisateur ; voir `L_AGENT_IMBRIQUE`) ---
_TEXTE_SEUL = {"cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe", "expect", "osascript"}
_COPIEURS = {"cp", "ln", "install", "mv", "rsync"}


def _judge_agent(prog: str, w0: _X, args: list, ctx: _Ctx, out: list) -> None:
    """05/11 : un autre agent IA lancé (claude, codex, gemini, aider…), sous son nom, son chemin, un lien, son
    paquet npm, `node …/cli.js`, `python -m aider` ; aussi son binaire copié ou lié sous un autre nom, un alias,
    `hash -p`, un texte de cmd/PowerShell/expect, et ses options propres données à un autre programme."""
    cands = list(w0.cands)
    if "/" in w0.text or "\\" in w0.text:
        cands += [str(p) for c in w0.cands if _DYN not in c for p in (_paths(c, ctx) or [])]
    noms = ({_nom_agent(c) for c in cands} | {_nom_agent(prog)}) - {""}
    if noms:
        nom = "claude" if noms & {"claude", "claude-code"} else sorted(noms)[0]
        if not _agent_sans_outils(nom, args):
            _agent(out)
        return
    a = [c for x in args for c in x.cands]
    fam = prog.lower()
    if _INTERP.fullmatch(prog) or fam in ("bun", "deno", "tsx", "ts-node"):
        if fam.startswith(("python", "pypy")):  # python -m aider, python -maider
            mods = [a[k + 1] if t == "-m" and k + 1 < len(a) else t[2:] for k, t in enumerate(a) if t.startswith("-m")]
            if any(m.split(".", 1)[0] in _AGENT_MODULES for m in mods):
                _agent(out)
                return
        for k, x in enumerate(args):  # node …/@anthropic-ai/claude-code/cli.js, deno run npm:@openai/codex
            nom = next((n for c in x.cands if ("/" in c or "\\" in c or ":" in c) and not _PAS_UN_CHEMIN.search(c)
                        for n in [_nom_agent(c)] if n), "")  # un chemin, pas le code de `python -c`
            if nom:
                if not _agent_sans_outils(nom, args[k + 1:]):
                    _agent(out)
                return
    if fam in _TEXTE_SEUL and _AGENT_TEXTE.search(" ".join(x.src or x.text for x in args)):
        _agent(out)  # cmd /c claude -p …, powershell -c "codex exec …", expect -c 'spawn claude'
    elif prog == "alias":  # alias c='claude -p' : la valeur est une commande
        for x in args:
            _nom, eq, val = x.text.partition("=")
            if eq and val:
                _analyze(val, _deeper(ctx), out)
    elif prog == "hash" and "-p" in a:  # hash -p ~/.local/bin/claude c ; c -p …
        k = a.index("-p")
        if k + 1 < len(a) and _nom_agent(a[k + 1]):
            _agent(out)
    elif prog in _COPIEURS:  # cp ~/.local/bin/claude /tmp/x ; /tmp/x -p … (le nom ne dit plus rien)
        ops = [x for x in args if x.dyn or not x.text.startswith("-")]
        for x in ops[:-1] if len(ops) > 1 else ops:
            if any(_binaire_agent(p) for c in x.cands if _DYN not in c for p in (_paths(c, ctx) or [])):
                _agent(out)
                break
    _relance(prog, args, ctx, out)
    if prog not in _CHERCHEURS and _drapeau_agent(args):
        _agent(out)  # /tmp/x --dangerously-skip-permissions -p …, python agent.py --permission-mode …


def _binaire_agent(p: Path) -> bool:
    """Le CLI d'un agent sur le disque (fichier, ou dossier du CLI natif qui contient `versions/`)."""
    s = str(p)
    if _CLAUDE_BIN.search(s) or _CLAUDE_JS.search(s) or _AGENT_PAQUET.search(s):
        return True
    if _AGENT_SUFFIXE.sub("", p.name.lower()) not in _AGENTS:
        return False
    try:
        return p.is_file() or (p / "versions").is_dir()
    except OSError:
        return False


# Multiplexeurs et terminaux qui lancent une commande (avant le 05/11 : rien n'y était lu, `tmux new -d 'sudo
# reboot'` ou `gnome-terminal -- claude -p …` passaient) ; lanceurs de paquets (`uvx aider-chat`, `uv run …`).
_TMUX_LANCE = {"new-session", "new", "new-window", "neww", "split-window", "splitw", "respawn-pane", "respawnp",
               "respawn-window", "respawnw", "run-shell", "run", "display-popup", "popup", "if-shell", "if",
               "pipe-pane", "pipep", "send-keys", "send"}
# Lettres d'options à valeur (toutes sous-commandes confondues ; -d, détaché le plus souvent, compté sans valeur :
# en cas de doute, plus de mots sont lus comme la commande, jamais moins).
_TMUX_VAL = set("ceFfhlnNsStTwxyb")
_SCREEN_VAL = set("ScehpstT")
_TERMINAUX = {"gnome-terminal", "xterm", "uxterm", "konsole", "kitty", "alacritty", "foot", "footclient", "wezterm",
              "xfce4-terminal", "mate-terminal", "lxterminal", "terminator", "tilix", "x-terminal-emulator", "kgx",
              "ptyxis", "urxvt", "rxvt", "st", "qterminal", "terminology", "sakura", "guake", "cool-retro-term"}
_TERM_MARQUES = {"--", "-e", "-x", "--command", "--execute", "--exec"}
_EXEC_OPTS = {  # options à valeur des lanceurs de paquets, avant le programme
    "uvx": {"--from", "--with", "--with-editable", "--with-requirements", "-p", "--python", "--index", "--index-url",
            "--extra-index-url", "--default-index", "-c", "--constraints"},
    "uv": {"--with", "--with-editable", "--with-requirements", "-p", "--python", "--project", "--directory",
           "--package", "--group", "--extra", "--env-file", "--index", "--only-group", "--no-group", "--from"},
    "pipx": {"--spec", "--python", "--pip-args", "--index-url"},
    "poetry": {"-C", "--directory", "-P", "--project"}, "pdm": {"-p", "--project"}, "hatch": {"-e", "--env"},
    "rye": set(), "pixi": {"-e", "--environment", "--manifest-path"},
    "conda": {"-n", "--name", "-p", "--prefix", "--cwd"}, "bun": set(),
}
_EXEC_OPTS["mamba"] = _EXEC_OPTS["micromamba"] = _EXEC_OPTS["conda"]


def _relance(prog: str, args: list, ctx: _Ctx, out: list) -> None:
    """05/11 : programmes qui en lancent un autre et n'étaient pas lus : la commande lancée est jugée."""
    a0 = args[0].text if args and not args[0].dyn else ""
    if prog == "tmux":
        _tmux(args, ctx, out)
    elif prog == "screen":
        _screen(args, ctx, out)
    elif prog in _TERMINAUX:
        _terminal(prog, args, ctx, out)
    elif prog in _EXEC_OPTS:
        if prog == "uvx":
            rest = args
        elif prog == "uv" and a0 == "tool" and len(args) > 1 and args[1].text == "run":
            rest = args[2:]
        elif (prog == "bun" and a0 == "x") or (prog != "uvx" and prog != "bun" and a0 == "run"):
            rest = args[1:]
        else:
            return
        opts, i = _EXEC_OPTS[prog], 0
        while i < len(rest) and not rest[i].dyn and rest[i].text.startswith("-"):
            t = rest[i].text
            if t == "--":
                i += 1
                break
            i += 2 if (t in opts and "=" not in t) else 1
        if rest[i:]:
            _run(list(rest[i:]), ctx.child(), out, None)


def _tmux(args: list, ctx: _Ctx, out: list) -> None:
    i = 0
    while i < len(args) and not args[i].dyn and args[i].text.startswith("-") and len(args[i].text) > 1:
        t = args[i].text
        if t == "-c" and i + 1 < len(args):  # tmux -c 'commande' : lancée par le shell
            _code([args[i + 1]], "sh", ctx, out)
        i += 2 if t in ("-L", "-S", "-f", "-T", "-c") else 1
    groups: list[list] = [[]]
    for x in args[i:]:  # tmux new -d x \; split-window y : plusieurs commandes
        if x.text == ";" and not x.dyn:
            groups.append([])
        else:
            groups[-1].append(x)
    for g in groups:
        if not g or g[0].dyn or g[0].text not in _TMUX_LANCE:
            continue
        j = 1
        while j < len(g) and not g[j].dyn and g[j].text.startswith("-") and len(g[j].text) > 1:
            if g[j].text == "--":
                j += 1
                break
            body = g[j].text[1:]
            k = next((k for k, ch in enumerate(body) if ch in _TMUX_VAL), None)
            j += 2 if k is not None and not body[k + 1:] else 1
        tail = g[j:]
        if not tail:
            continue
        if g[0].text in ("if-shell", "if"):
            for x in tail:
                _code([x], "sh", ctx, out)
        else:
            _code(tail, "sh", ctx, out)  # tmux joint les mots et les donne au shell


def _screen(args: list, ctx: _Ctx, out: list) -> None:
    i = 0
    while i < len(args) and not args[i].dyn and args[i].text.startswith("-") and len(args[i].text) > 1:
        t = args[i].text
        if t == "-X":  # screen -S s -X stuff 'claude -p …\n'
            rest = args[i + 1:]
            if rest and rest[0].text in ("stuff", "exec", "screen"):
                _code(rest[1:], "sh", ctx, out)
            return
        if t in ("-ls", "-list", "-wipe"):
            return
        if t == "-Logfile":
            i += 2
            continue
        body = t[1:]
        k = next((k for k, ch in enumerate(body) if ch in _SCREEN_VAL), None)
        i += 2 if k is not None and not body[k + 1:] else 1
    if args[i:]:
        _run(list(args[i:]), ctx.child(), out, None)


def _terminal(prog: str, args: list, ctx: _Ctx, out: list) -> None:
    for k, x in enumerate(args):
        if x.dyn:
            continue
        name, eq, v = x.text.partition("=")
        if x.text in _TERM_MARQUES or (eq and name in ("--command", "--execute", "--exec")):
            tail = ([_lit(v)] if eq else []) + list(args[k + 1:])
            if len(tail) == 1:
                _code(tail, "sh", ctx, out)  # gnome-terminal -e 'commande' : un texte
            elif tail:
                _run(tail, ctx.child(), out, None)
            return
    # kitty, foot, wezterm start… : le programme est le premier mot qui n'est pas une option (valeurs inconnues :
    # chaque départ possible est jugé, quatre au plus)
    starts = [k for k, x in enumerate(args) if x.dyn or not x.text.startswith("-")][:4]
    for k in starts:
        _run(list(args[k:]), ctx.child(), out, None)


_GIT_CODE_KEYS = re.compile(r"(core\.(fsmonitor|sshcommand|pager|editor|askpass)|diff\.external|sequence\.editor"
                            r"|gpg\.program|(filter|diff|merge)\..+\.(clean|smudge|process|textconv|command|driver)"
                            r"|alias\..+|credential\.helper)$", re.I)


def _git(args: list, a: list[str], ctx: _Ctx, out: list) -> None:
    i = 0
    aliases: dict[str, str] = {}
    while i < len(a):
        t = a[i]
        if t in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"):
            if t == "-c" and i + 1 < len(a):
                key, eq, val = a[i + 1].partition("=")
                if eq and key.lower().startswith("alias."):
                    if val.startswith("!"):
                        _analyze(val[1:], _deeper(ctx), out)  # alias shell : git -c alias.p='!sudo ls' p
                    else:
                        aliases[key.split(".", 1)[1]] = val  # alias d'une sous-commande git
                elif eq and _GIT_CODE_KEYS.match(key):  # git -c core.fsmonitor='…' : une commande
                    val = val[1:] if key.lower().startswith("credential.") and val.startswith("!") else val
                    if val and not key.lower().startswith("credential."):
                        _analyze(val, _deeper(ctx), out)
            i += 2
        elif t.startswith("-"):
            i += 1
        else:
            break
    sub, rest = (a[i], a[i + 1:]) if i < len(a) else ("", [])
    if _DYN in sub or _FOUND in sub:
        _doubt(out, D_PROGRAM)  # git $(echo push), git ${1:-push} : sous-commande calculée
        return
    if sub in aliases:  # alias défini dans la même commande vers une vraie sous-commande
        sub, rest = (aliases[sub].split() + rest)[0], (aliases[sub].split() + rest)[1:]
    if sub == "config" and any(_GIT_CODE_KEYS.match(r) or _DYN in r for r in rest if not r.startswith("-")):
        # git config core.pager … : les git suivants lanceront ce programme (comme PAGER=…)
        ctx.env_unknown = True
    short = "".join(r[1:] for r in rest if r.startswith("-") and not r.startswith("--"))
    if sub == "push":
        _add(out, L_PUSH)
    elif ((sub == "reset" and "--hard" in rest) or (sub == "clean" and ("f" in short or "--force" in rest))
          or sub in ("filter-branch", "filter-repo")
          or (sub == "branch" and ("D" in short
                                   or ({"-d", "--delete"} & set(rest) and {"-f", "--force"} & set(rest))))):
        _add(out, L_GIT_ERASE)
    elif sub in ("commit", "merge", "cherry-pick", "am", "rebase") or (sub == "checkout" and "--" in rest):
        _add(out, L_GIT, SENSITIVE)
    if sub == "clone":  # git clone URL DEST : le dossier de destination est écrit
        dests = _operands(rest, {"-o", "--origin", "-b", "--branch", "-u", "--upload-pack", "-c", "--config",
                                 "--depth", "--reference", "--separate-git-dir", "-j", "--jobs"})
        if len(dests) >= 2:
            _judge_write(_lit(dests[1]), ctx, out)
    if sub == "submodule" and "foreach" in rest:  # exécute une commande dans chaque sous-module
        k = i + 1 + rest.index("foreach")
        if args[k + 1:]:
            _analyze(" ".join(x.src for x in args[k + 1:]), _deeper(ctx), out)
    if sub == "rebase":  # git rebase --exec CMD / -x CMD : lance CMD à chaque commit
        for j, t in enumerate(rest):
            if t in ("-x", "--exec") and j + 1 < len(rest):
                _analyze(rest[j + 1], _deeper(ctx), out)
            elif t.startswith("--exec="):
                _analyze(t.split("=", 1)[1], _deeper(ctx), out)


def _cd(args: list, ctx: _Ctx) -> bool:
    ops = [x for x in args if x.dyn or not x.text.startswith("-") or x.text == "-"]
    if not ops:
        new = [Path.home()]
    elif ops[0].text == "-":
        new = list(ctx.seen)
    elif ops[0].dyn:
        new = None
    else:
        new = []
        for c in ops[0].cands:
            ps = _paths(c, ctx)
            if ps is None:
                new = None
                break
            new += ps
    ctx.cwds = list(dict.fromkeys(new)) if new else None
    if ctx.cwds:
        ctx.seen.extend(ctx.cwds)
    return True


def _shell(args: list, ctx: _Ctx, out: list, stdin) -> None:
    i, code = 0, False
    while i < len(args):
        t = args[i].text
        if t == "--":
            i += 1
            break
        if t in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            i += 2
        elif t.startswith("--"):
            i += 1
        elif t[:1] in ("-", "+") and len(t) > 1:
            code = code or (t[0] == "-" and "c" in t[1:])
            if t[0] == "-" and "n" in t[1:]:
                return  # bash -n : vérifie la syntaxe, ne lance rien
            i += 1
        else:
            break
    if code:  # bash -c 'commande'
        if i < len(args):
            _code(args[i:i + 1], "sh", ctx, out)
            _judge_cited("", args[i + 1:], ctx, out, None)  # bash -c '… "$1"' _ fichier : $1 non suivi
        return
    if i < len(args):  # bash script.sh, bash <(curl …)
        if _psub_feed(args[i]):
            _consume(_psub_feed(args[i]), "sh", ctx, out)
        else:
            _exec_file(args[i], "sh", ctx, out, args[i + 1:])
            if _written_text(args[i], ctx) is None:  # script déjà sur le disque, non lu
                _judge_cited("", args[i:], ctx, out, None)
        return
    _consume(stdin, "sh", ctx, out)


def _interp(prog: str, args: list, ctx: _Ctx, out: list, stdin) -> None:
    family = "node" if prog.startswith("node") else re.match(r"[A-Za-z]+", prog).group(0)
    flags = _CODE_FLAGS.get(family, "e")
    code, inplace, i = [], False, 0
    while i < len(args):
        t = args[i].text
        if t == "--":
            i += 1
            break
        if t in ("--syntax-check", "--check"):
            return
        if t.startswith("--"):
            name, eq, v = t.partition("=")
            if family == "node" and name in ("--eval", "--print"):
                if eq:
                    code.append(_lit(v))
                elif i + 1 < len(args):
                    code.append(args[i + 1])
                    i += 1
            i += 1
            continue
        if not t.startswith("-") or len(t) < 2:
            break
        body = t[1:]
        if family == "python" and body.startswith("m"):
            # python -m module : un module installé. Tests, compilation, vérificateurs ne
            # modifient pas les fichiers donnés ; tout autre module (black…) : comme un inconnu.
            mod = body[1:] or (args[i + 1].text if i + 1 < len(args) else "")
            if mod not in ("pytest", "py_compile", "compileall", "pyflakes", "pycodestyle", "tabnanny", "mypy"):
                _judge_cited("", args[i + (1 if body[1:] else 2):], ctx, out, None)
            return
        if _LINT_FLAGS.get(family, "") and _LINT_FLAGS[family] in body.split("e")[0]:
            return  # php -l, node -c, perl -c : vérifie la syntaxe
        if family in ("perl", "ruby") and "i" in body.split("e")[0]:
            inplace = True  # perl -pi -e : modifie les fichiers donnés
        k = next((k for k, ch in enumerate(body) if ch in flags), None)
        if k is not None:
            if body[k + 1:] and not (family == "perl" and "i" in body[:k]):
                code.append(_lit(body[k + 1:]))
            elif i + 1 < len(args):
                code.append(args[i + 1])
                i += 1
        i += 1
    if code:
        _code(code, "code", ctx, out)
        _judge_code(" ".join(x.src for x in code), args[i:], family, ctx, out)
        if inplace:
            for x in args[i:]:
                _judge_write(x, ctx, out)
        return
    if i < len(args) and args[i].text != "-":
        if _psub_feed(args[i]):
            _consume(_psub_feed(args[i]), "code", ctx, out)
            _judge_code(None, args[i + 1:], family, ctx, out)
        else:
            _exec_file(args[i], "code", ctx, out)
            _judge_code(_written_text(args[i], ctx), args[i:], family, ctx, out)  # script sur le disque : None
        return
    rest = args[i + 1:] if i < len(args) else []  # python - arg… : arguments du code lu sur l'entrée
    _consume(stdin, "code", ctx, out)
    _judge_code(stdin[1] if stdin and stdin[0] == "text" else None, rest, family, ctx, out)


def _awk(args: list, ctx: _Ctx, out: list) -> None:
    i = 0
    while i < len(args):
        t = args[i].text
        if t == "--":
            i += 1
            break
        if t in ("-f", "--file", "-i", "--include", "-E", "--exec", "-l", "--load") or t.startswith(
                ("--file=", "--include=", "--exec=", "--load=")) or re.fullmatch(r"-[fiEl]\S+", t):
            # programme dans un fichier (non lu), ou `gawk -i inplace` qui réécrit les fichiers donnés
            _raw_code(" ".join(x.src for x in args), out)
            _judge_cited("", args, ctx, out, None)
            return
        if t in ("-F", "-v", "--assign", "--field-separator"):
            i += 2
        elif t.startswith("-") and len(t) > 1:
            i += 1
        else:
            break
    if i < len(args):
        _raw_code(args[i].src, out)
        _code_compte(args[i].text, ctx, out)  # 04/11 : `getline < "~/.ssh/id_rsa"` dans le programme
        # Lire un fichier protégé avec awk est libre ; « print > f », « system() », « | getline » non.
        # 19/10 : le nom cité AVANT le programme compte aussi (`awk -v f=astrel/ipc.py '…> f'`).
        name = _cited_code([c for x in args for c in x.cands], ctx) if _AWK_WRITE.search(args[i].src) else ""
        if name:
            _add(out, _label_code(name), guard=True)


def _ssh(args: list, ctx: _Ctx, out: list, stdin) -> None:
    i = 0
    while i < len(args):
        t = args[i].text
        if t == "--":
            i += 1
            break
        if not t.startswith("-") or len(t) < 2:
            break
        i += 2 if (len(t) == 2 and t[1] in "BbcDEeFIiJLlmOopQRSWw") else 1
    remote = _deeper(ctx, cwds=[Path.home()], remote=True)  # dossier courant distant : son dossier personnel
    rest = args[i + 1:]
    if rest:
        _analyze(" ".join(x.src for x in rest), remote, out)
    else:
        _consume(stdin, "sh", remote, out)


def _rm(prog: str, args: list, ctx: _Ctx, out: list) -> None:
    recursive = prog == "rimraf"
    targets, opts = [], True
    for x in args:
        t = x.text
        if opts and not x.dyn:
            if t == "--":
                opts = False
                continue
            if t.startswith("--"):  # --recursive, et ses abréviations (--rec, --r)
                recursive = recursive or (prog == "rm" and len(t) >= 3 and "--recursive".startswith(t))
                continue
            if t.startswith("-") and len(t) > 1:
                recursive = recursive or (prog == "rm" and bool(set(t[1:]) & {"r", "R"}))
                continue
        targets.append(x)
    if prog != "unlink" and any(x.split for x in targets):
        _doubt(out, D_RM)  # rm $X : X peut valoir « -rf ~ »
        if any(_DYN in c and _PLANS_TEXT.search(c.replace(_DYN, "/")) for x in targets if x.split for c in x.cands):
            _add(out, L_GUARD, guard=True)  # 16/10 : « rm $D/runtime/plans/P3.json »
        targets = [x for x in targets if not x.split]
    if recursive or any(_FOUND in c for x in targets for c in x.cands):
        _judge_delete(targets, ctx, out, filtered=False)
    else:
        for x in targets:
            _judge_plain_delete(x, ctx, out)


def _find_roots(starts: list, ctx: _Ctx) -> list | None:
    roots: list = []
    for x in starts:
        if x.dyn:
            return None
        for c in x.cands:
            scopes = _scopes(c, ctx)
            if scopes is None:
                return None
            roots += [p for p, _pattern in scopes]
    return roots


def _find(args: list, ctx: _Ctx, out: list) -> tuple:
    a = [x.text for x in args]
    i = 0
    while i < len(a) and (a[i] in ("-H", "-L", "-P", "-D") or re.fullmatch(r"-O\d", a[i])):
        i += 2 if a[i] == "-D" else 1
    starts = []
    while i < len(a) and not (a[i].startswith("-") or a[i] in ("(", "!", ",", ")")):
        starts.append(args[i])
        i += 1
    roots = _find_roots(starts or [_lit(".")], ctx)
    fctx = ctx.child(found=roots, found_spec=None)
    first = i
    deletes = writes = False
    while i < len(a):
        t = a[i]
        if t == "-delete":
            _judge_delete([_X([_FOUND])], fctx, out, filtered=True)
            deletes = True
        elif t in ("-exec", "-execdir", "-ok", "-okdir"):
            j = i + 1
            while j < len(a) and a[j] not in (";", "+"):
                j += 1
            inner = [_sub_token(x, "{}", _X([_FOUND])) if "{}" in x.text else x for x in args[i + 1:j]]
            _run(inner, fctx.child(), out, None)
            words = a[i + 1:j]
            if words and words[0].rsplit("/", 1)[-1] in ("rm", "rmdir", "unlink"):
                deletes = True
            elif not (words and (words[0] in _CITE_READERS or _segment_read_only(words))):
                writes = True
            i = j
        elif t in ("-fprint", "-fprint0", "-fls", "-fprintf") and i + 1 < len(a):
            _judge_write(args[i + 1], ctx, out)
            i += 1
        i += 1
    # 19/10 (audit) : `find logs -name 'audit*' -delete` était une « suppression dans un
    # dossier du garde-fou » ordinaire (accordée par Total). Si ses filtres de nom peuvent
    # retenir un fichier du garde-fou : action sur le garde-fou.
    shown = []  # (début du chemin écrit par find, dossier) : « logs » → « logs/audit.jsonl »
    if not ctx.remote and roots is not None:
        for x in starts or [_lit(".")]:
            for c in x.cands:
                shown += [(os.path.expanduser(c) if pattern is None and not x.glob else None, p)
                          for p, pattern in _scopes(c, ctx) or []]
    if (deletes or writes) and not ctx.remote and roots is not None:
        _add_guard_hits(_find_guard_hits(a[first:], shown, skip_cache=not writes), out, deletion=deletes)
        _claude_found((a[first:], shown), out)  # 23/10 (audit P29) : `find ~ -name '*.json' -delete`
    # 23/10 (audit P29, constat 4) : mêmes filtres pour ce que xargs fera des fichiers trouvés.
    return ("found", roots, (a[first:], shown) if roots is not None and not ctx.remote else None)


def _fd(args: list, ctx: _Ctx, out: list) -> None:
    """fd motif [chemins] -x|-X commande : comme find -exec."""
    pos, i = [], 0
    while i < len(args):
        t = args[i].text
        if t in ("-x", "--exec", "-X", "--exec-batch"):
            inner = [_sub_token(x, "{}", _X([_FOUND])) if "{}" in x.text else x
                     for x in args[i + 1:] if x.text != ";"]
            if inner and not any(_FOUND in x.text for x in inner):
                inner.append(_X([_FOUND]))
            roots = _find_roots(pos[1:] or [_lit(".")], ctx)
            _run(inner, ctx.child(found=roots), out, None)
            return
        if t.startswith("-"):
            i += 2 if t in ("-e", "--extension", "-t", "--type", "-d", "--max-depth", "-E", "--exclude",
                            "-S", "--size", "--changed-within", "--changed-before", "-j", "--threads") else 1
            continue
        pos.append(args[i])
        i += 1


_COPY_ARG = {"cp": "tS", "mv": "tS", "ln": "tS", "install": "tSmog", "scp": "cFiJlmoPS", "rsync": "efBT"}
_COPY_LONG = {"--target-directory", "--suffix", "--mode", "--owner", "--group", "--exclude", "--include", "--rsh",
              "--filter", "--files-from", "--exclude-from", "--include-from", "--link-dest", "--compare-dest",
              "--copy-dest", "--backup-dir", "--temp-dir", "--partial-dir", "--log-file", "--chmod", "--chown",
              "--usermap", "--groupmap", "--rsync-path", "--out-format", "--password-file"}


def _copy(prog: str, args: list, ctx: _Ctx, out: list) -> None:
    """cp, mv, install, ln, scp, rsync : la destination (et dest/nom de chaque source)."""
    short_arg = _COPY_ARG[prog]
    operands, target, no_target, dirs, delete, i, opts = [], None, False, False, False, 0, True
    symbolic = False
    while i < len(args):
        x = args[i]
        t = x.text
        i += 1
        if not opts or x.dyn or not t.startswith("-") or len(t) < 2:
            operands.append(x)
            continue
        if t == "--":
            opts = False
            continue
        if t.startswith("--"):
            name, eq, v = t.partition("=")
            val = _lit(v) if eq else None
            if not eq and name in _COPY_LONG and i < len(args):
                val = args[i]
                i += 1
            if name == "--target-directory":
                target = val
            elif name == "--no-target-directory":
                no_target = True
            elif name == "--directory":
                dirs = True
            elif name.startswith("--del") or name == "--remove-source-files":
                delete = True
            elif name == "--rsh" and val is not None:
                _code([val], "sh", ctx, out)
            elif name == "--symbolic":
                symbolic = True
            continue
        body = t[1:]
        for k, ch in enumerate(body):
            if ch == "s" and prog == "ln":
                symbolic = True
            if ch == "T" and prog in ("cp", "mv", "ln", "install"):
                no_target = True
            if ch == "d" and prog == "install":
                dirs = True
            if ch in short_arg:
                if body[k + 1:]:
                    val = _lit(body[k + 1:])
                else:
                    val = args[i] if i < len(args) else _lit("")
                    i += 1
                if ch == "t" and prog in ("cp", "mv", "ln", "install"):
                    target = val
                elif ch == "e" and prog == "rsync":
                    _code([val], "sh", ctx, out)  # rsync -e 'ssh …' : une commande
                break
    if prog == "install" and dirs:
        dests, sources = operands, []
    elif target is not None:
        dests, sources = [target], operands
    elif len(operands) >= 2:
        dests, sources = [operands[-1]], operands[:-1]
    elif prog == "ln" and operands:
        dests, sources = [_lit(operands[0].text.rstrip("/").rsplit("/", 1)[-1] or ".")], []
    else:
        return
    for d in dests:
        if prog in ("scp", "rsync") and re.match(r"[^/]*:", d.text):
            continue  # destination distante
        _judge_write(d, ctx, out)
        if not no_target:
            for s in sources:
                names = [c.rstrip("/").rsplit("/", 1)[-1] for c in s.cands]
                _judge_write(_X([dc.rstrip("/") + "/" + n for dc in d.cands for n in names][:_MAX_CANDS],
                                d.dyn or s.dyn), ctx, out)
        if delete and prog == "rsync":
            _judge_delete([d], ctx, out, filtered=True)
    if prog == "mv":  # déplacer, c'est aussi retirer la source
        for s in sources:
            if not s.dyn:
                _judge_write(s, ctx, out)
    # 23/10 (plan P29, audit fiche 6, constats 1 et 3) : un lien (dur ou symbolique) vers un
    # fichier du plafond d'écriture se modifie ensuite à travers le lien, sans le nommer
    # (`ln ~/.claude/settings.json /tmp/s`, `ln .venv/pyvenv.cfg /tmp/p`, `cp -l`) : plafond.
    links = prog == "ln" or (prog == "cp" and any(
        t in ("--link", "--symbolic-link") or re.fullmatch(r"-[A-Za-z]*[ls][A-Za-z]*", t) for t in (x.text for x in args)))
    if links and not ctx.remote:
        # 27/10 (P40, relecture fiche 10) : un lien vers un dossier de runtime/ qui CONTIENT un
        # secret (`ln -s runtime/imagegen /tmp/zz`) permet ensuite de le lire sans le nommer
        # (`cat /tmp/zz/profils/…/Cookies`) : lecture d'un secret, plafond.
        runtime = [config.RUNTIME.resolve()]
        if any(p in _SECRET_ANCESTORS and _under(p, runtime)
               for s in sources or operands[:1] for c in s.cands if c and _DYN not in c
               for p in _paths(c, ctx) or []):
            _add(out, L_SECRET, guard=True, cat=_L.SECRETS_LARGES)
        labels = [_ceiling_raw(c, ctx) or next((lb for p in _paths(c, ctx) or [] if (lb := _ceiling_write_label(p))), None)
                  for s in sources or operands[:1] for c in s.cands if c and _DYN not in c]
        if any(labels):
            _add(out, next(lb for lb in labels if lb), guard=True)
        else:  # `ln ~/.bashrc ~/l` puis `echo … >> ~/l` : écrire dans un fichier sensible sans le nommer
            critical = [r.resolve() for r in CRITICAL_WRITE_ROOTS]
            for s in sources or operands[:1]:
                for p in [p for c in s.cands if c and _DYN not in c for p in _paths(c, ctx) or []]:
                    if _under(p, critical):
                        _add(out, f"une écriture dans {p.name or p}, hors de tes projets")
                        break
    if prog == "ln" and symbolic:
        # Un lien symbolique vers une cible hors des projets : une écriture plus tard à
        # travers lui ne serait pas vue (3/10). Doute sauf si la cible reste dans un projet.
        for s in sources:
            paths = None if s.dyn else _paths(s.text, ctx)
            if paths is None or any(not _under(p, [r.resolve() for r in ALLOWED_WRITE_ROOTS]) for p in paths):
                _doubt(out, D_WRITE)
    if prog == "ln" and _MISSION.get() and not ctx.remote:
        # Pendant une mission : écrire à travers un lien, c'est écrire dans sa CIBLE. La
        # cible doit rester dans le périmètre (résolue depuis le dossier courant ET depuis
        # le dossier où le lien est créé, une cible relative partant de là).
        bases: list | None = list(ctx.cwds or [])
        for d in dests:
            for c in d.cands:
                ps = None if (_DYN in c or d.dyn) else _paths(c, ctx)
                if ps is None:
                    bases = None
                    break
                bases += [q for p in ps for q in (p, p.parent)]
            if bases is None:
                break
        for s in sources or operands[:1]:  # `ln -s cible` : lien créé ici, la cible est le seul opérande
            for c in s.cands:
                if s.dyn or _DYN in c or bases is None or not bases:
                    _add(out, L_MISSION_OUT)
                    continue
                p = Path(os.path.expanduser(c))
                for b in ([Path("/")] if p.is_absolute() else bases):
                    _judge_fence(Path(os.path.normpath(Path(b) / p)), out)


def _extract(args: list, ctx: _Ctx, out: list, long_opt: set, short_opt: str) -> None:
    """Dossier d'extraction de tar (-C), unzip (-d) : une archive piégée y écrit
    n'importe quel fichier (5/10). La règle « contient un fichier sensible » s'applique."""
    i = 0
    while i < len(args):
        t = args[i].text
        name, eq, v = t.partition("=")
        if t in long_opt and i + 1 < len(args):
            _judge_write(args[i + 1], ctx, out)
            i += 1
        elif name in long_opt and eq:
            _judge_write(_lit(v), ctx, out)
        elif short_opt and t.startswith("-" + short_opt) and len(t) > 2 and not t.startswith("--"):
            _judge_write(_lit(t[2:]), ctx, out)
        i += 1


_SED_W = re.compile(r"(?:^|[;{}\n])\s*[wW]\s+([^;\n}]+)"          # commande w/W fichier
                    r"|[sy][/#|,].*?[/#|,].*?[/#|,][gpiImMe0-9]*[wW]\s+([^;\n}]+)")  # s/…/…/w fichier


def _sed(args: list, ctx: _Ctx, out: list) -> None:
    # 19/10 (relecture) : arguments lus par `_sed_args` (« --i » = --in-place) et script par
    # `_sed_scan` : `e` (et s///e) exécute une commande, `$!w f` écrit (l'expression les manquait).
    inplace, scripts, files, _opaque = _sed_args([x.text for x in args])
    if inplace:
        for k in files:
            _judge_write(args[k], ctx, out)
    for k, text in scripts:
        for c in (args[k].cands if k is not None else [text]):
            for m in _SED_W.finditer(c):  # commandes « w fichier », « s/…/…/w fichier » : une écriture (5/10)
                _judge_write(_lit((m.group(1) or m.group(2)).strip()), ctx, out)
            found = _sed_scan(c.replace(_DYN, "1"))  # « ${N}p » : un nombre inconnu
            if found is None:
                _doubt(out, D_SYNTAX)
                continue
            for kind, arg in found:
                if kind == "w":
                    _judge_write(_lit(arg), ctx, out)
                elif kind == "e":
                    _doubt(out, D_STDIN)  # la ligne lue, ou la commande donnée, est lancée par un shell
                    if arg:
                        _analyze(arg, _deeper(ctx), out)


def _download(prog: str, args: list, ctx: _Ctx, out: list) -> tuple:
    """curl/wget : ce qui est écrit sur le disque (-o, -O) ou sur la sortie (lu par un shell ?)."""
    argful = "AbcCdDeEFHKmorTuUwxXyYzQ" if prog == "curl" else "OoPaeiTtwQUlADIXR"
    outputs, stdout = [], prog == "curl"
    i = 0
    while i < len(args):
        t = args[i].text
        i += 1
        if t.startswith("--"):
            name, eq, v = t.partition("=")
            if name in ("--output", "--output-document", "--directory-prefix", "--output-dir"):
                val = _lit(v) if eq else (args[i] if i < len(args) else _lit(""))
                i += 0 if eq else 1
                is_dir = name in ("--directory-prefix", "--output-dir")
                outputs.append(_lit(val.text.rstrip("/") + "/x") if is_dir else val)
            elif name in ("--remote-name", "--remote-name-all"):
                stdout = False
            continue
        if not t.startswith("-") or len(t) < 2:
            continue
        body = t[1:]
        for k, ch in enumerate(body):
            if prog == "curl" and ch == "O":
                stdout = False
            if ch in argful:
                val = _lit(body[k + 1:]) if body[k + 1:] else (args[i] if i < len(args) else _lit(""))
                i += 0 if body[k + 1:] else 1
                if (prog == "curl" and ch == "o") or (prog == "wget" and ch == "O"):
                    outputs.append(val)
                elif prog == "wget" and ch == "P":
                    outputs.append(_lit(val.text.rstrip("/") + "/x"))
                break
    for x in outputs:
        if x.text == "-":
            stdout = True
            continue
        stdout = False
        _judge_write(x, ctx, out)
        _remember(x, ctx, ("dl", None))
    return ("dl", None) if stdout else ("pipe", None)


# --- jugements ---

# 03/11 (Windows) : « /dev/null » de Git Bash, une fois résolu par Python, devient « D:/dev/null » : le lecteur
# en tête est permis, et la comparaison se fait sur la forme en « / » (`as_posix`, identique sous Linux).
_DEV_FREE = re.compile(r"(?:[A-Za-z]:)?/dev/(null|zero|stdout|stderr|stdin|tty|fd/\d+|shm/.*|pts/\d+)$|/proc/self/fd/\d+$")
_DEV_DISK = re.compile(r"(?:[A-Za-z]:)?/dev/(sd|nvme|mmcblk|hd|vd|xvd|dm-|mapper/|disk/|loop|md\d|sr\d)")


# --- fichiers protégés CITÉS (15/10, « lectures libres ») ---
# Avant : une commande pas ENTIÈREMENT en lecture seule qui citait un fichier de
# `PROTECTED_CODE` n'importe où demandait (« grep … server.py | head; ss -l », un
# `python -c` à côté d'un grep, un heredoc python qui cite le nom dans une chaîne).
# Désormais chaque commande simple est jugée à part : celle qui cite le fichier
# (argument, heredoc reçu) ne demande que si elle peut écrire ou lancer autre chose.
# Les écritures réelles (redirection, cp, mv, tee, sed -i…) restent jugées par leur
# DESTINATION (`_judge_write`), et une commande illisible retombe sur le texte brut.

_CITE_READERS = {"bat", "batcat", "hexdump", "[[", ":", "export", "local", "declare", "typeset", "readonly",
                 "unset", "set", "exit", "return", "sleep", "wait"}  # ne modifient aucun fichier cité
# Commandes internes du shell : ne lancent rien, même avec une variable lanceuse posée.
_SHELL_READERS = {"[[", ":", "export", "local", "declare", "typeset", "readonly", "unset", "set", "exit",
                  "return", "wait", "echo", "printf", "pwd", "true", "false", "test", "[", "read", "type"}
_SKIP_CITED = {"cd", "pushd", "popd", "eval", "trap", "find", "fd", "fdfind", "ssh",  # jugés ailleurs
               "rm", "rimraf", "unlink"}  # suppressions : `_rm` (fichier protégé visé → garde-fou)
_PATHISH = re.compile(r"^\.{1,2}$|/|[*?]|\.\w{1,8}$")
_AWKS = {"awk", "gawk", "mawk", "nawk"}
_AWK_WRITE = re.compile(r"\bsystem\s*\(|\|\s*&?\s*getline|\bprintf?\b[^;}\n]*[>|]")


def _label_code(name: str) -> str:
    return f"une modification de {name}, fichier protégé d'Astrel"


def _written_text(x: _X, ctx: _Ctx) -> str | None:
    """Texte d'un fichier écrit plus tôt dans la même commande (None : inconnu)."""
    if x.dyn:
        return None
    for c in x.cands:
        for p in _paths(c, ctx) or []:
            kind, text = ctx.written.get(str(p), (None, None))
            if kind == "text" and text is not None:
                return text
    return None


def _cited_code(texts: list[str], ctx: _Ctx, in_cwd: bool = False, strip: bool = False) -> str:
    """Nom du fichier de `PROTECTED_CODE` que ces textes citent (vide sinon). Chaque
    mot qui ressemble à l'un d'eux est résolu depuis les dossiers courants de la
    commande. Dossier courant inconnu ou partie inconnue (`$D/server.py`) : cité.
    `in_cwd` : lancé DANS un dossier protégé (`cd hud/src && …`), tout compte."""
    return next(_cited_hits(texts, ctx, in_cwd, strip), ("", None))[0]


def _cited_guard(texts: list[str], ctx: _Ctx, in_cwd: bool = False,
                 strip: bool = False) -> tuple[str, bool | str, tuple[str, ...]]:
    """18/10 (plan P7) : (nom du PREMIER fichier protégé cité, comme `_cited_code` ;
    drapeau `guard`) : "dev" si TOUT ce qui est cité est dans `LIFTABLE`, True sinon.
    23/10 (plan P29) : + familles des fichiers cités quand "dev" (catégories des limites)."""
    hits = list(_cited_hits(texts, ctx, in_cwd, strip))
    if not hits:
        return "", False, ()
    if all(_liftable(p) for _n, p in hits):
        return hits[0][0], "dev", _hits_familles(p for _n, p in hits)
    # 24/10 (P29 lot B) : catégories (cases) de chaque fichier cité, pour le marqueur plafond.
    return hits[0][0], True, tuple(dict.fromkeys(_path_cat(p) if p is not None else _name_cat(n) for n, p in hits))


def _liftable(p: Path | None) -> bool:
    """18/10 (plan P7) : `p` est dans `LIFTABLE`, ou c'est un dossier dont TOUT ce qui
    est protégé dessous l'est (« cp -r x hud/ ») ; None (dossier inconnu) : non."""
    if p is None:
        return False
    if _under(p, LIFTABLE):
        return True
    prot = _protected_all()
    if _under(p, prot) or _plans_like(p):
        return False
    below = [q for q in prot if p in q.parents]
    return bool(below) and all(_under(q, LIFTABLE) for q in below)


def _cited_hits(texts: list[str], ctx: _Ctx, in_cwd: bool = False, strip: bool = False):
    """(nom, chemin ou None) de CHAQUE fichier de `PROTECTED_CODE` cité, dans l'ordre où
    `_cited_code` (avant le 18/10) rendait le premier."""
    if ctx.remote:
        return
    prot_cwd = [c for c in ctx.cwds or [] if _under(c, PROTECTED_CODE)]
    if in_cwd and prot_cwd:
        for c in prot_cwd:
            yield c.name, c
    for t in texts:
        if _FOUND in t:  # « {} » d'un find, entrée d'un xargs : les fichiers de ces dossiers
            for r in ctx.found or []:
                if _under(r, PROTECTED_CODE) or any(r in q.parents for q in PROTECTED_CODE):
                    yield r.name, r
            continue
        words = _PATH_WORD.findall(t)
        # « HEAD:astrel/server.py » (git show) : le chemin après la révision compte aussi.
        words += [w.rsplit(":", 1)[1] for w in words if ":" in w and "://" not in w]
        for w in words:
            # Dans un dossier protégé (`cd hud/src`), « main.js », « . », « * » y désignent un fichier.
            if not _cites_code_name(w) and not (
                    prot_cwd and _PATHISH.search(w) and not w.startswith("-") and "://" not in w):
                continue
            head, _sep, base = w.rpartition("/")
            if base in _NAMED_ANYWHERE:
                yield base, None
                continue
            if _DYN in w:
                if _DYN in head:  # « $D/server.py » : dossier inconnu
                    if _cites_code_name(base) and base not in _GENERIC_CODE_NAMES:
                        yield base.replace(_DYN, "…"), None
                    continue
                w = head + _sep + "x"  # « src/$f » : un fichier quelconque de ce dossier connu
            parts = w.split("/")
            # `strip` : un diff donné à patch/git apply écrit « a/astrel/server.py » (patch -p1).
            for k in range(min(len(parts), 4) if strip and not w.startswith("/") else 1):
                for p in _paths("/".join(parts[k:]), ctx) or [None]:
                    if p is None or _under(p, PROTECTED_CODE):
                        yield (p.name if p is not None else base), p


def _judge_cited(prog: str, args: list, ctx: _Ctx, out: list, stdin) -> None:
    """Une commande qui CITE un fichier protégé : libre si elle ne fait que lire
    (`_READ_ONLY` vérifié par `_segment_read_only` : pas de sed -i, git add…), jugée
    ailleurs pour les shells, interpréteurs, awk, find ; sinon question (garde-fou).
    `prog` vide : programme inconnu (script sur le disque, `$CMD`)."""
    if ctx.remote or prog in _SKIP_CITED or prog in _SHELLS or prog in _AWKS or (prog and _INTERP.fullmatch(prog)):
        return
    if prog == "git":
        args = _without_commit_message(args)  # 16/10 : le texte d'un message de commit n'écrit rien
    a = [x.text for x in args]
    # Variable lanceuse en vigueur (`_LAUNCH_VARS`) : le lecteur passerait le fichier à un
    # autre programme ; seules les commandes internes du shell restent sûres.
    launch = ctx.launch or ctx.env_unknown or any(_launch_var(k) for k in ctx.env)
    # 19/10 (relecture) : bat --pager/--paging=always lance un programme de page choisi.
    reader = prog in _CITE_READERS and not (prog in ("bat", "batcat") and any(t.startswith("--pag") for t in a))
    if prog in _SHELL_READERS or (not launch and (
            reader or (prog in _READ_ONLY and _segment_read_only([prog] + a)))):
        return
    name, flag, fams = _cited_guard([c for x in args for c in x.cands], ctx)
    if not name and stdin and stdin[0] == "text" and stdin[1]:
        # patch -p1 <<EOF, git apply <<EOF : le fichier visé est dans l'entrée
        name, flag, fams = _cited_guard([stdin[1]], ctx, strip=True)
    if name:
        _add(out, _label_code(name), guard=flag, cat=fams)  # 18/10 (plan P7) : "dev" si levable
        return
    # 16/10, plans : `cp` lit ses sources (sa destination est jugée par `_judge_write`),
    # sauf -l/-s qui en font des liens, modifiables ensuite. Lancé DANS un dossier des
    # plans, tout programme qui n'est pas un lecteur peut y écrire.
    if prog == "cp" and not any(t in ("--link", "--symbolic-link") or re.fullmatch(r"-[A-Za-z]*[ls][A-Za-z]*", t)
                                for t in a):
        return
    # 23/10 (audit P29, constat 4) : les fichiers listés par un find/ls/grep -rl, donnés par xargs
    # à un programme qui n'est pas un lecteur (`ls logs | xargs -I{} truncate -s0 logs/{}`).
    if ctx.found_spec is not None and any(_FOUND in c for x in args for c in x.cands):
        if _add_guard_hits(_find_guard_hits(*ctx.found_spec), out, deletion=prog in ("truncate", "shred", "mv")):
            return
    # 19/10 (audit) : un programme qui n'est pas un lecteur reçoit un joker qui peut désigner
    # un fichier du garde-fou (`cd logs; truncate -s 0 audit.*`, `chmod 666 runtime/auto*`).
    # `git -C */ remote -v` : la valeur de -C/-c (dossier, réglage) n'est pas un fichier visé.
    targets = [x for k, x in enumerate(args) if not (prog == "git" and k and a[k - 1] in ("-C", "-c"))]
    if _add_guard_hits([q for x in targets if x.glob for c in x.cands for q in _glob_guard_hits(c, ctx)], out,
                       deletion=prog in ("truncate", "shred", "mv")):
        return
    texts = [c for x in args for c in x.cands]
    if stdin and stdin[0] == "text" and stdin[1]:
        texts.append(stdin[1])
    if _cited_plans(texts, ctx, in_cwd=True):
        _add(out, L_GUARD, guard=True)


_CEILING_SKIP = {"cd", "pushd", "popd", "eval", "trap", "find", "fd", "fdfind", "ssh"}  # jugés ailleurs


def _claude_found(spec: tuple, out: list) -> None:
    """Les fichiers d'un find (ou de la liste donnée à xargs) que ses filtres PEUVENT retenir
    comptent-ils un réglage de Claude Code ? Alors plafond (`find ~ -name '*.json' -delete`)."""
    existing = [q for q in (*CLAUDE_SETTINGS_WRITE, *PROJECT_CLAUDE_WRITE) if q.exists()]  # un find ne trouve que l'existant
    if existing and _find_guard_hits(*spec, targets=existing):
        _add(out, L_CLAUDE_ID, guard=True)


def _judge_ceiling_cited(prog: str, args: list, ctx: _Ctx, out: list) -> None:
    """23/10 (plan P29, audit fiche 6, constat 3) : un programme qui CITE `~/.claude.json` ou un
    réglage de Claude Code (le sien, celui du projet, `.mcp.json`) sans seulement le lire (`ln`,
    `truncate`, `rm`, `chmod`, `cp -l`…) : plafond, comme `.credentials.json`. Mêmes lecteurs
    sûrs que `_judge_cited` ; `cp` simple lit sa source (sa destination est jugée à part) ;
    shells, interpréteurs et awk : leur code est jugé ailleurs (`_judge_code`)."""
    if ctx.remote or prog in _CEILING_SKIP or prog in _SHELLS or prog in _AWKS or (prog and _INTERP.fullmatch(prog)):
        return
    a = [x.text for x in args]
    launch = ctx.launch or ctx.env_unknown or any(_launch_var(k) for k in ctx.env)
    reader = prog in _CITE_READERS and not (prog in ("bat", "batcat") and any(t.startswith("--pag") for t in a))
    if prog in _SHELL_READERS or (not launch and (
            reader or (prog in _READ_ONLY and _segment_read_only([prog] + a)))):
        return
    if prog == "cp" and not any(t in ("--link", "--symbolic-link") or re.fullmatch(r"-[A-Za-z]*[ls][A-Za-z]*", t)
                                for t in a):
        return
    if prog == "git":
        args = _without_commit_message(args)
    label = _cited_ceiling([c for x in args for c in x.cands], ctx)
    if label:
        _add(out, label, guard=True)
    elif ctx.found_spec is not None and any(_FOUND in c for x in args for c in x.cands):
        _claude_found(ctx.found_spec, out)  # `find ~ -name '*.json' | xargs truncate -s0`


# Code d'un autre langage qui cite un fichier protégé : question seulement s'il contient
# une API capable d'écrire ou de lancer autre chose. Analyse de TEXTE, volontairement large.
_PY_WRITE = re.compile(
    r"\b(?:write_text|write_bytes|shutil|subprocess|fileinput|ctypes|mmap|importlib|__import__|__builtins__"
    r"|builtins|pty|FileIO)\b"
    r"|\bos\s*\.\s*(?:replace|renames?|remove|removedirs|rmdir|unlink|truncate|ftruncate|write|writev|pwrite|link"
    r"|symlink|chmod|chown|lchown|system|popen|exec\w*|spawn\w*|posix_spawn\w*|startfile|fdopen|open|fork|mkfifo"
    r"|utime|sendfile|copy_file_range)\b"
    r"|\bfrom\s+os\s+import\b|\bimport\s+os\s+as\b"
    r"|\bgetattr\s*\(\s*(?:os|shutil|pathlib|Path|io|sys|subprocess)\b"
    r"|\.(?:unlink|rename|symlink_to|hardlink_to|link_to|touch|chmod|lchmod|rmdir)\s*\("
    r"|\.replace\(\s*[^,()]*(?:\([^()]*\)[^,()]*)*\)"  # Path.replace(cible) : UN argument (str.replace en a deux)
    r"|(?<![\w.])(?:exec|eval)\s*\("
)
_JS_WRITE = re.compile(
    r"\b(?:writeFile|appendFile|createWriteStream|copyFile|cp|rename|unlink|rm|rmdir|truncate|ftruncate|symlink"
    r"|link|chmod|lchmod|chown|lchown|utimes|write|writev|open|mkdtemp)Sync\b"
    r"|\b(?:writeFile|appendFile|createWriteStream|copyFile)\b"
    r"|\bfs\s*\.\s*(?:promises|cp|rename|unlink|rm|rmdir|truncate|ftruncate|symlink|link|chmod|chown|utimes|open"
    r"|write)\b"
    r"|\b(?:import\s*\{|\{)[^}]*\b(?:rm|rename|unlink|truncate|symlink|cp|link|chmod|open|write)\b[^}]*\}\s*"
    r"(?:from\b|=\s*require\b)"
    r"|\bfsPromises\b|child_process|\bexecSync\b|\bexecFile\w*|\bspawn(?:Sync)?\b|\bfork\s*\("
    r"|(?<![\w.$])eval\s*\(|\bnew\s+Function\b|\bvm\s*\.\s*run|\bprocess\s*\.\s*binding"
    r"|\bDeno\s*\.\s*(?:write|remove|rename|run|Command)|\bBun\s*\.\s*(?:write|spawn)"
)
_OTHER_WRITE = re.compile(  # perl, ruby, php, lua… : tout ce qui ouvre, lance, renomme
    r"\b(?:open|sysopen|system|exec|eval|qx|unlink|rename|truncate|symlink|link|chmod|popen|passthru|shell_exec"
    r"|proc_open|file_put_contents|fopen|fwrite|copy|write|FileUtils|spawn)\b|`|%x|\bFile\s*\.|\bIO\s*\."
    r"|\bos\s*\.\s*(?:execute|remove|rename)|\bio\s*\.\s*(?:open|popen|output)|\bdo\s+shell\s+script\b"
)
_ANY_WRITE = re.compile(r"\bsed\s+-\w*i|\btee\s")  # commande shell écrite dans une chaîne
_OPEN = re.compile(r"\bopen\s*\(")
_MODE_LIKE = re.compile(r"""^[rb]?(['"])[rwaxbtU+]{1,4}\1$""")


def _call_args(code: str, i: int) -> list[str] | None:
    """Arguments (texte) d'un appel dont la parenthèse ouvrante précède `i` ; None : illisible."""
    args, cur, depth, quote = [], [], 0, ""
    while i < len(code):
        ch = code[i]
        if quote:
            cur.append(ch)
            if ch == "\\" and i + 1 < len(code):
                cur.append(code[i + 1])
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "'\"`":
            quote = ch
            cur.append(ch)
        elif ch in "([{":
            depth += 1
            cur.append(ch)
        elif ch in ")]}":
            if depth == 0:
                args.append("".join(cur).strip())
                return [x for x in args if x]
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    return None


def _opens_for_write(code: str) -> bool:
    """Un `open(…)` (Python, Node) dont le mode peut écrire ; mode calculé : oui."""
    for m in _OPEN.finditer(code):
        args = _call_args(code, m.end())
        if args is None:
            return True
        pre = code[:m.start()].rstrip()
        qual = re.search(r"(\w+)\s*\.\s*$", pre)
        kw = {k.group(1): a[k.end():].strip() for a in args if (k := re.match(r"(\w+)\s*=(?!=)", a))}
        pos = [a for a in args if not re.match(r"\w+\s*=(?!=)", a)]
        method = pre.endswith(".") and (qual is None or qual.group(1) not in ("io", "codecs", "builtins", "_io"))
        if method and pos and _MODE_LIKE.match(pos[0]):
            mode = pos[0]  # Path(…).open("w")
        else:
            mode = kw.get("mode") or kw.get("flags") or (pos[1] if len(pos) > 1 else None)
        if mode is None:
            continue
        lit = re.fullmatch(r"""[rb]?(['"])(.*)\1""", mode, re.S)
        if lit is None or not set(lit.group(2)) <= set("rbtU"):
            return True
    return False


def _code_writes(code: str, family: str) -> bool:
    if _ANY_WRITE.search(code):
        return True
    if family == "python":
        return bool(_PY_WRITE.search(code)) or _opens_for_write(code)
    if family == "node":
        return bool(_JS_WRITE.search(code)) or _opens_for_write(code)
    return bool(_OTHER_WRITE.search(code))


def _judge_code(code: str | None, rest: list, family: str, ctx: _Ctx, out: list) -> None:
    """Script en ligne (`python -c`, `python - <<EOF`, `node -e`…) : s'il cite un fichier
    protégé (dans son texte ou ses arguments), question seulement s'il peut écrire ou
    lancer. Code inconnu (script sur le disque, code venu d'un tuyau) : question."""
    texts = ([code] if code else []) + [c for x in rest for c in x.cands]
    _code_compte(code, ctx, out)  # 04/11 : code qui nomme un secret du compte (lecture : plafond)
    name, flag, fams = _cited_guard(texts, ctx, in_cwd=True)
    if name and (code is None or _code_writes(code, family)):
        _add(out, _label_code(name), guard=flag, cat=fams)  # 18/10 (plan P7) : "dev" si levable
        return
    # 23/10 (plan P29, audit fiche 6, constats 1 et 3) : code qui cite un réglage de Claude Code,
    # la venv ou le bytecode de `astrel`, et peut écrire (`open('…/.claude.json', 'w')`,
    # `Path.home() / ".claude.json"`, `…/site-packages/sitecustomize.py`) : plafond.
    if code is None or _code_writes(code, family):
        label = _cited_ceiling(texts, ctx, code=True)
        if label:
            _add(out, label, guard=True)
            return
    # 19/10 (audit) : `python3 x.py logs/aud*` : un joker donné en argument peut désigner un
    # fichier du garde-fou (un chemin construit DANS le code reste hors de portée).
    globbed = [q for x in rest if x.glob for c in x.cands for q in _glob_guard_hits(c, ctx)]
    if (code is None or _code_writes(code, family)) and _add_guard_hits(globbed, out):
        return
    # 16/10, plans : chemin résolu, ou texte du code (`"runtime" + "/plans/P3.json"`).
    plans = _cited_plans(texts, ctx, in_cwd=True) or bool(code and not ctx.remote and _PLANS_TEXT.search(code))
    if plans and (code is None or _code_writes(code, family)):
        _add(out, L_GUARD, guard=True)


def _cited_fallback(text: str, out: list) -> None:
    """Analyse impossible (syntaxe illisible) : retour au texte brut, tout nom cité compte."""
    name = _bash_protected_code(text)
    if name:
        _add(out, _label_code(name), guard=True)
    elif _PLANS_TEXT.search(text):  # 16/10 : un plan cité dans une commande illisible
        _add(out, L_GUARD, guard=True)
    if _cite_compte(text, False, _Ctx(cwds=[_base()]), entier=False):  # 04/11 : un secret du compte cité
        _add(out, L_SECRET_UTILISATEUR, guard=True, cat=_L.SECRETS_UTILISATEUR)


def _protected_all() -> list[Path]:
    return PROTECTED_PATHS + PROTECTED_CODE


# 19/10 (audit, accord de l'utilisateur) : un JOKER qui peut désigner un fichier du garde-fou
# (`cd logs; truncate -s 0 audit.*`, `echo x > logs/aud*`, `find logs -name 'audit*'
# -delete`) donnait une question ordinaire (doute, « destruction »…), que le mode Total
# accordait. Désormais : action sur le garde-fou. Limite connue : un chemin CONSTRUIT
# par du code (`python3 -c` qui assemble le nom) n'est pas vu (docs/DURCISSEMENT-GARDE-FOU.md).

def _glob_guard_hits(c: str, ctx: _Ctx) -> list[Path]:
    """Fichiers du garde-fou que le mot à joker `c` (valeur développée) PEUT désigner."""
    if ctx.remote or not c or c.startswith("-") or "://" in c:
        return []
    prot = _protected_all()
    hits: list[Path] = []
    for p, pattern in _scopes(c, ctx) or []:
        if _under(p, prot) or _plans_like(p, pattern):
            hits.append(p)
        elif pattern is not None:
            hits += [q for q in prot if _hits(p, pattern, q)]
    return hits


def _add_guard_hits(hits: list[Path], out: list, deletion: bool = False) -> bool:
    """Étiquette « garde-fou » pour ces fichiers ; levable ("dev") seulement pour une
    écriture dont TOUTES les cibles sont dans `LIFTABLE`, jamais pour une suppression."""
    if not hits:
        return False
    in_paths = any(_under(q, PROTECTED_PATHS) or _plans_like(q) for q in hits)
    label = "une modification du garde-fou d'Astrel" if in_paths else _label_code(hits[0].name)
    if deletion or not all(_liftable(q) for q in hits):
        # 24/10 (P29 lot B) : catégorie de chaque fichier visé (+ suppressions pour une suppression).
        cats = {_path_cat(q) for q in hits} | ({_L.SUPPRESSIONS} if deletion else set())
        _add(out, label, guard=True, cat=tuple(sorted(cats)))
    else:
        _add(out, label, guard="dev", cat=_hits_familles(hits))  # 23/10 (plan P29) : familles
    return True


# find : négation, « ou », expression régulière… : ce qui est retenu ne se lit plus (tout l'est).
_FIND_ANY = {"!", "-not", "-o", "-or", ",", "-regex", "-iregex", "-lname", "-ilname", "-samefile", "-inum"}
_FIND_PATH = {"-path": False, "-wholename": False, "-ipath": True, "-iwholename": True}
_FIND_WALK_MAX = 20000  # entrées lues au plus dans les dossiers protégés (au-delà : atteint)


def _fn(text: str, pat: str, icase: bool) -> bool:
    return fnmatch.fnmatchcase(text.lower(), pat.lower()) if icase else fnmatch.fnmatchcase(text, pat)


def _find_guard_hits(a: list[str], roots: list | None, skip_cache: bool = False,
                     targets: list | None = None) -> list[Path]:
    """Fichiers du garde-fou que les filtres de ce find PEUVENT retenir. `roots` :
    (début du chemin tel que find l'écrit, ou None si inconnu ; dossier). Expression en
    « et » seulement : chaque -name/-iname doit valoir pour le nom, chaque -path pour le
    chemin, -maxdepth est respecté ; le reste (-type, -mtime…) ne fait que restreindre,
    il est ignoré. Sans filtre, ou expression illisible : tout ce qui est sous les racines.
    Les dossiers protégés (hooks/, runtime/plans…) sont parcourus sur le disque.
    23/10 (audit P29) : `targets` = autre liste que le garde-fou (réglages de Claude Code)."""
    if roots is None:
        return []  # racines inconnues : déjà un doute (`_judge_delete`, `_judge_write`)
    names: list[tuple[str, bool]] = []
    paths: list[tuple[str, bool]] = []
    anything, maxdepth = False, None
    for t, nxt in zip(a, a[1:] + [""]):
        if t in ("-name", "-iname"):
            names.append((nxt, t == "-iname"))
        elif t in _FIND_PATH:
            paths.append((nxt, _FIND_PATH[t]))
        elif t in _FIND_ANY:
            anything = True
        elif t == "-maxdepth" and nxt.isdigit():
            maxdepth = int(nxt)

    def chosen(prefix: str | None, rel: Path, depth: int) -> bool:
        if maxdepth is not None and depth > maxdepth:
            return False
        if anything:
            return True
        if not all(_fn(rel.name, pat, icase) for pat, icase in names):
            return False
        # 03/11 : find écrit ses chemins en « / », Git Bash compris (str(rel) donnait « logs\\audit.jsonl »
        # sous Windows : `find . -path './logs/a*' -delete` ne voyait plus le journal du garde-fou).
        shown = None if prefix is None else (prefix.rstrip("/") or "/") + "/" + rel.as_posix()
        return all(shown is None or _fn(shown, pat, icase) for pat, icase in paths)

    prot = _protected_all() if targets is None else targets
    hits: list[Path] = []
    for prefix, r in roots:
        if _under(r, prot):
            hits.append(r)
            continue
        for q in prot:
            if r not in q.parents:
                continue
            rel = q.relative_to(r)
            if chosen(prefix, rel, len(rel.parts)):
                hits.append(q)
                continue
            if not q.is_dir():
                continue
            seen = 0
            for base, dirs, files in os.walk(q):
                if skip_cache:  # supprimer un cache de bytecode (`find . -name '*.pyc' -delete`) : sans danger
                    dirs[:] = [d for d in dirs if d != "__pycache__"]
                sub = Path(base).relative_to(r)
                seen += len(dirs) + len(files)
                if seen > _FIND_WALK_MAX or any(chosen(prefix, sub / n, len(sub.parts) + 1) for n in dirs + files):
                    hits.append(q)
                    break
    return hits


def _judge_write(x: _X, ctx: _Ctx, out: list) -> None:
    """Destination d'une écriture : garde-fou, disque, fichier sensible, hors projets."""
    if ctx.remote:
        return  # le disque d'une autre machine : ni projets ni fichiers sensibles d'ici
    critical = [r.resolve() for r in CRITICAL_WRITE_ROOTS] + _protected_all()
    for c in x.cands:
        if not c:
            continue
        if _win_douteux(c):  # 04/11 (CI Windows) : `\\?\Volume{…}\…` : destination inconnue, comme pour Write
            _add(out, D_WRITE, guard=True, cat=_L.DOUTES)
            continue
        c = _win_sans_prefixe(c)  # 04/11 (CI Windows) : le « ? » de `\\?\C:\…` n'est pas un joker
        if _FOUND in c:  # find -exec sed -i {}, grep -rl … | xargs sed -i : n'importe quel fichier de ces dossiers
            if ctx.found is None:
                _doubt(out, D_WRITE)
                continue
            if ctx.found_spec is not None:  # 23/10 (audit P29) : `grep -rl x logs | xargs sed -i …`
                _add_guard_hits(_find_guard_hits(*ctx.found_spec), out)
                _claude_found(ctx.found_spec, out)
            for r in ctx.found:
                _judge_fence(r, out, "*")  # n'importe quel fichier de ce dossier
                if not _under(r, _protected_all()) and any(_hits(r, None, q) for q in _protected_all()):
                    _add(out, L_WRITE_GUARD_DIR)
                elif any(_hits(r, None, q) for q in critical):
                    _add(out, f"une écriture dans {r.name or r}, hors de tes projets")
                else:
                    _judge_write_path(r, out)
            continue
        if _DYN in c:
            if _PLANS_TEXT.search(c.replace(_DYN, "/")):  # 16/10 : « $D/runtime/plans/P3.json »
                _add(out, L_GUARD, guard=True)
                continue
            # Seul le dossier est connu (« /tmp/$X.log ») : on juge un fichier neuf dedans,
            # sauf si ce dossier contient des fichiers sensibles (« ~/$X »).
            prefix = c.split(_DYN, 1)[0]
            if not prefix:
                _doubt(out, D_WRITE)
                continue
            known, _sep, start = prefix.rpartition("/")
            dirs = _paths((known or "/") if _sep else ".", ctx)
            # 19/10 : « > logs/$(…) » peut viser logs/audit.jsonl : garde-fou, pas un simple doute.
            if _add_guard_hits([q for d in dirs or [] for q in _protected_all()
                                if q.parent == d and q.name.startswith(start)], out):
                continue
            # 23/10 (plan P29, fiche 3) : « > config/$(…) » peut viser config/secrets.json : plafond.
            # Fiche 5 / audit fiche 6 (constat 5) : la cible est INCERTAINE, l'étiquette le dit (avant :
            # « compte ou serveurs MCP de Claude Code » pour « ~/$X »). Même chose pour un dossier
            # de la venv ou du bytecode (« .venv/lib/$P/site-packages/x.pth »).
            ceil = [q for d in dirs or [] for q in CEILING_WRITE if q.parent == d and q.name.startswith(start)]
            if ceil or any(_loaded_code(d / "x") or d in PROJECT_CLAUDE_DIRS for d in dirs or []):
                _add(out, D_WRITE, guard=True)
                continue
            if dirs is None or any(q.parent == d and q.name.startswith(start) for d in dirs for q in critical):
                _doubt(out, D_WRITE)
                continue
            paths = [d / "x" for d in dirs]
        elif _GLOB.search(c):  # cible avec joker : « echo x > ~/.ba*rc » peut viser .bashrc (5/10)
            scopes = _scopes(c, ctx)
            if scopes is None:
                _doubt(out, D_WRITE)
                continue
            for p, pattern in scopes:
                if pattern is None:
                    _judge_write_path(p, out)
                elif _add_guard_hits([q for q in _protected_all() if _hits(p, pattern, q)], out):
                    pass  # 19/10 : le joker peut toucher un fichier du garde-fou (avant : simple doute)
                elif [q for q in CEILING_WRITE if _hits(p, pattern, q)] or _loaded_code(p / "x"):
                    _add(out, D_WRITE, guard=True)  # 23/10 (plan P29, fiche 3 ; étiquette : audit fiche 6)
                elif any(_hits(p, pattern, q) for q in critical):
                    _doubt(out, D_WRITE)  # le joker peut toucher un fichier sensible
                else:
                    _judge_write_path(p, out)
            continue
        else:
            raw = _ceiling_raw(c, ctx)  # 23/10 (audit P29) : lien remplacé (`ln -sf x .venv/bin/python3`)
            if raw:
                _add(out, raw, guard=True)
                continue
            paths = _paths(c, ctx)
            if paths is None:
                _doubt(out, D_WRITE)
                continue
        for p in paths:
            _judge_write_path(p, out)


def _judge_write_path(p: Path, out: list) -> None:
    s = p.as_posix()
    if _DEV_FREE.match(s):
        return
    _judge_fence(p, out)  # mission en cours : hors périmètre ou dossier interdit
    if _DEV_DISK.match(s):
        _add(out, L_DISK_WRITE)
    elif _under(p, PROTECTED_PATHS) or _plans_like(p):
        # 23/10 (plan P29) : famille si "dev" ; 24/10 (lot B) : sinon la catégorie du fichier (case).
        _add(out, "une modification du garde-fou d'Astrel", guard="dev" if _liftable(p) else True,
             cat=_familles(p) if _liftable(p) else _path_cats(p))
    elif _under(p, PROTECTED_CODE):
        _add(out, f"une modification de {p.name}, fichier protégé d'Astrel", guard="dev" if _liftable(p) else True,
             cat=_familles(p) if _liftable(p) else _path_cats(p))
    elif _ceiling_write_label(p):  # 23/10 (plan P29, fiche 3) : secret, identifiants ou réglages de Claude Code
        _add(out, _ceiling_write_label(p), guard=True, cat=_ceiling_write_cat(p))
    elif any(p in q.parents for q in _protected_all()):
        _add(out, f"une écriture dans {p.name or s}, qui contient un fichier du garde-fou",
             guard="dev" if _liftable(p) else True,
             cat=_familles(p) if _liftable(p) else _path_cats(p))  # 18/10 (plan P7) : "dev" = levable
    elif secret_du_compte(p):  # 04/11 : une clé, un jeton, un mot de passe réécrits
        _add(out, L_SECRET_UTILISATEUR_MODIF)
    elif any(p in q.parents for q in [r.resolve() for r in CRITICAL_WRITE_ROOTS]):
        # Écrire DANS un dossier qui contient une zone sensible (`cp -r x ~/`, `mv ~/.config …`) :
        # une fusion peut écraser .bashrc, .ssh… (5/10).
        _add(out, f"une écriture dans {p.name or s}, qui contient un fichier sensible")
    else:
        level = _path_level(p)
        if level:
            _add(out, f"une écriture dans {p.name or s}, hors de tes projets", level)


def _judge_delete(targets: list, ctx: _Ctx, out: list, filtered: bool) -> None:
    """Suppression RÉCURSIVE. `filtered` : find -delete, rsync --delete… n'emportent
    pas forcément tout le dossier visé."""
    for x in targets:
        for c in x.cands:
            if not c:
                continue
            if _FOUND in c:
                if ctx.found is None:
                    _doubt(out, D_RM)
                    continue
                if ctx.found_spec is not None and not ctx.remote:  # 23/10 (audit P29) : `find logs | xargs rm`
                    _add_guard_hits(_find_guard_hits(*ctx.found_spec, skip_cache=True), out, deletion=True)
                    _claude_found(ctx.found_spec, out)
                scopes, filt = [(p, None) for p in ctx.found], True
            elif _DYN in c:
                if _PLANS_TEXT.search(c.replace(_DYN, "/")):  # 16/10 : « rm -rf $D/runtime/plans »
                    _add(out, L_GUARD, guard=True)
                    return
                _doubt(out, D_RM)
                continue
            else:
                scopes, filt = _scopes(c, ctx), filtered
                if scopes is None:
                    _doubt(out, D_RM)
                    continue
            for p, pattern in scopes:
                if ctx.remote:
                    _judge_remote_scope(p, pattern, out)
                else:
                    _judge_scope(p, pattern, filt, out)


def _judge_scope(p: Path, pattern: str | None, filtered: bool, out: list) -> None:
    """Ce que vise une suppression récursive (`p`, ou ce que le joker `pattern`
    y désigne). Libre seulement STRICTEMENT dans un projet autorisé."""
    _judge_fence(p, out, pattern)  # mission en cours : hors périmètre ou dossier interdit
    prot = _protected_all()
    if _under(p, prot) or _plans_like(p, pattern):
        _add(out, L_GUARD, guard=True, cat=_path_cats(p, None, deletion=True))  # 24/10 (P29 lot B)
        return
    if any(_hits(p, pattern, q) for q in prot):
        if not filtered:
            _add(out, L_GUARD, guard=True, cat=_path_cats(p, pattern, deletion=True))
            return
        _add(out, L_RM_GUARD_DIR)
    # 23/10 (plan P29, audit fiche 6) : `rm -rf .claude` dans le dossier de travail emporte ses réglages.
    claude = [*CLAUDE_SETTINGS_WRITE, *PROJECT_CLAUDE_WRITE]
    if (pattern is None and (_under(p, claude) or p in PROJECT_CLAUDE_DIRS)) or (
            not filtered and any(_hits(p, pattern, q) for q in claude)):
        _add(out, L_CLAUDE_ID, guard=True)
        return
    roots = [r.resolve() for r in ALLOWED_WRITE_ROOTS]
    if p in roots:  # « rm -rf /tmp/cb-*.log » : des entrées du projet ; « rm -rf * » : tout le projet
        inside = pattern is not None and _literal(pattern)
    else:
        inside = any(r in p.parents for r in roots)
    inside = inside and not any(_hits(p, pattern, r) for r in roots)  # rm -rf Studio emporterait Astrel
    compte = list(_US.racines)  # 04/11 : un secret du compte (ou ce qui en contient) n'est jamais « dans un projet »
    inside = inside and not (secret_du_compte(p) or _under(p, compte) or any(_hits(p, pattern, r) for r in compte))
    _add(out, L_RM if inside else L_RM_OUTSIDE, SENSITIVE if inside else CRITICAL)


def _literal(pattern: str) -> bool:
    """Le joker a-t-il une partie fixe (« *.log », « cb-run* ») ? « * », « .* » : non."""
    return bool(re.sub(r"[*?.]|\[[^\]]*\]", "", pattern))


def _judge_remote_scope(p: Path, pattern: str | None, out: list) -> None:
    """Sur une autre machine, les projets d'ici ne veulent rien dire : seule une
    suppression LARGE (racine, /var, /opt/appli, dossier personnel, joker nu) est critique."""
    target = p / "x" if pattern is not None and _literal(pattern) else p
    wide = len(target.parts) <= 3 or target in (Path.home(), *Path.home().parents)
    _add(out, L_RM_REMOTE_WIDE if wide else L_RM_REMOTE, CRITICAL if wide else SENSITIVE)


def _judge_plain_delete(x: _X, ctx: _Ctx, out: list) -> None:
    """rm sans -r : seuls le garde-fou et les fichiers sensibles comptent."""
    if ctx.remote:
        return
    critical = [r.resolve() for r in CRITICAL_WRITE_ROOTS]
    for c in x.cands:
        if c and _DYN in c and _PLANS_TEXT.search(c.replace(_DYN, "/")):  # 16/10 : « rm $D/runtime/plans/P3.json »
            _add(out, L_GUARD, guard=True)
            continue
        if not c or _DYN in c:
            continue
        if _win_douteux(c):  # 04/11 (CI Windows) : `rm -f '\\?\Volume{…}\…'` : cible inconnue (avant : libre)
            _add(out, D_WRITE, guard=True, cat=_L.DOUTES)
            continue
        for p, pattern in _scopes(c, ctx) or []:
            _judge_fence(p, out, pattern)
            prot = _protected_all()
            if (pattern is None and _under(p, prot)) or any(_hits(p, pattern, q) for q in prot) or _plans_like(p, pattern):
                _add(out, L_GUARD, guard=True, cat=_path_cats(p, pattern, deletion=True))  # 24/10 (P29 lot B)
            elif (pattern is None and (_under(p, critical) or secret_du_compte(p))) or any(
                    _hits(p, pattern, q) for q in critical):  # 04/11 : + un secret du compte (coffre .kdbx…)
                _add(out, f"une suppression de {p.name if pattern is None else pattern}, hors de tes projets")


def _analyze_bash(cmd: str) -> list[tuple[str, str, bool, bool, tuple[str, ...]]]:
    """(étiquette, gravité, autorisable 30 min, garde-fou, catégories) de chaque danger trouvé."""
    base = _base().resolve()
    ctx = _Ctx(cwds=[base], seen=[base], dirs_stable=not _DELETES_TEXT.search(cmd))
    out: list = []
    try:
        _analyze(cmd, ctx, out)
    except Exception:  # une erreur de l'analyse elle-même : dans le doute, demander
        _doubt(out, D_SYNTAX)
        _cited_fallback(cmd, out)
    if any(f[0] in (D_WRITE, D_RM) and f[3] is not True for f in out):
        _computed_target(cmd, out)
    return out


def _computed_target(cmd: str, out: list) -> None:
    """23/10 (plan P29, audit fiche 6, constat 4) : une écriture ou une suppression dont la cible
    est CALCULÉE (`sed -i … $(echo astrel/totalgate.py)`, `cp x "$f"`) n'était qu'un doute, que
    la case « doutes » d'Autonome TOTAL accordait. Si la commande cite (en texte) un fichier
    protégé, un réglage de Claude Code, ou un dossier qui contient DIRECTEMENT un fichier du
    garde-fou (`$(grep -rl x astrel/)`, `logs`, `runtime` ; pas la racine d'Astrel ni au-dessus,
    cités partout) : plafond. Mots résolus depuis le dossier courant et chaque `cd`. Une erreur
    de cette lecture : plafond aussi (un mot piégé ne doit pas la faire sauter)."""
    try:
        label = _computed_target_label(cmd)
    except Exception:  # noqa: BLE001
        label = L_GUARD
    if label:
        _add(out, label, guard=True)


def _computed_target_label(cmd: str) -> str:
    name = _bash_protected_code(cmd)
    if name:
        return _label_code(name)
    bases = [_base()]
    for m in list(_CD.finditer(cmd))[:8]:
        d = Path(os.path.expanduser(m.group(1).strip("'\"")))
        bases += [d.resolve()] if d.is_absolute() else [(b / d).resolve() for b in bases]
    prot = _protected_all()
    root = config.ROOT.resolve()
    for w in _PATH_WORD.findall(cmd)[:400]:
        if w.startswith("-") or "://" in w:
            continue
        if w.rsplit("/", 1)[-1] == _CLAUDE_NAMED:
            return L_CLAUDE_ID
        p = Path(os.path.expanduser(w))
        for b in ([Path("/")] if p.is_absolute() else bases):
            full = (b / p).resolve()
            if _under(full, prot) or _plans_like(full):
                return L_GUARD
            label = _ceiling_write_label(full)
            if label and not _loaded_code(full):
                return label
            if full != root and any(q.parent == full for q in prot) and _is_dir(full):
                return L_GUARD
    return ""


def _single(needs: bool, desc: str = "", key: str = "", level: str = "", guard: bool = False,
            lift: bool = False, cats: tuple[str, ...] = (),
            cat_items: tuple[tuple[str, tuple[str, ...]], ...] | None = None) -> Verdict:
    """Verdict d'un seul danger (tout sauf les commandes Bash composées).
    23/10 (plan P29) : `cats` (défaut : plafond si `guard`, sinon inconnue, donc demandé) ;
    `cat_items` : (gravité, catégories) de tous les dangers vus, si l'étiquette en résume plusieurs."""
    if not needs:
        return Verdict(False)
    if guard is True and (not cats or _L.PLAFOND in cats):
        # 24/10 (P29 lot B) : marqueur plafond + catégorie précise (`_add`) ; une famille levable
        # (Write d'un fichier du HUD) garde ses seules familles, comme avant.
        rest = tuple(c for c in cats if c != _L.PLAFOND)
        cats = (_L.PLAFOND,) + (rest or _ceiling_label_cats(desc))
    cats = tuple(cats) or ((_L.PLAFOND,) if guard else (_INCONNUE,))
    if cat_items is None:
        cat_items = ((level, cats),)
    return Verdict(True, desc, key, level, keys=(key,) if key else (), guard=guard, items=((key, level, desc),),
                   lift=lift, cats=_union_cats(cat_items), cat_items=cat_items)


def _writes_text(cmd: str) -> bool:
    """24/10 (P29 lot B) : un signe d'écriture dans le TEXTE de la commande (large exprès)."""
    return bool(_ANY_WRITE.search(cmd) or _PY_WRITE.search(cmd) or _JS_WRITE.search(cmd)
                or _DELETES_TEXT.search(cmd) or _opens_for_write(cmd))


def _cited_name_cat(hit: str) -> str:
    """Catégorie d'un nom capté par `PROTECTED_BASH` (« audit.jsonl », « --send estop »…)."""
    if hit.startswith(("--send", "\"", "'")):
        return _L.ETAT_GARDE_FOU
    return _name_cat(hit.rsplit("/", 1)[-1])


def _union_cats(cat_items) -> tuple[str, ...]:
    return tuple(dict.fromkeys(c for _lvl, cs in cat_items for c in cs))


# 03/11/2026 (portage Windows, autorisé par l'utilisateur) : `cat C:\Users\…\hud-token` NON cité. Git Bash mange les
# « \ » (rien n'est lu, la commande échoue), mais l'intention est claire et un autre interpréteur (cmd.exe,
# PowerShell, une ligne recitée pour `bash -c`) lirait le chemin tel quel : la commande est AUSSI jugée avec ces
# chemins en « / », et le verdict le plus strict des deux l'emporte. Jamais moins strict que la lecture de Bash.
_CHEMIN_WIN = re.compile(r"(?<![\w/\\])[A-Za-z]:\\[^\s;|&<>()`'\"]*")
_LECTURE_WINDOWS = os.name == "nt"


def _lecture_windows(cmd: str) -> str | None:
    """La commande avec ses chemins « X:\\… » écrits en « / » ; None s'il n'y en a pas (ou hors Windows)."""
    if not _LECTURE_WINDOWS or ":\\" not in cmd:
        return None
    autre = _CHEMIN_WIN.sub(lambda m: m.group(0).replace("\\", "/"), cmd)
    return autre if autre != cmd else None


def _rang(v: Verdict) -> tuple:
    """Sévérité d'un verdict : demander, garde-fou (plafond, puis levable), non levable, critique."""
    return (v.needs_confirm, v.guard is True, bool(v.guard), not v.lift, v.level == CRITICAL)


def classify(tool_name: str, tool_input: dict, cwd: str | None = None) -> Verdict:
    """Gravité intrinsèque de l'action, indépendamment du mode. `cwd` : dossier
    courant de Claude (transmis par le hook), pour les chemins relatifs."""
    token = _CWD.set(str(cwd or ""))
    fence = _MISSION.set(mission_fence())  # barrière de la mission en cours (lue à chaque appel du hook)
    try:
        v = _classify(tool_name, tool_input)
        autre = _lecture_windows(str(tool_input.get("command", ""))) if tool_name == "Bash" else None
        if autre is not None:
            w = _classify(tool_name, {**tool_input, "command": autre})
            if _rang(w) > _rang(v):
                v = w
        z = _zone_lue(tool_name, tool_input)  # 04/11 (lot 3b) : zone confidentielle, le plus strict l'emporte
        if z is not None and _rang(z) > _rang(v):
            v = z
        return v
    finally:
        _MISSION.reset(fence)
        _CWD.reset(token)


def _classify(tool_name: str, tool_input: dict) -> Verdict:
    if tool_name in ("Read", "Grep", "NotebookRead"):
        target = str(tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path") or "")
        if _win_douteux(target):  # 04/11 (lot A) : `\\?\Volume{…}\…` peut être un secret
            return _single(True, L_SECRET, "", CRITICAL, guard=True, cats=(_L.PLAFOND, _L.SECRETS))
        if target and (_under(_resolve(target), SECRET_PATHS) or _resolve(target) in [p.parent for p in SECRET_PATHS]):
            # 24/10 (P29 lot B) : le secret lui-même, ou une recherche dans le dossier qui le contient.
            wide = not _under(_resolve(target), SECRET_PATHS)
            return _single(True, L_SECRET, "", CRITICAL, guard=True,
                           cats=(_L.PLAFOND, _L.SECRETS_LARGES if wide else _L.SECRETS))
        compte = _lecture_compte(tool_name, tool_input)  # 04/11 : secret du compte (clé SSH, jeton, navigateur)
        if compte is not None:
            return compte
    if tool_name in SAFE_TOOLS:
        return Verdict(False)

    if tool_name in NATIVE_POLICY:
        desc, level = NATIVE_POLICY[tool_name]
        return _single(True, desc, tool_name, level, cats=(_NATIVE_CATS.get(tool_name, _L.DOUTES),))

    if tool_name in WRITE_TOOLS:
        target = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if _win_douteux(target):  # 04/11 (lot A) : forme Windows qui ne se ramène à aucun lecteur connu
            return _single(True, D_WRITE, "", CRITICAL, guard=True, cats=(_L.PLAFOND, _L.DOUTES))
        if _protected(target) or _protected_code(target):
            p = _resolve(target)
            label = ("une modification du garde-fou d'Astrel" if _protected(target)
                     else f"une modification de {p.name}, fichier protégé d'Astrel")
            # 18/10 (plan P7) : levable si le fichier est dans `LIFTABLE` et hors d'une barrière de mission.
            fence_out: list = []
            _judge_fence(p, fence_out)
            # 23/10 (plan P29) : famille du fichier levable (sinon plafond), + catégories de la barrière.
            # 24/10 (P29 lot B) : plafond = marqueur + catégorie du fichier (case de confirmation).
            cats = (_familles(p) if _liftable(p) else ()) or (_L.PLAFOND, _path_cat(p))
            cats += tuple(c for f in fence_out for c in f[4] if c not in cats)
            return _single(True, label, "", CRITICAL, guard=True, lift=_liftable(p) and not fence_out, cats=cats)
        # 23/10 (audit P29) : + le chemin non résolu (`.venv/bin/python3` est un lien vers /usr/bin).
        ceiling = (_ceiling_write_label(_resolve(target)) or _ceiling_write_label(
            Path(os.path.normpath(_base() / os.path.expanduser(target.strip().strip("'\"")))))) if target else None
        if ceiling:  # 23/10 (plan P29, fiche 3) : secret, identifiants ou réglages de Claude Code
            return _single(True, ceiling, "", CRITICAL, guard=True, cats=(_L.PLAFOND, _ceiling_cat(target)))
        if target and secret_du_compte(_resolve(target)) and not _MISSION.get():  # 04/11 : clé, jeton réécrits
            return _single(True, L_SECRET_UTILISATEUR_MODIF, f"bash:{L_SECRET_UTILISATEUR_MODIF}", CRITICAL,
                           cats=(_L.ECRITURE,))
        level = _write_level(target)
        fence: list = []
        if target:
            _judge_fence(_resolve(target), fence)
        if fence:  # mission en cours : hors de son périmètre, ou dossier interdit
            label, _lvl, _grantable, fguard, _cats = fence[0]
            if fguard:
                return _single(True, label, "", CRITICAL, guard=True)
            items = [(K_MISSION_OUT, CRITICAL, label)]
            cat_items = [(CRITICAL, (_L.MISSION,))]
            if level is not None:
                items.append((f"write:{level}", level, f"une écriture dans {Path(target).name}, hors de tes projets"))
                cat_items.append((level, (_L.ECRITURE,)))
            return Verdict(True, _join([it[2] for it in items]), K_MISSION_OUT, CRITICAL,
                           keys=tuple(it[0] for it in items), items=tuple(items),
                           cats=_union_cats(cat_items), cat_items=tuple(cat_items))
        if level is None:
            return Verdict(False)
        return _single(True, f"une écriture dans {Path(target).name}, hors de tes projets", f"write:{level}", level,
                       cats=(_L.ECRITURE,))

    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))
        read_only = _read_only(cmd)
        if _reads_secret(cmd, read_only):
            # 23/10 (fiche 5, audit fiche 6 constat 5) : « echo x > config/secrets.json » n'est pas une
            # LECTURE : l'étiquette dit la modification quand la commande écrit dans un secret.
            written = [] if read_only else [f[0] for f in _analyze_bash(cmd) if f[3] is True and f[0] != L_SECRET]
            return _single(True, written[0] if written else L_SECRET, "", CRITICAL, guard=True)
        # 19/10 : un nom du garde-fou cité seulement par des lecteurs sûrs ne demande plus.
        if PROTECTED_BASH.search(cmd) and not read_only and not _cites_only_to_read(cmd):
            # 21/10 (« assouplis le plus possible », l'utilisateur) : si TOUT ce que la commande cite du
            # garde-fou est levable (guard.py, confirm.py, le hook : fichiers de `LIFTABLE`), la carte
            # n'est plus demandée pendant une levée (heredoc, substitution, script de test qui les
            # cite). Sans levée, elle reste. Suppression, nom non levable, commande illisible : demandé.
            hits = [m.group(0).strip() for m in PROTECTED_BASH.finditer(cmd)]
            lift = False
            found = []
            if hits and all(h in _LIFTABLE_NAMES for h in hits):
                found = _analyze_bash(cmd)
                lift = all(f[3] is not True and f[2] for f in found) and not _DELETES_TEXT.search(cmd)
            # 23/10 (plan P29) : levable = code du garde-fou (les noms de `_LIFTABLE_NAMES`), plus les
            # catégories de TOUT ce que la commande fait d'autre (« git push && … guard.py » : git aussi).
            # 24/10 (P29 lot B, demande de l'utilisateur) : non levable = marqueur plafond + « citation » (case
            # cochée par défaut : `tail audit.jsonl | python3 -m json.tool` ne fait que lire) ; s'il y a un
            # SIGNE D'ÉCRITURE dans le texte (sed -i, tee, open(…, "w"), write_text, os.remove…), aussi la
            # catégorie de chaque nom cité (code, état, journaux : cochées par défaut).
            if lift:
                main: tuple[str, ...] = (_L.CODE_GARDE_FOU,)
            else:
                found = found or _analyze_bash(cmd)
                main = (_L.PLAFOND, _L.CITATION)
                if _writes_text(cmd):
                    main += tuple(dict.fromkeys(_cited_name_cat(h) for h in hits))
            cat_items = ((CRITICAL, main),) + tuple((f[1], f[4]) for f in found)
            return _single(True, "une action sur le garde-fou d'Astrel", "", CRITICAL, guard="dev" if lift else True,
                           lift=lift, cat_items=cat_items)
        # 15/10, partie B : analyse structurée (programme réellement lancé, arguments réels).
        # Les fichiers de `PROTECTED_CODE` y sont jugés commande par commande (`_judge_cited`) :
        # les LIRE est libre, même cités au milieu d'une commande qui lance autre chose.
        found = _analyze_bash(cmd)
        if any(f[3] for f in found):  # suppression ou écriture qui touche au garde-fou, même sans le nommer
            # 18/10 (plan P7) : avant, seul le PREMIER danger « garde-fou » était rendu et le reste
            # jeté (« git push && cp x hud/src/main.js » ne montrait que main.js). Toutes les
            # étiquettes critiques sont dites (les levables après la première, non : redites du
            # même fichier) ; levable seulement si CHACUNE est "dev".
            critical = [f for f in found if f[1] == CRITICAL]
            first = next(f[0] for f in found if f[3])
            labels = list(dict.fromkeys([first] + [f[0] for f in critical if f[3] != "dev"]))
            # 23/10 (plan P29) : catégories de TOUS les dangers (le mode choisit dans `assess`).
            cat_items = tuple((f[1], f[4]) for f in found)
            return Verdict(True, _join(labels), "", CRITICAL, guard=True,
                           items=tuple(("", CRITICAL, label) for label in labels),
                           lift=all(f[3] == "dev" for f in critical), cats=_union_cats(cat_items),
                           cat_items=cat_items)
        if not found:
            return Verdict(False)
        found.sort(key=lambda f: 0 if f[1] == CRITICAL else 1)  # le plus grave d'abord
        items: list[tuple[str, str, str]] = []
        for label, level, grantable, _guard, _cats in found:
            if not any(it[2] == label for it in items):
                # Un doute d'analyse n'est jamais couvert par « oui pour 30 min ».
                items.append((f"bash:{label}" if grantable else "", level, label))
        keys = tuple(it[0] for it in items) if all(it[0] for it in items) else ()
        cat_items = tuple((f[1], f[4]) for f in found)
        return Verdict(True, _join([it[2] for it in items]), items[0][0], items[0][1], keys=keys, items=tuple(items),
                       cats=_union_cats(cat_items), cat_items=cat_items)

    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__", 2)
        server, tool = (parts[1], parts[2]) if len(parts) == 3 else ("", tool_name)
        pretty = server.replace("claude_ai_", "")
        if tool in REFUSED_MCP_TOOLS:  # 04/11 (lot A) : refusé d'office par le hook (`refused`) ; ici, au plafond
            return _single(True, f"l'action {tool.replace('_', ' ')} sur {pretty}", "", CRITICAL, guard=True)
        table = _mcp_table(server)
        if table is not None and tool in table:
            level = table[tool] or None
        else:
            level = _mcp_level(tool)  # 04/11 (lot A) : absent d'un serveur connu = jugé sur son nom
        level = _mcp_input_level(server, tool, tool_input, level)
        # 20/10 (plan P12, lot 6) : le fichier de politique règle un outil ; jamais sous CRITIQUE.
        wanted = _policy_level(server, tool)
        if wanted is not None and level != CRITICAL:
            level = wanted or None
        elif wanted == CRITICAL:
            level = CRITICAL
        if level is None:
            return Verdict(False)
        return _single(True, f"l'action {tool.replace('_', ' ')} sur {pretty}", tool_name, level, cats=_mcp_cats(tool))

    return _single(True, f"l'utilisation de l'outil {tool_name}", tool_name, CRITICAL, cats=(_L.DOUTES,))


# --- politique d'outils (20/10, plan P12, lot 6 ; idée d'openclaw : la règle dans un fichier
# lu par du code, pas dans le prompt) ---
#
# `hooks/tool-policy.json` (protégé avec `hooks/`, dans l'empreinte du doctor) :
#   {"mcp": {"<serveur>": {"<outil>|*": "libre|sensible|critique"}}, "refuser": ["<outil>", "mcp__x__*"]}
# Un outil réglé ici remplace la gravité du code (MCP_POLICY, `_mcp_level`), SAUF une gravité
# CRITIQUE du code, jamais abaissée (envoyer, supprimer, payer…). `refuser` : refusé d'office,
# sans carte, même en Autonome TOTAL (`refused`, appelé par le hook avant `assess`). Fichier
# absent ou illisible : valeurs du code seules (`policy_error` le dit au doctor).
TOOL_POLICY_FILE = config.ROOT / "hooks" / "tool-policy.json"
_POLICY_LEVELS = {"libre": "", "sensible": SENSITIVE, "critique": CRITICAL}
_policy_cache: dict = {"key": None, "data": {}, "error": ""}


def _tool_policy() -> dict:
    """Politique lue sur disque, relue seulement si le fichier change (mtime, taille)."""
    try:
        st = TOOL_POLICY_FILE.stat()
        key = (str(TOOL_POLICY_FILE), st.st_mtime_ns, st.st_size)
    except OSError:
        _policy_cache.update(key=None, data={}, error="")
        return {}
    if _policy_cache["key"] == key:
        return _policy_cache["data"]
    data, error = {}, ""
    try:
        raw = json.loads(TOOL_POLICY_FILE.read_text(encoding="utf-8"))
        mcp = raw.get("mcp", {}) if isinstance(raw, dict) else None
        refuse = raw.get("refuser", []) if isinstance(raw, dict) else None
        if not isinstance(mcp, dict) or not isinstance(refuse, list):
            raise ValueError("« mcp » doit être un objet et « refuser » une liste")
        levels: dict[str, dict[str, str]] = {}
        for server, tools in mcp.items():
            if not isinstance(tools, dict):
                raise ValueError(f"serveur {server} : objet attendu")
            for tool, word in tools.items():
                if not isinstance(word, str) or word not in _POLICY_LEVELS:
                    raise ValueError(f"{server}/{tool} : « {word} » (libre, sensible ou critique)")
                levels.setdefault(str(server), {})[str(tool)] = _POLICY_LEVELS[word]
        if not all(isinstance(r, str) and r for r in refuse):
            raise ValueError("« refuser » : des noms d'outils")
        data = {"mcp": levels, "refuser": list(refuse)}
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        # Illisible : les gravités du code décident, mais la liste « refuser » du dernier fichier
        # valide reste appliquée (une faute de frappe ne doit pas rouvrir un outil refusé).
        prev = _policy_cache["data"].get("refuser", []) if isinstance(_policy_cache["data"], dict) else []
        data, error = ({"mcp": {}, "refuser": list(prev)} if prev else {}), str(exc)[:200]
    _policy_cache.update(key=key, data=data, error=error)
    return data


def policy_error() -> str:
    """Pourquoi le fichier de politique est ignoré (vide : lu, ou absent)."""
    _tool_policy()
    return _policy_cache["error"]


def _policy_level(server: str, tool: str) -> str | None:
    """Gravité réglée par le fichier : "" (libre), sensible, critique ; None : rien de réglé."""
    tools = _tool_policy().get("mcp", {}).get(server)
    if not tools:
        return None
    return tools.get(tool, tools.get("*"))


def _ids_commandes_locales(fichier: Path | None = None) -> frozenset[str]:
    """Identifiants de la liste de l'utilisateur (`config/commandes-locales.json`). Absente, illisible ou d'une autre
    forme : aucun (tout est refusé). La validation complète (argv, dossier, délai) est faite par le serveur."""
    try:
        data = json.loads(Path(fichier or COMMANDES_LOCALES_FILE).read_text(encoding="utf-8"))
        cmds = data.get("commandes") if isinstance(data, dict) else None
    except (OSError, ValueError):
        return frozenset()
    if not isinstance(cmds, dict):
        return frozenset()
    return frozenset(k for k, v in cmds.items() if isinstance(k, str) and _ID_COMMANDE_LOCALE.match(k)
                     and isinstance(v, dict) and isinstance(v.get("argv"), list) and v["argv"])


def _du_cli_local(env: dict | None = None) -> bool:
    """Le hook tourne-t-il pour le CLI LOCAL (`outils_local.environnement` : son dossier de configuration) ?"""
    vu = (os.environ if env is None else env).get("CLAUDE_CONFIG_DIR") or ""
    return bool(vu) and os.path.realpath(vu) == os.path.realpath(str(LOCAL_CLI_DIR))


def _cli_local_reel(env: dict | None = None, pid: int | None = None, fichier: Path | None = None) -> bool:
    """05/11 (essais réels du moteur local, accord de l'utilisateur pour ce chantier) : le hook juge-t-il un outil du
    VRAI CLI local ? Il faut les trois : son dossier de configuration (`_du_cli_local`) ; un ancêtre du hook
    inscrit par le cœur dans `LOCAL_CLI_PIDS_FILE` (protégé) ; et cet ancêtre enfant DIRECT du cœur qui l'a
    inscrit. Un CLI que Claude lancerait lui-même (même avec CLAUDE_CONFIG_DIR) descend du cerveau, pas du cœur :
    non. Sert à une seule chose : une lecture du LOCAL dans une zone confidentielle n'est pas demandée (rien ne
    sort de la machine) ; secrets du garde-fou et du compte restent jugés comme pour Claude."""
    if not _du_cli_local(env):
        return False
    try:
        data = json.loads(Path(fichier or LOCAL_CLI_PIDS_FILE).read_text(encoding="utf-8"))
        coeur = int(data.get("coeur") or 0)
        pids = {int(x) for x in data.get("pids") or ()}
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    if coeur <= 1 or not pids:
        return False
    from .platform import proc

    cur = os.getpid() if pid is None else pid
    for _ in range(3):  # hook → (sh) → CLI local : jamais plus loin (un CLI lancé par Claude est sous son Bash)
        cur = proc.parent(cur) or 0
        if cur <= 1 or cur == coeur:
            return False
        if cur in pids:
            return proc.parent(cur) == coeur
    return False


def _refus_outil_local(tool: str, tool_input: object, fichier: Path | None = None, env: dict | None = None) -> str:
    """05/11 (lot 5) : raison de refuser SANS CARTE un outil du serveur `astrel_local` ("" : libre). Refusé :
    hors du CLI local, outil inconnu, entrée illisible, clé en plus (« argument ajouté »), et pour
    `commande_autorisee` un identifiant absent de la liste de l'utilisateur."""
    if not _du_cli_local(env):
        return (f"l'outil {tool} n'existe que pour le modèle local d'Astrel : refusé par le garde-fou. "
                "N'insiste pas, propose une autre voie.")
    attendu = _ENTREES_LOCALES.get(tool)
    if attendu is None or not isinstance(tool_input, dict):
        return f"outil {tool} inconnu ou entrée illisible : refusé par le garde-fou d'Astrel."
    requis, permis = attendu
    cles = set(tool_input)
    if not requis <= cles or not cles <= permis:
        return (f"{tool} : argument non permis ({', '.join(sorted(cles - permis)) or 'manquant'}) : refusé par "
                "le garde-fou d'Astrel. Aucune option ni argument ne s'ajoute à une commande autorisée.")
    if tool == "commande_autorisee":
        cid = tool_input.get("id")
        if not isinstance(cid, str) or cid not in _ids_commandes_locales(fichier):
            return (f"commande « {str(cid)[:60]} » absente de la liste de l'utilisateur : refusée par le garde-fou "
                    "d'Astrel. Seuls les identifiants de liste_commandes se lancent ; n'en invente pas.")
    elif tool == "proposer_modification" and not isinstance(tool_input.get("fichier"), str):
        return "proposer_modification : chemin du fichier illisible : refusé par le garde-fou d'Astrel."
    return ""


# 05/11 (lot 7 partie B du moteur local, autorisation de l'utilisateur pour ce chantier) : outils web et navigateur du
# CLI LOCAL. Navigateur : 8 outils seulement (les autres ne sont pas montrés au modèle, `--disallowedTools`, et
# refusés ici en plus) ; `browser_navigate` n'ouvre qu'une adresse VUE dans le tour (registre tenu par le serveur
# `web_local`, fichier de `ASTREL_LOCAL_REGISTRE_WEB`, même règle que lire_page) : jamais une adresse fabriquée.
NAVIGATEUR_LOCAL_OUTILS = ("browser_navigate", "browser_navigate_back", "browser_snapshot", "browser_find",
                           "browser_click", "browser_type", "browser_press_key", "browser_select_option")
_WEB_LOCAL_OUTILS = ("recherche_web", "lire_page")
_SERVEURS_WEB_LOCAL = ("mcp__web_local__", "mcp__playwright_local__")
REGISTRE_WEB_ENV = "ASTREL_LOCAL_REGISTRE_WEB"


def _cle_adresse(url: object) -> str:
    """Forme comparable d'une adresse http(s) (schéma et nom en minuscules, port par défaut et #fragment retirés,
    « / » pour un chemin vide) ; "" si ce n'en est pas une. Plus stricte que `web_local.normaliser` (pas d'IDNA) :
    une adresse écrite autrement que dans le registre est REFUSÉE, jamais acceptée par erreur."""
    m = re.match(r"^(https?)://([^/?#@\s]+)(?=[/?#]|$)([^#\s]*)", str(url or "").strip(), re.I)
    if not m:
        return ""
    schema, hote, reste = m.group(1).lower(), m.group(2).lower().rstrip("."), m.group(3) or "/"
    hote = re.sub(r":80$", "", hote) if schema == "http" else re.sub(r":443$", "", hote)
    return f"{schema}://{hote}{reste if reste.startswith('/') else '/' + reste}"


def _adresses_du_tour(env: dict | None = None) -> frozenset[str]:
    """Registre des adresses vues dans le tour (écrit par le serveur `web_local` du CLI local) ; vide s'il manque."""
    chemin = (os.environ if env is None else env).get(REGISTRE_WEB_ENV) or ""
    try:
        p = Path(chemin)
        if not chemin or not p.is_absolute() or p.is_symlink() or not p.is_file():
            return frozenset()
        data = json.loads(p.read_text(encoding="utf-8"))
        adresses = data.get("adresses") if isinstance(data, dict) else None
    except (OSError, ValueError):
        return frozenset()
    return frozenset(c for c in (_cle_adresse(a) for a in adresses or () if isinstance(a, str)) if c)


def _refus_web_local(tool_name: str, tool_input: object, env: dict | None = None) -> str:
    """05/11 (lot 7B) : raison de refuser SANS CARTE un outil web du CLI local ("" : il passe au jugement habituel,
    `MCP_POLICY`). Sans le contrôle du vrai CLI local (`refused` le fait ; le banc l'appelle directement)."""
    outil = tool_name.rsplit("__", 1)[-1]
    if tool_name.startswith("mcp__web_local__"):
        if outil not in _WEB_LOCAL_OUTILS:
            return f"outil {outil} inconnu du serveur web du modèle local : refusé par le garde-fou d'Astrel."
        return ""
    if outil not in NAVIGATEUR_LOCAL_OUTILS:
        return (f"l'outil {outil} du navigateur n'est pas ouvert au modèle local : refusé par le garde-fou d'Astrel. "
                "Outils permis : " + ", ".join(NAVIGATEUR_LOCAL_OUTILS) + ".")
    if outil == "browser_navigate" and tool_input is not None:
        url = tool_input.get("url") if isinstance(tool_input, dict) else None
        cle = _cle_adresse(url)
        if not cle or cle not in _adresses_du_tour(env):
            return ("browser_navigate : adresse jamais vue dans ce tour, refusée par le garde-fou d'Astrel. Ouvre "
                    "une adresse écrite dans la demande ou rendue par recherche_web, recopiée telle quelle ; dans "
                    "le navigateur, clique sur les liens au lieu de taper leur adresse.")
    return ""


def refused(tool_name: str, tool_input: object = None) -> str:
    """Raison du refus d'office si `tool_name` est dans « refuser » ; sinon vide. 05/11 (lot 5) : outils du
    serveur `astrel_local` jugés sur leur ENTRÉE (`_refus_outil_local`) ; sans entrée : refusés.
    05/11 (lot 7B) : outils `web_local` et `playwright_local` refusés hors du VRAI CLI local (`_cli_local_reel`)
    quand le HOOK juge (entrée donnée ; le cœur, qui rejuge une carte sans l'entrée, ne voit pas ses ancêtres)."""
    if tool_name.startswith("mcp__astrel_local__"):
        why = _refus_outil_local(tool_name.rsplit("__", 1)[-1], tool_input)
        if why:
            return why
    if tool_name.startswith(_SERVEURS_WEB_LOCAL):
        if tool_input is not None and not _cli_local_reel():
            return (f"l'outil {tool_name} n'existe que pour le modèle local d'Astrel : refusé par le garde-fou. "
                    "N'insiste pas, propose une autre voie.")
        why = _refus_web_local(tool_name, tool_input)
        if why:
            return why
    if tool_name.startswith("mcp__") and tool_name.rsplit("__", 1)[-1] in REFUSED_MCP_TOOLS:
        # 04/11 (lot A, audit 2 T44-A) : en dur, quel que soit le fichier de politique (remplacé par un test,
        # illisible, ou d'une autre version) et quel que soit le serveur (`playwright`, `playwright2..4`…).
        return (f"l'outil {tool_name} exécute du code dans le serveur MCP lui-même : refusé d'office par le "
                "garde-fou d'Astrel. N'insiste pas, propose une autre voie.")
    for pattern in _tool_policy().get("refuser", ()):
        if tool_name == pattern or (pattern.endswith("*") and tool_name.startswith(pattern[:-1])):
            return (f"l'outil {tool_name} est refusé par la politique d'outils d'Astrel "
                    "(hooks/tool-policy.json) : n'insiste pas, propose une autre voie.")
    return ""


def _join(labels: list[str]) -> str:
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " et " + labels[-1]


def assess(tool_name: str, tool_input: dict, mode: str | None = None, cwd: str | None = None) -> Verdict:
    """Faut-il demander, compte tenu du mode et des autorisations en cours ?

    15/10 : chaque étiquette à confirmer (toutes en prudent, les critiques en
    autonome) doit être couverte par SA propre autorisation « 30 min » ; une
    seule étiquette sans clé (garde-fou) rend la commande non autorisable."""
    v = classify(tool_name, tool_input, cwd=cwd)
    if not v.needs_confirm:
        return v
    mode = mode or get_mode()
    items = v.items or ((v.key, v.level, v.desc),)
    relevant = [it for it in items if mode != "autonome" or it[1] == CRITICAL]
    if not relevant:
        return Verdict(False, v.desc, v.key, v.level)
    grantable = all(it[0] for it in relevant)
    keys = tuple(it[0] for it in relevant) if grantable else ()
    level = CRITICAL if any(it[1] == CRITICAL for it in relevant) else relevant[0][1]
    desc = _join([it[2] for it in relevant])
    lift = bool(v.lift) and mode == "autonome"  # 18/10 (plan P7) : jamais levé en prudent
    # 23/10 (plan P29) : catégories des dangers qui demandent à ce mode (critiques en autonome) ;
    # aucune trouvée alors qu'il faut demander : plafond (incohérent, jamais libre).
    cat_items = tuple(ci for ci in (v.cat_items or ((v.level, v.cats),)) if mode != "autonome" or ci[0] == CRITICAL)
    cats = _union_cats(cat_items) or (_L.PLAFOND,)
    if grantable and all(granted(k) for k in keys):
        return Verdict(False, desc, keys[0], level, keys=keys, guard=v.guard, items=tuple(relevant), lift=lift,
                       cats=cats, cat_items=cat_items)
    return Verdict(True, desc, keys[0] if keys else "", level, keys=keys, guard=v.guard, items=tuple(relevant),
                   lift=lift, cats=cats, cat_items=cat_items)


def short_command(tool_name: str, tool_input: dict, limit: int = 110) -> str:
    """Résumé lisible de la commande, affiché sous la question de confirmation."""
    cmd = tool_input.get("command") or tool_input.get("file_path") or tool_input.get("url") or ""
    if not cmd and tool_input:
        cmd = ", ".join(f"{k}={v}" for k, v in list(tool_input.items())[:3])
    cmd = re.sub(r"\s+", " ", str(cmd)).strip()
    return (cmd[:limit] + "…") if len(cmd) > limit else cmd


# « Oui pour 30 min » dit à voix haute : avant le 14/10, seul « oui à tout »
# ouvrait l'autorisation, et « oui pour trente minutes » ne valait qu'un oui.
ALWAYS_WORDS = ("oui à tout", "oui a tout", "oui pour tout", "à chaque fois", "a chaque fois",
                "toujours", "sans me demander", "plus besoin de demander", "vas-y pour tout",
                "pour 30", "pour trente", "30 min", "trente min", "demi-heure", "demi heure")


# 02/11 (relecture de la conversation anglaise, accord EXPLICITE de l'utilisateur, durcissement seulement) : les mots
# des réponses vivent ICI, dans le fichier protégé (avant : `config.YES_WORDS`/`NO_WORDS`, modifiables sans carte).
# - OUI : français partout ; anglais SEULEMENT si l'appelant le dit (conversation en anglais, `english=True`) ;
#   WhatsApp et le clavier n'en passent pas : seuls les oui français. Rien qui puisse cacher un refus (« do it »,
#   « go for it », « approve », « sure ») ; « yes » n'est plus un oui en français.
# - REFUS : les deux langues, partout, lus AVANT les oui ; toute négation (« pas », « jamais », n't…) l'emporte.
# - INDÉCIS (« je ne sais pas », « not sure ») : None, la question est reposée ; lus avant les refus.
# - « à chaque fois », « toujours » ne valent plus un oui à eux seuls (« demande-moi à chaque fois ») ; ils gardent
#   leur sens « 30 min » derrière un oui (`wants_always`).
# - L'apostrophe typographique (’ ‘ ʼ, clavier du téléphone, transcription) est ramenée à ' AVANT tout test.
YES_WORDS_FR = ("oui", "ouais", "vas-y", "vas y", "d'accord", "confirme", "ok", "valide")
YES_WORDS_EN = ("yes", "yeah", "yep", "okay", "go ahead", "confirmed")
NO_WORDS = (
    "non", "annule", "stop", "surtout pas", "laisse tomber", "pas", "jamais", "rien", "attends", "attendez",
    "plus tard",
    "no", "nope", "cancel", "not", "never", "never mind", "no way", "stop it", "cannot", "wait", "hold on",
    "hang on", "later", "don't", "won't", "wouldn't", "shouldn't", "didn't", "can't", "doesn't", "isn't",
    "aren't", "haven't", "mustn't",
)
UNDECIDED_WORDS = ("je ne sais pas", "je sais pas", "sais pas", "i don't know", "i do not know", "dunno",
                   "not sure")
_ALWAYS_ALONE = ("à chaque fois", "a chaque fois", "toujours")
# Tournures NEUTRES (accord du 02/11) : retirées AVANT de lire les refus, sinon « oui, pas de souci »
# était un refus à cause de « pas ». Elles ne valent jamais un oui : il faut un vrai mot de oui à côté
# (« pas de souci » seul → None) ; une négation ailleurs dans la phrase refuse toujours.
NEUTRAL_PHRASES = ("pas de soucis", "pas de souci", "pas de problèmes", "pas de problème", "pas de probleme",
                   "ça ne me dérange pas", "ca ne me derange pas", "ça me va", "ca me va", "pourquoi pas",
                   "no problem", "no worries", "not a problem")
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'"})
_NEGATION_NT = re.compile(r"\w+n't(?!\w)")  # 02/11 : règle générique, en plus de la liste


def decision_from_speech(text: str, english: bool = False) -> bool | None:
    """Interprète une réponse vocale. None = indécidable (et donc refus, en fin de compte).
    `english` : la conversation est en anglais, les oui anglais comptent aussi (refus : toujours les deux)."""
    low = str(text or "").lower().translate(_APOSTROPHES)
    t = " " + " ".join(re.sub(r"[^\wàâçéèêëîïôûùüÿœ' -]", " ", low).split()) + " "
    for w in NEUTRAL_PHRASES:
        t = t.replace(f" {w} ", " ")  # 02/11 : accord poli, ni oui ni refus ; un vrai oui reste exigé à côté
    for w in UNDECIDED_WORDS:
        if f" {w} " in t:
            return None
    for w in NO_WORDS:
        if f" {w} " in t:
            return False
    if _NEGATION_NT.search(t):
        return False  # 02/11 : toute négation anglaise en n't (« couldn't », « wasn't », « ain't »…)
    yes = YES_WORDS_FR + (YES_WORDS_EN if english else ()) + tuple(w for w in ALWAYS_WORDS if w not in _ALWAYS_ALONE)
    for w in yes:
        if f" {w} " in t:
            return True
    return None


def wants_always(text: str) -> bool:
    low = " " + text.lower() + " "
    return any(w in low for w in ALWAYS_WORDS)
