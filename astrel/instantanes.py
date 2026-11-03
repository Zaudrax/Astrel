"""Instantané automatique avant écriture et « annule ce que tu viens de faire » (01/11/2026).

Accord EXPLICITE de l'utilisateur (01/11, chantier « garde-fou », point 3 ; idées HERMES-outils 66 et
ECC-agents-commandes 23). Avant chaque écriture d'un fichier par Claude (Write, Edit, MultiEdit,
NotebookEdit, et les redirections shell ÉVIDENTES : `> f`, `>> f`, `tee f`, `sed -i … f`), le hook
`hooks/suivi_outils.py` (PreToolUse) garde la version d'avant dans `runtime/instantanes/` ; après
l'outil (PostToolUse, PostToolUseFailure), il note l'empreinte du fichier écrit. Les écritures d'un
même message de l'utilisateur (`prompt_id` du CLI, le même pour ses sous-agents) forment un LOT ;
`restaurer` rétablit le dernier lot pas encore annulé (le rappeler remonte au lot d'avant).

Ce qui n'est JAMAIS copié ni rétabli (un secret ne sort pas de sa place, un fichier du garde-fou ne
s'écrit qu'avec la question de l'utilisateur) :
- toute cible dont l'écriture par Claude demanderait une question (`guard.classify("Write")`) :
  fichiers et dossiers protégés, secrets, réglages de Claude Code, venv, hors des projets et de
  /tmp, hors du périmètre d'une mission ; jugé AVANT la copie, puis sur le VRAI chemin du fichier
  ouvert (`/proc/self/fd`), et de nouveau avant de rétablir, dans le dossier ouvert (`dir_fd`) ;
- `runtime/`, `logs/`, `.git/`, `node_modules/`, `.venv/`, les noms de secrets (`.env*`, clés,
  `*secret*`, `*token*`…), un fichier privé (aucun droit pour le groupe ni les autres : les secrets
  d'Astrel sont en 0600), un lien symbolique, un fichier à plusieurs liens durs, plus de
  `MAX_FICHIER` octets.
Rétablir n'écrit QUE ce qu'un Write de Claude écrirait sans question (même jugement) : outil MCP
`annuler_ecritures` (Claude, refusé sous ESTOP) ou `./run.sh annule` (terminal). Un fichier modifié
depuis l'écriture (empreinte différente) n'est pas écrasé ; un fichier créé par le lot est retiré
s'il n'a pas changé depuis. Bornes : `MAX_LOTS` lots, `MAX_LOT_FICHIERS` fichiers par lot,
`MAX_TOTAL` octets en tout, dossier 0700, copies 0600. `runtime/instantanes/` est protégé par le
garde-fou (`guard.PROTECTED_PATHS`) : seul ce module (hook, MCP, terminal) y écrit.

Bibliothèque standard seule (le hook tourne en mode isolé, `-I -S`) ; le garde-fou n'est importé
que s'il y a une cible à juger.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import time
from pathlib import Path

from . import config
from .platform import systeme

DIR = config.RUNTIME / "instantanes"
INDEX = DIR / "index.json"
VERROU = DIR / ".verrou"
MAX_FICHIER = 2 * 1024 * 1024
MAX_LOT_FICHIERS = 100
MAX_LOTS = 20
MAX_TOTAL = 64 * 1024 * 1024
LOT_INACTIF_S = 600  # sans `prompt_id` : un nouveau lot après 10 min sans écriture
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

_DOSSIERS_EXCLUS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".rtk"}
_SECRET_NOM = re.compile(
    r"^\.env(?:\..*)?$|\.(?:pem|key|p12|pfx|jks|kdbx|gpg|asc|keystore)$|^id_(?:rsa|dsa|ecdsa|ed25519)"
    r"|secret|credential|passw|token|cookie|\.netrc$|\.npmrc$|\.pypirc$", re.I)


# --------------------------------------------------------------------- cibles

def cible_outil(tool: str, tool_input: dict) -> str:
    """Chemin écrit par un outil d'écriture (vide sinon)."""
    if tool not in WRITE_TOOLS or not isinstance(tool_input, dict):
        return ""
    raw = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    return raw if isinstance(raw, str) else ""


