"""Pièces jointes envoyées à Astrel (14/10, demande de l'utilisateur) : fichiers et
images depuis le HUD (bouton 📎, Ctrl+V, glisser-déposer) ou le mini-chat de
la bulle (Ctrl+V).

- Chaque pièce est ENREGISTRÉE dans `runtime/attachments/` (0600, gardée
  ATTACH_KEEP_DAYS jours) : Claude la relit avec Read quand il en a besoin.
- Une image part AUSSI dans le message, en bloc image, après réduction à
  IMAGE_MAX_BYTES (≈ 256 Ko, côté long ≤ IMAGE_MAX_SIDE) : une capture brute
  de 2 à 5 Mo coûterait bien plus de tokens pour rien (idée HERMES,
  vision_tools_history_budget). Le HUD et la bulle réduisent déjà ; ici c'est
  le filet de sécurité.
- Un autre fichier n'est joint que par son CHEMIN, dans une note du message.

Entrées acceptées (commande `say`, champ `attachments`) :
    {"name": "plan.pdf", "type": "application/pdf", "data": "<base64>"}
    {"path": "<runtime/attachments/…>"}   (déjà écrit par la bulle ; ailleurs : refusé)
"""
from __future__ import annotations

import base64
import shlex
import binascii
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger("astrel.attachments")

ATTACH_DIR = config.RUNTIME / "attachments"
MAX_FILE_BYTES = 5 * 1024 * 1024   # le WebSocket du HUD accepte 8 Mo, base64 compris
MAX_FILES = 6
IMAGE_MAX_BYTES = 256 * 1024
IMAGE_MAX_SIDE = 1568              # au-delà, Claude réduit lui-même l'image : autant l'envoyer réduite
ATTACH_KEEP_DAYS = 7
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
# Lus par tools/lire-document.sh (markitdown, extras [pdf,docx,xlsx] seulement) plutôt que par Read
OFFICE_SUFFIXES = {".docx", ".xlsx"}


@dataclass
class Prepared:
    images: list[dict] = field(default_factory=list)   # blocs « source » pour Brain.send
    notes: list[str] = field(default_factory=list)      # ajoutées au texte envoyé à Claude
    shown: list[dict] = field(default_factory=list)     # pour le fil du HUD : nom, image ou non
    # `prepare(partial=True)` : pièces refusées une à une (id, nom, raison), les autres partent
    refused: list[dict] = field(default_factory=list)
    # 16/10 : `id` (HUD) des pièces acceptées, pour les rendre si l'envoi au cerveau échoue
    ids: list[str] = field(default_factory=list)


def _safe_name(name: str) -> str:
    base = Path(str(name or "fichier")).name
    base = re.sub(r"[^\w.\- ]+", "_", base).strip(" .") or "fichier"
    return base[:80]


def _media_type(name: str, declared: str, data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    declared = str(declared or "").lower()
    return "" if declared in IMAGE_TYPES else declared  # « image/png » déclaré mais pas une image : non


def shrink_image(data: bytes, max_bytes: int = IMAGE_MAX_BYTES, max_side: int = IMAGE_MAX_SIDE) -> tuple[bytes, str]:
    """JPEG d'au plus `max_bytes` (qualité puis taille réduites par paliers).
    Une image déjà assez petite et assez légère est rendue telle quelle."""
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("image illisible")
    if img.ndim == 3 and img.shape[2] == 4:  # transparence : sur fond blanc, pas noir
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        img = (img[:, :, :3].astype(np.float32) * alpha + 255.0 * (1.0 - alpha)).astype(np.uint8)
    h, w = img.shape[:2]
    if len(data) <= max_bytes and max(h, w) <= max_side:
        return data, ""
    scale = min(1.0, max_side / float(max(h, w)))
    out = b""
    for _ in range(6):
        cur = img if scale >= 1.0 else cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                                                  interpolation=cv2.INTER_AREA)
        for quality in (82, 65):
            ok, buf = cv2.imencode(".jpg", cur, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if ok:
                out = buf.tobytes()
                if len(out) <= max_bytes:
                    return out, "image/jpeg"
        # Le poids suit la surface : viser directement la bonne taille.
        scale *= max(0.3, min(0.85, (max_bytes / max(1, len(out))) ** 0.5 * 0.95))
    return out, "image/jpeg"


def _cleanup(now: float) -> None:
    limit = now - ATTACH_KEEP_DAYS * 86400
    try:
        for p in ATTACH_DIR.iterdir():
            if p.is_file() and p.stat().st_mtime < limit:
                p.unlink(missing_ok=True)
    except OSError:
        pass


def _write(name: str, data: bytes, now: float) -> Path:
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)
    ATTACH_DIR.chmod(0o700)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    path = ATTACH_DIR / f"{stamp}-{name}"
    n = 1
    while path.exists():
        n += 1
        path = ATTACH_DIR / f"{stamp}-{n}-{name}"
    path.write_bytes(data)
    path.chmod(0o600)
    return path


def save_bytes(name: str, data: bytes) -> Path:
    """Pour la bulle : enregistre une pièce jointe (image collée, fichier
    déposé) et rend son chemin, à envoyer au cœur par `{"path": …}` (le canal
    de contrôle ne prend pas de gros messages)."""
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"{name} : plus de {MAX_FILE_BYTES // (1024 * 1024)} Mo")
    return _write(_safe_name(name), data, time.time())


