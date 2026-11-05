"""Propositions de modification du moteur local (05/11/2026, lot 5, docs/chantiers/HYBRIDE-QWEN.md).

Le modèle local n'écrit JAMAIS un fichier : il PROPOSE (outil MCP `proposer_modification`, `mcp_local.py`).
La proposition est rangée dans `runtime/propositions-local/` (protégé par le garde-fou, écrit par le serveur
seul) avec l'empreinte du fichier d'origine. Le HUD montre le diff (panneau « Propositions de Qwen ») ;
« Appliquer » est à l'utilisateur SEUL (HUD, refusé à Claude et hors HUD, `coeur/propositions_local.py`). Le cœur
n'écrit qu'après avoir revérifié :
- la proposition est en attente, et c'est celle que l'utilisateur a vue (empreinte de la vue) ;
- le fichier est TOUJOURS identique à son empreinte d'origine (sinon : refusée, à refaire) ;
- le chemin est dans le périmètre du canal (son dossier de projet) ou de `config.WORKDIR`, sans lien ;
- jamais un fichier protégé par le garde-fou, un secret ni une zone confidentielle (`guard`, et les refus
  d'`instantanes.refus` : runtime/, .git, noms de secrets, fichier privé, lien dur, trop gros) ;
- instantané AVANT l'écriture (`instantanes.avant`, un lot par proposition : `./run.sh annule` la défait),
  puis écriture atomique dans le dossier ouvert (`instantanes._dans_dossier`, rejugé, `os.replace`).
« Refuser » garde la raison. Rien de tout cela ne lance de code."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import stat
import time
from datetime import datetime
from pathlib import Path

from . import config

DIR = config.RUNTIME / "propositions-local"
PROPOSITION_ID = re.compile(r"^L\d{8}-\d{6}-[0-9a-f]{6}$")
CONTENU_MAX = 200_000      # caractères du contenu proposé
FICHIER_MAX = 1_000_000    # octets du fichier d'origine
RAISON_MAX = 600
PAR_JOUR = 30
GARDEES = 100              # propositions traitées gardées (les plus anciennes partent)
DIFF_LIGNES_MAX = 2_000
EN_ATTENTE, APPLIQUEE, REFUSEE = "en attente", "appliquée", "refusée"
SESSION = "astrel-propositions-local"  # clé du lot d'instantanés (un lot par proposition)

# Textes d'écran (HUD) et réponses au modèle : traduits par astrel/locales/en.json.
E_INCONNUE = "proposition inconnue"
E_ILLISIBLE = "proposition illisible"
E_TRAITEE = "proposition déjà traitée ({statut})"
E_VUE = "la proposition a changé depuis son affichage : relis-la"
E_CHANGE = "le fichier a changé depuis la proposition : elle n'est pas appliquée (demande-en une nouvelle)"
E_PERIMETRE = "fichier hors du périmètre du canal et de tes projets : rien n'est écrit"
E_LIEN = "le fichier est un lien ou n'est plus au même endroit : rien n'est écrit"
E_PROTEGE = "fichier protégé ou secret ({raison}) : rien n'est écrit"
E_INSTANTANE = "instantané impossible avant l'écriture : rien n'est écrit"
E_CHEMIN = "chemin du fichier attendu, absolu ou relatif au projet"
E_HORS_PROJET = "fichier hors du dossier du projet : aucune proposition"
E_PAS_FICHIER = "ce chemin n'est pas un fichier ordinaire"
E_GROS = "fichier trop gros pour une proposition ({n} octets, {max} au plus)"
E_TEXTE = "fichier illisible en texte (UTF-8) : aucune proposition"
E_PARENT = "dossier du nouveau fichier introuvable dans le projet"
E_DEUX = "donne soit le contenu complet, soit « ancien » et « nouveau », pas les deux"
E_RIEN = "rien à proposer : contenu complet, ou « ancien » et « nouveau »"
E_ANCIEN = "le texte « ancien » doit apparaître exactement une fois dans le fichier ({n} fois)"
E_NOUVEAU_FICHIER = "« ancien » et « nouveau » ne valent que pour un fichier qui existe"
E_PAREIL = "la proposition ne change rien au fichier"
E_LONG = "contenu proposé trop long ({n} caractères, {max} au plus)"
E_JOUR = "{max} propositions déjà déposées aujourd'hui : on s'arrête là"
DEPOSEE = ("Proposition déposée : rien n'est écrit. L'utilisateur la relit et l'applique lui-même dans le HUD "
           "(Propositions de Qwen).")


class Refus(ValueError):
    """Proposition refusée (dépôt ou application) : rien n'est écrit dans le fichier."""


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lire_fichier(p: Path) -> tuple[bytes, int] | None:
    """(octets, droits) du fichier ordinaire `p`, sans suivre de lien ; None s'il n'existe pas."""
    from .platform import systeme

    try:
        fd = systeme.ouvrir_sans_lien(p, os.O_RDONLY)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Refus(E_LIEN) from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise Refus(E_PAS_FICHIER)
        if st.st_size > FICHIER_MAX:
            raise Refus(E_GROS.format(n=st.st_size, max=FICHIER_MAX))
        morceaux, total = [], 0
        while True:
            b = os.read(fd, 1 << 16)
            if not b:
                break
            total += len(b)
            if total > FICHIER_MAX:
                raise Refus(E_GROS.format(n=total, max=FICHIER_MAX))
            morceaux.append(b)
        return b"".join(morceaux), st.st_mode & 0o7777
    finally:
        os.close(fd)


