"""Routeur des moteurs (04/11/2026, lot 1 du moteur local, docs/chantiers/HYBRIDE-QWEN.md).

Lot 1 : seul le CHOIX EXPLICITE de l'utilisateur compte (« en local : … », « traite en local », canal Local) ;
tout le reste va à Claude, comme avant. Fonction PURE, sans import du paquet : le cœur décide, jamais un
modèle. Le routage automatique (domaines, données sensibles) viendra au lot 3."""
from __future__ import annotations

import re
import unicodedata

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
_FIN = r"\s*(?:[.,;:!?]|$)"
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
