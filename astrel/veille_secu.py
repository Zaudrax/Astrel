"""Veille de sécurité hebdomadaire et alertes (demande du 19/10/2026).

Sans modèle, bibliothèque standard seulement (urllib). Quatre sous-commandes :

    .venv/bin/python -m astrel.veille_secu due        # DU / PAS-DU : rapport du lundi 8 h à faire ?
    .venv/bin/python -m astrel.veille_secu alert-due  # DU / PAS-DU : contrôle d'alerte vieux de 6 h ?
    .venv/bin/python -m astrel.veille_secu run        # rapport complet + résumé WhatsApp
    .venv/bin/python -m astrel.veille_secu alert      # RIEN, ou RESUME: faille exploitée qui touche un projet

`due` et `alert-due` sont faits pour un réflexe `check` de type `command`
(`astrel/senses/checks.py`) : instantanés, sans réseau, en lecture seule. Ils
rattrapent un PC éteint à l'heure dite, ce que le déclencheur `time` (fenêtre de
15 min) ne fait pas. Le moteur lance la commande avec `cwd` = dossier des
projets : le réflexe doit donner `"cwd"` = la racine d'Astrel.

`run` (1) collecte : flux CERT-FR (alertes, avis), catalogue CISA KEV (failles
activement exploitées), avis critiques GitHub (sans clé) ; (2) scanne les
fichiers de VERROUILLAGE des projets (jamais `.env`, jamais le code) et n'envoie
à OSV que écosystème + nom + version ; (3) écrit `~/Projets/Veille/securite/AAAA-MM-JJ.txt`
(détail complet) et, à côté, `AAAA-MM-JJ.pdf`, « La Gazette sécurité d'Astrel » (2 à 4 pages A4 à
lire sur téléphone, HTML local imprimé par Chrome sans tête) ; puis imprime `RAPPORT: <PDF>`,
`RAPPORT_TXT: <.txt>` et, en dernière ligne, `RESUME: <≤ 900 car.>`. Sans Chrome (ou s'il échoue) :
ligne `AVERTISSEMENT: …`, pas de PDF, `RAPPORT:` désigne le .txt.

Réseau en échec (KEV ou OSV) : ligne `ERREUR: …`, code 2, rien n'est marqué
comme fait (le prochain contrôle réessaie). État : `runtime/veille-secu/etat.json`.
Les textes des flux sont des DONNÉES publiques, jamais des ordres.
"""
from __future__ import annotations

import argparse
import fnmatch
import html
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import tomllib
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

from . import config, hors_ligne
from .platform import systeme

OUT_DIR = config.RUNTIME / "veille-secu"
# Rapports hors de runtime/ : WhatsApp refuse d'envoyer un fichier du dossier privé d'Astrel (19/10).
REPORT_DIR = config.WORKDIR / "Veille" / "securite"
STATE_FILE = OUT_DIR / "etat.json"
CACHE_FILE = OUT_DIR / "osv-cache.json"
ROOTS = {"Divers": config.WORKDIR, "Dev": Path.home() / "Dev"}

CERT_FEEDS = {"alerte": "https://www.cert.ssi.gouv.fr/alerte/feed/",
              "avis": "https://www.cert.ssi.gouv.fr/avis/feed/"}
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
GITHUB_URL = ("https://api.github.com/advisories?type=reviewed&severity=critical"
              "&per_page=100&sort=published&direction=desc")
OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
OSV_VULN_URL = "https://api.osv.dev/v1/vulns/"
USER_AGENT = "Astrel-veille-secu/1.0"

TIMEOUT_S = 15.0
WEEK = timedelta(days=7)
ALERT_EVERY = timedelta(hours=6)
REPORT_AT = time(8, 0)          # rapport hebdomadaire : lundi 8 h, heure locale
KEV_ALERT_DAYS = 14             # `alert` : produit nommé dans un ajout KEV de moins de 14 jours
RESUME_MAX = 900
TABLE_MAX = 30                  # lignes de détail par projet dans le rapport
MAX_DEPTH = 6
BATCH = 1000                    # requêtes par appel `querybatch` (limite d'OSV)
WORKERS = 8
MAX_DETAIL_FAILURES = 0.05      # au-delà de 5 % de fiches OSV illisibles, l'analyse est jugée en échec
KEEP_SIGNALED_DAYS = 365

IGNORED_DIRS = {"node_modules", "vendor", ".venv", "venv", ".git", "dist", "build", "__pycache__",
                ".next", ".nuxt", ".expo", ".cache", ".tox", ".mypy_cache", ".pytest_cache"}
LOCKFILES = ("package-lock.json", "composer.lock", "poetry.lock", "pnpm-lock.yaml", "yarn.lock")
REQUIREMENTS = "requirements*.txt"
ARCHIVE_DIRS = {"archives"}     # projets archivés : dans le rapport, hors du résumé et des alertes
GENERIC_DIRS = {"app", "apps", "web", "site", "server", "client", "frontend", "backend", "mobile", "api",
                "src", "docs", "tools", "knowledge"}
# Paquets qui font d'un projet un « produit » à reconnaître dans le KEV et les titres CERT-FR.
# paquet → (nom, produits KEV qui le désignent exactement).
FRAMEWORKS = {("Packagist", "laravel/framework"): ("laravel", {"laravel", "laravelframework"}),
              ("Packagist", "symfony/http-kernel"): ("symfony", {"symfony"}),
              ("npm", "next"): ("nextjs", {"nextjs"}), ("PyPI", "django"): ("django", {"django"}),
              ("PyPI", "flask"): ("flask", {"flask"})}
