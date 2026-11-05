"""Routeur des moteurs (04/11/2026, moteur local, docs/chantiers/HYBRIDE-QWEN.md).

Lot 1 : `forced_engine`, le CHOIX EXPLICITE de l'utilisateur (« en local : … », « traite en local », canal Local).
Lot 3 : `decider`, le routage complet, dans cet ordre (décisions du 04/11) :
1. mode « rien ne sort » → local ; 2. local explicite → local ; 3. données sensibles (`sensible.scan`) ;
4. Claude explicite → Claude, avec CARTE si sensible ; 5. réglage `moteur_mode` (auto, local, claude, hybride) ;
6. auto : sensible → local, cybersécurité hors du projet → local, sinon → CLAUDE (ce qui n'est classé nulle
part va à Claude) ; 7. le local n'a pas la capacité (outils, web, envoi, pièces jointes) → carte PROPOSÉE.
Jamais de bascule seule vers Claude, jamais d'envoi d'une donnée sensible sans carte ; un refus de Claude ne
renvoie rien au local. Fonctions PURES, sans modèle : le cœur décide (`coeur/routage.py`).
05/11 (lot 7 partie B, décision de l'utilisateur : Qwen a Internet en permanence, sauf « rien ne sort ») : quand les
outils web du local sont ouverts (`Etat.web_local`), une demande LOCALE qui cherche sur Internet ou lit une page
n'est plus proposée à Claude : le local répond avec ses outils (`Decision.outils`), et avec son navigateur
(`Decision.navigateur`) si elle vise un site interactif et qu'il est ouvert (`Etat.navigateur_local`)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable

LOCAL, CLAUDE = "local", "claude"


def _plier(text: str) -> str:
    """Minuscules sans accents, apostrophes droites (même pliage que `channels.fold`)."""
    s = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c)).replace("’", "'")


# Nom de l'assistant en tête (« Astrel, en local : … », « hey jarvis, … ») : retiré avant de chercher.
_VOCATIF = re.compile(r"^\s*(?:(?:hey|ok|dis)\s+)?(?:astrel|jarvis)\s*[,:!.]?\s*")
# Préfixe qui désigne le moteur, en tête du message (retiré du texte donné au modèle).
_PREFIXE_LOCAL = re.compile(
    r"^\s*(?:en\s+local|localement|local|hors\s+ligne|locally|local\s+model|offline)\s*[:,;.\-–—]\s*")
_PREFIXE_CLAUDE = re.compile(r"^\s*(?:avec\s+claude|claude|with\s+claude)\s*[:,;.\-–—]\s*")
# Demande locale exprimée dans la phrase. Étroit exprès : « fais tourner le serveur en local » ou « rédige
# le mail sans l'envoyer » parlent d'autre chose et vont à Claude ; un faux positif part chez Qwen, badge
# « Local » visible, et « avec Claude : … » le renvoie.
_FIN = r"\s*(?:[.,;:!?]|$|s'il te pla[iî]t\b|s'il vous pla[iî]t\b|stp\b|svp\b|please\b)"  # 04/11 : formule de politesse
_DEHORS = r"(?:a\s+l'exterieur|dehors|sur\s+internet|en\s+ligne|a\s+claude|nulle\s+part)"
_PHRASES_LOCAL = re.compile(
    r"\b(?:"
    r"(?:traite|reponds|analyse)\w*(?:[\s'-]+[\w']+){0,3}\s+en\s+local" + _FIN +
    r"|fais[\s-]+le\s+en\s+local\b"
    r"|(?:avec|utilise|prends|passe\s+par)\s+(?:le\s+)?modele\s+local\b"
    r"|sans\s+rien\s+envoyer\b"
    r"|sans\s+envoyer\s+(?:de\s+donnees\s+|quoi\s+que\s+ce\s+soit\s+|rien\s+)?" + _DEHORS +
    r"|(?:handle|process|answer|analy[sz]e)\w*(?:\s+[\w']+){0,3}\s+locally" + _FIN +
    r"|use\s+(?:the\s+)?local\s+model\b"
    r"|without\s+sending\s+(?:anything|any\s+data|data)\b"
    r")")
_PHRASES_CLAUDE = re.compile(r"\b(?:utilise|prends|passe\s+par|use)\s+claude\b")
# « pas en local », « not locally », « n'utilise pas le modèle local » : jamais pris pour une demande locale.
_NEGATION = re.compile(r"\b(?:pas|plus|jamais|not|never|don't|dont)\b[^.?!]{0,25}\b(?:local\w*|modele\s+local)")


def forced_engine(text: str, channel: str = "", canaux_locaux: frozenset[str] | set[str] = frozenset()) -> tuple[str, str]:
    """(moteur choisi EXPRESSÉMENT, texte pour ce moteur). Moteur : « local », « claude » ou « » (rien de
    demandé : le cœur garde son choix habituel). Le préfixe qui désigne le moteur est retiré du texte ;
    une demande dans la phrase garde le texte entier. `channel` : canal du HUD d'où vient le message ;
    un canal de `canaux_locaux` (canal « Local ») vaut un choix explicite."""
    original = str(text or "")
    plie = _plier(original)
    sans_nom = _VOCATIF.sub("", plie, count=1)
    debut = len(plie) - len(sans_nom)  # le pliage garde les longueurs (accents retirés après NFKD : non)
    for motif, moteur in ((_PREFIXE_LOCAL, LOCAL), (_PREFIXE_CLAUDE, CLAUDE)):
        m = motif.match(sans_nom)
        if m:
            return moteur, _apres(original, debut + m.end()) or original
    if _NEGATION.search(sans_nom):
        return "", original
    if _PHRASES_LOCAL.search(sans_nom):
        return LOCAL, original
    if _PHRASES_CLAUDE.search(sans_nom):
        return CLAUDE, original
    if channel and channel in canaux_locaux:
        return LOCAL, original
    return "", original


def _apres(original: str, n_plie: int) -> str:
    """Le texte d'origine après les `n_plie` premiers caractères du texte PLIÉ. Le pliage NFKD retire les
    accents combinants : on avance dans l'original caractère par caractère en repliant chacun."""
    vu = 0
    for i, c in enumerate(original):
        if vu >= n_plie:
            return original[i:].strip()
        vu += len(_plier(c))
    return ""