def _dans(p: Path, racine: Path) -> bool:
    return p == racine or racine in p.parents


def _cible(fichier: str, projet: Path) -> Path:
    """VRAI chemin du fichier (absolu, résolu), DANS le projet ; le fichier lui-même n'est pas un lien. C'est
    ce chemin réel qui est gardé : à l'application, il doit l'être encore (`appliquer`)."""
    raw = os.path.expanduser(str(fichier or "").strip())
    if not raw or "\0" in raw:
        raise Refus(E_CHEMIN)
    absolu = Path(os.path.normpath(raw if os.path.isabs(raw) else os.path.join(str(projet), raw)))
    projet = projet.resolve()
    try:
        if absolu.is_symlink():
            raise Refus(E_LIEN)
        reel = absolu.resolve()
    except (OSError, RuntimeError) as exc:
        raise Refus(E_LIEN) from exc
    if not _dans(reel, projet):
        raise Refus(E_HORS_PROJET)
    return reel


def _juge(p: Path) -> str:
    """Pourquoi ce fichier ne peut pas être écrit par une proposition ("" : il peut). Le garde-fou décide :
    une écriture ou une lecture de Claude qui demanderait une question (protégé, secret, zone
    confidentielle, hors des projets), et ce que l'instantané refuse de copier."""
    from . import guard, instantanes

    pourquoi = instantanes.refus(str(p))
    if pourquoi:
        return pourquoi
    try:
        if guard.secret_du_compte(p) or guard.classify("Write", {"file_path": str(p)}).needs_confirm \
                or guard.classify("Read", {"file_path": str(p)}).needs_confirm:
            return "protégé"
    except Exception:  # noqa: BLE001 — garde-fou en panne : jamais d'écriture
        return "garde-fou indisponible"
    return ""


