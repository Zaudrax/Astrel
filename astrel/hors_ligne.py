"""Mode « rien ne sort » (04/11/2026, lot 2 du moteur local, docs/chantiers/HYBRIDE-QWEN.md).

Réglage `hors_ligne` (runtime/settings.json, protégé contre Claude ; `ASTREL_HORS_LIGNE=1` l'impose). Actif :
- le cerveau Claude est ARRÊTÉ (aucun process `claude`), les demandes de l'utilisateur vont au moteur local
  (Qwen sur Ollama, `moteur_local`), les messages automatiques attendent le retour en ligne ;
- la voix passe par Piper pour TOUT (jamais Edge) ;
- chaque module qui sort se coupe À LA SOURCE en consultant `actif()` (`verifier` lève `HorsLigne`).

Le cœur décide (`coeur/hors_ligne.py`) : activer est refusé tant qu'une tâche de Claude tourne (décision de
l'utilisateur du 04/11 : « Stop tout » d'abord) ; activer comme désactiver est refusé à Claude.

`SORTIES` = l'inventaire de ce qui part de la machine : il nourrit la page « Ce qui part de cette machine »
(`confidentialite.view`) et `tests/test_hors_ligne.py` vérifie que tout module qui sort y est coupé.
"""
from __future__ import annotations

import os
import re
import unicodedata

CLE = "hors_ligne"
ENV = "ASTREL_HORS_LIGNE"
REFUS = "mode « rien ne sort » actif : rien ne part de la machine"


class HorsLigne(RuntimeError):
    """Une sortie vers Internet demandée pendant le mode « rien ne sort »."""


def impose() -> bool:
    """Valeur imposée par l'environnement (`ASTREL_HORS_LIGNE`), aussi défaut du réglage."""
    return os.environ.get(ENV, "").strip().lower() in ("1", "oui", "on", "true", "yes")


def actif() -> bool:
    """Mode « rien ne sort » en vigueur (réglage relu si le fichier a changé)."""
    try:
        from .settings import reglages
        return reglages.value(CLE) is True  # strict : une valeur d'un autre genre n'active rien
    except Exception:  # réglages illisibles : la valeur imposée, sinon en ligne (comme avant ce mode)
        return impose()


def verifier(quoi: str) -> None:
    """À appeler juste avant une sortie : lève `HorsLigne` si le mode est actif."""
    if actif():
        raise HorsLigne(f"{quoi} : {REFUS}")