# --- lot 3 : routage automatique (04/11)

MODES = ("auto", "local", "claude", "hybride")
CARTE_SENSIBLE, CARTE_CAPACITE = "sensible", "capacite"

# Cybersécurité : des motifs PRÉCIS (outils, notions du métier), jamais un mot banal isolé.
_CYBER = re.compile(
    r"\bcve-\d{4}-\d{3,}\b|\bcwe-\d+\b|\bctf\b|\bcapture the flag\b|\bpentest\w*|\btests? d'intrusion\b"
    r"|\bpenetration test\w*|\b(?:nmap|metasploit|msfconsole|burp\s?suite|wireshark|tcpdump|yara|snort|suricata"
    r"|hashcat|john the ripper|sqlmap|gobuster|ffuf|nikto|volatility|ghidra|radare2|bloodhound|mimikatz|hydra"
    r"|zeek|osquery|wazuh|crowdsec|lynis|openvas|nessus)\b"
    r"|\bregles? sigma\b|\bsigma rules?\b|\bvulnerabilit\w*|\bvulnerab\w*|\bfailles? de securite\b"
    r"|\b(?:injection sql|sql injection|sqli|xss|csrf|ssrf|xxe|lfi|rfi|idor)\b|\bexecution de code a distance\b"
    r"|\bremote code execution\b|\bescalade de privileges?\b|\bprivilege escalation\b|\bbuffer overflow\b"
    r"|\bdepassement de tampon\b|\breverse shell\b|\bshellcode\b|\bmalwares?\b|\bransomware\b|\brancongiciel"
    r"|\bindicateurs? de compromission\b|\bindicators? of compromise\b|\biocs?\b|\bthreat hunting\b"
    r"|\bforensi\w*|\breponse a (?:un )?incident\b|\bincident response\b|\bcybersecurit\w*|\bsecurite informatique\b"
    r"|\b(?:force brute|brute.?force|bruteforce|scan(?:s)? (?:de|des) ports?|port ?scan\w*|ports? ouverts?|open ports?)\b"
    r"|\bdurcissement\b|\bhardening\b|\b(?:iptables|nftables|ufw|fail2ban|selinux|apparmor)\b"
    r"|\b(?:analyse|lis|explique|regarde|decortique|analy[sz]e|read|explain)\w*\b[^.?!]{0,40}"
    r"\b(?:journaux|logs?|syslog|journalctl|auth\.log|dmesg|pcap)\b"
    r"|\b(?:sshd|sudo|kernel|systemd|audit)\[\d+\]:|\bfailed password for\b|\bsshd_config\b")
