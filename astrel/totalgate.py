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
"""
from __future__ import annotations

import re
import unicodedata

from . import limites

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
    demandé."""
    if not total or estop or non_claude:
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
    avec un fournisseur non-Claude."""
    if not (active is True and lift is True and not estop) or non_claude:
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
