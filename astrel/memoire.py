"""Pression mémoire du PC (21/10, plan P20, demande de l'utilisateur : 16 Go de RAM, 8 Go de swap,
souvent 85-90 % de swap quand plusieurs agents tournent). LECTURE SEULE : rien n'est fermé ni tué.

- `sample()` : RAM disponible, swap utilisé, pression du noyau (`/proc/pressure/memory`, absente =
  ignorée) ; ≈ 0,15 ms, appelé toutes les 5 s par le cœur (`Astrel._memoire_tick`).
- `decide(échantillon, état, maintenant)` : fonction PURE, états `ok`, `attention`, `blocage`.
  Seuils de la question 1 du plan : attention à swap ≥ 75 % ou RAM disponible < 2 Go ; blocage à
  (swap ≥ 90 % ET disponible < 3 Go) ou disponible < 1 Go ; sortie du blocage quand
  (swap < 80 % ou disponible ≥ 4 Go) ET disponible ≥ 2 Go, tenu 30 s. Le swap haut ne redescend
  PAS seul quand la RAM se libère (pages relues seulement à l'usage) : une reprise sur le swap
  seul bloquerait pour rien, d'où le critère combiné.
- `runtime/memoire.json` (écrit par le cœur, atomique) : lu par `route_hook.py` (nouveaux agents,
  Chrome de test Playwright). Absent, périmé (> 30 s) ou illisible = on laisse passer.
- Blocage ACTIF seulement après relevé des seuils (lot B du plan) : `on` dans
  `runtime/memoire-blocage.txt` ou `ASTREL_MEMOIRE_BLOCAGE=1` ; sinon « observation » (état
  affiché, rien n'est refusé). Relevé : `.venv/bin/python -m astrel.memoire --releve`.
- `top()` : classement par APPLICATION (mémoire réelle PSS, repli RSS, plus le swap), processus
  de l'utilisateur seulement ; jamais de ligne de commande dans le résultat. ≈ 250 ms, cache 10 s.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config
from .platform import systeme

GO = 2 ** 30


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


# Seuils (question 1 du plan P20) ; ASTREL_MEMOIRE_* pour les recalibrer sans toucher au code.
ATTENTION_SWAP_PCT = _env_float("ASTREL_MEMOIRE_ATTENTION_SWAP", 75.0)
ATTENTION_DISPO_GO = _env_float("ASTREL_MEMOIRE_ATTENTION_DISPO", 2.0)
BLOCAGE_SWAP_PCT = _env_float("ASTREL_MEMOIRE_BLOCAGE_SWAP", 90.0)
BLOCAGE_SWAP_DISPO_GO = _env_float("ASTREL_MEMOIRE_BLOCAGE_SWAP_DISPO", 3.0)
BLOCAGE_DISPO_GO = _env_float("ASTREL_MEMOIRE_BLOCAGE_DISPO", 1.0)
REPRISE_SWAP_PCT = _env_float("ASTREL_MEMOIRE_REPRISE_SWAP", 80.0)
REPRISE_DISPO_GO = _env_float("ASTREL_MEMOIRE_REPRISE_DISPO", 4.0)
REPRISE_MIN_DISPO_GO = _env_float("ASTREL_MEMOIRE_REPRISE_MIN_DISPO", 2.0)
REPRISE_TENUE_S = _env_float("ASTREL_MEMOIRE_REPRISE_S", 30.0)
# Hystérésis de l'attention : on n'en sort qu'un peu sous le seuil (pas de clignotement du HUD).
ATTENTION_MARGE_SWAP = 3.0
ATTENTION_MARGE_GO = 0.25

PERIODE_S = 5.0          # un échantillon toutes les 5 s (boucle d'une seconde du cœur)
JOURNAL_PERIODE_S = 60.0  # une ligne par minute dans logs/memoire.jsonl (calibrage)
PERIME_S = 30.0          # fichier plus vieux : on laisse passer
TOP_CACHE_S = 10.0

STATE_FILE = Path(os.environ.get("ASTREL_MEMOIRE_FILE") or config.RUNTIME / "memoire.json")
SWITCH_FILE = config.RUNTIME / "memoire-blocage.txt"
JOURNAL = Path(os.environ.get("ASTREL_MEMOIRE_JOURNAL") or config.LOGS / "memoire.jsonl")
PSI_FILE = Path("/proc/pressure/memory")

ETATS = ("ok", "attention", "blocage")


# --- mesure ---

def _psi(path: Path = PSI_FILE) -> dict[str, float] | None:
    """`some avg10` et `full avg10` (% du temps où des tâches attendent la mémoire) ; None si absent."""
    try:
        text = path.read_text(encoding="ascii")
    except OSError:
        return None
    out: dict[str, float] = {}
    for line in text.splitlines():
        kind, _, rest = line.partition(" ")
        for part in rest.split():
            key, _, value = part.partition("=")
            if key == "avg10":
                try:
                    out[kind] = float(value)
                except ValueError:
                    pass
    return out or None


def sample() -> dict[str, Any]:
    """Un échantillon (octets et pourcentage) : {ram_total, ram_dispo, swap_total, swap_used,
    swap_pct, psi}. `psi` = None sans /proc/pressure/memory."""
    import psutil

    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    return {
        "ram_total": int(vm.total), "ram_dispo": int(vm.available),
        "swap_total": int(sw.total), "swap_used": int(sw.used),
        "swap_pct": float(sw.percent) if sw.total else 0.0,
        "psi": _psi(),
    }


# --- décision (pure) ---

@dataclass(frozen=True)
class Etat:
    etat: str = "ok"
    depuis: float = 0.0          # entrée dans l'état courant
    reprise_depuis: float = 0.0  # blocage seulement : début de la condition de reprise, 0 = pas tenue


def _go(s: dict[str, Any], key: str) -> float:
    return float(s.get(key) or 0) / GO


def bloque(s: dict[str, Any]) -> bool:
    dispo, swap = _go(s, "ram_dispo"), float(s.get("swap_pct") or 0)
    return (swap >= BLOCAGE_SWAP_PCT and dispo < BLOCAGE_SWAP_DISPO_GO) or dispo < BLOCAGE_DISPO_GO


def reprise_possible(s: dict[str, Any]) -> bool:
    dispo, swap = _go(s, "ram_dispo"), float(s.get("swap_pct") or 0)
    return (swap < REPRISE_SWAP_PCT or dispo >= REPRISE_DISPO_GO) and dispo >= REPRISE_MIN_DISPO_GO


def attention(s: dict[str, Any], deja: bool = False) -> bool:
    """`deja` : on y est, on n'en sort qu'un peu sous le seuil."""
    dispo, swap = _go(s, "ram_dispo"), float(s.get("swap_pct") or 0)
    marge_swap, marge_go = (ATTENTION_MARGE_SWAP, ATTENTION_MARGE_GO) if deja else (0.0, 0.0)
    return swap >= ATTENTION_SWAP_PCT - marge_swap or dispo < ATTENTION_DISPO_GO + marge_go


def decide(s: dict[str, Any], prev: Etat, now: float) -> Etat:
    """État suivant. Blocage immédiat ; sortie du blocage seulement si la reprise tient
    `REPRISE_TENUE_S` sans interruption (un échantillon hors reprise remet le compteur à zéro)."""
    if prev.etat == "blocage":
        if bloque(s) or not reprise_possible(s):
            return Etat("blocage", prev.depuis, 0.0)
        since = prev.reprise_depuis or now
        if now - since < REPRISE_TENUE_S:
            return Etat("blocage", prev.depuis, since)
        return Etat("attention" if attention(s) else "ok", now, 0.0)
    if bloque(s):
        return Etat("blocage", now, 0.0)
    new = "attention" if attention(s, deja=prev.etat == "attention") else "ok"
    return prev if new == prev.etat else Etat(new, now, 0.0)


# --- vue (HUD, fichier, journal) ---

def view(s: dict[str, Any], st: Etat) -> dict[str, Any]:
    """Valeurs ARRONDIES (Go à 0,1, % entier) : l'évènement `memory` n'est publié que si elles
    ou l'état changent."""
    total, dispo = _go(s, "ram_total"), _go(s, "ram_dispo")
    return {
        "state": st.etat,
        "ram_total_go": round(total, 1), "ram_used_go": round(max(0.0, total - dispo), 1),
        "ram_dispo_go": round(dispo, 1),
        "swap_total_go": round(_go(s, "swap_total"), 1), "swap_pct": int(round(float(s.get("swap_pct") or 0))),
        "active": blocage_actif(),
    }