_HUD_FILE = re.compile(r"[\w.\- ]{1,120}")


def hud_image(file: str) -> tuple[bytes, str] | None:
    """19/10 (demande : voir l'image dans le fil après l'envoi) : contenu et type d'une IMAGE
    de ATTACH_DIR, servie au HUD par `/api/attachment/<file>`. Liste blanche : un simple nom
    de fichier (ni chemin, ni « .. », ni fichier caché), un fichier ordinaire placé DIRECTEMENT
    dans ATTACH_DIR (pas de lien symbolique), reconnu comme image par ses premiers octets
    (pas par son nom). Sinon None : rien d'autre du disque ne sort par là."""
    name = str(file or "")
    if not _HUD_FILE.fullmatch(name) or name.startswith("."):
        return None
    root = ATTACH_DIR.resolve()
    path = ATTACH_DIR / name
    try:
        if path.is_symlink() or not path.is_file() or path.resolve().parent != root:
            return None
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    media = _media_type(name, "", data)
    return (data, media) if media in IMAGE_TYPES else None


def prepare(items: Any, partial: bool = False) -> Prepared:
    """Enregistre les pièces jointes et prépare ce qui part chez Claude.
    Lève ValueError avec un message pour l'utilisateur si une pièce est refusée.
    `partial` (16/10) : une pièce refusée (vide, trop grosse, illisible) ne bloque
    plus les autres ; elle est rangée dans `refused`, les valides partent. Une liste
    illisible ou trop longue est toujours refusée en entier."""
    if not items:
        return Prepared()
    if not isinstance(items, list):
        raise ValueError("pièces jointes illisibles")
    if len(items) > MAX_FILES:
        raise ValueError(f"{MAX_FILES} pièces jointes au plus par message")
    now = time.time()
    _cleanup(now)
    out = Prepared()
    for item in items:
        aid = str(item.get("id") or "") if isinstance(item, dict) else ""
        try:
            _prepare_one(item, out, now)
            if aid:
                out.ids.append(aid)
        except ValueError as exc:
            if not partial:
                raise
            name = _safe_name(str(item.get("name") or "fichier")) if isinstance(item, dict) else "fichier"
            out.refused.append({"id": aid, "name": name, "error": str(exc)})
            log.warning("pièce jointe refusée : %s", exc)
    return out


def _prepare_one(item: Any, out: Prepared, now: float) -> None:
    root = ATTACH_DIR.resolve()
    if not isinstance(item, dict):
        raise ValueError("pièce jointe illisible")
    if item.get("path"):
        path = Path(str(item["path"])).expanduser().resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError(f"pièce jointe hors de {ATTACH_DIR} : {item['path']}")
        data, name = path.read_bytes(), path.name
    else:
        name = _safe_name(str(item.get("name") or "fichier"))
        try:
            data = base64.b64decode(str(item.get("data") or ""), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError(f"{name} : contenu illisible") from None
        path = None
    if not data:
        raise ValueError(f"{name} : fichier vide")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"{name} : {len(data) // (1024 * 1024)} Mo, {MAX_FILE_BYTES // (1024 * 1024)} Mo au plus. "
                         "Pour un gros fichier, donne-moi plutôt son chemin.")
    media = _media_type(name, str(item.get("type") or ""), data)
    if media in IMAGE_TYPES:
        try:
            small, new_media = shrink_image(data)
        except Exception as exc:  # image abîmée : jointe comme simple fichier
            log.warning("image %s non réduite : %s", name, exc)
            small, new_media = b"", ""
        if small:
            if new_media and path is None:
                name = re.sub(r"\.\w+$", "", name) + ".jpg"
            saved = path or _write(name, small, now)
            out.images.append({"type": "base64", "media_type": new_media or media,
                               "data": base64.b64encode(small).decode()})
            out.notes.append(f"[Image jointe par l'utilisateur (aussi enregistrée : {saved})]")
            # 19/10 : `file` = nom dans ATTACH_DIR (jamais un chemin) ; le fil du HUD en montre
            # la vignette par /api/attachment/<file> (`hud_image`).
            out.shown.append({"name": name, "image": True, "file": saved.name})
            log.info("image jointe : %s (%d Ko envoyés)", saved, len(small) // 1024)
            return
    saved = path or _write(name, data, now)
    kind = media or "type inconnu"
    # 20/10 (plan P12, lot 1) : Read ne rend rien d'utile d'un Word/Excel/PowerPoint ;
    # markitdown (venv à part) les convertit en Markdown. Indice ici, pas dans le prompt.
    reader = (f"tools/lire-document.sh {shlex.quote(str(saved))}" if saved.suffix.lower() in OFFICE_SUFFIXES
              else "Read")
    out.notes.append(f"[Fichier joint par l'utilisateur : {saved} ({kind}, {max(1, len(data) // 1024)} Ko). "
                     f"Lis-le avec {reader} si la demande le concerne.]")
    out.shown.append({"name": name, "image": False})
    log.info("fichier joint : %s (%d Ko)", saved, len(data) // 1024)