# --- inventaire de ce qui sort (« Ce qui part de cette machine »)
# (titre, ce qui part, modules qui le font, comment c'est coupé en mode « rien ne sort »)
SORTIES = (
    ("Vers Claude (Anthropic)",
     "tes messages, dits ou tapés, et ce qu'Astrel lit pour y répondre (fichiers, pages, captures d'écran "
     "demandées), par Claude Code, sur ton abonnement ; le relevé du quota et l'état du compte",
     ("brain", "quota_releve", "compte", "claudeai"), "cerveau Claude arrêté"),
    ("Vers Microsoft (voix Edge)", "le texte qu'Astrel dit à voix haute",
     ("voice.engines",), "voix Piper, sur la machine"),
    ("WhatsApp", "les messages du chat « message à soi-même », dans les deux sens",
     ("whatsapp",), "pont arrêté"),
    ("Images et vidéos IA", "tes demandes d'images et de vidéos, par Chrome, vers Gemini, Bing, ChatGPT, Google "
                             "Flow ou Dreamina", ("images_ia", "videos_ia", "ia_web", "ia_connexion"), "refusées"),
    ("Veille de sécurité", "la liste des paquets de tes projets, vers OSV, GitHub, NVD, CISA et le CERT-FR",
     ("veille_secu",), "sautée"),
    ("Mises à jour", "la vérification des mises à jour d'Astrel (git, vers son dépôt)", ("maj",), "sautée"),
    ("Ollama (registre)", "le nom des modèles locaux, pour connaître leur taille ou les télécharger",
     ("modeles_locaux",), "refusé"),
    ("Agenda et connecteurs", "agenda, Gmail, Home Assistant, traduction (DeepL), transports : chacun parle à son "
                              "service, seulement si tu les branches",
     ("tools.agenda", "tools.translate", "tools.transport"), "coupés"),
    ("Grok (xAI)", "la connexion à ton compte Grok, si tu l'as reliée", ("xai_oauth",), "refusée"),
    ("Fournisseurs à clé ou en CLI", "tes messages vers OpenAI, Gemini, OpenRouter, Grok, Codex, si tu en choisis un",
     ("fournisseurs_cli.base",), "refusés"),
    ("Icônes des connecteurs", "le nom des connecteurs, pour récupérer leur icône", ("icones",), "sautées"),
    ("Réflexes « vérifier une adresse »", "une requête vers l'adresse web que tu as donnée à un réflexe",
     ("senses.checks",), "sautés"),
    ("Vers context7 (agent codeur)", "le nom d'une bibliothèque et la question de documentation du codeur (couper : "
                                     "« off » dans runtime/context7.txt)", ("brain",), "coupé avec le cerveau"),
    # 05/11 (lot 7 partie A) : outils web du modèle local ; SearXNG (127.0.0.1) ne reçoit une recherche que par
    # `web_local`, qui consulte `actif()` avant chaque recherche, page et redirection.
    # 05/11 (lot 7B) : et le navigateur du local, par son mandataire (`proxy_local`, qui consulte `actif()` à chaque
    # connexion et ferme les tunnels ouverts quand le mode s'allume).
    ("Recherche web du modèle local", "les mots des recherches de Qwen (vers DuckDuckGo, Brave, Bing, Yahoo et "
                                      "Wikipédia, par SearXNG installé sur la machine), l'adresse des pages qu'il lit "
                                      "et les sites que son navigateur ouvre",
     ("web_local", "mcp_web_local", "proxy_local"), "refusées"),
)
RESTE = ("le son du micro (reconnu sur place), la mémoire (vault/), l'index de l'historique (runtime/history.db), "
         "les réglages, le moteur local (Ollama sur la machine)")
GARDE_PAR_CLAUDE = ("ses propres transcriptions (~/.claude/projects) : « Effacer l'historique » n'y touche pas")


# --- commande vocale ou tapée (« passe hors ligne », « go offline ») : la phrase ENTIÈRE, rien d'autre

def _plier(text: str) -> str:
    s = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c)).replace("’", "'")


_VOCATIF = re.compile(r"^\s*(?:(?:hey|ok|dis)\s+)?(?:astrel|jarvis)\s*[,:!.]?\s*")
_MODE = r"(?:(?:le\s+)?mode\s+)?(?:hors[\s-]+ligne|rien\s+ne\s+sort)"
_ACTIVER = re.compile(
    r"(?:(?:passe|mets[\s-]+toi|bascule|active)\s+)?(?:en\s+)?" + _MODE
    + r"|(?:go|switch\s+to|turn\s+on)\s+offline(?:\s+mode)?|offline\s+mode(?:\s+on)?|nothing\s+leaves")
_DESACTIVER = re.compile(
    r"(?:repasse|reviens|retourne|repars)\s+en\s+ligne|(?:quitte|coupe|desactive|sors\s+du)\s+" + _MODE
    + r"|(?:go|come)\s+back\s+online|go\s+online|(?:leave|exit|turn\s+off)\s+offline(?:\s+mode)?"
    r"|offline\s+mode\s+off")


def commande(text: str) -> str:
    """« on », « off » ou « » : la phrase entière demande d'entrer dans le mode ou d'en sortir."""
    s = _VOCATIF.sub("", _plier(text), count=1).strip().rstrip(".!?…").strip()
    if _DESACTIVER.fullmatch(s):
        return "off"
    if _ACTIVER.fullmatch(s):
        return "on"
    return ""