def write_state(v: dict[str, Any], now: float | None = None, path: Path | None = None) -> None:
    """`runtime/memoire.json`, atomique (un lecteur voit l'ancien ou le nouveau, jamais un morceau)."""
    path = path or STATE_FILE
    data = {**v, "at": time.time() if now is None else now}
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def journal(s: dict[str, Any], st: Etat, now: float | None = None, path: Path | None = None,
            **extra: Any) -> None:
    """Une ligne JSON (calibrage des seuils) : valeurs fines, pas arrondies."""
    path = path or JOURNAL
    line = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
        "etat": st.etat, "dispo_go": round(_go(s, "ram_dispo"), 2), "swap_pct": round(float(s.get("swap_pct") or 0), 1),
        "psi": s.get("psi"), **extra,
    }
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


# --- lecture par le hook (autre processus) ---

def blocage_actif() -> bool:
    """Lot B activé ? `ASTREL_MEMOIRE_BLOCAGE` prime ; sinon `on` dans runtime/memoire-blocage.txt."""
    raw = os.environ.get("ASTREL_MEMOIRE_BLOCAGE")
    if raw is not None and raw.strip():
        return raw.strip().lower() in ("1", "true", "yes", "oui", "on")
    try:
        return SWITCH_FILE.read_text(encoding="utf-8").strip().lower() in ("on", "oui", "1")
    except OSError:
        return False


