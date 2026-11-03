"""Médias riches dans le fil du HUD (22/10, plan P26 lot 1a, demande de l'utilisateur :
« dans le chat tu dois pouvoir afficher tout, pdf, image, vidéo… »).

PUBLIER un fichier = le vérifier puis le COPIER dans `runtime/medias/<id>.<ext>` (0600) :
le HUD ne lit jamais le disque, il demande `/api/media/<id>` au serveur, qui ne sert que
ces copies. Règles (sécurité du plan, prioritaire) :

- chemin ABSOLU, résolu (liens suivis), puis le fichier réellement OUVERT est revérifié
  (un lien échangé entre la vérification et l'ouverture ne passe pas) ;
- sources permises seulement (`source_roots`) ; jamais un secret d'Astrel
  (`guard.SECRET_PATHS`), ni ses dossiers privés (runtime hors pièces jointes et images,
  config, hooks, vault, logs), ni un élément caché (.env, .ssh, .git…) ni un nom de secret ;
- le TYPE est lu dans les OCTETS (`sniff`), jamais dans le nom : image (JPEG, PNG, GIF,
  WebP ; pas de SVG), PDF, son (MP3, Ogg, FLAC, WAV, M4A), vidéo (MP4, MOV, WebM, MKV) ;
- plafonds par genre (`LIMITS`) ; au-delà, ou au-delà du budget de copie d'un appel,
  le média est publié « Ouvrir seul » (`open_only`) : pas de copie, rien à servir, le HUD
  n'offre que le bouton Ouvrir (commande `media_open`, réservée au HUD) ;
- id aléatoire (24 hexadécimaux), purge à 7 jours et à 2 Go (les plus vieux d'abord).

Variantes servies : `thumb` (image, Pillow, 480 px, WebP en cache) et `page` (PDF, page n
en PNG par `pdftoppm`, mémoire bornée par `prlimit`, 10 s au plus, en cache). poppler lit un
PDF venu d'ailleurs hors de tout bac à sable : à relire par l'agent sécurité (risque du plan).

Descripteur (contrat avec le HUD, `hud/src/medias.js`) : {id, kind, name, mime, size, w, h,
pages, open_only, path} ; `path` = chemin d'origine, montré en Avancé seulement.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from . import config
from .platform import systeme

log = logging.getLogger("astrel.medias")

MEDIA_DIR = config.RUNTIME / "medias"
KEEP_S = 7 * 86400
MAX_TOTAL = 2 * 1024 ** 3
MB = 1024 * 1024
LIMITS = {"image": 25 * MB, "pdf": 50 * MB, "audio": 100 * MB, "video": 300 * MB}
COPY_BUDGET = 600 * MB      # copie au plus par appel : le canal de contrôle attend 8 s
MAX_ITEMS = 10
THUMB_PX = 480
# Pixels décodés au plus pour une miniature (compté APRÈS `draft` : un JPEG est décodé réduit).
# Mesuré le 22/10 : PNG 8000×8000 = 512 Mo de pointe dans le cœur, 6000×4000 = 204 Mo,
# JPEG 8000×8000 réduit au 1/8 = 28 Mo. Au-delà, pas de miniature : le HUD montre son repli.
THUMB_MAX_PIXELS = 25_000_000
THUMB_PARALLEL = 2          # miniatures calculées en même temps au plus (galerie de 10 grandes PNG)
PAGE_PX = 1100
PAGE_MAX = 2000
POPPLER_TIMEOUT_S = 10
POPPLER_MEMORY = 1024 * MB
POPPLER_PARALLEL = 2        # processus poppler en même temps au plus (pdfinfo et pdftoppm)
POPPLER_WAIT_S = 15         # attente d'une place ; au-delà, 404 (non retenu comme échec)
FAIL_KEEP_S = 3600          # un échec de poppler est retenu une heure (PDF piégé : pas de relance)
NAME_MAX = 120

ID_RE = re.compile(r"[0-9a-f]{24}")
_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
        "application/pdf": ".pdf", "audio/mpeg": ".mp3", "audio/ogg": ".ogg", "audio/flac": ".flac",
        "audio/wav": ".wav", "audio/mp4": ".m4a", "video/mp4": ".mp4", "video/quicktime": ".mov",
        "video/webm": ".webm", "video/x-matroska": ".mkv"}
KIND_LABEL = {"image": "image", "pdf": "PDF", "audio": "son", "video": "vidéo"}
# « Ouvrir seul » ouvre l'ORIGINAL : l'application du système est choisie d'après son NOM
# (xdg-open : un PNG nommé .html part dans le navigateur, fiche 1 de l'audit P26). L'extension
# doit donc correspondre au type lu dans les octets.
_OPEN_EXT = {"image/jpeg": (".jpg", ".jpeg", ".jpe", ".jfif"), "image/png": (".png",), "image/gif": (".gif",),
             "image/webp": (".webp",), "application/pdf": (".pdf",), "audio/mpeg": (".mp3",),
             "audio/ogg": (".ogg", ".oga", ".opus"), "audio/flac": (".flac",), "audio/wav": (".wav",),
             "audio/mp4": (".m4a", ".m4b", ".m4p"), "video/mp4": (".mp4", ".m4v"), "video/quicktime": (".mov", ".qt"),
             "video/webm": (".webm", ".mkv"), "video/x-matroska": (".mkv", ".webm")}

# Dossiers d'Astrel jamais montrés, même sous une racine permise (comme `whatsapp.file_refusal`).
_PRIVATE = ("runtime", "config", "hooks", "vault", "logs")
_SECRET_NAME = re.compile(
    r"(?:^|[^a-z0-9])(?:secrets?|credentials?|passwords?|passwd|mots?[-_ ]de[-_ ]passe|api[-_ ]?keys?"
    r"|private[-_ ]?keys?|id_(?:rsa|dsa|ecdsa|ed25519)|whatsapp-auth|whatsapp-media|hud-token|hud-session-key"
    r"|hud-profile)(?:[^a-z0-9]|$)", re.I)
_CONTROLS = re.compile(r"[\x00-\x1f\x7f‪-‮⁦-⁩/\\]")

_lock = threading.Lock()
_gen_locks: dict[str, threading.Lock] = {}
_poppler_slots = threading.BoundedSemaphore(POPPLER_PARALLEL)
_thumb_slots = threading.BoundedSemaphore(THUMB_PARALLEL)
_failed: dict[str, float] = {}      # variante → heure de l'échec (mémoire seulement)
_FAILED_MAX = 1000


def source_roots() -> list[Path]:
    """Dossiers d'où Astrel peut montrer un fichier (question 2 du plan, « Recommandé »).
    Noms anglais à côté des français : les mêmes dossiers sur un système en anglais."""
    home = Path.home()
    roots = [config.RUNTIME / "attachments", config.IMAGES_RUNTIME, config.WORKDIR]
    if systeme.UNIX:
        roots.append(Path("/tmp"))  # 03/11 : sous Windows, « \\tmp » à la racine du lecteur, pas le temporaire
    roots.append(Path(tempfile.gettempdir()))  # 03/11 : le dossier temporaire du système (Windows : %TEMP%)
    roots += [home / d for d in ("Images", "Téléchargements", "Bureau", "Pictures", "Downloads", "Desktop")]
    return roots


def _allowed_private() -> list[Path]:
    """Dans runtime/ : pièces jointes et images IA seulement ; les profils du navigateur
    (`config.IMAGES_PROFILS`) sont refusés AVANT ce test par `refusal`."""
    return [(config.RUNTIME / "attachments").resolve(), config.IMAGES_RUNTIME.resolve()]


def refusal(path: Path) -> str:
    """Raison de refuser `path` (chemin RÉSOLU), vide s'il peut être montré."""
    roots = []
    for r in source_roots():
        try:
            roots.append(r.resolve())
        except (OSError, RuntimeError):
            continue
    if not any(path != r and path.is_relative_to(r) for r in roots):
        return "hors des dossiers permis"
    from . import guard  # import tardif : guard importe beaucoup

    # Profils Chrome des comptes d'images IA (cookies) : jamais montrés, avant le reste de
    # runtime/imagegen qui l'est ; aussi dans `guard.SECRET_PATHS` depuis P40 (A0).
    if config.IMAGES_PROFILS.resolve() in (path, *path.parents):
        return "dossier privé d'Astrel"
    for s in guard.SECRET_PATHS:
        if path == s or path.is_relative_to(s):
            return "secret d'Astrel"
    root = config.ROOT.resolve()
    private = [(root / d).resolve() for d in _PRIVATE]
    if any(path.is_relative_to(d) for d in private) and not any(path.is_relative_to(a) for a in _allowed_private()):
        return "dossier privé d'Astrel (jetons, réglages, mémoire, journaux)"
    parts = path.parts[1:]
    if any(p.startswith(".") for p in parts):
        return "fichier ou dossier caché"
    if any(_SECRET_NAME.search(p) for p in parts):
        return "le nom ressemble à un secret"
    return ""


