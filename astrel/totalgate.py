"""Qui a le droit d'activer « Autonome TOTAL », de desserrer le garde-fou ou de
lever l'arrêt d'urgence (15/10, docs/DURCISSEMENT-GARDE-FOU.md).

Sorti de `core.py` à la demande de l'utilisateur : `core.py` reste libre en écriture
(développement courant), CE fichier est protégé (`guard.PROTECTED_PATHS`) :
Claude ne peut le modifier qu'avec une confirmation, même en « Autonome TOTAL ».
Le cœur ne fait qu'APPELER ces fonctions. Aucune dépendance vers un module non
protégé (d'où le `_fold` recopié ici) : la décision ne doit pas pouvoir être
changée en retouchant un autre fichier.

Limite : un `core.py` modifié peut cesser d'appeler ces fonctions. La protection
empêche de retoucher la décision elle-même, pas de la contourner après relance.

23/10 (plan P29) : `total_grants` et `lift_grants` prennent les catégories de l'action
(`cats`, du hook) et les cases cochées (`choix`, réglage lu par le cœur) et appellent
`limites.decide` (protégé lui aussi). `cats` absent (ancien hook) : règles d'avant.
`may_change_limits` : qui peut changer les cases (l'utilisateur, depuis le HUD seulement).

24/10 (P29 lot B) : `confirmation_free` (cases de confirmation de Réglages › Garde-fou : toutes
les catégories décochées = pas de carte ; tout décoché = aucune carte, sauf ESTOP) et
`may_change_confirmations` (même règle que les limites : HUD non-Claude seulement).

04/11 (lot de sécurité B, H2, accord explicite de l'utilisateur) : EMPREINTE DU CŒUR. Les appels qui
refusent à Claude vivent dans du code libre (`core.py`, mixins…), chargé à la relance suivante.
`./run.sh doctor --accept` (l'utilisateur, son terminal) retient aussi l'empreinte de ce code (`CORE_FILES`,
clé `coeur` de `hooks/empreinte.json`, protégé). Au démarrage (`verifier_coeur`), l'empreinte du code
CHARGÉ est relevée ; tant qu'elle diffère de la référence (relue à chaque besoin), ou que celle-ci manque ou
est illisible : Total, levée et validation automatique des plans REFUSÉS (le cœur les demande ici, et
`total_grants`/`lift_grants` n'accordent plus rien) ; le reste marche. Limite : un
cœur réécrit peut cesser d'appeler ce fichier ; la manœuvre demande alors de réécrire aussi ses appels,
et `./run.sh doctor` la montre (ligne « empreinte du cœur »).
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

from . import limites

# 04/11 (H2) : chemins pris depuis CE fichier (pas `config.py`, libre) ; `hooks/` est protégé.
_ROOT = Path(__file__).resolve().parent.parent
EMPREINTE = _ROOT / "hooks" / "empreinte.json"
# Le code qui décide côté cœur (refus à Claude, droits, hooks déclarés, ce qui part au CLI) :
# - core.py et coeur/*.py : `_on_command`, confirmations, réglages, plans, mission, tâches ;
# - confirm.py (verdict recalculé), plans.py, mission.py, preuve.py (fin de mission = fin de la barrière),
#   levee.py (surveillance de la levée), attente.py (messages rejoués au nom de l'utilisateur, signature) ;
# - totalgate.py, state.py, server.py : qui est Claude, qui desserre (déjà protégés, comptés aussi ici) ;
# - fournisseurs.py, cle_api.py : variables retirées du cerveau, clé donnée au CLI ;
# - medias.py : ce que le cœur ouvre et montre (sources permises, secrets) ;
# - cli.py : `--send`, `doctor`, ouverture du HUD ; brain.py : hooks et réglages écrits pour le CLI ;
# - events.py : routage des commandes ; mcp_config.py : serveurs MCP déclarés ; config.py : chemins des
#   hooks, de l'ESTOP, des sockets (un chemin changé là change ce que tout le reste protège).
CORE_FILES = (
    "astrel/core.py", "astrel/coeur/*.py", "astrel/confirm.py", "astrel/plans.py", "astrel/mission.py",
    "astrel/preuve.py", "astrel/levee.py", "astrel/attente.py", "astrel/totalgate.py", "astrel/state.py",
    "astrel/server.py", "astrel/fournisseurs.py", "astrel/cle_api.py", "astrel/medias.py", "astrel/cli.py",
    "astrel/brain.py", "astrel/events.py", "astrel/mcp_config.py", "astrel/config.py",
)
REFUS_COEUR = "code du cœur modifié depuis la dernière validation : ./run.sh doctor --accept dans ton terminal"


def core_files() -> list[Path]:
    """Fichiers de `CORE_FILES` (motifs développés, triés) : un module ajouté dans coeur/ compte aussi."""
    out: list[Path] = []
    for rel in CORE_FILES:
        if "*" in rel:
            out.extend(sorted(_ROOT.glob(rel)))
        else:
            out.append(_ROOT / rel)
    return out


def core_fingerprint() -> dict[str, str]:
    """sha256 de chaque fichier du cœur, par chemin relatif en « / » (« absent » s'il manque)."""
    out: dict[str, str] = {}
    for path in core_files():
        rel = path.relative_to(_ROOT).as_posix()
        try:
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            out[rel] = "absent"
    return out


def core_changes(known=None) -> list[str] | None:
    """Fichiers du cœur différents de l'empreinte validée (`known`, sinon celle de `EMPREINTE`) ;
    None : aucune empreinte du cœur validée, ou illisible."""
    if known is None:
        try:
            known = json.loads(EMPREINTE.read_text(encoding="utf-8")).get("coeur")
        except (OSError, ValueError, AttributeError):
            return None
    if not isinstance(known, dict) or not known:
        return None
    now = core_fingerprint()
    return sorted(f for f in set(now) | set(known) if now.get(f) != known.get(f))


# Empreinte du code CHARGÉ (le disque au démarrage du cœur) ; None : pas encore relevée (relevée au premier
# besoin : un cœur qui n'appellerait pas `verifier_coeur` n'y échappe pas).
_au_demarrage: dict[str, str] | None = None
# Raison imposée (tests : "" = cœur tenu pour validé) ; None : calculée à chaque fois.
_coeur: str | None = None


def verifier_coeur() -> str:
    """Au démarrage du cœur : relève l'empreinte du code chargé, et la compare à celle validée."""
    global _au_demarrage
    try:
        _au_demarrage = core_fingerprint()
    except Exception:  # noqa: BLE001 — dans le doute, refuser
        _au_demarrage = {}
    return coeur_refus()


def coeur_refus() -> str:
    """Vide si le code chargé est celui validé par l'utilisateur, sinon la raison (Total, levée et plans
    automatiques refusés). La référence est RELUE à chaque fois : un `doctor --accept` fait sans rien changer
    au disque depuis le démarrage vaut tout de suite, sans relance ; le code chargé, lui, reste celui du
    démarrage (un fichier modifié depuis n'est pas ce qui tourne)."""
    global _au_demarrage
    if _coeur is not None:
        return _coeur
    try:
        if _au_demarrage is None:
            _au_demarrage = core_fingerprint()
        known = json.loads(EMPREINTE.read_text(encoding="utf-8")).get("coeur")
    except Exception:  # noqa: BLE001 — illisible : refuser
        return REFUS_COEUR
    return "" if isinstance(known, dict) and known and known == _au_demarrage else REFUS_COEUR

ON_WORDS = ("on", "oui", "1", "true", "actif", "active", "activer")
OFF_WORDS = ("off", "non", "0", "false", "inactif", "desactive", "désactive", "desactiver", "désactiver")
ESTOP_NAMES = ("estop", "stop-tout", "stoptout", "stop_tout")
ESTOP_OFF_WORDS = ("off", "non", "lever", "leve", "levé", "0", "false")
_MISSION_TIGHTEN = ("", "status", "pause", "stop", "arret", "arrêt", "arrete", "arrête", "fin")


def _arg(cmd: dict) -> str:
    return str(cmd.get("mode") or cmd.get("action") or cmd.get("text") or "").strip().lower()


def mission_action(cmd: dict) -> str:
    """L'action d'une commande `mission`, lue d'UNE seule façon par ce contrôle ET par
    le cœur (15/10) : avant, `{"mode": "pause", "action": "start"}` passait ici pour
    une pause et le cœur la lançait. Vide = état."""
    return str(cmd.get("action") or cmd.get("mode") or cmd.get("text") or "").strip().lower()


def loosens_guard(name: str, cmd: dict) -> bool:
    """Commande qui desserrerait le garde-fou : réservée à l'utilisateur (14/10)."""
    arg = _arg(cmd)
    if name in ESTOP_NAMES:
        return arg in ESTOP_OFF_WORDS
    if name == "autonomy":
        return bool(arg)
    if name == "mission":
        # Lancer, reprendre, ou toute action inconnue : à l'utilisateur seul. Pause, arrêt, état : permis.
        return mission_action(cmd) not in _MISSION_TIGHTEN
    if name == "total":
        return arg in ON_WORDS
    return name == "confirm"


def refused_to_claude(name: str, cmd: dict) -> bool:
    """La commande vient de Claude (marquée par le canal de contrôle ou le HUD)
    ET desserrerait le garde-fou : le cœur doit la refuser."""
    return cmd.get("_from_claude") is True and loosens_guard(name, cmd)


REFUS_SANS_CONFINEMENT = ("sans service (cgroup ou Job Object), ce qui desserre le garde-fou ne passe que par le HUD : "
                          "réponds ou agis dans le HUD")


def refused_unconfined(name: str, cmd: dict) -> bool:
    """04/11 (lot de sécurité B, H3, accord de l'utilisateur) : sur le canal de contrôle (`_via` « socket »), sans
    confinement de l'arbre de Claude (`_confine`, écrit par `state.ControlServer` : cgroup du service sous
    Linux, Job Object sous Windows), un process détaché de Claude passe pour l'utilisateur. Ce qui desserrerait
    le garde-fou (`loosens_guard`) n'y passe donc plus : le HUD non-Claude seulement. Un « non » à une
    confirmation resserre : permis (la lecture de `allow` est celle du cœur, `bool`)."""
    if cmd.get("_via") != "socket" or cmd.get("_confine") is True or not loosens_guard(name, cmd):
        return False
    return not (name == "confirm" and not bool(cmd.get("allow")))


def estop_wanted(cmd: dict) -> bool:
    """`estop` sans argument POSE l'arrêt d'urgence ; « off », « lever »… le lèvent."""
    arg = str(cmd.get("mode") or cmd.get("text") or "on").strip().lower()
    return arg not in ESTOP_OFF_WORDS


def total_request(cmd: dict) -> str:
    """Ce que demande une commande `total` : "" (état), "on", "off" ou "?" (illisible)."""
    arg = str(cmd.get("mode") or cmd.get("text") or cmd.get("on") or "").strip().lower()
    if cmd.get("on") is True:
        arg = "on"
    elif cmd.get("on") is False:
        arg = "off"
    if not arg:
        return ""
    if arg in OFF_WORDS:
        return "off"  # resserrer : toujours permis
    if arg in ON_WORDS:
        return "on"
    return "?"


def may_enable_total(cmd: dict) -> bool:
    """Activer Total : UNIQUEMENT l'interrupteur du HUD, sur une connexion qui ne
    vient pas de Claude (`_via` et `_from_claude` sont écrits par le serveur du
    HUD, jamais par le client). La voix passe par `total_phrase`."""
    return cmd.get("_via") == "hud" and cmd.get("_from_claude") is False


def _choix(colonne: str, choix) -> dict:
    """Cases de la colonne : défauts si rien n'est donné ; ids d'une autre colonne ignorés."""
    if choix is None:
        return limites.defauts(colonne)
    if not isinstance(choix, dict):
        return limites.tout_demande(colonne)
    base = limites.defauts(colonne)
    return {k: choix.get(k) is True for k in base}


def total_grants(total: bool, guard: bool, estop: bool, cats=None, choix=None, non_claude: bool = False) -> bool:
    """Total accorde-t-il cette action sans question ? Jamais pendant un arrêt d'urgence,
    jamais avec un fournisseur non-Claude.
    `cats` None (ancien hook) : comme avant le 23/10, jamais pour le garde-fou lui-même
    (`Verdict.guard`). Sinon (plan P29) : seulement si TOUTES les catégories sont cochées dans
    la colonne Total (`choix`, défauts si None) ; le plafond et le code du garde-fou jamais.
    Une action « garde-fou » dont aucune catégorie n'est une famille de fichiers : incohérent,
    demandé.
    04/11 (H2) : jamais si le code du cœur n'est pas celui validé (`coeur_refus`)."""
    if not total or estop or non_claude or coeur_refus():
        return False
    if cats is None:
        return not guard
    if isinstance(cats, str):
        return False
    if guard and not any(c in limites.FAMILLES for c in cats):
        return False
    return limites.decide(cats, _choix(limites.TOTAL, choix), estop, non_claude, limites.TOTAL)


# --- Levée du garde-fou (18/10, plan P7, demande de l'utilisateur) ---
# Un simple interrupteur, sans durée : levée, une ÉCRITURE d'un fichier levable
# (`guard.LIFTABLE`, `Verdict.lift`) passe sans question. En mémoire, éteinte au
# démarrage, coupée par « Stop tout ». Claude ne peut ni la lever ni la rétablir.

def may_enable_lift(cmd: dict) -> bool:
    """Lever : UNIQUEMENT le bouton des Réglages du HUD, sur une connexion qui ne vient
    pas de Claude (`_via`/`_from_claude` écrits par le serveur du HUD). Ni la voix, ni
    le terminal, ni le canal de contrôle."""
    return cmd.get("_via") == "hud" and cmd.get("_from_claude") is False


def may_disable_lift(cmd: dict) -> bool:
    """Rétablir : l'utilisateur seul (HUD ou terminal), jamais Claude, même pour resserrer
    (décision de l'utilisateur). « Stop tout » la coupe aussi, par le cœur."""
    return cmd.get("_from_claude") is False


def lift_grants(active: bool, lift: bool, estop: bool, cats=None, choix=None, non_claude: bool = False) -> bool:
    """La levée accorde-t-elle cette action sans question ? Seulement si elle est
    active, que TOUT ce qui est critique dans l'action est levable (`Verdict.lift`,
    faux hors mode autonome), et jamais pendant un arrêt d'urgence.
    23/10 (plan P29) : ET, si `cats` est donné, toutes ses catégories cochées dans la colonne
    levée (`choix`, défauts si None) : les cases ne peuvent que resserrer la levée ; jamais
    avec un fournisseur non-Claude. 04/11 (H2) : jamais si le code du cœur n'est pas celui validé."""
    if not (active is True and lift is True and not estop) or non_claude or coeur_refus():
        return False
    if cats is None:
        return True
    if isinstance(cats, str):
        return False
    return limites.decide(cats, _choix(limites.LEVEE, choix), estop, non_claude, limites.LEVEE)


def may_change_limits(cmd: dict) -> bool:
    """Changer les cases des limites (Total ou levée) : UNIQUEMENT les Réglages du HUD, sur
    une connexion qui ne vient pas de Claude (`_via`/`_from_claude` écrits par le serveur du
    HUD). Ni le terminal (`--send`), ni le canal de contrôle, ni la voix (plan P29, question 4)."""
    return cmd.get("_via") == "hud" and cmd.get("_from_claude") is False


def confirmation_free(cats, coches, estop: bool, non_claude: bool = False) -> bool:
    """24/10 (lot B, demande de l'utilisateur) : l'action passe-t-elle SANS carte parce que toutes ses
    catégories sont décochées dans Réglages › Garde-fou ? Décision dans `limites.sans_carte`
    (protégé) : tout décoché = aucune carte ; jamais sous ESTOP ni avec un fournisseur non-Claude.
    Avant toute autre règle (Total, levée, « oui pour 30 min »), qui ne servent que si une case
    concernée est cochée."""
    return limites.sans_carte(cats, coches, estop, non_claude)


def may_change_confirmations(cmd: dict) -> bool:
    """Changer les cases de confirmation : l'utilisateur, depuis les Réglages du HUD, sur une connexion
    qui ne vient pas de Claude. Ni Claude, ni un agent, ni le MCP, ni la voix, ni le terminal."""
    return may_change_limits(cmd)


def limits_change_refused(colonne: str, avant: dict, apres: dict, total: bool, lift: bool) -> str:
    """Raison du refus d'un changement de cases, vide s'il est permis. Élargir (rendre libre
    une case qui ne l'était pas) est refusé tant que Total ou la levée est active ; resserrer
    est toujours permis (plan P29, question 3). Colonne inconnue : refusé."""
    if colonne not in limites.COLONNES:
        return "colonne de limites inconnue"
    wider = limites.elargit(colonne, _choix(colonne, avant), _choix(colonne, apres))
    if wider and (total or lift):
        return "élargir les limites est impossible tant que le mode sans confirmation ou la levée est active"
    return ""


# Mode « Autonome TOTAL » dit à voix haute : toute la phrase, 8 mots au plus. Le nom devant ou derrière
# est ignoré : « astrel » (mot d'éveil depuis P36, oublié jusqu'au 29/10) et « astrel » (alias).
_TOTAL_ON = re.compile(r"(?:(?:ok|bon|alors|hey|astrel|jarvis|passe|active|mets[- ]toi|met[- ]toi)\s+)*"
                       r"(?:en |le )?(?:mode )?(?:autonome|autonomie) totale?|(?:en |le )?mode total")
_TOTAL_OFF = re.compile(r"(?:(?:ok|bon|alors|hey|astrel|jarvis)\s+)*"
                        r"(?:fin (?:du |de l')?(?:mode )?(?:autonome |autonomie )?total(?:e)?"
                        r"|(?:arrete|stop|desactive|coupe|quitte|sors du|sort du) (?:le |l')?(?:mode )?"
                        r"(?:autonome |autonomie )?total(?:e)?)")
_TAIL = re.compile(r"(?:\s+(?:astrel|jarvis|stp|svp|merci|maintenant|s'il te plait|s'il vous plait))*$")


def _fold(text: str) -> str:
    s = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(c for c in s if not unicodedata.combining(c)).replace("’", "'")


def total_phrase(text: str) -> str:
    """« Mode autonome total » → "on", « fin du mode total » → "off", vide sinon
    (la phrase part alors chez Claude). Reconnue AVANT l'envoi à Claude, sur ce
    que l'utilisateur a DIT (jamais tapé, jamais venu de Claude)."""
    t = re.sub(r"[^\w' -]", " ", _fold(text))
    t = re.sub(r"\s+", " ", t).strip()
    if not t or len(t.split()) > 8:
        return ""
    t = _TAIL.sub("", t).strip()
    if _TOTAL_OFF.fullmatch(t):
        return "off"
    if _TOTAL_ON.fullmatch(t):
        return "on"
    return ""