_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][\w-]*)\1")
_REDIR = {">", ">>", "&>", "&>>", ">|"}
_SKIP_REDIR = {"<": 2, "<<": 2, "<<<": 2, "<&": 2, ">&": 2, "<<-": 2, "<>": 2}
_SEPS = {";", "&&", "||", "|", "&", "|&", ";;"}
_PREFIXES = {"sudo", "env", "nohup", "time", "command", "builtin", "exec", "nice", "timeout", "then", "do",
             "else", "!"}
_PRE_FILTRE = re.compile(r">|\btee\b|\bsed\b[^|;&]*\s-[a-zA-Z]*i")


def _sans_heredocs(cmd: str) -> str:
    """Retire le CORPS des heredocs (des données, pas des commandes), garde la ligne qui les ouvre."""
    out: list[str] = []
    fins: list[str] = []
    for line in cmd.split("\n"):
        if fins:
            if line.strip() == fins[0]:
                fins.pop(0)
            continue
        out.append(line)
        fins = [m.group(2) for m in _HEREDOC.finditer(line)]
    return "\n".join(out)


def _litteral(word: str) -> bool:
    """Un nom de fichier écrit tel quel (ni variable, ni joker, ni substitution, ni option)."""
    w = word[1:] if word.startswith("~/") else word
    return bool(w) and not any(c in w for c in "$`*?[]{}()<>\\~'\"") and not w.startswith("-")


def _sans_guillemets(word: str) -> str:
    """Mot cité en entier (`"a b.txt"`, `'x'`) : son texte ; sinon tel quel (un guillemet au milieu
    reste et le rend non littéral pour `cibles_bash`, qui ne devine pas)."""
    if len(word) >= 2 and word[0] == word[-1] and word[0] in "'\"":
        inner = word[1:-1]
        return inner if word[0] == "'" or "$" not in inner and "`" not in inner else word
    return word


def _sed_fichiers(args: list[str]) -> list[str]:
    """Fichiers d'un `sed -i` : opérandes, moins le script quand aucun -e/-f n'est donné."""
    ops: list[str] = []
    script = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            ops += args[i + 1:]
            break
        if a in ("-e", "--expression", "-f", "--file", "-l", "--line-length"):
            script = script or a in ("-e", "--expression", "-f", "--file")
            i += 2
            continue
        if a.startswith(("--expression=", "--file=")):
            script = True
        elif a.startswith("--"):
            pass
        elif a.startswith("-") and len(a) > 1:
            for j, ch in enumerate(a[1:], start=1):
                if ch == "i":
                    break  # le reste est le suffixe de sauvegarde
                if ch in "efl":
                    script = script or ch in "ef"
                    if j == len(a) - 1:
                        i += 1  # l'argument est le mot suivant
                    break
        else:
            ops.append(a)
        i += 1
    return ops if script else ops[1:]