def sniff(head: bytes) -> tuple[str, str] | None:
    """(genre, type MIME) lus dans les premiers octets ; None si le type n'est pas affiché."""
    if head[:3] == b"\xff\xd8\xff":
        return "image", "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image", "image/png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image", "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", "image/webp"
    if head[:5] == b"%PDF-":
        return "pdf", "application/pdf"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio", "audio/wav"
    if head[:4] == b"fLaC":
        return "audio", "audio/flac"
    if head[:4] == b"OggS":
        return "audio", "audio/ogg"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE6 in (0xE2, 0xE4, 0xE6)):
        return "audio", "audio/mpeg"  # trame MPEG : synchro 11 bits + couche I, II ou III
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"M4A ", b"M4B ", b"M4P "):
            return "audio", "audio/mp4"
        if brand == b"qt  ":
            return "video", "video/quicktime"
        if brand in (b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"dash", b"M4V ",
                     b"MSNV", b"3gp4", b"3gp5"):
            return "video", "video/mp4"
        return None
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return ("video", "video/webm") if b"webm" in head[:64] else ("video", "video/x-matroska")
    return None


def _name(path: Path) -> str:
    name = _CONTROLS.sub("_", path.name).strip() or "fichier"
    if len(name) <= NAME_MAX:
        return name
    stem, dot, ext = name.rpartition(".")
    ext = ext[:10] if dot and stem else ""
    return (stem if ext else name)[:NAME_MAX - 12] + "…" + (f".{ext}" if ext else "")


