"""Commandes « / » de la barre de chat (05/11/2026, demande de l'utilisateur : « pouvoir mettre des commandes dans le
chat, genre /clear pour clear une conversation et d'autres commandes utiles »).

Reconnues AVANT tout envoi à un modèle (jamais envoyées à Claude ni au modèle local) : par le HUD
(`hud/src/commandes.js`, même table, mêmes règles : `tests/test_commandes_chat.py` compare les deux), puis par
le cœur (`coeur/commandes_chat.py` : commande HUD `chat_commande {nom, arg}`, et `say` tapé dans la bulle).
Chaque commande RÉUTILISE un chemin qui existe déjà (remise à zéro de session, routeur, réglages, `set_model`,
modes d'usage, ■, « Stop tout ») : aucune n'ouvre de droit nouveau.

Règle (précise exprès) : c'est une commande seulement si le message commence par « / » suivi d'un MOT (lettres,
tiret) puis d'une espace ou de la fin. « /data/x est-ce que… », « /tmp.log », « /1 » ou « / » seul ne sont pas des
commandes : du texte comme un autre. Un mot qui n'est pas dans la table (« /xyz », « /tmp est plein ») est une
commande INCONNUE : rien n'est envoyé (une faute de frappe sur /local ne part pas chez Claude) ; dans le HUD, une
espace devant le « / » envoie le texte tel quel. Fonctions PURES."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# `arg` : "" (rien après le nom), "texte" (texte libre exigé), "choix" (une des `valeurs`).


@dataclass(frozen=True)
class Commande:
    nom: str                       # nom canonique (français), celui qu'envoie le HUD
    en: str                        # nom montré quand le HUD est en anglais
    alias: tuple[str, ...] = ()
    arg: str = ""
    valeurs: tuple[str, ...] = ()


COMMANDES: tuple[Commande, ...] = (
    Commande("aide", "help", ("help",)),
    Commande("clear", "clear", ("nouveau", "new")),
    Commande("local", "local", (), "texte"),
    Commande("claude", "claude", (), "texte"),
    # 05/11 (lot 8 du moteur local) : la question au modèle local, réflexion activée pour elle seule
    Commande("reflechir", "think", ("think", "reflexion"), "texte"),
    Commande("mode", "mode", (), "choix", ("auto", "local", "claude", "hybride")),
    Commande("modele", "model", ("model",), "choix", ("sonnet", "opus", "haiku")),
    Commande("horsligne", "offline", ("hors-ligne", "offline")),
    Commande("enligne", "online", ("en-ligne", "online")),
    Commande("silence", "silent", ("silencieux", "silent")),
    Commande("vocal", "voice", ("voix", "voice", "conversation")),
    Commande("dictee", "dictation", ("dictation",)),
    Commande("stop", "stop", ()),
    Commande("tout-arreter", "stop-all", ("toutarreter", "stop-tout", "stop-all")),
)
ALIAS_VALEURS = {"hybrid": "hybride"}  # /mode hybrid (anglais)
MOT_MAX = 30

# Préfixes que le routeur connaît déjà (`routeur.forced_engine`) : /local et /claude ne font que les écrire.
PREFIXES = {"local": "en local : ", "claude": "avec Claude : "}

# Textes d'écran (catalogue astrel/locales/en.json)
INCONNUE = "Commande inconnue : /{nom}. /aide donne la liste ; rien n'est envoyé."
SANS_ARGUMENT = "/{nom} ne prend rien après son nom ; rien n'est envoyé."
TEXTE_MANQUANT = "Écris ta demande après /{nom}."
CHOIX = "/{nom} attend une de ces valeurs : {choix}."
AIDE = "Commandes du chat : {liste}."


def plier(texte: str) -> str:
    """Minuscules sans accents (« /Dictée » = « /dictee »)."""
    s = unicodedata.normalize("NFKD", str(texte or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


_INDEX = {plier(n): c for c in COMMANDES for n in (c.nom, c.en, *c.alias)}
_JETON = re.compile(r"^/(\S*)")
_MOT = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")  # lettres (str.isalpha, comme \p{L} du HUD), tirets internes


@dataclass(frozen=True)
class Analyse:
    nom: str = ""          # nom canonique ; vide pour une commande inconnue
    arg: str = ""          # texte (local, claude) ou valeur choisie (mode, modele)
    erreur: str = ""       # texte d'écran si la commande est mal formée (rien n'est fait)
    inconnue: str = ""     # mot tapé quand il n'est pas dans la table
    code: str = ""         # « inconnue », « sans_argument », « texte_manquant », « choix » (le HUD, hud/src/commandes.js)

    @property
    def ok(self) -> bool:
        return bool(self.nom) and not self.erreur


def analyser(texte: str) -> Analyse | None:
    """Commande tapée, ou None : le texte part comme un message ordinaire. `texte` tel que tapé (pas d'espace
    retirée devant : « ␣/clear » n'est pas une commande)."""
    s = str(texte or "")
    jeton = _JETON.match(s)
    if not jeton:
        return None
    mot0 = jeton.group(1)
    if not mot0 or len(mot0) > MOT_MAX or not _MOT.fullmatch(mot0) or not mot0.replace("-", "").isalpha():
        return None
    mot = plier(mot0)
    reste = s[jeton.end():].strip()
    c = _INDEX.get(mot)
    if c is None:
        return Analyse(inconnue=mot, erreur=INCONNUE.format(nom=mot), code="inconnue")
    if c.arg == "" and reste:
        return Analyse(nom=c.nom, erreur=SANS_ARGUMENT.format(nom=c.nom), code="sans_argument")
    if c.arg == "texte" and not reste:
        return Analyse(nom=c.nom, erreur=TEXTE_MANQUANT.format(nom=c.nom), code="texte_manquant")
    if c.arg == "choix":
        v = plier(reste)
        v = ALIAS_VALEURS.get(v, v)
        if v not in c.valeurs:
            return Analyse(nom=c.nom, erreur=CHOIX.format(nom=c.nom, choix=", ".join(c.valeurs)), code="choix")
        reste = v
    return Analyse(nom=c.nom, arg=reste)


def texte_routage(a: Analyse) -> str:
    """/local et /claude : le message avec le préfixe que le routeur reconnaît (« en local : … »)."""
    return PREFIXES[a.nom] + a.arg if a.ok and a.nom in PREFIXES else ""


def aide() -> str:
    """Liste courte, pour la bulle et le terminal (le HUD a sa propre liste, avec une ligne par commande)."""
    parts = []
    for c in COMMANDES:
        nom = "/" + c.nom
        if c.arg == "texte":
            nom += " …"
        elif c.arg == "choix":
            nom += " " + "|".join(c.valeurs)
        parts.append(nom)
    return AIDE.format(liste=", ".join(parts))
