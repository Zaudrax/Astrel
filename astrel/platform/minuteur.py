"""Planificateur interne des rappels, là où `systemd-run --user` n'existe pas
(macOS, Windows, Linux sans systemd comme certains WSL2).

Un petit processus détaché par rappel :
`python -m astrel.platform.minuteur <id> <échéance en secondes epoch> <message>`.
Il dort jusqu'à l'échéance (par tranches d'une minute au plus : une mise en
veille de la machine ne décale donc le rappel que jusqu'au réveil), affiche une
notification, demande à Astrel de le dire (`--send announce …`), puis se retire
de l'index `runtime/reminders.json`.

⚠️ Limite, dite à Claude dans le résultat de `reminder_create` : contrairement
au minuteur systemd, ce processus ne survit PAS à un redémarrage de la machine.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

MAX_NAP_S = 60.0


def wait_until(due: float, now=time.time, sleep=time.sleep) -> None:
    while True:
        left = due - now()
        if left <= 0:
            return
        sleep(min(left, MAX_NAP_S))


def fire(uid: str, message: str) -> None:
    root = Path(__file__).resolve().parent.parent.parent
    try:
        from astrel.i18n import tr
        from astrel.tools import desktop

        desktop.notify(tr("Rappel"), message, urgent=True)  # titre dans la langue de l'interface (20/10)
    except Exception:
        pass
    try:
        subprocess.run([sys.executable, "-m", "astrel", "--send", f"announce Rappel : {message}"],
                       cwd=str(root), capture_output=True, timeout=30)
    except Exception:
        pass
    try:
        from astrel.tools import reminders

        reminders.forget(uid)
    except Exception:
        pass


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage : python -m astrel.platform.minuteur <id> <échéance epoch> <message>", file=sys.stderr)
        return 2
    uid, due, message = argv[0], float(argv[1]), argv[2]
    wait_until(due)
    fire(uid, message)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    sys.exit(main(sys.argv[1:]))
