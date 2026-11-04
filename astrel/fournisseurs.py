"""Fournisseur du cerveau (20/10, plan P11 ; demande de l'utilisateur : se connecter avec une clé API
Claude, ou choisir autre chose que Claude : OpenAI, Gemini…).

- Abonnement Claude (défaut) : rien ne change, le CLI `claude` et son compte.
- Clé API Claude : le CLI la prend par `apiKeyHelper` (`astrel/cle_api.py`), au-dessus de
  l'abonnement ; tout le reste est conservé. Facturée à l'usage : alerte de dépense, jamais
  d'arrêt automatique.
- Fournisseur à clé (OpenAI, Gemini, OpenRouter, Grok) : le CLI reste le cerveau, derrière la
  PASSERELLE locale (`astrel/passerelle.py`) ; `ANTHROPIC_BASE_URL` et le modèle changent.
  Jamais d'Autonome TOTAL ni de levée du garde-fou. Depuis le banc d'essai du 20/10 :
  CONVERSATION SEULE par défaut (`config.PASSERELLE_OUTILS`) ; avec les outils, mode PRUDENT exigé.

Clés : `config/secrets.json` (écriture atomique, 0600, lu aussi par `config.secret`) sous les
noms `CLE_API_*`. Jamais dans l'environnement du cerveau, la ligne de commande, les réglages,
le journal ni l'historique : elles arrivent du HUD par `settings_action` (valeur `value`,
emballée dans `Secret` par le cœur dès réception), ne repartent jamais vers le HUD.
Le choix lui-même est le réglage `fournisseur` (`runtime/settings.json`, protégé par le
garde-fou : Claude ne peut pas l'écrire).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from . import config

log = logging.getLogger("astrel.fournisseurs")


@dataclass(frozen=True)
class Fournisseur:
    id: str
    nom: str                 # court : « OpenAI », « Claude »
    cle: str = ""            # nom de la clé dans config/secrets.json ("" : aucune)
    passerelle: bool = False  # passe par la passerelle locale (non-Claude)
    modeles: tuple[tuple[str, str], ...] = ()  # (identifiant chez le fournisseur, libellé) ; le 1er : conseillé
    prefixe: str = ""        # début attendu d'une clé (contrôle de saisie seulement)
    oauth: str = ""          # nom de l'objet OAuth dans config/secrets.json (connexion par compte, plan P16)
    repli: str = ""          # fournisseur à clé pris si la connexion OAuth est refusée ou absente
    cli: bool = False        # plan P27 : le CLI officiel du fournisseur (`astrel/fournisseurs_cli/`)


CATALOGUE: dict[str, Fournisseur] = {f.id: f for f in (
    Fournisseur("abonnement", "Claude"),
    Fournisseur("cle_claude", "Claude", cle="CLE_API_ANTHROPIC", prefixe="sk-ant-"),
    Fournisseur("openai", "OpenAI", cle="CLE_API_OPENAI", passerelle=True, prefixe="sk-",
                modeles=(("gpt-5", "GPT-5"), ("gpt-5-mini", "GPT-5 mini"), ("gpt-4.1", "GPT-4.1"))),
    Fournisseur("gemini", "Gemini", cle="CLE_API_GEMINI", passerelle=True,
                modeles=(("gemini-2.5-pro", "Gemini 2.5 Pro"), ("gemini-2.5-flash", "Gemini 2.5 Flash"))),
    Fournisseur("openrouter", "OpenRouter", cle="CLE_API_OPENROUTER", passerelle=True, prefixe="sk-or-",
                modeles=(("openai/gpt-5", "GPT-5"), ("google/gemini-2.5-pro", "Gemini 2.5 Pro"),
                         ("qwen/qwen3-coder", "Qwen3 Coder"), ("deepseek/deepseek-chat-v3.1", "DeepSeek V3.1"),
                         ("moonshotai/kimi-k2", "Kimi K2"))),
    # Grok (21/10, plan P15 lot A ; demande de l'utilisateur : un ami abonné à Grok doit pouvoir le
    # brancher). Clé de console.x.ai, facturée à l'usage, DISTINCTE d'un abonnement Grok.
    # Noms lus dans la table de LiteLLM 1.105.1, pas encore confirmés par `GET /v1/models` avec
    # une vraie clé ; un nom plus récent s'ajoute par `ASTREL_FOURNISSEUR_MODELES`.
    Fournisseur("xai", "Grok", cle="CLE_API_XAI", passerelle=True, prefixe="xai-",
                modeles=(("grok-4.3", "Grok 4.3"), ("grok-4.20", "Grok 4.20"),
                         ("grok-code-fast-1", "Grok Code Fast 1"))),
    # Grok par abonnement : absent de ce dépôt (`astrel/xai_oauth.py` n'est qu'une interface vide),
    # ce choix n'est donc jamais proposé ; repli sur la clé API Grok.
    Fournisseur("xaiabo", "Grok (abonnement)", passerelle=True, oauth="OAUTH_XAI", repli="xai",
                modeles=(("grok-4.3", "Grok 4.3"), ("grok-4.20", "Grok 4.20"),
                         ("grok-code-fast-1", "Grok Code Fast 1"))),
    # Plan P27 (22/10 ; demande de l'utilisateur : « le plus d'IA connues utilisables sur Astrel ») : le
    # CLI OFFICIEL du fournisseur, avec la connexion que l'utilisateur y a faite (abonnement ChatGPT,
    # abonnement Grok). Proposés seulement si le CLI est installé ; REFUSÉS tant que le lot 0 n'a
    # rien prouvé (`fournisseurs_cli`, capacités). Modèles : celui du CLI par défaut, puis des noms
    # supposés (un nom plus récent s'ajoute par `ASTREL_FOURNISSEUR_MODELES`).
    Fournisseur("codex", "Codex", cli=True,
                modeles=(("defaut", "modèle par défaut du CLI"), ("gpt-5-codex", "GPT-5 Codex"), ("gpt-5", "GPT-5"))),
    Fournisseur("grokbuild", "Grok Build", cli=True,
                modeles=(("defaut", "modèle par défaut du CLI"), ("grok-code-fast-1", "Grok Code Fast 1"),
                         ("grok-4", "Grok 4"))),
)}
DEFAUT = "abonnement"
_ENV_PURGE = ("ANTHROPIC_", "CLE_API_")
_ENV_PURGE_EXACT = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
# Clés d'autres services : retirées seulement derrière la passerelle (le mode abonnement garde l'environnement tel quel).
_ENV_PURGE_PASSERELLE = ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY")


def _modeles(fid: str) -> tuple[tuple[str, str], ...]:
    """Modèles proposés ; `ASTREL_FOURNISSEUR_MODELES` ({"openai": ["gpt-5.1"]}) en ajoute (un nom
    sorti après cette liste) sans toucher au code."""
    base = CATALOGUE[fid].modeles
    try:
        extra = json.loads(os.environ.get("ASTREL_FOURNISSEUR_MODELES") or "{}").get(fid) or []
    except (ValueError, AttributeError):
        extra = []
    known = {m for m, _ in base}
    return base + tuple((str(m), str(m)) for m in extra if str(m) not in known)


def choix() -> list[tuple[str, str]]:
    """Valeurs du réglage `fournisseur` : « abonnement », « cle_claude », « openai:gpt-5 »…"""
    out = [("abonnement", "Abonnement Claude (connexion du compte)"),
           ("cle_claude", "Clé API Claude (facturée à l'usage)")]
    for f in CATALOGUE.values():
        if f.oauth:
            continue
        if f.passerelle:
            out += [(f"{f.id}:{m}", f"{f.nom} · {label} (clé API, sans Claude)") for m, label in _modeles(f.id)]
        elif f.cli and _cli_installe(f.id):  # plan P27 : seulement si le CLI est sur la machine
            out += [(f"{f.id}:{m}", f"{f.nom} · {label} (ton abonnement, par son CLI, sans Claude)")
                    for m, label in _modeles(f.id)]
    from . import xai_oauth

    if xai_oauth.accord():  # plan P16 : seulement une fois connecté ET l'avertissement accepté
        out += [(f"xaiabo:{m}", f"Grok · {label} (abonnement, sans Claude)")
                for m, label in _modeles("xaiabo")]
    return out


def lire(valeur: str) -> tuple[str, str]:
    """« openai:gpt-5 » → ("openai", "gpt-5") ; inconnu → l'abonnement."""
    fid, _, modele = str(valeur or "").partition(":")
    f = CATALOGUE.get(fid)
    if f is None:
        return DEFAUT, ""
    if not (f.passerelle or f.cli):
        return fid, ""
    if f.cli and modele.startswith("-"):  # P27 : le nom va dans l'argv du CLI, jamais une option
        modele = ""
    return fid, modele or f.modeles[0][0]


