"""Briques communes aux moteurs macOS et Windows (le moteur Linux garde ses
propres copies d'origine, inchangées).

Mêmes règles que sous Linux : jamais de `shell=True`, une phrase courte en
retour, et ce qui manque est dit franchement.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

MEDIA_ACTIONS = ("play", "pause", "toggle", "next", "previous", "stop")
POWER_ACTIONS = ("suspend", "poweroff", "reboot")


def run(cmd: list[str], timeout: float = 10.0, check: bool = True, input: str | None = None,
        env: dict | None = None) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input,
                          encoding="utf-8", errors="replace",
                          env=None if env is None else {**os.environ, **env})
    if check and proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"{cmd[0]} a échoué").strip()[:300])
    return (proc.stdout or "").strip()


def spawn(cmd: list[str]) -> None:
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


def check_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^(https?|mailto):", url, re.I):
        raise ValueError("seules les adresses http(s) et mailto sont ouvertes")
    return url


def check_path(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"introuvable : {p}")
    return p


def check_media(action: str) -> str:
    if action not in MEDIA_ACTIONS:
        raise ValueError(f"action inconnue : {action} (attendu : {', '.join(MEDIA_ACTIONS)})")
    return action


def check_power(action: str) -> str:
    if action not in POWER_ACTIONS:
        raise ValueError("action attendue : suspend, poweroff ou reboot")
    return action


def best_match(wanted: str, entries: dict[str, str]) -> tuple[str, str] | None:
    """Même règle de choix que sous Linux : nom exact, puis début du nom, puis
    contenu ; à égalité, le nom le plus court. `entries` : clé → nom affiché."""
    wanted = wanted.strip().lower()
    scored = []
    for key, label in entries.items():
        low_label, low_key = label.lower(), key.lower()
        if wanted in (low_label, low_key):
            score = 0
        elif low_label.startswith(wanted):
            score = 1
        elif wanted in low_label or wanted in low_key:
            score = 2
        else:
            continue
        scored.append((score, len(label), key, label))
    if not scored:
        return None
    _, _, key, label = sorted(scored)[0]
    return key, label
