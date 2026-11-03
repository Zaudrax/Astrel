#!/usr/bin/env python3
"""Essai de démarrage d'Astrel sur une machine de la CI (03/11/2026, portage Windows et macOS).

    python tools/ci/demarrage.py

Lance le VRAI Astrel en mode texte (`python -m astrel --text --no-voice-out`), avec le faux
Claude de `tools/ci/faux-claude` et le moteur audio `fichier` (ni micro ni haut-parleur), attend
qu'il soit prêt, envoie un message, attend la réponse du faux Claude, puis ferme l'entrée et
vérifie qu'Astrel s'arrête proprement. Code 0 si tout s'est passé ; sinon la sortie et la fin
du journal sont affichées pour comprendre où ça casse."""
from __future__ import annotations

import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
FAUX = ROOT / "tools" / "ci" / "faux-claude"
REPONSE = "faux Claude de l'intégration continue"
PRET = "Mode texte."
DELAI_PRET_S = 240.0
DELAI_REPONSE_S = 120.0
DELAI_ARRET_S = 60.0


def main() -> int:
    env = dict(os.environ)
    env["PATH"] = str(FAUX) + os.pathsep + env.get("PATH", "")
    env["ASTREL_AUDIO_ENGINE"] = "fichier"
    env["ASTREL_WORKDIR"] = tempfile.mkdtemp(prefix="astrel-ci-projets-")
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    with socket.socket() as s:  # port libre : n'entre jamais en conflit avec un Astrel qui tourne
        s.bind(("127.0.0.1", 0))
        env["ASTREL_HUD_PORT"] = str(s.getsockname()[1])
    proc = subprocess.Popen([sys.executable, "-m", "astrel", "--text", "--no-voice-out"], cwd=ROOT, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    lignes: queue.Queue[str] = queue.Queue()
    sortie: list[str] = []

    def lire() -> None:
        for ligne in proc.stdout:
            sortie.append(ligne)
            lignes.put(ligne)

    threading.Thread(target=lire, daemon=True).start()

    def attendre(motif: str, delai: float) -> bool:
        fin = time.monotonic() + delai
        if any(motif in ligne for ligne in sortie):
            return True
        while time.monotonic() < fin:
            try:
                if motif in lignes.get(timeout=0.5):
                    return True
            except queue.Empty:
                if proc.poll() is not None:
                    return any(motif in ligne for ligne in sortie)
        return False

    etapes = []
    ok = attendre(PRET, DELAI_PRET_S)
    etapes.append(("démarrage (mode texte prêt)", ok))
    if ok:
        proc.stdin.write("bonjour\n")
        proc.stdin.flush()
        ok = attendre(REPONSE, DELAI_REPONSE_S)
        etapes.append(("un tour avec le faux Claude", ok))
    try:
        proc.stdin.close()
    except OSError:
        pass
    try:
        code = proc.wait(timeout=DELAI_ARRET_S)
        etapes.append((f"arrêt propre (code {code})", code == 0))
    except subprocess.TimeoutExpired:
        proc.kill()
        etapes.append(("arrêt propre (bloqué : tué)", False))
    for nom, reussi in etapes:
        print(f"{'OK    ' if reussi else 'ÉCHEC '} {nom}")
    if all(r for _, r in etapes):
        return 0
    print("\n--- sortie d'Astrel ---")
    print("".join(sortie)[-8000:])
    journal = ROOT / "logs" / "astrel.log"
    if journal.exists():
        print("\n--- fin de logs/astrel.log ---")
        print(journal.read_text(encoding="utf-8", errors="replace")[-8000:])
    return 1


if __name__ == "__main__":
    sys.exit(main())
