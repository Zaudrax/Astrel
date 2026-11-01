"""Icône d'un service pour la page « Réglages » (19/10, plan P10, demande de l'utilisateur :
chaque connecteur affiche l'icône de son service, stockée en LOCAL).

Les icônes sont des SVG de simple-icons (CC0) copiés une fois par `tools/icones.py` dans
`hud/assets/icons/<slug>.svg`, avec `index.json` (slug → titre, couleur de la marque).
`for_name` ne télécharge jamais rien. `for_name("claude.ai Google Drive")` retire le préfixe,
normalise comme simple-icons (`googledrive`), essaie aussi sans le dernier mot (« Claude
Docs » → `claude`) et rend `{src, color, letter}` ; sans icône, `src` est None et la
couleur est une teinte tirée du nom (stable d'une fois à l'autre), pour une pastille à lettre.
La couleur est toujours lisible sur le fond sombre du HUD (Vercel est noir).

Icône récupérée d'elle-même (19/10, demande de l'utilisateur : « si on ajoute un connecteur,
celui-ci doit avoir son icône qui arrive en même temps ») : `fetch_missing`, appelé en FOND
par `claudeai` après chaque découverte, cherche UNE fois dans simple-icons (même paquet npm,
même version FIXÉE que `tools/icones.py`, servi fichier par fichier par jsDelivr, seul hôte
permis, HTTPS, sans redirection) l'icône d'un connecteur qui n'en a pas en local. Données de
simple-icons vérifiées par empreinte sha256 et gardées dans `runtime/icones-cache/` ; SVG
contrôlé (balises et attributs d'une liste fermée : ni script, ni foreignObject, ni on*, ni
référence externe), écrit dans `hud/assets/icons/auto/` (servi par le HUD, comme le reste).
Licence autre que CC0 : écartée. Échec ou absence : pastille à lettre, NÉGATIF daté dans
`runtime/icones-auto.json` (pas de nouvel essai avant `RETRY_*`), journalisé, jamais d'erreur
côté Réglages. Le nom du connecteur n'entre jamais dans un chemin : seul un slug
`[a-z0-9]` venant des données vérifiées y sert.
"""
from __future__ import annotations

import colorsys
import hashlib
import json
import logging
import os
import re
import threading
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from typing import Iterable

from . import config

log = logging.getLogger("astrel.icones")

ICON_DIR = config.HUD_DIR / "assets" / "icons"
URL_PREFIX = "assets/icons/"  # servi par le HUD (dossier statique), CSP img-src 'self'
PREFIX = "claude.ai "
MIN_LUMINANCE = 0.12  # en dessous, la couleur de la marque se perd sur le fond du HUD
# Remplacements de `titleToSlug` (simple-icons, sdk.mjs) avant de retirer tout le reste.
_SLUG_CHARS = {"+": "plus", ".": "dot", "&": "and", "đ": "d", "ħ": "h", "ı": "i", "ĸ": "k", "ŀ": "l",
               "ł": "l", "ß": "ss", "ŧ": "t", "ø": "o"}
_NOT_SLUG = re.compile(r"[^a-z0-9]")
_HEX = re.compile(r"[0-9A-Fa-f]{6}")

_SAFE_SLUG = re.compile(r"[a-z0-9]{1,64}")

# --- récupération automatique (voir l'en-tête) ---
AUTO_SUBDIR = "auto"
AUTO_DIR: Path | None = None  # None : ICON_DIR / AUTO_SUBDIR (les tests le déplacent)
STATE_FILE = config.RUNTIME / "icones-auto.json"
SIMPLE_ICONS_VERSION = "16.35.0"  # même version que tools/icones.py ; changer aussi DATA_SHA256
CDN_HOST = "cdn.jsdelivr.net"
ALLOWED_HOSTS = frozenset({CDN_HOST})
CDN_BASE = f"https://{CDN_HOST}/npm/simple-icons@{SIMPLE_ICONS_VERSION}/"
DATA_URL = CDN_BASE + "data/simple-icons.json"
# sha256 de package/data/simple-icons.json du paquet npm 16.35.0 (vérifié le 19/10 : identique sur jsDelivr).
DATA_SHA256 = "243a08e9148567a0a463939cc92674887faf5314f86eece88a76d6f6ca822872"
DATA_FILE = config.RUNTIME / "icones-cache" / f"simple-icons-{SIMPLE_ICONS_VERSION}.json"
ALLOWED_LICENSES = ("CC0-1.0",)
DATA_MAX_BYTES = 1_000_000   # 457 Ko en 16.35
SVG_MAX_BYTES = 64_000       # la plus grosse icône de simple-icons 16.35 fait 54 Ko
TIMEOUT_S = 6.0
PASS_BUDGET_S = 30.0         # une passe ne dure jamais plus (le reste attend la suivante)
MAX_PER_PASS = 20
RETRY_ABSENT_S = 30 * 86400  # absente de simple-icons, licence, SVG refusé
RETRY_ERROR_S = 86400        # réseau, délai, empreinte fausse
_TRANSPORT = None            # httpx.MockTransport dans les tests (aucun appel réseau réel)

