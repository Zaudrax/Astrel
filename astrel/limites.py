"""Limites d'« Autonome TOTAL » et de la levée du garde-fou, par cases à cocher (23/10, plan P29).

Demande de l'utilisateur : décider dans les Réglages QUELLES catégories d'actions sont libres
en Total (colonne `total`) ou levées pendant une levée du garde-fou (colonne `levee`), au
lieu de deux interrupteurs tout-ou-rien. Une case cochée = « libre sans question ».

Défauts = comportement d'avant le 23/10 (question 1 du plan, réponse de l'utilisateur : oui) :
- Total : tout ce qui n'est pas le garde-fou est libre ; les fichiers du HUD, de la bulle et
  de WhatsApp sont des cases DÉCOCHÉES (Total les demandait déjà) ; le code du garde-fou
  (guard, confirm, hooks) n'est PAS dans la colonne Total (question 2 : jamais en Total) ;
- levée : seules les familles de fichiers levables, cochées (la levée ne libérait qu'elles).

24/10 (lot B) : voir « Confirmations par cases à cocher » en bas : TOUT est décochable (demande
de l'utilisateur) ; le plafond ci-dessous ne vaut plus que pour les colonnes Total et levée.

PLAFOND : ce qui n'est jamais libre, dans aucune colonne et quoi que dise le réglage
(identifiants de Claude, secrets, journaux, ESTOP, autonomie, plans, réglages et fichier des
limites, code qui dit qui est Claude, empreinte, politique d'outils). Le garde-fou marque ces
étiquettes `PLAFOND` (`guard.Verdict.guard` vrai sans famille) ; un id inconnu vaut plafond.

Module PUR, sans dépendance (comme `totalgate`) et protégé (`guard.PROTECTED_CODE`) : la
décision ne doit pas pouvoir changer en retouchant un autre fichier. Appelé par `totalgate`
(décision) et par le garde-fou (ids des catégories). Le réglage lui-même (`limites_total`,
`limites_levee` de runtime/settings.json, fichier protégé) est lu par le cœur (lot B) : seules
les DIFFÉRENCES au défaut y sont écrites, d'où `choix_depuis`.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets as _secrets
from dataclasses import dataclass

TOTAL = "total"
LEVEE = "levee"
COLONNES = (TOTAL, LEVEE)

MOYENNE = "moyenne"
GRAVE = "grave"
TRES_GRAVE = "très grave"

PLAFOND = "plafond"

# Ids des catégories (le garde-fou les importe ; ne pas les renommer : le réglage les garde).
ADMINISTRATION = "administration"
EXTINCTION = "extinction"
DISQUES = "disques"
SUPPRESSIONS = "suppressions"
GIT = "git"
GIT_COURANT = "git_courant"
ENVOIS = "envois"
PAIEMENTS = "paiements"
DEPLOIEMENTS = "deploiements"
PARTAGE = "partage"
EXTERIEUR = "exterieur"
TACHES = "taches_planifiees"
ECRITURE = "ecriture_hors_projets"
REMISE_A_ZERO = "remise_a_zero"
MISSION = "mission"
OUTILS = "outils"
DOUTES = "doutes"
# Familles de fichiers protégés (écriture ou édition seulement : une suppression est plafond).
FICHIERS_HUD = "fichiers_hud"
FICHIERS_BULLE = "fichiers_bulle"
FICHIERS_WHATSAPP = "fichiers_whatsapp"
CODE_GARDE_FOU = "code_garde_fou"


@dataclass(frozen=True)
class Categorie:
    id: str
    texte: str
    gravite: str
    # colonne → défaut (True = libre sans question). Absente : jamais libre dans cette colonne.
    defauts: tuple[tuple[str, bool], ...]

    @property
    def colonnes(self) -> tuple[str, ...]:
        return tuple(c for c, _d in self.defauts)


def _t(defaut: bool = True) -> tuple[tuple[str, bool], ...]:
    return ((TOTAL, defaut),)


CATALOGUE: tuple[Categorie, ...] = (
    Categorie(ADMINISTRATION, "Commandes administrateur (sudo), permissions, paquets, services, arrêt forcé de "
              "processus", GRAVE, _t()),
    Categorie(EXTINCTION, "Extinction, redémarrage, session, réseau", GRAVE, _t()),
    Categorie(DISQUES, "Opérations directes sur les disques", TRES_GRAVE, _t()),
    Categorie(SUPPRESSIONS, "Suppressions récursives, Docker, suppressions par les connecteurs (mails, étiquettes)",
              GRAVE, _t()),
    Categorie(GIT, "git push et opérations git qui effacent du travail", GRAVE, _t()),
    Categorie(GIT_COURANT, "Commit, fusion, rebase (mode prudent seulement)", MOYENNE, _t()),
    Categorie(ENVOIS, "Envois : mail, message, réponse, publication d'un post", GRAVE, _t()),
    Categorie(PAIEMENTS, "Paiements, achats, remboursements", TRES_GRAVE, _t()),
    Categorie(DEPLOIEMENTS, "Déploiements, publication sur GitHub ou d'un paquet", GRAVE, _t()),
    Categorie(PARTAGE, "Partage, invitations, abonnements", MOYENNE, _t()),
    Categorie(EXTERIEUR, "Code ou machine extérieurs (script téléchargé, ssh)", GRAVE, _t()),
    Categorie(TACHES, "Tâches planifiées, réveils, réflexes", MOYENNE, _t()),
    Categorie(ECRITURE, "Écriture hors de tes projets ou dans les dossiers système", GRAVE, _t()),
    Categorie(REMISE_A_ZERO, "Remise à zéro des réglages", MOYENNE, _t()),
    Categorie(MISSION, "Écriture hors du périmètre d'une mission", MOYENNE, _t()),
    Categorie(OUTILS, "Outils de connecteurs, du navigateur et outils natifs (lire, ranger, remplir)", MOYENNE,
              _t()),
    Categorie(DOUTES, "Commandes que le garde-fou ne sait pas analyser, outils inconnus", GRAVE, _t()),
    # Familles : Total décoché, levée cochée (comme avant le 23/10).
    Categorie(FICHIERS_HUD, "Fichiers du HUD (hud/src, hud/dist, hud/index.html)", GRAVE,
              ((TOTAL, False), (LEVEE, True))),
    Categorie(FICHIERS_BULLE, "Fichiers de la bulle (bubble.py, minichat.py)", GRAVE,
              ((TOTAL, False), (LEVEE, True))),
    Categorie(FICHIERS_WHATSAPP, "Fichiers de WhatsApp (whatsapp.py, whatsapp/)", GRAVE,
              ((TOTAL, False), (LEVEE, True))),
    # Question 2 du plan : levée SEULE (un hook réécrit agit aussitôt et dure).
    Categorie(CODE_GARDE_FOU, "Code du garde-fou (guard.py, confirm.py, hooks)", TRES_GRAVE, ((LEVEE, True),)),
)

PAR_ID: dict[str, Categorie] = {c.id: c for c in CATALOGUE}
FAMILLES = frozenset({FICHIERS_HUD, FICHIERS_BULLE, FICHIERS_WHATSAPP, CODE_GARDE_FOU})

# 24/10 (lot B, demande de l'utilisateur) : le PLAFOND est découpé en catégories, chacune avec sa
# case de confirmation (voir « Confirmations par cases à cocher » plus bas). Le garde-fou les
# pose À CÔTÉ du marqueur `PLAFOND` : Total et la levée ne les accordent toujours jamais
# (absentes de `CATALOGUE`, donc refusées par `decide`) ; seules les cases peuvent les libérer.
IDENTIFIANTS_CLAUDE = "identifiants_claude"
SECRETS = "secrets"
SECRETS_LARGES = "secrets_larges"
JOURNAUX = "journaux"
ETAT_GARDE_FOU = "etat_garde_fou"
CODE_IDENTITE = "code_identite"
CITATION = "citation_garde_fou"
MISSION_INTERDIT = "mission_interdit"
# 04/11 (secrets du compte, autorisation de l'utilisateur) : clés SSH et GPG, jetons du cloud, mots de passe et cookies
# des navigateurs, trousseaux (`guard.SecretsCompte`). Lus : plafond, comme un secret d'Astrel.
SECRETS_UTILISATEUR = "secrets_utilisateur"
# 05/11 (trou confirmé, autorisation explicite de l'utilisateur) : un AUTRE agent IA lancé par Claude (claude -p, codex,
# gemini, aider…) ne charge pas le hook d'Astrel : il agirait sans garde-fou. Plafond (ni Total, ni levée, ni
# « oui pour 30 min ») ; sa case existe et reste cochée par défaut, comme toutes (décision du 24/10, lot B).
AGENT_IMBRIQUE = "agent_imbrique"
PLAFONDS = frozenset({IDENTIFIANTS_CLAUDE, SECRETS, SECRETS_LARGES, JOURNAUX, ETAT_GARDE_FOU, CODE_IDENTITE,
                      CITATION, MISSION_INTERDIT, SECRETS_UTILISATEUR, AGENT_IMBRIQUE})

# Ligne d'information du plafond (lecture seule dans les Réglages).
PLAFOND_TEXTES = (
    "identifiants et serveurs MCP de Claude Code",
    "secrets (jeton du HUD, clés API, identifiants WhatsApp)",
    "secrets de ton compte (clés SSH, mots de passe, jetons, profils des navigateurs)",  # 04/11
    "lancement d'un autre agent IA sans le garde-fou (claude, codex, gemini…)",  # 05/11
    "journaux (audit, Sans confirmation, levée)",
    "arrêt d'urgence, autonomie, autorisations, mission, plans, réglages et limites",
    "code qui dit qui est Claude (totalgate, state, ipc, proc, server, limites)",
    "empreinte du garde-fou et politique d'outils",
    "suppression d'un fichier du garde-fou, du HUD, de la bulle ou de WhatsApp",
)


def categories(colonne: str) -> tuple[Categorie, ...]:
    """Catégories qui ont une case dans cette colonne, dans l'ordre du catalogue."""
    return tuple(c for c in CATALOGUE if colonne in c.colonnes)


