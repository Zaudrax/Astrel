"""État de la machine : processeur, mémoire, disques, batterie, carte graphique."""
from __future__ import annotations

import shutil
import subprocess
import time

import psutil

from .. import memoire


def _gpu() -> dict | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip().splitlines()[0]
        name, util, used, total, temp = [x.strip() for x in out.split(",")]
        return {"nom": name, "charge_pct": int(util), "vram_mo": f"{used}/{total}", "temp_c": int(temp)}
    except Exception:
        return None


def status() -> dict:
    vm = psutil.virtual_memory()
    disks = {}
    for mount in ("/", "/data", str(__import__("pathlib").Path.home())):
        try:
            du = psutil.disk_usage(mount)
            disks[mount] = f"{du.percent:.0f} % ({du.free // 2**30} Go libres)"
        except OSError:
            pass
    battery = psutil.sensors_battery()
    temps = {}
    try:
        for chip, entries in (psutil.sensors_temperatures() or {}).items():
            if entries and chip in ("coretemp", "k10temp", "acpitz", "nvme"):
                temps[chip] = round(max(e.current for e in entries))
    except Exception:
        pass
    sw = psutil.swap_memory()
    return {
        "processeur_pct": psutil.cpu_percent(interval=0.4),
        "coeurs": psutil.cpu_count(),
        "memoire": f"{vm.percent:.0f} % ({vm.used // 2**20} / {vm.total // 2**20} Mo)",
        # 21/10 (plan P20) : swap, et le blocage éventuel des nouveaux agents (astrel/memoire.py)
        "memoire_disponible": memoire.fmt_go(vm.available / memoire.GO),
        "swap": f"{sw.percent:.0f} % ({sw.used // 2**20} / {sw.total // 2**20} Mo)",
        "pression_memoire": memoire.resume_etat(),
        "disques": disks,
        "batterie": None if battery is None else {
            "pct": round(battery.percent), "secteur": battery.power_plugged,
        },
        "temperatures_c": temps,
        "gpu": _gpu(),
        "allume_depuis_h": round((time.time() - psutil.boot_time()) / 3600, 1),
        # Par application (mémoire réelle + swap, processus de l'utilisateur) : « qu'est-ce qui mange ma RAM ? »
        "plus_gourmands": memoire.top_lisible(),
    }