def _cli_installe(fid: str) -> bool:
    from . import fournisseurs_cli

    c = fournisseurs_cli.get(fid)
    return bool(c and c.installe())


def libelle(fid: str, modele: str = "") -> str:
    f = CATALOGUE.get(fid) or CATALOGUE[DEFAUT]
    if fid == "abonnement":
        return "Abonnement Claude"
    if fid == "cle_claude":
        return "Clé API Claude"
    return f"{f.nom} · {dict(_modeles(fid)).get(modele, modele)}"


# --------------------------------------------------------------------------- clés

class Secret:
    """Valeur secrète venue du HUD : `repr`/`str` masqués, pour qu'aucun journal (« commande %r
    en échec », refus de Claude…) ne la montre. `reveler()` la rend, au seul endroit qui l'écrit."""

    __slots__ = ("_v",)

    def __init__(self, value: Any) -> None:
        self._v = "" if value is None else str(value)

    def reveler(self) -> str:
        return self._v

    def __repr__(self) -> str:
        return "«clé masquée»"

    __str__ = __repr__

    def __bool__(self) -> bool:
        return bool(self._v)


_secrets_lock = threading.Lock()


def _lire_secrets() -> dict:
    try:
        raw = config.SECRETS_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    data = json.loads(raw)  # illisible : ValueError, rien n'est écrasé
    if not isinstance(data, dict):
        raise ValueError("config/secrets.json n'est pas un objet JSON")
    return data