def defauts(colonne: str) -> dict[str, bool]:
    """{id: libre} au défaut (= comportement d'avant le 23/10) ; colonne inconnue : vide."""
    return {c.id: dict(c.defauts)[colonne] for c in categories(colonne)}


def tout_demande(colonne: str) -> dict[str, bool]:
    """Toutes les cases décochées : réglage illisible au premier chargement (plan P29, risques)."""
    return {c.id: False for c in categories(colonne)}


def choix_depuis(colonne: str, differences) -> tuple[dict[str, bool], bool]:
    """(choix complet de la colonne, lisible ?) depuis les différences au défaut écrites
    dans le réglage. Ids inconnus ou disparus : oubliés (sans rendre le tout illisible).
    Valeur qui n'est pas un booléen, ou pas un objet : illisible (`False`), et le choix
    rendu est alors `tout_demande` : à l'appelant de garder plutôt ses dernières valeurs
    valides s'il en a."""
    if differences is None:
        return defauts(colonne), True
    if not isinstance(differences, dict):
        return tout_demande(colonne), False
    choix = defauts(colonne)
    for k, v in differences.items():
        if k not in choix:
            continue  # id disparu ou d'une autre colonne : oublié
        if not isinstance(v, bool):
            return tout_demande(colonne), False
        choix[k] = v
    return choix, True