def _ecrire_json(path: Path, data: dict) -> None:
    from .platform import systeme

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = systeme.ouvrir_sans_lien(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _diff(avant: str, apres: str, nom: str) -> tuple[list[str], int, int]:
    lignes = list(difflib.unified_diff(avant.splitlines(), apres.splitlines(), fromfile=nom, tofile=nom,
                                       n=3, lineterm=""))
    plus = sum(1 for x in lignes if x.startswith("+") and not x.startswith("+++"))
    moins = sum(1 for x in lignes if x.startswith("-") and not x.startswith("---"))
    if len(lignes) > DIFF_LIGNES_MAX:
        lignes = lignes[:DIFF_LIGNES_MAX] + [f"… ({len(lignes) - DIFF_LIGNES_MAX} lignes de plus)"]
    return lignes, plus, moins


# ------------------------------------------------------------------ dépôt (serveur MCP du CLI local)

def deposer(fichier: str, projet: Path, contenu: str = "", ancien: str = "", nouveau: str = "", raison: str = "",
            canal: str = "", dossier: Path | None = None, now: float | None = None) -> dict:
    """Range une proposition ; le fichier n'est PAS touché. `contenu` : le texte complet proposé ; ou bien
    `ancien` (présent une seule fois) remplacé par `nouveau`. Lève `Refus`."""
    dossier = Path(dossier or DIR)
    projet = Path(projet).expanduser().resolve()
    p = _cible(fichier, projet)
    # Jugé AVANT de lire : un fichier protégé ou secret n'est jamais ouvert (« ancien » trouvé ou non dirait
    # quelque chose de son contenu).
    pourquoi = _juge(p)
    if pourquoi:
        raise Refus(E_PROTEGE.format(raison=pourquoi))
    lu = _lire_fichier(p)
    if contenu and (ancien or nouveau):
        raise Refus(E_DEUX)
    if lu is None:
        if ancien or nouveau:
            raise Refus(E_NOUVEAU_FICHIER)
        if not p.parent.is_dir():
            raise Refus(E_PARENT)
        origine = None
    else:
        try:
            origine = lu[0].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise Refus(E_TEXTE) from exc
    if ancien:
        n = origine.count(ancien) if origine is not None else 0
        if n != 1:
            raise Refus(E_ANCIEN.format(n=n))
        propose = origine.replace(ancien, nouveau, 1)
    elif contenu:
        propose = str(contenu)
    else:
        raise Refus(E_RIEN)
    if len(propose) > CONTENU_MAX:
        raise Refus(E_LONG.format(n=len(propose), max=CONTENU_MAX))
    if propose == origine:
        raise Refus(E_PAREIL)
    now = now or time.time()
    jour = datetime.fromtimestamp(now).strftime("%Y%m%d")
    if dossier.is_dir() and len(list(dossier.glob(f"L{jour}-*.json"))) >= PAR_JOUR:
        raise Refus(E_JOUR.format(max=PAR_JOUR))
    pid = f"L{datetime.fromtimestamp(now).strftime('%Y%m%d-%H%M%S')}-{os.urandom(3).hex()}"
    data = {
        "id": pid, "date": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M"), "at": now,
        "statut": EN_ATTENTE, "fichier": str(p), "projet": str(projet), "canal": str(canal or "")[:80],
        "existe": lu is not None, "empreinte_origine": _sha_bytes(lu[0]) if lu is not None else None,
        "contenu_propose": propose, "raison": " ".join(str(raison or "").split())[:RAISON_MAX],
        "par": "modèle local",
    }
    _ecrire_json(dossier / f"{pid}.json", data)
    _elaguer(dossier)
    _lignes, plus, moins = _diff(origine or "", propose, p.name)
    return {"ok": True, "proposition": pid, "fichier": str(p), "lignes_ajoutees": plus, "lignes_retirees": moins,
            "message": DEPOSEE}


def _elaguer(dossier: Path) -> None:
    """Au-delà de `GARDEES` propositions traitées, les plus anciennes partent (jamais une en attente)."""
    traitees = []
    for f in sorted(dossier.glob("L*.json")):
        d = _charger(f)
        if d is not None and d.get("statut") != EN_ATTENTE:
            traitees.append(f)
    for f in traitees[:max(0, len(traitees) - GARDEES)]:
        try:
            f.unlink()
        except OSError:
            pass


# ------------------------------------------------------------------ lecture (cœur, HUD)

def _charger(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not PROPOSITION_ID.match(str(data.get("id") or "")) \
            or data.get("id") != path.stem or not isinstance(data.get("fichier"), str) \
            or not isinstance(data.get("contenu_propose"), str) or not isinstance(data.get("projet"), str):
        return None
    return data


def _fichier(pid: str, dossier: Path | None) -> Path:
    pid = str(pid or "").strip()
    if not PROPOSITION_ID.match(pid):
        raise Refus(E_INCONNUE)
    base = Path(dossier or DIR).resolve()
    path = base / f"{pid}.json"
    if path.is_symlink() or not path.is_file():
        raise Refus(E_INCONNUE)
    return path


def empreinte_vue(data: dict) -> str:
    """Lie ce que l'utilisateur a vu à ce qui sera écrit."""
    vue = {k: data.get(k) for k in ("id", "fichier", "contenu_propose", "empreinte_origine")}
    return _sha_bytes(json.dumps(vue, ensure_ascii=False, sort_keys=True).encode("utf-8"))[:16]


def _etat_fichier(data: dict) -> tuple[str, str]:
    """(« identique » | « modifié » | « absent » | « illisible », texte actuel) du fichier visé."""
    p = Path(data["fichier"])
    try:
        lu = _lire_fichier(p)
    except Refus:
        return "illisible", ""
    if lu is None:
        return ("identique" if data.get("empreinte_origine") is None else "absent"), ""
    try:
        texte = lu[0].decode("utf-8")
    except UnicodeDecodeError:
        return "illisible", ""
    return ("identique" if _sha_bytes(lu[0]) == data.get("empreinte_origine") else "modifié"), texte


def lister(toutes: bool = False, dossier: Path | None = None, limite: int = 50) -> list[dict]:
    """Propositions (en attente seulement, sauf `toutes`), la plus récente d'abord, avec leur diff calculé
    MAINTENANT contre le fichier actuel."""
    dossier = Path(dossier or DIR)
    out = []
    for path in sorted(dossier.glob("L*.json"), reverse=True) if dossier.is_dir() else []:
        data = _charger(path)
        if data is None or (not toutes and data.get("statut") != EN_ATTENTE):
            continue
        etat, actuel = _etat_fichier(data)
        lignes, plus, moins = _diff(actuel, data["contenu_propose"], Path(data["fichier"]).name)
        out.append({
            "proposition_id": data["id"], "date": str(data.get("date") or ""), "statut": str(data.get("statut") or ""),
            "fichier": data["fichier"], "canal": str(data.get("canal") or ""), "raison": str(data.get("raison") or ""),
            "nouveau": not data.get("existe"), "etat_fichier": etat, "diff": lignes, "plus": plus, "moins": moins,
            "empreinte": empreinte_vue(data), "traitee": data.get("traitee") or None,
        })
        if len(out) >= limite:
            break
    return out


def en_attente(dossier: Path | None = None) -> list[dict]:
    """(id, fichier, canal) des propositions en attente (badge, notices)."""
    dossier = Path(dossier or DIR)
    out = []
    for path in sorted(dossier.glob("L*.json")) if dossier.is_dir() else []:
        data = _charger(path)
        if data is not None and data.get("statut") == EN_ATTENTE:
            out.append({"id": data["id"], "fichier": data["fichier"], "canal": str(data.get("canal") or "")})
    return out


def signature(dossier: Path | None = None) -> tuple:
    dossier = Path(dossier or DIR)
    try:
        return tuple(sorted((p.name, p.stat().st_mtime_ns) for p in dossier.glob("L*.json")))
    except OSError:
        return ()


# ------------------------------------------------------------------ décision de l'utilisateur (cœur)

def _a_traiter(pid: str, dossier: Path | None) -> tuple[Path, dict]:
    path = _fichier(pid, dossier)
    data = _charger(path)
    if data is None:
        raise Refus(E_ILLISIBLE)
    if data.get("statut") != EN_ATTENTE:
        raise Refus(E_TRAITEE.format(statut=data.get("statut")))
    return path, data


def appliquer(pid: str, empreinte: str, perimetres: list[Path], par: str = "utilisateur",
              dossier: Path | None = None) -> dict:
    """Écrit la proposition `pid` si TOUT est encore vrai (voir l'en-tête) ; lève `Refus` sinon (rien n'est
    écrit, la proposition reste en attente). `perimetres` : dossiers où ce canal peut écrire (son projet,
    `config.WORKDIR`), donnés par le cœur."""
    from . import instantanes

    path, data = _a_traiter(pid, dossier)
    if str(empreinte or "") != empreinte_vue(data):
        raise Refus(E_VUE)
    p = Path(data["fichier"])
    if not p.is_absolute() or Path(os.path.normpath(str(p))) != p:
        raise Refus(E_LIEN)
    projet = Path(data["projet"])
    racines = [Path(r).resolve() for r in perimetres if r]
    if not racines or not any(_dans(projet.resolve(), r) for r in racines) or not _dans(p, projet.resolve()) \
            or not any(_dans(p, r) for r in racines):
        raise Refus(E_PERIMETRE)
    try:
        if p.is_symlink() or p.resolve() != p or p.parent.resolve() != p.parent:
            raise Refus(E_LIEN)
    except (OSError, RuntimeError) as exc:
        raise Refus(E_LIEN) from exc
    lu = _lire_fichier(p)
    sha = _sha_bytes(lu[0]) if lu is not None else None
    if sha != data.get("empreinte_origine"):
        raise Refus(E_CHANGE)
    pourquoi = _juge(p)
    if pourquoi:
        raise Refus(E_PROTEGE.format(raison=pourquoi))
    contenu = data["contenu_propose"].encode("utf-8")
    mode = lu[1] if lu is not None else 0o644
    charge = {"tool_name": "Write", "tool_input": {"file_path": str(p)}, "cwd": str(projet),
              "session_id": SESSION, "prompt_id": pid, "tool_use_id": f"proposition-{pid}"}
    if instantanes.avant(charge) != 1:
        raise Refus(E_INSTANTANE)
    # L'instantané a lu le fichier : il doit être encore celui de l'empreinte (rien n'a changé entre-temps).
    relu = _lire_fichier(p)
    if (_sha_bytes(relu[0]) if relu is not None else None) != data.get("empreinte_origine"):
        instantanes.apres(charge)  # inchangé par nous : il sort du lot
        raise Refus(E_CHANGE)
    try:
        # Atomique, dans le DOSSIER ouvert et rejugé (un dossier remplacé par un lien ne détourne rien).
        instantanes._dans_dossier(p, instantanes._remplacer(contenu, mode))
    except instantanes._Refus as exc:
        raise Refus(E_PROTEGE.format(raison=str(exc))) from exc
    finally:
        instantanes.apres(charge)  # empreinte « après » : `./run.sh annule` peut rétablir
    data.update(statut=APPLIQUEE, traitee={"le": datetime.now().strftime("%Y-%m-%d %H:%M"), "par": str(par)[:20],
                                           "instantane": True})
    _ecrire_json(path, data)
    return {"ok": True, "proposition_id": pid, "fichier": str(p)}


def refuser(pid: str, raison: str = "", par: str = "utilisateur", dossier: Path | None = None) -> dict:
    path, data = _a_traiter(pid, dossier)
    data.update(statut=REFUSEE, traitee={"le": datetime.now().strftime("%Y-%m-%d %H:%M"), "par": str(par)[:20],
                                         "raison": " ".join(str(raison or "").split())[:RAISON_MAX]})
    _ecrire_json(path, data)
    return {"ok": True, "proposition_id": pid, "fichier": data["fichier"]}