def _ecrire_secrets(changes: dict[str, str | None]) -> None:
    """Écriture ATOMIQUE en 0600 (création comprise) ; les autres secrets sont gardés tels quels."""
    path = config.SECRETS_FILE
    with _secrets_lock:
        data = _lire_secrets()
        for k, v in changes.items():
            if v is None:
                data.pop(k, None)
            else:
                data[k] = v
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name("." + path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
        os.chmod(path, 0o600)


def a_une_cle(fid: str) -> bool:
    f = CATALOGUE.get(fid)
    if f is None or not f.cle:
        return False
    try:
        return bool(str(_lire_secrets().get(f.cle) or "").strip())
    except (OSError, ValueError):
        return False


def verifier_cle(fid: str, cle: str) -> str:
    """Message d'erreur (sans la clé) si la saisie ne ressemble pas à une clé ; "" sinon."""
    f = CATALOGUE.get(fid)
    if f is None or not f.cle:
        return "fournisseur sans clé"
    if not cle:
        return "clé vide"
    if len(cle) < 20 or len(cle) > 512:
        return "longueur inattendue pour une clé API"
    if not re.fullmatch(r"[\x21-\x7e]+", cle):
        return "caractères inattendus (espace, retour à la ligne, accent ?) : recopie la clé seule"
    if f.prefixe and not cle.startswith(f.prefixe):
        return f"une clé {f.nom} commence par « {f.prefixe} »"
    return ""


def enregistrer_cle(fid: str, cle: str) -> dict:
    err = verifier_cle(fid, cle)
    if err:
        return {"ok": False, "error": f"clé refusée : {err}."}
    try:
        _ecrire_secrets({CATALOGUE[fid].cle: cle})
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"clé non enregistrée : {type(exc).__name__} (config/secrets.json)."}
    log.info("clé API %s enregistrée", fid)  # jamais la clé, ni un morceau
    return {"ok": True}


def effacer_cle(fid: str) -> dict:
    f = CATALOGUE.get(fid)
    if f is None or not f.cle:
        return {"ok": False, "error": "fournisseur sans clé"}
    try:
        _ecrire_secrets({f.cle: None})
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"clé non effacée : {type(exc).__name__} (config/secrets.json)."}
    log.info("clé API %s effacée", fid)
    return {"ok": True}


# --------------------------------------------------------------------------- cerveau