def differences(colonne: str, choix: dict) -> dict[str, bool]:
    """Ce qu'il faut écrire dans le réglage : seulement ce qui diffère du défaut."""
    base = defauts(colonne)
    return {k: v for k, v in choix.items() if k in base and isinstance(v, bool) and v != base[k]}


def elargit(colonne: str, avant: dict, apres: dict) -> list[str]:
    """Ids qu'un changement rend libres alors qu'ils ne l'étaient pas (vide : il resserre ou
    ne change rien). Question 3 du plan : élargir est refusé tant que Total ou la levée est
    active ; resserrer est toujours permis."""
    base = defauts(colonne)
    return [k for k in base if apres.get(k) is True and avant.get(k) is not True]


def decide(cats, choix, estop: bool, non_claude: bool, colonne: str | None = None) -> bool:
    """L'action est-elle libre sans question ? Seulement si TOUTES ses catégories sont des
    cases cochées de `choix` (logique de la levée, plan P7) ; jamais sous ESTOP, jamais avec
    un fournisseur non-Claude, jamais pour le plafond, un id inconnu, ou une action sans
    catégorie (incohérent : demandé). `colonne` donnée : une catégorie absente de cette
    colonne (le code du garde-fou en Total) n'est jamais libre, même cochée par erreur."""
    if estop or non_claude or not isinstance(choix, dict):
        return False
    if isinstance(cats, str) or not cats:
        return False
    try:
        ids = list(cats)
    except TypeError:
        return False
    for c in ids:
        if not isinstance(c, str) or c == PLAFOND or c not in PAR_ID or choix.get(c) is not True:
            return False
        if colonne is not None and colonne not in PAR_ID[c].colonnes:
            return False
    return True