_lock = threading.Lock()
_fetch_lock = threading.Lock()
_table_cache: dict[str, dict] | None = None
# ((mtime, taille) des deux index, clé → (dossier "" ou "auto", slug, hex))
_cache: tuple[tuple, dict[str, tuple[str, str, str]]] | None = None


def _auto_dir() -> Path:
    return AUTO_DIR if AUTO_DIR is not None else ICON_DIR / AUTO_SUBDIR


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def slugify(text: str) -> str:
    """Comme `titleToSlug` de simple-icons : « Google Drive » → `googledrive`."""
    low = "".join(_SLUG_CHARS.get(c, c) for c in str(text).lower())
    return _NOT_SLUG.sub("", unicodedata.normalize("NFD", low))


def _index() -> dict[str, tuple[str, str, str]]:
    """Clé (slug, titre ou alias normalisé) → (sous-dossier, slug, couleur). Icônes copiées
    d'abord, puis celles récupérées d'elles-mêmes (`auto/`). Relu si un des index change."""
    global _cache
    sources = (("", ICON_DIR / "index.json"), (AUTO_SUBDIR, _auto_dir() / "index.json"))
    stamp = tuple((str(path), _stamp(path)) for _, path in sources)
    if all(s is None for _, s in stamp):
        return {}
    with _lock:
        if _cache and _cache[0] == stamp:
            return _cache[1]
    found: dict[str, tuple[str, str, str]] = {}
    for sub, path in sources:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("index des icônes illisible (%s) : %s", path.name, exc)
            data = {}
        icons = data.get("icons") if isinstance(data, dict) else None
        for slug, meta in (icons or {}).items() if isinstance(icons, dict) else ():
            if slugify(slug) != slug or not isinstance(meta, dict) or not _HEX.fullmatch(str(meta.get("hex", ""))):
                continue
            entry = (sub, slug, "#" + str(meta["hex"]).upper())
            for key in (slug, slugify(meta.get("title", "")), *map(slugify, meta.get("aliases") or [])):
                if key:
                    found.setdefault(key, entry)
    with _lock:
        _cache = (stamp, found)
    return found


def _luminance(color: str) -> float:
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def readable(color: str) -> str:
    """Couleur éclaircie (vers le blanc) jusqu'à rester lisible sur le fond sombre."""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    for step in range(11):
        t = step / 10
        mixed = "#%02X%02X%02X" % tuple(round(c + (255 - c) * t) for c in (r, g, b))
        if _luminance(mixed) >= MIN_LUMINANCE:
            return mixed
    return "#FFFFFF"


def _hue(text: str) -> str:
    """Teinte tirée du nom (crc32 : stable d'un lancement à l'autre, contrairement à hash())."""
    h = zlib.crc32(text.casefold().encode("utf-8")) % 360
    r, g, b = colorsys.hls_to_rgb(h / 360, 0.5, 0.55)
    return readable("#%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255)))


def for_name(name: str) -> dict:
    """{src, color, letter} : `src` = chemin de l'icône dans le HUD, ou None (pastille à lettre)."""
    short = str(name).removeprefix(PREFIX).strip()
    letter = next((c.upper() for c in short if c.isalnum()), "?")
    words = short.split()
    tries = [slugify(short)] + ([slugify(" ".join(words[:-1]))] if len(words) > 1 else [])
    index = _index()
    for key in tries:
        hit = index.get(key) if key else None
        if not hit:
            continue
        sub, slug, color = hit
        folder, url = (_auto_dir(), f"{URL_PREFIX}{AUTO_SUBDIR}/") if sub else (ICON_DIR, URL_PREFIX)
        if (folder / f"{slug}.svg").is_file():
            return {"src": f"{url}{slug}.svg", "color": readable(color), "letter": letter}
    return {"src": None, "color": _hue(short or str(name)), "letter": letter}


# --------------------------------------------------------------------------- récupération automatique

def _download(url: str, max_bytes: int) -> bytes | None:
    """GET HTTPS vers un hôte de `ALLOWED_HOSTS` seulement, sans redirection, délai court,
    `max_bytes` au plus (décompressé). None sur tout échec (journalisé)."""
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS or parts.port not in (None, 443)
            or parts.username or parts.password):
        log.warning("icône : adresse refusée %r", url)
        return None
    import httpx  # au premier usage seulement : le cœur démarre sans payer l'import

    try:
        with httpx.Client(timeout=TIMEOUT_S, follow_redirects=False, transport=_TRANSPORT) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    log.info("icône : %s → HTTP %s", url, resp.status_code)
                    return None
                declared = resp.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > max_bytes:
                    log.warning("icône : %s trop gros (%s octets annoncés)", url, declared)
                    return None
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > max_bytes:
                        log.warning("icône : %s dépasse %d octets, abandon", url, max_bytes)
                        return None
                return bytes(body)
    except Exception as exc:  # httpx.HTTPError, TLS, décodage… : jamais d'erreur côté Réglages
        log.info("icône : %s non téléchargé : %s", url, exc)
        return None