def _meta_file(mid: str) -> Path:
    return MEDIA_DIR / f"{mid}.json"


def _write_private(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _image_size(path: Path) -> tuple[int | None, int | None]:
    """Largeur et hauteur TELLES QU'AFFICHÉES : une photo marquée EXIF « tourner de 90° »
    (orientations 5 à 8, téléphone tenu droit) a ses côtés échangés, comme sa miniature
    (`ImageOps.exif_transpose`). Sinon le HUD réserve un cadre au mauvais ratio (fiche 2)."""
    try:
        from PIL import Image

        with Image.open(path) as im:  # lit l'en-tête seulement
            w, h = int(im.width), int(im.height)
            try:
                turned = im.getexif().get(0x0112) in (5, 6, 7, 8)
            except Exception:
                turned = False
            return (h, w) if turned else (w, h)
    except Exception:
        return None, None


def label(raw: Any) -> str:
    """Nom du fichier pour un refus rendu à Claude (jamais la fin d'un chemin coupé)."""
    raw = str(raw or "")
    return _CONTROLS.sub("_", Path(raw).name)[:60] or raw[:60]


def _ext_ok(path: Path, mime: str) -> bool:
    return path.suffix.lower() in _OPEN_EXT.get(mime, ())


def _limited(cmd: list[str]) -> list[str]:
    """Mémoire bornée pour poppler (le PDF vient d'ailleurs)."""
    prlimit = shutil.which("prlimit")
    return [prlimit, f"--as={POPPLER_MEMORY}", "--"] + cmd if prlimit else cmd


def _failed_recently(key: str) -> bool:
    with _lock:
        at = _failed.get(key)
        return at is not None and time.time() - at < FAIL_KEEP_S


def _remember_failure(key: str) -> None:
    with _lock:
        if len(_failed) >= _FAILED_MAX:
            for old in sorted(_failed, key=_failed.get)[: _FAILED_MAX // 2]:
                del _failed[old]
        _failed[key] = time.time()


def _poppler(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess | None:
    """Lance poppler, mémoire et temps bornés, `POPPLER_PARALLEL` à la fois au plus.
    None si aucune place ne s'est libérée à temps (rien n'a tourné)."""
    if not _poppler_slots.acquire(timeout=POPPLER_WAIT_S):
        log.info("poppler occupé : requête abandonnée")
        return None
    try:
        return subprocess.run(_limited(cmd), capture_output=True, timeout=POPPLER_TIMEOUT_S, cwd="/tmp",
                              env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}, **kw)
    finally:
        _poppler_slots.release()


def _pdf_pages(path: Path) -> int | None:
    exe = shutil.which("pdfinfo")
    if not exe:
        return None
    try:
        out = _poppler([exe, "--", str(path)], text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"^Pages:\s+(\d+)", out.stdout, re.M) if out is not None else None
    return int(m.group(1)) if m else None


def publish(raw: Any, budget: list[int] | None = None) -> dict[str, Any]:
    """Vérifie et copie UN fichier ; rend son descripteur. ValueError (raison courte) sinon.
    `budget` : [octets encore copiables dans cet appel], décrémenté ; épuisé = « Ouvrir seul »."""
    raw = str(raw or "").strip()
    if not raw:
        raise ValueError("chemin vide")
    asked = Path(raw).expanduser()
    if not asked.is_absolute():
        raise ValueError("chemin absolu exigé")
    try:
        real = asked.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("fichier introuvable") from None
    why = refusal(real)
    if why:
        raise ValueError(why)
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)  # sans lien : ouvrir_sans_lien
    try:
        fd = systeme.ouvrir_sans_lien(real, flags)
    except OSError as exc:
        raise ValueError(f"fichier illisible ({exc.strerror})") from None
    dest: Path | None = None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("pas un fichier ordinaire")
        if st.st_nlink > 1:
            # Un lien dur dans un dossier permis peut être un fichier interdit (même disque) :
            # ni le chemin ni `opened_path` ne le disent (fiche 1 de l'audit P26).
            raise ValueError("lien dur (fichier à plusieurs noms) : non montré")
        from .platform import proc

        opened = proc.opened_path(fd) or real
        why = refusal(opened)
        if why:
            raise ValueError(why)
        if st.st_size <= 0:
            raise ValueError("fichier vide")
        head = systeme.pread(fd, 64, 0)
        found = sniff(head)
        if found is None:
            raise ValueError("type non affiché (image, PDF, son ou vidéo seulement)")
        kind, mime = found
        mid = secrets.token_hex(12)
        desc: dict[str, Any] = {"id": mid, "kind": kind, "name": _name(opened), "mime": mime, "size": st.st_size,
                                "w": None, "h": None, "pages": None, "open_only": False, "path": str(opened)}
        left = budget[0] if budget else COPY_BUDGET
        if (st.st_size > LIMITS[kind] or st.st_size > left) and not _ext_ok(opened, mime):
            raise ValueError(f"trop gros pour l'aperçu, et l'extension ne correspond pas au contenu "
                             f"({KIND_LABEL[kind]}) : pas d'ouverture")
        MEDIA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        purge()
        if st.st_size > LIMITS[kind] or st.st_size > left:
            desc["open_only"] = True
        else:
            target = MEDIA_DIR / f"{mid}{_EXT[mime]}"
            out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            dest = target
            total = 0
            with os.fdopen(out, "wb") as fh:
                while chunk := os.read(fd, 1 << 20):
                    total += len(chunk)
                    if total > LIMITS[kind]:
                        raise ValueError("a grossi pendant la copie")
                    fh.write(chunk)
            desc["size"] = total
            if budget:
                budget[0] -= total
            if kind == "image":
                desc["w"], desc["h"] = _image_size(target)
            elif kind == "pdf":
                desc["pages"] = _pdf_pages(target)
        _write_private(_meta_file(mid), json.dumps({**desc, "ts": time.time()}, ensure_ascii=False).encode("utf-8"))
        return desc
    except BaseException:
        if dest is not None:
            dest.unlink(missing_ok=True)
        raise
    finally:
        os.close(fd)


def publish_many(paths: Any, limit: int = MAX_ITEMS) -> tuple[list[dict], list[dict]]:
    """Publie une liste de chemins → (descripteurs, refus [{fichier, raison}]). Dix au plus."""
    if isinstance(paths, str):
        paths = [paths]
    if not isinstance(paths, (list, tuple)):
        return [], []
    budget = [COPY_BUDGET]
    ok: list[dict] = []
    refused: list[dict] = []
    for raw in list(paths)[:limit]:
        name = label(raw)
        try:
            ok.append(publish(raw, budget))
        except ValueError as exc:
            refused.append({"fichier": name, "raison": str(exc)})
        except OSError as exc:
            log.warning("média non publié (%s) : %s", name, exc)
            refused.append({"fichier": name, "raison": "erreur d'écriture"})
    if ok or refused:
        log.info("médias publiés : %d, refusés : %d", len(ok), len(refused))
    return ok, refused


def summary(ok: list[dict], refused: list[dict]) -> dict[str, Any]:
    """Résultat court pour Claude (le canal de contrôle rend 4 Ko au plus)."""
    out: dict[str, Any] = {"acceptes": [f"{d['name'][:60]} ({KIND_LABEL[d['kind']]}"
                                        + (", Ouvrir seul : trop gros pour l'aperçu" if d["open_only"] else "") + ")"
                                        for d in ok]}
    if refused:
        out["refuses"] = refused
    return out


def get(mid: Any) -> dict[str, Any] | None:
    """Descripteur gardé d'un id, None s'il est inconnu, mal formé ou purgé."""
    mid = str(mid or "")
    if not ID_RE.fullmatch(mid):
        return None
    try:
        meta = json.loads(_meta_file(mid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) and meta.get("id") == mid and meta.get("mime") in _EXT else None


def _copy(meta: dict) -> Path | None:
    path = MEDIA_DIR / f"{meta['id']}{_EXT[meta['mime']]}"
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return path if stat.S_ISREG(st.st_mode) else None


def _gen_lock(key: str) -> threading.Lock:
    with _lock:
        return _gen_locks.setdefault(key, threading.Lock())


def _thumb(meta: dict, src: Path) -> Path | None:
    out = MEDIA_DIR / f"{meta['id']}.thumb.webp"
    with _gen_lock(out.name):
        if out.exists():
            return out
        if _failed_recently(out.name):
            return None
        if not _thumb_slots.acquire(timeout=POPPLER_WAIT_S):
            return None
        try:
            from PIL import Image, ImageOps

            with Image.open(src) as im:
                im.draft("RGB", (THUMB_PX, THUMB_PX))  # JPEG : décodage réduit, mémoire bornée
                if im.width * im.height > THUMB_MAX_PIXELS:  # taille APRÈS draft = ce qui sera décodé
                    log.info("miniature non faite (%s) : %d×%d px", meta["id"], im.width, im.height)
                    _remember_failure(out.name)
                    return None
                im = ImageOps.exif_transpose(im)
                im.thumbnail((THUMB_PX, THUMB_PX))
                im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
                import io

                buf = io.BytesIO()
                im.save(buf, "WEBP", quality=80)
            _write_private(out, buf.getvalue())
            return out
        except Exception as exc:
            log.info("miniature impossible (%s) : %s", meta["id"], exc)
            _remember_failure(out.name)
            return None
        finally:
            _thumb_slots.release()


def _page(meta: dict, src: Path, n: int) -> Path | None:
    pages = meta.get("pages") or 0
    if not 1 <= n <= min(pages, PAGE_MAX):
        return None
    out = MEDIA_DIR / f"{meta['id']}.p{n}.png"
    exe = shutil.which("pdftoppm")
    if not exe:
        return None
    with _gen_lock(out.name):
        if out.exists():
            return out
        if _failed_recently(out.name):
            return None  # PDF piégé (10 s, 1 Go) : pas de relance à chaque requête du HUD
        root = MEDIA_DIR / f".{meta['id']}.p{n}.{secrets.token_hex(4)}"
        made = root.with_name(root.name + ".png")
        try:
            done = _poppler([exe, "-f", str(n), "-l", str(n), "-singlefile", "-scale-to", str(PAGE_PX),
                             "-png", "--", str(src), str(root)], check=True)
            if done is None:
                return None  # pas de place : rien n'a tourné, la page pourra être redemandée
            if not made.is_file() or made.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
                _remember_failure(out.name)
                return None
            os.chmod(made, 0o600)
            os.replace(made, out)
            return out
        except (OSError, subprocess.SubprocessError) as exc:
            log.info("page %d du PDF %s impossible : %s", n, meta["id"], type(exc).__name__)
            _remember_failure(out.name)
            return None
        finally:
            made.unlink(missing_ok=True)


def serve(mid: Any, variant: str = "", n: Any = "") -> tuple[Path, str] | None:
    """(fichier, type MIME) à servir pour `/api/media/<id>` ; None = 404 (sans dire pourquoi)."""
    meta = get(mid)
    if meta is None or meta.get("open_only"):
        return None
    src = _copy(meta)
    if src is None:
        return None
    if not variant:
        return src, meta["mime"]
    if variant == "thumb" and meta["kind"] == "image":
        found = _thumb(meta, src)
        return (found, "image/webp") if found else None
    if variant == "page" and meta["kind"] == "pdf":
        try:
            page = int(str(n or "1"))
        except ValueError:
            return None
        found = _page(meta, src, page)
        return (found, "image/png") if found else None
    return None


def open_target(mid: Any) -> Path | None:
    """Fichier à ouvrir dans l'application du système (`media_open`) : la copie si elle
    existe, sinon l'original (« Ouvrir seul »), revérifié maintenant."""
    meta = get(mid)
    if meta is None:
        return None
    copy = _copy(meta)
    if copy is not None:
        return copy
    try:
        real = Path(str(meta.get("path") or "")).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    # L'application est choisie d'après le NOM : extension conforme au type, exigée (fiche 1).
    if refusal(real) or not _ext_ok(real, str(meta.get("mime"))):
        return None
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)  # sans lien : ouvrir_sans_lien
    try:
        fd = systeme.ouvrir_sans_lien(real, flags)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
            return None
        found = sniff(systeme.pread(fd, 64, 0))
    except OSError:
        return None
    finally:
        os.close(fd)
    return real if found and found[1] == meta["mime"] else None


def purge(now: float | None = None) -> int:
    """Efface les médias de plus de 7 jours, puis les plus vieux tant que le dossier dépasse 2 Go."""
    now = time.time() if now is None else now
    try:
        entries = [p for p in MEDIA_DIR.iterdir() if p.is_file() and not p.is_symlink()]
    except OSError:
        return 0
    groups: dict[str, list[Path]] = {}
    for p in entries:
        key = p.name.lstrip(".").split(".", 1)[0]
        groups.setdefault(key, []).append(p)
    ages: list[tuple[float, str, int]] = []
    for key, files in groups.items():
        try:
            stats = [f.stat() for f in files]
        except OSError:
            continue
        ages.append((min(s.st_mtime for s in stats), key, sum(s.st_size for s in stats)))
    ages.sort()
    total = sum(a[2] for a in ages)
    removed = 0
    for mtime, key, size in ages:
        if mtime > now - KEEP_S and total <= MAX_TOTAL:
            break
        for f in groups[key]:
            f.unlink(missing_ok=True)
        total -= size
        removed += 1
    return removed