def cibles_bash(cmd: str, cwd: str = "") -> list[str]:
    """Fichiers qu'une commande Bash écrit À COUP SÛR, chemins absolus : redirection vers un nom
    littéral, `tee [-a] f…`, `sed -i … f…`. Rien dès que c'est douteux (variable, joker, `cd`
    calculé, sous-shell) : un instantané manqué ne casse rien, une cible en trop n'est jamais
    rétablie (inchangée, elle sort du lot). Ce n'est PAS un jugement de sécurité : chaque cible
    passe ensuite par `refus` (garde-fou)."""
    if not isinstance(cmd, str) or len(cmd) > 20000 or not _PRE_FILTRE.search(cmd):
        return []
    base = cwd if cwd and os.path.isabs(cwd) else str(config.WORKDIR)
    try:
        # posix=False : un « > » entre guillemets reste un mot cité (`grep '>' f`), jamais une redirection.
        lex = shlex.shlex(_sans_heredocs(cmd).replace("\n", " ; "), posix=False, punctuation_chars=";&|<>")
        lex.whitespace_split = True
        lex.commenters = ""
        toks = list(lex)
    except ValueError:
        return []
    cmds: list[list[str]] = [[]]
    for t in toks:
        if t in _SEPS:
            cmds.append([])
        else:
            cmds[-1].append(t)
    out: list[str] = []

    def add(word: str) -> None:
        word = _sans_guillemets(word)
        if not _litteral(word) or word.startswith("/dev/"):
            return
        p = os.path.expanduser(word)
        p = os.path.normpath(p if os.path.isabs(p) else os.path.join(base, p))
        if p not in out:
            out.append(p)

    for words in cmds:
        if any(w.startswith(("(", "{")) or w.endswith((")", "}")) for w in words):
            return out  # sous-shell ou groupe : dossier courant incertain pour la suite
        rest: list[str] = []
        i = 0
        while i < len(words):
            w = words[i]
            if w in _REDIR:
                if i + 1 < len(words):
                    add(words[i + 1])
                i += 2
                continue
            if w in _SKIP_REDIR:
                i += _SKIP_REDIR[w]
                continue
            rest.append(w)
            i += 1
        while rest and (rest[0] in _PREFIXES or re.fullmatch(r"[A-Za-z_]\w*=\S*", rest[0])
                        or re.fullmatch(r"\d+", rest[0])):
            rest.pop(0)
        if not rest:
            continue
        prog = os.path.basename(rest[0])
        if prog in ("cd", "pushd"):
            if len(rest) == 2 and _litteral(_sans_guillemets(rest[1])):
                d = os.path.expanduser(_sans_guillemets(rest[1]))
                base = os.path.normpath(d if os.path.isabs(d) else os.path.join(base, d))
                continue
            return out  # dossier inconnu : la suite n'est plus sûre
        if prog == "tee":
            for a in rest[1:]:
                if not a.startswith("-"):
                    add(a)
        elif prog == "sed" and any(re.fullmatch(r"-[a-zA-Z]*i\S*|--in-place(=\S*)?", a) for a in rest[1:]):
            for a in _sed_fichiers(rest[1:]):
                add(a)
    return out


# --------------------------------------------------------------------- admissibilité