# Le projet d'Astrel ou un dépôt : du développement, même sur un sujet de sécurité (le local ne lit pas le code).
_PROJET = re.compile(r"\b(?:astrel|jarvis|garde-fou|hud|le projet|ce projet|mon projet|le depot|ce depot|repo"
                     r"|repository|codebase|pull request)\b")
# Développement et tâches lourdes : proposés à Claude en mode hybride.
_DEV = re.compile(r"\b(?:code|coder|fonction|classe|refactor\w*|refacto\w*|bug|debug\w*|commit|git|pytest|tests?"
                  r" unitaires?|python|javascript|typescript|node|npm|api|compile\w*|build|deploi\w*|deploy\w*"
                  r"|script|programme|implement\w*)\b")
# Ce que le moteur local NE SAIT PAS faire (pas d'outils au lot 3) : chercher en ligne, agir, envoyer, lire un
# fichier de la machine.
_CAPACITE = re.compile(
    r"\b(?:cherche|recherche|trouve|regarde)\w*\b[^.?!]{0,30}\b(?:sur (?:internet|le web|google)|en ligne)\b"
    r"|\b(?:search|look up|browse)\b[^.?!]{0,20}\b(?:web|online|internet)\b"
    r"|\benvoie\w*\b|\bsend\b|\bpublie\w*\b|\brappelle-moi\b|\bremind me\b|\bmets? un rappel\b"
    r"|\b(?:ouvre|lance|execute|installe|supprime|cree|modifie|ecris|lis|scanne)\w*\b[^.?!]{0,30}"
    r"\b(?:fichier|dossier|programme|commande|script|application|appli|page|mail|message|agenda|reseau|port)s?\b"
    r"|\b(?:open|run|execute|install|delete|create|edit|write|read|scan)\b[^.?!]{0,30}"
    r"\b(?:file|folder|program|command|script|app|email|calendar|network|port)s?\b"
    r"|\b(?:agent|agents|sous-agent|mission)\b")
# 04/11 (lot 4) : LIRE seulement (fichiers d'un projet, journaux, configurations) : le local le fait avec ses
# outils de lecture quand ils sont ouverts (`Etat.lecture_locale`) ; tout le reste de `_CAPACITE` reste proposé.
_LECTURE = re.compile(
    r"\b(?:lis|lire|relis|analyse\w*|examine\w*|regarde|inspecte\w*|audite\w*|parcours|explique|resume|cherche)"
    r"\b[^.?!]{0,40}\b(?:fichiers?|dossiers?|repertoires?|code|config\w*|journaux|logs?|projet|scripts?"
    r"|sshd_config|sources?)\b"
    r"|\b(?:read|analy[sz]e|inspect|audit|review|look at|explain|summari[sz]e|grep|search)\b[^.?!]{0,40}"
    r"\b(?:files?|folders?|director(?:y|ies)|code|config\w*|logs?|project|scripts?|sources?)\b")
# 05/11 (lot 5) : lancer une commande AUTORISÉE par l'utilisateur, ou PROPOSER une modification d'un fichier (rien
# n'est écrit sans son clic au HUD) : le local le fait quand ces outils sont ouverts (`Etat.actions_locales`).
# « supprime », « envoie », « installe », le web… restent proposés à Claude (`_CAPACITE`).
_ACTION_LOCALE = re.compile(
    r"\b(?:lance|relance|execute|passe|run|rerun)\w*\b[^.?!]{0,40}\b(?:commandes?(?: autorisees?)?|tests?|pytest"
    r"|commands?|git status)\b"
    r"|\b(?:propose|prepare|suggere|redige|suggest|draft)\w*\b[^.?!]{0,40}\b(?:modifications?|correctifs?|patch"
    r"|changements?|corrections?|changes?|fix(?:es)?|edits?)\b"
    r"|\b(?:corrige|modifie|change|ecris|edite|fix|edit|modify|write)\w*\b[^.?!]{0,30}\b(?:fichiers?|code"
    r"|config\w*|scripts?|readme|files?)\b")


