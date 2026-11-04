"""Confidentialité et données (17/10, plan P4, lot 6 : page « Réglages »).

- Ce qui part de la machine, dit en clair (04/11 : tiré de l'inventaire `hors_ligne.SORTIES`).
- Effacer l'historique : vide l'index `runtime/history.db` (`history.clear`). Les
  transcriptions de Claude Code (`~/.claude/projects`) restent : c'est à Claude Code.
- Effacer la mémoire : TOUT le coffre (`vault/`), après un instantané
  (`vault_backup.snapshot`, gardé dans `runtime/vault-backups/snapshots`).
- Exporter la mémoire : un zip du coffre dans `~/Astrel-export/`, dossier ouvert.
  Pas de téléchargement par le HUD (server.py est protégé).

Les deux effacements exigent le mot tapé (`settings.Reglages.action`) et sont refusés
à Claude par le cœur (`_settings_command`).
"""
from __future__ import annotations

import logging
import shutil
import time
import zipfile
from pathlib import Path

from . import config

log = logging.getLogger("astrel.confidentialite")

WORD = "EFFACER"

def _lignes() -> tuple[dict, ...]:
    """04/11 (lot 2 du moteur local) : tirées de l'inventaire `hors_ligne.SORTIES` (le test d'inventaire
    vérifie que chaque module qui sort y est coupé) : la page ne peut plus en oublier."""
    from . import hors_ligne

    lignes = [{"label": titre, "level": "info", "text": quoi} for titre, quoi, _modules, _coupe in hors_ligne.SORTIES]
    lignes.append({"label": "Reste sur la machine", "level": "ok", "text": hors_ligne.RESTE})
    lignes.append({"label": "Gardé par Claude Code", "level": "warn", "text": hors_ligne.GARDE_PAR_CLAUDE})
    return tuple(lignes)


WHAT_LEAVES = _lignes()


def view() -> dict:
    from . import hors_ligne

    lignes = [dict(line) for line in WHAT_LEAVES]
    if hors_ligne.actif():  # 04/11 : ce qui est coupé maintenant
        lignes.insert(0, {"label": "Rien ne sort", "level": "ok",
                          "text": "mode actif : tout ce qui suit est coupé ; le modèle local répond, la voix est Piper"})
    return {"value": {"lines": lignes}, "actions": []}


def data_view() -> dict:
    erase = "Tape le mot pour confirmer."
    return {"value": {"lines": []}, "actions": [
        {"id": "export", "label": "Exporter la mémoire"},
        {"id": "historique", "label": "Effacer l'historique", "danger": True, "word": WORD,
         "confirm": "L'index des conversations d'Astrel sera vidé (les transcriptions de Claude Code restent). "
                    + erase},
        {"id": "memoire", "label": "Effacer la mémoire", "danger": True, "word": WORD,
         "confirm": "Toutes les notes du coffre seront effacées ; un instantané est pris juste avant "
                    "(runtime/vault-backups). " + erase},
    ]}


def _vault():
    from .tools import memory
    return memory.VAULT


def run(action: str, cmd: dict | None = None) -> dict:
    if action == "export":
        return export()
    if action == "historique":
        return clear_history()
    if action == "memoire":
        return clear_memory()
    return {"ok": False, "error": f"action inconnue : {action or '(vide)'}"}


def export() -> dict:
    vault = _vault()
    files = sorted(p for p in vault.rglob("*") if p.is_file()) if vault.is_dir() else []
    if not files:
        return {"ok": False, "error": "la mémoire est vide : rien à exporter"}
    out_dir = config.EXPORT_DIR
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"memoire-astrel-{time.strftime('%Y-%m-%d-%H%M%S')}.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for p in files:
                z.write(p, arcname=str(p.relative_to(vault)))
    except OSError as exc:
        return {"ok": False, "error": f"export impossible : {exc}"}
    log.info("mémoire exportée : %d fichier(s)", len(files))
    opened = ""
    try:
        from . import platform

        platform.desktop_backend().open_path(str(out_dir))
    except Exception as exc:  # pas de bureau (SSH, WSL sans navigateur de fichiers) : le chemin suffit
        opened = f" (dossier non ouvert : {exc})"
    shown = str(target).replace(str(Path.home()), "~", 1)
    return {"ok": True, "level": "ok", "text": f"Exporté : {shown}, {len(files)} fichier(s){opened}."}


def clear_history() -> dict:
    from . import history

    try:
        n = history.clear()
    except Exception as exc:
        return {"ok": False, "error": f"historique non effacé : {exc}"}
    log.info("historique effacé (%d message(s) de l'index)", n)
    return {"ok": True, "level": "ok",
            "text": f"Historique effacé : {n} message(s) retirés de l'index. Les transcriptions de Claude Code "
                    "(~/.claude/projects) restent."}


def clear_memory() -> dict:
    from .tools import vault_backup

    vault = _vault()
    files = sorted(p for p in vault.rglob("*") if p.is_file()) if vault.is_dir() else []
    if not files:
        return {"ok": True, "level": "info", "text": "La mémoire était déjà vide."}
    try:
        sid = vault_backup.snapshot(vault, "avant « Effacer la mémoire » (page Réglages)")
    except Exception as exc:  # pas d'instantané : on n'efface RIEN
        return {"ok": False, "error": f"instantané impossible, rien n'est effacé : {exc}"}
    left = []
    for child in list(vault.iterdir()):
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        except OSError as exc:
            log.warning("mémoire : %s non effacé (%s)", child.name, exc)
            left.append(child.name)
    log.info("mémoire effacée (instantané %s)", sid)
    if left:
        return {"ok": False, "error": f"mémoire effacée en partie ({', '.join(left[:5])} restent) ; "
                                      f"instantané pris avant : {sid}"}
    return {"ok": True, "level": "ok",
            "text": f"Mémoire effacée. Instantané pris juste avant : {sid} (runtime/vault-backups/snapshots)."}