def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha(path: Path) -> str | None:
    """Empreinte du contenu ; None si absent ; « ? » si illisible (ou pas un fichier ordinaire)."""
    try:
        fd = systeme.ouvrir_sans_lien(path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    except OSError:
        return "?"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return "?"
        h = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                return h.hexdigest()
            h.update(chunk)
    except OSError:
        return "?"
    finally:
        os.close(fd)


def _vrai_chemin(fd: int) -> str | None:
    """Chemin réel d'un descripteur ouvert (Linux : /proc/self/fd), None si le système ne le dit pas."""
    lien = f"/proc/self/fd/{fd}"
    try:
        return os.readlink(lien) if os.path.islink(lien) else None
    except OSError:
        return None


def _juge_garde_fou(*chemins: str) -> str:
    """« protégé » si une écriture de Claude vers l'un de ces chemins demanderait une question."""
    try:
        from . import guard
        for p in dict.fromkeys(chemins):
            if guard.classify("Write", {"file_path": p}, cwd=None).needs_confirm:
                return "protégé"
    except Exception:  # noqa: BLE001 — garde-fou absent ou en panne : jamais de copie ni d'écriture
        return "garde-fou indisponible"
    return ""


def _dossier_prive(reel: Path) -> bool:
    """Un dossier entre le fichier et la racine de son projet n'ouvre aucun droit au groupe ni aux
    autres (whatsapp-auth, profils de navigateur…). Sous /tmp, non jugé : les dossiers de travail
    par utilisateur y sont en 0700 (pytest, Claude), et rien n'y est protégé du garde-fou. Racine
    introuvable : privé (dans le doute, pas de copie)."""
    try:
        from . import guard
        racines = [r.resolve() for r in guard.ALLOWED_WRITE_ROOTS]
    except Exception:  # noqa: BLE001
        return True
    dedans = [r for r in racines if r == reel or r in reel.parents]
    if not dedans:
        return True
    racine = max(dedans, key=lambda r: len(r.parts))
    if racine == Path("/tmp").resolve():
        return False
    for d in reel.parents:
        if d == racine or racine not in d.parents:
            break
        try:
            if not os.lstat(d).st_mode & 0o077:
                return True
        except FileNotFoundError:
            continue  # dossier encore à créer (Write de nouveau/a.py) : on juge les parents existants
        except OSError:
            return True
    return False


def refus(chemin: str, cwd: str = "", copie: bool = True) -> str:
    """Pourquoi ce fichier n'est pas copié (`copie`) ou pas rétabli ("" : admissible). `chemin`
    absolu, ou relatif au dossier de Claude `cwd`. Dans le doute : refusé."""
    raw = os.path.expanduser(str(chemin or "").strip())
    if not raw:
        return "chemin vide"
    if not os.path.isabs(raw):
        if not (cwd and os.path.isabs(cwd)):
            return "chemin relatif sans dossier"
        raw = os.path.join(cwd, raw)
    absolu = Path(os.path.normpath(raw))
    try:
        if absolu.is_symlink():
            return "lien symbolique"
        reel = absolu.resolve()
    except (OSError, RuntimeError):
        return "chemin illisible"
    for zone in (config.RUNTIME, config.LOGS):
        z = zone.resolve()
        if reel == z or z in reel.parents:
            return "état d'Astrel"
    if any(part in _DOSSIERS_EXCLUS for part in reel.parts):
        return "dossier exclu"
    if _SECRET_NOM.search(reel.name):
        return "secret"
    try:
        st = os.lstat(reel)
    except FileNotFoundError:
        st = None
    except OSError:
        return "fichier illisible"
    if copie and _dossier_prive(reel):
        return "secret"  # 0644 dans un dossier 0700 : son propriétaire le cache aussi
    if st is not None:
        if not stat.S_ISREG(st.st_mode):
            return "pas un fichier ordinaire"
        if copie and not st.st_mode & 0o077:
            return "secret"  # privé (0600…) : son propriétaire le cache, on ne le copie pas
        if copie and st.st_nlink > 1:
            return "lien dur"
        if copie and st.st_size > MAX_FICHIER:
            return "trop gros"
    return _juge_garde_fou(str(reel), str(absolu))


def _lire_copie(reel: str) -> tuple[bytes, int] | str:
    """(contenu, droits) du fichier `reel`, ou la raison de ne pas le copier. Le fichier OUVERT est
    rejugé : vrai chemin (un dossier remplacé par un lien entre-temps), droits, liens, taille."""
    try:
        fd = systeme.ouvrir_sans_lien(reel, os.O_RDONLY)
    except OSError:
        return "fichier illisible"
    try:
        st = os.fstat(fd)
        vrai = _vrai_chemin(fd)
        if vrai is not None and vrai != reel:
            return "chemin changé"
        if not stat.S_ISREG(st.st_mode) or not st.st_mode & 0o077 or st.st_nlink > 1:
            return "secret"
        # 01/11 (relecture) : le fichier OUVERT est rejugé sur son vrai chemin (un lien du projet qui
        # bascule entre la résolution et l'ouverture ne fait pas copier un secret de runtime/).
        pourquoi = refus(vrai or os.path.realpath(reel))
        if pourquoi:
            return pourquoi
        if st.st_size > MAX_FICHIER:
            return "trop gros"
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FICHIER:
                return "trop gros"
            chunks.append(chunk)
        return b"".join(chunks), st.st_mode & 0o7777
    finally:
        os.close(fd)


# --------------------------------------------------------------------- index

def _verrou():
    """Verrou exclusif de l'index (hooks parallèles). Sans fcntl (Windows) : sans verrou."""
    DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(DIR, 0o700)
    except OSError:
        pass
    fd = systeme.ouvrir_sans_lien(VERROU, os.O_RDWR | os.O_CREAT, 0o600)
    fh = os.fdopen(fd, "a+")
    try:
        import fcntl
        fcntl.flock(fh, fcntl.LOCK_EX)
    except (ImportError, OSError):
        pass
    return fh


def _lire() -> dict:
    try:
        data = json.loads(INDEX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"lots": []}
    if not isinstance(data, dict) or not isinstance(data.get("lots"), list):
        return {"lots": []}
    data["lots"] = [lot for lot in data["lots"] if isinstance(lot, dict) and isinstance(lot.get("fichiers"), list)
                    and _lot_dir(lot) is not None]
    return data


def _ecrire(data: dict) -> None:
    tmp = INDEX.with_name(f".index.{os.getpid()}.tmp")
    fd = systeme.ouvrir_sans_lien(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, INDEX)


def _lot_dir(lot: dict) -> Path | None:
    lid = str(lot.get("id") or "")
    return DIR / lid if re.fullmatch(r"[0-9]+-[0-9a-f]{8}", lid) else None


def _supprimer_lot(lot: dict) -> None:
    d = _lot_dir(lot)
    if d is None or d.is_symlink() or not d.is_dir():
        return
    for f in d.iterdir():
        try:
            f.unlink()
        except OSError:
            pass
    try:
        d.rmdir()
    except OSError:
        pass


def _taille(lot: dict) -> int:
    return sum(int(f.get("taille") or 0) for f in lot.get("fichiers", []))


def _elaguer(data: dict, garder: dict | None) -> None:
    lots = data["lots"]
    while len(lots) > MAX_LOTS or (sum(_taille(lot) for lot in lots) > MAX_TOTAL and len(lots) > 1):
        vieux = next((lot for lot in lots if lot is not garder), None)
        if vieux is None:
            break
        lots.remove(vieux)
        _supprimer_lot(vieux)


def cle_lot(payload: dict) -> str:
    """Un lot par message (`prompt_id`, le même pour les sous-agents de ce message)."""
    sid = str(payload.get("session_id") or "")
    pid = str(payload.get("prompt_id") or "")
    return f"{sid}|{pid}" if pid else ""


def _lot_courant(data: dict, cle: str, now: float) -> dict:
    lots = data["lots"]
    last = lots[-1] if lots else None
    if last is not None and not last.get("restaure"):
        if (cle and last.get("cle") == cle) or (not cle and not last.get("cle")
                                                and now - float(last.get("fin") or 0) < LOT_INACTIF_S):
            return last
    lid = f"{int(now)}-{hashlib.sha256(f'{cle}{now}{os.getpid()}'.encode()).hexdigest()[:8]}"
    lot = {"id": lid, "cle": cle, "debut": now, "fin": now, "restaure": None, "fichiers": [], "ignores": {}}
    lots.append(lot)
    return lot


def _compte(lot: dict, pourquoi: str) -> None:
    ign = lot.setdefault("ignores", {})
    ign[pourquoi] = int(ign.get(pourquoi, 0)) + 1


# --------------------------------------------------------------------- hook

def avant(payload: dict) -> int:
    """PreToolUse : copie la version d'avant de chaque fichier que l'outil va écrire. Nombre de
    fichiers ajoutés au lot. Ne lève jamais (le hook laisse toujours l'outil se faire)."""
    try:
        return _avant(payload)
    except Exception:  # noqa: BLE001
        return 0


def _avant(payload: dict) -> int:
    tool = str(payload.get("tool_name") or "")
    inp = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    cwd = str(payload.get("cwd") or "")
    if tool == "Bash":
        cibles = cibles_bash(str(inp.get("command") or ""), cwd)
    else:
        c = cible_outil(tool, inp)
        cibles = [c] if c else []
    if not cibles:
        return 0
    tid = str(payload.get("tool_use_id") or "")
    now = time.time()
    juges = []  # (chemin réel, raison du refus) — jugés HORS du verrou (le garde-fou est lent à charger)
    for raw in cibles[:MAX_LOT_FICHIERS]:
        absolu = os.path.normpath(os.path.join(cwd, os.path.expanduser(raw)) if cwd else os.path.expanduser(raw))
        try:
            reel = str(Path(absolu).resolve())
        except (OSError, RuntimeError):
            juges.append((absolu, "chemin illisible"))
            continue
        # 01/11 (relecture) : le chemin écrit ET le chemin résolu, celui qu'on ouvrira ; le fichier
        # ouvert est rejugé ensuite sur son VRAI chemin (`_lire_copie`).
        juges.append((reel, refus(raw, cwd) or refus(reel)))
    ajoutes = 0
    with _verrou():
        data = _lire()
        lot = _lot_courant(data, cle_lot(payload), now)
        connus = {f.get("chemin"): f for f in lot["fichiers"]}
        for reel, pourquoi in juges:
            if reel in connus:  # déjà dans le lot : la PREMIÈRE version d'avant fait foi
                e = connus[reel]
                # Contre-relecture : dès qu'une empreinte « après » existe, même si une écriture reste en
                # attente (outil refusé : ni PostToolUse ni Failure) ; sans empreinte, dans le doute, rien.
                if (e.get("apres") is not None or not e.get("attente")) and _sha(Path(reel)) != e.get("apres"):
                    e["hors_outil"] = True  # 01/11 (relecture) : modifié entre deux outils (l'utilisateur ?)
                if tid and tid not in e.setdefault("attente", []):
                    e["attente"].append(tid)
                continue
            if pourquoi:
                if pourquoi in ("protégé", "secret", "lien dur", "trop gros", "garde-fou indisponible"):
                    _compte(lot, pourquoi)
                continue
            if len(lot["fichiers"]) >= MAX_LOT_FICHIERS:
                _compte(lot, "plein")
                continue
            entree = {"chemin": reel, "outil": tool, "avant": None, "apres": None, "copie": "", "taille": 0,
                      "mode": None, "attente": [tid] if tid else []}
            if os.path.lexists(reel):
                lu = _lire_copie(reel)
                if isinstance(lu, str):
                    _compte(lot, lu)
                    continue
                contenu, mode = lu
                d = _lot_dir(lot)
                d.mkdir(mode=0o700, exist_ok=True)
                nom = f"{len(lot['fichiers'])}-{int(now * 1000) % 100000}.bin"
                fd = os.open(d / nom, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(contenu)
                entree.update(avant=_sha_bytes(contenu), copie=nom, taille=len(contenu), mode=mode)
            lot["fichiers"].append(entree)
            connus[reel] = entree
            ajoutes += 1
        lot["fin"] = now
        _elaguer(data, lot)
        _ecrire(data)
    return ajoutes


def apres(payload: dict) -> int:
    """PostToolUse / PostToolUseFailure : empreinte de chaque fichier que cet outil attendait ;
    un fichier inchangé sort du lot. Nombre d'entrées mises à jour. Ne lève jamais."""
    try:
        return _apres(payload)
    except Exception:  # noqa: BLE001
        return 0


def _attendu(tid: str) -> bool:
    """Lecture SANS verrou : cet outil a-t-il un fichier en attente ? (le cas de presque tout Bash : non)."""
    data = _lire()
    return any(tid in (f.get("attente") or []) for lot in data["lots"][-3:] for f in lot["fichiers"])


def _apres(payload: dict) -> int:
    tid = str(payload.get("tool_use_id") or "")
    if not tid or not INDEX.exists() or not _attendu(tid):
        return 0
    n = 0
    with _verrou():
        data = _lire()
        for lot in data["lots"][-3:]:
            garder = []
            for f in lot["fichiers"]:
                attente = f.setdefault("attente", [])
                if tid in attente:
                    attente.remove(tid)
                    f["apres"] = _sha(Path(str(f.get("chemin") or "")))
                    n += 1
                    if not attente and f["apres"] == f.get("avant"):
                        _oublier(lot, f)  # rien n'a changé : outil refusé, échoué, ou cible en trop
                        continue
                garder.append(f)
            lot["fichiers"] = garder
        if n:
            _ecrire(data)
    return n


def _oublier(lot: dict, f: dict) -> None:
    d = _lot_dir(lot)
    nom = str(f.get("copie") or "")
    if d is not None and re.fullmatch(r"[\w.-]+\.bin", nom):
        try:
            (d / nom).unlink()
        except OSError:
            pass


# --------------------------------------------------------------------- annuler

def lots() -> list[dict]:
    """Les lots gardés, du plus récent au plus ancien (sans contenu)."""
    return [{"id": lot["id"], "debut": lot.get("debut"), "fin": lot.get("fin"), "restaure": lot.get("restaure"),
             "fichiers": [f.get("chemin") for f in lot["fichiers"]], "ignores": lot.get("ignores") or {}}
            for lot in reversed(_lire()["lots"])]


class _Refus(Exception):
    pass


def _dans_dossier(p: Path, action) -> None:
    """Ouvre le DOSSIER de `p`, vérifie son vrai chemin, rejuge `p`, puis agit par `dir_fd` : un
    dossier remplacé par un lien après la vérification ne détourne pas l'écriture."""
    if not getattr(os, "supports_dir_fd", None) or os.open not in os.supports_dir_fd:
        if refus(str(p), copie=False):
            raise _Refus("protégé")
        action(None, str(p))
        return
    dfd = os.open(p.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        vrai = _vrai_chemin(dfd)
        if vrai is not None and vrai != str(p.parent):
            raise _Refus("chemin changé")
        if refus(str(p), copie=False):
            raise _Refus("protégé")
        action(dfd, p.name)
    finally:
        os.close(dfd)


def _remplacer(contenu: bytes, mode) -> object:
    def action(dfd, nom: str) -> None:
        tmp = f".{nom}.astrel-annule.{os.getpid()}"
        kw = {"dir_fd": dfd} if dfd is not None else {}
        if dfd is None:
            tmp = str(Path(nom).with_name(tmp))
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600, **kw)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(contenu)
                os.fchmod(fh.fileno(), mode if isinstance(mode, int) else 0o644)
            if dfd is not None:
                os.replace(tmp, nom, src_dir_fd=dfd, dst_dir_fd=dfd)  # remplace un lien, jamais sa cible
            else:
                os.replace(tmp, nom)
        except BaseException:
            try:
                os.unlink(tmp, **kw)
            except OSError:
                pass
            raise
    return action


def _retirer(dfd, nom: str) -> None:
    os.unlink(nom, **({"dir_fd": dfd} if dfd is not None else {}))


def vient_de_claude(pid: int | None = None) -> bool:
    """Ce process vient-il de Claude ? Variable de Claude Code ou du cerveau, cgroup du service, ou
    ancêtre « claude » (comme `doctor.launched_by_claude`, mais par les seuls modules PROTÉGÉS :
    `state.py`, `platform/proc.py`). Dans le doute : oui."""
    try:
        from . import state
        from .platform import proc as procinfo
        if os.environ.get("CLAUDECODE") or os.environ.get(procinfo.BRAIN_VAR):
            return True
        pid = os.getpid() if pid is None else pid
        if state.cgroup_of(pid).rsplit("/", 1)[-1] == state.SERVICE_UNIT:
            return True
        for _ in range(64):
            if pid <= 1:
                return False
            name, parent = procinfo.name(pid), procinfo.parent(pid)
            if name is None or parent is None or name == "claude":
                return True
            pid = parent
    except Exception:  # noqa: BLE001
        return True
    return True


class ArretUrgence(PermissionError):
    """Arrêt d'urgence posé : rien n'est rétabli pour Claude."""


def restaurer(par: str = "utilisateur", apercu: bool = False) -> dict:
    """Rétablit le dernier lot pas encore annulé. `apercu` : dit ce qui serait fait, sans rien écrire.
    Chaque fichier est rejugé (`refus`) juste avant d'être écrit : jamais un fichier protégé.
    01/11 (relecture) : sous ESTOP, refusé à tout process de Claude, quel que soit `par` (outil MCP,
    `python -c`, `./run.sh annule` lancé par lui) ; seul le terminal de l'utilisateur rétablit alors."""
    if not apercu and config.ESTOP_FILE.exists() and vient_de_claude():
        raise ArretUrgence("arrêt d'urgence posé : rien n'est rétabli par Claude (demande : ./run.sh annule)")
    with _verrou():
        data = _lire()
        lot = next((x for x in reversed(data["lots"]) if not x.get("restaure") and x["fichiers"]), None)
        if lot is None:
            return {"lot": None, "apercu": apercu, "restaures": [], "supprimes": [], "laisses": [],
                    "resume": "Aucune écriture récente à annuler."}
        restaures: list[str] = []
        supprimes: list[str] = []
        laisses: list[dict] = []
        d = _lot_dir(lot)
        for f in reversed(lot["fichiers"]):
            chemin = str(f.get("chemin") or "")
            p = Path(chemin)
            pourquoi = refus(chemin, copie=False) if os.path.isabs(chemin) else "chemin relatif"
            if pourquoi:
                laisses.append({"chemin": chemin, "pourquoi": pourquoi})
                continue
            if f.get("hors_outil"):
                laisses.append({"chemin": chemin, "pourquoi": "modifié hors outil pendant le lot"})
                continue
            actuel = _sha(p)
            if actuel == f.get("avant"):
                continue  # déjà dans l'état d'avant
            if f.get("attente") and f.get("apres") is None:
                laisses.append({"chemin": chemin, "pourquoi": "état après l'écriture inconnu"})
                continue
            if actuel != f.get("apres"):
                laisses.append({"chemin": chemin, "pourquoi": "modifié depuis"})
                continue
            try:
                if f.get("avant") is None:  # créé par le lot : le retirer
                    if not apercu:
                        _dans_dossier(p, _retirer)
                    supprimes.append(chemin)
                    continue
                nom = str(f.get("copie") or "")
                contenu = (d / nom).read_bytes() if d is not None and re.fullmatch(r"[\w.-]+\.bin", nom) else b""
                if _sha_bytes(contenu) != f.get("avant"):
                    laisses.append({"chemin": chemin, "pourquoi": "copie abîmée"})
                    continue
                if not apercu:
                    _dans_dossier(p, _remplacer(contenu, f.get("mode")))
                restaures.append(chemin)
            except _Refus as exc:
                laisses.append({"chemin": chemin, "pourquoi": str(exc)})
            except OSError as exc:
                laisses.append({"chemin": chemin, "pourquoi": f"erreur ({exc.strerror or type(exc).__name__})"})
        if not apercu:
            lot["restaure"] = time.time()
            lot["par"] = str(par)[:20]
            _ecrire(data)
    age = max(0, int((time.time() - float(lot.get("fin") or 0)) // 60))
    parts = []
    if restaures:
        parts.append(f"{len(restaures)} fichier(s) rétabli(s)")
    if supprimes:
        parts.append(f"{len(supprimes)} fichier(s) créé(s) retiré(s)")
    if laisses:
        parts.append(f"{len(laisses)} laissé(s) tel(s) quel(s)")
    ign = sum(int(v) for v in (lot.get("ignores") or {}).values())
    resume = (f"Lot d'il y a {age} min {'serait' if apercu else 'est'} annulé : "
              + (", ".join(parts) or "rien à changer") + "."
              + (f" {ign} écriture(s) jamais sauvegardée(s) (protégées, secrètes ou trop grosses)." if ign else ""))
    return {"lot": lot["id"], "apercu": apercu, "restaures": restaures, "supprimes": supprimes,
            "laisses": laisses, "resume": resume}


def cli_main(argv: list[str]) -> int:
    """`./run.sh annule [--apercu | --liste]` : depuis le terminal de l'utilisateur."""
    if "--liste" in argv:
        for lot in lots():
            quand = time.strftime("%d/%m %H:%M", time.localtime(float(lot["fin"] or 0)))
            etat = " (annulé)" if lot["restaure"] else ""
            print(f"{lot['id']}  {quand}{etat}  {len(lot['fichiers'])} fichier(s)")
            for c in lot["fichiers"][:20]:
                print(f"    {c}")
        return 0
    try:
        r = restaurer(par="terminal", apercu="--apercu" in argv)
    except ArretUrgence:
        print("Arrêt d'urgence posé : rien n'est rétabli par Claude.")
        return 1
    print(r["resume"])
    for c in r["restaures"]:
        print(f"  rétabli : {c}")
    for c in r["supprimes"]:
        print(f"  retiré  : {c}")
    for x in r["laisses"]:
        print(f"  laissé  : {x['chemin']} ({x['pourquoi']})")
    return 0