# 05/11 (lot 7B) : chercher sur Internet, lire une page, une adresse écrite dans la demande.
_WEB = re.compile(
    r"\b(?:cherche|recherche|trouve|regarde|verifie|consulte)\w*\b[^.?!]{0,40}\b(?:sur (?:internet|le web|le net"
    r"|google|wikipedia)|en ligne|sur le site)\b"
    r"|\b(?:search|look up|browse|check)\b[^.?!]{0,30}\b(?:web|online|internet|wikipedia)\b"
    r"|\b(?:lis|lire|ouvre|ouvrir|resume|regarde|consulte|read|open|summari[sz]e|look at)\w*\b[^.?!]{0,30}"
    r"\b(?:page|article|site|url|lien|link|website)s?\b"
    r"|\bhttps?://\S+|\bwww\.[a-z0-9-]+\.\S+")
# 05/11 (lot 7B) : un site INTERACTIF (le navigateur du local, 8 outils, derrière son mandataire).
_NAVIGATEUR = re.compile(
    r"\b(?:navigateur|browser)\b|\bsite interactif\b|\binteractive (?:site|website)\b"
    r"|\b(?:remplis|remplir|fill(?: in| out)?)\b[^.?!]{0,30}\b(?:formulaire|form|champs?|fields?)\b"
    r"|\b(?:clique|cliquer|click)\w*\b"
    r"|\b(?:va|aller|navigue\w*|go|navigate)\b[^.?!]{0,10}\b(?:sur|to)\b[^.?!]{0,30}\b(?:site|page|website)\b"
    r"|\bapplication (?:web|javascript)\b|\bweb app\b")


@dataclass(frozen=True)
class Decision:
    """Moteur choisi par le cœur. `carte` : « sensible » (données sensibles vers Claude) ou « capacite » (le
    local ne sait pas faire) : le message est RETENU et l'utilisateur choisit à l'écran ; `recommande` : moteur du
    premier bouton. `texte` : ce que le moteur reçoit (préfixe explicite retiré)."""
    moteur: str
    categorie: str
    texte: str
    raisons: tuple[str, ...] = ()
    carte: str = ""
    recommande: str = ""
    explicite: bool = False
    outils: bool = False   # 04/11 (lot 4) : le local répond AVEC ses outils de lecture
    navigateur: bool = False  # 05/11 (lot 7B) : et avec son navigateur (site interactif)


@dataclass
class Etat:
    """Ce que le cœur sait au moment du message (tout est donné : `decider` ne lit rien)."""
    hors_ligne: bool = False
    canaux_locaux: frozenset = frozenset()
    projet: bool = False          # canal relié à un dossier de projet
    source: str = "clavier"
    scan: Callable[[str, Iterable[str]], list[str]] | None = None
    lecture_locale: bool = False  # 04/11 (lot 4) : outils de lecture du local ouverts (banc réussi, ni Total ni levée)
    actions_locales: bool = False  # 05/11 (lot 5) : commandes autorisées et propositions ouvertes (banc du lot 5)
    web_local: bool = False        # 05/11 (lot 7B) : outils web du local ouverts (épreuves web du banc, en ligne)
    navigateur_local: bool = False  # 05/11 (lot 7B) : navigateur du local ouvert (ses épreuves du banc)


def mode_valide(mode: object) -> str:
    m = str(mode or "auto")
    return m if m in MODES else "auto"


def _lecture_seule(demande: str, actions: bool = False, web: bool = False, navigateur: bool = False) -> bool:
    """La demande a besoin de LIRE et de rien d'autre (aucune action, aucun envoi, pas de web). 05/11 (lot 5) :
    `actions` : lancer une commande autorisée ou proposer une modification compte aussi. 05/11 (lot 7B) : `web` :
    chercher sur Internet et lire une page aussi ; `navigateur` : un site interactif aussi."""
    plie = _plier(demande)
    motifs = [_LECTURE] + ([_ACTION_LOCALE] if actions else []) + ([_WEB] if web else []) + (
        [_NAVIGATEUR] if web and navigateur else [])
    vu = any(m.search(plie) for m in motifs)
    reste = plie
    for m in motifs:
        reste = m.sub(" ", reste)
    return vu and not _CAPACITE.search(reste)


_SUR_LE_WEB = re.compile(r"\b(?:site|page|web|url|navigateur|browser|website|internet|en ligne|online)\b|https?://|www\.")