@dataclass
class Effectif:
    """Fournisseur avec lequel le cerveau démarre (le choix, ou le repli s'il est impossible)."""
    genre: str               # abonnement, cle_claude, passerelle, cli (plan P27)
    id: str                  # id du CATALOGUE
    modele: str = ""         # identifiant chez le fournisseur (passerelle, cli)
    outils: bool = True      # faux : conversation seule (passerelle hors mode prudent, CLI non prouvé)
    note: str = ""           # pourquoi le choix n'a pas pu être pris, ou ce qui manque

    @property
    def cle(self) -> str:
        """NOM du secret que lit la passerelle : la clé, ou l'objet OAuth (plan P16)."""
        f = CATALOGUE[self.id]
        return f.cle or f.oauth

    @property
    def oauth(self) -> bool:
        return bool(CATALOGUE[self.id].oauth)

    @property
    def cle_api(self) -> bool:
        """Connexion par clé ou jeton (clé Claude, passerelle), Grok par abonnement compris : pour savoir ce
        qui est FACTURÉ À L'USAGE (alerte de dépense), voir `facturation`."""
        return self.genre in ("cle_claude", "passerelle")

    @property
    def facturation(self) -> str:
        """Plan P19 : `abonnement` (Claude, non facturé au token), `usage` (clé API : dépense et
        alerte), `forfait` (abonnement d'un autre fournisseur, Grok par abonnement : jamais compté en dollars).
        Même règle que `consommation.facturation`."""
        if self.genre == "abonnement":
            return "abonnement"
        return "forfait" if self.oauth or self.genre == "cli" else "usage"

    @property
    def session_key(self) -> str:
        """Une session ne se reprend (`--resume`) qu'avec le même fournisseur : abonnement et clé
        Claude partagent les mêmes modèles ; une session née ailleurs repart à neuf."""
        if self.genre == "cli":  # plan P27 : la conversation du CLI se reprend, modèle changé ou non
            return f"cli:{self.id}" + ("" if self.outils else ":sans-outil")
        if self.genre != "passerelle":
            return "claude"
        return f"passerelle:{self.id}:{self.modele}" + ("" if self.outils else ":sans-outil")

    def libelle(self) -> str:
        return libelle(self.id, self.modele)


def mode_prudent() -> bool:
    """Mode du garde-fou (LU seulement : `autonomy.json` ne s'écrit que par l'utilisateur)."""
    from . import guard

    return guard.get_mode() == "prudent"


def effectif(valeur: str | None = None) -> Effectif:
    """Fournisseur à prendre au démarrage du cerveau : le réglage, sauf impossibilité (clé absente,
    passerelle non installée) : alors l'abonnement, avec la raison dans `note`."""
    from . import passerelle
    from .settings import reglages

    fid, modele = lire(reglages.value("fournisseur") if valeur is None else valeur)
    f = CATALOGUE[fid]
    if fid == "abonnement":
        return Effectif("abonnement", fid)
    if f.oauth:
        return _effectif_oauth(f, modele)
    if f.cli:
        return _effectif_cli(f, modele)
    if not a_une_cle(fid):
        return Effectif("abonnement", DEFAUT, note=f"aucune clé {f.nom} enregistrée : abonnement Claude")
    if not f.passerelle:
        return Effectif("cle_claude", fid)
    if not passerelle.installed():
        return Effectif("abonnement", DEFAUT, note="passerelle non installée : abonnement Claude")
    if not outils_passerelle():
        return Effectif("passerelle", fid, modele, outils=False)
    if not mode_prudent():
        # Le cœur refuse ce choix hors mode prudent ; si le mode a changé par un autre chemin
        # (fichier modifié à la main), le modèle n'a AUCUN outil plutôt qu'un garde-fou desserré.
        return Effectif("passerelle", fid, modele, outils=False,
                        note="mode autonome : conversation seule, sans outil (passe en prudent pour les outils)")
    return Effectif("passerelle", fid, modele, outils=True)


def _effectif_oauth(f: Fournisseur, modele: str) -> Effectif:
    """Abonnement xAI (plan P16) : connexion refusée ou absente → la clé API du repli si elle est
    enregistrée (note explicite), sinon l'abonnement Claude. Toujours conversation seule."""
    from . import passerelle, xai_oauth

    raison = xai_oauth.raison_indisponible()
    if raison:
        r = CATALOGUE[f.repli] if f.repli else None
        if r is not None and a_une_cle(r.id):
            eff = effectif(f"{r.id}:{modele}")
            if eff.id == r.id:
                eff.note = f"{raison} : clé API {r.nom} à la place" + (f" ; {eff.note}" if eff.note else "")
                return eff
        return Effectif("abonnement", DEFAUT, note=f"{raison} : abonnement Claude")
    if not passerelle.installed():
        return Effectif("abonnement", DEFAUT, note="passerelle non installée : abonnement Claude")
    return Effectif("passerelle", f.id, modele, outils=False)


def _effectif_cli(f: Fournisseur, modele: str) -> Effectif:
    """Plan P27 : CLI absent, non prouvé (lot 0) ou non connecté → abonnement Claude, la raison
    dans `note`. `prouve` = le lot 0 a validé ce CLI au moins en conversation ; outils seulement si
    le REFUS du hook est prouvé ET en mode prudent, sinon conversation seule."""
    from . import fournisseurs_cli

    c = fournisseurs_cli.get(f.id)
    if c is None or not c.installe():
        return Effectif("abonnement", DEFAUT, note=f"CLI {f.nom} non installé : abonnement Claude")
    if not c.capacites.prouve:
        return Effectif("abonnement", DEFAUT,
                        note=f"{f.nom} par son CLI pas encore prouvé sur cette machine : abonnement Claude")
    if c.detecter().connecte is False:
        return Effectif("abonnement", DEFAUT, note=f"CLI {f.nom} non connecté à ton abonnement : abonnement Claude")
    if not fournisseurs_cli.outils_possibles(f.id):
        return Effectif("cli", f.id, modele, outils=False)
    if not mode_prudent():
        return Effectif("cli", f.id, modele, outils=False,
                        note="mode autonome : conversation seule, sans outil (passe en prudent pour les outils)")
    return Effectif("cli", f.id, modele, outils=True)