_SVG_NS = "{http://www.w3.org/2000/svg}"
_SVG_TAGS = frozenset({"svg", "title", "desc", "g", "path", "circle", "ellipse", "rect", "line", "polyline",
                       "polygon"})
_SVG_ATTRS = frozenset({"role", "viewBox", "d", "fill", "fill-rule", "clip-rule", "transform", "cx", "cy", "r",
                        "rx", "ry", "x", "y", "x1", "y1", "x2", "y2", "width", "height", "points", "opacity",
                        "fill-opacity", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin"})
_SVG_VALUE = re.compile(r"[A-Za-z0-9 \t\r\n.,+\-#%()]*")
_XML_DECL = re.compile(r"\A\s*<\?xml\s[^<>?]*\?>")  # `<?xml-stylesheet …?>` n'en est PAS une


def safe_svg(data: bytes) -> bool:
    """Vrai si `data` est un SVG SIMPLE : balises et attributs d'une liste fermée, aucune
    déclaration (DOCTYPE, ENTITY), commentaire, CDATA, instruction, attribut d'espace de
    noms (xlink:href), `style`, `href`, événement on*, ni `url(` ou `:` dans une valeur."""
    if not data or len(data) > SVG_MAX_BYTES:
        return False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    body = _XML_DECL.sub("", text, count=1)
    if "<!" in body or "<?" in body:
        return False
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return False
    if root.tag != _SVG_NS + "svg":
        return False
    for node in root.iter():
        if not isinstance(node.tag, str) or not node.tag.startswith(_SVG_NS):
            return False
        if node.tag[len(_SVG_NS):] not in _SVG_TAGS:
            return False
        for key, value in node.attrib.items():
            if key not in _SVG_ATTRS or not _SVG_VALUE.fullmatch(value) or "url(" in value.lower():
                return False
    return True


def _read_state() -> dict[str, dict]:
    """Négatifs datés {clé: {"at": s, "why": raison}} ; oubliés si la version change."""
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != SIMPLE_ICONS_VERSION:
        return {}
    misses = data.get("absent")
    return {str(k): v for k, v in misses.items() if isinstance(v, dict)} if isinstance(misses, dict) else {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _fresh_miss(miss: dict | None, now: float) -> bool:
    if not miss:
        return False
    try:
        at = float(miss.get("at", 0))
    except (TypeError, ValueError):
        return False
    wait = RETRY_ERROR_S if miss.get("why") == "réseau" else RETRY_ABSENT_S
    return 0 <= now - at < wait


def _table() -> dict[str, dict] | None:
    """Clé (slug, titre, alias normalisés) → icône des données de simple-icons, vérifiées par
    empreinte. Fichier gardé en cache ; téléchargé une fois s'il manque. None si indisponible."""
    global _table_cache
    if _table_cache is not None:
        return _table_cache
    raw = None
    try:
        raw = DATA_FILE.read_bytes()
    except OSError:
        pass
    if raw is not None and hashlib.sha256(raw).hexdigest() != DATA_SHA256:
        log.warning("icône : cache des données de simple-icons abîmé, relu")
        raw = None
    if raw is None:
        raw = _download(DATA_URL, DATA_MAX_BYTES)
        if raw is None:
            return None
        if hashlib.sha256(raw).hexdigest() != DATA_SHA256:
            log.warning("icône : données de simple-icons d'empreinte inattendue, écartées")
            return None
        try:
            DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = DATA_FILE.with_name(DATA_FILE.name + ".tmp")
            tmp.write_bytes(raw)
            os.replace(tmp, DATA_FILE)
        except OSError as exc:
            log.warning("icône : données de simple-icons non gardées : %s", exc)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    table: dict[str, dict] = {}
    for icon in data if isinstance(data, list) else ():
        if not isinstance(icon, dict) or not isinstance(icon.get("title"), str):
            continue
        slug = icon.get("slug") or slugify(icon["title"])
        aliases = icon.get("aliases")
        aka = aliases.get("aka") if isinstance(aliases, dict) else None
        icon = {**icon, "slug": slug, "aka": [a for a in aka if isinstance(a, str)] if isinstance(aka, list) else []}
        for key in (slug, slugify(icon["title"]), *map(slugify, icon["aka"])):
            if key:
                table.setdefault(key, icon)
    _table_cache = table
    return table


def _store(icon: dict, svg: bytes) -> None:
    """Écrit `auto/<slug>.svg` puis l'ajoute à `auto/index.json` (slug déjà validé)."""
    folder = _auto_dir()
    slug = icon["slug"]
    target = folder / f"{slug}.svg"
    if not _SAFE_SLUG.fullmatch(slug) or target.parent != folder:
        raise ValueError(f"slug refusé : {slug!r}")
    folder.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(svg)
    os.replace(tmp, target)
    try:
        index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
        icons = index.get("icons") if isinstance(index, dict) and isinstance(index.get("icons"), dict) else {}
    except (OSError, ValueError):
        icons = {}
    icons[slug] = {"title": icon["title"], "hex": icon["hex"], "aliases": icon["aka"],
                   "fetched": time.strftime("%Y-%m-%d")}
    _write_json(folder / "index.json", {
        "source": "simple-icons (CC0-1.0), https://simpleicons.org ; récupérées d'elles-mêmes par astrel/icones.py",
        "version": SIMPLE_ICONS_VERSION, "icons": dict(sorted(icons.items()))})


def fetch_missing(names: Iterable[str]) -> bool:
    """Cherche une fois l'icône de chaque nom qui n'en a pas en local (bloquant, à appeler
    en FOND). Vrai si au moins une icône est arrivée (republier la page). Jamais d'exception
    pour un échec réseau ou un contenu refusé : négatif daté, journalisé."""
    if not _fetch_lock.acquire(blocking=False):
        return False  # une autre passe tourne déjà
    try:
        now, start = time.time(), time.monotonic()
        misses = _read_state()
        todo: dict[str, str] = {}
        for name in names:
            short = str(name).removeprefix(PREFIX).strip()
            key = slugify(short)
            if key and key not in todo and not for_name(name)["src"] and not _fresh_miss(misses.get(key), now):
                todo[key] = short
        if not todo:
            return False
        todo = dict(list(todo.items())[:MAX_PER_PASS])
        table = _table()
        added = False
        for key, short in todo.items():
            if time.monotonic() - start > PASS_BUDGET_S:
                break  # pas de négatif : la passe suivante reprendra
            why = ""
            if table is None:
                why = "réseau"
            else:
                words = short.split()
                tries = [key] + ([slugify(" ".join(words[:-1]))] if len(words) > 1 else [])
                icon = next((table[k] for k in tries if k and k in table), None)
                licence = (icon or {}).get("license")
                lic = licence.get("type") if isinstance(licence, dict) else None
                if icon is None:
                    why = "absente de simple-icons"
                elif not _SAFE_SLUG.fullmatch(str(icon["slug"])) or not _HEX.fullmatch(str(icon.get("hex", ""))):
                    why = "données refusées"
                elif lic and lic not in ALLOWED_LICENSES:
                    why = f"licence {lic}"
                else:
                    svg = _download(f"{CDN_BASE}icons/{icon['slug']}.svg", SVG_MAX_BYTES)
                    if svg is None:
                        why = "réseau"
                    elif not safe_svg(svg):
                        why = "SVG refusé"
                    else:
                        try:
                            _store(icon, svg)
                        except (OSError, ValueError) as exc:
                            log.warning("icône de %r non enregistrée : %s", short, exc)
                            why = "réseau"  # disque : réessai court
                        else:
                            added = True
                            log.info("icône de %r récupérée (simple-icons %s)", short, icon["slug"])
            if why:
                misses[key] = {"at": now, "why": why}
                log.info("icône de %r non récupérée : %s (pas de nouvel essai avant %d j)", short, why,
                         (RETRY_ERROR_S if why == "réseau" else RETRY_ABSENT_S) // 86400)
            else:
                misses.pop(key, None)
        try:
            _write_json(STATE_FILE, {"version": SIMPLE_ICONS_VERSION, "absent": misses})
        except OSError as exc:
            log.warning("négatifs des icônes non enregistrés : %s", exc)
        return added
    finally:
        _fetch_lock.release()