# =====================================================================================
# 24/10 (lot B) : CONFIRMATIONS PAR CASES À COCHER. Demande de l'utilisateur (remplace le
# « plafond non décochable » du plan) : « sur VS Code il n'y a pas de confirmation en mode
# auto ; je dois pouvoir choisir via l'interface de désactiver ça avec des cases à cocher.
# Tout doit être décochable : si je décoche tout, plus aucune demande de confirmation pour
# rien. Cochées par défaut pour les choses sensibles. »
#
# Une case COCHÉE = la carte de confirmation est montrée (règles d'avant : mode, Total,
# levée, « oui pour 30 min ») ; DÉCOCHÉE = l'action passe sans carte. Une action n'est libre
# que si TOUTES ses catégories sont décochées. Tout décoché = aucune carte, quoi que dise le
# garde-fou (catégorie inconnue, marqueur seul, ancien hook sans catégories compris).
# Jamais sous l'arrêt d'urgence (« Stop tout » coupe, ce n'est pas une case), jamais avec un
# fournisseur non-Claude (plan P11 : ni Total ni levée pour eux, les cases non plus).
# Le réglage ne change que par l'utilisateur depuis le HUD (`totalgate.may_change_limits`) ;
# fichier absent, illisible, mal signé ou aux types invalides = défauts (sensible coché).
# =====================================================================================

FICHIER_CASES = "confirmations.json"      # dans runtime/ (protégé par le garde-fou)
FICHIER_CLE = "confirmations-cle"         # dans runtime/ : clé HMAC, 0600, secret
JOURNAL_CASES = "confirmations.log"       # dans logs/ : journal en ajout seul


@dataclass(frozen=True)
class Case:
    id: str
    groupe: str
    texte: str
    coche: bool  # défaut : True = carte montrée


GROUPES_CASES: tuple[tuple[str, str], ...] = (
    ("garde_fou", "Garde-fou et Claude Code"),
    ("secrets", "Secrets"),
    ("fichiers", "Fichiers d'Astrel"),
    ("systeme", "Système"),
    ("git", "Suppressions et git"),
    ("exterieur", "Envois, paiements et extérieur"),
    ("autres", "Outils, doutes et mission"),
)

