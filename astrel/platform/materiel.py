"""Matériel de la machine, pour proposer un modèle local qui y TIENT (19/10, plan P8).

Lecture seule, sans droit d'administrateur :

- carte graphique NVIDIA : `nvidia-smi` (aussi sous WSL, où il vit dans /usr/lib/wsl/lib) ;
- Mac Apple (arm64) : mémoire UNIFIÉE, dont le GPU n'utilise qu'une part (× `UNIFIED_SHARE`) ;
- sans carte reconnue (AMD, Intel, Mac Intel) : la mémoire vive seule ;
- mémoire vive : psutil ;
- disque : place libre dans le DOSSIER DES MODÈLES d'Ollama (`OLLAMA_MODELS`, sinon celui
  du service systemd `ollama`, sinon ~/.ollama/models).

Tailles en Go binaires (Gio, 2^30 octets), comme le compte nvidia-smi : ici 8 188 Mio
de mémoire graphique = 8,0 Go.

`ASTREL_MATERIEL_FAKE` (tests, captures du HUD) : un nom de profil de `PROFILS`, ou un
objet JSON avec les champs de `Materiel`. Windows et macOS : écrits d'après la doc,
jamais lancés sur une vraie machine.
"""
from __future__ import annotations

import json
import os
import platform as _platform
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

GO = 1024 ** 3
UNIFIED_SHARE = 0.66   # part de la mémoire unifiée d'un Mac Apple que le GPU peut prendre
CACHE_S = 30.0         # une page de Réglages décrite = plusieurs lectures : pas un nvidia-smi par ligne


@dataclass
class Materiel:
    systeme: str = "linux"      # linux, macos, windows, autre
    wsl: bool = False
    gpu: str = ""               # nom de la carte, vide si aucune reconnue
    vram_go: float = 0.0        # mémoire graphique utilisable (0 : pas de carte reconnue)
    ram_go: float = 0.0
    unifiee: bool = False       # Mac Apple : VRAM prise sur la mémoire vive
    disque_go: float = 0.0      # libre sur le disque qui porte RÉELLEMENT le dossier des modèles
    disque_total_go: float = 0.0  # taille de ce disque (marge de 15 %, règle de l'utilisateur du 19/10)
    dossier: str = ""           # dossier des modèles d'Ollama
    faux: bool = False          # profil factice (ASTREL_MATERIEL_FAKE)

    def as_dict(self) -> dict:
        return asdict(self)


# Profils factices des vérifications du plan P8 (et des captures du HUD).
PROFILS: dict[str, dict] = {
    "rtx12": {"systeme": "linux", "gpu": "NVIDIA GeForce RTX 3060", "vram_go": 12.0, "ram_go": 32.0,
              "disque_go": 200.0, "disque_total_go": 500.0, "dossier": "/home/ami/.ollama/models"},
    "cpu8": {"systeme": "windows", "gpu": "", "vram_go": 0.0, "ram_go": 8.0,
             "disque_go": 60.0, "disque_total_go": 256.0, "dossier": "C:/Users/ami/.ollama/models"},
    "m2_16": {"systeme": "macos", "gpu": "Apple M2 (mémoire unifiée)", "vram_go": round(16 * UNIFIED_SHARE, 2),
              "ram_go": 16.0, "unifiee": True,
              "disque_go": 120.0, "disque_total_go": 460.0, "dossier": "/Users/ami/.ollama/models"},
    # Le PC de l'utilisateur si les modèles étaient sur / (7 Go libres sur 96) : tout téléchargement refusé.
    "utilisateur": {"systeme": "linux", "gpu": "NVIDIA GeForce RTX 4060 Laptop GPU", "vram_go": 8.0, "ram_go": 15.0,
                 "disque_go": 7.0, "disque_total_go": 96.0, "dossier": "/usr/share/ollama/.ollama/models"},
    # Le même, tel qu'il est : OLLAMA_MODELS=/data/ollama/models (service), 631 Go libres sur 741.
    "utilisateur_data": {"systeme": "linux", "gpu": "NVIDIA GeForce RTX 4060 Laptop GPU", "vram_go": 8.0,
                      "ram_go": 15.0, "disque_go": 631.0, "disque_total_go": 741.0, "dossier": "/data/ollama/models"},
}

_cache: tuple[float, str, Materiel] | None = None
_lock = threading.Lock()