def prudent_exige() -> bool:
    """Un fournisseur non-Claude AVEC outils n'existe qu'en mode prudent (P11, P27) : outils de la
    passerelle permis, ou CLI choisi ou en marche dont le refus du hook est prouvé."""
    from . import fournisseurs_cli
    from .settings import reglages

    if outils_passerelle():
        return True
    ids = {lire(reglages.value("fournisseur"))[0], etat.actif.id}
    return any(CATALOGUE[i].cli and fournisseurs_cli.outils_possibles(i) for i in ids if i in CATALOGUE)


def outils_passerelle() -> bool:
    """Outils permis à un fournisseur non-Claude (`ASTREL_PASSERELLE_OUTILS`, faux depuis le banc
    du 20/10 : voir config.py) ; jamais hors mode prudent (`effectif`)."""
    return bool(config.PASSERELLE_OUTILS)


def refus_choix(valeur: str, total: bool, levee: bool) -> str:
    """Raison de refuser ce choix de fournisseur maintenant ("" : permis). Appelé par le cœur
    avant d'écrire le réglage (HUD ou terminal ; jamais Claude, refusé plus haut)."""
    from . import passerelle

    fid, _ = lire(valeur)
    f = CATALOGUE[fid]
    if fid == "abonnement":
        return ""
    if f.cli:
        return _refus_cli(f, total, levee)
    if f.oauth:
        from . import xai_oauth

        if not xai_oauth.accord():
            return "connecte-toi d'abord avec xAI et accepte l'avertissement (bouton plus bas)"
        raison = xai_oauth.raison_indisponible()
        if raison:
            return f"{raison} : reconnecte-toi avec xAI, ou prends la clé API Grok"
    elif not a_une_cle(fid):
        return f"enregistre d'abord ta clé {f.nom} (bouton plus bas)"
    if not f.passerelle:
        return ""
    if not passerelle.installed():
        return "installe d'abord la passerelle (bouton plus bas)"
    if total:
        return "coupe d'abord le mode Sans confirmation : jamais avec un modèle autre que Claude"
    if levee:
        return "rétablis d'abord le garde-fou : jamais levé avec un modèle autre que Claude"
    if outils_passerelle() and not f.oauth and not mode_prudent():
        return "passe d'abord le garde-fou en mode PRUDENT (une question pour chaque action sensible) : " \
               "exigé avec un modèle autre que Claude"
    return ""


def _refus_cli(f: Fournisseur, total: bool, levee: bool) -> str:
    """Plan P27 : CLI installé, prouvé (lot 0) et connecté ; puis les règles de tout non-Claude."""
    from . import fournisseurs_cli

    c = fournisseurs_cli.get(f.id)
    if c is None or not c.installe():
        return f"installe d'abord le CLI {f.nom} et connecte-toi avec ton abonnement"
    if not c.capacites.prouve:
        return f"{f.nom} par son CLI n'est pas encore prouvé sur cette machine (plan P27, lot 0) : choix refusé"
    if c.detecter().connecte is False:
        return f"connecte d'abord le CLI {f.nom} à ton abonnement, dans ton terminal"
    if total:
        return "coupe d'abord le mode Sans confirmation : jamais avec un modèle autre que Claude"
    if levee:
        return "rétablis d'abord le garde-fou : jamais levé avec un modèle autre que Claude"
    if fournisseurs_cli.outils_possibles(f.id) and not mode_prudent():
        return "passe d'abord le garde-fou en mode PRUDENT (une question pour chaque action sensible) : " \
               "exigé avec un modèle autre que Claude"
    return ""


def non_claude_choisi() -> bool:
    from .settings import reglages

    fid, _ = lire(reglages.value("fournisseur"))
    return CATALOGUE[fid].passerelle or CATALOGUE[fid].cli