NAME_SUFFIXES = {"manager", "admin", "server", "client", "web", "app", "api", "portal"}
RANKS = {"CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "MEDIUM": 2, "LOW": 1}
LABELS = {4: "critique", 3: "haute", 2: "moyenne", 1: "basse", 0: "inconnue"}


class NetError(Exception):
    """Source injoignable ou réponse illisible (message sans secret : l'adresse publique seule)."""


# ---------------------------------------------------------------- réseau

def _get(url: str, data: bytes | None = None, timeout: float = TIMEOUT_S) -> bytes:
    """GET (ou POST JSON si `data`). Aucun jeton, aucun cookie : sources publiques seulement."""
    if hors_ligne.actif():  # 04/11 : rien ne part (la veille est sautée, comme une source injoignable)
        raise NetError(f"{_host(url)} : {hors_ligne.REFUS}")
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(40 * 2**20)
    except urllib.error.HTTPError as exc:
        raise NetError(f"{_host(url)} : code HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise NetError(f"{_host(url)} injoignable ({reason})") from None


def _host(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url)
    return m.group(1) if m else url


def _get_json(url: str, data: dict | None = None, timeout: float = TIMEOUT_S):
    raw = _get(url, json.dumps(data).encode() if data is not None else None, timeout)
    try:
        return json.loads(raw)
    except ValueError:
        raise NetError(f"{_host(url)} : réponse JSON illisible") from None


# ---------------------------------------------------------------- état

def load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def save_state(state: dict) -> None:
    _write_json(STATE_FILE, state)


def _when(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def last_monday_8(now: datetime) -> datetime:
    """Le dernier lundi 8 h (heure locale) passé à `now`."""
    monday = datetime.combine(now.date() - timedelta(days=now.weekday()), REPORT_AT)
    return monday if now >= monday else monday - WEEK


def report_due(now: datetime | None = None, state: dict | None = None) -> bool:
    now = now or datetime.now()
    last = _when((state if state is not None else load_state()).get("dernier_rapport"))
    return last is None or last < last_monday_8(now)


def alert_due(now: datetime | None = None, state: dict | None = None) -> bool:
    now = now or datetime.now()
    last = _when((state if state is not None else load_state()).get("dernier_controle_alerte"))
    return last is None or now - last >= ALERT_EVERY


# ---------------------------------------------------------------- sources publiques

def _clean(text: str, limit: int = 300) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = " ".join(text.replace("\\[", "[").replace("\\]", "]").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def parse_cert_feed(raw: bytes, since: datetime) -> list[dict]:
    """Éléments d'un flux RSS du CERT-FR publiés depuis `since` (le flux n'est pas trié)."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise NetError("flux CERT-FR illisible") from None
    items = []
    for it in root.iter("item"):
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        local = when.astimezone().replace(tzinfo=None) if when.tzinfo else when
        if local < since:
            continue
        items.append({"titre": _clean(it.findtext("title") or "", 200), "lien": (it.findtext("link") or "").strip(),
                      "date": local.strftime("%Y-%m-%d"), "texte": _clean(it.findtext("description") or "")})
    return sorted(items, key=lambda i: i["date"], reverse=True)


def parse_kev(data: dict) -> list[dict]:
    if not isinstance(data, dict) or not isinstance(data.get("vulnerabilities"), list):
        raise NetError("catalogue CISA KEV illisible")
    return [v for v in data["vulnerabilities"] if isinstance(v, dict) and v.get("cveID")]


def kev_recent(kev: list[dict], since: datetime) -> list[dict]:
    day = since.strftime("%Y-%m-%d")
    return sorted((v for v in kev if str(v.get("dateAdded", "")) >= day), key=lambda v: v["dateAdded"],
                  reverse=True)


def parse_github(data, since: datetime) -> list[dict]:
    if not isinstance(data, list):
        raise NetError("avis GitHub illisibles")
    out = []
    for a in data:
        if not isinstance(a, dict) or str(a.get("published_at", ""))[:10] < since.strftime("%Y-%m-%d"):
            continue
        pkgs = []
        for v in a.get("vulnerabilities") or []:
            p = (v or {}).get("package") or {}
            if p.get("name"):
                pkgs.append(f"{p.get('ecosystem', '?')}:{p['name']}"
                            + (f" (corrigé en {v['first_patched_version']})" if v.get("first_patched_version") else ""))
        out.append({"id": a.get("ghsa_id", ""), "cve": a.get("cve_id") or "", "date": str(a.get("published_at"))[:10],
                    "titre": _clean(a.get("summary", ""), 160), "paquets": pkgs[:4], "lien": a.get("html_url", "")})
    return out


# ---------------------------------------------------------------- lecture des fichiers de verrouillage

def _digit(version) -> bool:
    return isinstance(version, str) and bool(re.match(r"^\d", version))


def parse_package_lock(text: str) -> dict:
    """package-lock.json (v1 à v3) → {(npm, nom, version): dépendance de développement seulement ?}."""
    data = json.loads(text)
    pkgs: dict = {}
    if isinstance(data.get("packages"), dict):
        for key, entry in data["packages"].items():
            if not isinstance(entry, dict):
                continue
            if entry.get("link") or "node_modules/" not in key:
                continue
            name = entry.get("name") or key.rsplit("node_modules/", 1)[1]
            if _digit(entry.get("version")):
                k = ("npm", name, entry["version"])
                pkgs[k] = pkgs.get(k, True) and bool(entry.get("dev"))
    else:
        def walk(deps: dict) -> None:
            for name, entry in (deps or {}).items():
                if not isinstance(entry, dict):
                    continue
                if _digit(entry.get("version")):
                    k = ("npm", name, entry["version"])
                    pkgs[k] = pkgs.get(k, True) and bool(entry.get("dev"))
                walk(entry.get("dependencies") or {})
        walk(data.get("dependencies") or {})
    return pkgs


def parse_composer_lock(text: str) -> dict:
    data = json.loads(text)
    pkgs: dict = {}
    for section, dev in (("packages", False), ("packages-dev", True)):
        for p in data.get(section) or []:
            version = str(p.get("version", "")).lstrip("vV")
            if p.get("name") and _digit(version):
                k = ("Packagist", p["name"], version)
                pkgs[k] = pkgs.get(k, True) and dev
    return pkgs


def _pep503(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirements(text: str) -> dict:
    """Seulement les lignes épinglées `nom==version` (options, URL et chemins ignorés)."""
    pkgs: dict = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].split(";", 1)[0].strip()
        if not line or line.startswith("-") or "://" in line or " @ " in line:
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*==\s*([0-9][^\s,]*)$", line)
        if m:
            pkgs[("PyPI", _pep503(m.group(1)), m.group(3))] = False
    return pkgs


def parse_poetry_lock(text: str) -> dict:
    data = tomllib.loads(text)
    pkgs: dict = {}
    for p in data.get("package") or []:
        if p.get("name") and _digit(p.get("version")):
            pkgs[("PyPI", _pep503(p["name"]), p["version"])] = p.get("category") == "dev"
    return pkgs


def parse_pnpm_lock(text: str) -> dict:
    """Clés des sections `packages`/`snapshots` (formats v5 `/nom/1.0.0`, v6 `/nom@1.0.0`, v9 `nom@1.0.0`)."""
    pkgs: dict = {}
    section = ""
    for line in text.splitlines():
        if line and not line[0].isspace():
            section = line.split(":", 1)[0].strip()
            continue
        if section not in ("packages", "snapshots"):
            continue
        m = re.match(r"^  (['\"]?)(.+?)\1:\s*$", line)
        if not m:
            continue
        key = re.sub(r"\(.*$", "", m.group(2)).lstrip("/")
        old = re.match(r"^(@?[^@]+)/(\d[^/_]*)(?:_.*)?$", key)        # v5 : /nom/1.0.0_pairs
        if old:
            name, version = old.groups()
        else:
            name, _, version = key.rpartition("@")
        if name and _digit(version):
            pkgs[("npm", name, version)] = False
    return pkgs


def parse_yarn_lock(text: str) -> dict:
    """yarn.lock v1 (`version "1.0.0"`) et v2+ (`version: 1.0.0`) ; protocoles `patch:` ignorés."""
    pkgs: dict = {}
    name = ""
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            spec = line.rstrip().rstrip(":").split(",")[0].strip().strip('"')
            at = spec.rfind("@")
            name = "" if at <= 0 or "@patch:" in spec or spec.startswith("__metadata") else spec[:at]
            continue
        m = re.match(r'^  version:?\s+"?([^"\s]+)"?\s*$', line)
        if m and name and _digit(m.group(1)):
            pkgs[("npm", name, m.group(1))] = False
    return pkgs


PARSERS = {"package-lock.json": parse_package_lock, "composer.lock": parse_composer_lock,
           "poetry.lock": parse_poetry_lock, "pnpm-lock.yaml": parse_pnpm_lock, "yarn.lock": parse_yarn_lock}


def _parser(filename: str):
    if fnmatch.fnmatch(filename, REQUIREMENTS):
        return parse_requirements
    return PARSERS.get(filename)


def find_lockfiles(root: Path, max_depth: int = MAX_DEPTH) -> list[Path]:
    found = []
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
        depth = len(Path(dirpath).relative_to(root).parts)
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")
                             and depth < max_depth and not os.path.islink(os.path.join(dirpath, d)))
        for f in sorted(filenames):
            if _parser(f) and not os.path.islink(os.path.join(dirpath, f)):
                found.append(Path(dirpath) / f)
    return found


def _project_key(rel: Path, lock_dirs: set[tuple]) -> tuple[str, ...]:
    """Un dossier de premier niveau est UN projet, sauf s'il n'a pas de fichier de
    verrouillage à lui et en regroupe plusieurs (Studio, archives) : alors deux niveaux."""
    parts = rel.parts[:-1]
    if not parts:
        return ()
    top = parts[0]
    if top.lower() in ARCHIVE_DIRS and len(parts) >= 2:
        return (top, parts[1])
    if (top,) in lock_dirs:
        return (top,)
    subs = {d[1] for d in lock_dirs if len(d) > 1 and d[0] == top}
    return (top,) if len(subs) <= 1 or len(parts) < 2 else (top, parts[1])


def scan_projects(roots: dict[str, Path] | None = None) -> tuple[dict, list[str]]:
    """→ ({projet: {chemin, fichiers, paquets, archive}}, fichiers illisibles)."""
    roots = ROOTS if roots is None else roots
    projects: dict = {}
    errors: list[str] = []
    for label, root in roots.items():
        root = Path(root)
        if not root.is_dir():
            continue
        files = find_lockfiles(root)
        lock_dirs = {f.relative_to(root).parts[:-1] for f in files}
        tops = set()
        for f in files:
            rel = f.relative_to(root)
            key = _project_key(rel, lock_dirs)
            if not key:
                continue
            tops.add(key[0])
            pid = f"{label}/{'/'.join(key)}"
            proj = projects.setdefault(pid, _empty_project(root.joinpath(*key), key[0]))
            try:
                pkgs = _parser(f.name)(f.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError, tomllib.TOMLDecodeError, AttributeError, TypeError):
                errors.append(f"{label}/{rel}")
                continue
            proj["fichiers"].append(str(rel))
            for k, dev in pkgs.items():
                proj["paquets"][k] = proj["paquets"].get(k, True) and dev
        try:
            names = sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            names = []
        for top in names:   # projets sans fichier de verrouillage (gestsup…) : reconnus par leur nom seulement
            if top not in tops and top not in IGNORED_DIRS and top.lower() not in ARCHIVE_DIRS:
                projects[f"{label}/{top}"] = _empty_project(root / top, top)
    return projects, errors


def _empty_project(path: Path, top: str) -> dict:
    return {"chemin": str(path), "fichiers": [], "paquets": {}, "archive": top.lower() in ARCHIVE_DIRS}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def project_tokens(pid: str, proj: dict) -> dict:
    """Noms sous lesquels un projet peut apparaître dans le KEV ou un titre CERT-FR :
    `noms` (ses dossiers, gestsup, snipeit…) et `cadres` (laravel… → produits KEV exacts)."""
    names = {_norm(p) for p in pid.split("/")[1:] if p.lower() not in GENERIC_DIRS and p.lower() not in ARCHIVE_DIRS}
    frames: dict = {}
    for (eco, name, _v) in proj["paquets"]:
        if (eco, name) in FRAMEWORKS:
            token, products = FRAMEWORKS[(eco, name)]
            frames[token] = products
    return {"noms": {t for t in names if len(t) >= 3}, "cadres": frames}


def kev_matches_project(entry: dict, tokens: dict) -> bool:
    """Nom du projet = éditeur ou produit KEV (éventuellement suivi d'un suffixe générique :
    3cxmanager pour 3CX) ; cadre (Laravel…) = produit KEV EXACT (pas « Laravel Livewire »)."""
    vendor, product = _norm(entry.get("vendorProject", "")), _norm(entry.get("product", ""))
    for t in tokens["noms"]:
        for ref in {vendor, product} - {"", "multipleproducts", "multiple", "unknown"}:
            if t == ref or (len(ref) >= 3 and t.startswith(ref) and t[len(ref):] in NAME_SUFFIXES):
                return True
    return any(product in products for products in tokens["cadres"].values())


def title_mentions(title: str, tokens: dict) -> bool:
    words = [_norm(w) for w in title.split()]
    seen = set(words) | {a + b for a, b in zip(words, words[1:])}
    return any(len(t) >= 4 and t in seen for t in tokens["noms"] | set(tokens["cadres"]))


# ---------------------------------------------------------------- OSV

def _cvss3_score(vector: str) -> float | None:
    """Score de base CVSS 3.x à partir du vecteur (formule de la spécification FIRST)."""
    m = dict(p.split(":", 1) for p in vector.split("/")[1:] if ":" in p)
    try:
        scope_changed = m["S"] == "C"
        av = {"N": .85, "A": .62, "L": .55, "P": .2}[m["AV"]]
        ac = {"L": .77, "H": .44}[m["AC"]]
        pr = {"N": .85, "L": .68 if scope_changed else .62, "H": .5 if scope_changed else .27}[m["PR"]]
        ui = {"N": .85, "R": .62}[m["UI"]]
        cia = [{"H": .56, "L": .22, "N": 0.0}[m[k]] for k in ("C", "I", "A")]
    except KeyError:
        return None
    iss = 1 - (1 - cia[0]) * (1 - cia[1]) * (1 - cia[2])
    impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15 if scope_changed else 6.42 * iss
    if impact <= 0:
        return 0.0
    exploit = 8.22 * av * ac * pr * ui
    raw = min((1.08 if scope_changed else 1) * (impact + exploit), 10)
    i = round(raw * 100000)
    return i / 100000.0 if i % 10000 == 0 else (math.floor(i / 10000) + 1) / 10.0


def severity_rank(vuln: dict) -> int:
    level = str((vuln.get("database_specific") or {}).get("severity") or "").upper()
    if level in RANKS:
        return RANKS[level]
    best = 0
    for s in vuln.get("severity") or []:
        if str(s.get("type", "")).startswith("CVSS_V3"):
            score = _cvss3_score(str(s.get("score", "")))
            if score is not None:
                best = max(best, 4 if score >= 9 else 3 if score >= 7 else 2 if score >= 4 else 1 if score > 0 else 0)
    return best


def summarize_vuln(v: dict) -> dict:
    affected = []
    for a in v.get("affected") or []:
        p = a.get("package") or {}
        ranges = [[e for e in r.get("events") or [] if isinstance(e, dict)]
                  for r in a.get("ranges") or [] if r.get("type") in ("ECOSYSTEM", "SEMVER")]
        affected.append({"eco": p.get("ecosystem", ""), "name": p.get("name", ""), "ranges": ranges})
    return {"id": v.get("id", ""), "aliases": list(v.get("aliases") or []), "rang": severity_rank(v),
            "titre": _clean(v.get("summary") or v.get("details") or "", 160), "affected": affected,
            "retiree": bool(v.get("withdrawn"))}


def _vkey(version: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", version.split("+", 1)[0])[:4])


def fixed_version(summary: dict, eco: str, name: str, version: str) -> str | None:
    """Version qui corrige `name@version`, d'après les plages OSV (None : pas de correctif publié)."""
    cur = _vkey(version)
    norm = _pep503 if eco == "PyPI" else str.lower
    candidates = []
    for a in summary["affected"]:
        if a["eco"] != eco or norm(a["name"]) != norm(name):
            continue
        for events in a["ranges"]:
            start = None
            for e in events:
                if "introduced" in e:
                    start = e["introduced"]
                elif "fixed" in e:
                    if start is not None and (start == "0" or _vkey(start) <= cur) and cur < _vkey(e["fixed"]):
                        return e["fixed"]
                    candidates.append(e["fixed"])
    later = [f for f in candidates if _vkey(f) > cur]
    return min(later, key=_vkey) if later else None


def _load_cache() -> dict:
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def osv_scan(packages: list[tuple]) -> tuple[dict, dict, int]:
    """→ ({paquet: [id…]}, {id: résumé}, fiches illisibles). Seuls écosystème, nom et version partent.
    Lève NetError si plus de 5 % des fiches de faille n'ont pas pu être lues."""
    hits: dict = {}
    modified: dict = {}
    for start in range(0, len(packages), BATCH):
        chunk = packages[start:start + BATCH]
        queries = [{"package": {"ecosystem": e, "name": n}, "version": v} for e, n, v in chunk]
        data = _get_json(OSV_BATCH_URL, {"queries": queries}, timeout=30)
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list) or len(results) != len(chunk):
            raise NetError("api.osv.dev : réponse inattendue")
        for pkg, query, res in zip(chunk, queries, results):
            vulns = list((res or {}).get("vulns") or [])
            token, pages = (res or {}).get("next_page_token"), 0
            while token and pages < 5:
                more = _get_json(OSV_BATCH_URL, {"queries": [{**query, "page_token": token}]}, timeout=30)
                r = ((more or {}).get("results") or [{}])[0] or {}
                vulns += r.get("vulns") or []
                token, pages = r.get("next_page_token"), pages + 1
            for v in vulns:
                if v.get("id"):
                    hits.setdefault(pkg, []).append(v["id"])
                    modified[v["id"]] = v.get("modified", "")
    cache = _load_cache()
    todo = [i for i in modified if (cache.get(i) or {}).get("modified") != modified[i]]

    def fetch(vid: str):
        for _ in range(2):
            try:
                return vid, summarize_vuln(_get_json(OSV_VULN_URL + vid))
            except NetError:
                continue
        return vid, None

    failures = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for vid, summary in pool.map(fetch, todo):
            if summary is None:
                failures += 1
            else:
                cache[vid] = {"modified": modified[vid], "v": summary}
    try:
        _write_json(CACHE_FILE, {k: c for k, c in cache.items() if k in modified})
    except OSError:
        pass
    if failures > MAX_DETAIL_FAILURES * max(1, len(modified)):
        raise NetError(f"api.osv.dev : {failures} fiches de faille sur {len(modified)} illisibles")
    details = {i: cache[i]["v"] for i in modified if i in cache}
    return hits, details, failures


def project_findings(projects: dict, hits: dict, details: dict, kev_cves: set[str]) -> dict:
    """Failles hautes/critiques ou présentes au KEV, par projet et par paquet (alias dédoublonnés)."""
    out: dict = {}
    for pid, proj in projects.items():
        rows = []
        for pkg, dev in proj["paquets"].items():
            seen: set = set()
            for vid in hits.get(pkg, []):
                s = details.get(vid)
                if not s or s["retiree"]:
                    continue
                ids = {s["id"], *s["aliases"]}
                if ids & seen:
                    continue
                seen |= ids
                cves = sorted(i for i in ids if i.startswith("CVE-"))
                exploited = bool(set(cves) & kev_cves)
                if s["rang"] < 3 and not exploited:
                    continue
                rows.append({"eco": pkg[0], "paquet": pkg[1], "version": pkg[2], "dev": dev, "id": s["id"],
                             "cve": cves[0] if cves else "", "rang": s["rang"], "exploitee": exploited,
                             "titre": s["titre"], "correctif": fixed_version(s, *pkg),
                             "cle": f"{pid}|{pkg[0]}:{pkg[1]}|{cves[0] if cves else s['id']}"})
        if rows:
            out[pid] = sorted(rows, key=lambda r: (not r["exploitee"], -r["rang"], r["paquet"]))
    return out


def fixes_by_package(rows: list[dict]) -> list[dict]:
    """Une correction par paquet : la plus haute version demandée par ses failles."""
    by: dict = {}
    for r in rows:
        k = (r["eco"], r["paquet"], r["version"])
        f = by.setdefault(k, {"eco": k[0], "paquet": k[1], "version": k[2], "dev": r["dev"], "vers": None,
                              "sans_correctif": False, "rang": 0, "exploitee": False, "n": 0})
        f["n"] += 1
        f["rang"] = max(f["rang"], r["rang"])
        f["exploitee"] |= r["exploitee"]
        if r["correctif"] is None:
            f["sans_correctif"] = True
        elif f["vers"] is None or _vkey(r["correctif"]) > _vkey(f["vers"]):
            f["vers"] = r["correctif"]
    return sorted(by.values(), key=lambda f: (not f["exploitee"], -f["rang"], f["dev"], -f["n"], f["paquet"]))


# ---------------------------------------------------------------- collecte complète

def collect(now: datetime, *, with_news: bool = True, roots: dict | None = None) -> dict:
    """Tout ce que voient `run` et `alert`. Lève NetError si KEV ou OSV manquent."""
    since = now - WEEK
    sources: dict = {}
    data: dict = {"cert": {"alerte": [], "avis": []}, "github": []}
    if with_news:
        for kind, url in CERT_FEEDS.items():
            try:
                data["cert"][kind] = parse_cert_feed(_get(url), since)
                sources[f"CERT-FR ({kind})"] = "lu"
            except NetError as exc:
                sources[f"CERT-FR ({kind})"] = f"échec : {exc}"
        try:
            data["github"] = parse_github(_get_json(GITHUB_URL), since)
            sources["GitHub (avis critiques)"] = "lu"
        except NetError as exc:
            sources["GitHub (avis critiques)"] = f"échec : {exc}"
    kev = parse_kev(_get_json(KEV_URL, timeout=30))
    sources["CISA KEV"] = "lu"
    projects, unreadable = scan_projects(roots)
    packages = sorted({k for p in projects.values() for k in p["paquets"]})
    hits, details, failures = osv_scan(packages)
    sources["OSV"] = f"lu ({len(packages)} paquets)" + (f", {failures} fiches illisibles" if failures else "")
    kev_cves = {v["cveID"] for v in kev}
    tokens = {pid: project_tokens(pid, p) for pid, p in projects.items()}
    recent = kev_recent(kev, since)
    # (projet, entrée KEV) : produit d'un projet nommé dans un ajout KEV récent (14 jours)
    named = [(pid, v) for v in kev_recent(kev, now - timedelta(days=KEV_ALERT_DAYS))
             for pid in projects if kev_matches_project(v, tokens[pid])]
    cert_named = [(pid, kind, item) for kind in ("alerte", "avis") for item in data["cert"][kind]
                  for pid in projects if title_mentions(item["titre"], tokens[pid])]
    return {"now": now, "since": since, "sources": sources, "kev": kev, "kev_recent": recent, "projects": projects,
            "unreadable": unreadable, "findings": project_findings(projects, hits, details, kev_cves),
            "named": named, "cert_named": cert_named, **data}


def _short(pid: str) -> str:
    return pid.split("/", 1)[1] if "/" in pid else pid


def _fix_text(f: dict) -> str:
    if f["vers"]:
        return f"{f['paquet']} {f['version']} vers {f['vers']}"
    return f"{f['paquet']} {f['version']} (pas encore de correctif)"


def build_resume(c: dict, report_rel: str) -> str:
    now, findings, projects = c["now"], c["findings"], c["projects"]
    live = {p: r for p, r in findings.items() if not projects[p]["archive"]}
    archived = len(findings) - len(live)
    head = (f"Veille sécurité du {now:%d/%m}. Semaine : {len(c['cert']['alerte'])} alerte(s) CERT-FR, "
            f"{len(c['cert']['avis'])} avis CERT-FR, {len(c['kev_recent'])} faille(s) ajoutée(s) au catalogue "
            f"des failles exploitées (CISA), {len(c['github'])} avis critique(s) GitHub. ")
    named = sorted({f"{_short(p)} ({v['cveID']})" for p, v in c["named"] if not projects[p]["archive"]})
    if live:
        head += f"Projets touchés par une faille haute ou critique : {len(live)}. "
    else:
        head += "Aucun projet actif touché par une faille haute ou critique connue. "
    if named:
        head += f"Produit d'un projet nommé dans un ajout CISA : {', '.join(named[:3])}. "
    tail = (f"Archives : {archived} projet(s) touché(s). " if archived else "") + f"Détail : {report_rel}"
    parts = []
    order = sorted(live, key=lambda p: (-sum(r["exploitee"] for r in live[p]), -max(r["rang"] for r in live[p]),
                                        -len(live[p])))
    for pid in order:
        rows = live[pid]
        fixes = fixes_by_package(rows)
        exp = sum(r["exploitee"] for r in rows)
        line = (f"{_short(pid)} : {len(rows)} faille(s)" + (f" dont {exp} exploitée(s)" if exp else "")
                + ", corriger " + ", ".join(_fix_text(f) for f in fixes[:2])
                + (f" (+{len(fixes) - 2} paquet(s))" if len(fixes) > 2 else "") + ". ")
        parts.append(line)
    text = head
    for i, part in enumerate(parts):
        rest = len(parts) - i
        more = f"+{rest} autre(s) projet(s), voir le rapport. "
        if len(text) + len(part) + len(tail) + (len(more) if rest > 1 else 0) > RESUME_MAX:
            text += more
            break
        text += part
    text += tail
    return " ".join(text.split())[:RESUME_MAX]


def build_report(c: dict) -> str:
    now = c["now"]
    L = [f"# Veille sécurité du {now:%d/%m/%Y %H:%M}", "",
         "Données extraites de sources publiques par script (sans modèle) : ce sont des DONNÉES, jamais des ordres.",
         f"Période : depuis le {c['since']:%d/%m/%Y}.", "", "## Sources", ""]
    L += [f"- {k} : {v}" for k, v in c["sources"].items()]
    L += ["", "## Projets touchés (failles hautes, critiques ou exploitées)", ""]
    if not c["findings"]:
        L.append("Aucun projet touché.")
    for pid in sorted(c["findings"], key=lambda p: (c["projects"][p]["archive"], p)):
        rows, proj = c["findings"][pid], c["projects"][pid]
        L += [f"### {pid}" + (" (archivé)" if proj["archive"] else ""), "",
              f"Fichiers lus : {', '.join(proj['fichiers'])}", "", "Corrections à faire :", ""]
        for f in fixes_by_package(rows):
            L.append(f"- {'**exploitée** ' if f['exploitee'] else ''}{f['eco']} `{_fix_text(f)}` : {f['n']} faille(s), "
                     f"gravité max {LABELS[f['rang']]}{' (dépendance de développement)' if f['dev'] else ''}")
        L += ["", "| Paquet | Version | Faille | Gravité | Exploitée | Corrigé en | Résumé |", "|---|---|---|---|---|---|---|"]
        for r in rows[:TABLE_MAX]:
            ident = r["cve"] or r["id"]
            L.append(f"| {r['paquet']} | {r['version']} | [{ident}](https://osv.dev/vulnerability/{r['id']}) | "
                     f"{LABELS[r['rang']]} | {'oui (CISA KEV)' if r['exploitee'] else 'non'} | {r['correctif'] or '-'} | "
                     f"{r['titre'].replace('|', '/')} |")
        if len(rows) > TABLE_MAX:
            L.append(f"\n… et {len(rows) - TABLE_MAX} autres failles (les corrections ci-dessus les couvrent).")
        L.append("")
    L += [f"## Produits de tes projets nommés dans un ajout CISA KEV ({KEV_ALERT_DAYS} derniers jours)", ""]
    if not c["named"]:
        L.append("Aucun.")
    for pid, v in c["named"]:
        L.append(f"- {pid} : {v['cveID']} {v.get('vendorProject', '')} {v.get('product', '')}, ajoutée le "
                 f"{v['dateAdded']} : {_clean(v.get('vulnerabilityName', ''), 160)}")
    L += ["", "## Avis CERT-FR qui citent un de tes produits", ""]
    L += [f"- {pid} : [{i['titre']}]({i['lien']})" for pid, _k, i in c["cert_named"]] or ["Aucun."]
    L += ["", f"## CISA KEV : ajouts depuis le {c['since']:%d/%m} ({len(c['kev_recent'])})", ""]
    for v in c["kev_recent"]:
        L.append(f"- {v['dateAdded']} {v['cveID']} {v.get('vendorProject', '')} {v.get('product', '')} : "
                 f"{_clean(v.get('vulnerabilityName', ''), 160)}"
                 f"{' (rançongiciel connu)' if v.get('knownRansomwareCampaignUse') == 'Known' else ''}")
    for kind in ("alerte", "avis"):
        items = c["cert"][kind]
        L += ["", f"## CERT-FR : {'alertes' if kind == 'alerte' else 'avis'} de la semaine ({len(items)})", ""]
        L += [f"- {i['date']} [{i['titre']}]({i['lien']})" + (f" : {i['texte']}" if kind == "alerte" else "")
              for i in items] or ["Aucun."]
    L += ["", f"## GitHub : avis critiques de la semaine ({len(c['github'])})", ""]
    L += [f"- {g['date']} [{g['cve'] or g['id']}]({g['lien']}) {g['titre']} ({'; '.join(g['paquets'])})"
          for g in c["github"]] or ["Aucun."]
    clean = sorted(p for p, proj in c["projects"].items() if proj["paquets"] and p not in c["findings"])
    without = sorted(p for p, proj in c["projects"].items() if not proj["fichiers"])
    unpinned = sorted(p for p, proj in c["projects"].items() if proj["fichiers"] and not proj["paquets"])
    L += ["", "## Projets scannés sans faille haute connue", "", ", ".join(clean) or "Aucun.", "",
          "## Limites", "",
          "- Seuls les fichiers de verrouillage sont lus ; un requirements.txt non épinglé (`>=`) n'est pas vérifié"
          + (f" ({', '.join(unpinned)})." if unpinned else "."),
          "- Projets sans fichier de verrouillage (reconnus par leur nom seulement) : " + (", ".join(without) or "aucun"),
          "- Fichiers illisibles : " + (", ".join(c["unreadable"]) or "aucun"), ""]
    return "\n".join(L)


# ---------------------------------------------------------------- gazette (PDF « journal », 19/10)
# L'utilisateur lit le rapport sur son téléphone : le .txt brut « ne donne pas envie ». Le PDF est un
# HTML LOCAL imprimé par Chrome sans tête. Tout texte venu des flux ou des projets est échappé ;
# aucun script, aucune ressource distante (CSP `default-src 'none'`, polices système seulement).

GAZETTE_TITLE = "La Gazette sécurité d'Astrel"
PDF_MAX_PAGES = 4
CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
CHROME_TIMEOUT_S = 90
GAZETTE_LIMITS = {  # au-delà de PDF_MAX_PAGES pages, une seconde impression en « compact »
    False: {"priorite": 8, "corrections": 5, "projets": 14, "kev": 10, "alertes": 4, "avis": 10, "github": 8},
    True: {"priorite": 5, "corrections": 3, "projets": 8, "kev": 6, "alertes": 3, "avis": 5, "github": 4}}
DAYS_FR = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MONTHS_FR = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
             "novembre", "décembre")
ROOT_TITLES = {"Divers": "Tes projets (Divers)", "Dev": "Travail (Dev)"}
# Type de faille (titres anglais du KEV et de GitHub) → mots français ; le premier qui correspond gagne.
KINDS_FR = (
    (r"remote code execution|\brce\b|code execution", "exécution de code à distance"),
    (r"deseriali[sz]ation", "désérialisation dangereuse"),
    (r"sql injection", "injection SQL"),
    (r"command injection|os command", "injection de commandes"),
    (r"code injection", "injection de code"),
    (r"argument injection", "injection d'arguments"),
    (r"cross[- ]site scripting|\bxss\b", "injection de script (XSS)"),
    (r"server[- ]side request forgery|\bssrf\b", "requête forgée côté serveur (SSRF)"),
    (r"cross[- ]site request forgery|\bcsrf\b", "requête forgée (CSRF)"),
    (r"path traversal|directory traversal", "accès à des fichiers hors du dossier prévu"),
    (r"authentication bypass|bypass\w* authentication", "contournement de l'authentification"),
    (r"privilege escalation|elevation of privilege", "élévation de privilèges"),
    (r"use[- ]after[- ]free|out[- ]of[- ]bounds|buffer overflow|heap overflow|memory corruption|type confusion",
     "corruption de la mémoire"),
    (r"denial of service|\bredos\b|\bdos\b", "déni de service"),
    (r"improper authentication|missing authentication|unauthenticated", "authentification défaillante"),
    (r"authori[sz]ation|access control", "contrôle d'accès défaillant"),
    (r"security feature bypass|bypass", "contournement d'une protection"),
    (r"prototype pollution", "pollution de prototype"),
    (r"hard[- ]coded|credential", "identifiants exposés"),
    (r"accessible to external|information disclosure|exposure|sensitive information|\bleak", "fuite d'informations"),
    (r"unrestricted upload|file upload", "envoi de fichier sans contrôle"),
    (r"open redirect", "redirection ouverte"),
    (r"sandbox", "sortie du bac à sable"),
)


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _pl(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _date_fr(d: datetime) -> str:
    return f"{DAYS_FR[d.weekday()]} {d.day} {MONTHS_FR[d.month - 1]} {d.year}"


def vuln_kind_fr(text: str) -> str:
    low = str(text or "").lower()
    for pattern, label in KINDS_FR:
        if re.search(pattern, low):
            return label
    return ""


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def cert_title_fr(title: str) -> str:
    """« Multiples vulnérabilités dans les produits F5 (15 octobre 2026) » → « Produits F5 : plusieurs failles »."""
    t = re.sub(r"\s*\(\d{1,2}(er)? \w+ \d{4}\)\s*$", "", str(title or "")).strip()
    m = re.match(r"^(Multiples vulnérabilités|Vulnérabilité) dans (les produits |le |la |les |l')?(.+)$", t)
    if not m:
        return t
    subject = ("produits " if m.group(2) == "les produits " else "") + m.group(3).strip()
    return f"{_cap(subject)} : {'plusieurs failles' if m.group(1).startswith('Multiples') else 'une faille'}"


def kev_title_fr(v: dict) -> str:
    vendor = _clean(v.get("vendorProject", ""), 40)
    product = _clean(v.get("product", ""), 50)
    if _norm(product) in ("multipleproducts", "multiple"):
        name = f"{vendor} (plusieurs produits)"
    elif _norm(product).startswith(_norm(vendor)):
        name = product
    else:
        name = f"{vendor} {product}".strip()
    return f"{name} : {vuln_kind_fr(v.get('vulnerabilityName', '')) or 'faille activement exploitée'}"


def github_title_fr(g: dict) -> str:
    summary = str(g.get("titre", ""))
    kind = vuln_kind_fr(summary)
    product = ""
    if ":" in summary and len(summary.split(":", 1)[0]) <= 40:
        product = summary.split(":", 1)[0]
    else:
        tail = summary.rsplit(" - ", 1)[-1]
        m = next((re.search(p, tail.lower()) for p, _l in KINDS_FR if re.search(p, tail.lower())), None)
        head = tail[:m.start()].strip(" -") if m else ""
        if 3 <= len(head) <= 40:
            product = head
    if not product and g.get("paquets"):
        product = str(g["paquets"][0]).split(":", 1)[-1].split(" (", 1)[0][:40]
    return f"{product.strip() or 'Paquet'} : {kind or 'faille critique'}"


def _link(url: str) -> str:
    """Lien affiché en texte (cliquable dans le PDF) ; `https://` seulement, sinon rien."""
    url = str(url or "").strip()
    if not re.match(r"^https://[A-Za-z0-9.-]+(/[^\s\"'<>\\]*)?$", url):
        return ""
    return f'<a class="url" href="{_esc(url)}">{_esc(url[len("https://"):])}</a>'


def _fix_html(f: dict) -> str:
    name = f"<b>{_esc(f['paquet'])}</b>"
    if f["vers"]:
        return f"{name} {_esc(f['version'])} → <b>{_esc(f['vers'])}</b>"
    return f"{name} {_esc(f['version'])} : pas encore de correctif"


def _fix_tags(f: dict) -> str:
    tags = []
    if f["exploitee"]:
        tags.append('<span class="tag exp">exploitée</span>')
    if f["rang"] >= 4:
        tags.append('<span class="tag crit">critique</span>')
    if f["dev"]:
        tags.append('<span class="tag dev">outil de dev</span>')
    return " ".join(tags)


GAZETTE_CSS = """
@page { size: A4; margin: 13mm 13mm 15mm;
  @bottom-center { content: "%(footer)s · page " counter(page) " sur " counter(pages);
                   font: 9pt "DejaVu Sans", sans-serif; color: #444; } }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; color: #111; background: #fff; font: 11pt/1.4 "DejaVu Serif", "Liberation Serif", serif;
       hyphens: auto; }
.mast { text-align: center; border-top: 3pt solid #111; border-bottom: 4pt double #111; padding: 2mm 0 1.6mm; }
.mast h1 { font: 700 27pt/1.05 "Liberation Serif", "DejaVu Serif", serif; letter-spacing: .3pt; margin: 0; }
.dateline { display: flex; justify-content: space-between; font: 9.5pt "DejaVu Sans", sans-serif;
            border-top: .6pt solid #111; margin-top: 1.6mm; padding-top: 1.2mm; text-transform: uppercase;
            letter-spacing: .4pt; }
.une { display: flex; gap: 6mm; align-items: center; margin: 4mm 0 3mm; padding-bottom: 3mm;
       border-bottom: .8pt solid #111; }
.chiffre { flex: 0 0 34mm; text-align: center; border-right: .6pt solid #999; padding-right: 5mm; }
.chiffre b { display: block; font: 700 40pt/1 "Liberation Serif", serif; color: #8a1010; }
.chiffre span { font: 9.5pt/1.25 "DejaVu Sans", sans-serif; }
.une h2 { font: 700 19pt/1.15 "Liberation Serif", "DejaVu Serif", serif; margin: 0 0 1.5mm; }
.chapeau { font-style: italic; margin: 0; }
h3 { font: 700 10pt "DejaVu Sans", sans-serif; text-transform: uppercase; letter-spacing: .8pt;
     border-top: 2pt solid #111; border-bottom: .6pt solid #111; padding: 1mm 0; margin: 4mm 0 2.5mm; }
h3.urgent { color: #8a1010; border-top-color: #8a1010; }
.cols { column-count: 2; column-gap: 7mm; column-rule: .6pt solid #aaa; }
.prio { margin: 0; padding: 0; list-style: none; }
.prio li { padding: 1.2mm 0; border-bottom: .4pt dotted #888; break-inside: avoid; }
.prio .proj { font-weight: 700; font-style: italic; }
section { break-inside: auto; }
h3, h4, .groupe { break-after: avoid; }
.tag { font: 700 7.5pt "DejaVu Sans", sans-serif; text-transform: uppercase; letter-spacing: .4pt;
       padding: .2mm 1.2mm; border: .6pt solid #111; border-radius: 1mm; white-space: nowrap; }
.tag.exp { background: #8a1010; color: #fff; border-color: #8a1010; }
.tag.crit { color: #8a1010; border-color: #8a1010; }
.tag.dev { color: #444; border-color: #888; }
.tag.toi { background: #111; color: #fff; }
.groupe { font: 700 12pt "Liberation Serif", serif; font-style: italic; margin: 1mm 0 2mm; }
.box { break-inside: avoid; border: .8pt solid #111; padding: 2mm 2.6mm 1.6mm; margin: 0 0 3mm; }
.box h4 { font: 700 13pt/1.15 "Liberation Serif", serif; margin: 0; overflow-wrap: anywhere; }
.box .count { font: 9pt "DejaVu Sans", sans-serif; color: #333; margin: .5mm 0 1mm;
              border-bottom: .4pt solid #999; padding-bottom: 1mm; }
.box ul { margin: 0; padding-left: 4mm; }
.box li { margin: .5mm 0; overflow-wrap: anywhere; }
.more { font-size: 9.5pt; font-style: italic; color: #333; margin: 1mm 0 0; }
.news h4 { font: 700 10pt "DejaVu Sans", sans-serif; text-transform: uppercase; letter-spacing: .5pt;
           margin: 0 0 1.5mm; border-bottom: .6pt solid #111; break-after: avoid; }
.item { break-inside: avoid; margin: 0 0 2.4mm; }
.item b { font-size: 11.5pt; }
.item .meta { font: 8.5pt "DejaVu Sans", sans-serif; color: #444; }
.item p { margin: .5mm 0 0; font-size: 10.5pt; }
.url { color: #0b3d91; text-decoration: none; font: 8.5pt "DejaVu Sans Mono", monospace; overflow-wrap: anywhere; }
.bref { font-size: 10.5pt; margin: 0 0 2.4mm; }
footer { margin-top: 4mm; border-top: 2pt solid #111; padding-top: 2mm; font: 9pt/1.4 "DejaVu Sans", sans-serif;
         color: #222; break-inside: avoid; }
footer p { margin: 0 0 1mm; }
"""


def build_gazette_html(c: dict, txt_name: str, compact: bool = False) -> str:
    """La une du lundi : HTML autonome (aucune ressource extérieure), TOUT texte venu des
    flux ou des projets passé par `_esc`."""
    lim = GAZETTE_LIMITS[bool(compact)]
    now, since, findings, projects = c["now"], c["since"], c["findings"], c["projects"]
    live = {p: r for p, r in findings.items() if not projects[p]["archive"]}
    archived = sorted(pid.rsplit("/", 1)[-1] for pid in findings if projects[pid]["archive"])
    n_rows = sum(len(r) for r in live.values())
    n_exp = sum(r["exploitee"] for rows in live.values() for r in rows)
    n_crit = sum(r["rang"] >= 4 for rows in live.values() for r in rows)
    edition = now.isocalendar()[1]
    cert_hit = {i["lien"]: _short(pid) for pid, _k, i in c["cert_named"] if not projects[pid]["archive"]}
    kev_hit = {v["cveID"]: _short(pid) for pid, v in c["named"] if not projects[pid]["archive"]}

    # ---- la une : chiffre clé, gros titre, chapeau
    if n_exp:
        big, big_label = n_exp, ("faille activement exploitée" if n_exp == 1 else "failles activement exploitées")
        hit = sorted({_short(p) for p, rows in live.items() if any(r["exploitee"] for r in rows)})
        headline = f"{', '.join(hit[:2])}{' et autres' if len(hit) > 2 else ''} : une faille déjà exploitée à corriger d'abord"
    elif live:
        big, big_label = len(live), "projets actifs à mettre à jour" if len(live) > 1 else "projet actif à mettre à jour"
        headline = f"{_pl(len(live), 'projet a', 'projets ont')} une faille haute ou critique"
    else:
        big, big_label = 0, "projet actif touché"
        headline = "Semaine calme pour tes projets"
    start = f"{since.day}" if since.month == now.month else f"{since.day} {MONTHS_FR[since.month - 1]}"
    chapeau = (f"Du {start} au {now.day} {MONTHS_FR[now.month - 1]} : "
               f"{_pl(len(c['cert']['alerte']), 'alerte', 'alertes')} et {len(c['cert']['avis'])} avis du CERT-FR, "
               f"{_pl(len(c['kev_recent']), 'faille ajoutée', 'failles ajoutées')} au catalogue américain des failles "
               f"exploitées (CISA), {_pl(len(c['github']), 'avis critique', 'avis critiques')} sur GitHub. ")
    if live:
        chapeau += (f"Dans tes projets actifs : {_pl(n_rows, 'faille haute ou critique connue', 'failles hautes ou critiques connues')}, "
                    f"dont {n_crit} critique{'s' if n_crit > 1 else ''} et {n_exp} activement exploitée{'s' if n_exp > 1 else ''}. "
                    "Une mise à jour de paquet en corrige souvent plusieurs : la liste ci-dessous va droit au but.")
    else:
        chapeau += "Aucune faille haute ou critique connue dans tes projets actifs."

    H = [f"<header class=\"mast\"><h1>{_esc(GAZETTE_TITLE)}</h1><div class=\"dateline\">"
         f"<span>{_esc(_cap(_date_fr(now)))}</span><span>Édition n° {edition} (semaine {edition})</span>"
         f"<span>Veille hebdomadaire</span></div></header>",
         f"<section class=\"une\"><div class=\"chiffre\"><b>{big}</b><span>{_esc(big_label)}</span></div>"
         f"<div><h2>{_esc(headline)}</h2><p class=\"chapeau\">{_esc(chapeau)}</p></div></section>"]

    # ---- à corriger en priorité : exploitées, puis critiques, puis le reste (une ligne chacune)
    cands = [(pid, f) for pid, rows in live.items() for f in fixes_by_package(rows)]
    cands.sort(key=lambda x: (not x[1]["exploitee"], -x[1]["rang"], x[1]["dev"], -x[1]["n"], _short(x[0])))
    prio = [f"<li><span class=\"tag toi\">produit visé</span> <span class=\"proj\">{_esc(_short(pid))}</span> : "
            f"{_esc(v['cveID'])} ({_esc(_clean(v.get('vendorProject', ''), 40))} {_esc(_clean(v.get('product', ''), 40))}), "
            f"exploitée : appliquer le correctif de l'éditeur</li>"
            for pid, v in c["named"] if not projects[pid]["archive"]]
    first, later, per = [], [], {}      # 2 par projet d'abord, pour ne pas remplir la liste d'un seul
    for x in cands:
        per[x[0]] = per.get(x[0], 0) + 1
        (first if per[x[0]] <= 2 else later).append(x)
    for pid, f in (first + later)[:max(0, lim["priorite"] - len(prio))]:
        prio.append(f"<li><span class=\"proj\">{_esc(_short(pid))}</span> : {_fix_html(f)} {_fix_tags(f)}</li>")
    H.append("<h3 class=\"urgent\">À corriger en priorité</h3>")
    H.append(f"<ul class=\"prio cols\">{''.join(prio)}</ul>" if prio
             else "<p>Rien d'urgent : aucune faille haute, critique ou exploitée dans tes projets actifs.</p>")

    # ---- un encadré par projet touché : Divers d'abord, puis Travail (Dev)
    if live:
        H.append("<h3>Les projets touchés</h3>")
        order = sorted(live, key=lambda p: ({"Divers": 0, "Dev": 1}.get(p.split("/", 1)[0], 2), p.split("/", 1)[0],
                                            -sum(r["exploitee"] for r in live[p]), -max(r["rang"] for r in live[p]),
                                            -len(live[p]), p))
        shown, rest = order[:lim["projets"]], order[lim["projets"]:]
        root = None
        for pid in shown:
            label = pid.split("/", 1)[0]
            if label != root:
                H.append("</div>" if root is not None else "")
                root = label
                H.append(f"<p class=\"groupe\">{_esc(ROOT_TITLES.get(label, label))}</p><div class=\"cols\">")
            rows = live[pid]
            fixes = fixes_by_package(rows)
            exp, crit = sum(r["exploitee"] for r in rows), sum(r["rang"] >= 4 for r in rows)
            count = _pl(len(rows), "faille", "failles") + (f" · {exp} exploitée{'s' if exp > 1 else ''}" if exp else "") \
                + (f" · {crit} critique{'s' if crit > 1 else ''}" if crit else "") \
                + f" · {_pl(len(fixes), 'paquet', 'paquets')} à mettre à jour"
            items = "".join(f"<li>{_fix_html(f)} {_fix_tags(f)}</li>" for f in fixes[:lim["corrections"]])
            more = (f"<p class=\"more\">+ {_pl(len(fixes) - lim['corrections'], 'autre paquet', 'autres paquets')} : "
                    f"détail dans le .txt.</p>" if len(fixes) > lim["corrections"] else "")
            H.append(f"<div class=\"box\"><h4>{_esc(_short(pid))}</h4><p class=\"count\">{_esc(count)}</p>"
                     f"<ul>{items}</ul>{more}</div>")
        H.append("</div>" if shown else "")
        if rest:
            H.append(f"<p class=\"more\">Aussi touchés : {_esc(', '.join(_short(p) for p in rest))} (détail dans le .txt).</p>")

    # ---- dans l'actualité
    N = ["<h3>Dans l'actualité</h3><div class=\"cols news\">"]

    def item(title: str, meta: str, url: str = "", text: str = "", toi: str = "") -> str:
        tag = f" <span class=\"tag toi\">concerne {_esc(toi)}</span>" if toi else ""
        body = f"<p>{_esc(text)}</p>" if text else ""
        link = _link(url)
        return (f"<div class=\"item\"><b>{_esc(title)}</b>{tag}<br><span class=\"meta\">{_esc(meta)}</span>"
                f"{body}{'<div>' + link + '</div>' if link else ''}</div>")

    def words(text: str, limit: int) -> str:
        text = " ".join(str(text or "").split())
        return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:.…") + "…"

    alerts = c["cert"]["alerte"]
    if alerts:
        N.append("<h4>Alertes du CERT-FR</h4>")
        for i in alerts[:lim["alertes"]]:
            N.append(item(cert_title_fr(i["titre"]), f"Alerte du {i['date'][8:10]}/{i['date'][5:7]}", i["lien"],
                          words(i.get("texte", ""), 260), cert_hit.get(i["lien"], "")))
    if c["kev_recent"]:
        N.append("<h4>Nouvelles failles exploitées (CISA)</h4>")
        for v in c["kev_recent"][:lim["kev"]]:
            cve = str(v.get("cveID", ""))
            ransom = " · utilisée par des rançongiciels" if v.get("knownRansomwareCampaignUse") == "Known" else ""
            url = f"https://nvd.nist.gov/vuln/detail/{cve}" if re.match(r"^CVE-\d{4}-\d{4,}$", cve) else ""
            d = str(v.get("dateAdded", ""))
            N.append(item(kev_title_fr(v), f"{cve} · ajoutée le {d[8:10]}/{d[5:7]}{ransom}", url,
                          toi=kev_hit.get(cve, "")))
        if len(c["kev_recent"]) > lim["kev"]:
            N.append(f"<p class=\"bref\">+ {len(c['kev_recent']) - lim['kev']} autres ajouts (détail dans le .txt).</p>")
    avis = sorted(c["cert"]["avis"], key=lambda i: i["lien"] not in cert_hit)   # ceux qui te concernent d'abord
    if avis:
        N.append("<h4>Avis du CERT-FR</h4>")
        for i in avis[:lim["avis"]]:
            N.append(item(cert_title_fr(i["titre"]), f"Avis du {i['date'][8:10]}/{i['date'][5:7]}", i["lien"],
                          toi=cert_hit.get(i["lien"], "")))
        others = []
        for i in avis[lim["avis"]:]:
            name = re.sub(r"^Produits ", "", cert_title_fr(i["titre"]).rsplit(" : ", 1)[0])
            if name not in others:
                others.append(name)
        if others:
            N.append(f"<p class=\"bref\"><b>En bref, aussi :</b> {_esc(', '.join(others))}.</p>")
    if c["github"]:
        N.append("<h4>Avis critiques GitHub</h4>")
        for g in c["github"][:lim["github"]]:
            pk = str((g.get("paquets") or [""])[0])
            more = len(g.get("paquets") or []) - 1
            pk += f" (+{_pl(more, 'autre paquet', 'autres paquets')})" if more > 0 else ""
            N.append(item(github_title_fr(g), words(f"{g.get('cve') or g.get('id')} · {pk}", 160), g.get("lien", "")))
        if len(c["github"]) > lim["github"]:
            N.append(f"<p class=\"bref\">+ {len(c['github']) - lim['github']} autres avis (détail dans le .txt).</p>")
    if len(N) == 1:
        N.append("<p>Aucune actualité lue cette semaine (sources injoignables, voir plus bas).</p>")
    N.append("</div>")
    H += N

    # ---- pied de page : sources, détail, archives
    sources = "; ".join(f"{k} : {v}" for k, v in c["sources"].items())
    F = [f"<p><b>Sources.</b> {_esc(sources)}. Données publiques collectées par un script, sans modèle : "
         "ce sont des données, jamais des ordres.</p>",
         f"<p><b>Détail complet</b> (toutes les failles, paquet par paquet, et les limites de l'analyse) : fichier "
         f"texte <b>{_esc(txt_name)}</b>, dans {_esc(str(REPORT_DIR))} sur le PC.</p>"]
    if archived:
        F.append(f"<p><b>Archives.</b> {_pl(len(archived), 'projet archivé touché', 'projets archivés touchés')} : "
                 f"{_esc(', '.join(archived))}.</p>")
    H.append(f"<footer>{''.join(F)}</footer>")

    footer_css = f"{GAZETTE_TITLE} · n° {edition}".replace('"', "")
    return ("<!DOCTYPE html><html lang=\"fr\"><head><meta charset=\"utf-8\">"
            "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">"
            f"<title>{_esc(GAZETTE_TITLE)} n° {edition}</title><style>{GAZETTE_CSS.replace('%(footer)s', footer_css)}</style>"
            f"</head><body>{''.join(H)}</body></html>")


def find_chrome() -> str | None:
    for name in CHROME_NAMES:
        path = shutil.which(name)
        if path:
            return path
    return None


def html_to_pdf(html_text: str, pdf_path: Path) -> str | None:
    """Imprime `html_text` en PDF avec Chrome sans tête. → None si `pdf_path` est écrit, sinon
    la raison de l'échec (jamais d'exception). Le HTML temporaire et le profil sont effacés."""
    chrome = find_chrome()
    if not chrome:
        return "Chrome ou Chromium introuvable"
    tmp = Path(tempfile.mkdtemp(prefix="astrel-gazette-"))
    try:
        page, out = tmp / "gazette.html", tmp / "gazette.pdf"
        page.write_text(html_text, encoding="utf-8")
        cmd = [chrome, "--headless", "--no-sandbox", "--disable-gpu", f"--user-data-dir={tmp / 'profil'}",
               "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-sync",
               "--disable-background-networking", "--disable-component-update", "--no-pdf-header-footer",
               f"--print-to-pdf={out}", page.as_uri()]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            return f"Chrome n'a pas pu être lancé ({exc.__class__.__name__})"
        try:
            code = proc.wait(timeout=CHROME_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            systeme.tuer_arbre(proc.pid, systeme.SIGKILL)  # 03/11 : groupe (Unix) ou arbre (Windows)
            proc.wait()
            return f"Chrome n'a pas fini en {CHROME_TIMEOUT_S} s"
        try:
            ok = out.is_file() and out.read_bytes()[:5] == b"%PDF-"
        except OSError:
            ok = False
        if not ok:
            return f"Chrome n'a pas produit de PDF (code {code})"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        part = pdf_path.with_name(pdf_path.name + ".part")
        shutil.copyfile(out, part)
        part.replace(pdf_path)
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def pdf_pages(path: Path) -> int:
    """Nombre de pages (objets `/Type /Page`) ; 0 si illisible."""
    try:
        return len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", Path(path).read_bytes()))
    except OSError:
        return 0


def write_gazette(c: dict, txt_path: Path) -> tuple[Path | None, str]:
    """→ (chemin du PDF, "") ou (None, avertissement). Jamais d'exception : le .txt suffit."""
    pdf = txt_path.with_suffix(".pdf")
    try:
        err = html_to_pdf(build_gazette_html(c, txt_path.name), pdf)
        if err is None and pdf_pages(pdf) > PDF_MAX_PAGES:
            err = html_to_pdf(build_gazette_html(c, txt_path.name, compact=True), pdf)
    except Exception as exc:  # noqa: BLE001 — le PDF est un plus, il ne fait jamais échouer le rapport
        err = f"erreur inattendue ({exc.__class__.__name__})"
    if err:
        return None, f"PDF non produit ({err}), rapport texte seul."
    return pdf, ""


def _remember(state: dict, keys: list[str], now: datetime) -> None:
    signaled = state.setdefault("signalees", {})
    for k in keys:
        signaled.setdefault(k, now.strftime("%Y-%m-%d"))
    limit = (now - timedelta(days=KEEP_SIGNALED_DAYS)).strftime("%Y-%m-%d")
    state["signalees"] = {k: d for k, d in signaled.items() if d >= limit}


def alert_items(c: dict) -> list[dict]:
    """Ce qui justifie une alerte immédiate : faille exploitée (KEV) dans un paquet d'un projet
    actif, ou produit d'un projet actif nommé dans un ajout KEV récent."""
    items = []
    for pid, rows in c["findings"].items():
        if c["projects"][pid]["archive"]:
            continue
        for r in rows:
            if r["exploitee"]:
                items.append({"cle": r["cle"], "texte": f"{_short(pid)} : {r['cve'] or r['id']} dans {r['paquet']} "
                              f"{r['version']}, corriger vers {r['correctif'] or '(pas encore de correctif)'}"})
    day = (c["now"] - timedelta(days=KEV_ALERT_DAYS)).strftime("%Y-%m-%d")
    for pid, v in c["named"]:
        if not c["projects"][pid]["archive"] and v["dateAdded"] >= day:
            items.append({"cle": f"{pid}|kev:{v['cveID']}",
                          "texte": f"{_short(pid)} : {v['cveID']} ({_clean(v.get('vendorProject', ''), 40)} "
                                   f"{_clean(v.get('product', ''), 40)}) exploitée, voir l'avis de l'éditeur"})
    return items


# ---------------------------------------------------------------- sous-commandes

def cmd_run(now: datetime | None = None) -> int:
    now = now or datetime.now()
    try:
        c = collect(now)
    except NetError as exc:
        print(f"ERREUR: veille sécurité impossible, {exc}. Rapport non marqué comme fait, nouvel essai au prochain contrôle.")
        return 2
    path = REPORT_DIR / f"{now:%Y-%m-%d}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_report(c), encoding="utf-8")
    pdf, warning = write_gazette(c, path)
    rel = os.path.relpath(path, config.ROOT) if path.is_relative_to(config.ROOT) else str(path)
    resume = build_resume(c, rel)
    state = load_state()
    _remember(state, [i["cle"] for i in alert_items(c)], now)
    state["dernier_rapport"] = now.isoformat(timespec="seconds")
    state["dernier_controle_alerte"] = now.isoformat(timespec="seconds")
    state["dernier_resume"] = resume
    save_state(state)
    if warning:
        print(f"AVERTISSEMENT: {warning}")
    print(f"RAPPORT: {pdf or path}")       # le PDF « journal » est ce qui part sur WhatsApp
    print(f"RAPPORT_TXT: {path}")
    print(f"RESUME: {resume}")
    return 0


def cmd_alert(now: datetime | None = None) -> int:
    now = now or datetime.now()
    try:
        c = collect(now, with_news=False)
    except NetError as exc:
        print(f"ERREUR: contrôle d'alerte impossible, {exc}. Non marqué comme fait.")
        return 2
    state = load_state()
    signaled = state.get("signalees") or {}
    fresh, seen = [], set()
    for item in alert_items(c):
        if item["cle"] not in signaled and item["cle"] not in seen:
            seen.add(item["cle"])
            fresh.append(item)
    _remember(state, [i["cle"] for i in fresh], now)
    state["dernier_controle_alerte"] = now.isoformat(timespec="seconds")
    save_state(state)
    if not fresh:
        print("RIEN")
        return 0
    text = f"ALERTE sécurité : {len(fresh)} faille(s) activement exploitée(s) touchent tes projets. "
    for i, item in enumerate(fresh):
        part = item["texte"] + ". "
        if len(text) + len(part) > RESUME_MAX - 40:
            text += f"+{len(fresh) - i} autre(s)."
            break
        text += part
    print(f"RESUME: {' '.join(text.split())}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="astrel.veille_secu", description="Veille de sécurité d'Astrel")
    parser.add_argument("commande", choices=("due", "alert-due", "run", "alert"))
    args = parser.parse_args(argv)
    if args.commande == "due":
        print("DU" if report_due() else "PAS-DU")
        return 0
    if args.commande == "alert-due":
        print("DU" if alert_due() else "PAS-DU")
        return 0
    return cmd_run() if args.commande == "run" else cmd_alert()


if __name__ == "__main__":
    sys.exit(main())