def vise_un_site(demande: str) -> bool:
    """05/11 (lot 7B) : la demande vise-t-elle un site INTERACTIF (formulaire, clic, navigateur) ? Il faut aussi
    qu'elle parle du web (« explique où cliquer » dans un fichier n'ouvre pas Chrome)."""
    plie = _plier(demande)
    return bool(_NAVIGATEUR.search(plie) and (_SUR_LE_WEB.search(plie) or _WEB.search(plie)))


def decider(texte: str, canal: str = "", pieces: Iterable[str] = (), reglages: dict | None = None,
            etat: Etat | None = None) -> Decision:
    """Moteur d'une demande de l'utilisateur (voir l'en-tête du module pour l'ordre). 04/11 (lot 4) : une demande
    locale qui a seulement besoin de lire passe par les outils de lecture du local s'ils sont ouverts ;
    05/11 (lot 5) : aussi lancer une commande autorisée ou proposer une modification, s'ils sont ouverts."""
    etat = etat or Etat()
    dec = _decider(texte, canal, pieces, reglages, etat)
    if dec.moteur == LOCAL and not dec.carte and etat.lecture_locale and not list(pieces or ()) \
            and _lecture_seule(dec.texte, etat.actions_locales, etat.web_local, etat.navigateur_local):
        nav = etat.web_local and etat.navigateur_local and vise_un_site(dec.texte)
        return replace(dec, outils=True, navigateur=nav)
    return dec


def _decider(texte: str, canal: str, pieces: Iterable[str], reglages: dict | None, etat: Etat) -> Decision:
    pieces = [str(p) for p in pieces or ()]
    mode = mode_valide((reglages or {}).get("moteur_mode"))
    moteur, demande = forced_engine(texte, canal, etat.canaux_locaux)
    if etat.hors_ligne:
        return Decision(LOCAL, "hors_ligne", demande, explicite=moteur == LOCAL)
    if moteur == LOCAL:
        return Decision(LOCAL, "explicite", demande, explicite=True)
    scan = etat.scan
    if scan is None:
        from . import sensible
        scan = sensible.scan
    raisons = tuple(scan(texte, pieces))
    if pieces:  # le moteur local ne lit pas encore les pièces jointes
        manque = ("pièces jointes",)
    elif etat.lecture_locale and _lecture_seule(demande, etat.actions_locales, etat.web_local, etat.navigateur_local):
        manque = ()  # 04/11 (lot 4) : le local sait lire, avec ses outils (05/11, lot 5 : et lancer, proposer ;
        # lot 7B : et chercher sur Internet, lire une page, un site interactif)
    else:
        manque = ("outils",) if _CAPACITE.search(_plier(demande)) else ()
    if moteur == CLAUDE:
        if raisons:
            return Decision(CLAUDE, "explicite", demande, raisons, CARTE_SENSIBLE, CLAUDE, explicite=True)
        return Decision(CLAUDE, "explicite", demande, explicite=True)
    plie = _plier(texte)
    whatsapp = etat.source == "whatsapp"  # la réponse locale ne repart pas par WhatsApp (lot 1)
    if mode == "claude" or whatsapp:
        if raisons:
            return Decision(CLAUDE, "sensible", demande, raisons, CARTE_SENSIBLE,
                            CLAUDE if whatsapp else LOCAL)
        return Decision(CLAUDE, "reglage" if mode == "claude" else "whatsapp", demande)
    if raisons:
        if manque:
            return Decision(LOCAL, "sensible", demande, raisons + manque, CARTE_SENSIBLE, LOCAL)
        return Decision(LOCAL, "sensible", demande, raisons)
    if mode == "local":
        if manque:
            return Decision(LOCAL, "reglage", demande, manque, CARTE_CAPACITE, CLAUDE)
        return Decision(LOCAL, "reglage", demande)
    projet = etat.projet or bool(_PROJET.search(plie))
    cyber = bool(_CYBER.search(plie)) and not projet
    if mode == "hybride":
        if manque or projet or _DEV.search(plie):
            return Decision(LOCAL, "hybride", demande, manque or ("développement",), CARTE_CAPACITE, CLAUDE)
        return Decision(LOCAL, "hybride", demande)
    if cyber:  # auto
        if manque:
            return Decision(LOCAL, "cybersecurite", demande, manque, CARTE_CAPACITE, LOCAL)
        return Decision(LOCAL, "cybersecurite", demande)
    return Decision(CLAUDE, "projet" if projet else "defaut", demande)