def env_cerveau(base: dict, genre: str, url: str = "", modele: str = "") -> dict:
    """Environnement du process `claude` : AUCUNE variable Anthropic ni clé de la machine (une
    ANTHROPIC_API_KEY du shell passait en silence et primait sur l'abonnement), aucune clé
    `CLE_API_*` ; la clé passe par `apiKeyHelper`. Passerelle : adresse locale et modèle."""
    # Plan P27 : un CLI non-Claude non plus ne voit aucune clé d'API (elle primerait sur l'abonnement).
    purge = _ENV_PURGE_EXACT + (_ENV_PURGE_PASSERELLE if genre in ("passerelle", "cli") else ())
    env = {k: v for k, v in base.items() if not (k.startswith(_ENV_PURGE) or k in purge)}
    if genre == "passerelle":
        env.update(ANTHROPIC_BASE_URL=url,
                   # Tous les alias (cerveau, sous-agents, petites requêtes de fond) visent le modèle choisi.
                   ANTHROPIC_DEFAULT_OPUS_MODEL=modele, ANTHROPIC_DEFAULT_SONNET_MODEL=modele,
                   ANTHROPIC_DEFAULT_HAIKU_MODEL=modele, ANTHROPIC_SMALL_FAST_MODEL=modele,
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    return env


def helper(genre: str) -> str:
    """Commande `apiKeyHelper` du CLI ("" : abonnement). Seuls des CHEMINS y figurent."""
    if genre == "cle_claude":
        args = ("anthropic", config.SECRETS_FILE)
    elif genre == "passerelle":
        args = ("passerelle", config.PASSERELLE_JETON_FILE)
    else:
        return ""
    # 04/11 (CI Windows) : -I ignore PYTHONUTF8 ; sans `-X utf8`, les messages de `cle_api.py` (« aucune clé
    # enregistrée (Réglages › …) ») partaient en cp1252 et le CLI, comme tout lecteur UTF-8, ne les lisait pas.
    # Mêmes options que `config.hook_python_args` sous Windows.
    mots = (config.VENV_PYTHON, "-I", *(("-X", "utf8") if os.name == "nt" else ()), config.CLE_API_SCRIPT, *args)
    if os.name == "nt":
        # 03/11 (CI Windows) : cmd.exe ne connaît pas les « '…' » de shlex (« syntaxe du nom de fichier
        # incorrecte ») ; entre « "…" », un chemin à « \\ » se lit pareil dans cmd.exe et dans Git Bash.
        return " ".join(f'"{p}"' if re.search(r"[^\w.:=+-]", str(p)) else str(p) for p in mots)
    return " ".join(shlex.quote(str(p)) for p in mots)


class Etat:
    """Ce que le cerveau a pris à son dernier démarrage (`Brain.start`), pour les Réglages et le HUD."""

    def __init__(self) -> None:
        self.actif = Effectif("abonnement", DEFAUT)
        self.on_change: Callable[[], Any] | None = None
        self.installing = ""  # dernière ligne de pip pendant l'installation de la passerelle
        self.install_msg: tuple[str, str] = ("", "")


etat = Etat()


# --------------------------------------------------------------------------- dépense (alerte seule)

class Depense:
    """Dépense estimée du JOUR en mode clé API : `total_cost_usd` du CLI (estimation au prix
    Claude ; pour un autre fournisseur, l'ordre de grandeur seulement). Le CLI le donne CUMULÉ
    pour son process : on n'ajoute que la différence. ALERTE une fois par jour au-delà du seuil,
    jamais d'arrêt (choix de l'utilisateur, comme les boucles)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: tuple[int, float] = (0, 0.0)  # (process, cumul déjà compté)

    def _lire(self) -> dict:
        try:
            data = json.loads(config.DEPENSE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        today = time.strftime("%Y-%m-%d")
        if not isinstance(data, dict) or data.get("jour") != today:
            data = {"jour": today, "usd": 0.0, "alerte": False}
        return data

    def jour(self) -> float:
        with self._lock:
            return float(self._lire().get("usd") or 0.0)

    def noter(self, cumul: Any, process: int, seuil: float) -> float | None:
        """Rend la dépense du jour si elle VIENT de passer le seuil (alerte à donner), sinon None."""
        if not isinstance(cumul, (int, float)) or isinstance(cumul, bool) or cumul < 0:
            return None
        with self._lock:
            last_proc, last = self._last
            delta = cumul - last if process == last_proc and cumul >= last else cumul
            self._last = (process, float(cumul))
            data = self._lire()
            data["usd"] = round(float(data.get("usd") or 0.0) + max(0.0, delta), 6)
            alert = seuil > 0 and data["usd"] >= seuil and not data.get("alerte")
            if alert:
                data["alerte"] = True
            try:
                config.DEPENSE_FILE.write_text(json.dumps(data), encoding="utf-8")
            except OSError:
                log.warning("dépense non retenue : %s", config.DEPENSE_FILE)
            return data["usd"] if alert else None


depense = Depense()


# --------------------------------------------------------------------------- Réglages › Fournisseur

def _usd(v: float) -> str:
    return f"{v:,.2f} $".replace(",", " ").replace(".", ",")


def vue() -> dict:
    """Ligne « Clés et passerelle » (genre `action`). Jamais une clé, ni un morceau de clé."""
    from . import passerelle
    from .settings import reglages

    a = etat.actif
    suffixe = ""
    if a.genre == "passerelle":
        suffixe = " par la passerelle, mode prudent" if a.outils else " par la passerelle, conversation seule"
    elif a.genre == "cli":  # plan P27
        suffixe = " par son CLI, mode prudent" if a.outils else " par son CLI, conversation seule"
    lines: list[dict] = [{"label": "Cerveau", "text": a.libelle() + suffixe,
                          "level": "warn" if a.genre in ("passerelle", "cli") else "ok"}]
    if a.note:
        lines.append({"label": "Attention", "text": a.note, "level": "warn"})
    actions: list[dict] = []
    for f in CATALOGUE.values():
        if not f.cle:
            continue
        has = a_une_cle(f.id)
        nom = "Claude" if f.id == "cle_claude" else f.nom
        lines.append({"label": f"Clé {nom}", "text": "enregistrée sur cette machine" if has else "aucune",
                      "level": "ok" if has else "info"})
        actions.append({"id": f"enregistrer_{f.id}", "label": f"{'Changer' if has else 'Enregistrer'} la clé {nom}",
                        "input": {"label": f"Clé API {nom}", "secret": True},
                        "confirm": "Colle la clé : elle reste sur cette machine (config/secrets.json, lisible par toi "
                                   "seul), n'est jamais affichée ni écrite au journal."})
        if has:
            actions.append({"id": f"effacer_{f.id}", "label": f"Effacer la clé {nom}", "danger": True,
                            "confirm": f"La clé {nom} sera effacée de cette machine ; si Astrel l'utilise, il "
                                       "repassera sur l'abonnement Claude au prochain démarrage du cerveau."})
    # Abonnement xAI (plan P16) : état non secret, code court pendant la connexion, jamais un jeton.
    from . import xai_oauth

    lines += xai_oauth.lignes()
    actions += xai_oauth.actions()
    lines += lignes_cli()
    lien = xai_oauth.lien()
    version = passerelle.installed_version()
    if etat.installing:
        lines.append({"label": "Passerelle", "text": f"installation en cours… {etat.installing}", "level": "info"})
    elif passerelle.installed():
        state = f"en marche ({passerelle.passerelle.url})" if passerelle.passerelle.alive else "installée, arrêtée"
        lines.append({"label": "Passerelle", "level": "ok",
                      "text": f"{state} ; LiteLLM {version}, venv à part : {config.PASSERELLE_VENV}"})
    else:
        lines.append({"label": "Passerelle", "level": "info",
                      "text": "non installée (≈ 270 Mo) : nécessaire pour OpenAI, Gemini, OpenRouter et Grok, pas pour Claude"
                              + (f" ; version {version} trouvée, {config.PASSERELLE_LITELLM} attendue" if version else "")})
        actions.append({"id": "installer", "label": "Installer la passerelle",
                        "confirm": f"Télécharge {config.PASSERELLE_LITELLM} (version fixée, ≈ 270 Mo) dans une venv à "
                                   f"part : {config.PASSERELLE_VENV}. Quelques minutes."})
    msg, level = etat.install_msg
    if msg:
        lines.append({"label": "Installation", "text": msg, "level": level})
    spent = depense.jour()
    if a.facturation == "usage" or spent:
        seuil = float(reglages.value("depense_alerte_usd"))
        lines.append({"label": "Dépense du jour", "level": "warn" if seuil and spent >= seuil else "info",
                      "text": f"≈ {_usd(spent)} (estimation du CLI)" + (f", alerte à {_usd(seuil)}" if seuil else "")})
    value: dict = {"lines": lines}
    if lien:
        value["link"] = lien
    return {"value": value, "actions": actions}


def lignes_cli() -> list[dict]:
    """Plan P27 : une ligne par fournisseur CLI (installé, version, connexion, prouvé). Jamais
    d'identifiant ni de jeton ; la connexion se fait dans le terminal de l'utilisateur."""
    from . import fournisseurs_cli

    out: list[dict] = []
    for f in CATALOGUE.values():
        c = fournisseurs_cli.get(f.id) if f.cli else None
        if c is None:
            continue
        det = c.detecter()
        if not det.installe:
            out.append({"label": f"CLI {f.nom}", "text": "non installé", "level": "info"})
            continue
        parts = [f"installé, version {det.version}" if det.version else "installé, version inconnue"]
        if det.connecte is True:
            parts.append("connecté à ton abonnement")
        elif det.connecte is False:
            parts.append(f"non connecté : dans ton terminal, {c.commande_connexion}")
        else:
            parts.append("connexion non vérifiable")
        if not c.capacites.prouve:
            parts.append("pas encore prouvé sur cette machine (plan P27, lot 0) : pas encore proposé au choix")
        elif not fournisseurs_cli.outils_possibles(f.id):
            parts.append("conversation seule (refus du hook non prouvé)")
        out.append({"label": f"CLI {f.nom}", "text": " · ".join(parts),
                    "level": "ok" if c.capacites.prouve and det.connecte is not False else "info"})
    return out


def run(action: str, cmd: dict | None = None) -> dict:
    cmd = cmd or {}
    if cmd.get("_from_claude") is True:
        return {"ok": False, "error": "les clés se règlent depuis le HUD, jamais par Claude"}
    if action == "installer":
        return _installer()
    if action in ("connecter_xai", "annuler_xai", "deconnecter_xai"):
        # Plan P16 : le mot « accepter » sous l'avertissement est vérifié par `reglages.action`.
        from . import xai_oauth

        return {"connecter_xai": xai_oauth.connecter, "annuler_xai": xai_oauth.annuler,
                "deconnecter_xai": xai_oauth.deconnecter}[action]()
    for prefix, fn in (("enregistrer_", None), ("effacer_", effacer_cle)):
        if not action.startswith(prefix):
            continue
        fid = action[len(prefix):]
        f = CATALOGUE.get(fid)
        if f is None or not f.cle:
            break
        nom = "Claude" if fid == "cle_claude" else f.nom
        if fn is not None:
            res = fn(fid)
            return {**res, "text": f"Clé {nom} effacée."} if res.get("ok") else res
        if cmd.get("_via") != "hud":
            # Une clé tapée dans `--send` resterait dans l'historique du shell et dans `ps`.
            return {"ok": False, "error": "la clé se colle dans le HUD (Réglages › Fournisseur), jamais en ligne de commande"}
        value = cmd.get("value")
        cle = (value.reveler() if isinstance(value, Secret) else str(value or "")).strip()
        res = enregistrer_cle(fid, cle)
        if not res.get("ok"):
            return res
        return {"ok": True, "level": "ok",
                "text": f"Clé {nom} enregistrée. Choisis-la dans « Fournisseur » ci-dessus, puis applique."}
    return {"ok": False, "error": f"action inconnue : {action or '(vide)'}"}


def _installer() -> dict:
    from . import passerelle

    if etat.installing:
        return {"ok": False, "error": "installation déjà en cours"}
    if passerelle.installed():
        return {"ok": True, "text": "Passerelle déjà installée.", "level": "info"}
    etat.installing, etat.install_msg = "démarrage", ("", "")

    def progress(line: str) -> None:
        etat.installing = line
        _changed()

    def work() -> None:
        try:
            res = passerelle.install(progress)
        except Exception as exc:  # noqa: BLE001 — jamais de fil mort en silence
            log.exception("installation de la passerelle")
            res = {"ok": False, "error": str(exc)}
        etat.installing = ""
        etat.install_msg = ("passerelle installée", "ok") if res.get("ok") else (str(res.get("error")), "error")
        _changed()

    threading.Thread(target=work, name="passerelle-install", daemon=True).start()
    _changed()
    return {"ok": True, "level": "info", "text": "Installation lancée : quelques minutes, la ligne se met à jour."}


def _changed() -> None:
    if etat.on_change:
        try:
            etat.on_change()
        except Exception:  # noqa: BLE001
            log.exception("page Réglages non republiée")


def masquer_commande(cmd: dict) -> dict:
    """Appelé par le cœur DÈS réception d'une commande : la valeur d'un bouton de clé est
    emballée dans `Secret` (le dict est modifié EN PLACE : le serveur du HUD, qui le journalise
    s'il échoue, tient le même objet)."""
    name = str(cmd.get("cmd", "")).strip().lower()
    if name == "settings_action" and str(cmd.get("key") or "").startswith("fournisseur") \
            and "value" in cmd and not isinstance(cmd["value"], Secret):
        cmd["value"] = Secret(cmd["value"])
    return cmd