# Défauts : TOUT coché (25/10, fiche 8 du testeur ; décision du 24/10 : le sensible
# coché par défaut, tout coché = comme avant le lot B). « Citation » et « recherche récursive »
# étaient décochées (sources de cartes « pour rien » du 23-24/10) mais elles relâchaient des
# cartes d'avant : « citation » est ce que le garde-fou n'a PAS prouvé en lecture seule (awk,
# perl -e, openssl -out, chmod 666 vers autonomy.json ou audit.jsonl n'ont pas de signe
# d'écriture reconnu), une recherche récursive sort le contenu de secrets.json ou hud-token.
# L'utilisateur peut les décocher (Réglages › Garde-fou) : ses choix priment sur ces défauts.
CASES: tuple[Case, ...] = (
    Case(CODE_GARDE_FOU, "garde_fou", "Modifier le code du garde-fou (guard.py, confirm.py, hooks)", True),
    Case(CODE_IDENTITE, "garde_fou", "Modifier le code qui décide (totalgate, limites, state, ipc, proc, server, "
                                     "venv, empreinte, politique d'outils)", True),
    Case(ETAT_GARDE_FOU, "garde_fou", "Modifier l'état du garde-fou (arrêt d'urgence, autonomie, autorisations, "
                                      "mission, plans, réglages)", True),
    Case(JOURNAUX, "garde_fou", "Modifier un journal (audit, sans confirmation, levée, cases)", True),
    Case(CITATION, "garde_fou", "Commande qui cite le garde-fou ou un journal sans signe d'écriture", True),
    Case(IDENTIFIANTS_CLAUDE, "garde_fou", "Compte, réglages et serveurs MCP de Claude Code", True),
    Case(AGENT_IMBRIQUE, "garde_fou", "Lancer un autre agent IA, qui agirait sans le garde-fou (claude -p, codex, "
                                      "gemini, aider…)", True),  # 05/11
    Case(SECRETS, "secrets", "Lire ou modifier un secret nommé (jeton du HUD, clés API, WhatsApp, clé qui signe ces cases)", True),
    Case(SECRETS_LARGES, "secrets", "Recherche récursive dans un dossier qui contient un secret", True),
    Case(SECRETS_UTILISATEUR, "secrets", "Lire un secret de ton compte (clés SSH et GPG, jetons du cloud, mots de "
                                         "passe, profils des navigateurs)", True),  # 04/11
    Case(FICHIERS_HUD, "fichiers", "Fichiers du HUD (hud/src, hud/dist, hud/index.html)", True),
    Case(FICHIERS_BULLE, "fichiers", "Fichiers de la bulle (bubble.py, minichat.py)", True),
    Case(FICHIERS_WHATSAPP, "fichiers", "Fichiers de WhatsApp (whatsapp.py, whatsapp/)", True),
    Case(ADMINISTRATION, "systeme", "Commandes administrateur (sudo), permissions, paquets, services", True),
    Case(EXTINCTION, "systeme", "Extinction, redémarrage, session, réseau", True),
    Case(DISQUES, "systeme", "Opérations directes sur les disques", True),
    Case(ECRITURE, "systeme", "Écriture hors de tes projets ou dans les dossiers système", True),
    Case(TACHES, "systeme", "Tâches planifiées, réveils, réflexes", True),
    Case(REMISE_A_ZERO, "systeme", "Remise à zéro des réglages", True),
    Case(SUPPRESSIONS, "git", "Suppressions récursives, Docker, suppressions par les connecteurs", True),
    Case(GIT, "git", "git push et opérations git qui effacent du travail", True),
    Case(GIT_COURANT, "git", "Commit, fusion, rebase (mode prudent seulement)", True),
    Case(ENVOIS, "exterieur", "Envois : mail, message, réponse, publication d'un post", True),
    Case(PAIEMENTS, "exterieur", "Paiements, achats, remboursements", True),
    Case(DEPLOIEMENTS, "exterieur", "Déploiements, publication sur GitHub ou d'un paquet", True),
    Case(PARTAGE, "exterieur", "Partage, invitations, abonnements", True),
    Case(EXTERIEUR, "exterieur", "Code ou machine extérieurs (script téléchargé, ssh)", True),
    Case(OUTILS, "autres", "Outils de connecteurs et du navigateur (mode prudent seulement)", True),
    Case(DOUTES, "autres", "Commandes que le garde-fou ne sait pas analyser, outils inconnus", True),
    Case(MISSION, "autres", "Écriture hors du périmètre d'une mission", True),
    Case(MISSION_INTERDIT, "autres", "Écriture dans un dossier interdit à la mission", True),
)
CASES_PAR_ID: dict[str, Case] = {c.id: c for c in CASES}


def cases_du_groupe(groupe: str) -> tuple[Case, ...]:
    return tuple(c for c in CASES if c.groupe == groupe)


def coches_defaut() -> dict[str, bool]:
    """{id: coché} au défaut (sensible coché)."""
    return {c.id: c.coche for c in CASES}


def coches_toutes(valeur: bool) -> dict[str, bool]:
    """« Tout cocher » (True) ou « Tout décocher » (False)."""
    return dict.fromkeys(CASES_PAR_ID, bool(valeur))


def coches_depuis(raw) -> tuple[dict[str, bool], bool]:
    """(cases complètes, lisible ?) depuis l'objet `coches` du fichier. Pas un objet, ou une
    valeur qui n'est pas un booléen : défauts ENTIERS (sûrs) et `False`. Id inconnu ou
    disparu : oublié ; id absent : sa valeur par défaut."""
    base = coches_defaut()
    if not isinstance(raw, dict):
        return base, False
    out = dict(base)
    for k, v in raw.items():
        if not isinstance(v, bool):
            return base, False
        if k in out:
            out[k] = v
    return out, True


def _normales(coches) -> dict[str, bool] | None:
    if not isinstance(coches, dict):
        return None
    if any(not isinstance(coches.get(k, c.coche), bool) for k, c in CASES_PAR_ID.items()):
        return None
    return {k: coches.get(k, c.coche) for k, c in CASES_PAR_ID.items()}