def _system() -> str:
    from . import SYSTEM
    return SYSTEM


def _fake(raw: str) -> Materiel | None:
    raw = raw.strip()
    if not raw:
        return None
    data: dict | None = PROFILS.get(raw)
    if data is None:
        try:
            data = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(data, dict):
        return None
    known = {f.name for f in fields(Materiel)}
    return Materiel(**{k: v for k, v in data.items() if k in known}, faux=True)


def is_wsl() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False


def _nvidia_smi() -> str:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    for candidate in ("/usr/lib/wsl/lib/nvidia-smi", r"C:\Windows\System32\nvidia-smi.exe"):
        if Path(candidate).exists():
            return candidate
    return ""


def nvidia() -> tuple[str, float]:
    """(nom, Go) de la carte NVIDIA la mieux dotée ; ("", 0.0) sans carte ou sans pilote."""
    exe = _nvidia_smi()
    if not exe:
        return "", 0.0
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return "", 0.0
    best = ("", 0.0)
    for line in (out.stdout or "").splitlines():
        name, _, mib = line.rpartition(",")
        try:
            go = float(mib.strip()) * 1024 ** 2 / GO
        except ValueError:
            continue
        if go > best[1]:
            best = (name.strip(), round(go, 2))
    return best


def ram_go() -> float:
    try:
        import psutil

        return round(psutil.virtual_memory().total / GO, 2)
    except Exception:
        pass
    try:  # repli Linux sans psutil
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) * 1024 / GO, 2)
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def _service_models_dir() -> str:
    """`OLLAMA_MODELS` du service systemd `ollama` (installation Linux officielle)."""
    if not shutil.which("systemctl"):
        return ""
    try:
        out = subprocess.run(["systemctl", "show", "ollama", "-p", "Environment"], capture_output=True, text=True,
                             timeout=3, encoding="utf-8", errors="replace").stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""
    for word in out.replace("Environment=", " ").split():
        if word.startswith("OLLAMA_MODELS="):
            return word.partition("=")[2].strip('"')
    return ""


def models_dir() -> Path:
    """Dossier où Ollama range ses modèles (là où il faut de la place)."""
    for var in ("ASTREL_OLLAMA_MODELS", "OLLAMA_MODELS"):
        if os.environ.get(var):
            return Path(os.environ[var]).expanduser()
    if sys.platform.startswith("linux"):
        service = _service_models_dir()
        if service:
            return Path(service)
        system_dir = Path("/usr/share/ollama/.ollama/models")  # service installé par install.sh d'Ollama
        if system_dir.exists():
            return system_dir
    return Path.home() / ".ollama" / "models"


def disk_go(path: Path) -> tuple[float, float]:
    """(libre, total) du disque où `path` est (ou serait créé : premier dossier existant en
    remontant). Jamais le disque racine par défaut : celui du dossier des modèles."""
    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return 0.0, 0.0
    return round(usage.free / GO, 2), round(usage.total / GO, 2)


def _read() -> Materiel:
    fake = _fake(os.environ.get("ASTREL_MATERIEL_FAKE", ""))
    if fake is not None:
        return fake
    system = _system()
    ram = ram_go()
    gpu, vram = nvidia()
    unified = False
    if system == "macos" and _platform.machine().lower() in ("arm64", "aarch64"):
        # Apple Silicon : pas de carte à part, le GPU puise dans la mémoire vive.
        gpu, vram, unified = "Apple (mémoire unifiée)", round(ram * UNIFIED_SHARE, 2), True
    folder = models_dir()
    free, total = disk_go(folder)
    return Materiel(systeme=system, wsl=is_wsl(), gpu=gpu, vram_go=vram, ram_go=ram, unifiee=unified,
                    disque_go=free, disque_total_go=total, dossier=str(folder))


def detect(fresh: bool = False) -> Materiel:
    """Matériel de la machine (gardé `CACHE_S` secondes ; `fresh` : relu)."""
    global _cache
    key = os.environ.get("ASTREL_MATERIEL_FAKE", "")
    with _lock:
        if not fresh and _cache is not None and _cache[1] == key and time.monotonic() - _cache[0] < CACHE_S:
            return _cache[2]
        value = _read()
        _cache = (time.monotonic(), key, value)
        return value


def forget() -> None:
    """Oublie la dernière lecture (après un téléchargement : le disque a changé)."""
    global _cache
    with _lock:
        _cache = None