def lire_etat(now: float | None = None, path: Path | None = None) -> dict[str, Any] | None:
    """Le dernier état écrit par le cœur ; None s'il est absent, illisible ou périmé (> 30 s)."""
    try:
        data = json.loads((path or STATE_FILE).read_text(encoding="utf-8"))
        at = float(data["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    now = time.time() if now is None else now
    if not isinstance(data, dict) or not (0 <= now - at <= PERIME_S) or data.get("state") not in ETATS:
        return None
    return data


def pression(now: float | None = None, path: Path | None = None) -> str:
    """« blocage » si le blocage est ACTIF et l'état frais le dit ; sinon l'état (ou « » si inconnu).
    Appelé par `route_hook.py` avant chaque nouvel agent et chaque navigation Playwright."""
    data = lire_etat(now, path)
    if data is None:
        return ""
    state = str(data["state"])
    if state == "blocage" and not blocage_actif():
        return "attention"
    return state


def resume_etat(now: float | None = None) -> str:
    """Pour Claude (`system_status`) : « ok », « attention », « blocage (nouveaux agents et
    navigateur de test refusés) », « blocage (observation : rien n'est refusé) » ou « inconnue »."""
    data = lire_etat(now)
    if data is None:
        return "inconnue (Astrel ne mesure pas, ou mesure périmée)"
    if data["state"] != "blocage":
        return str(data["state"])
    return ("blocage (nouveaux agents et navigateur de test refusés)" if blocage_actif()
            else "blocage (observation : rien n'est refusé)")


def refus(quoi: str, now: float | None = None, path: Path | None = None) -> str:
    """Raison du refus (pour Claude, qui la répète à l'utilisateur), ou « » : on laisse passer."""
    if pression(now, path) != "blocage":
        return ""
    data = lire_etat(now, path) or {}
    return (f"Mémoire du PC presque pleine (RAM disponible {fmt_go(data.get('ram_dispo_go'))}, swap "
            f"{data.get('swap_pct', '?')} %) : {quoi} refusé tant qu'elle ne redescend pas. Dis-le à l'utilisateur "
            "en une phrase ; fais le travail toi-même ou attends. Les agents déjà lancés, Bash et pytest "
            "continuent, mais pèsent aussi : évite de lancer du lourd. Rien n'a été fermé.")


def fmt_go(value: Any) -> str:
    try:
        return f"{float(value):.1f} Go".replace(".", ",")
    except (TypeError, ValueError):
        return "? Go"


# --- classement des gros consommateurs ---

_top_lock = threading.Lock()
_top_cache: tuple[float, dict[str, Any]] | None = None

# nom du processus (comm) → application ; le reste garde son nom de processus.
_NOMS = {
    "code": "VS Code", "code-oss": "VS Code", "codium": "VS Code",
    "firefox": "Firefox", "firefox-esr": "Firefox", "Isolated Web Co": "Firefox", "Web Content": "Firefox",
    "WebExtensions": "Firefox", "RDD Process": "Firefox", "Socket Process": "Firefox",
    "Privileged Cont": "Firefox", "Utility Process": "Firefox", "forkserver": "Firefox",
    "claude": "Claude Code", "gnome-shell": "GNOME (bureau)", "Xwayland": "GNOME (bureau)",
    "nautilus": "Fichiers", "ollama": "Ollama",
}
_CHROME = ("chrome", "chromium", "chrome_crashpad", "chrome_crashpad_handler", "chromium-browser")

LIBELLE_HUD = "HUD d'Astrel"
LIBELLE_HEADLESS = "Chrome de test (headless)"
LIBELLE_CHROME = "Navigateur Chrome"
LIBELLE_ASTREL = "Astrel"
LIBELLE_PYTEST = "Tests (pytest)"


def _is_chrome(name: str) -> bool:
    return name in _CHROME or name.startswith("chrome")


def _chrome_label(cmdline: list[str], hud_profile: str) -> str:
    joined = " ".join(cmdline)
    if hud_profile and f"--user-data-dir={hud_profile}" in joined:
        return LIBELLE_HUD
    if "--headless" in joined:
        return LIBELLE_HEADLESS
    return LIBELLE_CHROME


def classify(procs: dict[int, dict[str, Any]], root: str | None = None,
             hud_profile: str | None = None) -> dict[int, str]:
    """pid → libellé d'application. `procs` : pid → {ppid, name, cmdline}. Chrome est rangé
    d'après le processus Chrome le plus HAUT de son arbre (ses enfants n'ont pas tous le profil
    ni `--headless`). Fonction pure (tests sur faux processus)."""
    root = str(config.ROOT if root is None else root)
    hud_profile = str(config.HUD_PROFILE_DIR if hud_profile is None else hud_profile)
    out: dict[int, str] = {}
    for pid, info in procs.items():
        name = str(info.get("name") or "")
        cmd = [str(c) for c in (info.get("cmdline") or [])]
        if _is_chrome(name):
            top, seen = pid, {pid}
            while True:
                ppid = int(procs[top].get("ppid") or 0)
                parent = procs.get(ppid)
                if not parent or ppid in seen or not _is_chrome(str(parent.get("name") or "")):
                    break
                seen.add(ppid)
                top = ppid
            out[pid] = _chrome_label([str(c) for c in (procs[top].get("cmdline") or [])], hud_profile)
        elif name in _NOMS:
            out[pid] = _NOMS[name]
        elif any(c == "pytest" or c.endswith("/pytest") for c in cmd[:3]):
            out[pid] = LIBELLE_PYTEST
        elif any(root in c for c in cmd[:3]) or (len(cmd) > 2 and cmd[1:3] == ["-m", "astrel"]):
            out[pid] = LIBELLE_ASTREL
        else:
            out[pid] = name or "?"
    return out


def top(limit: int = 8, force: bool = False) -> dict[str, Any]:
    """{items: [{label, n, go, swap_go}], total_go, at} trié par mémoire réelle + swap. Processus
    de l'utilisateur seulement ; un processus illisible compte en RSS (repli), sans swap."""
    global _top_cache
    with _top_lock:
        now = time.time()
        if not force and _top_cache and now - _top_cache[0] < TOP_CACHE_S:
            return _top_cache[1]
        result = _top(limit)
        _top_cache = (time.time(), result)
        return result


def _top(limit: int) -> dict[str, Any]:
    import psutil

    uid = systeme.uid()  # 03/11 : None sous Windows : par le nom d'utilisateur
    moi = psutil.Process().username() if uid is None else ""
    procs: dict[int, dict[str, Any]] = {}
    mem: dict[int, tuple[int, int]] = {}
    attrs = ["pid", "ppid", "name", "cmdline", "memory_info"] + (["uids"] if uid is not None else ["username"])
    for p in psutil.process_iter(attrs):
        info = p.info
        procs[info["pid"]] = info
        if uid is None:
            if info.get("username") != moi:
                continue
        elif not info.get("uids") or info["uids"].real != uid:
            continue
        try:
            if not sys.platform.startswith("linux"):
                # 03/11 (CI macOS : le test se bloquait sans fin) : memory_full_info y passe par task_for_pid,
                # qui peut ne jamais rendre la main ; PSS et swap par process n'existent que sous Linux.
                raise psutil.AccessDenied(info["pid"])
            full = p.memory_full_info()
            mem[info["pid"]] = (int(getattr(full, "pss", 0) or full.rss), int(getattr(full, "swap", 0) or 0))
        except psutil.Error:
            rss = info.get("memory_info")
            if rss is not None:
                mem[info["pid"]] = (int(rss.rss), 0)
    labels = classify(procs)
    groups: dict[str, list[int]] = {}
    for pid, (real, swap) in mem.items():
        g = groups.setdefault(labels.get(pid, "?"), [0, 0, 0])
        g[0] += 1
        g[1] += real
        g[2] += swap
    items = [{"label": label, "n": n, "go": round(real / GO, 2), "swap_go": round(swap / GO, 2)}
             for label, (n, real, swap) in groups.items()]
    items.sort(key=lambda i: i["go"] + i["swap_go"], reverse=True)
    total = sum(r for r, _ in mem.values()) / GO
    return {"items": items[:limit], "total_go": round(total, 1), "at": time.time()}


def top_lisible(limit: int = 8) -> list[str]:
    """Pour Claude (`system_status`) : « VS Code : 3,2 Go (+0,4 Go de swap), 14 processus »."""
    out = []
    for i in top(limit)["items"]:
        swap = f" (+{fmt_go(i['swap_go'])} de swap)" if i["swap_go"] >= 0.05 else ""
        out.append(f"{i['label']} : {fmt_go(i['go'])}{swap}, {i['n']} processus")
    return out


# --- relevé des seuils (lot B : activer le blocage après ce relevé) ---

def releve(path: Path | None = None) -> str:
    """Résumé de logs/memoire.jsonl : minutes relevées, minutes par état, minutes qui auraient
    été en blocage, pire disponible et pire swap."""
    try:
        lines = (path or JOURNAL).read_text(encoding="utf-8").splitlines()
    except OSError:
        return "Aucun relevé (logs/memoire.jsonl absent)."
    rows = []
    for raw in lines:
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and "dispo_go" in row:
            rows.append(row)
    if not rows:
        return "Aucun relevé exploitable."
    counts = {e: sum(1 for r in rows if r.get("etat") == e) for e in ETATS}
    would = sum(1 for r in rows if bloque({"ram_dispo": float(r["dispo_go"]) * GO, "swap_pct": r.get("swap_pct")}))
    return (f"{len(rows)} relevés du {rows[0].get('at')} au {rows[-1].get('at')} : "
            f"ok {counts['ok']}, attention {counts['attention']}, blocage {counts['blocage']} ; "
            f"au-dessus du seuil de blocage : {would} ; disponible au plus bas "
            f"{min(float(r['dispo_go']) for r in rows):.2f} Go, swap au plus haut "
            f"{max(float(r.get('swap_pct') or 0) for r in rows):.1f} %.")


if __name__ == "__main__":  # pragma: no cover - outil en ligne de commande
    import sys

    if "--releve" in sys.argv:
        print(releve())
    else:
        print(json.dumps(top(force=True), ensure_ascii=False, indent=1))