def tout_decoche(coches) -> bool:
    n = _normales(coches)
    return n is not None and not any(n.values())


def sans_carte(cats, coches, estop: bool, non_claude: bool) -> bool:
    """L'action passe-t-elle SANS carte ? Oui si toutes ses catégories sont décochées.
    Le marqueur `PLAFOND` ne compte pas (seule sa catégorie précise compte) ; une catégorie
    inconnue suit la case « doutes ». Catégories absentes (ancien hook), mal formées, ou
    marqueur seul : libres seulement si TOUT est décoché. Jamais sous ESTOP, jamais avec un
    fournisseur non-Claude ; cases illisibles (pas un objet de booléens) : carte."""
    if estop or non_claude:
        return False
    n = _normales(coches)
    if n is None:
        return False
    if cats is None or isinstance(cats, (str, bytes)):
        return not any(n.values())
    try:
        ids = [c for c in cats if c != PLAFOND]
    except TypeError:
        return not any(n.values())
    if not ids or not all(isinstance(c, str) for c in ids):
        return not any(n.values())
    for c in ids:
        if n.get(c, n[DOUTES]):
            return False
    return True


def resume(coches) -> str:
    """« 25 cases cochées sur 30 » ; « aucune carte » si tout est décoché."""
    n = _normales(coches) or coches_defaut()
    k = sum(n.values())
    if not k:
        return "Toutes décochées : aucune carte de confirmation (sauf arrêt d'urgence)"
    return f"{k} cochée(s) sur {len(n)} : carte pour ces catégories"


# --- fichier signé : seul le cœur l'écrit (clé HMAC illisible pour Claude, `SECRET_PATHS`).
# Un fichier réécrit par un autre process ne porte pas la bonne signature : défauts.

def _signature(cle: bytes, coches: dict) -> str:
    corps = json.dumps(coches, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hmac.new(cle, corps, hashlib.sha256).hexdigest()


def _cle(chemin, creer: bool) -> bytes | None:
    try:
        data = chemin.read_bytes()
        if len(data) >= 32:
            return data
    except FileNotFoundError:
        pass
    except OSError:
        return None
    if not creer:
        return None
    data = _secrets.token_bytes(32)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return data


def lire_cases(fichier, fichier_cle) -> tuple[dict[str, bool], str]:
    """(cases, raison) : raison vide si le fichier est valide ; sinon défauts et la raison
    (absent, illisible, signature, types)."""
    try:
        data = json.loads(fichier.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return coches_defaut(), "absent"
    except (OSError, ValueError) as exc:
        return coches_defaut(), f"illisible ({type(exc).__name__})"
    if not isinstance(data, dict) or not isinstance(data.get("coches"), dict):
        return coches_defaut(), "forme inattendue"
    cle = _cle(fichier_cle, creer=False)
    if cle is None or not isinstance(data.get("sig"), str) or not hmac.compare_digest(
            data["sig"], _signature(cle, data["coches"])):
        return coches_defaut(), "signature absente ou fausse"
    coches, ok = coches_depuis(data["coches"])
    return coches, "" if ok else "types invalides"


def ecrire_cases(fichier, fichier_cle, coches: dict) -> None:
    """Écriture atomique et signée (0600). Lève OSError."""
    n = _normales(coches)
    if n is None:
        raise ValueError("cases invalides")
    cle = _cle(fichier_cle, creer=True)
    if cle is None:
        raise OSError("clé des cases illisible")
    fichier.parent.mkdir(parents=True, exist_ok=True)
    tmp = fichier.with_name(fichier.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "coches": n, "sig": _signature(cle, n)}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, fichier)


def journaliser(journal, qui: str, avant: dict, apres: dict, quand: str) -> list[str]:
    """Une ligne par case changée, en AJOUT SEUL (0600) : « quand	qui	id	avant -> après ».
    Rend les ids changés."""
    a, b = _normales(avant) or coches_defaut(), _normales(apres) or coches_defaut()
    changes = [k for k in CASES_PAR_ID if a[k] != b[k]]
    if not changes:
        return []
    def mot(v: bool) -> str:
        return "cochée" if v else "décochée"
    lignes = "".join(f"{quand}\t{qui}\t{k}\t{mot(a[k])} -> {mot(b[k])}\n" for k in changes)
    journal.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(journal, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(lignes)
    return changes
