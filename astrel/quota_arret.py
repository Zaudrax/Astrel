"""Arrêt automatique des agents sur quota (24/10, plan P34, demande de l'utilisateur).

« À 95 % de la fenêtre de 5 h, le cœur fait noter leur état à tous les agents vivants ; à 98 % il
les arrête proprement ; à la reprise, Astrel met à jour CHANTIERS-EN-COURS et propose de relancer. »

- Quota lu par le flux (`rate_limit_event`) et le relevé `/usage` du CLI (`quota_releve`, 0 jeton),
  jamais `~/.claude/.credentials.json` ni l'API OAuth. Fenêtre de 5 h SEULE (question 1 du plan :
  l'hebdomadaire à 98 % couperait tout pendant des jours). Une décision n'est prise que sur un
  relevé FRAIS (`FRAIS_S`) : un pourcentage vieux de 10 min ne fait ni noter ni arrêter.
- Phases (`runtime/quota-arret.json`, écrit de façon atomique, fait foi après une relance du cœur) :
  « veille » (rien), « note » (ordre posé : `runtime/quota-ordre.json`, lu par `quota_hook.py` qui
  le glisse UNE fois à chaque sous-agent, avant son prochain outil), « arret » (chaque sous-agent
  est arrêté par `stop_task` après son accusé — l'appel de l'outil `passation` — ou `QUOTA_ACK_WAIT_S`
  au plus, et après son écriture de fichier en cours, `RELIEF_STOP_WAIT_S` au plus ; le cœur note
  ce qu'il sait de lui, cette note fait foi même sans accusé ; aucun nouvel agent, `route_hook`).
  Les commandes Bash de fond ne sont PAS arrêtées : elles ne consomment pas le quota.
- Plan P39 (26/10, arrêt plus doux) : la passation et le courrier en cours sont attendus comme une
  écriture ; un agent sans accusé mais OCCUPÉ (Bash… : il ne lira l'ordre qu'à la fin de son outil)
  a un délai de grâce (`QUOTA_GRACE_S`, coupé net à `QUOTA_GRACE_PCT`, sur relevé périmé ou ESTOP).
  Pendant la note ou l'arrêt, le relais d'équipe (testeur, relecteur) est suspendu (`RELAIS_SUSPENDU`).
- Fin de la fenêtre (heure de réinitialisation passée, ou nouvelle fenêtre lue) : ordre retiré ;
  s'il y a des agents arrêtés, à `reset_at` + `REPRISE_DELAY_S`, selon le réglage `quota_reprise`
  (27/10, demande de l'utilisateur : « la relance doit être AUTOMATIQUE ») :
  « auto » (défaut) : dès qu'un relevé FRAIS, pris après la fin de la fenêtre, montre la fenêtre de
  5 h sous `seuil_relance` (60 %, marge sous le seuil de note), UN message source « quota » fait
  relancer les agents (note CHANTIERS mise à jour dans le même tour) ; ceux qui touchent les mêmes
  fichiers partent par lots, le lot suivant quand plus aucun agent ne tourne. Un agent coupé en
  pleine commande ou sans passation est marqué « à vérifier d'abord ». Relevé `/usage` (0 jeton)
  demandé au plus chaque minute ; sans relevé sous le seuil en `REPRISE_AUTO_ATTENTE_S`, autre
  fenêtre presque pleine (hebdomadaire ≥ `AUTRES_MAX_PCT`) ou reprise après limite atteinte en
  cours : repli sur la carte.
  « carte » : UN tour (jamais dit) fait mettre la note à jour, puis une carte (`choices.ask`, genre
  « quota ») propose « Tout relancer / Choisir / Non » ; rien n'est relancé sans clic. La carte
  est posée dans le canal OUVERT du HUD (27/10 : posée dans Général alors que l'onglet Astrel était
  ouvert, elle était cachée, un toast passager seulement) et annoncée par une notice.
  « rien » : le tour de note seul, puis la liste est oubliée.
  La reprise après limite atteinte (`reprise.py`) partie avant : pas de second tour.
- Inactif : réglage `quota_arret` = off, mode léger, hors abonnement Claude. L'arrêt d'urgence
  prime : sous ESTOP, ordre retiré et liste jetée (rien ne sera proposé).
- Seul le cœur écrit ces fichiers (25/10, audit p33-p34, constats M1 à M3 ; protégés aussi par le
  garde-fou, mais le code n'en dépend pas : le garde-fou peut être levé). L'ORDRE n'est honoré
  qu'avec une échéance (`until`) à venir et à moins de `ORDRE_MAX_S` ; chaque seconde, le cœur
  retire un ordre qu'il n'a pas posé et rétablit le sien s'il a changé. L'ÉTAT est signé HMAC
  (clé `quota-cle` à côté, 0600, secret du garde-fou) : un état non signé par le cœur est ignoré,
  ses descriptions et consignes ne vont jamais au cerveau ni sur la carte « Tout relancer ».
  Écritures sans suivre de lien (mkstemp, lien refusé) ; seules les marques `*.note`/`*.arret` du
  dossier des marques sont effacées, et jamais à travers un lien.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from . import config
from .events import bus
from .settings import reglages

log = logging.getLogger("astrel.quota_arret")

MODES = ("off", "note", "arret")
REPRISES = ("auto", "carte", "rien")  # réglage `quota_reprise` (27/10)
FRAIS_S = 180.0          # un relevé plus vieux ne décide rien
REPRISE_AUTO_PCT = 60.0  # relance automatique seulement sous ce remplissage de la fenêtre de 5 h…
REPRISE_AUTO_MARGE = 20.0  # … et au moins 20 points sous le seuil de note
AUTRES_MAX_PCT = 90.0    # une autre fenêtre (hebdomadaire) plus pleine : carte, pas de relance seule
REPRISE_AUTO_ATTENTE_S = 600.0  # sans relevé frais sous le seuil après ce délai : carte
RELEVE_ECART_S = 60.0    # relevé `/usage` demandé au plus une fois par minute pendant l'attente
LOT_ECART_S = 120.0      # lot suivant : au moins 2 min après le précédent, et plus aucun agent vivant
HYSTERESIS = 5.0         # en note : l'ordre n'est retiré que 5 points sous le seuil (arrondi de /usage)
APPROCHE = 10.0          # relevé rapide dès « seuil de note − 10 » quand des agents tournent
PERIODE_PROCHE_S = 120.0
PERIODE_NOTE_S = 60.0
# Échéance d'un ordre : fenêtre de 5 h + 15 min de marge (heure de /usage recalée). Un ordre sans
# échéance, échu ou plus lointain n'est pas honoré (M1 : un « arret » forgé sans `until` bloquait
# les agents et la relève pour toujours). Même valeur dans `quota_hook.py` (bibliothèque standard).
ORDRE_MAX_S = 5 * 3600 + 900
FICHIER_CLE = "quota-cle"   # à côté de `config.QUOTA_ARRET_FILE` ; `guard.QUOTA_KEY_FILE`
_MARQUE = re.compile(r"[A-Za-z0-9_-]{1,80}\.(?:note|arret)")  # nom posé par `quota_hook.py`

# --- textes pour Claude (les ordres glissés aux agents sont dans `quota_hook.py`) ---

TOUR_REPRISE = (
    "[Reprise après l'arrêt pour quota, l'utilisateur n'a rien demandé.] La limite de 5 h est levée. Astrel avait "
    "arrêté ces agents avant la coupure ; mets à jour MAINTENANT la note CHANTIERS-EN-COURS (memory_write) : "
    "marque-les « arrêtés pour quota, relance proposée à l'utilisateur » avec leur dernière étape et leurs fichiers. "
    "Ne relance RIEN : l'utilisateur choisit sur une carte. Réponds ensuite seulement [SILENT].\n{items}\n"
)
RELANCE = (
    "[l'utilisateur a choisi de relancer ces agents, arrêtés pour le quota : relance-les maintenant avec leur "
    "consigne, ceux de cette liste seulement. Dis à chacun ce qui est déjà fait (dernière étape, fichiers, "
    "sa passation au journal d'équipe s'il en a écrit une) pour qu'il reprenne sans refaire ; un agent coupé "
    "pendant une commande vérifie d'abord l'état qu'elle a laissé.]\n{items}\n"
    "Relance-les, puis réponds seulement « Agents relancés. »"
)
# 27/10 (demande de l'utilisateur) : relance automatique au retour du quota, réglage `quota_reprise` = auto.
RELANCE_AUTO = (
    "[Relance automatique après l'arrêt pour quota, voulue par l'utilisateur (réglage « relance auto »), l'utilisateur "
    "n'a rien demandé à l'instant.] La limite de 5 h est levée, la fenêtre est revenue à {pct} %. Relance "
    "MAINTENANT ces agents, arrêtés pour le quota, avec leur consigne, ceux de cette liste seulement. Dis à chacun "
    "ce qui est déjà fait (dernière étape, fichiers, sa passation au journal d'équipe s'il en a écrit une) pour "
    "qu'il reprenne sans refaire ; un agent marqué « à vérifier d'abord » commence par vérifier l'état des "
    "fichiers qu'il touchait et de la commande coupée (écriture peut-être partielle) avant d'écrire quoi que ce "
    "soit. Règles habituelles : un seul écrivain par fichier, une seule tâche à la fois sur la carte graphique "
    "(entraînement, banc) ; s'ils se gênent, lance-les l'un après l'autre.{suite} Mets aussi à jour la note "
    "CHANTIERS-EN-COURS (memory_write) : « relancés automatiquement après le quota ». Réponds ensuite seulement "
    "[SILENT].\n{items}\n"
)
RELANCE_AUTO_SUITE = (" D'autres agents arrêtés pour le quota touchent les mêmes fichiers : le cœur les relancera "
                      "quand ceux-ci auront fini, ne les lance pas toi-même.")
# Réglage `quota_reprise` = rien : la note seule, rien de proposé.
TOUR_NOTE_RIEN = (
    "[Reprise après l'arrêt pour quota, l'utilisateur n'a rien demandé.] La limite de 5 h est levée. Astrel avait "
    "arrêté ces agents avant la coupure ; mets à jour MAINTENANT la note CHANTIERS-EN-COURS (memory_write) : "
    "marque-les « arrêtés pour quota, pas relancés (réglage de l'utilisateur) » avec leur dernière étape et leurs "
    "fichiers. Ne relance RIEN. Réponds ensuite seulement [SILENT].\n{items}\n"
)
A_VERIFIER = ("coupé en pleine commande ou sans passation : vérifier l'état des fichiers touchés et de la "
              "commande avant de continuer")
# Plan P39, étape 7 : le relais d'équipe lancé en pleine note avait fait naître le testeur coupé du 26/10.
RELAIS_SUSPENDU = (
    "[Équipe, message automatique du cœur] Le codeur a fini (« {desc} »), mais le quota de l'abonnement est à "
    "{pct} % de la fenêtre de 5 h : ne lance PAS le suivant (ni testeur ni relecteur). Note-le dans "
    "CHANTIERS-EN-COURS (memory_write) : relais à faire après la levée de la limite ({relais}). "
    "Réponds ensuite seulement [SILENT]."
)
RELAIS_SUSPENDU_VU = "Équipe : relais suspendu, quota à {pct} %."
TOUT = "Tout relancer"
CHOISIR = "Choisir"
NON = "Non"
AUCUN = "Aucun"
QUEL = "Quel agent relancer ? Avec « Autre », tu peux donner plusieurs numéros (ex. 1 et 4)."
_CONSIGNE_MAX = 8_000


# --- lecture du quota ---

def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def fenetre_5h(q: dict) -> tuple[float | None, str, float]:
    """`Brain.quota` → (remplissage de la fenêtre de 5 h en %, clé de fenêtre, heure de
    réinitialisation en epoch s). (None, "", 0) si la fenêtre de 5 h est absente."""
    w = ((q or {}).get("windows") or {}).get("five_hour")
    if not isinstance(w, dict):
        return None, "", 0.0
    u = _num(w.get("utilization"))
    if u is None:
        return None, "", 0.0
    pct = u * 100 if u <= 1 else u
    if q.get("status") == "rejected" and q.get("type") == "five_hour":
        pct = max(pct, 100.0)
    reset = w.get("resetsAt")
    from .brain import epoch_s  # import local : brain importe beaucoup
    return round(pct, 2), str(reset if reset is not None else ""), float(epoch_s(reset) or 0.0)


def reglage() -> tuple[str, float, float]:
    """(mode, seuil de note, seuil d'arrêt) ; l'arrêt est toujours au-dessus de la note."""
    mode = str(reglages.value("quota_arret") or "off")
    mode = mode if mode in MODES else "off"
    note = float(reglages.value("quota_note_pct"))
    stop = float(reglages.value("quota_stop_pct"))
    return mode, note, min(100.0, max(stop, note + 1))


def reglage_reprise() -> str:
    """« auto » (défaut), « carte » ou « rien » : ce qui suit la levée de la limite (27/10)."""
    try:
        mode = str(reglages.value("quota_reprise") or "auto")
    except KeyError:  # réglage absent (ancienne table) : la demande de l'utilisateur, relance auto
        mode = "auto"
    return mode if mode in REPRISES else "auto"


def seuil_relance(note: float) -> float:
    """Remplissage de la fenêtre de 5 h sous lequel la relance automatique part (marge sous la note)."""
    return max(10.0, min(REPRISE_AUTO_PCT, note - REPRISE_AUTO_MARGE))


def autres_fenetres(q: dict) -> float:
    """Remplissage le plus haut (en %) des fenêtres autres que celle de 5 h, 0 si aucune."""
    best = 0.0
    for name, w in (((q or {}).get("windows") or {}).items()):
        if name == "five_hour" or not isinstance(w, dict):
            continue
        u = _num(w.get("utilization"))
        if u is not None and math.isfinite(u):
            best = max(best, u * 100 if u <= 1 else u)
    return best


def a_verifier(a: dict) -> bool:
    """Relance risquée sans vérification : coupé pendant un outil, ou sans passation avec des fichiers."""
    if a.get("outil_en_cours"):
        return True
    return bool(a.get("fichiers")) and "passation de l'agent" not in str(a.get("note") or "")


def lots(agents: list[dict]) -> tuple[list[dict], list[dict]]:
    """(premier lot, reste) : un seul agent par fichier touché ; l'ordre de la liste est gardé."""
    pris, reste, vus = [], [], set()
    for a in agents:
        fichiers = {str(f) for f in (a.get("fichiers") or []) if f}
        if fichiers & vus:
            reste.append(a)
            continue
        vus |= fichiers
        pris.append(a)
    return pris, reste


def phase_voulue(pct: float, mode: str, note: float, stop: float) -> str:
    if mode == "off" or pct < note:
        return "veille"
    if mode == "arret" and pct >= stop:
        return "arret"
    return "note"


def meme_fenetre(a: str, b: str) -> bool:
    """Même tolérance que `Astrel._same_window` (heure du texte de /usage ≠ heure du flux)."""
    if not a or not b:
        return True  # clé inconnue d'un côté : on ne conclut pas à une nouvelle fenêtre
    if a == b:
        return True
    from .brain import epoch_s
    from .quota_releve import RECALAGE_S
    x, y = epoch_s(a), epoch_s(b)
    return bool(x and y) and abs(x - y) <= RECALAGE_S


# --- fichiers ---

def _ecrire(path: Path, data: dict) -> None:
    """Écriture atomique 0600 qui ne suit aucun lien (B1) : fichier temporaire à nom unique
    (mkstemp : O_EXCL, O_NOFOLLOW), dossier ou cible en lien symbolique refusés. Lève OSError."""
    if path.parent.is_symlink() or path.is_symlink():
        raise OSError(f"{path.name} : lien symbolique refusé")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _lire(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# --- état signé (M2) : la clé est un secret du garde-fou, lue et créée par le cœur seul ---

def _cle_path() -> Path:
    """À côté de l'état : un état de test (`config.QUOTA_ARRET_FILE` redirigé) n'utilise jamais la vraie clé."""
    return config.QUOTA_ARRET_FILE.with_name(FICHIER_CLE)


def _cle(creer: bool) -> bytes | None:
    """Clé HMAC : fichier ordinaire à nous, 0600, lu sans suivre de lien. Absente ou douteuse :
    None, ou refaite si `creer` (l'état signé avec l'ancienne n'est alors plus reconnu)."""
    path = _cle_path()
    try:
        st = os.lstat(path)
        if stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o077:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as fh:
                key = fh.read(4096)
            if len(key) >= 32:
                return key
    except FileNotFoundError:
        pass
    except OSError:
        if not creer:
            return None
    if not creer:
        return None
    key = secrets.token_bytes(32)
    if path.parent.is_symlink():
        raise OSError(f"{path.parent.name} : lien symbolique refusé")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)  # lien, droits trop larges, trop courte : refaite (un lien n'est pas suivi)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    log.info("arrêt pour quota : nouvelle clé de l'état")
    return key


def _signature(key: bytes, data: dict) -> str:
    corps = json.dumps({k: v for k, v in data.items() if k != "sig"}, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")).encode("utf-8")
    return hmac.new(key, b"astrel-quota-arret\0" + corps, hashlib.sha256).hexdigest()


def _lire_etat() -> dict:
    """État écrit par le cœur, {} sinon : absent, illisible, signature absente ou fausse (M2 : un
    état forgé portait des consignes reprises dans les messages au cerveau et la carte de relance)."""
    path = config.QUOTA_ARRET_FILE
    if path.is_symlink():
        log.warning("arrêt pour quota : état ignoré (lien symbolique)")
        return {}
    d = _lire(path)
    if not d:
        return {}
    key = _cle(creer=False)
    sig = d.get("sig")
    try:
        # Octets comparés (2e passage, relecteur : `compare_digest` lève TypeError sur une chaîne non
        # ASCII) ; un demi-caractère (« \ud800 ») fait lever l'encodage : état non signé, jamais une panne.
        ok = key is not None and isinstance(sig, str) and hmac.compare_digest(
            sig.encode("utf-8"), _signature(key, d).encode("ascii"))
    except (TypeError, ValueError):
        ok = False
    if not ok:
        log.warning("arrêt pour quota : état ignoré (signature absente ou fausse : pas écrit par le cœur)")
        return {}
    return d


def _ordre_valide(d: dict, now: float) -> bool:
    """M1 : phase connue ET échéance obligatoire, à venir, à moins de `ORDRE_MAX_S`."""
    if d.get("phase") not in ("note", "arret"):
        return False
    until = _num(d.get("until"))
    return until is not None and math.isfinite(until) and now < until <= now + ORDRE_MAX_S


def lire_ordre(now: float | None = None) -> dict:
    """Ordre en cours ({phase, pct, stop, until, …}) ou {} : absent, illisible, périmé, sans
    échéance ou à échéance trop lointaine (pas posé par le cœur)."""
    now = time.time() if now is None else now
    if config.QUOTA_ORDRE_FILE.is_symlink():
        return {}
    d = _lire(config.QUOTA_ORDRE_FILE)
    return d if _ordre_valide(d, now) else {}


def ordre_actif(now: float | None = None) -> bool:
    """Phase note ou arrêt en cours (la relève de session attend, `relief.py`)."""
    return bool(lire_ordre(now))


def refus(quoi: str = "nouvel agent", now: float | None = None) -> str:
    """Raison de refuser un NOUVEL agent (`route_hook`), « » sinon : phase d'arrêt seulement."""
    d = lire_ordre(now)
    if d.get("phase") != "arret":
        return ""
    return (f"Quota de l'abonnement à {d.get('pct', '?')} % de la fenêtre de 5 h : {quoi} refusé, Astrel arrête "
            "proprement les agents en cours jusqu'à la levée de la limite. Dis-le à l'utilisateur en une phrase ; "
            "fais le travail toi-même seulement s'il est court, sinon attends la levée.")


def relais_suspendu(desc: str, relais: str, now: float | None = None) -> tuple[str, str] | None:
    """Plan P39 : (message pour Astrel, ligne affichée) remplaçant le relais testeur/relecteur
    (`Astrel._equipe_after`) quand un ordre de quota est en cours (note ou arrêt) ; None sinon."""
    d = lire_ordre(now)
    if not d:
        return None
    pct = _num(d.get("pct"))
    pct_txt = str(round(pct)) if pct is not None and math.isfinite(pct) else "?"
    return (RELAIS_SUSPENDU.format(desc=str(desc or "codeur")[:120], pct=pct_txt, relais=str(relais or "")[:300].rstrip(". ")),
            RELAIS_SUSPENDU_VU.format(pct=pct_txt))


def _vider_marques() -> None:
    """Efface les marques `*.note`/`*.arret` posées par `quota_hook.py`, RIEN d'autre (M3 : le
    dossier remplacé par un lien vers un projet en faisait tout effacer). Dossier en lien : refusé."""
    d = config.QUOTA_ORDRE_DIR
    if d.is_symlink():
        log.warning("arrêt pour quota : marques non effacées, %s est un lien symbolique", d.name)
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    par_fd = os.unlink in os.supports_dir_fd and os.scandir in os.supports_fd
    try:
        fd = os.open(d, flags) if par_fd else None
    except FileNotFoundError:
        return
    except OSError as exc:  # lien posé entre-temps (ELOOP), pas un dossier
        log.warning("arrêt pour quota : marques non effacées (%s)", exc)
        return
    try:
        for entry in os.scandir(fd if fd is not None else d):
            if _MARQUE.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                try:
                    if fd is not None:
                        os.unlink(entry.name, dir_fd=fd)
                    else:
                        os.unlink(os.path.join(d, entry.name))
                except OSError:
                    pass
    except OSError:
        pass
    finally:
        if fd is not None:
            os.close(fd)


def lignes(agents: list[dict]) -> str:
    """Liste des agents arrêtés, comme `relief.list_block` (consigne, étape, fichiers, note)."""
    out = []
    for n, a in enumerate(agents, 1):
        head = f"{n}. « {a.get('desc') or 'tâche'} » ({a.get('type') or 'agent'}" + (
            f", canal {a['canal']}" if a.get("canal") else "") + ")"
        parts = [head]
        consigne = str(a.get("consigne") or "")
        if len(consigne) > _CONSIGNE_MAX:
            consigne = consigne[:_CONSIGNE_MAX] + " […]"
        for label, value in (("consigne", consigne), ("dernière étape", a.get("etape")),
                             ("fichiers touchés", ", ".join(a.get("fichiers") or [])),
                             ("coupé pendant", a.get("outil_en_cours")), ("note", a.get("note")),
                             ("à vérifier d'abord", A_VERIFIER if a_verifier(a) else "")):
            if value:
                parts.append(f"{label} : {value}")
        out.append(" ; ".join(parts))
    return "\n".join(out)


def _dicts(value: Any) -> list[dict]:
    """Liste de dicts lue dans l'état (types invalides ignorés : fichier abîmé ou écrit à la main)."""
    return [a for a in value if isinstance(a, dict)] if isinstance(value, list) else []


def _chaines(value: Any) -> set[str]:
    return {x for x in value if isinstance(x, str)} if isinstance(value, list) else set()


_NUMEROS = re.compile(r"^\s*\d{1,3}(?:\s*(?:,|;|/|\+|&|\bet\b|\band\b)?\s*\d{1,3})*\s*\.?\s*$", re.I)


def numeros(texte: str, n: int) -> list[int] | None:
    """Texte libre « Autre » : indices (0..n-1) si c'est une liste de numéros PURE et valide
    (« 1, 3 », « 2 et 4 »), None sinon (« ne relance pas le 2 » n'est pas une liste)."""
    if not _NUMEROS.match(texte or ""):
        return None
    vus = [int(x) for x in re.findall(r"\d+", texte)]
    if not vus or any(not 0 < x <= n for x in vus):
        return None
    return sorted({x - 1 for x in vus})


def _court(agents: list[dict], n: int = 3) -> str:
    noms = [f"« {str(a.get('desc') or 'tâche')[:60]} »" for a in agents[:n]]
    return ", ".join(noms) + (f" et {len(agents) - n} autre(s)" if len(agents) > n else "")


# --- le cycle, tenu par le cœur ---

class QuotaArret:
    def __init__(self, core: Any) -> None:
        self.core = core
        self._lock = threading.RLock()
        self._ordre: dict | None = None  # dernier ordre posé par CE processus (M1)
        self._ordre_ko = False           # écriture de l'ordre en échec : déjà journalisé
        d = _lire_etat()
        self.phase = d.get("phase") if d.get("phase") in ("veille", "note", "arret") else "veille"
        self.window = str(d.get("window") or "")
        self.reset_at = float(_num(d.get("reset_at")) or 0.0)
        self.arret_at = float(_num(d.get("arret_at")) or 0.0)
        self.pct = float(_num(d.get("pct")) or 0.0)
        self.until = float(_num(d.get("until")) or 0.0)  # fin de l'ordre (heure de réinitialisation inconnue)
        self.agents: list[dict] = _dicts(d.get("agents"))
        self.acks: set[str] = _chaines(d.get("acks"))            # accusés reçus DANS la phase en cours
        self.acks_note: set[str] = _chaines(d.get("acks_note"))  # accusés de la phase « note » (plus anciens)
        reprise = d.get("reprise") if isinstance(d.get("reprise"), dict) else {}
        if reprise:
            reprise = {**reprise, "agents": _dicts(reprise.get("agents"))}
        self.reprise: dict = reprise
        self._ready: dict[str, float] = {}   # tid → heure où il pouvait être arrêté (accusé ou délai)
        self._vu: dict[str, float] = {}      # tid → première fois vu en phase d'arrêt par CE processus
        self._grace: dict[str, float] = {}   # tid → début de son délai de grâce (plan P39), en mémoire
        self._sent: set[str] = {str(a.get("tid")) for a in self.agents if a.get("tid")}
        self._card_reasked = False
        self._essai_at: float | None = None  # premier essai réel de relance automatique (mémoire seule)

    # --- état ---

    def _save(self) -> None:
        data = {"phase": self.phase, "window": self.window, "reset_at": self.reset_at, "arret_at": self.arret_at,
                "pct": self.pct, "until": self.until, "agents": self.agents, "acks": sorted(self.acks),
                "acks_note": sorted(self.acks_note), "reprise": self.reprise,
                "updated": time.time()}
        try:
            key = _cle(creer=True)
            if key is None:
                raise OSError("clé de l'état illisible")
            data["sig"] = _signature(key, data)
            _ecrire(config.QUOTA_ARRET_FILE, data)
        except OSError as exc:
            log.warning("état de l'arrêt pour quota non écrit : %s", exc)

    def _poser_ordre(self, phase: str, note: float, stop: float, now: float) -> None:
        until = self.reset_at if self.reset_at > now else (self.until if self.until > now else now + 5 * 3600)
        self.until = until
        # Échéance bornée (M1) : un ordre plus lointain que `ORDRE_MAX_S` ne serait pas honoré.
        ordre = {"phase": phase, "pct": self.pct, "note": note, "stop": stop,
                 "since": now, "until": min(until, now + ORDRE_MAX_S), "window": self.window}
        self._ordre = ordre
        try:
            if not config.QUOTA_ORDRE_DIR.is_symlink():  # en lien : le hook n'y écrira pas (M3)
                config.QUOTA_ORDRE_DIR.mkdir(parents=True, exist_ok=True)
            _ecrire(config.QUOTA_ORDRE_FILE, ordre)
            self._ordre_ko = False
        except OSError as exc:
            self._echec_ordre("ordre de quota non posé : %s", exc)

    def _echec_ordre(self, msg: str, exc: OSError) -> None:
        """Échec d'écriture de l'ordre : un avertissement, pas un par seconde (rejoué par `_garder_ordre`)."""
        if not getattr(self, "_ordre_ko", False):
            log.warning(msg, exc)
        self._ordre_ko = True

    def _retirer_ordre(self) -> None:
        self._ordre = None
        try:
            config.QUOTA_ORDRE_FILE.unlink(missing_ok=True)  # un lien est retiré, sa cible jamais touchée
        except OSError:
            log.warning("ordre de quota non retiré : %s", config.QUOTA_ORDRE_FILE)
        _vider_marques()

    def _garder_ordre(self, now: float) -> None:
        """M1, chaque seconde : l'ordre sur le disque est celui du cœur, ou rien. En veille, un ordre
        présent (forgé, ou laissé par un cœur arrêté net) est retiré ; en note ou en arrêt, un ordre
        absent ou changé est reposé (après une relance du cœur : reposé depuis l'état signé)."""
        path = config.QUOTA_ORDRE_FILE
        if self.phase not in ("note", "arret"):
            if os.path.lexists(path):
                log.warning("arrêt pour quota : ordre qui n'est pas celui du cœur retiré")
                self._retirer_ordre()
            return
        if self._ordre is None:
            _mode, note, stop = reglage()
            self._poser_ordre(self.phase, note, stop, now)
            return
        if not _ordre_valide(self._ordre, now):
            # Horloge reculée (échéance à plus de `ORDRE_MAX_S`) ou avancée (échue, phase pas finie) :
            # les lecteurs ne l'honorent plus, le cœur pose un ordre neuf, échéance recalculée.
            o = self._ordre
            self._poser_ordre(self.phase, float(_num(o.get("note")) or 0.0), float(_num(o.get("stop")) or 0.0), now)
            return
        if path.is_symlink() or _lire(path) != self._ordre:
            if not getattr(self, "_ordre_ko", False):
                log.warning("arrêt pour quota : ordre changé hors du cœur, reposé")
            attendu = self._ordre
            try:
                path.unlink(missing_ok=True)
                _ecrire(path, attendu)
                self._ordre_ko = False
            except OSError as exc:
                self._echec_ordre("ordre de quota non reposé : %s", exc)

    def _inactif(self) -> str:
        """Raison de ne rien faire (« » : actif). L'arrêt d'urgence est traité à part."""
        j = self.core
        eff = getattr(getattr(j, "brain", None), "fournisseur", None)
        if eff is not None and getattr(eff, "genre", "abonnement") != "abonnement":
            return "hors abonnement Claude"
        local = getattr(j, "_local_active", None)
        if callable(local) and local():
            return "mode léger"
        return ""

    def _frais(self, now: float) -> bool:
        at = _num(getattr(getattr(self.core, "brain", None), "quota_at", 0.0)) or 0.0
        return now - at <= FRAIS_S

    def _agents_vivants(self) -> dict[str, dict]:
        j = self.core
        with j._tasks_lock:
            return {tid: dict(t) for tid, t in j._tasks.items() if t.get("kind") == "agent"}

    # --- entrées du cœur ---

    def periode(self, agents: bool) -> float:
        """Période du relevé rapide (`Astrel._quota_tick`), 0 si inutile : des agents tournent et la
        fenêtre de 5 h approche du seuil de note (120 s), ou l'a passé (60 s). `/usage` seul."""
        if not agents or config.ESTOP_FILE.exists() or self._inactif():
            return 0.0
        mode, note, _stop = reglage()
        if mode == "off" or not reglages.value("quota_refresh"):
            return 0.0  # « Suivre le quota hors d'Astrel » coupé : pas de relevé de plus, le flux seul
        pct, _key, _reset = fenetre_5h(dict(getattr(self.core.brain, "quota", None) or {}))
        if pct is None:
            return 0.0
        if pct >= note:
            return PERIODE_NOTE_S
        return PERIODE_PROCHE_S if pct >= note - APPROCHE else 0.0

    def observe(self, q: dict, now: float | None = None) -> str:
        """Chaque quota reçu (`Astrel._guard_quota`) : rend la phase après décision."""
        now = time.time() if now is None else now
        with self._lock:
            if config.ESTOP_FILE.exists():
                if self.phase != "veille" or self.agents or self.reprise:
                    self._estop()
                return self.phase
            mode, note, stop = reglage()
            if mode == "off" or self._inactif():
                if self.phase != "veille":
                    log.info("arrêt pour quota : %s, ordre retiré", "réglage coupé" if mode == "off" else self._inactif())
                    self._fin_fenetre(now)
                return self.phase
            pct, key, reset = fenetre_5h(q)
            if pct is None or not self._frais(now):
                return self.phase
            if self.phase != "veille" and not meme_fenetre(key, self.window):
                self._fin_fenetre(now)  # nouvelle fenêtre lue avant l'heure prévue
            voulue = phase_voulue(pct, mode, note, stop)
            if self.phase == "veille":
                if voulue != "veille" and reset and reset <= now:
                    # Relevé plein mais heure de réinitialisation déjà passée (fiche 5) : pendant la bascule
                    # (tolérance de `meme_fenetre`), la fenêtre se termine, le relevé suivant tranche ; au-delà,
                    # l'heure est fausse (fuseau, texte de /usage) : le pourcentage prime, heure inconnue
                    # (l'ordre expire après 5 h ; un relevé sous la note ou une nouvelle fenêtre lève la phase).
                    from .quota_releve import RECALAGE_S
                    if now - reset <= RECALAGE_S:
                        return self.phase
                    log.info("arrêt pour quota : heure de réinitialisation déjà passée (%s) à %.0f %%, tenue pour "
                             "inconnue", key, pct)
                    reset = 0.0
                if voulue != "veille":
                    self.window, self.reset_at, self.pct = key, reset, pct
                    self._noter(now, note, stop)
                    if voulue == "arret":
                        self._arreter(now, note, stop)
            elif self.phase == "note":
                self.pct = pct
                if voulue == "arret":
                    self._arreter(now, note, stop)
                elif pct < note - HYSTERESIS:
                    log.info("arrêt pour quota : fenêtre redescendue à %.0f %%, ordre retiré", pct)
                    self._fin_fenetre(now)
                else:
                    self._poser_ordre("note", note, stop, now)  # pourcentage à jour pour le hook
                    self._save()
            elif pct < note:
                # En arrêt, un relevé frais sous le seuil de note : la fenêtre s'est vidée (heure de
                # réinitialisation inconnue ou fausse), la phase est levée.
                log.info("arrêt pour quota : fenêtre redescendue à %.0f %% pendant l'arrêt, phase levée", pct)
                self._fin_fenetre(now)
            else:  # arret : on y reste jusqu'à la fin de la fenêtre
                self.pct = max(self.pct, pct)
            return self.phase

    def _noter(self, now: float, note: float, stop: float) -> None:
        self.phase = "note"
        self._poser_ordre("note", note, stop, now)
        self._save()
        n = len(self._agents_vivants())
        log.info("arrêt pour quota : fenêtre de 5 h à %.0f %%, %d agent(s) invités à noter leur état", self.pct, n)
        if n:
            bus.publish("notice", level="info",
                        text=f"Quota de 5 h à {round(self.pct)} % : j'ai demandé aux {n} agent(s) en cours de noter "
                             f"leur état ; à {round(stop)} %, je les arrête proprement.")

    def _arreter(self, now: float, note: float, stop: float) -> None:
        self.phase, self.arret_at = "arret", now
        self._ready, self._grace = {}, {}
        self._vu = {tid: now for tid in self._agents_vivants()}  # délai d'accusé compté d'ici pour eux
        # Un accusé de la note (95 %) est périmé : à l'arrêt, chaque agent en donne un neuf.
        self.acks_note, self.acks = self.acks_note | self.acks, set()
        self._poser_ordre("arret", note, stop, now)
        self._save()
        n = len(self._agents_vivants())
        log.info("arrêt pour quota : fenêtre de 5 h à %.0f %%, %d agent(s) à arrêter", self.pct, n)
        if n:
            quand = ""
            if self.reset_at > now:
                from . import reprise
                quand = f" ({reprise.heure(self.reset_at)})"
            if reglage_reprise() == "auto":
                text = (f"Quota de 5 h à {round(self.pct)} % : j'arrête proprement {n} agent(s). Je les relancerai "
                        f"tout seul quand la limite se lèvera{quand}.")
            else:
                text = (f"Quota de 5 h à {round(self.pct)} % : j'arrête proprement {n} agent(s). Je te proposerai "
                        f"de les relancer quand la limite se lèvera{quand}.")
            bus.publish("notice", level="warning", text=text)

    def ack(self, scope: str) -> None:
        """Un sous-agent a appelé `passation` (`Astrel._note_agent_tool`) : son accusé."""
        if not scope:
            return
        with self._lock:
            if self.phase in ("note", "arret") and scope not in self.acks:
                self.acks.add(scope)
                self._save()
                log.info("arrêt pour quota : accusé (passation) de l'agent %s", scope)

    def tick(self, now: float | None = None) -> str:
        """Chaque seconde (`_mission_loop`) : arrêt des agents, fin de fenêtre, reprise."""
        now = time.time() if now is None else now
        envoi: tuple[str, str] | None = None
        with self._lock:
            if config.ESTOP_FILE.exists():
                if self.phase != "veille" or self.agents or self.reprise:
                    return self._estop()
                self._garder_ordre(now)
                return ""
            step = ""
            fin = self.reset_at if self.reset_at else self.until  # heure inconnue : l'ordre expire (5 h)
            if self.phase in ("note", "arret") and fin and now >= fin:
                self._fin_fenetre(now)
                step = "fin"
            self._garder_ordre(now)
            if self.phase == "arret":
                step = self._arret_step(now) or step
            if self.reprise:
                reprise_step, envoi = self._reprise_step(now)
                step = reprise_step or step
        if envoi:
            # Hors du verrou : `handle` peut attendre un verrou que tient le fil du cerveau, lequel
            # appelle `ack`/`task_ended` (verrou d'ici) en traitant ses évènements.
            self.core.handle(envoi[0], source="quota", shown=envoi[1])
        return step

    def _arret_step(self, now: float) -> str:
        j = self.core
        wait = float(config.QUOTA_ACK_WAIT_S)
        to_stop: list[str] = []
        for tid, task in self._agents_vivants().items():
            if tid in self._sent:
                continue
            tuid = str(task.get("tool_use_id") or "")
            acked = tuid in self.acks
            # Délai compté depuis l'arrêt, ou depuis la première fois que CE processus voit l'agent
            # (relance du cœur en pleine phase, agent apparu ensuite) : chacun a sa chance d'accuser.
            depuis = max(self.arret_at, self._vu.setdefault(tid, now))
            if not acked and now - depuis < wait:
                continue
            if not acked and self._en_grace(tid, task, tuid, now, depuis + wait):
                continue  # plan P39 : commande en cours, il n'a pas encore pu lire l'ordre
            ready = self._ready.setdefault(tid, now)
            if j._agent_writing(tuid) and now - ready < config.RELIEF_STOP_WAIT_S:
                continue  # écriture (fichier, passation, courrier) en cours : on la laisse finir (plafonné)
            entry = j._task_journal(tid) or _entree(task)
            entry["note"] = ("passation de l'agent au journal d'équipe" if acked
                             else "passation de l'agent au journal d'équipe à la note, rien de plus récent : la note du "
                                  "cœur complète" if tuid in self.acks_note
                             else "arrêté sans note de l'agent après le délai de grâce : la note du cœur fait foi"
                             if tid in self._grace
                             else "arrêté sans note de l'agent : la note du cœur fait foi")
            entry["quota"] = True
            self.agents.append(entry)
            self._sent.add(tid)
            to_stop.append(tid)
        if not to_stop:
            return ""
        self._save()  # la liste fait foi AVANT l'arrêt
        self._stop(to_stop)
        return "stop"

    def _en_grace(self, tid: str, task: dict, tuid: str, now: float, debut: float) -> bool:
        """Plan P39 (26/10 : un testeur coupé en pleine commande, sans fiche) : vrai tant qu'un agent
        sans accusé reçoit un délai de grâce. Accordé à la fin de l'attente de l'accusé s'il a un
        outil en cours (`Astrel._agent_busy` : il ne lira l'ordre qu'à la fin de cet outil), jusqu'à
        `debut + QUOTA_GRACE_S`. Coupé net sur un relevé périmé, à `QUOTA_GRACE_PCT` ou au-delà, sous
        ESTOP. Son accusé (passation) le fait arrêter dès la fiche rendue (`_agent_writing`)."""
        fin = debut + float(config.QUOTA_GRACE_S)
        if self._grace.get(tid, 0.0) < 0 or now >= fin:
            return False  # grâce coupée (définitif) ou écoulée
        if config.ESTOP_FILE.exists() or not self._frais(now) or self.pct >= float(config.QUOTA_GRACE_PCT):
            if tid in self._grace:
                log.info("arrêt pour quota : délai de grâce de l'agent %s coupé (%s)", tid,
                         "arrêt d'urgence" if config.ESTOP_FILE.exists()
                         else "relevé du quota périmé" if not self._frais(now) else f"fenêtre à {self.pct:.0f} %")
                self._grace[tid] = -1.0  # une seule ligne, et pas de reprise sur un relevé suivant
            return False
        if tid not in self._grace:
            busy = getattr(self.core, "_agent_busy", None)
            if not (callable(busy) and busy(tuid)):
                return False
            self._grace[tid] = now
            desc = str(task.get("desc") or "tâche")[:80]
            minutes = max(1, math.ceil((fin - now) / 60))
            log.info("arrêt pour quota : agent %s sans accusé, outil en cours : délai de grâce de %.0f s au plus",
                     tid, fin - now)
            bus.publish("notice", level="info",
                        text=f"Quota de 5 h à {round(self.pct)} % : « {desc} » finit sa commande en cours. Je "
                             f"l'arrête dès sa passation, dans {minutes} min au plus.")
        return True

    def _stop(self, tids: list[str]) -> None:
        """`stop_task` attend jusqu'à 5 s la réponse du CLI : hors de la boucle principale."""
        def run() -> None:
            for tid in tids:
                ok = self.core.brain.stop_task(tid)
                log.info("arrêt pour quota : tâche %s %s", tid, "arrêtée" if ok else "refusée par le CLI")

        threading.Thread(target=run, name="quota-stop", daemon=True).start()

    def task_ended(self, tid: str, status: str) -> bool:
        """Fin d'une tâche (`Astrel._on_task`) : vrai si l'arrêt pour quota l'a arrêtée (ni échec
        ni relais d'équipe). Finie d'elle-même entre-temps : retirée de la liste, pas à relancer."""
        with self._lock:
            if tid not in self._sent:
                return False
            if status == "completed":
                self.agents = [a for a in self.agents if a.get("tid") != tid]
                self._sent.discard(tid)
                self._save()
                return False
            return True

    def _fin_fenetre(self, now: float) -> None:
        self._retirer_ordre()
        if self.agents:
            at = max(self.reset_at, now) + float(config.REPRISE_DELAY_S)
            old = [a for a in (self.reprise or {}).get("agents") or [] if isinstance(a, dict)]
            # `fin` : un relevé pris avant n'est pas celui de la nouvelle fenêtre (relance automatique).
            self.reprise = {"at": at, "agents": old + self.agents, "arret_at": self.arret_at, "pct": self.pct,
                            "tour": False, "card": "", "fin": now}
            self._card_reasked = False
            self._essai_at = None
            log.info("arrêt pour quota : fenêtre finie, %d agent(s) à proposer à la relance", len(self.agents))
        self.phase, self.window, self.reset_at, self.arret_at, self.pct, self.until = "veille", "", 0.0, 0.0, 0.0, 0.0
        self.agents, self.acks, self.acks_note, self._ready, self._sent, self._vu = [], set(), set(), {}, set(), {}
        self._grace = {}
        self._save()

    def _estop(self) -> str:
        n = len(self.agents) + len((self.reprise or {}).get("agents") or [])
        self._retirer_ordre()
        self.phase, self.window, self.reset_at, self.arret_at, self.pct, self.until = "veille", "", 0.0, 0.0, 0.0, 0.0
        self.agents, self.acks, self.acks_note, self._ready, self._sent, self.reprise = [], set(), set(), {}, set(), {}
        self._vu, self._grace = {}, {}
        self._save()
        log.info("arrêt pour quota : arrêt d'urgence, ordre retiré%s", f", {n} agent(s) non proposés" if n else "")
        return "estop"

    # --- reprise : un tour pour la note, puis la carte ---

    def _reprise_step(self, now: float) -> tuple[str, tuple[str, str] | None]:
        """(étape, message à envoyer au cerveau hors du verrou ou None)."""
        r = self.reprise
        agents = _dicts(r.get("agents"))
        if not agents:
            self.reprise = {}
            self._save()
            return "", None
        if r.get("tour"):
            if r.get("auto"):
                return self._lot_suivant(now, agents)  # relance automatique par lots en cours
            if not self._card_reasked:
                # Carte perdue à la relance du cœur (questions gardées en mémoire) : reposée une fois.
                self._card_reasked = True
                self._carte()
                return "carte", None
            return "", None
        if now < float(_num(r.get("at")) or 0.0):
            return "", None
        if reglage_reprise() == "auto" and not self._inactif():
            # Relecture du 27/10 : le délai avant le repli sur la carte part du PREMIER essai réel (en
            # mémoire), pas de `at` (Astrel relancé tard : carte aussitôt, sans un seul relevé) ; le relevé
            # (0 jeton) est demandé même pendant un tour, seul l'envoi attend le calme.
            if self._essai_at is None:
                self._essai_at = now
            if not self._releve_bas(now, r):
                self._demander_releve(now)
        if not self._calme():
            return "", None  # on attend le calme (et le retour sur Claude)
        from . import reprise
        info = reprise.state.info()
        arret_at = float(_num(r.get("arret_at")) or 0.0)
        # La limite a vraiment été atteinte : la reprise de `reprise.py` (la demande de l'utilisateur coupée
        # par la limite) part ou est partie, on ne l'annule JAMAIS ; ici, la carte seule.
        deja = bool(info.get("pending")) or float(_num(info.get("limit_at")) or 0.0) >= arret_at > 0
        mode = reglage_reprise()
        notice = ""
        if mode == "auto":
            if deja:
                notice = ("La limite de 5 h est levée. La reprise après limite atteinte est déjà en cours : pour ne "
                          "rien lancer deux fois, la relance des agents arrêtés pour le quota t'attend sur une carte.")
            else:
                verdict, pct = self._quota_pour_relance(now, r)
                if verdict == "attendre":
                    return "", None
                if verdict == "ok":
                    return self._relance_auto(now, agents, pct)
                if verdict == "autres":
                    notice = (f"La limite de 5 h est levée, mais une autre limite de l'abonnement est à {round(pct)} % : "
                              "je ne relance pas seul les agents arrêtés pour le quota, la carte t'attend.")
                else:
                    notice = ("La limite de 5 h est levée, mais je n'ai pas pu lire un quota assez bas pour relancer "
                              "seul les agents arrêtés pour le quota : la carte t'attend.")
                log.info("arrêt pour quota : relance automatique impossible (%s), carte", verdict)
        r["tour"] = True
        self._card_reasked = True
        self._save()  # noté AVANT l'envoi : une coupure juste après ne relance pas le tour
        envoi = None
        if deja:
            log.info("arrêt pour quota : reprise après limite atteinte en cours ou partie, carte seule")
        elif mode == "rien":
            envoi = (TOUR_NOTE_RIEN.format(items=lignes(agents)),
                     f"Reprise après l'arrêt pour quota : note des chantiers mise à jour ({len(agents)} agent(s)).")
        else:
            envoi = (TOUR_REPRISE.format(items=lignes(agents)),
                     f"Reprise après l'arrêt pour quota : note des chantiers mise à jour ({len(agents)} agent(s)).")
        if mode == "rien" and not deja:
            self.reprise = {}
            self._save()
            log.info("arrêt pour quota : %d agent(s) non relancés (réglage « rien »)", len(agents))
            bus.publish("notice", level="info",
                        text=f"La limite de 5 h est levée. {len(agents)} agent(s) arrêtés pour le quota ne sont pas "
                             "relancés (réglage) ; ils restent notés dans la note des chantiers.")
            return "reprise", envoi
        self._carte(notice)
        return "reprise", envoi

    def _calme(self) -> bool:
        j = self.core
        return not (j._turn_active or j._pending_confirm is not None or j._floor_taken() or self._inactif())

    # --- relance automatique (27/10, réglage `quota_reprise` = auto) ---

    def _quota_pour_relance(self, now: float, r: dict) -> tuple[str, float]:
        """(« ok » | « attendre » | « autres » | « illisible », pourcentage) : la relance automatique
        exige un relevé FRAIS pris après la fin de la fenêtre, sous `seuil_relance`. Sinon on attend
        (relevé `/usage` demandé, 0 jeton) jusqu'à `REPRISE_AUTO_ATTENTE_S`, puis la carte."""
        q = dict(getattr(getattr(self.core, "brain", None), "quota", None) or {})
        _mode, note, _stop = reglage()
        pct, _key, _reset = fenetre_5h(q)
        if self._releve_frais(now, r) and pct is not None:
            autres = autres_fenetres(q)
            if autres >= AUTRES_MAX_PCT:
                return "autres", autres
            if pct < seuil_relance(note):
                return "ok", pct
        if self._essai_at is None:
            self._essai_at = now
        if now - self._essai_at >= REPRISE_AUTO_ATTENTE_S:
            return "illisible", pct if pct is not None else 0.0
        self._demander_releve(now)
        return "attendre", pct if pct is not None else 0.0

    def _releve_frais(self, now: float, r: dict) -> bool:
        """Relevé pris après la fin de la fenêtre et encore frais (`FRAIS_S`)."""
        at = float(_num(getattr(getattr(self.core, "brain", None), "quota_at", 0.0)) or 0.0)
        fin = float(_num(r.get("fin")) or 0.0) or (float(_num(r.get("at")) or 0.0) - float(config.REPRISE_DELAY_S))
        return at >= fin and self._frais(now)

    def _releve_bas(self, now: float, r: dict) -> bool:
        """Relevé frais de la nouvelle fenêtre sous `seuil_relance` : plus besoin d'en demander un."""
        pct = fenetre_5h(dict(getattr(getattr(self.core, "brain", None), "quota", None) or {}))[0]
        return pct is not None and self._releve_frais(now, r) and pct < seuil_relance(reglage()[1])

    def _demander_releve(self, now: float) -> None:
        """Relevé `/usage` (0 jeton, jamais la sonde) hors du verrou, au plus une fois par minute ;
        rien si « Suivre le quota hors d'Astrel » est coupé (le flux seul décidera) ou interdit."""
        if now - getattr(self, "_releve_at", 0.0) < RELEVE_ECART_S:
            return
        self._releve_at = now
        j = self.core
        refresh, allowed = getattr(j, "_quota_refresh", None), getattr(j, "_quota_refresh_allowed", None)
        if not callable(refresh) or not reglages.value("quota_refresh"):
            return
        if (callable(allowed) and allowed()) or getattr(j, "__dict__", {}).get("_quota_refreshing"):
            return
        log.info("arrêt pour quota : relevé du quota demandé avant la relance automatique")
        threading.Thread(target=refresh, kwargs={"sonde": False}, name="quota-releve-reprise", daemon=True).start()

    def _relance_auto(self, now: float, agents: list[dict], pct: float) -> tuple[str, tuple[str, str] | None]:
        # Relecture du 27/10 : un agent déjà relancé à la main (même description qu'un agent vivant)
        # n'est pas relancé une seconde fois (doublon, deux écrivains sur les mêmes fichiers).
        vivants = {str(t.get("desc") or "") for t in self._agents_vivants().values()} - {""}
        doublons = [a for a in agents if str(a.get("desc") or "") in vivants]
        agents = [a for a in agents if a not in doublons]
        if doublons:
            log.info("arrêt pour quota : %d agent(s) déjà relancés (même description qu'un agent vivant), retirés",
                     len(doublons))
        if not agents:
            self.reprise = {}
            self._save()
            bus.publish("notice", level="info",
                        text=f"Quota de 5 h revenu : les agents arrêtés pour le quota tournent déjà ({_court(doublons)}), "
                             "rien à relancer.")
            return "doublon", None
        lot, reste = lots(agents)
        r = self.reprise
        r.update({"tour": True, "auto": True, "agents": reste, "lot_at": now, "card": ""})
        self._card_reasked = True
        if not reste:
            self.reprise = {}
        # Noté AVANT l'envoi : une coupure juste après ne relance pas deux fois. Limite connue (relecture
        # du 27/10) : `Astrel.handle` avale un échec de `brain.send` (message marqué « non envoyé » dans le
        # HUD, bouton Renvoyer) sans le rendre ; ce lot n'est alors pas reproposé.
        self._save()
        pct_txt = str(round(pct))
        log.info("arrêt pour quota : relance automatique de %d agent(s) (fenêtre de 5 h à %s %%), %d en attente",
                 len(lot), pct_txt, len(reste))
        bus.publish("notice", level="info",
                    text=f"Quota de 5 h revenu à {pct_txt} % : je relance {len(lot)} agent(s) arrêté(s) pour le quota : "
                         f"{_court(lot)}.")
        texte = RELANCE_AUTO.format(pct=pct_txt, items=lignes(lot), suite=RELANCE_AUTO_SUITE if reste else "")
        return "relance", (texte, f"Relance automatique de {len(lot)} agent(s) arrêté(s) pour le quota.")

    def _lot_suivant(self, now: float, agents: list[dict]) -> tuple[str, tuple[str, str] | None]:
        """Lot suivant d'une relance automatique : quand plus aucun agent ne tourne, au calme, la
        fenêtre toujours en veille (un nouvel arrêt pour quota reprend la liste à sa fin)."""
        if self.phase != "veille" or now - float(_num(self.reprise.get("lot_at")) or 0.0) < LOT_ECART_S:
            return "", None
        if self._agents_vivants() or not self._calme():
            return "", None
        _mode, note, _stop = reglage()
        pct, _key, _reset = fenetre_5h(dict(getattr(getattr(self.core, "brain", None), "quota", None) or {}))
        if pct is not None and pct >= seuil_relance(note):
            return "", None  # la fenêtre s'est remplie entre-temps : on attend (l'arrêt pour quota veille)
        return self._relance_auto(now, agents, pct or 0.0)

    def _carte(self, notice: str = "") -> None:
        """Carte de relance dans le canal OUVERT du HUD (27/10 : posée dans Général pendant que
        l'onglet Astrel était ouvert, elle restait cachée), annoncée par une notice."""
        from .channels import channels
        from .choices import choices
        agents = list(self.reprise.get("agents") or [])
        question = (f"La limite de 5 h est levée. {len(agents)} agent(s) avaient été arrêtés pour le quota : "
                    f"{_court(agents)}. Les relancer ?")
        res = choices.ask(question, [TOUT, CHOISIR, NON], channel=channels.active, kind="quota",
                          meta={"etape": "reprise"})
        self.reprise["card"] = str(res.get("id") or "")
        self._save()
        log.info("arrêt pour quota : carte de relance %s (canal %s)", res.get("id") or res.get("error"), channels.active)
        notice = notice or (f"La limite de 5 h est levée : une carte te propose de relancer les {len(agents)} agent(s) "
                            "arrêtés pour le quota.")
        bus.publish("notice", level="warning", text=notice)

    def _carte_choix(self) -> None:
        from .choices import choices
        agents = list(self.reprise.get("agents") or [])
        options, actions = [], {}
        for i, a in enumerate(agents[:3]):
            label = f"{i + 1}. {str(a.get('desc') or 'tâche')[:100]}"
            options.append(label)
            actions[label] = i
        options.append(AUCUN)
        from .channels import channels
        res = choices.ask(QUEL, options, channel=channels.active, kind="quota",
                          meta={"etape": "choix", "actions": actions})
        self.reprise["card"] = str(res.get("id") or "")
        self._save()

    def answer(self, result: dict) -> dict:
        """Clic de l'utilisateur sur une carte « quota » (`Astrel._choice_answer`, déjà refusé à Claude)."""
        with self._lock:
            agents = _dicts(self.reprise.get("agents"))
            meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
            value = str(result.get("value") or "")
            out: dict[str, Any] = {"ok": True, "id": result.get("id"), "quota": ""}
            if not agents:
                out["quota"] = "rien"
                return out
            etape = meta.get("etape")
            choisis: list[int] = []
            if result.get("libre"):
                lus = numeros(value, len(agents))
                if lus is None:
                    # Pas une liste de numéros pure (« ne relance pas le 2 ») : on redemande, rien n'est relancé.
                    if etape == "choix":
                        self._carte_choix()
                    else:
                        self._carte()
                    bus.publish("notice", level="info", text="Réponse non comprise : donne seulement des numéros "
                                                             "(par exemple « 1 et 3 »), ou choisis un bouton.")
                    out["quota"] = "redemande"
                    return out
                choisis = lus
            if not choisis:
                if etape == "reprise" and value == TOUT:
                    choisis = list(range(len(agents)))
                elif etape == "reprise" and value == CHOISIR:
                    if len(agents) == 1:
                        choisis = [0]
                    else:
                        self._carte_choix()
                        out["quota"] = "choisir"
                        return out
                elif etape == "choix" and value in (meta.get("actions") or {}):
                    choisis = [int(meta["actions"][value])]
            if not choisis:
                self.reprise = {}
                self._save()
                bus.publish("notice", level="info", text="Rien relancé. Les agents arrêtés pour le quota restent notés "
                                                         "dans la note des chantiers.")
                out["quota"] = "non"
                return out
            pris = [agents[i] for i in choisis]
            reste = [a for i, a in enumerate(agents) if i not in choisis]
            if result.get("libre") and etape != "choix":
                reste = []  # numéros donnés sur la première carte : les autres ne sont pas relancés
            self.reprise["agents"] = reste
            if not reste:
                self.reprise = {}
            self._save()
        self.core.handle(RELANCE.format(items=lignes(pris)), source="quota",
                         shown=f"Relance de {len(pris)} agent(s) arrêté(s) pour le quota.")
        if reste and etape == "choix":
            with self._lock:
                self._carte_choix()
        out["quota"] = f"relance:{len(pris)}"
        return out


def _entree(task: dict) -> dict:
    return {"tid": task.get("id", ""), "type": task.get("kind") or "agent", "desc": task.get("desc", ""),
            "consigne": "", "canal": task.get("channel", ""), "fichiers": [], "etape": task.get("step", ""),
            "outil_en_cours": "", "note": ""}
